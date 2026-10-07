"""Compensatory off: earning, idempotency, and the HR confirm/reject workflow.

Covers the required cases holiday work, saturday work, comp off, comp confirm,
comp reject and re-derivation idempotency — plus risk R5 (no retroactive
earning) and the constraint behind risk R3.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from attendance.models import Attendance, AttendancePolicy, PolicyAssignment
from attendance.services import recompute_status
from leaves.category_engine import comp_summary
from leaves.models import CompensatoryLedger

pytestmark = pytest.mark.django_db

FLOOR = date(2000, 1, 1)
PENDING_URL = "/api/v1/leaves/compensatory/pending/"


@pytest.fixture
def comp_policy(employee):
    """A policy with comp-off switched on, applied to the test employee."""
    policy = AttendancePolicy.objects.create(
        name="Comp enabled", comp_off_enabled=True,
        comp_off_min_hours=Decimal("4.00"),
        comp_off_half_day_hours=Decimal("4.00"),
        comp_off_full_day_hours=Decimal("6.00"))
    PolicyAssignment.objects.create(
        policy=policy, scope=PolicyAssignment.Scope.USER, user=employee,
        effective_from=FLOOR)
    return policy


@pytest.fixture
def work(local, django_capture_on_commit_callbacks):
    """Record a worked day and run the comp-off sync it queues.

    The ledger write is deliberately deferred to ``transaction.on_commit`` so a
    rolled-back derivation cannot leave a comp entry behind. pytest-django wraps
    each test in a transaction that never commits, so the callback has to be
    drained explicitly — that is what this fixture does.
    """
    def _work(employee, day, start=(10, 0), end=(18, 0)):
        # Reuses the existing row rather than inserting a second one, so calling
        # this repeatedly models a re-derivation of the same day (unique_together
        # on employee+date) instead of hitting an integrity error.
        with django_capture_on_commit_callbacks(execute=True):
            record = (Attendance.objects.filter(employee=employee, date=day).first()
                      or Attendance(employee=employee, date=day))
            record.check_in = local(day.year, day.month, day.day, *start)
            record.check_out = local(day.year, day.month, day.day, *end)
            recompute_status(record)
            record.save()
        return record
    return _work


def earns(employee):
    return CompensatoryLedger.objects.filter(
        user=employee, entry_type=CompensatoryLedger.EntryType.EARN,
        source=CompensatoryLedger.Source.ATTENDANCE)


# --------------------------------------------------------------------------
# earning
# --------------------------------------------------------------------------
def test_comp_off_is_disabled_by_default(employee, a_saturday, work):
    record = work(employee, a_saturday)
    assert record.comp_off_days == Decimal("0.00")
    assert record.comp_off_eligible is False
    assert earns(employee).count() == 0


def test_saturday_work_earns_comp(employee, comp_policy, a_saturday, work):
    record = work(employee, a_saturday, (10, 0), (17, 0))
    assert record.comp_off_days == Decimal("1.00")
    assert record.comp_off_eligible is True


def test_short_saturday_work_earns_half_a_day(employee, comp_policy, a_saturday, work):
    record = work(employee, a_saturday, (10, 0), (15, 0))  # 5h
    assert record.comp_off_days == Decimal("0.50")


def test_very_short_saturday_work_earns_nothing(employee, comp_policy, a_saturday, work):
    record = work(employee, a_saturday, (10, 0), (12, 0))  # 2h
    assert record.comp_off_days == Decimal("0.00")
    assert record.comp_off_eligible is False


def test_holiday_work_earns_comp(employee, comp_policy, a_weekday, public_holiday, work):
    public_holiday(a_weekday, "Dashain")
    record = work(employee, a_weekday, (10, 0), (17, 0))
    assert record.comp_off_days == Decimal("1.00")


def test_an_ordinary_weekday_earns_nothing(employee, comp_policy, a_weekday, work):
    record = work(employee, a_weekday, (10, 0), (20, 0))
    assert record.comp_off_days == Decimal("0.00")


def test_saturday_earning_can_be_switched_off(employee, comp_policy, a_saturday, work):
    comp_policy.comp_off_on_saturday = False
    comp_policy.save()
    assert work(employee, a_saturday, (10, 0), (18, 0)).comp_off_days == Decimal("0.00")


def test_comp_is_not_earned_before_the_assignment_took_effect(employee, a_saturday, work):
    """R5: enabling comp-off must not mint leave out of historical Saturdays."""
    policy = AttendancePolicy.objects.create(name="Late comp", comp_off_enabled=True)
    PolicyAssignment.objects.create(
        policy=policy, scope=PolicyAssignment.Scope.USER, user=employee,
        effective_from=a_saturday + timedelta(days=1))
    assert work(employee, a_saturday, (10, 0), (18, 0)).comp_off_days == Decimal("0.00")


# --------------------------------------------------------------------------
# ledger integration + idempotency
# --------------------------------------------------------------------------
def test_a_ledger_entry_is_created_pending(employee, comp_policy, a_saturday, work):
    work(employee, a_saturday, (10, 0), (17, 0))
    entry = earns(employee).get()
    assert entry.status == CompensatoryLedger.Status.PENDING
    assert entry.days == Decimal("1.00")
    assert entry.source_date == a_saturday


def test_a_pending_earn_is_not_spendable(employee, comp_policy, a_saturday, work):
    work(employee, a_saturday, (10, 0), (17, 0))
    summary = comp_summary(employee)
    assert summary["available"] == Decimal("0.00")
    assert summary["pending"] == Decimal("1.00")


def test_re_derivation_is_idempotent(employee, comp_policy, a_saturday, work):
    """The required 're-derivation idempotency' case, and the reason the
    partial unique constraint exists (R3)."""
    for _ in range(5):
        work(employee, a_saturday, (10, 0), (17, 0))
    assert earns(employee).count() == 1
    assert comp_summary(employee)["pending"] == Decimal("1.00")


def test_re_derivation_does_not_reopen_a_confirmed_entry(employee, comp_policy, a_saturday, work):
    work(employee, a_saturday, (10, 0), (17, 0))
    entry = earns(employee).get()
    entry.status = CompensatoryLedger.Status.CONFIRMED
    entry.save()

    work(employee, a_saturday, (10, 0), (17, 0))
    entry.refresh_from_db()
    assert entry.status == CompensatoryLedger.Status.CONFIRMED


def test_a_day_that_stops_qualifying_withdraws_its_pending_entry(
        employee, comp_policy, a_saturday, work):
    work(employee, a_saturday, (10, 0), (17, 0))
    assert earns(employee).count() == 1

    work(employee, a_saturday, (10, 0), (11, 0))  # 1h — no longer qualifies
    assert earns(employee).count() == 0


def test_a_confirmed_entry_is_never_withdrawn_automatically(
        employee, comp_policy, a_saturday, work):
    work(employee, a_saturday, (10, 0), (17, 0))
    earns(employee).update(status=CompensatoryLedger.Status.CONFIRMED)

    work(employee, a_saturday, (10, 0), (11, 0))
    assert earns(employee).count() == 1, "granted days belong to the employee"


def test_hr_grants_are_untouched_by_the_constraint(employee, hr):
    """The partial unique index is scoped to source='attendance', so HR may
    still grant several days for the same date."""
    for _ in range(2):
        CompensatoryLedger.objects.create(
            user=employee, entry_type=CompensatoryLedger.EntryType.EARN,
            days=Decimal("1.00"), source=CompensatoryLedger.Source.HR_GRANT,
            status=CompensatoryLedger.Status.CONFIRMED,
            source_date=date(2026, 3, 1), approved_by=hr)
    assert CompensatoryLedger.objects.filter(
        source=CompensatoryLedger.Source.HR_GRANT).count() == 2


# --------------------------------------------------------------------------
# HR confirm / reject  (closes G18)
# --------------------------------------------------------------------------
def test_the_pending_queue_lists_attendance_earns(employee, comp_policy, hr, auth,
                                                  a_saturday, work):
    work(employee, a_saturday, (10, 0), (17, 0))
    response = auth(hr).get(PENDING_URL)
    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["entries"][0]["days"] == 1.0


def test_the_pending_queue_is_hr_only(employee, comp_policy, auth, a_saturday, work):
    work(employee, a_saturday, (10, 0), (17, 0))
    assert auth(employee).get(PENDING_URL).status_code == 403


def test_comp_confirm_makes_the_day_spendable(employee, comp_policy, hr, auth,
                                              a_saturday, work):
    work(employee, a_saturday, (10, 0), (17, 0))
    entry = earns(employee).get()

    response = auth(hr).post(f"/api/v1/leaves/compensatory/{entry.id}/confirm/", {}, format="json")
    assert response.status_code == 200

    entry.refresh_from_db()
    assert entry.status == CompensatoryLedger.Status.CONFIRMED
    assert entry.approved_by_id == hr.id
    assert comp_summary(employee)["available"] == Decimal("1.00")


def test_comp_confirm_is_hr_only(employee, comp_policy, auth, a_saturday, work):
    work(employee, a_saturday, (10, 0), (17, 0))
    entry = earns(employee).get()
    assert auth(employee).post(
        f"/api/v1/leaves/compensatory/{entry.id}/confirm/", {}, format="json").status_code == 403


def test_comp_reject_removes_the_entry(employee, comp_policy, hr, auth, a_saturday, work):
    work(employee, a_saturday, (10, 0), (17, 0))
    entry = earns(employee).get()

    response = auth(hr).post(f"/api/v1/leaves/compensatory/{entry.id}/reject/",
                             {"reason": "Office was closed"}, format="json")
    assert response.status_code == 204
    assert earns(employee).count() == 0
    assert comp_summary(employee)["pending"] == Decimal("0.00")


def test_rejecting_is_recorded_in_the_audit_log(employee, comp_policy, hr, auth,
                                                a_saturday, work):
    from audit.models import AuditLog

    work(employee, a_saturday, (10, 0), (17, 0))
    entry = earns(employee).get()
    auth(hr).post(f"/api/v1/leaves/compensatory/{entry.id}/reject/",
                  {"reason": "Office was closed"}, format="json")

    log = AuditLog.objects.filter(changes__event="COMP_REJECT").first()
    assert log is not None
    assert log.changes["reason"] == "Office was closed"


def test_confirming_an_already_confirmed_entry_is_a_404(employee, comp_policy, hr, auth,
                                                        a_saturday, work):
    work(employee, a_saturday, (10, 0), (17, 0))
    entry = earns(employee).get()
    url = f"/api/v1/leaves/compensatory/{entry.id}/confirm/"
    assert auth(hr).post(url, {}, format="json").status_code == 200
    assert auth(hr).post(url, {}, format="json").status_code == 404


def test_confirmed_comp_unlocks_compensatory_leave(employee, comp_policy, hr, auth,
                                                   a_saturday, work):
    """The gate in applicable_type_codes() hides Compensatory at zero balance."""
    from leaves.category_engine import comp_available

    work(employee, a_saturday, (10, 0), (17, 0))
    assert comp_available(employee) == Decimal("0.00")

    entry = earns(employee).get()
    auth(hr).post(f"/api/v1/leaves/compensatory/{entry.id}/confirm/", {}, format="json")
    assert comp_available(employee) == Decimal("1.00")
