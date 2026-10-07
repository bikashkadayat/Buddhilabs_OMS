"""Regression: a leave must route to the reporting manager the EMPLOYEE SELECTED.

The bug: leaves.approvals resolved approvers purely from the applicant's
DEPARTMENT and never read Leave.approver. An employee who picked a specific Dept
Head had their request routed to their own department's head instead (or to the
HR fallback), so the selected manager never saw it — while Admin, whose queue is
unfiltered oversight, saw everything. It looked like "the request only went to
Admin".

Routing contract, in order:
  1. the selected reporting manager (Leave.approver), when they can still act
  2. else the active Dept Head(s) of the applicant's department
  3. else HR
Admin keeps oversight but is never the routing target.
"""
from datetime import timedelta

import pytest
from rest_framework.test import APIClient

from users.models import User
from leaves.models import Department, Leave
from leaves.approvals import actionable_approver_ids, pending_actionable_leaves
from .conftest import _user, MONDAY

pytestmark = pytest.mark.django_db

END = MONDAY + timedelta(days=1)


@pytest.fixture
def org(db):
    eng = Department.objects.create(name="Engineering", code="RT-ENG")
    fin = Department.objects.create(name="Finance", code="RT-FIN")
    return {
        "eng": eng,
        "fin": fin,
        # Bikash heads Finance — a DIFFERENT department from the employee.
        "bikash": _user("rt_bikash", User.Roles.CHECKER, department_ref=fin),
        "eng_head": _user("rt_enghead", User.Roles.CHECKER, department_ref=eng),
        "emp": _user("rt_emp", User.Roles.MAKER, department_ref=eng),
        "hr": _user("rt_hr", User.Roles.APPROVER, department_ref=eng),
        "admin": _user("rt_admin", User.Roles.ADMIN, department_ref=eng),
    }


def _leave(user, approver=None, **kw):
    return Leave.objects.create(
        user=user, leave_type="annual", start_date=MONDAY, end_date=END,
        reason="family", status=Leave.Status.PENDING, approver=approver, **kw)


def _queue_has(user, leave):
    return pending_actionable_leaves(user).filter(pk=leave.pk).exists()


# --------------------------------------------------------------------------- #
# 1. The selected manager is the approver
# --------------------------------------------------------------------------- #
def test_selected_manager_is_the_actionable_approver(org):
    lv = _leave(org["emp"], approver=org["bikash"])
    assert actionable_approver_ids(lv) == {org["bikash"].id}


def test_selected_manager_sees_it_in_their_pending_queue(org):
    lv = _leave(org["emp"], approver=org["bikash"])
    assert _queue_has(org["bikash"], lv), "the manager the employee picked must see it"


def test_not_routed_to_the_applicants_own_dept_head_when_someone_else_is_picked(org):
    lv = _leave(org["emp"], approver=org["bikash"])
    assert org["eng_head"].id not in actionable_approver_ids(lv)
    assert not _queue_has(org["eng_head"], lv)


def test_notified_set_matches_the_pending_queue(org):
    """The invariant: whoever is told 'awaiting your review' finds it in their queue."""
    lv = _leave(org["emp"], approver=org["bikash"])
    for uid in actionable_approver_ids(lv):
        assert _queue_has(User.objects.get(pk=uid), lv)


def test_admin_keeps_oversight_visibility(org):
    lv = _leave(org["emp"], approver=org["bikash"])
    assert _queue_has(org["admin"], lv)


def test_hr_is_not_forced_to_action_a_routed_leave(org):
    """HR gets visibility (a CC), not an action item, when a manager was picked."""
    lv = _leave(org["emp"], approver=org["bikash"])
    assert not _queue_has(org["hr"], lv)


def test_employee_may_select_hr_as_reporting_manager(org):
    lv = _leave(org["emp"], approver=org["hr"])
    assert actionable_approver_ids(lv) == {org["hr"].id}
    assert _queue_has(org["hr"], lv)


# --------------------------------------------------------------------------- #
# 2. Fallbacks — never silently Admin
# --------------------------------------------------------------------------- #
def test_no_selection_falls_back_to_the_applicants_dept_head(org):
    lv = _leave(org["emp"], approver=None)
    assert actionable_approver_ids(lv) == {org["eng_head"].id}
    assert _queue_has(org["eng_head"], lv)


