"""NIF arrival-based attendance rules (Phase 8.1).

    check-in <= 11:45   ->  Present
    11:45 < check-in < 13:00  ->  Late
    check-in >= 13:00   ->  Half Day
    no check-in, past the absent cut-off  ->  Absent

The boundaries are policy fields (``late_after_time`` / ``half_day_after_time``),
they move with an employee's shift, and they coexist with the duration-based
half-day rule that predates them.
"""
from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from attendance.models import (
    Attendance,
    AttendancePolicy,
    EmployeeShift,
    PolicyAssignment,
    Shift,
)
from attendance.policy import resolver
from attendance.services import recompute_status

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _location_capture_optional(settings):
    """Relax the geofence's mandatory-location gate for this module.

    ``ATTENDANCE_REQUIRE_LOCATION`` defaults ON, so a check-in with no
    coordinates is refused with 400. Nothing in this module is about location —
    these tests predate the geofence — and making each one post coordinates
    would test the geofence by accident and obscure what they actually assert.

    The gate itself is covered where it belongs, in ``test_geofence.py``, which
    uses this same override for its own non-location cases.
    """
    settings.ATTENDANCE_REQUIRE_LOCATION = False

FLOOR = date(2000, 1, 1)
NIF_POLICY = "NIF Attendance Policy"


@pytest.fixture
def nif(employee):
    """Put the employee on the NIF arrival rules regardless of the date.

    The cutover migration only applies them from its go-live date onward, which
    is the point — but a test asserting the rules themselves must not depend on
    where its fixture date falls relative to that.
    """
    policy = AttendancePolicy.objects.create(
        name="NIF rules (test)", office_start_time=time(10, 0),
        late_after_time=time(11, 45), half_day_after_time=time(13, 0),
        half_day_hours=Decimal("5.00"), full_day_hours=Decimal("8.00"))
    PolicyAssignment.objects.create(
        policy=policy, scope=PolicyAssignment.Scope.USER, user=employee,
        effective_from=FLOOR)
    return policy


@pytest.fixture
def arrive(employee, a_weekday, local):
    """Record a day for the employee and return the evaluated row."""
    def _arrive(hh, mm, out=(18, 30), user=None, day=None, ss=0, **extra):
        user = user or employee
        day = day or a_weekday
        record = Attendance(
            employee=user, date=day,
            check_in=local(day.year, day.month, day.day, hh, mm, ss),
            check_out=local(day.year, day.month, day.day, *out) if out else None,
            **extra)
        recompute_status(record)
        return record
    return _arrive


# --------------------------------------------------------------------------
# the ladder
# --------------------------------------------------------------------------
def test_early_arrival_is_present(nif, arrive):
    record = arrive(9, 30)
    assert record.status == Attendance.Status.PRESENT
    assert record.late_minutes == 0


def test_arriving_after_the_old_office_start_is_no_longer_late(nif, arrive):
    """The point of the change: 10:16 used to be Late."""
    record = arrive(10, 16)
    assert record.status == Attendance.Status.PRESENT
    assert record.late_minutes == 0


def test_just_before_the_late_boundary_is_present(nif, arrive):
    assert arrive(11, 44).status == Attendance.Status.PRESENT


def test_the_late_boundary_is_inclusive(nif, arrive):
    """11:45:00 exactly is still Present."""
    record = arrive(11, 45)
    assert record.status == Attendance.Status.PRESENT
    assert record.late_minutes == 0


def test_one_minute_past_the_boundary_is_late(nif, arrive):
    record = arrive(11, 46)
    assert record.status == Attendance.Status.LATE
    assert record.late_minutes == 1


def test_a_second_past_the_boundary_is_late(nif, arrive):
    """Matches the strict-`>` behaviour the old rule had."""
    record = arrive(11, 45, ss=30)
    assert record.status == Attendance.Status.LATE
    assert record.late_minutes == 1


def test_mid_morning_arrival_is_late(nif, arrive):
    record = arrive(12, 30)
    assert record.status == Attendance.Status.LATE
    assert record.late_minutes == 45


def test_just_before_the_half_day_boundary_is_still_late(nif, arrive):
    assert arrive(12, 59).status == Attendance.Status.LATE


def test_the_half_day_boundary_is_inclusive(nif, arrive):
    """13:00:00 exactly is Half Day."""
    assert arrive(13, 0).status == Attendance.Status.HALF_DAY


def test_afternoon_arrival_is_half_day(nif, arrive):
    assert arrive(14, 0).status == Attendance.Status.HALF_DAY


