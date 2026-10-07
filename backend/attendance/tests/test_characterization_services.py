"""Characterization tests for attendance business logic.

Written BEFORE the biometric derivation engine touches this app. These assert
what the code does *today*, not what it ideally should — their job is to fail
loudly if Phase 5 changes existing behaviour. Nothing here should need editing
when derivation lands.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from attendance import services
from attendance.models import Attendance
from leaves.models import Leave, LeaveBalance

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# Working hours
# --------------------------------------------------------------------------

def test_working_hours_is_a_gross_span_to_two_decimals(local):
    ci = local(2026, 8, 3, 9, 0)
    assert services.compute_working_hours(ci, local(2026, 8, 3, 17, 0)) == Decimal("8.00")
    assert services.compute_working_hours(ci, local(2026, 8, 3, 13, 30)) == Decimal("4.50")


def test_working_hours_rounds_half_up(local):
    ci = local(2026, 8, 3, 9, 0)
    # 7h 59m 42s -> 7.995 -> 8.00 (not 7.99)
    assert services.compute_working_hours(ci, local(2026, 8, 3, 16, 59, 42)) == Decimal("8.00")


def test_working_hours_is_zero_when_incomplete_or_inverted(local):
    ci = local(2026, 8, 3, 9, 0)
    assert services.compute_working_hours(ci, None) == Decimal("0.00")
    assert services.compute_working_hours(None, ci) == Decimal("0.00")
    assert services.compute_working_hours(ci, ci) == Decimal("0.00")
    assert services.compute_working_hours(ci, local(2026, 8, 3, 8, 0)) == Decimal("0.00")


# --------------------------------------------------------------------------
# Status recomputation
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def legacy_global_rules(db):
    """Characterise the office-start + grace rules, whatever today's date is.

    These tests document the engine maths that a policy with an empty
    late_after_time uses (and that the settings fallback uses). The NIF arrival
    rules introduced in 8.1 are characterised separately in test_arrival_rules.py.
    """
    from attendance.models import PolicyAssignment

    PolicyAssignment.objects.filter(policy__name="NIF Attendance Policy").delete()
    PolicyAssignment.objects.filter(policy__name="Global Policy").update(effective_until=None)



def test_on_time_full_day_is_present(employee, local):
    rec = Attendance(employee=employee, date=local(2026, 8, 3).date(),
                     check_in=local(2026, 8, 3, 9, 30), check_out=local(2026, 8, 3, 18, 0))
    services.recompute_status(rec)
    assert rec.status == Attendance.Status.PRESENT
    assert rec.working_hours == Decimal("8.50")


def test_arriving_after_office_start_is_late(employee, local):
    """Office start is 10:00; 10:01 is Late."""
    rec = Attendance(employee=employee, date=local(2026, 8, 3).date(),
                     check_in=local(2026, 8, 3, 10, 1), check_out=local(2026, 8, 3, 19, 0))
    services.recompute_status(rec)
    assert rec.status == Attendance.Status.LATE


def test_exactly_office_start_is_not_late(employee, local):
    """is_late is a strict >, so 10:00:00 on the dot counts as on time."""
    rec = Attendance(employee=employee, date=local(2026, 8, 3).date(),
                     check_in=local(2026, 8, 3, 10, 0), check_out=local(2026, 8, 3, 19, 0))
    services.recompute_status(rec)
    assert rec.status == Attendance.Status.PRESENT


def test_short_day_is_half_day_and_outranks_lateness(employee, local):
    """Below 5 hours wins over the late/present decision entirely."""
    rec = Attendance(employee=employee, date=local(2026, 8, 3).date(),
                     check_in=local(2026, 8, 3, 11, 0), check_out=local(2026, 8, 3, 14, 0))
    services.recompute_status(rec)
    assert rec.status == Attendance.Status.HALF_DAY


def test_check_in_without_check_out_keeps_zero_hours(employee, local):
    rec = Attendance(employee=employee, date=local(2026, 8, 3).date(),
                     check_in=local(2026, 8, 3, 9, 0))
    services.recompute_status(rec)
    assert rec.status == Attendance.Status.PRESENT
    assert rec.working_hours == Decimal("0.00")


def test_no_times_is_absent(employee, local):
    rec = Attendance(employee=employee, date=local(2026, 8, 3).date())
    services.recompute_status(rec)
    assert rec.status == Attendance.Status.ABSENT


# --------------------------------------------------------------------------
# Holidays
# --------------------------------------------------------------------------

def test_saturday_is_a_holiday(a_saturday):
    assert services.is_holiday(a_saturday) is True
    assert services.holiday_name(a_saturday) == "Saturday (Weekly Holiday)"


def test_sunday_is_a_working_day(a_saturday):
    """Nepal's weekend is Saturday only — Sunday must stay a working day."""
    assert services.is_holiday(a_saturday + timedelta(days=1)) is False


