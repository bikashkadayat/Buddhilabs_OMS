"""Attendance analytics: rates, exception trends, overtime and period comparison.

Every figure here reads the STORED ``Attendance.status`` and the stored hour
columns that the Phase 8 policy engine already wrote. Nothing re-derives a
status. That is not only a correctness decision -- ``resolve_day_status`` is a
per-employee-per-day Python call, and running it over a 250-person year would
cost ~65,000 invocations, which is the single easiest way to blow the 250 ms
budget.

Rates are always recomputed from raw counts when rolling months into quarters or
years. Averaging twelve monthly percentages would weight a 19-working-day month
the same as a 23-working-day one.
"""
from collections import defaultdict

from django.db.models import Count, Q, Sum

from attendance.models import Attendance

from .. import calendar as work_calendar, periods
from .base import (
    bucket_expr,
    attendance_rows,
    clamp,
    delta,
    leave_rows,
    leave_weight_sum,
    num,
    pct,
    status_counts,
)

# How far back the Monthly / Quarterly / Yearly comparison tabs look. One base
# series is fetched at month grain and rolled up in Python, so three comparison
# views cost one pair of queries rather than three.
COMPARISON_MONTHS = 36


# ---------------------------------------------------------------------------
# raw collection
# ---------------------------------------------------------------------------
def collect(scope, window, calendar):
    """Every raw count the attendance KPIs need. THREE queries, always.

    Returns raw numbers only -- no percentages -- so the same counts can be
    summed across departments or periods before any division happens.
    """
    working = attendance_rows(scope, window, calendar, working_days_only=True).aggregate(
        **status_counts())
    hours = attendance_rows(scope, window).aggregate(
        worked=Sum("working_hours"), regular=Sum("regular_hours"),
        overtime=Sum("overtime_hours"), late_minutes=Sum("late_minutes"),
        overtime_days=Count("id", filter=Q(overtime_hours__gt=0)),
        comp_days=Sum("comp_off_days"))
    leave = leave_rows(scope, window).aggregate(days=leave_weight_sum())

    counts = {key: (value or 0) for key, value in working.items()}
    counts["leave_days"] = float(leave["days"] or 0)
    counts["expected_days"] = work_calendar.expected_total(scope, calendar)
    counts.update({key: (0 if value is None else value) for key, value in hours.items()})
    return counts


def derive(counts, headcount=None):
    """Turn raw counts into the published KPI set.

    Split from ``collect`` on purpose: department rollups, period rollups and
    the export builders all reuse this, so there is exactly one implementation
    of every formula in the KPI matrix.
    """
    expected = counts.get("expected_days") or 0
    present = counts.get(Attendance.Status.PRESENT, 0)
    late = counts.get(Attendance.Status.LATE, 0)
    half = counts.get(Attendance.Status.HALF_DAY, 0)
    wfh = counts.get(Attendance.Status.WORK_FROM_HOME, 0)
    stored_absent = counts.get(Attendance.Status.ABSENT, 0)
    leave_days = counts.get("leave_days", 0)

    attended = present + late + half + wfh
    weighted_present = present + late + wfh + 0.5 * half

    # Unexplained: an expected working day with no attendance row and no
    # approved leave. A stored 'absent' row is unexplained too -- HR marking
    # someone absent records the absence, it does not excuse it.
    unexplained = expected - attended - leave_days
    warnings = []
    if unexplained < 0:
        # Only reachable when approved leave overlaps a worked day (the Phase 9
        # conflict). Surfaced, never silently zeroed: a negative denominator
        # component means the underlying data needs a human.
        warnings.append("leave_attendance_overlap")
        unexplained = 0

    absent_equivalent = expected - weighted_present - leave_days
    if absent_equivalent < 0:
        absent_equivalent = 0

    return {
        # raw
        "expected_days": expected,
        "attended_days": attended,
        "present_days": present,
        "late_days": late,
        "half_days": half,
        "wfh_days": wfh,
        "stored_absent_days": stored_absent,
        "leave_days": round(leave_days, 2),
        "unexplained_days": round(unexplained, 2),
        "overtime_hours": num(counts.get("overtime")),
        "worked_hours": num(counts.get("worked")),
        "regular_hours": num(counts.get("regular")),
        "late_minutes": int(counts.get("late_minutes") or 0),
        "overtime_days": counts.get("overtime_days", 0),
        "comp_off_days_earned": num(counts.get("comp_days")),
        # rates -- None, never 0, when there is nothing to divide by
        "present_pct": clamp(pct(weighted_present, expected)),
        "absent_pct": clamp(pct(absent_equivalent, expected)),
        "leave_pct": clamp(pct(leave_days, expected)),
        "late_pct": clamp(pct(late, attended)),
        "half_day_pct": clamp(pct(half, attended)),
        "wfh_pct": clamp(pct(wfh, attended)),
        "compliance_pct": clamp(pct(expected - unexplained, expected)),
        "avg_working_hours": (round(num(counts.get("worked")) / attended, 2)
                              if attended else None),
        "overtime_per_capita": (round(num(counts.get("overtime")) / headcount, 2)
                                if headcount else None),
        "warnings": warnings,
    }


