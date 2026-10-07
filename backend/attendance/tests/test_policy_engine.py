"""Policy resolution and the status/overtime rules it drives.

Covers the audit report's required cases: late employee, half day, absent,
overtime, policy override, department policy, user policy — plus the
migration-parity guarantee that makes Phase 8 safe to deploy (risk R2).
"""
from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from attendance.models import Attendance, AttendancePolicy, PolicyAssignment, Shift
from attendance.policy import engine, resolver
from attendance.services import recompute_status

pytestmark = pytest.mark.django_db

FLOOR = date(2000, 1, 1)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def make_policy(name="Test Policy", **kwargs):
    """A policy on the LEGACY grace path unless told otherwise.

    ``late_after_time`` defaults to 11:45 on the model — the NIF arrival rule.
    This module exercises the office-start + grace maths, which is selected by
    NULL and remains fully supported, so the default is flipped here. The
    arrival rules have their own module, ``test_arrival_rules.py``.
    """
    kwargs.setdefault("late_after_time", None)
    kwargs.setdefault("half_day_after_time", None)
    return AttendancePolicy.objects.create(name=name, **kwargs)


def assign(policy, *, scope, user=None, department=None, effective_from=FLOOR):
    return PolicyAssignment.objects.create(
        policy=policy, scope=scope, user=user, department=department,
        effective_from=effective_from)


@pytest.fixture(autouse=True)
def legacy_global_rules(db):
    """Run this whole module under the pre-8.1 office-start + grace rules.

    The NIF cutover (attendance/0007) hands the open global assignment to a
    policy with arrival boundaries, effective from its own go-live date. Pinning
    the module here keeps every assertion date-independent — otherwise a test
    saying "10:16 is Late" would pass only while its fixture date happened to
    fall before the cutover, and start failing on its own weeks later.

    The grace path is not dead code: it is what a policy with an empty
    late_after_time uses, and what the settings fallback uses.
    """
    PolicyAssignment.objects.filter(policy__name="NIF Attendance Policy").delete()
    PolicyAssignment.objects.filter(policy__name="Global Policy").update(effective_until=None)


def build(employee, day, local, check_in=(10, 0), check_out=None, save=False, **extra):
    record = Attendance(
        employee=employee, date=day,
        check_in=local(day.year, day.month, day.day, *check_in) if check_in else None,
        check_out=local(day.year, day.month, day.day, *check_out) if check_out else None,
        **extra,
    )
    recompute_status(record)
    if save:
        record.save()
    return record


# --------------------------------------------------------------------------
# migration parity — the guarantee that Phase 8 changes nothing on day one
# --------------------------------------------------------------------------
def test_seeded_global_policy_matches_the_settings(settings):
    """The seed migration and the settings fallback must describe the same
    behaviour, or migrating would silently move everyone's status."""
    seeded = AttendancePolicy.objects.get(name="Global Policy")
    fallback = resolver.settings_fallback_policy()

    assert seeded.office_start_time == fallback.office_start_time
    assert seeded.absent_cutoff_time == fallback.absent_cutoff_time
    assert Decimal(seeded.half_day_hours) == Decimal(fallback.half_day_hours)
    assert Decimal(seeded.full_day_hours) == Decimal(fallback.full_day_hours)
    assert seeded.grace_minutes == 0
    assert seeded.overtime_threshold_hours is None, "overtime must ship disabled"
    assert seeded.comp_off_enabled is False, "comp-off must ship disabled"
    assert seeded.deduct_breaks is False
    # 8.1 pinned the legacy policy explicitly rather than letting it inherit the
    # new 11:45/13:00 field defaults, so re-deriving history cannot restate it.
    assert seeded.late_after_time is None
    assert seeded.half_day_after_time is None
    assert fallback.late_after_time is None
    assert fallback.half_day_after_time is None


def test_status_is_identical_with_and_without_policy_rows(employee, a_weekday, local):
    """R2: the same punches must produce the same status whether resolution
    lands on the seeded Global policy or falls through to settings."""
    with_rows = build(employee, a_weekday, local, (10, 30), (18, 0))
    seeded = (with_rows.status, with_rows.working_hours)

    PolicyAssignment.objects.all().delete()
    without_rows = build(employee, a_weekday, local, (10, 30), (18, 0))

    assert resolver.resolve_policy(employee, a_weekday).is_fallback is True
    assert (without_rows.status, without_rows.working_hours) == seeded