def test_a_full_eight_hours_starting_late_is_still_half_day(nif, arrive):
    """Arrival beats duration: eight hours from 14:00 is half a working day
    against a 10:00 office."""
    record = arrive(14, 0, out=(22, 0))
    assert record.status == Attendance.Status.HALF_DAY
    assert record.working_hours == Decimal("8.00")


def test_the_duration_half_day_rule_is_retained(nif, arrive):
    """On time but only two hours worked — still Half Day. Without this, the
    arrival rule alone would call it Present."""
    record = arrive(10, 0, out=(12, 0))
    assert record.status == Attendance.Status.HALF_DAY
    assert record.working_hours == Decimal("2.00")


def test_no_check_in_is_absent(nif, employee, a_weekday):
    record = Attendance(employee=employee, date=a_weekday)
    recompute_status(record)
    assert record.status == Attendance.Status.ABSENT
    assert record.late_minutes == 0


def test_a_day_before_the_cutoff_is_not_yet_absent(nif, employee):
    """The absent cut-off is untouched by 8.1: today stays Not Applicable until
    18:00 rather than flipping to Absent at midnight."""
    from attendance.services import effective_status, now_local

    today = timezone.localdate()
    status = effective_status(employee, today)
    if now_local().time() < time(18, 0):
        assert status is None
    else:
        assert status == Attendance.Status.ABSENT


def test_an_open_day_without_a_check_out_uses_the_arrival_rule(nif, arrive):
    record = arrive(12, 30, out=None)
    assert record.status == Attendance.Status.LATE
    assert record.working_hours == Decimal("0.00")


# --------------------------------------------------------------------------
# the boundaries are configurable
# --------------------------------------------------------------------------
def test_the_boundaries_are_policy_fields(employee, arrive):
    PolicyAssignment.objects.create(
        policy=AttendancePolicy.objects.create(
            name="Strict-ish", office_start_time=time(10, 0),
            late_after_time=time(10, 30), half_day_after_time=time(11, 0)),
        scope=PolicyAssignment.Scope.USER, user=employee, effective_from=FLOOR)
    assert arrive(10, 29).status == Attendance.Status.PRESENT
    assert arrive(10, 45).status == Attendance.Status.LATE
    assert arrive(11, 0).status == Attendance.Status.HALF_DAY


def test_an_empty_late_after_time_falls_back_to_grace(employee, arrive):
    """NULL selects the legacy office_start + grace path, still supported."""
    PolicyAssignment.objects.create(
        policy=AttendancePolicy.objects.create(
            name="Legacy path", office_start_time=time(10, 0), grace_minutes=15,
            late_after_time=None, half_day_after_time=None),
        scope=PolicyAssignment.Scope.USER, user=employee, effective_from=FLOOR)
    assert arrive(10, 15).status == Attendance.Status.PRESENT
    assert arrive(10, 16).status == Attendance.Status.LATE
    # A long afternoon day: Late, not Half Day. The arrival half-day rule is off
    # on this path, and 6 hours clears the duration threshold.
    assert arrive(14, 0, out=(20, 0)).status == Attendance.Status.LATE


def test_an_empty_half_day_after_time_disables_only_the_arrival_rule(employee, arrive):
    PolicyAssignment.objects.create(
        policy=AttendancePolicy.objects.create(
            name="No arrival half-day", office_start_time=time(10, 0),
            late_after_time=time(11, 45), half_day_after_time=None,
            half_day_hours=Decimal("5.00")),
        scope=PolicyAssignment.Scope.USER, user=employee, effective_from=FLOOR)
    assert arrive(14, 0, out=(22, 0)).status == Attendance.Status.LATE
    assert arrive(10, 0, out=(12, 0)).status == Attendance.Status.HALF_DAY


# --------------------------------------------------------------------------
# shifts move the window
# --------------------------------------------------------------------------
def test_the_window_moves_with_an_evening_shift(nif, employee, arrive):
    """Absolute boundaries would mark every evening-shift arrival Half Day.
    A 14:00 shift against a 10:00 office start shifts them to 15:45 / 17:00."""
    EmployeeShift.objects.create(user=employee, shift=Shift.objects.get(code="evening"),
                                 effective_from=FLOOR)
    assert arrive(14, 30, out=(22, 0)).status == Attendance.Status.PRESENT
    assert arrive(15, 45, out=(22, 0)).status == Attendance.Status.PRESENT
    assert arrive(16, 0, out=(22, 0)).status == Attendance.Status.LATE
    assert arrive(17, 0, out=(23, 0)).status == Attendance.Status.HALF_DAY


