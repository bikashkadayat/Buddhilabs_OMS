"""Server-side analytics for the admin dashboard (no client-side computation)."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Avg, Count, Sum
from django.utils import timezone

from leaves.models import EnterpriseLeaveBalance, Leave, LeaveDayRecord, MonthlyLeaveSummary

User = get_user_model()
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _num(v):
    return v if isinstance(v, Decimal) else Decimal(str(v or "0"))


def dashboard_analytics(year=None):
    today = timezone.now().date()
    year = year or today.year

    # --- KPIs ---
    on_leave_today = (
        LeaveDayRecord.objects.filter(date=today, status=LeaveDayRecord.Status.APPROVED)
        .values("user_id").distinct().count()
    )
    this_month = MonthlyLeaveSummary.objects.filter(year=today.year, month=today.month)
    attendance_this_month = this_month.aggregate(a=Avg("attendance_percentage"))["a"] or Decimal("100")
    pending_approvals = Leave.objects.filter(status="pending", is_deleted=False).count()

    approaching = set()
    for b in EnterpriseLeaveBalance.objects.filter(year=year).select_related("leave_type"):
        entitled = _num(b.entitled_days) + _num(b.carried_forward_days)
        if entitled > 0 and (_num(b.used_days) / entitled) >= Decimal("0.8"):
            approaching.add(b.user_id)

    # --- Leave trend (last 12 months) ---
    trend = []
    cursor = today.replace(day=1)
    months = []
    for _ in range(12):
        months.append((cursor.year, cursor.month))
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    for y, m in reversed(months):
        agg = MonthlyLeaveSummary.objects.filter(year=y, month=m).aggregate(t=Sum("total_leave_days"))
        trend.append({"label": f"{MONTHS[m - 1]} {str(y)[2:]}", "leave_days": float(agg["t"] or 0)})

    # --- By department / type (this year, approved working days) ---
    records = LeaveDayRecord.objects.filter(
        year=year, status=LeaveDayRecord.Status.APPROVED, is_weekend=False, is_holiday=False,
    ).select_related("leave_type", "user")
    by_dept, by_type = {}, {}
    for r in records:
        dep = getattr(r.user, "department", "") or "—"
        by_dept[dep] = by_dept.get(dep, 0.0) + float(r.portion_days)
        by_type[r.leave_type.code] = by_type.get(r.leave_type.code, 0.0) + float(r.portion_days)

    # --- Approval turnaround (avg hours submit -> approve) ---
    approved = Leave.objects.filter(status="approved")
    turnaround_hours = 0.0
    if approved.exists():
        secs = [
            (l.updated_at - l.created_at).total_seconds()
            for l in approved.only("created_at", "updated_at")
        ]
        turnaround_hours = round(sum(secs) / len(secs) / 3600, 1)

    # --- Top 10 by used days ---
    top = (
        EnterpriseLeaveBalance.objects.filter(year=year)
        .values("user__username", "user__first_name", "user__last_name")
        .annotate(used=Sum("used_days")).order_by("-used")[:10]
    )
    top10 = [{
        "employee": (f"{t['user__first_name']} {t['user__last_name']}".strip() or t["user__username"]),
        "used": float(t["used"] or 0),
    } for t in top]

    # --- Heatmap (last 60 days, approved record density) ---
    since = today - timedelta(days=60)
    heat = (
        LeaveDayRecord.objects.filter(date__gte=since, date__lte=today, status=LeaveDayRecord.Status.APPROVED)
        .values("date").annotate(count=Count("id")).order_by("date")
    )
    heatmap = [{"date": str(h["date"]), "count": h["count"]} for h in heat]

    return {
        "year": year,
        "kpis": {
            "on_leave_today": on_leave_today,
            "attendance_this_month": float(round(attendance_this_month, 1)),
            "pending_approvals": pending_approvals,
            "approaching_limit": len(approaching),
        },
        "leave_trend": trend,
        "by_department": [{"label": k, "value": round(v, 1)} for k, v in sorted(by_dept.items())],
        "by_type": [{"label": k, "value": round(v, 1)} for k, v in sorted(by_type.items())],
        "approval_turnaround_hours": turnaround_hours,
        "top10": top10,
        "heatmap": heatmap,
    }
