"""Derivation engine: punches -> attendance.Attendance."""
from datetime import date, datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from attendance import services as attendance_services
from attendance.models import Attendance
from biometric.derivation import (
    NaiveTimestampError,
    derive_daily_attendance,
    pending_days,
    process_unprocessed_punches,
    require_aware,
    select_boundary_punches,
    unmapped_backlog_count,
)
from biometric.models import AttendancePunch, BiometricEmployee
from leaves.models import Holiday, Leave, LeaveBalance

pytestmark = pytest.mark.django_db

DAY = date(2026, 8, 3)  # a Monday


@pytest.fixture
def punch(device, employee, mapping):
    def _make(hh, mm, code=0, day=DAY, user=employee, **extra):
        return AttendancePunch.objects.create(
            device=device, employee_device_id=mapping.device_user_id,
            biometric_employee=mapping, user=user,
            timestamp=timezone.make_aware(datetime(day.year, day.month, day.day, hh, mm)),
            punch=code, **extra,
        )
    return _make


def _record(employee, day=DAY):
    return Attendance.objects.filter(employee=employee, date=day).first()


# --------------------------------------------------------------------------
# Single / multiple punches
# --------------------------------------------------------------------------

def test_single_punch_is_an_arrival_not_a_zero_length_day(employee, punch):
    punch(9, 30, 0)
    derive_daily_attendance(employee, DAY)

    rec = _record(employee)
    assert rec.check_in is not None
    assert rec.check_out is None, "one punch must not fabricate a check-out"
    assert rec.working_hours == Decimal("0.00")
    assert rec.status == Attendance.Status.PRESENT
    assert rec.source == Attendance.Source.BIOMETRIC
    assert rec.marked_by == Attendance.MarkedBy.SYSTEM
    assert rec.punch_count == 1


def test_in_and_out_produce_hours_and_status(employee, punch):
    punch(9, 30, 0)
    punch(18, 0, 1)
    derive_daily_attendance(employee, DAY)

    rec = _record(employee)
    assert rec.working_hours == Decimal("8.50")
    assert rec.status == Attendance.Status.PRESENT
    assert rec.punch_count == 2


def test_late_arrival_via_device(employee, punch):
    punch(10, 45, 0)
    punch(19, 0, 1)
    derive_daily_attendance(employee, DAY)
    assert _record(employee).status == Attendance.Status.LATE


def test_short_day_via_device_is_half_day(employee, punch):
    punch(11, 0, 0)
    punch(14, 0, 1)
    derive_daily_attendance(employee, DAY)
    assert _record(employee).status == Attendance.Status.HALF_DAY


def test_many_punches_use_earliest_entry_and_latest_exit(employee, punch):
    punch(9, 15, 0)
    punch(12, 30, 1)
    punch(13, 15, 0)
    punch(17, 45, 1)
    derive_daily_attendance(employee, DAY)

    rec = _record(employee)
    assert timezone.localtime(rec.check_in).strftime("%H:%M") == "09:15"
    assert timezone.localtime(rec.check_out).strftime("%H:%M") == "17:45"
    assert rec.punch_count == 4
    assert rec.working_hours == Decimal("8.50")


# --------------------------------------------------------------------------
# Break and overtime punches
# --------------------------------------------------------------------------

def test_break_punches_are_counted_but_not_deducted(employee, punch):
    """Documented assumption: hours are a GROSS span. Net-of-breaks is Phase 9."""
    punch(9, 0, 0)
    punch(13, 0, 2)   # break_out
    punch(14, 0, 3)   # break_in
    punch(18, 0, 1)
    derive_daily_attendance(employee, DAY)

    rec = _record(employee)
    assert rec.punch_count == 4
    assert rec.working_hours == Decimal("9.00"), "break time is not subtracted in Phase 5"


def test_overtime_punches_extend_the_day(employee, punch):
    """The live device has 1,523 overtime_in / 866 overtime_out events, so
    ignoring codes 4/5 would understate hours."""
    punch(9, 0, 0)
    punch(17, 0, 1)
    punch(18, 0, 4)   # overtime_in
    punch(21, 0, 5)   # overtime_out
    derive_daily_attendance(employee, DAY)

    rec = _record(employee)
    assert timezone.localtime(rec.check_out).strftime("%H:%M") == "21:00"
    assert rec.working_hours == Decimal("12.00")


def test_day_of_only_exit_punches_falls_back(employee, punch):
    """A forgotten punch-in must not render a worked day as Absent."""
    punch(13, 0, 1)
    punch(18, 0, 1)
    derive_daily_attendance(employee, DAY)

    rec = _record(employee)
    assert rec.check_in is not None
    assert rec.status != Attendance.Status.ABSENT