def test_the_window_moves_with_a_morning_shift(nif, employee, arrive):
    EmployeeShift.objects.create(user=employee, shift=Shift.objects.get(code="morning"),
                                 effective_from=FLOOR)
    assert arrive(7, 45, out=(14, 0)).status == Attendance.Status.PRESENT
    assert arrive(7, 46, out=(14, 0)).status == Attendance.Status.LATE
    assert arrive(9, 0, out=(17, 0)).status == Attendance.Status.HALF_DAY


def test_the_general_shift_leaves_the_window_alone(nif, employee, arrive):
    """Its 10:00 start equals the policy's office start, so the offset is zero
    and the NIF numbers apply verbatim."""
    EmployeeShift.objects.create(user=employee, shift=Shift.objects.get(code="general"),
                                 effective_from=FLOOR)
    assert arrive(11, 45).status == Attendance.Status.PRESENT
    assert arrive(11, 46).status == Attendance.Status.LATE
    assert arrive(13, 0).status == Attendance.Status.HALF_DAY


def test_a_shift_grace_is_ignored_when_late_after_time_is_set(nif, employee, arrive):
    """The window is already encoded in the boundary; stacking a second grace
    period on top would double-count it."""
    shift = Shift.objects.create(code="graceful", name="Graceful", start_time=time(10, 0),
                                 end_time=time(18, 0), grace_minutes=120)
    EmployeeShift.objects.create(user=employee, shift=shift, effective_from=FLOOR)
    assert arrive(11, 46).status == Attendance.Status.LATE


def test_boundaries_are_clamped_inside_the_day(nif):
    """A very late shift must not wrap a boundary past midnight, which would
    make every arrival look early."""
    policy = AttendancePolicy.objects.get(name="NIF rules (test)")
    late_shift = Shift(code="x", name="X", start_time=time(23, 0), end_time=time(23, 59))
    late_at, half_at = resolver.status_boundaries(policy, late_shift)
    assert late_at <= time(23, 59, 59)
    assert half_at <= time(23, 59, 59)


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------
def test_late_after_time_cannot_precede_the_office_start():
    policy = AttendancePolicy(name="Backwards", office_start_time=time(10, 0),
                              late_after_time=time(9, 0), half_day_after_time=time(13, 0))
    with pytest.raises(ValidationError):
        policy.clean()


def test_half_day_after_time_must_follow_late_after_time():
    policy = AttendancePolicy(name="Inverted", office_start_time=time(10, 0),
                              late_after_time=time(13, 0), half_day_after_time=time(11, 45))
    with pytest.raises(ValidationError):
        policy.clean()


def test_half_day_after_time_must_precede_the_absent_cutoff():
    policy = AttendancePolicy(name="Too late", office_start_time=time(10, 0),
                              late_after_time=time(11, 45),
                              half_day_after_time=time(18, 30),
                              absent_cutoff_time=time(18, 0))
    with pytest.raises(ValidationError):
        policy.clean()


def test_the_api_rejects_inverted_boundaries(hr, auth):
    response = auth(hr).post("/api/v1/attendance/policies/", {
        "name": "Inverted via API", "office_start_time": "10:00",
        "late_after_time": "13:00", "half_day_after_time": "11:45"}, format="json")
    assert response.status_code == 400
    assert "half_day_after_time" in response.data


def test_hr_can_edit_the_boundaries(hr, auth):
    response = auth(hr).post("/api/v1/attendance/policies/", {
        "name": "Relaxed", "office_start_time": "10:00",
        "late_after_time": "12:00", "half_day_after_time": "14:00"}, format="json")
    assert response.status_code == 201
    assert response.data["late_after_time"] == "12:00:00"


# --------------------------------------------------------------------------
# every write path, per the brief
# --------------------------------------------------------------------------
def test_browser_check_in_uses_the_arrival_rule(nif, employee, auth, monkeypatch):
    """CheckInView stamps timezone.now(); freeze it at 12:30."""
    from attendance import services, views

    today = timezone.localdate()
    if services.is_holiday(today):
        pytest.skip("today is a holiday; check-in is refused by design")
    noonish = timezone.make_aware(
        timezone.datetime(today.year, today.month, today.day, 12, 30))
    monkeypatch.setattr(views.timezone, "now", lambda: noonish)

    response = auth(employee).post("/api/v1/attendance/check-in/", {}, format="json")
    assert response.status_code == 201
    assert response.data["status"] == Attendance.Status.LATE
    assert Attendance.objects.get(employee=employee, date=today).late_minutes == 45