def summary(scope, window, calendar):
    """The KPI block. Used by the executive, management and attendance pages."""
    return derive(collect(scope, window, calendar), headcount=scope.headcount)


# ---------------------------------------------------------------------------
# trends
# ---------------------------------------------------------------------------
def trend(scope, window, calendar):
    """Per-bucket attendance rates. TWO queries plus a query-free calendar pass.

    Densified against the window's full bucket list, so a month with no rows is
    a zero point rather than a gap -- charts with holes are read as outages.
    """
    expression = bucket_expr(window.granularity)

    status_rows = list(attendance_rows(scope, window, calendar, working_days_only=True)
                       .annotate(bucket=expression).values("bucket")
                       .annotate(**status_counts(),
                                 overtime=Sum("overtime_hours"),
                                 worked=Sum("working_hours"),
                                 late_minutes=Sum("late_minutes")))
    leave_by_bucket = {
        _key(row["bucket"], window): float(row["days"] or 0)
        for row in leave_rows(scope, window).annotate(bucket=expression)
        .values("bucket").annotate(days=leave_weight_sum())
    }
    expected_by_bucket = work_calendar.expected_series(scope, calendar)

    collected = {_key(row["bucket"], window): row for row in status_rows}
    series = []
    for key in periods.buckets(window):
        row = collected.get(key, {})
        counts = {status.value: (row.get(status.value) or 0) for status in Attendance.Status}
        counts.update({
            "leave_days": leave_by_bucket.get(key, 0.0),
            "expected_days": expected_by_bucket.get(key, 0),
            "overtime": row.get("overtime") or 0,
            "worked": row.get("worked") or 0,
            "late_minutes": row.get("late_minutes") or 0,
        })
        point = derive(counts)
        series.append({
            "period": key,
            "label": periods.bucket_label(key, window.granularity),
            **{field: point[field] for field in (
                "present_pct", "absent_pct", "late_pct", "half_day_pct", "wfh_pct",
                "leave_pct", "compliance_pct", "expected_days", "attended_days",
                "present_days", "late_days", "half_days", "wfh_days", "leave_days",
                "unexplained_days", "overtime_hours", "worked_hours")},
        })
    return series


def _key(bucket, window):
    if bucket is None:
        return None
    return periods.bucket_key(
        bucket.date() if hasattr(bucket, "date") else bucket, window.granularity)


