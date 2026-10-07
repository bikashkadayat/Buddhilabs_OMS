"""
The Board of Directors role and its leave routing (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE).

    Employee          ->  Department Head  ->  Approved      (unchanged)
    Department Head   ->  Board            ->  Approved
    Board member      ->  other Board      ->  Approved
    no other Board    ->  HR fallback

Every routing assertion is checked from BOTH ends - who is told to act
(`actionable_approver_ids`) and whose queue it lands in
(`pending_actionable_leaves`) - and then through the real review endpoint, because
the three disagreeing is exactly the failure the routing module was written to
prevent.
"""
from datetime import timedelta

import pytest
from rest_framework.test import APIClient

from audit.models import AuditLog
from leaves.approvals import actionable_approver_ids, pending_actionable_leaves
from leaves.models import Department, Leave
from notifications.models import Category, Notification
from users.models import User
from .conftest import MONDAY, _user

pytestmark = pytest.mark.django_db
END = MONDAY + timedelta(days=1)


@pytest.fixture
def org(db):
    eng = Department.objects.create(name="Engineering", code="BD-ENG")
    ops = Department.objects.create(name="Operations", code="BD-OPS")
    return {
        "eng": eng,
        "emp": _user("bd_emp", User.Roles.MAKER, department_ref=eng),
        "head": _user("bd_head", User.Roles.CHECKER, department_ref=eng),
        "ops_head": _user("bd_ops_head", User.Roles.CHECKER, department_ref=ops),
        "hr": _user("bd_hr", User.Roles.APPROVER, department_ref=ops),
        "board1": _user("bd_board1", User.Roles.BOD, department_ref=None),
        "board2": _user("bd_board2", User.Roles.BOD, department_ref=None),
        "admin": _user("bd_admin", User.Roles.ADMIN, department_ref=None),
    }


def _leave(user, approver=None):
    return Leave.objects.create(user=user, leave_type="annual", start_date=MONDAY,
                                end_date=END, reason="family", approver=approver,
                                status=Leave.Status.PENDING)


def _queue(user, leave):
    return pending_actionable_leaves(user).filter(pk=leave.pk).exists()


def _review(user, leave, decision="approve", remarks="Approved at board level."):
    api = APIClient()
    api.force_authenticate(user)
    return api.post(f"/api/v1/leaves/{leave.id}/dept-head-review/",
                    {"decision": decision, "remarks": remarks}, format="json")


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #
def test_employee_leave_still_goes_to_their_department_head_not_the_board(org):
    leave = _leave(org["emp"])
    assert actionable_approver_ids(leave) == {org["head"].id}
    assert _queue(org["head"], leave)
    for member in ("board1", "board2"):
        assert not _queue(org[member], leave), "the Board does not decide employee leave"
    assert _review(org["board1"], leave).status_code == 403


def test_department_head_leave_goes_to_the_board(org):
    leave = _leave(org["head"])
    assert actionable_approver_ids(leave) == {org["board1"].id, org["board2"].id}
    for member in ("board1", "board2"):
        assert _queue(org[member], leave)
    # Not HR, not another department head - both used to be able to decide it.
    for outsider in ("hr", "ops_head"):
        assert not _queue(org[outsider], leave), outsider
        # 403, or 404 when the leave is not even in their view - refused either way.
        assert _review(org[outsider], leave).status_code in (403, 404), outsider

    res = _review(org["board1"], leave)
    assert res.status_code == 200, res.data
    leave.refresh_from_db()
    assert leave.status == Leave.Status.APPROVED
    assert leave.board_reviewer == org["board1"]
    assert leave.board_action_date is not None
    assert leave.department_head_reviewer is None, "never recorded as a Department Head's decision"


def test_a_head_who_picked_a_fellow_head_still_goes_to_the_board(org):
    leave = _leave(org["head"], approver=org["ops_head"])
    assert org["ops_head"].id not in actionable_approver_ids(leave)
    assert not _queue(org["ops_head"], leave)
    assert _review(org["ops_head"], leave).status_code in (403, 404)


def test_a_head_who_picked_one_board_member_is_decided_by_that_member(org):
    leave = _leave(org["head"], approver=org["board2"])
    assert actionable_approver_ids(leave) == {org["board2"].id}
    # The queue must agree with the decision (found in the browser): board1 is
    # not shown a request they would be refused on.
    assert _queue(org["board2"], leave)
    assert not _queue(org["board1"], leave)
    assert _review(org["board1"], leave).status_code == 403
    assert _review(org["board2"], leave).status_code == 200


