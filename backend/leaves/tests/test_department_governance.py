"""
Department ownership (Phase DEPARTMENT-GOVERNANCE-HARDENING).

Every departmental workflow in this system ends at a department head: task
routing refuses without one, leave approval falls back to HR and now says so,
and every department report names them. So the rule under test is simple and
the consequences are not:

  AN ACTIVE DEPARTMENT MUST HAVE AN ACTIVE HEAD.

Enforced where a person writes one (the admin API), reported everywhere anyone
might look, and NOT applied retroactively by deactivating the departments a live
organisation is using - that would take leave, attendance and task scoping down
for real staff to fix a configuration gap.
"""
import pytest

from django.apps import apps

from leaves.governance import (
    departments_missing_head, governance_summary, ownership_rows,
)

Department = apps.get_model("leaves", "Department")
DEPTS = "/api/v1/leaves/admin/departments/"
pytestmark = pytest.mark.django_db


def _post(api, admin, **body):
    api.force_authenticate(admin)
    payload = {"name": "Facilities", "code": "FAC"}
    payload.update(body)
    return api.post(DEPTS, payload, format="json")


# --------------------------------------------------------------------------- #
# The rule
# --------------------------------------------------------------------------- #
def test_an_active_department_cannot_be_created_without_a_head(api, admin):
    res = _post(api, admin)
    assert res.status_code == 400, res.data
    message = str(res.data["head"])
    # It says what the gap COSTS, not just that the field is required - and it
    # no longer claims tasks are refused, which stopped being true in
    # TASK-SIMPLIFICATION (corrected in OMS-FINAL-FREEZE-HARDENING).
    assert "Department Head" in message
    assert "Leave approval" in message
    assert "task" not in message.lower()
    assert not Department.objects.filter(code="FAC").exists()


def test_an_active_department_can_be_created_with_one(api, admin, checker):
    res = _post(api, admin, head=str(checker.id))
    assert res.status_code == 201, res.data
    assert Department.objects.get(code="FAC").head == checker


def test_a_department_stood_down_needs_no_head(api, admin):
    """
    Inactive is the honest way to have no head: no work routes to it, so there
    is nobody to answer for. Refusing this would leave an admin with no way to
    retire a department whose head has left.
    """
    res = _post(api, admin, is_active=False)
    assert res.status_code == 201, res.data


def test_the_head_cannot_be_removed_from_an_active_department(api, admin, checker):
    dept = Department.objects.create(name="Facilities", code="FAC", head=checker)
    api.force_authenticate(admin)
    res = api.patch(f"{DEPTS}{dept.id}/", {"head": None}, format="json")
    assert res.status_code == 400, res.data
    dept.refresh_from_db()
    assert dept.head == checker, "the head must still be recorded after a refusal"


def test_a_headless_department_cannot_be_activated(api, admin):
    dept = Department.objects.create(name="Facilities", code="FAC", is_active=False)
    api.force_authenticate(admin)
    res = api.patch(f"{DEPTS}{dept.id}/", {"is_active": True}, format="json")
    assert res.status_code == 400, res.data
    dept.refresh_from_db()
    assert dept.is_active is False


def test_an_inactive_person_cannot_be_the_head(api, admin, checker):
    checker.is_active = False
    checker.save(update_fields=["is_active"])
    res = _post(api, admin, head=str(checker.id))
    assert res.status_code == 400, res.data
    assert "not an active account" in str(res.data["head"])


def test_departments_that_already_exist_are_not_deactivated_by_the_rule(
        api, admin, eng_department):
    """
    The rule governs WRITES. A live organisation's departments keep working
    until somebody sets a head; the gap is reported, not enforced retroactively.
    """
    eng_department.head = None
    eng_department.save(update_fields=["head"])
    eng_department.refresh_from_db()
    assert eng_department.is_active is True
    assert eng_department in departments_missing_head()