# ---------------------------------------------------------------------------
# period comparison (monthly / quarterly / yearly)
# ---------------------------------------------------------------------------
def comparisons(scope, today=None):
    """Monthly, quarterly and yearly views of the same measures.

    Built from ONE 36-month monthly series and rolled up in Python. Fetching a
    quarterly and a yearly series separately would triple the query cost to
    produce numbers that are, by definition, sums of the monthly ones.
    """
    from django.utils import timezone

    today = today or timezone.localdate()
    start = periods.month_start(periods.add_months(today, -(COMPARISON_MONTHS - 1)))
    base_window = periods.Window(start=start, end=today, granularity="month")
    base_calendar = work_calendar.build(base_window, today)
    monthly = trend(scope, base_window, base_calendar)

    return {
        "monthly": monthly[-12:],
        "quarterly": _rollup(monthly, "quarter")[-8:],
        "yearly": _rollup(monthly, "year")[-3:],
    }


_ROLLUP_RAW = ("expected_days", "attended_days", "present_days", "late_days",
               "half_days", "wfh_days", "leave_days", "unexplained_days",
               "overtime_hours", "worked_hours")


def _rollup(monthly, granularity):
    """Sum monthly RAW counts into coarser buckets, then recompute the rates.

    Recomputing rather than averaging is the whole point: a mean of monthly
    percentages weights a short month equally with a long one.
    """
    grouped = defaultdict(lambda: dict.fromkeys(_ROLLUP_RAW, 0.0))
    for point in monthly:
        year, month = point["period"].split("-")
        key = (f"{year}-Q{(int(month) - 1) // 3 + 1}" if granularity == "quarter" else year)
        bucket = grouped[key]
        for field in _ROLLUP_RAW:
            bucket[field] += point[field] or 0

    out = []
    for key in sorted(grouped):
        raw = grouped[key]
        expected = raw["expected_days"]
        attended = raw["attended_days"]
        weighted_present = (raw["present_days"] + raw["late_days"] + raw["wfh_days"]
                            + 0.5 * raw["half_days"])
        out.append({
            "period": key,
            "label": periods.bucket_label(key, granularity),
            "expected_days": round(expected, 2),
            "attended_days": round(attended, 2),
            "leave_days": round(raw["leave_days"], 2),
            "overtime_hours": round(raw["overtime_hours"], 2),
            "worked_hours": round(raw["worked_hours"], 2),
            "present_pct": clamp(pct(weighted_present, expected)),
            "absent_pct": clamp(pct(
                max(0.0, expected - weighted_present - raw["leave_days"]), expected)),
            "late_pct": clamp(pct(raw["late_days"], attended)),
            "half_day_pct": clamp(pct(raw["half_days"], attended)),
            "wfh_pct": clamp(pct(raw["wfh_days"], attended)),
            "leave_pct": clamp(pct(raw["leave_days"], expected)),
            "compliance_pct": clamp(pct(expected - raw["unexplained_days"], expected)),
        })
    return out


# ---------------------------------------------------------------------------
# the /analytics/attendance payload
# ---------------------------------------------------------------------------
KPI_FIELDS = ("present_pct", "absent_pct", "late_pct", "half_day_pct", "wfh_pct",
              "leave_pct", "compliance_pct", "overtime_hours", "avg_working_hours")


def dashboard(scope, window, calendar, today=None):
    from . import departments

    current = summary(scope, window, calendar)
    payload = {
        "kpis": current,
        "trend": trend(scope, window, calendar),
        "comparisons": comparisons(scope, today),
        "by_department": departments.comparison(scope, window, calendar),
    }
    if window.compare:
        payload["comparison"] = compare(scope, window, current, today)
    return payload


def compare(scope, window, current, today=None):
    """The same KPI block for the comparison window, plus per-KPI deltas.

    ``current`` is passed in rather than recomputed: the caller already paid for
    it, and running ``summary`` twice is three wasted queries on every request
    that asks for a comparison.
    """
    previous_window = periods.previous_window(window)
    previous_calendar = work_calendar.build(previous_window, today)
    previous = summary(scope, previous_window, previous_calendar)
    return {
        "window": previous_window.as_dict(),
        "kpis": previous,
        "deltas": {field: delta(current.get(field), previous.get(field))
                   for field in KPI_FIELDS},
    }