# --------------------------------------------------------------------------
# resolution order:  user -> department -> global -> settings
# --------------------------------------------------------------------------
def test_global_policy_applies_when_nothing_more_specific_exists(employee, a_weekday):
    assert resolver.resolve_policy(employee, a_weekday).name == "Global Policy"


def test_department_policy_beats_global(employee, dept, a_weekday):
    assign(make_policy("Dept 09:00", office_start_time=time(9, 0)),
           scope=PolicyAssignment.Scope.DEPARTMENT, department=dept)
    assert resolver.resolve_policy(employee, a_weekday).name == "Dept 09:00"


def test_user_policy_beats_department_and_global(employee, dept, a_weekday):
    assign(make_policy("Dept 09:00", office_start_time=time(9, 0)),
           scope=PolicyAssignment.Scope.DEPARTMENT, department=dept)
    assign(make_policy("User 11:00", office_start_time=time(11, 0)),
           scope=PolicyAssignment.Scope.USER, user=employee)
    assert resolver.resolve_policy(employee, a_weekday).name == "User 11:00"


def test_a_department_policy_does_not_leak_to_other_departments(employee, outsider, dept, a_weekday):
    assign(make_policy("Dept 09:00", office_start_time=time(9, 0)),
           scope=PolicyAssignment.Scope.DEPARTMENT, department=dept)
    assert resolver.resolve_policy(employee, a_weekday).name == "Dept 09:00"
    assert resolver.resolve_policy(outsider, a_weekday).name == "Global Policy"


def test_resolution_is_date_effective(employee, a_weekday):
    """Re-deriving an old month must apply the policy in force THEN."""
    later = make_policy("Later 09:00", office_start_time=time(9, 0))
    assign(later, scope=PolicyAssignment.Scope.USER, user=employee,
           effective_from=a_weekday + timedelta(days=1))

    assert resolver.resolve_policy(employee, a_weekday).name == "Global Policy"
    assert resolver.resolve_policy(employee, a_weekday + timedelta(days=2)).name == "Later 09:00"


def test_an_expired_assignment_stops_applying(employee, a_weekday):
    policy = make_policy("Retired", office_start_time=time(9, 0))
    a = assign(policy, scope=PolicyAssignment.Scope.USER, user=employee)
    a.effective_until = a_weekday - timedelta(days=1)
    a.save()
    assert resolver.resolve_policy(employee, a_weekday).name == "Global Policy"


def test_an_inactive_policy_is_skipped(employee, a_weekday):
    assign(make_policy("Off", office_start_time=time(9, 0), is_active=False),
           scope=PolicyAssignment.Scope.USER, user=employee)
    assert resolver.resolve_policy(employee, a_weekday).name == "Global Policy"


def test_resolution_never_returns_none(employee, a_weekday):
    PolicyAssignment.objects.all().delete()
    AttendancePolicy.objects.all().delete()
    policy = resolver.resolve_policy(employee, a_weekday)
    assert policy is not None
    assert policy.is_fallback is True
    assert policy.pk is None, "the fallback must never be persisted"


# --------------------------------------------------------------------------
# lateness + grace
# --------------------------------------------------------------------------
def test_late_employee(employee, a_weekday, local):
    record = build(employee, a_weekday, local, (10, 16), (18, 0))
    assert record.status == Attendance.Status.LATE
    assert record.late_minutes == 16


def test_grace_period_keeps_a_slightly_late_arrival_present(employee, a_weekday, local):
    assign(make_policy("Grace 15", grace_minutes=15),
           scope=PolicyAssignment.Scope.USER, user=employee)
    record = build(employee, a_weekday, local, (10, 12), (18, 0))
    assert record.status == Attendance.Status.PRESENT
    assert record.late_minutes == 0


def test_grace_period_boundary_is_inclusive(employee, a_weekday, local):
    assign(make_policy("Grace 15", grace_minutes=15),
           scope=PolicyAssignment.Scope.USER, user=employee)
    assert build(employee, a_weekday, local, (10, 15), (18, 0)).status == Attendance.Status.PRESENT
    late = build(employee, a_weekday, local, (10, 16), (18, 0))
    assert late.status == Attendance.Status.LATE
    assert late.late_minutes == 1