def test_active_public_holiday(a_weekday, public_holiday):
    public_holiday(a_weekday, name="Dashain")
    assert services.is_holiday(a_weekday) is True
    assert services.holiday_name(a_weekday) == "Dashain"


def test_inactive_holiday_is_ignored(a_weekday, public_holiday):
    h = public_holiday(a_weekday)
    h.is_active = False
    h.save(update_fields=["is_active"])
    assert services.is_holiday(a_weekday) is False


# --------------------------------------------------------------------------
# Absent floor
# --------------------------------------------------------------------------

def test_absent_floor_takes_the_later_of_registration_and_joining(employee):
    """A backdated date_of_joining can only push the floor later, never earlier."""
    floor = services.absent_floor(employee)
    registration = timezone.localtime(employee.date_joined).date()
    assert floor == max(registration, employee.date_of_joining)


def test_tracking_start_pushes_the_floor_later(employee, settings):
    later = timezone.localdate() + timedelta(days=5)
    settings.ATTENDANCE_TRACKING_START = later.isoformat()
    assert services.absent_floor(employee) == later


def test_malformed_tracking_start_is_ignored(settings):
    settings.ATTENDANCE_TRACKING_START = "not-a-date"
    assert services.attendance_tracking_start() is None


# --------------------------------------------------------------------------
# resolve_day_status — THE precedence contract
# --------------------------------------------------------------------------

def _resolve(employee, d, record=None, holiday=False, leave=False):
    return services.resolve_day_status(
        record=record, is_holiday_day=holiday, is_leave_day=leave, d=d,
        floor=services.absent_floor(employee), today=timezone.localdate(),
    )


def test_real_check_in_outranks_holiday_and_leave(employee, a_weekday, make_attendance, local):
    """A stored check-in is ground truth and beats every derived status."""
    rec = make_attendance(a_weekday, check_in=local(a_weekday.year, a_weekday.month, a_weekday.day, 9, 0))
    services.recompute_status(rec)
    rec.save()
    assert _resolve(employee, a_weekday, rec, holiday=True, leave=True) == Attendance.Status.PRESENT


def test_holiday_outranks_leave(employee, a_weekday):
    """Documented precedence: a holiday during approved leave shows Holiday, so
    the holiday does not visually consume the employee's leave."""
    assert _resolve(employee, a_weekday, holiday=True, leave=True) == Attendance.Status.HOLIDAY


def test_leave_outranks_absent(employee, a_weekday):
    assert _resolve(employee, a_weekday, leave=True) == Attendance.Status.ON_LEAVE


def test_manual_record_without_check_in_loses_to_holiday_and_leave(
        employee, a_weekday, make_attendance):
    """Current behaviour: a stored HR row with no check-in ranks BELOW holiday
    and leave. Phase 5 must not change this."""
    rec = make_attendance(a_weekday, status=Attendance.Status.HALF_DAY,
                          marked_by=Attendance.MarkedBy.HR)
    assert _resolve(employee, a_weekday, rec, holiday=True) == Attendance.Status.HOLIDAY
    assert _resolve(employee, a_weekday, rec, leave=True) == Attendance.Status.ON_LEAVE
    assert _resolve(employee, a_weekday, rec) == Attendance.Status.HALF_DAY


def test_past_working_day_with_no_record_is_absent(employee, a_weekday):
    assert _resolve(employee, a_weekday) == Attendance.Status.ABSENT


def test_day_before_the_floor_is_not_applicable(employee):
    """Not Absent — excluded entirely."""
    before = services.absent_floor(employee) - timedelta(days=1)
    assert _resolve(employee, before) is None
    assert _resolve(employee, before, holiday=True) is None, "floor is checked before holiday"


