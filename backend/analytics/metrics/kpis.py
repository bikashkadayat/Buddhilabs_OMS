"""Composition: the executive, HR and management dashboard payloads.

This module contains no new arithmetic -- it assembles what the domain modules
already compute. Any formula that appears here and nowhere else is a bug: it
means a number on a dashboard has no export and no test.

The exceptions are the three things only a KPI dashboard asks for: approval
turnaround, queue ageing, and workforce utilization/capacity.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Min, Q
from django.utils import timezone

from attendance.models import AttendancePolicy, WFHRequest
from attendance.workforce.models import AttendanceCorrectionRequest
from leaves.models import CompensatoryLedger, Leave, LeaveDayRecord

from .. import calendar as work_calendar, periods
from ..scope import UNASSIGNED
from . import attendance as attendance_metrics, comp_off, departments, devices, leave, wfh
from .base import leave_weight_sum, num, pct

# Approval turnaround is computed in Python from two timestamp columns rather
# than with PERCENTILE_CONT, so the same code path runs on PostgreSQL and on the
# SQLite used by dev and CI. Safe because the row count is bounded: these are
# decisions taken inside the analysis window by an internal HR team, which is
# hundreds of rows, not millions. The cap makes that bound explicit rather than
# assumed, and a truncated sample is reported in the payload.
APPROVAL_SAMPLE_CAP = 5000

# Ageing buckets for open queues, in days.
AGE_BUCKETS = ((1, "under_1d"), (3, "1_to_3d"), (7, "3_to_7d"), (None, "over_7d"))


# ---------------------------------------------------------------------------
# approval turnaround
# ---------------------------------------------------------------------------
def _percentiles(hours):
    """Median and p90 from a list of hour values. Median, never mean.

    One forgotten three-month-old request drags a mean past usefulness -- which
    is exactly what the Phase 8 ``dashboard_analytics`` turnaround figure does
    today. The median is what an HR lead can act on.
    """
    if not hours:
        return {"median_hours": None, "p90_hours": None, "sample": 0}
    ordered = sorted(hours)
    return {
        "median_hours": round(_quantile(ordered, 0.5), 1),
        "p90_hours": round(_quantile(ordered, 0.9), 1),
        "sample": len(ordered),
    }


def _quantile(ordered, fraction):
    """Linear-interpolated quantile, matching PERCENTILE_CONT."""
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _elapsed_hours(pairs):
    """Hours between two timestamps, skipping rows where either is missing."""
    out = []
    for start, end in pairs:
        if start and end and end >= start:
            out.append((end - start).total_seconds() / 3600)
    return out


def approval_times(scope, window):
    """Median and p90 turnaround per workflow stage. FOUR queries."""
    start, end = window.start, window.end

    leave_rows = list(Leave.objects.filter(
        user_id__in=scope.employee_ids, is_deleted=False,
        created_at__date__gte=start, created_at__date__lte=end,
    ).values_list("created_at", "department_head_action_date",
                  "hr_action_date")[:APPROVAL_SAMPLE_CAP])

    correction_rows = list(AttendanceCorrectionRequest.objects.filter(
        employee_id__in=scope.employee_ids,
        created_at__date__gte=start, created_at__date__lte=end,
    ).values_list("created_at", "manager_action_at",
                  "hr_action_at")[:APPROVAL_SAMPLE_CAP])

    wfh_rows = list(WFHRequest.objects.filter(
        user_id__in=scope.employee_ids,
        status__in=[WFHRequest.Status.APPROVED, WFHRequest.Status.REJECTED],
        created_at__date__gte=start, created_at__date__lte=end,
    ).values_list("created_at", "updated_at")[:APPROVAL_SAMPLE_CAP])

    # Min, not Avg: SQLite cannot average a datetime column (it stores them as
    # text), and "the oldest thing still waiting" is the number an HR lead can
    # actually act on anyway.
    pending_comp = CompensatoryLedger.objects.filter(
        user_id__in=scope.employee_ids,
        entry_type=CompensatoryLedger.EntryType.EARN,
        source=CompensatoryLedger.Source.ATTENDANCE,
        status=CompensatoryLedger.Status.PENDING,
    ).aggregate(n=Count("id"), oldest=Min("created_at"))

    return {
        "leave_department_head": _percentiles(
            _elapsed_hours((row[0], row[1]) for row in leave_rows)),
        "leave_hr": _percentiles(
            _elapsed_hours((row[1] or row[0], row[2]) for row in leave_rows)),
        "correction_department_head": _percentiles(
            _elapsed_hours((row[0], row[1]) for row in correction_rows)),
        "correction_hr": _percentiles(
            _elapsed_hours((row[1] or row[0], row[2]) for row in correction_rows)),
        # WFH carries no decision timestamp of its own, so this reads
        # updated_at: an upper bound, flagged rather than dressed up.
        "wfh": {**_percentiles(_elapsed_hours(wfh_rows)), "approximate": True},
        # CompensatoryLedger records no confirmation timestamp at all, so a
        # turnaround simply cannot be measured. Reporting the backlog is honest;
        # inventing a duration would not be.
        "comp_off": {
            "median_hours": None, "p90_hours": None,
            "pending_count": pending_comp["n"] or 0,
            "oldest_pending_hours": (
                round((timezone.now() - pending_comp["oldest"]).total_seconds() / 3600, 1)
                if pending_comp["oldest"] else None),
            "unmeasurable_reason": "no_confirmation_timestamp",
        },
        "sample_cap": APPROVAL_SAMPLE_CAP,
    }


# ---------------------------------------------------------------------------
# queues and ageing
# ---------------------------------------------------------------------------
def _age_buckets(timestamps, now):
    counts = {name: 0 for _limit, name in AGE_BUCKETS}
    for stamp in timestamps:
        age_days = (now - stamp).total_seconds() / 86400
        for limit, name in AGE_BUCKETS:
            if limit is None or age_days < limit:
                counts[name] += 1
                break
    return counts


def queues(scope, include_org=True):
    """Open queue sizes and their age profile. FOUR queries.

    Ages matter more than counts: five corrections raised this morning is a
    normal Tuesday, five raised three weeks ago is a broken process.
    """
    now = timezone.now()

    corrections = list(AttendanceCorrectionRequest.objects.filter(
        employee_id__in=scope.employee_ids,
        status__in=AttendanceCorrectionRequest.OPEN_STATUSES,
    ).values_list("status", "created_at"))
    manager_stage = [stamp for status, stamp in corrections
                     if status == AttendanceCorrectionRequest.Status.PENDING]
    hr_stage = [stamp for status, stamp in corrections
                if status == AttendanceCorrectionRequest.Status.MANAGER_APPROVED]

    wfh_pending = list(WFHRequest.objects.filter(
        user_id__in=scope.employee_ids, status=WFHRequest.Status.PENDING,
    ).values_list("created_at", flat=True))

    leave_pending = list(Leave.objects.filter(
        user_id__in=scope.employee_ids, is_deleted=False,
        status__in=[Leave.Status.PENDING, Leave.Status.PENDING_HR],
    ).values_list("status", "created_at"))

    payload = {
        "corrections_department_head": {
            "count": len(manager_stage), "ages": _age_buckets(manager_stage, now)},
        "corrections_hr": {
            "count": len(hr_stage), "ages": _age_buckets(hr_stage, now)},
        "wfh_pending": {
            "count": len(wfh_pending), "ages": _age_buckets(wfh_pending, now)},
        "leave_department_head": {
            "count": sum(1 for status, _ in leave_pending
                         if status == Leave.Status.PENDING),
            "ages": _age_buckets([stamp for status, stamp in leave_pending
                                  if status == Leave.Status.PENDING], now)},
        "leave_hr": {
            "count": sum(1 for status, _ in leave_pending
                         if status == Leave.Status.PENDING_HR),
            "ages": _age_buckets([stamp for status, stamp in leave_pending
                                  if status == Leave.Status.PENDING_HR], now)},
    }
    if include_org:
        comp_pending = list(CompensatoryLedger.objects.filter(
            user_id__in=scope.employee_ids,
            entry_type=CompensatoryLedger.EntryType.EARN,
            source=CompensatoryLedger.Source.ATTENDANCE,
            status=CompensatoryLedger.Status.PENDING,
        ).values_list("created_at", flat=True))
        payload["comp_off_pending"] = {
            "count": len(comp_pending), "ages": _age_buckets(comp_pending, now)}
    payload["total_open"] = sum(entry["count"] for entry in payload.values()
                                if isinstance(entry, dict) and "count" in entry)
    return payload


# ---------------------------------------------------------------------------
# corrections volume
# ---------------------------------------------------------------------------
def corrections(scope, window):
    """Raised / resolved volume per bucket, plus window totals. TWO queries.

    Raised against resolved is the shape to watch: two lines diverging is a
    backlog forming, which no snapshot count reveals.
    """
    from .base import bucket_expr

    base = AttendanceCorrectionRequest.objects.filter(employee_id__in=scope.employee_ids)
    totals = base.filter(
        created_at__date__gte=window.start, created_at__date__lte=window.end,
    ).aggregate(
        raised=Count("id"),
        approved=Count("id", filter=Q(status=AttendanceCorrectionRequest.Status.HR_APPROVED)),
        rejected=Count("id", filter=Q(status=AttendanceCorrectionRequest.Status.REJECTED)),
        cancelled=Count("id", filter=Q(status=AttendanceCorrectionRequest.Status.CANCELLED)),
        reverted=Count("id", filter=Q(reverted_at__isnull=False)))

    raised_by_bucket = defaultdict(int)
    resolved_by_bucket = defaultdict(int)
    for row in (base.filter(created_at__date__gte=window.start,
                            created_at__date__lte=window.end)
                .annotate(bucket=bucket_expr(window.granularity, "created_at__date"))
                .values("bucket").annotate(n=Count("id"))):
        raised_by_bucket[_key(row["bucket"], window)] += row["n"]
    for row in (base.filter(hr_action_at__date__gte=window.start,
                            hr_action_at__date__lte=window.end)
                .annotate(bucket=bucket_expr(window.granularity, "hr_action_at__date"))
                .values("bucket").annotate(n=Count("id"))):
        resolved_by_bucket[_key(row["bucket"], window)] += row["n"]

    return {
        "totals": {key: (value or 0) for key, value in totals.items()},
        "approval_rate_pct": pct(totals["approved"] or 0,
                                 (totals["approved"] or 0) + (totals["rejected"] or 0)),
        "trend": [{
            "period": key,
            "label": periods.bucket_label(key, window.granularity),
            "raised": raised_by_bucket.get(key, 0),
            "resolved": resolved_by_bucket.get(key, 0),
        } for key in periods.buckets(window)],
    }


def _key(bucket, window):
    if bucket is None:
        return None
    return periods.bucket_key(
        bucket.date() if hasattr(bucket, "date") else bucket, window.granularity)


# ---------------------------------------------------------------------------
# utilization and capacity
# ---------------------------------------------------------------------------
def standard_day_hours():
    """The organisation's full working day, from policy -- never a hardcoded 8.

    Averaged across active policies so an organisation running a 7.5-hour and an
    8-hour policy gets something between them rather than whichever happens to
    be first. Falls back to the ``ATTENDANCE_FULL_DAY_HOURS`` setting, which is
    what the engine itself falls back to when no policy row exists.
    """
    from attendance.services import full_day_hours

    average = AttendancePolicy.objects.filter(is_active=True).aggregate(
        hours=Avg("full_day_hours"))["hours"]
    if average:
        return float(average)
    return float(full_day_hours() or Decimal("8"))


def utilization(scope, window, calendar, worked_regular_hours):
    """Regular hours worked against the hours the calendar expected."""
    expected_days = work_calendar.expected_total(scope, calendar)
    capacity_hours = expected_days * standard_day_hours()
    return {
        "regular_hours": num(worked_regular_hours),
        "capacity_hours": round(capacity_hours, 2),
        "expected_days": expected_days,
        "standard_day_hours": round(standard_day_hours(), 2),
        "utilization_pct": pct(worked_regular_hours, capacity_hours),
    }


def capacity_outlook(scope, days=30, today=None):
    """Available working days per department over the next N days. TWO queries.

    Approved future leave is subtracted from the calendar; nothing else is
    predicted. A capacity number that leans on a forecast is a forecast.
    """
    today = today or timezone.localdate()
    end = today + timedelta(days=days)
    window = periods.Window(start=today, end=end, granularity="day")

    # Future days are deliberately in scope here, so the calendar is built with
    # a horizon at the window end rather than at today.
    from leaves.models import Holiday

    holidays = set(Holiday.objects.filter(
        is_active=True, date__gte=today, date__lte=end).values_list("date", flat=True))
    calendar = work_calendar.WorkCalendar(window, holidays, end)

    booked = defaultdict(float)
    for row in (LeaveDayRecord.objects
                .filter(user_id__in=scope.employee_ids,
                        status=LeaveDayRecord.Status.APPROVED,
                        leave_request__is_deleted=False,
                        is_weekend=False, is_holiday=False,
                        date__gte=today, date__lte=end)
                .values("user__department_ref_id")
                .annotate(days=leave_weight_sum())):
        key = (str(row["user__department_ref_id"])
               if row["user__department_ref_id"] else UNASSIGNED)
        booked[key] += float(row["days"] or 0)

    expected = work_calendar.expected_by_department(scope, calendar)
    rows = []
    for key in scope.departments:
        total = expected.get(key, 0)
        on_leave = booked.get(key, 0.0)
        rows.append({
            "department_id": None if key == UNASSIGNED else key,
            "department": scope.label(key),
            "headcount": scope.department_headcount(key),
            "capacity_days": total,
            "leave_days": round(on_leave, 2),
            "available_days": round(max(0.0, total - on_leave), 2),
            "availability_pct": pct(max(0.0, total - on_leave), total),
        })
    rows.sort(key=lambda row: row["department"])
    return {"from": today.isoformat(), "to": end.isoformat(),
            "horizon_days": days, "departments": rows}


def overtime_overview(scope, window, summary):
    """Overtime totals plus how widely it is spread. ONE query.

    Total hours alone hides the difference between an organisation where
    everyone works twenty minutes over and one where four people are carrying
    it. ``employees_pct`` is that difference.
    """
    from .base import attendance_rows

    with_overtime = (attendance_rows(scope, window).filter(overtime_hours__gt=0)
                     .values("employee_id").distinct().count())
    return {
        "total_hours": summary["overtime_hours"],
        "per_capita": summary["overtime_per_capita"],
        "days_with_overtime": summary["overtime_days"],
        "employees_with_overtime": with_overtime,
        "employees_pct": pct(with_overtime, scope.headcount),
    }


# ===========================================================================
# dashboard payloads
# ===========================================================================
def executive(scope, window, calendar, today=None):
    """/analytics/executive -- headline health of the whole organisation."""
    summary = attendance_metrics.summary(scope, window, calendar)
    department_rows = departments.rank(departments.rows(scope, window, calendar))
    comp = comp_off.balances(scope)
    comp_window = comp_off.earned_in_window(scope, window)

    return {
        "kpis": {
            "headcount": scope.headcount,
            "present_pct": summary["present_pct"],
            "late_pct": summary["late_pct"],
            "absent_pct": summary["absent_pct"],
            "wfh_pct": summary["wfh_pct"],
            "leave_pct": summary["leave_pct"],
            "compliance_pct": summary["compliance_pct"],
            "overtime_hours": summary["overtime_hours"],
            "avg_working_hours": summary["avg_working_hours"],
            "comp_off_earned": comp_window["confirmed"],
            "comp_off_used": comp["used"],
            "comp_off_pending": comp["pending"],
            "department_health_score": departments.org_average(
                department_rows, "health_score"),
        },
        "attendance": summary,
        "trend": attendance_metrics.trend(scope, window, calendar),
        "departments": departments.redact(department_rows, scope),
        "health_weights": departments.HEALTH_WEIGHTS,
        "comp_off": comp,
    }


def hr(scope, window, calendar, today=None):
    """/analytics/hr -- the operational-quality dashboard."""
    summary = attendance_metrics.summary(scope, window, calendar)
    return {
        "kpis": {
            "headcount": scope.headcount,
            "compliance_pct": summary["compliance_pct"],
            "present_pct": summary["present_pct"],
            "unexplained_days": summary["unexplained_days"],
        },
        "attendance": summary,
        "corrections": corrections(scope, window),
        "approval_times": approval_times(scope, window),
        "queues": queues(scope),
        "mapping": devices.mapping_progress(),
        "wfh": wfh.summary(scope, window),
        "comp_off": comp_off.balances(scope),
        "compliance_by_department": departments.redact(
            departments.rank(departments.rows(scope, window, calendar)), scope),
    }


def management(scope, window, calendar, today=None):
    """/analytics/management -- trends, utilization and forward capacity."""
    summary = attendance_metrics.summary(scope, window, calendar)
    return {
        "kpis": {
            "headcount": scope.headcount,
            "present_pct": summary["present_pct"],
            "absent_pct": summary["absent_pct"],
            "compliance_pct": summary["compliance_pct"],
            "overtime_hours": summary["overtime_hours"],
            "overtime_per_capita": summary["overtime_per_capita"],
        },
        "attendance": summary,
        "utilization": utilization(scope, window, calendar, summary["regular_hours"]),
        "department_trends": departments.trends(scope, window, calendar),
        "departments": departments.redact(
            departments.rank(departments.rows(scope, window, calendar)), scope),
        "overtime": overtime_overview(scope, window, summary),
        "capacity": capacity_outlook(scope, today=today),
        "leave_forecast": leave.forecast(scope, today=today),
    }
