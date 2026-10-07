"""Department analytics and the compliance ranking.

Two constraints from the phase brief shape this module:

* **Rank departments by attendance compliance only.** The health score exists
  and is charted, but it does not order the table -- compliance does.
* **Do not score employees.** The smallest unit here is a department, and a
  department below ``MIN_DEPARTMENT_SAMPLE`` is suppressed rather than ranked,
  because a two-person department's "attendance rate" is one person's
  attendance record wearing a department's name.

The health-score weights are a published constant returned in the API response.
A composite score with a hidden formula is a political weapon; one with a
visible formula is a management tool.
"""
from collections import defaultdict

from django.db.models import Count, Q, Sum

from attendance.models import Attendance

from .. import calendar as work_calendar, periods
from ..scope import MIN_DEPARTMENT_SAMPLE, UNASSIGNED
from .attendance import derive
from .base import (
    attendance_rows,
    bucket_expr,
    clamp,
    leave_rows,
    leave_weight_sum,
    pct,
    status_counts,
)

HEALTH_WEIGHTS = {
    "compliance": 0.50,
    "punctuality": 0.20,   # 100 - late %
    "presence": 0.20,      # 100 - absent %
    "overtime_load": 0.10,  # 100 - overtime stress
}
# Overtime hours per person, per window, at which overtime stress reads 100.
OVERTIME_STRESS_CEILING = 20.0

DEPARTMENT_FIELD = "employee__department_ref_id"
LEAVE_DEPARTMENT_FIELD = "user__department_ref_id"

# Multi-series department charts get unreadable past this many lines. The rest
# are folded into "Others" and the fold is REPORTED, not hidden.
MAX_TREND_SERIES = 8


# ---------------------------------------------------------------------------
# per-department rows
# ---------------------------------------------------------------------------
def rows(scope, window, calendar):
    """One KPI row per department. THREE queries regardless of department count."""
    status_by_department = defaultdict(dict)
    for row in (attendance_rows(scope, window, calendar, working_days_only=True)
                .values(DEPARTMENT_FIELD).annotate(**status_counts())):
        status_by_department[_key(row[DEPARTMENT_FIELD])] = row

    hours_by_department = {
        _key(row[DEPARTMENT_FIELD]): row
        for row in attendance_rows(scope, window).values(DEPARTMENT_FIELD).annotate(
            worked=Sum("working_hours"), regular=Sum("regular_hours"),
            overtime=Sum("overtime_hours"), late_minutes=Sum("late_minutes"),
            overtime_days=Count("id", filter=Q(overtime_hours__gt=0)))
    }
    leave_by_department = {
        _key(row[LEAVE_DEPARTMENT_FIELD]): float(row["days"] or 0)
        for row in leave_rows(scope, window).values(LEAVE_DEPARTMENT_FIELD)
        .annotate(days=leave_weight_sum())
    }
    expected_by_department = work_calendar.expected_by_department(scope, calendar)

    out = []
    for key in scope.departments:
        headcount = scope.department_headcount(key)
        counts = {status.value: (status_by_department.get(key, {}).get(status.value) or 0)
                  for status in Attendance.Status}
        hours = hours_by_department.get(key, {})
        counts.update({
            "leave_days": leave_by_department.get(key, 0.0),
            "expected_days": expected_by_department.get(key, 0),
            "worked": hours.get("worked") or 0,
            "regular": hours.get("regular") or 0,
            "overtime": hours.get("overtime") or 0,
            "late_minutes": hours.get("late_minutes") or 0,
            "overtime_days": hours.get("overtime_days") or 0,
        })
        kpis = derive(counts, headcount=headcount)
        small = headcount < MIN_DEPARTMENT_SAMPLE
        out.append({
            "department_id": None if key == UNASSIGNED else key,
            "department": scope.label(key),
            "headcount": headcount,
            "small_sample": small,
            "health_score": None if small else health_score(kpis),
            **kpis,
        })
    out.sort(key=lambda row: row["department"])
    return out


def _key(value):
    return str(value) if value else UNASSIGNED