def test_board_member_leave_goes_to_the_other_board_members(org):
    leave = _leave(org["board1"])
    assert actionable_approver_ids(leave) == {org["board2"].id}
    assert not _queue(org["board1"], leave), "never their own"
    assert _queue(org["board2"], leave)
    assert _review(org["board1"], leave).status_code == 403
    assert _review(org["board2"], leave).status_code == 200


def test_no_board_member_available_falls_back_to_hr(org):
    for member in ("board1", "board2"):
        org[member].is_active = False
        org[member].save(update_fields=["is_active"])
    leave = _leave(org["head"])
    assert actionable_approver_ids(leave) == {org["hr"].id}
    assert _queue(org["hr"], leave)
    assert _review(org["hr"], leave).status_code == 200


def test_a_lone_board_member_s_own_leave_falls_back_to_hr(org):
    org["board2"].is_active = False
    org["board2"].save(update_fields=["is_active"])
    leave = _leave(org["board1"])
    assert actionable_approver_ids(leave) == {org["hr"].id}
    assert _review(org["hr"], leave).status_code == 200


def test_hr_cannot_decide_a_head_s_leave_while_the_board_can(org):
    leave = _leave(org["head"])
    assert _review(org["hr"], leave).status_code == 403


def test_an_employee_cannot_address_their_leave_to_the_board(org):
    api = APIClient()
    api.force_authenticate(org["emp"])
    res = api.post("/api/v1/leaves/", {
        "leave_type": "annual", "start_date": str(MONDAY), "end_date": str(END),
        "reason": "family", "approver": str(org["board1"].id)}, format="json")
    assert res.status_code in (400, 403), res.data


def test_a_board_member_can_apply_for_leave(org):
    api = APIClient()
    api.force_authenticate(org["board1"])
    res = api.post("/api/v1/leaves/", {
        "leave_type": "annual", "start_date": str(MONDAY), "end_date": str(END),
        "reason": "board travel"}, format="json")
    assert res.status_code == 201, res.data


# --------------------------------------------------------------------------- #
# Audit, timeline, notifications
# --------------------------------------------------------------------------- #
def test_board_decisions_are_audited_as_bod_approved_and_bod_rejected(org):
    approved, rejected = _leave(org["head"]), _leave(org["ops_head"])
    assert _review(org["board1"], approved).status_code == 200
    assert _review(org["board2"], rejected, "reject", "Not during the audit week.").status_code == 200
    a = AuditLog.objects.get(object_id=str(approved.id), action=AuditLog.Action.BOD_APPROVED)
    r = AuditLog.objects.get(object_id=str(rejected.id), action=AuditLog.Action.BOD_REJECTED)
    assert a.changes["stage"] == "board" and r.changes["stage"] == "board"
    assert a.actor == org["board1"]


def test_an_hr_fallback_is_not_audited_as_a_board_decision(org):
    for member in ("board1", "board2"):
        org[member].is_active = False
        org[member].save(update_fields=["is_active"])
    leave = _leave(org["head"])
    _review(org["hr"], leave)
    row = AuditLog.objects.get(object_id=str(leave.id), action=AuditLog.Action.APPROVE)
    assert row.changes["stage"] == "board_fallback"
    assert not AuditLog.objects.filter(object_id=str(leave.id),
                                       action=AuditLog.Action.BOD_APPROVED).exists()


def test_the_timeline_names_the_board_not_a_department_head(org):
    leave = _leave(org["head"])
    _review(org["board1"], leave)
    api = APIClient()
    api.force_authenticate(org["head"])
    steps = api.get(f"/api/v1/leaves/{leave.id}/").data["timeline"]
    assert [s["stage"] for s in steps] == ["Employee", "Board of Directors"]
    assert steps[1]["status"] == "approved"
    assert steps[1]["name"] == org["board1"].get_full_name()


def test_board_members_get_the_board_approval_notification(org, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        leave = _leave(org["head"])
        from leaves.notifications import leave_submitted
        leave_submitted(leave)
    for member in ("board1", "board2"):
        assert Notification.objects.filter(
            recipient=org[member], category=Category.LEAVE_BOARD_APPROVAL_REQUIRED).exists()
    assert not Notification.objects.filter(
        recipient=org["ops_head"], category=Category.LEAVE_BOARD_APPROVAL_REQUIRED).exists()


def test_an_admin_override_notifies_the_board(org, django_capture_on_commit_callbacks):
    leave = _leave(org["head"])
    with django_capture_on_commit_callbacks(execute=True):
        assert _review(org["admin"], leave).status_code == 200
    for member in ("board1", "board2"):
        assert Notification.objects.filter(recipient=org[member],
                                           category=Category.BOARD_NOTICE).exists()
