"""Employee, manager and HR dashboards.

All three are read-only and all three get their arithmetic from
``aggregates.py``, so they cannot disagree with each other or with the live
attendance widgets.

Every handler runs inside ``resolver.policy_cache()``: policy and shift lookups
are then paid once per employee for the whole request instead of once per
employee-day (Phase 8 measured 27.5x on that change).
"""
from collections import Counter
from datetime import date as _date, timedelta

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from users.models import User

from .. import services
from ..models import Attendance, WFHRequest
from ..policy import resolver as policy_resolver
from ..views import IsHROrAdmin
from . import aggregates, conflicts
from .models import AttendanceCorrectionRequest
from users.roles import has_org_wide_read as _org_wide_read


class IsManagerOrAbove(IsAuthenticated):
    """Department heads, HR, the Board and Admin. Employees have their own dashboard."""

    def has_permission(self, request, view):
        return super().has_permission(request, view) and (
            request.user.role == User.Roles.CHECKER or _org_wide_read(request.user))


def _window(request, default_days=30):
    """`?from=&to=` with a sane default, validated."""
    today = timezone.localdate()
    raw_from = request.query_params.get("from")
    raw_to = request.query_params.get("to")
    try:
        end = _date.fromisoformat(raw_to) if raw_to else today
        start = (_date.fromisoformat(raw_from) if raw_from
                 else end - timedelta(days=default_days - 1))
    except ValueError:
        raise ValidationError({"detail": "Dates must be YYYY-MM-DD."})
    if end < start:
        raise ValidationError({"detail": "`to` must not precede `from`."})
    return start, end


class EmployeeDashboardView(APIView):
    """GET /api/v1/workforce/me/ — everything one employee needs about themselves."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        employee = request.user
        today = timezone.localdate()

        with policy_resolver.policy_cache():
            # One window fetch covers both the 14-day history AND today, so the
            # dashboard never re-asks "is today a holiday / is this employee on
            # leave" — that duplication was most of the query budget.
            window = aggregates.employee_window(employee, end=today)
            record = window["records"].get(today)
            holiday_today = aggregates.is_holiday_day(today, window["holidays"])
            effective = services.resolve_day_status(
                record=record, is_holiday_day=holiday_today,
                is_leave_day=today in window["leave_days"], d=today,
                floor=services.absent_floor(employee), today=today,
                now=services.now_local())

            policy = policy_resolver.resolve_policy(employee, today)
            shift = policy_resolver.resolve_shift(employee, today, policy)
            late_at, half_at = policy_resolver.status_boundaries(policy, shift)

            history = aggregates.recent_history(employee, window=window)
            month_start = today.replace(day=1)
            hours = aggregates.hours_summary([employee.pk], month_start, today)

        # One query for every WFH fact on this dashboard.
        wfh_rows = list(WFHRequest.objects.filter(user=employee)
                        .values("status", "start_date", "end_date"))
        wfh_today = any(row["status"] == WFHRequest.Status.APPROVED
                        and row["start_date"] <= today <= row["end_date"]
                        for row in wfh_rows)
        wfh_counts = Counter(row["status"] for row in wfh_rows)

        # One aggregate instead of three separate .count() calls.
        correction_counts = AttendanceCorrectionRequest.objects.filter(
            employee=employee).aggregate(
            open=Count("id", filter=Q(status__in=AttendanceCorrectionRequest.OPEN_STATUSES)),
            applied=Count("id", filter=Q(
                status=AttendanceCorrectionRequest.Status.HR_APPROVED)),
            rejected=Count("id", filter=Q(
                status=AttendanceCorrectionRequest.Status.REJECTED)))
        # comp_off_summary is one grouped query; category_engine.comp_summary
        # issues three. Same figures.
        comp = aggregates.comp_off_summary([employee.pk])

        return Response({
            "date": today.isoformat(),
            "employee": {
                "id": str(employee.id),
                "name": employee.get_full_name(),
                "employee_id": employee.employee_id,
                "department": (employee.department_ref.name
                               if employee.department_ref else None),
            },
            "today": {
                "status": effective,
                "check_in": record.check_in.isoformat() if record and record.check_in else None,
                "check_out": record.check_out.isoformat() if record and record.check_out else None,
                "working_hours": str(record.working_hours) if record else "0.00",
                "regular_hours": str(record.regular_hours) if record else "0.00",
                "overtime_hours": str(record.overtime_hours) if record else "0.00",
                "late_minutes": record.late_minutes if record else 0,
                "is_wfh": bool(record and record.is_wfh),
                "comp_off_eligible": bool(record and record.comp_off_eligible),
                "source": record.source if record else None,
                "can_check_in": bool(not (record and record.check_in)
                                     and not holiday_today),
                "can_check_out": bool(record and record.check_in and not record.check_out),
            },
            "shift": {
                "name": shift.name if shift else None,
                "start_time": shift.start_time.strftime("%H:%M") if shift else None,
                "end_time": shift.end_time.strftime("%H:%M") if shift else None,
            },
            "policy": {
                "name": policy.name,
                "office_start": policy.office_start_time.strftime("%H:%M"),
                "late_after": late_at.strftime("%H:%M"),
                "half_day_after": half_at.strftime("%H:%M") if half_at else None,
            },
            "month_to_date": {
                "working_hours": str(hours["working"]),
                "regular_hours": str(hours["regular"]),
                "overtime_hours": str(hours["overtime"]),
                "late_days": hours["late_days"],
                "late_minutes": hours["late_minutes"],
            },
            "comp_off": {key: float(value) for key, value in comp.items()},
            "leave_balances": _leave_balances(employee),
            "wfh": {
                "approved_today": wfh_today,
                "counts": {s.value: wfh_counts.get(s.value, 0) for s in WFHRequest.Status},
            },
            "recent_attendance": history,
            "corrections": correction_counts,
        })


def _leave_balances(employee):
    """Reuses the existing balance model rather than recomputing entitlements."""
    from leaves.models import LeaveBalance

    year = timezone.localdate().year
    return [{
        "leave_type": balance.leave_type,
        "total_allocated": balance.total_allocated,
        "used_so_far": balance.used_so_far,
        "remaining": balance.remaining,
    } for balance in LeaveBalance.objects.filter(user=employee, year=year)]


class ManagerDashboardView(APIView):
    """GET /api/v1/workforce/team/ — the department head's view of their team."""
    permission_classes = [IsManagerOrAbove]

    def get(self, request):
        today = timezone.localdate()
        employees = list(aggregates.scoped_employees(request.user))
        emp_ids = [e.pk for e in employees]

        with policy_resolver.policy_cache():
            records = {a.employee_id: a for a in Attendance.objects.filter(
                employee_id__in=emp_ids, date=today)}
            counts, _per_employee, holiday = aggregates.day_counts(
                employees, today, records=records)
            trend = aggregates.attendance_trend(emp_ids)
            breakdown = aggregates.department_breakdown(employees, today, records=records)
            month_start = today.replace(day=1)
            hours = aggregates.hours_summary(emp_ids, month_start, today)

        queues = aggregates.pending_queue_counts(request.user, emp_ids)
        window_start, window_end = _window(request)
        team_conflicts = conflicts.detect(emp_ids, window_start, window_end)

        return Response({
            "date": today.isoformat(),
            "is_holiday": holiday,
            "holiday_name": services.holiday_name(today) if holiday else None,
            "team_size": len(employees),
            "counts": counts,
            "present_now": aggregates.present_now(counts),
            "queues": queues,
            "month_to_date": {key: str(value) for key, value in hours.items()},
            "trends": trend,
            "department_summary": breakdown,
            "conflicts": conflicts.summarise(team_conflicts),
            "absent_employees": _named(employees, records, counts_key="absent",
                                       day=today, user=request.user),
        })