def health_score(kpis):
    """Composite 0-100. Returns None when the inputs do not exist.

    A department with no expected working days in the window has no health, and
    inventing 0 would drop it to the bottom of a chart it should not be on.
    """
    compliance = kpis.get("compliance_pct")
    if compliance is None:
        return None
    punctuality = 100.0 - (kpis.get("late_pct") or 0.0)
    presence = 100.0 - (kpis.get("absent_pct") or 0.0)
    overtime_stress = min(100.0, (kpis.get("overtime_per_capita") or 0.0)
                          / OVERTIME_STRESS_CEILING * 100.0)
    score = (HEALTH_WEIGHTS["compliance"] * compliance
             + HEALTH_WEIGHTS["punctuality"] * punctuality
             + HEALTH_WEIGHTS["presence"] * presence
             + HEALTH_WEIGHTS["overtime_load"] * (100.0 - overtime_stress))
    return round(clamp(score), 1)


# ---------------------------------------------------------------------------
# ranking
# ---------------------------------------------------------------------------
def rank(department_rows):
    """Dense rank on attendance compliance ONLY, descending.

    Small departments and departments with no measurable compliance keep their
    row (so nobody wonders where they went) but carry ``rank: null`` and a
    machine-readable reason.
    """
    ranked = sorted(
        (row for row in department_rows
         if not row["small_sample"] and row["compliance_pct"] is not None),
        key=lambda row: row["compliance_pct"], reverse=True)

    position, previous, seen = 0, object(), 0
    for row in ranked:
        seen += 1
        if row["compliance_pct"] != previous:
            position = seen
            previous = row["compliance_pct"]
        row["rank"] = position

    for row in department_rows:
        if "rank" not in row:
            row["rank"] = None
            row["rank_excluded_reason"] = (
                "small_sample" if row["small_sample"] else "no_expected_days")
    return sorted(department_rows,
                  key=lambda row: (row["rank"] is None, row["rank"] or 0, row["department"]))


def org_average(department_rows, field="compliance_pct"):
    """Headcount-weighted organisation average.

    Weighted, not a plain mean: a 3-person department must not move the org
    figure as much as a 60-person one.
    """
    total = sum(row["headcount"] for row in department_rows
                if row.get(field) is not None)
    if not total:
        return None
    weighted = sum((row[field] or 0) * row["headcount"] for row in department_rows
                   if row.get(field) is not None)
    return round(weighted / total, 1)


def percentile_of(department_rows, department_id, field="compliance_pct"):
    """Where one department sits among the ranked ones, 0-100 (higher is better)."""
    values = [row[field] for row in department_rows
              if row.get(field) is not None and not row["small_sample"]]
    target = next((row.get(field) for row in department_rows
                   if row.get("department_id") == department_id), None)
    if target is None or len(values) < 2:
        return None
    below = sum(1 for value in values if value < target)
    return round(below / (len(values) - 1) * 100, 1)