def test_on_time_arrival_is_present(employee, a_weekday, local):
    record = build(employee, a_weekday, local, (9, 45), (18, 0))
    assert record.status == Attendance.Status.PRESENT
    assert record.late_minutes == 0


# --------------------------------------------------------------------------
# hours
# --------------------------------------------------------------------------
def test_half_day(employee, a_weekday, local):
    record = build(employee, a_weekday, local, (10, 0), (14, 30))
    assert record.status == Attendance.Status.HALF_DAY
    assert record.working_hours == Decimal("4.50")


def test_half_day_threshold_is_policy_driven(employee, a_weekday, local):
    assign(make_policy("Short day", half_day_hours=Decimal("3.00")),
           scope=PolicyAssignment.Scope.USER, user=employee)
    record = build(employee, a_weekday, local, (10, 0), (14, 30))
    assert record.status == Attendance.Status.PRESENT, "4.5h now clears the 3h bar"


def test_absent_when_there_is_no_check_in(employee, a_weekday):
    record = Attendance(employee=employee, date=a_weekday)
    recompute_status(record)
    assert record.status == Attendance.Status.ABSENT
    assert record.working_hours == Decimal("0.00")
    assert record.overtime_hours == Decimal("0.00")
    assert record.late_minutes == 0


def test_a_check_in_without_a_check_out_is_never_half_day(employee, a_weekday, local):
    """Pre-Phase-8 behaviour: an open day is Present/Late with 0.00 hours."""
    record = build(employee, a_weekday, local, (9, 0), None)
    assert record.status == Attendance.Status.PRESENT
    assert record.working_hours == Decimal("0.00")


# --------------------------------------------------------------------------
# overtime
# --------------------------------------------------------------------------
def test_overtime_is_disabled_by_default(employee, a_weekday, local):
    record = build(employee, a_weekday, local, (10, 0), (20, 30))
    assert record.overtime_hours == Decimal("0.00")
    assert record.regular_hours == Decimal("10.50")
    assert record.working_hours == Decimal("10.50")


def test_overtime_splits_regular_and_extra_hours(employee, a_weekday, local):
    assign(make_policy("OT 8h", overtime_threshold_hours=Decimal("8.00")),
           scope=PolicyAssignment.Scope.USER, user=employee)
    record = build(employee, a_weekday, local, (10, 0), (20, 30))
    assert record.working_hours == Decimal("10.50"), "gross span keeps its meaning"
    assert record.regular_hours == Decimal("8.00")
    assert record.overtime_hours == Decimal("2.50")


def test_trivial_overtime_is_ignored(employee, a_weekday, local):
    assign(make_policy("OT 8h", overtime_threshold_hours=Decimal("8.00"),
                       overtime_min_minutes=30),
           scope=PolicyAssignment.Scope.USER, user=employee)
    record = build(employee, a_weekday, local, (10, 0), (18, 12))
    assert record.overtime_hours == Decimal("0.00"), "12 minutes is below the 30-minute floor"
    assert record.regular_hours == Decimal("8.20")


def test_overtime_records_the_policy_that_produced_it(employee, a_weekday, local):
    policy = make_policy("OT 8h", overtime_threshold_hours=Decimal("8.00"))
    assign(policy, scope=PolicyAssignment.Scope.USER, user=employee)
    record = build(employee, a_weekday, local, (10, 0), (20, 0), save=True)
    assert record.applied_policy_id == policy.id


# --------------------------------------------------------------------------
# shifts
# --------------------------------------------------------------------------
def test_seeded_shifts_exist():
    codes = set(Shift.objects.values_list("code", flat=True))
    assert {"general", "morning", "evening"} <= codes