def test_only_entry_punches_uses_last_as_exit(employee, punch):
    punch(9, 0, 0)
    punch(18, 0, 4)
    derive_daily_attendance(employee, DAY)
    assert _record(employee).working_hours == Decimal("9.00")


def test_select_boundary_punches_on_empty_input():
    assert select_boundary_punches([]) == (None, None)


# --------------------------------------------------------------------------
# Precedence: HR > Biometric > Browser
# --------------------------------------------------------------------------

def test_biometric_overrides_a_browser_check_in(employee, punch, local_browser_record):
    rec = local_browser_record(check_in_hour=9, check_out_hour=17)
    punch(8, 45, 0)
    punch(18, 30, 1)
    derive_daily_attendance(employee, DAY)

    rec.refresh_from_db()
    assert rec.source == Attendance.Source.BIOMETRIC
    assert timezone.localtime(rec.check_in).strftime("%H:%M") == "08:45"
    # The employee's own times are preserved so the override stays reversible.
    assert timezone.localtime(rec.browser_check_in).strftime("%H:%M") == "09:00"
    assert timezone.localtime(rec.browser_check_out).strftime("%H:%M") == "17:00"


def test_browser_attendance_survives_when_there_are_no_punches(employee, local_browser_record):
    rec = local_browser_record(check_in_hour=9, check_out_hour=17)
    derive_daily_attendance(employee, DAY)

    rec.refresh_from_db()
    assert rec.source == Attendance.Source.BROWSER
    assert timezone.localtime(rec.check_in).strftime("%H:%M") == "09:00"


@pytest.mark.parametrize("marker", ["source", "marked_by"])
def test_hr_override_is_never_overwritten(employee, punch, marker):
    """Both markers are honoured: rows written before Phase 5 only carry
    marked_by, so checking `source` alone would clobber them."""
    kwargs = ({"source": Attendance.Source.HR, "marked_by": Attendance.MarkedBy.SELF}
              if marker == "source"
              else {"source": Attendance.Source.BROWSER, "marked_by": Attendance.MarkedBy.HR})
    hr_ci = timezone.make_aware(datetime(DAY.year, DAY.month, DAY.day, 10, 0))
    rec = Attendance.objects.create(
        employee=employee, date=DAY, check_in=hr_ci,
        status=Attendance.Status.HALF_DAY, working_hours=Decimal("4.00"),
        remarks="Approved early leave", **kwargs)

    punch(8, 0, 0)
    punch(20, 0, 1)
    derive_daily_attendance(employee, DAY)

    rec.refresh_from_db()
    assert rec.status == Attendance.Status.HALF_DAY
    assert rec.working_hours == Decimal("4.00")
    assert timezone.localtime(rec.check_in).strftime("%H:%M") == "10:00"
    assert rec.remarks == "Approved early leave"
    # Metadata IS recorded, so the raw evidence behind the correction is visible.
    assert rec.punch_count == 2
    assert rec.first_punch_at is not None


def test_force_overrides_hr_protection(employee, punch):
    rec = Attendance.objects.create(
        employee=employee, date=DAY, source=Attendance.Source.HR,
        marked_by=Attendance.MarkedBy.HR, status=Attendance.Status.HALF_DAY)
    punch(9, 0, 0)
    punch(18, 0, 1)
    derive_daily_attendance(employee, DAY, force=True)

    rec.refresh_from_db()
    assert rec.source == Attendance.Source.BIOMETRIC
    assert rec.status == Attendance.Status.PRESENT


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------

def test_derivation_is_idempotent_over_many_runs(employee, punch):
    punch(9, 15, 0)
    punch(13, 0, 2)
    punch(14, 0, 3)
    punch(18, 45, 1)

    derive_daily_attendance(employee, DAY)
    first = _record(employee)
    snapshot = (first.check_in, first.check_out, first.status,
                first.working_hours, first.punch_count, first.source)

    for _ in range(100):
        derive_daily_attendance(employee, DAY)

    assert Attendance.objects.filter(employee=employee, date=DAY).count() == 1
    rec = _record(employee)
    assert (rec.check_in, rec.check_out, rec.status,
            rec.working_hours, rec.punch_count, rec.source) == snapshot
    assert rec.id == first.id, "must update in place, never recreate"


def test_repeated_derivation_does_not_corrupt_the_browser_snapshot(
        employee, punch, local_browser_record):
    """The snapshot is taken once. Re-deriving must not overwrite it with the
    biometric times it already replaced."""
    local_browser_record(check_in_hour=9, check_out_hour=17)
    punch(8, 45, 0)
    for _ in range(5):
        derive_daily_attendance(employee, DAY)
    rec = _record(employee)
    assert timezone.localtime(rec.browser_check_in).strftime("%H:%M") == "09:00"