# ---------------------------------------------------------------------------
# trends
# ---------------------------------------------------------------------------
def trends(scope, window, calendar, limit=MAX_TREND_SERIES):
    """Compliance per department per bucket, for the multi-series chart.

    THREE queries. Departments beyond ``limit`` (by headcount) are folded into
    an "Others" series and the fold is reported in the payload.
    """
    expression = bucket_expr(window.granularity)
    attended = defaultdict(lambda: defaultdict(float))
    for row in (attendance_rows(scope, window, calendar, working_days_only=True)
                .annotate(bucket=expression).values("bucket", DEPARTMENT_FIELD)
                .annotate(n=Count("id", filter=Q(status__in=[
                    Attendance.Status.PRESENT, Attendance.Status.LATE,
                    Attendance.Status.HALF_DAY, Attendance.Status.WORK_FROM_HOME])))):
        attended[_key(row[DEPARTMENT_FIELD])][_bucket_key(row["bucket"], window)] += row["n"]

    leave = defaultdict(lambda: defaultdict(float))
    for row in (leave_rows(scope, window).annotate(bucket=expression)
                .values("bucket", LEAVE_DEPARTMENT_FIELD).annotate(days=leave_weight_sum())):
        leave[_key(row[LEAVE_DEPARTMENT_FIELD])][
            _bucket_key(row["bucket"], window)] += float(row["days"] or 0)

    expected = work_calendar.expected_department_series(scope, calendar)

    ordered = sorted(scope.departments,
                     key=lambda key: scope.department_headcount(key), reverse=True)
    keep, folded = ordered[:limit], ordered[limit:]

    keys = periods.buckets(window)
    series = []
    for key in keep:
        series.append({
            "department_id": None if key == UNASSIGNED else key,
            "department": scope.label(key),
            "points": _points(keys, window, expected.get(key, {}),
                              attended[key], leave[key]),
        })
    if folded:
        merged_expected, merged_attended, merged_leave = defaultdict(float), defaultdict(float), defaultdict(float)
        for key in folded:
            for bucket, value in (expected.get(key) or {}).items():
                merged_expected[bucket] += value
            for bucket, value in attended[key].items():
                merged_attended[bucket] += value
            for bucket, value in leave[key].items():
                merged_leave[bucket] += value
        series.append({
            "department_id": None, "department": f"Others ({len(folded)})",
            "is_aggregate": True,
            "points": _points(keys, window, merged_expected, merged_attended, merged_leave),
        })

    return {"series": series, "folded_departments": len(folded),
            "max_series": limit}


def _points(keys, window, expected, attended, leave):
    points = []
    for key in keys:
        expected_days = expected.get(key, 0)
        unexplained = max(0.0, expected_days - attended.get(key, 0) - leave.get(key, 0))
        points.append({
            "period": key,
            "label": periods.bucket_label(key, window.granularity),
            "compliance_pct": clamp(pct(expected_days - unexplained, expected_days)),
            "expected_days": expected_days,
        })
    return points


def _bucket_key(bucket, window):
    if bucket is None:
        return None
    return periods.bucket_key(
        bucket.date() if hasattr(bucket, "date") else bucket, window.granularity)


# ---------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------
def redact(department_rows, scope):
    """Strip other departments' identity and figures for a department head.

    They keep their own row in full, and every other department collapses to a
    position in the ranking. This is the approved decision #4: comparison value
    without disclosing another team's numbers.
    """
    if scope.named_departments:
        return department_rows
    own = scope.own_department_id
    out = []
    for row in department_rows:
        if row["department_id"] == own:
            out.append(row)
        else:
            out.append({
                "department_id": None,
                "department": f"Department {row.get('rank') or '—'}",
                "redacted": True,
                "rank": row.get("rank"),
                "headcount": None,
                "compliance_pct": None,
                "health_score": None,
            })
    return out


# ---------------------------------------------------------------------------
# payloads
# ---------------------------------------------------------------------------
def comparison(scope, window, calendar):
    """Named department rows for the scope in hand. Used by the attendance page."""
    return rank(rows(scope, window, calendar))


def dashboard(scope, window, calendar, org_scope=None):
    """The /analytics/departments payload.

    The ranking is always computed over the ORGANISATION so that a department
    head's rank and the org average mean something; ``redact`` then removes
    every other department's identity and figures before the response is built.
    """
    population = org_scope if org_scope is not None else scope
    all_rows = rank(rows(population, window, calendar))
    own_id = scope.own_department_id

    return {
        "departments": redact(all_rows, scope),
        "org_average": {
            "compliance_pct": org_average(all_rows, "compliance_pct"),
            "present_pct": org_average(all_rows, "present_pct"),
            "late_pct": org_average(all_rows, "late_pct"),
            "overtime_per_capita": org_average(all_rows, "overtime_per_capita"),
            "health_score": org_average(all_rows, "health_score"),
        },
        "own_department": next(
            (row for row in all_rows if row["department_id"] == own_id), None),
        "percentile": percentile_of(all_rows, own_id) if own_id else None,
        "department_count": len(all_rows),
        "trends": trends(scope, window, calendar),
        "health_weights": HEALTH_WEIGHTS,
        "overtime_stress_ceiling": OVERTIME_STRESS_CEILING,
        "min_sample": MIN_DEPARTMENT_SAMPLE,
    }
