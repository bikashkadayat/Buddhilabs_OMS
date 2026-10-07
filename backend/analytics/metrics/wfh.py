"""Work-from-home analytics.

The interesting number in this module is the gap between *approved* and
*worked*. Phase 8 established that an approved WFH request is not attendance --
a check-in is still required -- so a request granted with no work recorded
against it is exactly the thing management wants to see, and it is invisible in
either figure alone.

Approval rate excludes cancelled and pending requests from its denominator. A
request the employee withdrew says nothing about how HR decides.
"""
from collections import defaultdict

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q
from django.utils import timezone

from attendance.models import Attendance, WFHRequest

from .. import periods
from ..scope import UNASSIGNED
from .base import attendance_rows, bucket_expr, pct


def summary(scope, window):
    """Request counts, approval rate and conversion. TWO queries."""
    requests = WFHRequest.objects.filter(
        user_id__in=scope.employee_ids,
        start_date__lte=window.end, end_date__gte=window.start)
    counts = {row["status"]: row["n"] for row in
              requests.values("status").annotate(n=Count("id"))}

    days = attendance_rows(scope, window).aggregate(
        approved_days=Count("id", filter=Q(is_wfh=True)),
        worked_days=Count("id", filter=Q(status=Attendance.Status.WORK_FROM_HOME)))

    approved = counts.get(WFHRequest.Status.APPROVED, 0)
    rejected = counts.get(WFHRequest.Status.REJECTED, 0)
    approved_day_rows = days["approved_days"] or 0
    worked = days["worked_days"] or 0

    return {
        "requests": {status.value: counts.get(status.value, 0)
                     for status in WFHRequest.Status},
        "total_requests": sum(counts.values()),
        # Cancelled and pending are out of the denominator on purpose.
        "approval_rate_pct": pct(approved, approved + rejected),
        "approved_day_rows": approved_day_rows,
        "worked_from_home_days": worked,
        "approved_not_worked_days": max(0, approved_day_rows - worked),
        "conversion_pct": pct(worked, approved_day_rows),
    }


def trend(scope, window):
    """WFH days worked, and requests raised, per bucket. TWO queries."""
    expression = bucket_expr(window.granularity)
    worked = defaultdict(int)
    for row in (attendance_rows(scope, window)
                .filter(status=Attendance.Status.WORK_FROM_HOME)
                .annotate(bucket=expression).values("bucket").annotate(n=Count("id"))):
        worked[_key(row["bucket"], window)] += row["n"]

    raised = defaultdict(int)
    for row in (WFHRequest.objects
                .filter(user_id__in=scope.employee_ids,
                        start_date__gte=window.start, start_date__lte=window.end)
                .annotate(bucket=bucket_expr(window.granularity, "start_date"))
                .values("bucket").annotate(n=Count("id"))):
        raised[_key(row["bucket"], window)] += row["n"]

    return [{
        "period": key,
        "label": periods.bucket_label(key, window.granularity),
        "wfh_days": worked.get(key, 0),
        "requests_raised": raised.get(key, 0),
    } for key in periods.buckets(window)]


def by_department(scope, window):
    """WFH days per department, total and per head. ONE query."""
    rows = (attendance_rows(scope, window)
            .filter(status=Attendance.Status.WORK_FROM_HOME)
            .values("employee__department_ref_id").annotate(n=Count("id")))
    totals = {str(row["employee__department_ref_id"])
              if row["employee__department_ref_id"] else UNASSIGNED: row["n"]
              for row in rows}
    out = []
    for key in scope.departments:
        headcount = scope.department_headcount(key)
        days = totals.get(key, 0)
        out.append({
            "department_id": None if key == UNASSIGNED else key,
            "department": scope.label(key),
            "headcount": headcount,
            "wfh_days": days,
            "days_per_capita": round(days / headcount, 2) if headcount else None,
        })
    out.sort(key=lambda row: row["wfh_days"], reverse=True)
    return out


def approval_time_hours(scope, window):
    """Mean hours from request to decision.

    WFH requests carry no decision timestamp of their own, so this reads
    ``updated_at`` on decided requests. That is an upper bound -- a later edit
    would inflate it -- and it is labelled ``approximate`` in the payload rather
    than presented as measured.
    """
    decided = WFHRequest.objects.filter(
        user_id__in=scope.employee_ids,
        status__in=[WFHRequest.Status.APPROVED, WFHRequest.Status.REJECTED],
        created_at__date__gte=window.start, created_at__date__lte=window.end)
    result = decided.annotate(
        elapsed=ExpressionWrapper(F("updated_at") - F("created_at"),
                                  output_field=DurationField())
    ).aggregate(average=Avg("elapsed"))
    average = result["average"]
    return {
        "hours": round(average.total_seconds() / 3600, 1) if average else None,
        "sample": decided.count(),
        "approximate": True,
    }


def _key(bucket, window):
    if bucket is None:
        return None
    return periods.bucket_key(
        bucket.date() if hasattr(bucket, "date") else bucket, window.granularity)


def dashboard(scope, window):
    return {
        "summary": summary(scope, window),
        "trend": trend(scope, window),
        "by_department": by_department(scope, window),
        "approval_time": approval_time_hours(scope, window),
        "as_of": timezone.localdate().isoformat(),
    }
