"""Leave analytics: consumption, utilization, distribution and a forecast.

``LeaveDayRecord`` is the grain everything here reads -- one row per calendar
day of a request, already flagged for weekends and holidays. Aggregating the
``Leave`` header instead would count a 5-day request as one leave.

Chart colours come from ``LeaveType.display_color``, which HR already maintains,
rather than a palette invented here. A leave type that is purple in the leave
calendar must be purple in the analytics pie.
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import F, Sum
from django.utils import timezone

from leaves.models import EnterpriseLeaveBalance, LeaveDayRecord

from .. import periods
from ..scope import UNASSIGNED
from .base import bucket_expr, leave_rows, leave_weight_sum, num, pct

FORECAST_MONTHS = 3
# Two full years of the same calendar month is the minimum for a seasonal claim;
# below that the projection falls back to a trailing mean, and below six months
# of history it refuses to project at all.
SEASONAL_YEARS = 2
MIN_HISTORY_MONTHS = 6
BASELINE_MONTHS = 24


def by_type(scope, window):
    """Approved leave days per leave type, descending. ONE query."""
    rows = (leave_rows(scope, window, working_days_only=False)
            .values("leave_type__code", "leave_type__name", "leave_type__display_color")
            .annotate(days=leave_weight_sum()).order_by("-days"))
    total = sum(float(row["days"] or 0) for row in rows)
    return [{
        "code": row["leave_type__code"],
        "label": row["leave_type__name"],
        "colour": row["leave_type__display_color"],
        "days": num(row["days"]),
        "share_pct": pct(row["days"] or 0, total),
    } for row in rows]


def consumption_trend(scope, window):
    """Approved leave days per bucket, split into working and non-working days.

    Both are shown because they answer different questions: working-day leave is
    what costs capacity, total leave is what the employee actually booked.
    """
    expression = bucket_expr(window.granularity)
    working, total = defaultdict(float), defaultdict(float)
    for row in (leave_rows(scope, window, working_days_only=False)
                .annotate(bucket=expression).values("bucket", "is_weekend", "is_holiday")
                .annotate(days=leave_weight_sum())):
        key = _key(row["bucket"], window)
        days = float(row["days"] or 0)
        total[key] += days
        if not (row["is_weekend"] or row["is_holiday"]):
            working[key] += days

    return [{
        "period": key,
        "label": periods.bucket_label(key, window.granularity),
        "leave_days": round(working.get(key, 0.0), 2),
        "total_days": round(total.get(key, 0.0), 2),
    } for key in periods.buckets(window)]


def balance_utilization(scope, year=None):
    """Used against entitled + carried forward, per leave type. ONE query.

    Entitlement is annual, so this ignores the analysis window on purpose --
    utilization of a year's allowance is only meaningful over that year.
    """
    year = year or timezone.localdate().year
    rows = (EnterpriseLeaveBalance.objects
            .filter(user_id__in=scope.employee_ids, year=year)
            .values("leave_type__code", "leave_type__name", "leave_type__display_color")
            .annotate(entitled=Sum(F("entitled_days") + F("carried_forward_days")),
                      used=Sum("used_days"), pending=Sum("pending_days"))
            .order_by("leave_type__name"))
    return {
        "year": year,
        "types": [{
            "code": row["leave_type__code"],
            "label": row["leave_type__name"],
            "colour": row["leave_type__display_color"],
            "entitled_days": num(row["entitled"]),
            "used_days": num(row["used"]),
            "pending_days": num(row["pending"]),
            "remaining_days": num((row["entitled"] or Decimal("0"))
                                  - (row["used"] or Decimal("0"))),
            "utilization_pct": pct(row["used"] or 0, row["entitled"] or 0),
        } for row in rows],
    }


def by_department(scope, window):
    """Approved working-day leave per department. ONE query."""
    rows = (leave_rows(scope, window).values("user__department_ref_id")
            .annotate(days=leave_weight_sum()))
    totals = {str(row["user__department_ref_id"]) if row["user__department_ref_id"]
              else UNASSIGNED: float(row["days"] or 0) for row in rows}
    grand = sum(totals.values())
    out = []
    for key in scope.departments:
        headcount = scope.department_headcount(key)
        days = totals.get(key, 0.0)
        out.append({
            "department_id": None if key == UNASSIGNED else key,
            "department": scope.label(key),
            "headcount": headcount,
            "leave_days": round(days, 2),
            "days_per_capita": round(days / headcount, 2) if headcount else None,
            "share_pct": pct(days, grand),
        })
    out.sort(key=lambda row: row["leave_days"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# forecast
# ---------------------------------------------------------------------------
def forecast(scope, months=FORECAST_MONTHS, today=None):
    """A naive seasonal-baseline projection. Explicitly not a model.

    Per month ahead the projection is ``max(already approved, historical
    baseline)``, not their sum. Approved leave is a floor -- those days are
    committed -- while the baseline is what that calendar month has historically
    consumed in total, including the requests that had not been raised yet at
    this point in the cycle. Adding the two would count the same days twice and
    roughly double the nearest month.

    The method used is returned alongside the numbers, and when there is too
    little history the projection is ``None`` rather than a fabricated figure:
    an invented number on an executive dashboard is worse than a blank one.
    """
    today = today or timezone.localdate()
    history_start = periods.month_start(periods.add_months(today, -BASELINE_MONTHS))

    history = defaultdict(float)
    for row in (LeaveDayRecord.objects
                .filter(user_id__in=scope.employee_ids,
                        status=LeaveDayRecord.Status.APPROVED,
                        leave_request__is_deleted=False,
                        is_weekend=False, is_holiday=False,
                        date__gte=history_start, date__lt=periods.month_start(today))
                .values("year", "month").annotate(days=leave_weight_sum())):
        history[(row["year"], row["month"])] += float(row["days"] or 0)

    # The projection covers whole future months only, so the booked query starts
    # at the first of next month. Fetching from `today` instead would pull in
    # the tail of the current month -- days that are never displayed, because no
    # bucket exists for them.
    horizon_start = periods.add_months(periods.month_start(today), 1)
    horizon_end = periods.add_months(periods.month_start(today), months + 1)
    booked = defaultdict(float)
    for row in (LeaveDayRecord.objects
                .filter(user_id__in=scope.employee_ids,
                        status=LeaveDayRecord.Status.APPROVED,
                        leave_request__is_deleted=False,
                        is_weekend=False, is_holiday=False,
                        date__gte=horizon_start, date__lt=horizon_end)
                .values("year", "month").annotate(days=leave_weight_sum())):
        booked[(row["year"], row["month"])] += float(row["days"] or 0)

    trailing = [value for _key, value in sorted(history.items())][-MIN_HISTORY_MONTHS:]
    method = "insufficient_history"
    if len(history) >= SEASONAL_YEARS * 12:
        method = "seasonal_mean_2y"
    elif len(history) >= MIN_HISTORY_MONTHS:
        method = "trailing_6m"

    points = []
    for offset in range(1, months + 1):
        target = periods.add_months(periods.month_start(today), offset)
        key = (target.year, target.month)
        approved = round(booked.get(key, 0.0), 2)

        baseline = None
        if method == "seasonal_mean_2y":
            samples = [history[(target.year - back, target.month)]
                       for back in range(1, SEASONAL_YEARS + 1)
                       if (target.year - back, target.month) in history]
            baseline = round(sum(samples) / len(samples), 2) if samples else None
        if baseline is None and method != "insufficient_history" and trailing:
            baseline = round(sum(trailing) / len(trailing), 2)

        points.append({
            "period": periods.bucket_key(target, "month"),
            "label": periods.bucket_label(periods.bucket_key(target, "month"), "month"),
            "approved_days": approved,
            "baseline_days": baseline,
            "projected_days": (round(max(approved, baseline), 2)
                               if baseline is not None else None),
        })

    return {
        "method": method,
        "months_of_history": len(history),
        "is_projection": True,
        "points": points,
    }


def _key(bucket, window):
    if bucket is None:
        return None
    return periods.bucket_key(
        bucket.date() if hasattr(bucket, "date") else bucket, window.granularity)


def dashboard(scope, window, today=None):
    return {
        "by_type": by_type(scope, window),
        "consumption_trend": consumption_trend(scope, window),
        "balance_utilization": balance_utilization(scope),
        "by_department": by_department(scope, window),
        "forecast": forecast(scope, today=today),
    }