def test_future_day_is_not_applicable(employee):
    assert _resolve(employee, timezone.localdate() + timedelta(days=3)) is None


def test_today_is_not_absent_before_the_cutoff(employee):
    """Today only becomes eligible for Absent after the 18:00 window closes."""
    today = timezone.localdate()
    morning = timezone.make_aware(
        timezone.datetime(today.year, today.month, today.day, 9, 0))
    assert services.resolve_day_status(
        record=None, is_holiday_day=False, is_leave_day=False, d=today,
        floor=services.absent_floor(employee), today=today, now=morning) is None


def test_today_is_absent_after_the_cutoff(employee):
    today = timezone.localdate()
    evening = timezone.make_aware(
        timezone.datetime(today.year, today.month, today.day, 19, 0))
    assert services.resolve_day_status(
        record=None, is_holiday_day=False, is_leave_day=False, d=today,
        floor=services.absent_floor(employee), today=today, now=evening
    ) == Attendance.Status.ABSENT


# --------------------------------------------------------------------------
# Leave integration
# --------------------------------------------------------------------------

def test_approved_leave_is_detected(employee, a_weekday):
    Leave.objects.create(
        user=employee, leave_type=LeaveBalance.LeaveType.ANNUAL,
        start_date=a_weekday, end_date=a_weekday, reason="test",
        status=Leave.Status.APPROVED,
    )
    assert services.has_approved_leave(employee, a_weekday) is True
    assert services.effective_status(employee, a_weekday) == Attendance.Status.ON_LEAVE


def test_pending_leave_is_not_counted(employee, a_weekday):
    Leave.objects.create(
        user=employee, leave_type=LeaveBalance.LeaveType.ANNUAL,
        start_date=a_weekday, end_date=a_weekday, reason="test",
        status=Leave.Status.PENDING,
    )
    assert services.has_approved_leave(employee, a_weekday) is False


def test_soft_deleted_leave_is_not_counted(employee, a_weekday):
    Leave.objects.create(
        user=employee, leave_type=LeaveBalance.LeaveType.ANNUAL,
        start_date=a_weekday, end_date=a_weekday, reason="test",
        status=Leave.Status.APPROVED, is_deleted=True,
    )
    assert services.has_approved_leave(employee, a_weekday) is False


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------

def test_calendar_skips_not_applicable_days_and_counts_the_rest(employee, local):
    today = timezone.localdate()
    cal = services.build_calendar(employee, today.year, today.month)
    assert cal["year"] == today.year and cal["month"] == today.month
    assert all(day["status"] is not None for day in cal["days"])
    assert sum(cal["summary"].values()) == len(cal["days"])


def test_calendar_includes_bs_dates_and_holiday_names(employee):
    today = timezone.localdate()
    cal = services.build_calendar(employee, today.year, today.month)
    for day in cal["days"]:
        assert day["date_bs"]
        if day["status"] == Attendance.Status.HOLIDAY:
            assert day["holiday_name"]


def test_calendar_reflects_a_stored_check_in(employee, make_attendance, local,
                                             a_weekday_this_month):
    d = a_weekday_this_month
    rec = make_attendance(d, check_in=local(d.year, d.month, d.day, 9, 0))
    services.recompute_status(rec)
    rec.save()
    cal = services.build_calendar(employee, d.year, d.month)
    entry = next(day for day in cal["days"] if day["date"] == d.isoformat())
    assert entry["status"] == Attendance.Status.PRESENT
    assert entry["check_in"] is not None


# --------------------------------------------------------------------------
# Coordinates
# --------------------------------------------------------------------------

def test_parse_coordinates_accepts_both_key_spellings():
    assert services.parse_coordinates({"latitude": "27.7", "longitude": "85.3"})["lat"] == Decimal("27.700000")
    assert services.parse_coordinates({"lat": "27.7", "lng": "85.3"})["lng"] == Decimal("85.300000")


def test_parse_coordinates_drops_anything_invalid():
    """Location is optional — bad input must never break a check-in."""
    assert services.parse_coordinates({}) == {}
    assert services.parse_coordinates({"latitude": "abc", "longitude": "85.3"}) == {}
    assert services.parse_coordinates({"latitude": "91", "longitude": "85.3"}) == {}
    assert services.parse_coordinates({"latitude": "27.7", "longitude": "181"}) == {}