def _named(employees, records, *, counts_key, day, user):
    """Who exactly is absent today — a count alone is not actionable."""
    on_leave = aggregates.leave_user_ids([e.pk for e in employees], day)
    holiday = services.is_holiday(day)
    today = timezone.localdate()
    now = services.now_local()
    out = []
    for employee in employees:
        status = services.resolve_day_status(
            record=records.get(employee.pk), is_holiday_day=holiday,
            is_leave_day=employee.pk in on_leave, d=day,
            floor=services.absent_floor(employee), today=today, now=now)
        if status == counts_key:
            out.append({"id": str(employee.pk), "name": employee.get_full_name(),
                        "employee_id": employee.employee_id})
    return out


class HRCommandCenterView(APIView):
    """GET /api/v1/workforce/hr/ — org-wide state plus every actionable queue."""
    permission_classes = [IsHROrAdmin]

    def get(self, request):
        from ..dashboard_views import _device_health, _recent_punches

        today = timezone.localdate()
        employees = list(aggregates.scoped_employees(request.user))
        emp_ids = [e.pk for e in employees]

        with policy_resolver.policy_cache():
            records = {a.employee_id: a for a in Attendance.objects.filter(
                employee_id__in=emp_ids, date=today)}
            counts, _per_employee, holiday = aggregates.day_counts(
                employees, today, records=records)
            trend = aggregates.attendance_trend(emp_ids)
            breakdown = aggregates.department_breakdown(employees, today, records=records)
            month_start = today.replace(day=1)
            hours = aggregates.hours_summary(emp_ids, month_start, today)

        devices = _device_health(request.user)
        window_start, window_end = _window(request)

        return Response({
            "date": today.isoformat(),
            "is_holiday": holiday,
            "holiday_name": services.holiday_name(today) if holiday else None,
            "headcount": len(employees),
            "counts": counts,
            "present_now": aggregates.present_now(counts),
            "comp_off_eligible_today": sum(
                1 for record in records.values() if record.comp_off_eligible),
            "devices": {
                "online": [d for d in devices if d["connection_status"] == "online"],
                "offline": [d for d in devices if d["connection_status"] != "online"],
                "pending_mapping": sum(d["unmapped_count"] for d in devices),
            },
            "queues": aggregates.pending_queue_counts(request.user),
            "month_to_date": {key: str(value) for key, value in hours.items()},
            "trends": trend,
            "department_breakdown": breakdown,
            "comp_off": aggregates.comp_off_summary(emp_ids),
            "wfh": aggregates.wfh_summary(emp_ids, window_start, window_end),
            "recent_punches": _recent_punches(emp_ids, request.user),
        })