def test_biometric_derivation_uses_the_arrival_rule(nif, employee, a_weekday, local):
    """A punch at 12:30 must produce the same verdict as a browser check-in."""
    from biometric.derivation import derive_daily_attendance
    from biometric.models import AttendancePunch, BiometricDevice

    device = BiometricDevice.objects.create(name="Arrival test", label="arrival-test")
    for punch, hour in ((0, 12), (1, 18)):
        ts = local(a_weekday.year, a_weekday.month, a_weekday.day, hour, 30)
        AttendancePunch.objects.create(
            device=device, employee_device_id="7", user=employee,
            timestamp=ts, local_date=a_weekday, punch=punch)

    record = derive_daily_attendance(employee, a_weekday)
    assert record.status == Attendance.Status.LATE
    assert record.late_minutes == 45
    assert record.source == Attendance.Source.BIOMETRIC


def test_re_derivation_keeps_the_status_stable(nif, employee, a_weekday, local):
    from biometric.derivation import derive_daily_attendance
    from biometric.models import AttendancePunch, BiometricDevice

    device = BiometricDevice.objects.create(name="Idem", label="idem")
    AttendancePunch.objects.create(
        device=device, employee_device_id="7", user=employee,
        timestamp=local(a_weekday.year, a_weekday.month, a_weekday.day, 14, 0),
        local_date=a_weekday, punch=0)

    statuses = {derive_daily_attendance(employee, a_weekday, force=True).status
                for _ in range(3)}
    assert statuses == {Attendance.Status.HALF_DAY}


def test_dashboard_statistics_follow_the_arrival_rule(nif, employee, hr, auth, local):
    today = timezone.localdate()
    record = Attendance(
        employee=employee, date=today,
        check_in=local(today.year, today.month, today.day, 12, 30))
    recompute_status(record)
    record.save()

    counts = auth(hr).get("/api/v1/attendance/dashboard/").data["counts"]
    assert counts["late"] == 1
    assert counts["present"] == 0


def test_reports_follow_the_arrival_rule(nif, employee, local, a_weekday):
    from attendance.reports import build_employee_report

    for day, hour in ((a_weekday, 12), (a_weekday - timedelta(days=1), 14)):
        if day.weekday() == 5:
            pytest.skip("fixture window straddles a Saturday")
        record = Attendance(
            employee=employee, date=day,
            check_in=local(day.year, day.month, day.day, hour, 30),
            check_out=local(day.year, day.month, day.day, 18, 30))
        recompute_status(record)
        record.save()

    report = build_employee_report(employee, a_weekday - timedelta(days=1), a_weekday)
    assert report["summary"]["late"] == 1
    assert report["summary"]["half_day"] == 1
    assert report["summary"]["present"] == 0


# --------------------------------------------------------------------------
# the cutover — history must not be restated
# --------------------------------------------------------------------------
def test_the_cutover_policy_is_seeded():
    policy = AttendancePolicy.objects.get(name=NIF_POLICY)
    assert policy.late_after_time == time(11, 45)
    assert policy.half_day_after_time == time(13, 0)
    assert PolicyAssignment.objects.filter(
        policy=policy, scope="global", effective_until__isnull=True).exists()


def test_the_legacy_policy_keeps_its_own_rules():
    legacy = AttendancePolicy.objects.get(name="Global Policy")
    assert legacy.late_after_time is None
    assert legacy.half_day_after_time is None


def test_history_resolves_the_legacy_policy_and_today_the_new_one(employee):
    """The whole reason for a dated cutover: re-deriving an old month must not
    turn recorded Late days into Present."""
    nif_assignment = PolicyAssignment.objects.get(
        policy__name=NIF_POLICY, scope="global")
    go_live = nif_assignment.effective_from

    before = resolver.resolve_policy(employee, go_live - timedelta(days=1))
    on_or_after = resolver.resolve_policy(employee, go_live)

    assert before.name == "Global Policy"
    assert before.late_after_time is None
    assert on_or_after.name == NIF_POLICY
    assert on_or_after.late_after_time == time(11, 45)


def test_a_historical_day_keeps_the_strict_verdict(employee, local):
    """10:16 before the cutover is still Late; the same arrival after it is
    Present."""
    go_live = PolicyAssignment.objects.get(
        policy__name=NIF_POLICY, scope="global").effective_from
    yesterday = go_live - timedelta(days=1)

    old = Attendance(employee=employee, date=yesterday,
                     check_in=local(yesterday.year, yesterday.month, yesterday.day, 10, 16),
                     check_out=local(yesterday.year, yesterday.month, yesterday.day, 18, 0))
    recompute_status(old)
    assert old.status == Attendance.Status.LATE

    new = Attendance(employee=employee, date=go_live,
                     check_in=local(go_live.year, go_live.month, go_live.day, 10, 16),
                     check_out=local(go_live.year, go_live.month, go_live.day, 18, 0))
    recompute_status(new)
    assert new.status == Attendance.Status.PRESENT