def test_shift_assignment_changes_who_is_late(employee, coworker, a_weekday, local):
    """The required 'shift assignment' case: same wall-clock arrival, different
    verdicts, because one employee is on the evening shift."""
    from attendance.models import EmployeeShift

    evening = Shift.objects.get(code="evening")  # 14:00
    EmployeeShift.objects.create(user=employee, shift=evening, effective_from=FLOOR)

    on_evening = build(employee, a_weekday, local, (14, 20), (22, 0))
    on_general = build(coworker, a_weekday, local, (14, 20), (22, 0))

    assert on_evening.status == Attendance.Status.LATE
    assert on_evening.late_minutes == 20
    assert on_general.status == Attendance.Status.LATE
    assert on_general.late_minutes == 260, "14:20 against a 10:00 general shift"


def test_a_shift_grace_overrides_the_policy_grace(employee, a_weekday, local):
    from attendance.models import EmployeeShift

    assign(make_policy("Policy grace 0", grace_minutes=0),
           scope=PolicyAssignment.Scope.USER, user=employee)
    shift = Shift.objects.create(code="flexi", name="Flexi", start_time=time(10, 0),
                                 end_time=time(18, 0), grace_minutes=30)
    EmployeeShift.objects.create(user=employee, shift=shift, effective_from=FLOOR)

    assert build(employee, a_weekday, local, (10, 25), (18, 0)).status == Attendance.Status.PRESENT


def test_overnight_shifts_are_rejected():
    with pytest.raises(ValidationError):
        Shift.objects.create(code="night", name="Night Shift", start_time=time(22, 0),
                             end_time=time(6, 0), crosses_midnight=True)
    assert not Shift.objects.filter(code="night").exists()


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------
def test_half_day_hours_must_be_below_full_day_hours():
    policy = AttendancePolicy(name="Bad", half_day_hours=Decimal("9.00"),
                              full_day_hours=Decimal("8.00"))
    with pytest.raises(ValidationError):
        policy.clean()


def test_a_user_scoped_assignment_requires_a_user(employee):
    a = PolicyAssignment(policy=make_policy("P"), scope=PolicyAssignment.Scope.USER,
                         effective_from=FLOOR)
    with pytest.raises(ValidationError):
        a.clean()


def test_only_one_open_ended_assignment_per_user(employee):
    from django.db.utils import IntegrityError

    assign(make_policy("A"), scope=PolicyAssignment.Scope.USER, user=employee)
    with pytest.raises(IntegrityError):
        assign(make_policy("B"), scope=PolicyAssignment.Scope.USER, user=employee)


# --------------------------------------------------------------------------
# caching (risk R8)
# --------------------------------------------------------------------------
def test_policy_cache_collapses_repeated_resolution(employee, a_weekday,
                                                    django_assert_num_queries):
    """One query per employee, cached for the whole block — not one per
    employee-day, which is what a month-long re-derivation actually costs."""
    with django_assert_num_queries(5):
        for _ in range(5):
            resolver.resolve_policy(employee, a_weekday)

    with resolver.policy_cache():
        with django_assert_num_queries(1):
            for _ in range(5):
                resolver.resolve_policy(employee, a_weekday)


def test_the_cache_spans_different_days_for_one_employee(employee, a_weekday,
                                                         django_assert_num_queries):
    """The workload that matters: each (user, day) pair is visited exactly once
    during a sweep, so a day-keyed cache would never hit. Keyed per user, a
    30-day range costs a single query."""
    with resolver.policy_cache():
        with django_assert_num_queries(1):
            for offset in range(30):
                resolver.resolve_policy(employee, a_weekday - timedelta(days=offset))


def test_the_cache_does_not_outlive_its_block(employee, a_weekday):
    with resolver.policy_cache():
        assert resolver.resolve_policy(employee, a_weekday).name == "Global Policy"
    assign(make_policy("Fresh", office_start_time=time(9, 0)),
           scope=PolicyAssignment.Scope.USER, user=employee)
    assert resolver.resolve_policy(employee, a_weekday).name == "Fresh"


def test_late_minutes_helper_matches_the_old_is_late_rule():
    """With grace 0 the engine must agree with services.is_late exactly:
    late iff strictly after the start time."""
    from datetime import datetime

    start = time(10, 0)
    exact = datetime(2026, 1, 1, 10, 0)
    assert engine.late_minutes(exact, start, 0) == 0
    assert engine.late_minutes(datetime(2026, 1, 1, 10, 0, 30), start, 0) == 1
    assert engine.late_minutes(datetime(2026, 1, 1, 9, 59), start, 0) == 0