class ConflictReportView(APIView):
    """GET /api/v1/workforce/conflicts/ — approved leave that met real attendance.

    Detection only. No leave is refunded and no attendance is altered; resolving
    a conflict stays an explicit human decision.
    """
    permission_classes = [IsManagerOrAbove]

    def get(self, request):
        start, end = _window(request, default_days=90)
        emp_ids = [e.pk for e in aggregates.scoped_employees(request.user)]
        rows = conflicts.detect(emp_ids, start, end)
        return Response({
            "from": start.isoformat(), "to": end.isoformat(),
            "summary": conflicts.summarise(rows),
            "conflicts": rows,
        })


class LeaveConflictPreviewView(APIView):
    """GET /api/v1/workforce/leave-preview/?start=&end=&portion=

    Advisory warnings for a leave request that has not been submitted. Read-only
    and non-blocking by design: ``LeaveViewSet.perform_create`` holds row locks
    inside an atomic block, and this phase does not touch it.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = request.query_params
        try:
            start = _date.fromisoformat(params["start"])
            end = _date.fromisoformat(params.get("end") or params["start"])
        except (KeyError, ValueError):
            raise ValidationError({"detail": "`start` (and optional `end`) must be YYYY-MM-DD."})

        employee = request.user
        if params.get("employee") and request.user.role in (
                User.Roles.APPROVER, User.Roles.ADMIN):
            employee = User.objects.filter(pk=params["employee"]).first() or request.user

        portion = params.get("portion", "full")
        return Response({
            "start": start.isoformat(), "end": end.isoformat(),
            "warnings": conflicts.warnings_for_request(employee, start, end, portion),
        })


class WFHSummaryView(APIView):
    """GET /api/v1/workforce/wfh/summary/ — WFH dashboard + report data."""
    permission_classes = [IsManagerOrAbove]

    def get(self, request):
        start, end = _window(request)
        employees = list(aggregates.scoped_employees(request.user))
        emp_ids = [e.pk for e in employees]

        pending = (WFHRequest.objects
                   .filter(user_id__in=emp_ids, status=WFHRequest.Status.PENDING)
                   .select_related("user")
                   .order_by("start_date"))
        return Response({
            "from": start.isoformat(), "to": end.isoformat(),
            "summary": aggregates.wfh_summary(emp_ids, start, end),
            "pending_queue": [{
                "id": str(w.pk), "employee": w.user.get_full_name(),
                "employee_id": str(w.user_id),
                "start_date": w.start_date.isoformat(),
                "end_date": w.end_date.isoformat(),
                "reason": w.reason,
                "created_at": w.created_at.isoformat(),
            } for w in pending[:100]],
            # Approval stays HR-only (Phase 8 decision); managers see the queue
            # so they know what is coming, but cannot act on it.
            "can_approve": request.user.role in (User.Roles.APPROVER, User.Roles.ADMIN),
        })


class CompOffSummaryView(APIView):
    """GET /api/v1/workforce/comp-off/summary/ — balances, queue and trends."""
    permission_classes = [IsManagerOrAbove]

    def get(self, request):
        from leaves.models import CompensatoryLedger

        employees = list(aggregates.scoped_employees(request.user))
        emp_ids = [e.pk for e in employees]

        pending = (CompensatoryLedger.objects
                   .filter(user_id__in=emp_ids,
                           entry_type=CompensatoryLedger.EntryType.EARN,
                           source=CompensatoryLedger.Source.ATTENDANCE,
                           status=CompensatoryLedger.Status.PENDING)
                   .select_related("user").order_by("source_date"))
        recent = (CompensatoryLedger.objects
                  .filter(user_id__in=emp_ids)
                  .select_related("user").order_by("-created_at")[:50])

        return Response({
            "summary": {key: float(value) for key, value
                        in aggregates.comp_off_summary(emp_ids).items()},
            "pending_queue": [{
                "id": str(e.pk), "employee": e.user.get_full_name(),
                "employee_id": str(e.user_id), "days": float(e.days),
                "source_date": e.source_date.isoformat() if e.source_date else None,
                "note": e.note,
            } for e in pending[:100]],
            "history": [{
                "id": str(e.pk), "employee": e.user.get_full_name(),
                "entry_type": e.entry_type, "source": e.source, "status": e.status,
                "days": float(e.days),
                "source_date": e.source_date.isoformat() if e.source_date else None,
                "created_at": e.created_at.isoformat(),
            } for e in recent],
            "trend": aggregates.comp_off_trend(emp_ids),
            "can_confirm": request.user.role in (User.Roles.APPROVER, User.Roles.ADMIN),
        })