def test_punches_are_marked_processed(employee, punch):
    punch(9, 0, 0)
    punch(18, 0, 1)
    assert AttendancePunch.objects.filter(is_processed=False).count() == 2
    derive_daily_attendance(employee, DAY)
    assert AttendancePunch.objects.filter(is_processed=False).count() == 0
    assert AttendancePunch.objects.filter(processed_at__isnull=True).count() == 0


def test_punches_are_never_mutated_by_derivation(employee, punch):
    p = punch(9, 0, 0)
    original = (p.timestamp, p.punch, p.punch_label, p.local_date, p.employee_device_id)
    derive_daily_attendance(employee, DAY)
    p.refresh_from_db()
    assert (p.timestamp, p.punch, p.punch_label, p.local_date, p.employee_device_id) == original


# --------------------------------------------------------------------------
# Revert path (unmap / remap)
# --------------------------------------------------------------------------

def test_losing_all_punches_deletes_a_purely_derived_row(employee, punch):
    punch(9, 0, 0)
    derive_daily_attendance(employee, DAY)
    assert _record(employee) is not None

    AttendancePunch.objects.update(user=None)
    assert derive_daily_attendance(employee, DAY) is None
    assert _record(employee) is None


def test_losing_all_punches_restores_the_browser_check_in(employee, punch,
                                                          local_browser_record):
    local_browser_record(check_in_hour=9, check_out_hour=17)
    punch(8, 45, 0)
    punch(18, 30, 1)
    derive_daily_attendance(employee, DAY)

    AttendancePunch.objects.update(user=None)
    derive_daily_attendance(employee, DAY)

    rec = _record(employee)
    assert rec is not None, "the employee's own check-in must not be destroyed"
    assert rec.source == Attendance.Source.BROWSER
    assert timezone.localtime(rec.check_in).strftime("%H:%M") == "09:00"
    assert timezone.localtime(rec.check_out).strftime("%H:%M") == "17:00"
    assert rec.browser_check_in is None
    assert rec.punch_count == 0


def test_revert_never_touches_an_hr_row(employee):
    rec = Attendance.objects.create(
        employee=employee, date=DAY, source=Attendance.Source.HR,
        marked_by=Attendance.MarkedBy.HR, status=Attendance.Status.PRESENT)
    assert derive_daily_attendance(employee, DAY) is not None
    rec.refresh_from_db()
    assert rec.status == Attendance.Status.PRESENT


def test_unmapping_reverts_derived_attendance_end_to_end(employee, hr, mapping, punch):
    """The Phase 4 -> Phase 5 handoff."""
    from biometric.services import unmap_employee

    punch(9, 0, 0)
    punch(18, 0, 1)
    derive_daily_attendance(employee, DAY)
    assert _record(employee) is not None

    unmap_employee(mapping, actor=hr)
    assert _record(employee) is None, "unmap must roll back the attendance it produced"


# --------------------------------------------------------------------------
# Timezone safety
# --------------------------------------------------------------------------

def test_naive_timestamps_are_rejected():
    with pytest.raises(NaiveTimestampError):
        require_aware(datetime(2026, 8, 3, 9, 0))
    assert require_aware(None) is None


def test_just_after_midnight_belongs_to_that_day(employee, punch):
    """00:15 Kathmandu is 18:30 UTC the PREVIOUS day — a naive UTC reading would
    file this punch under the wrong date."""
    p = punch(0, 15, 0)
    assert p.local_date == DAY
    derive_daily_attendance(employee, DAY)
    rec = _record(employee)
    assert rec.date == DAY
    assert timezone.localtime(rec.check_in).strftime("%H:%M") == "00:15"


def test_just_before_midnight_belongs_to_that_day(employee, punch):
    p = punch(23, 55, 0)
    assert p.local_date == DAY
    derive_daily_attendance(employee, DAY)
    assert _record(employee).date == DAY


def test_a_day_spanning_both_midnight_edges(employee, punch):
    punch(0, 15, 0)
    punch(23, 55, 1)
    derive_daily_attendance(employee, DAY)
    rec = _record(employee)
    assert rec.working_hours == Decimal("23.67")
    assert rec.punch_count == 2


@pytest.mark.parametrize("month", [1, 3, 6, 7, 9, 11, 12])
def test_local_date_is_stable_across_the_year(employee, punch, month):
    """Asia/Kathmandu is a fixed UTC+05:45 and has never observed DST, so the
    local-date mapping must not shift in any month."""
    day = date(2026, month, 15)
    p = punch(0, 30, 0, day=day)
    assert p.local_date == day
    assert timezone.localtime(p.timestamp).hour == 0


