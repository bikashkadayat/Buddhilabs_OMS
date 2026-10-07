"""Compensatory-off analytics.

Balances are cumulative, not windowed: a day earned two years ago and never
taken is still a liability today. So ``balances`` deliberately ignores the
analysis window, while ``trend`` and ``earned_in_window`` respect it. Mixing the
two is how a comp-off dashboard ends up claiming an organisation owes nothing.

The arithmetic matches ``attendance.workforce.aggregates.comp_off_summary``
exactly -- earned counts confirmed EARN rows, pending counts unconfirmed ones,
and available is earned minus used -- so the executive figure and the HR queue
can never disagree.
"""
from collections import defaultdict

from django.db.models import Count, Sum

from leaves.models import CompensatoryLedger

from .. import periods
from ..scope import UNASSIGNED


def balances(scope):
    """Cumulative earned / used / pending / available. ONE grouped query."""
    rows = (CompensatoryLedger.objects.filter(user_id__in=scope.employee_ids)
            .values("entry_type", "status").annotate(days=Sum("days"), n=Count("id")))
    earned = used = pending = 0.0
    for row in rows:
        days = float(row["days"] or 0)
        if row["entry_type"] == CompensatoryLedger.EntryType.USE:
            used += days
        elif row["status"] == CompensatoryLedger.Status.CONFIRMED:
            earned += days
        else:
            pending += days
    return {
        "earned": round(earned, 2),
        "used": round(used, 2),
        "pending": round(pending, 2),
        "available": round(earned - used, 2),
    }


def earned_in_window(scope, window):
    """Comp days earned inside the analysis window. ONE query."""
    rows = (CompensatoryLedger.objects
            .filter(user_id__in=scope.employee_ids,
                    entry_type=CompensatoryLedger.EntryType.EARN,
                    source_date__gte=window.start, source_date__lte=window.end)
            .values("status").annotate(days=Sum("days")))
    by_status = {row["status"]: float(row["days"] or 0) for row in rows}
    return {
        "confirmed": round(by_status.get(CompensatoryLedger.Status.CONFIRMED, 0.0), 2),
        "pending": round(by_status.get(CompensatoryLedger.Status.PENDING, 0.0), 2),
    }


def by_department(scope):
    """Cumulative position per department. ONE grouped query."""
    rows = (CompensatoryLedger.objects.filter(user_id__in=scope.employee_ids)
            .values("user__department_ref_id", "entry_type", "status")
            .annotate(days=Sum("days")))
    buckets = defaultdict(lambda: {"earned": 0.0, "used": 0.0, "pending": 0.0})
    for row in rows:
        key = (str(row["user__department_ref_id"])
               if row["user__department_ref_id"] else UNASSIGNED)
        days = float(row["days"] or 0)
        if row["entry_type"] == CompensatoryLedger.EntryType.USE:
            buckets[key]["used"] += days
        elif row["status"] == CompensatoryLedger.Status.CONFIRMED:
            buckets[key]["earned"] += days
        else:
            buckets[key]["pending"] += days

    out = []
    for key in scope.departments:
        bucket = buckets.get(key, {"earned": 0.0, "used": 0.0, "pending": 0.0})
        out.append({
            "department_id": None if key == UNASSIGNED else key,
            "department": scope.label(key),
            "headcount": scope.department_headcount(key),
            "earned": round(bucket["earned"], 2),
            "used": round(bucket["used"], 2),
            "pending": round(bucket["pending"], 2),
            "available": round(bucket["earned"] - bucket["used"], 2),
        })
    out.sort(key=lambda row: row["available"], reverse=True)
    return out


def trend(scope, window):
    """Earned (confirmed vs pending) and used, per bucket. TWO queries.

    Earn rows are bucketed on ``source_date`` -- the Saturday actually worked --
    and use rows on ``created_at``, because a comp day is consumed when it is
    taken, not when it was originally earned.
    """
    from .base import bucket_expr

    earned = defaultdict(lambda: {"confirmed": 0.0, "pending": 0.0})
    for row in (CompensatoryLedger.objects
                .filter(user_id__in=scope.employee_ids,
                        entry_type=CompensatoryLedger.EntryType.EARN,
                        source_date__gte=window.start, source_date__lte=window.end)
                .annotate(bucket=bucket_expr(window.granularity, "source_date"))
                .values("bucket", "status").annotate(days=Sum("days"))):
        key = _key(row["bucket"], window)
        field = ("confirmed" if row["status"] == CompensatoryLedger.Status.CONFIRMED
                 else "pending")
        earned[key][field] += float(row["days"] or 0)

    used = defaultdict(float)
    for row in (CompensatoryLedger.objects
                .filter(user_id__in=scope.employee_ids,
                        entry_type=CompensatoryLedger.EntryType.USE,
                        created_at__date__gte=window.start,
                        created_at__date__lte=window.end)
                .annotate(bucket=bucket_expr(window.granularity, "created_at__date"))
                .values("bucket").annotate(days=Sum("days"))):
        used[_key(row["bucket"], window)] += float(row["days"] or 0)

    return [{
        "period": key,
        "label": periods.bucket_label(key, window.granularity),
        "earned_confirmed": round(earned[key]["confirmed"], 2),
        "earned_pending": round(earned[key]["pending"], 2),
        "used": round(used.get(key, 0.0), 2),
    } for key in periods.buckets(window)]


def _key(bucket, window):
    if bucket is None:
        return None
    return periods.bucket_key(
        bucket.date() if hasattr(bucket, "date") else bucket, window.granularity)


def dashboard(scope, window):
    position = balances(scope)
    return {
        "balances": position,
        "earned_in_window": earned_in_window(scope, window),
        "by_department": by_department(scope),
        "trend": trend(scope, window),
        # Unused comp days are a real accrual, so the headline liability is
        # stated rather than left for the reader to compute from two tiles.
        "liability_days": position["available"],
        "liability_per_capita": (round(position["available"] / scope.headcount, 2)
                                 if scope.headcount else None),
    }