def test_inactive_selected_manager_falls_back_to_dept_head(org):
    lv = _leave(org["emp"], approver=org["bikash"])
    User.objects.filter(pk=org["bikash"].pk).update(is_active=False)
    lv.refresh_from_db()
    assert actionable_approver_ids(lv) == {org["eng_head"].id}
    assert not _queue_has(org["bikash"], lv), "must not stay stuck with a disabled manager"


def test_no_selection_and_no_dept_head_falls_back_to_hr(org):
    orphan = _user("rt_orphan", User.Roles.MAKER, department_ref=None, department="")
    lv = _leave(orphan, approver=None)
    assert actionable_approver_ids(lv) == {org["hr"].id}
    assert _queue_has(org["hr"], lv)


def test_applicant_cannot_be_their_own_approver(org):
    """A Dept Head applying and picking themselves falls through to the chain."""
    lv = _leave(org["eng_head"], approver=org["eng_head"])
    assert org["eng_head"].id not in actionable_approver_ids(lv)


def test_dept_head_still_gets_unrouted_leaves_from_own_department(org):
    lv = _leave(org["emp"], approver=None)
    assert _queue_has(org["eng_head"], lv)


def test_dept_head_without_a_department_gets_only_what_they_were_picked_for(org):
    """Guard: filtering on a None department must not match every unscoped applicant."""
    headless = _user("rt_headless", User.Roles.CHECKER, department_ref=None, department="")
    unrouted = _leave(org["emp"], approver=None)
    mine = _leave(org["emp"], approver=headless)
    assert not _queue_has(headless, unrouted)
    assert _queue_has(headless, mine)


# --------------------------------------------------------------------------- #
# 3. End-to-end through the API: submit -> selected manager -> approve
# --------------------------------------------------------------------------- #
def test_submitted_leave_reaches_the_selected_manager_queue_endpoint(org):
    c = APIClient(); c.force_authenticate(org["emp"])
    r = c.post("/api/v1/leaves/", {
        "leave_type": "annual", "start_date": str(MONDAY), "end_date": str(END),
        "reason": "family event", "approver": str(org["bikash"].id),
    }, format="json")
    assert r.status_code == 201, r.content
    leave_id = r.json()["id"]

    cb = APIClient(); cb.force_authenticate(org["bikash"])
    q = cb.get("/api/v1/leaves/?queue=actionable")
    assert q.status_code == 200
    body = q.json()
    rows = body.get("results", body)
    assert any(str(x["id"]) == str(leave_id) for x in rows), \
        "the selected manager's Pending Approvals must contain the request"


def test_selected_manager_can_approve_across_departments(org):
    """Bikash heads Finance; the applicant is in Engineering. Because the employee
    picked him, the department scope must not refuse his decision."""
    c = APIClient(); c.force_authenticate(org["emp"])
    r = c.post("/api/v1/leaves/", {
        "leave_type": "annual", "start_date": str(MONDAY), "end_date": str(END),
        "reason": "family event", "approver": str(org["bikash"].id),
    }, format="json")
    assert r.status_code == 201, r.content
    leave_id = r.json()["id"]

    cb = APIClient(); cb.force_authenticate(org["bikash"])
    d = cb.post(f"/api/v1/leaves/{leave_id}/dept-head-review/",
                {"decision": "approve"}, format="json")
    assert d.status_code == 200, d.content
    assert Leave.objects.get(pk=leave_id).status == Leave.Status.APPROVED


def test_unpicked_dept_head_still_cannot_act_outside_their_department(org):
    """The cross-department escape hatch is ONLY for the manager who was picked.

    404 rather than 403 is correct and preferred: the queryset scope hides the row
    entirely, so an out-of-department head learns nothing about its existence.
    """
    other_emp = _user("rt_fin_emp", User.Roles.MAKER, department_ref=org["fin"])
    lv = _leave(other_emp, approver=org["bikash"])
    c = APIClient(); c.force_authenticate(org["eng_head"])
    d = c.post(f"/api/v1/leaves/{lv.id}/dept-head-review/", {"decision": "approve"}, format="json")
    assert d.status_code in (403, 404), "an unpicked head must not act on another department's leave"
    lv.refresh_from_db()
    assert lv.status == Leave.Status.PENDING
