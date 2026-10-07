"""What a day's times mean under the policy that applies to that employee.

This module is the single seam Phase 8 needed. ``attendance.services.recompute_status``
delegates here, and every write path — browser check-in, browser check-out,
biometric derivation, biometric revert — already routes through that one
function, so all four became policy-aware without touching a single call site.

Two invariants preserved from Phase 5 onward:

* ``working_hours`` keeps its existing meaning — the GROSS check-in..check-out
  span. Every existing report, PDF and export reads it and must not shift.
  ``regular_hours`` and ``overtime_hours`` are new columns beside it.
* ``resolve_day_status`` is not modified. A row with a real ``check_in`` still
  wins over Holiday and Leave through that resolver's first branch.
"""
import math
from datetime import date as _date, datetime
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from ..models import Attendance
from . import resolver
from .models import WFHRequest

ZERO = Decimal("0.00")
_HOURS = Decimal("0.01")


def _q(value):
    return Decimal(value).quantize(_HOURS, rounding=ROUND_HALF_UP)


def late_minutes(check_in_local, start_time, grace_minutes):
    """Minutes past ``start_time + grace_minutes``. 0 when on time.

    With ``grace_minutes=0`` this reproduces the old ``is_late()`` exactly:
    late iff the check-in is strictly after the reference time. Since 8.1 the
    engine passes the resolved ``late_after_time`` as ``start_time`` with a
    grace of 0, because the grace is already baked into that boundary.
    """
    if check_in_local is None:
        return 0
    anchor = _date.min
    delta = (datetime.combine(anchor, check_in_local.time())
             - datetime.combine(anchor, start_time)).total_seconds() / 60
    return max(0, math.ceil(delta - grace_minutes))


def _break_minutes(record):
    """Paired break_out(2) -> break_in(3) time, in minutes.

    Only called when the policy opts in. The live device has recorded zero
    break punches, so this path is unexercised in production today.
    """
    from biometric.models import AttendancePunch

    punches = list(AttendancePunch.objects
                   .filter(user=record.employee, local_date=record.date)
                   .order_by("timestamp")
                   .values_list("punch", "timestamp"))
    total, open_at = 0.0, None
    for code, ts in punches:
        if code == 2 and open_at is None:      # break_out — break starts
            open_at = ts
        elif code == 3 and open_at is not None:  # break_in — break ends
            total += (ts - open_at).total_seconds() / 60
            open_at = None
    return total


def _is_public_holiday(day):
    """Active public holiday. Deliberately NOT Saturday — the two are configured
    independently on the policy, so they must be distinguishable here."""
    from leaves.models import Holiday

    return Holiday.objects.filter(is_active=True, date=day).exists()


def approved_wfh(user, day):
    """True when an approved WFH request covers the day.

    Approval on its own is not attendance — see ``evaluate``.
    """
    return WFHRequest.objects.filter(
        user=user, status=WFHRequest.Status.APPROVED,
        start_date__lte=day, end_date__gte=day,
    ).exists()


def evaluate_comp_off(record, policy, effective_from, net_hours):
    """Comp days earned by working this day: 0, 0.5 or 1.0.

    Pure — writes nothing. ``comp_off.sync_entry`` turns the result into a
    ledger row.
    """
    if not policy.comp_off_enabled:
        return ZERO
    # Never earn retroactively for days before the assignment existed: enabling
    # comp-off must not mint leave out of months of historical Saturdays.
    if effective_from is None or record.date < effective_from:
        return ZERO

    is_saturday = record.date.weekday() == 5
    if is_saturday:
        if not policy.comp_off_on_saturday:
            return ZERO
    elif _is_public_holiday(record.date):
        if not policy.comp_off_on_holiday:
            return ZERO
    else:
        return ZERO  # an ordinary working day earns nothing

    if net_hours < Decimal(policy.comp_off_min_hours):
        return ZERO
    if net_hours >= Decimal(policy.comp_off_full_day_hours):
        return Decimal("1.00")
    if net_hours >= Decimal(policy.comp_off_half_day_hours):
        return Decimal("0.50")
    return ZERO


def evaluate(record, *, policy=None, shift=None):
    """Compute every policy-derived field on ``record`` in place.

    Returns the record. Does not save — callers already do, and keeping this
    side-effect-free at the DB level is what makes it safe to call from the
    derivation engine inside a transaction.
    """
    from attendance import services

    user, day = record.employee, record.date
    binding = resolver.resolve_binding(user, day)
    policy = policy or binding.policy
    shift = shift if shift is not None else resolver.resolve_shift(user, day, policy)

    # The settings fallback is unsaved and has no pk — never write it as an FK.
    record.applied_policy = None if policy.is_fallback else policy
    record.applied_shift = shift
    record.is_wfh = approved_wfh(user, day)

    ci, co = record.check_in, record.check_out

    if not ci:
        record.working_hours = ZERO
        record.regular_hours = ZERO
        record.overtime_hours = ZERO
        record.late_minutes = 0
        record.comp_off_days = ZERO
        record.comp_off_eligible = False
        record.status = Attendance.Status.ABSENT
        return record

    arrival = timezone.localtime(ci)
    late_at, half_at = resolver.status_boundaries(policy, shift)
    # Minutes past the boundary that actually makes someone late, so
    # `late_minutes > 0` always agrees with the status stored beside it.
    record.late_minutes = late_minutes(arrival, late_at, 0)

    # GROSS span — unchanged meaning, unchanged column.
    gross = services.compute_working_hours(ci, co) if co else ZERO
    record.working_hours = gross

    net = gross
    if co and policy.deduct_breaks:
        net = max(ZERO, _q(gross - Decimal(_break_minutes(record)) / Decimal(60)))

    threshold = resolver.overtime_threshold(policy)
    if threshold is None or not co:
        record.regular_hours = net
        record.overtime_hours = ZERO
    else:
        raw_overtime = max(ZERO, _q(net - threshold))
        keep = raw_overtime * 60 >= Decimal(policy.overtime_min_minutes)
        record.overtime_hours = raw_overtime if keep else ZERO
        record.regular_hours = _q(net - record.overtime_hours)

    # Status ladder. Two independent routes to Half Day:
    #   * arrival  — turning up at or after half_day_after_time, however long
    #                you then stay (a full eight hours from 14:00 on a 10:00
    #                office is still half a day);
    #   * duration — clocking fewer than half_day_hours, however early you
    #                arrived. Retained from before 8.1: without it a 10:00
    #                arrival who leaves at 12:00 would read as Present.
    # Otherwise a single check-in with no check-out stays Present/Late with
    # 0.00 hours — never Half Day — exactly as before Phase 8.
    if half_at is not None and arrival.time() >= half_at:
        record.status = Attendance.Status.HALF_DAY
    elif co and net < Decimal(policy.half_day_hours):
        record.status = Attendance.Status.HALF_DAY
    elif record.late_minutes > 0:
        record.status = Attendance.Status.LATE
    else:
        record.status = Attendance.Status.PRESENT

    # Approved WFH + a browser check-in = WORK_FROM_HOME. A biometric punch means
    # the employee was physically at a device, so it can never be WFH. Half Day
    # is left standing: a short WFH day is still a short day.
    if (record.is_wfh
            and record.source == Attendance.Source.BROWSER
            and record.status in (Attendance.Status.PRESENT, Attendance.Status.LATE)):
        record.status = Attendance.Status.WORK_FROM_HOME

    record.comp_off_days = evaluate_comp_off(record, policy, binding.effective_from, net)
    record.comp_off_eligible = record.comp_off_days > ZERO
    return record