# --------------------------------------------------------------------------- #
# What every surface reads
# --------------------------------------------------------------------------- #
def test_a_head_whose_account_is_deactivated_counts_as_missing(checker,
                                                               eng_department):
    """
    The work routes nowhere either way. A department whose head has left is
    exactly as unowned as one that never had a head, and reporting it as owned
    would be the more dangerous of the two lies.
    """
    eng_department.head = checker
    eng_department.save(update_fields=["head"])
    assert eng_department not in departments_missing_head()

    checker.is_active = False
    checker.save(update_fields=["is_active"])
    assert eng_department in departments_missing_head()
    assert governance_summary()["status"] == "critical"


def test_an_inactive_department_is_not_counted_as_a_gap(eng_department):
    """A warning nobody can ever clear is a warning people learn to ignore."""
    # The project ships seeded departments, so the assertion is about THIS
    # department moving out of the count rather than the count reaching zero.
    eng_department.head = None
    eng_department.save(update_fields=["head"])
    before = governance_summary()
    assert eng_department.name in before["missing_head_names"]

    eng_department.is_active = False
    eng_department.save(update_fields=["is_active"])
    after = governance_summary()
    assert eng_department.name not in after["missing_head_names"]
    assert after["missing_head"] == before["missing_head"] - 1


def test_the_summary_names_the_departments_so_it_can_be_acted_on(eng_department):
    """Named, not just counted: a number nobody can act on is not governance."""
    eng_department.head = None
    eng_department.save(update_fields=["head"])
    summary = governance_summary()
    assert eng_department.name in summary["missing_head_names"]
    assert summary["missing_head"] == len(summary["missing_head_names"])
    assert summary["status"] == "critical"
    assert summary["active"] >= 1
    assert summary["total"] >= summary["active"]


def test_the_ownership_register_says_why_a_head_cell_is_empty(checker,
                                                             eng_department):
    """
    "Not assigned" and "Inactive account" are different problems with different
    fixes, and a register that showed both as blank would hide that.
    """
    rows = {row["department"]: row for row in ownership_rows()}
    assert rows[eng_department.name]["head_status"] == "Not assigned"
    assert rows[eng_department.name]["governance"] == "NO HEAD"

    eng_department.head = checker
    eng_department.save(update_fields=["head"])
    checker.is_active = False
    checker.save(update_fields=["is_active"])
    rows = {row["department"]: row for row in ownership_rows()}
    assert rows[eng_department.name]["head_status"] == "Inactive account"
    assert rows[eng_department.name]["head"] == ""


def test_the_register_includes_a_department_that_was_stood_down(eng_department):
    eng_department.is_active = False
    eng_department.save(update_fields=["is_active"])
    row = next(r for r in ownership_rows() if r["department"] == eng_department.name)
    assert row["is_active"] is False
    assert row["governance"] == "Stood down"


# --------------------------------------------------------------------------- #
# The impact review: leave approval says WHEN it is covering a gap
# --------------------------------------------------------------------------- #
def test_hr_covering_for_a_missing_head_is_audited_as_exactly_that(
        api, maker, admin, annual):
    """
    Phase DEPARTMENT-GOVERNANCE-HARDENING. HR still decides - nobody is blocked
    from taking leave over a configuration gap - but the trail used to record it
    as a 'department_head' decision, which made an unowned department invisible
    in the one place somebody would audit it.
    """
    from datetime import timedelta

    from audit.models import AuditLog
    from leaves.models import Leave
    from users.models import User
    from .conftest import MONDAY

    hr = User.objects.create_user(
        username="gov_hr", email="gov_hr@nif.test", password="pass12345",
        first_name="Gov", last_name="HR", role=User.Roles.APPROVER)
    # maker's department has no active Department Head at all.
    assert not User.objects.filter(role=User.Roles.CHECKER, is_active=True).exists()

    leave = Leave.objects.create(
        user=maker, leave_type="annual", start_date=MONDAY,
        end_date=MONDAY + timedelta(days=1), reason="family",
        status=Leave.Status.PENDING)

    api.force_authenticate(hr)
    res = api.post(f"/api/v1/leaves/{leave.id}/dept-head-review/",
                   {"decision": "approve", "remarks": "Approved by HR."},
                   format="json")
    assert res.status_code == 200, res.data

    row = AuditLog.objects.filter(object_id=str(leave.id),
                                  action=AuditLog.Action.APPROVE).latest("created_at")
    assert row.changes["stage"] == "hr_fallback_no_department_head"
    # And WHICH department was unowned, so the trail is actionable.
    assert row.changes["department"]


