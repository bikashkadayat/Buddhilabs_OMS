"""Live attendance dashboard feed.

The WebSocket only tells the browser that *something changed*; this endpoint is
what it re-reads. Keeping the numbers in one server-side place means the live
view and a plain page refresh can never disagree, and a client with no WebSocket
at all still gets the same dashboard by polling.
"""
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from users.models import User

from . import services
from .models import Attendance
from users.roles import has_org_wide_read as _org_wide_read

RECENT_PUNCH_LIMIT = 20

# How far ahead of the server clock a punch may be stamped and still be shown.
#
# A terminal whose clock has glitched writes records years in the future — the
# main-gate device holds seven stamped 2033-02-22. They are genuine rows and are
# never deleted, but "most recent" is ordered by timestamp, so they pin
# themselves to the top of the live feed permanently and push the actual punches
# from today off the end of it. The feed then looks stale on a system that is
# working perfectly.
#
# The tolerance is deliberately not zero: a device running slightly ahead of the
# server is normal and its punches are real. It matches
# BIOMETRIC_CLOCK_SKEW_SECONDS (5 minutes), which is the same allowance the
# ingest path already makes for skew.
FUTURE_PUNCH_TOLERANCE = timedelta(minutes=5)


def scoped_employees(user):
    """Employees this user may see — same rules as AttendanceListView."""
    qs = User.objects.filter(is_active=True).select_related("department_ref")
    if _org_wide_read(user):  # Board sees this read-only (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE).
        return qs
    if user.role == User.Roles.CHECKER and user.department_ref_id:
        return qs.filter(department_ref_id=user.department_ref_id)
    return qs.filter(pk=user.pk)


class AttendanceDashboardView(APIView):
    """Today's counts, device health and the recent punch feed.

    Powers the live widgets. Every figure is role-scoped, so a department head
    polling this sees exactly the same subset the WebSocket sends them.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        now = services.now_local()
        today = now.date()
        employees = list(scoped_employees(request.user))
        emp_ids = [e.pk for e in employees]

        records = {
            a.employee_id: a
            for a in Attendance.objects.filter(employee_id__in=emp_ids, date=today)
        }
        holiday = services.is_holiday(today)
        leave_ids = set(_employees_on_leave(emp_ids, today))

        # Derived from the enum, not a literal list: a hardcoded dict would
        # raise KeyError (HTTP 500) the moment a new status ships — which is
        # exactly what Phase 8's WORK_FROM_HOME would have done.
        counts = {s.value: 0 for s in Attendance.Status}
        counts["not_applicable"] = 0
        for emp in employees:
            status = services.resolve_day_status(
                record=records.get(emp.pk),
                is_holiday_day=holiday,
                is_leave_day=emp.pk in leave_ids,
                d=today, floor=services.absent_floor(emp), today=today, now=now,
            )
            counts["not_applicable" if status is None else status] += 1

        return Response({
            "date": today.isoformat(),
            "is_holiday": holiday,
            "holiday_name": services.holiday_name(today) if holiday else None,
            "total_employees": len(employees),
            "counts": counts,
            # Convenience alias: "checked in today", which is what the headline
            # number on the dashboard actually means.
            "present_now": (counts["present"] + counts["late"] + counts["half_day"]
                            + counts["wfh"]),
            "devices": _device_health(request.user),
            "recent_punches": _recent_punches(emp_ids, request.user),
        })


def _employees_on_leave(emp_ids, day):
    from leaves.models import Leave

    return Leave.objects.filter(
        user_id__in=emp_ids, status=Leave.Status.APPROVED, is_deleted=False,
        start_date__lte=day, end_date__gte=day,
    ).values_list("user_id", flat=True)


def _device_health(user):
    """Device status is HR/Admin only — it is infrastructure, not attendance."""
    if user.role not in (User.Roles.APPROVER, User.Roles.ADMIN):
        return []

    from biometric.models import BiometricDevice

    devices = BiometricDevice.objects.filter(is_active=True).annotate(
        unmapped_count=Count("employees", filter=Q(employees__user__isnull=True,
                                                   employees__is_active=True)),
    )
    return [{
        "id": str(d.pk), "name": d.name, "label": d.label,
        "connection_status": d.connection_status,
        "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
        "last_sync_at": d.last_sync_at.isoformat() if d.last_sync_at else None,
        "last_punch_at": d.last_punch_at.isoformat() if d.last_punch_at else None,
        "pending_punches": d.pending_punches,
        "successful_batches": d.successful_batches,
        "failed_batches": d.failed_batches,
        "unmapped_count": d.unmapped_count,
    } for d in devices]


def _recent_punches(emp_ids, user):
    from biometric.models import AttendancePunch

    qs = (AttendancePunch.objects
          .select_related("device", "user")
          # Clock-glitch rows are stored but not shown: see
          # FUTURE_PUNCH_TOLERANCE. Filtered rather than deleted — the raw punch
          # log is the audit trail every derived figure traces back to.
          .filter(timestamp__lte=timezone.now() + FUTURE_PUNCH_TOLERANCE)
          .order_by("-timestamp"))
    if user.role in (User.Roles.APPROVER, User.Roles.ADMIN):
        # HR also sees unmapped punches — those are the ones needing action.
        qs = qs.filter(Q(user_id__in=emp_ids) | Q(user__isnull=True))
    else:
        qs = qs.filter(user_id__in=emp_ids)

    return [{
        "punch_id": str(p.pk),
        "user": str(p.user_id) if p.user_id else None,
        "employee_name": p.user.get_full_name() if p.user else (p.employee_name or "Unmapped"),
        "employee_code": p.user.employee_id if p.user else None,
        "device": p.device.label,
        "device_user_id": p.employee_device_id,
        "punch": p.punch,
        "punch_label": p.punch_label,
        "timestamp": p.timestamp.isoformat(),
        "local_date": p.local_date.isoformat(),
        "is_mapped": p.user_id is not None,
    } for p in qs[:RECENT_PUNCH_LIMIT]]