# ----------------------------------------------------------------------
# Leave / holiday interaction (resolve_day_status must stay untouched)
# ----------------------------------------------------------------------

def test_biometric_presence_outranks_approved_leave(employee, punch):
    """They came in despite the leave — the punch is ground truth."""
    Leave.objects.create(
        user=employee, leave_type=LeaveBalance.LeaveType.ANNUAL,
        start_date=DAY, end_date=DAY, reason="t", status=Leave.Status.APPROVED)
    punch(9, 0, 0)
    punch(18, 0, 1)
    derive_daily_attendance(employee, DAY)

    assert attendance_services.effective_status(
        employee, DAY, _record(employee)) == Attendance.Status.PRESENT


def test_leave_still_shows_when_there_is_no_punch(employee):
    Leave.objects.create(
        user=employee, leave_type=LeaveBalance.LeaveType.ANNUAL,
        start_date=DAY, end_date=DAY, reason="t", status=Leave.Status.APPROVED)
    derive_daily_attendance(employee, DAY)
    assert attendance_services.effective_status(
        employee, DAY, _record(employee)) == Attendance.Status.ON_LEAVE


def test_biometric_presence_outranks_a_public_holiday(employee, punch):
    """Weekend/holiday work is real work — and the input Phase 10 needs for
    compensatory leave."""
    Holiday.objects.create(date=DAY, name="Dashain", is_active=True)
    punch(9, 0, 0)
    punch(18, 0, 1)
    derive_daily_attendance(employee, DAY)

    assert attendance_services.effective_status(
        employee, DAY, _record(employee)) == Attendance.Status.PRESENT


def test_holiday_still_outranks_leave_when_no_punch_exists(employee):
    """Phase 5 must NOT have changed this pre-existing precedence."""
    Holiday.objects.create(date=DAY, name="Dashain", is_active=True)
    Leave.objects.create(
        user=employee, leave_type=LeaveBalance.LeaveType.ANNUAL,
        start_date=DAY, end_date=DAY, reason="t", status=Leave.Status.APPROVED)
    assert attendance_services.effective_status(employee, DAY) == Attendance.Status.HOLIDAY


def test_saturday_punch_creates_a_present_row(employee, punch):
    saturday = date(2026, 8, 1)
    assert saturday.weekday() == 5
    punch(10, 0, 0, day=saturday)
    punch(15, 0, 1, day=saturday)
    derive_daily_attendance(employee, saturday)

    rec = _record(employee, saturday)
    assert rec.status == Attendance.Status.PRESENT
    assert attendance_services.effective_status(employee, saturday, rec) == Attendance.Status.PRESENT


# -------------------
# Queue processing
# -------------------

def test_pending_days_excludes_unmapped_punches(employee, punch, device):
    punch(9, 0, 0)
    AttendancePunch.objects.create(
        device=device, employee_device_id="99", user=None,
        timestamp=timezone.make_aware(datetime(2026, 8, 3, 10, 0)), punch=0)

    assert pending_days() == [(employee.pk, DAY)]
    assert unmapped_backlog_count() == 1


def test_process_unprocessed_punches_summary(employee, punch, device):
    punch(9, 0, 0)
    punch(18, 0, 1)
    AttendancePunch.objects.create(
        device=device, employee_device_id="99", user=None,
        timestamp=timezone.make_aware(datetime(2026, 8, 3, 10, 0)), punch=0)

    summary = process_unprocessed_punches()
    assert summary == {"days_processed": 1, "derived": 1, "reverted": 0,
                       "failed": 0, "unmapped_backlog": 1}
    assert _record(employee) is not None
    assert process_unprocessed_punches()["days_processed"] == 0, "nothing left to do"


def test_process_handles_multiple_employees_and_days(employee, other_employee, device,
                                                     mapping, second_device):
    other_map = BiometricEmployee.objects.create(
        device=second_device, device_user_id="2", user=other_employee)
    for day in (date(2026, 8, 3), date(2026, 8, 4)):
        for user, dev, m in ((employee, device, mapping),
                             (other_employee, second_device, other_map)):
            AttendancePunch.objects.create(
                device=dev, employee_device_id=m.device_user_id, biometric_employee=m,
                user=user,
                timestamp=timezone.make_aware(datetime(day.year, day.month, day.day, 9, 0)),
                punch=0)

    summary = process_unprocessed_punches()
    assert summary["days_processed"] == 4
    assert Attendance.objects.count() == 4