def test_a_real_department_head_decision_is_still_recorded_as_one(
        api, maker, checker, annual):
    from datetime import timedelta

    from audit.models import AuditLog
    from leaves.models import Leave
    from .conftest import MONDAY

    leave = Leave.objects.create(
        user=maker, leave_type="annual", start_date=MONDAY,
        end_date=MONDAY + timedelta(days=1), reason="family",
        status=Leave.Status.PENDING)
    api.force_authenticate(checker)
    res = api.post(f"/api/v1/leaves/{leave.id}/dept-head-review/",
                   {"decision": "approve", "remarks": "Approved."}, format="json")
    assert res.status_code == 200, res.data
    row = AuditLog.objects.filter(object_id=str(leave.id),
                                  action=AuditLog.Action.APPROVE).latest("created_at")
    assert row.changes["stage"] == "department_head"


def test_hr_is_told_why_the_request_reached_them(
        api, maker, django_capture_on_commit_callbacks):
    """
    HR used to be handed these with no sign they were covering a gap, so the gap
    stayed open: the person who could see it never learned it existed.
    """
    from datetime import timedelta

    from leaves.models import Leave
    from leaves.notifications import leave_submitted
    from notifications.models import Notification
    from users.models import User
    from .conftest import MONDAY

    hr = User.objects.create_user(
        username="gov_hr2", email="gov_hr2@nif.test", password="pass12345",
        first_name="Gov", last_name="HRTwo", role=User.Roles.APPROVER)
    leave = Leave.objects.create(
        user=maker, leave_type="annual", start_date=MONDAY,
        end_date=MONDAY + timedelta(days=1), reason="family",
        status=Leave.Status.PENDING)

    with django_capture_on_commit_callbacks(execute=True):
        leave_submitted(leave)

    note = Notification.objects.filter(recipient=hr).latest("created_at")
    assert "no active Department Head" in note.body
    assert "assign one" in note.body


# --------------------------------------------------------------------------- #
# The impact review: the surfaces that must verify a head exists
# --------------------------------------------------------------------------- #
def test_the_governance_endpoint_is_for_people_who_can_act_on_it(api, maker,
                                                                 checker, admin):
    url = "/api/v1/leaves/departments/governance/"
    api.force_authenticate(maker)
    assert api.get(url).status_code == 403

    for user in (checker, admin):
        api.force_authenticate(user)
        res = api.get(url)
        assert res.status_code == 200, user
        assert set(res.data) == {"total", "active", "missing_head",
                                 "missing_head_names", "status"}


def test_the_health_board_reports_the_gap_as_a_warning(admin):
    """
    AMBER, not red (corrected in OMS-FINAL-FREEZE-HARDENING). There is no
    acceptable number of departments with nobody answerable for them, but
    nothing is BLOCKED while it is true - leave falls back to HR and reporting
    has no owner - and a board that paints a fixable configuration gap the same
    colour as a dead database teaches people to read past both.
    """
    from monitoring import metrics

    board = metrics.collect()
    section = next(s for s in board["sections"] if s["key"] == "governance")
    missing = next(m for m in section["metrics"]
                   if m["key"] == "departments_missing_head")
    assert missing["value"] >= 1, "the seeded departments have no heads"
    assert missing["state"] == "amber"
    assert section["state"] == "amber"
    # It names them, so the board is actionable rather than only alarming.
    assert missing["detail"]


# The fourth surface in the Impact Review - the task module's Department
# Performance report - is asserted in tasks/tests/test_governance.py instead.
# Task Management may not be imported by another module (tasks/tests/
# test_independence.py), and that boundary holds for its test suite too: a rule
# the tests are exempt from is a rule that gets broken in the tests first.
