"""Policy administration and the WFH workflow.

Permissions reuse the existing ``attendance.views.IsHROrAdmin`` rather than
introducing a parallel rule — HR and Admin already own attendance.

Every mutation is written to the central ``audit.AuditLog`` through the existing
``log_action()`` helper. A bad policy can silently change everyone's status, so
"who changed what, when" has to be reconstructable.
"""
from django.db import transaction
from django.utils import timezone
from rest_framework import status as http_status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from audit.models import AuditLog
from audit.services import log_action
from users.models import User

from ..views import IsHROrAdmin
from .models import AttendancePolicy, EmployeeShift, PolicyAssignment, Shift, WFHRequest
from users.roles import has_org_wide_read as _org_wide_read
from .serializers import (
    AttendancePolicySerializer,
    EmployeeShiftSerializer,
    PolicyAssignmentSerializer,
    ShiftSerializer,
    WFHRequestSerializer,
    WFHReviewSerializer,
)


class _AuditedModelViewSet(viewsets.ModelViewSet):
    """ModelViewSet that records create/update/delete in the central audit log."""

    permission_classes = [IsAuthenticated, IsHROrAdmin]
    audit_event = "ATTENDANCE_POLICY"

    def perform_create(self, serializer):
        instance = serializer.save()
        log_action(self.request.user, AuditLog.Action.CREATE, instance=instance,
                   changes={"event": f"{self.audit_event}_CREATE", "name": str(instance)},
                   request=self.request)

    def perform_update(self, serializer):
        instance = serializer.save()
        log_action(self.request.user, AuditLog.Action.UPDATE, instance=instance,
                   changes={"event": f"{self.audit_event}_UPDATE", "name": str(instance),
                            "fields": sorted(serializer.validated_data.keys())},
                   request=self.request)

    def perform_destroy(self, instance):
        label = str(instance)
        log_action(self.request.user, AuditLog.Action.DELETE, instance=instance,
                   changes={"event": f"{self.audit_event}_DELETE", "name": label},
                   request=self.request)
        instance.delete()


class ShiftViewSet(_AuditedModelViewSet):
    """CRUD for shifts. ``crosses_midnight=True`` is rejected — see Shift.save()."""
    queryset = Shift.objects.all()
    serializer_class = ShiftSerializer
    audit_event = "ATTENDANCE_SHIFT"


class AttendancePolicyViewSet(_AuditedModelViewSet):
    queryset = AttendancePolicy.objects.select_related("default_shift").all()
    serializer_class = AttendancePolicySerializer
    audit_event = "ATTENDANCE_POLICY"


class PolicyAssignmentViewSet(_AuditedModelViewSet):
    queryset = PolicyAssignment.objects.select_related("policy", "department", "user").all()
    serializer_class = PolicyAssignmentSerializer
    audit_event = "ATTENDANCE_POLICY_ASSIGNMENT"

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
        log_action(self.request.user, AuditLog.Action.CREATE, instance=serializer.instance,
                   changes={"event": "ATTENDANCE_POLICY_ASSIGNMENT_CREATE",
                            "name": str(serializer.instance)},
                   request=self.request)

    def get_queryset(self):
        qs = super().get_queryset()
        scope = self.request.query_params.get("scope")
        if scope:
            qs = qs.filter(scope=scope)
        return qs


class EmployeeShiftViewSet(_AuditedModelViewSet):
    queryset = EmployeeShift.objects.select_related("shift", "user").all()
    serializer_class = EmployeeShiftSerializer
    audit_event = "ATTENDANCE_EMPLOYEE_SHIFT"

    def perform_create(self, serializer):
        serializer.save(assigned_by=self.request.user)
        log_action(self.request.user, AuditLog.Action.CREATE, instance=serializer.instance,
                   changes={"event": "ATTENDANCE_EMPLOYEE_SHIFT_CREATE",
                            "name": str(serializer.instance)},
                   request=self.request)

    def get_queryset(self):
        qs = super().get_queryset()
        user_id = self.request.query_params.get("user")
        return qs.filter(user_id=user_id) if user_id else qs


class WFHRequestViewSet(viewsets.ModelViewSet):
    """Employees raise requests; HR/Admin approve or reject.

    Approval alone is never attendance — the day only becomes WORK_FROM_HOME
    once the employee also browser-checks-in. See ``policy/engine.py``.
    """
    serializer_class = WFHRequestSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = WFHRequest.objects.select_related("user", "reviewed_by")
        role = self.request.user.role
        # Scoping mirrors AttendanceListView exactly: nobody sees more here than
        # they can already see there.
        if _org_wide_read(self.request.user):  # Board sees this read-only (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE).
            pass
        elif role == User.Roles.CHECKER:
            qs = qs.filter(user__department_ref_id=self.request.user.department_ref_id)
        else:
            qs = qs.filter(user=self.request.user)
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs

    def perform_create(self, serializer):
        instance = serializer.save(user=self.request.user)
        log_action(self.request.user, AuditLog.Action.CREATE, instance=instance,
                   changes={"event": "WFH_REQUEST", "from": str(instance.start_date),
                            "to": str(instance.end_date)},
                   request=self.request)

    def _require_reviewer(self):
        if self.request.user.role not in (User.Roles.APPROVER, User.Roles.ADMIN):
            raise PermissionDenied("Only HR or Admin can review WFH requests.")

    def _review(self, request, new_status, event):
        self._require_reviewer()
        wfh = self.get_object()
        if wfh.status != WFHRequest.Status.PENDING:
            raise ValidationError(
                {"detail": f"This request is already {wfh.get_status_display().lower()}."})
        note_ser = WFHReviewSerializer(data=request.data)
        note_ser.is_valid(raise_exception=True)

        wfh.status = new_status
        wfh.reviewed_by = request.user
        wfh.reviewed_at = timezone.now()
        wfh.review_note = note_ser.validated_data["note"]
        wfh.save(update_fields=["status", "reviewed_by", "reviewed_at",
                                "review_note", "updated_at"])
        log_action(request.user, AuditLog.Action.UPDATE, instance=wfh,
                   changes={"event": event, "user": str(wfh.user_id)}, request=request)
        _refresh_attendance_for(wfh)
        return Response(WFHRequestSerializer(wfh).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._review(request, WFHRequest.Status.APPROVED, "WFH_APPROVE")

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._review(request, WFHRequest.Status.REJECTED, "WFH_REJECT")

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """An employee withdraws their own request while it is still pending."""
        wfh = self.get_object()
        if wfh.user_id != request.user.id:
            raise PermissionDenied("You can only cancel your own request.")
        if wfh.status != WFHRequest.Status.PENDING:
            raise ValidationError({"detail": "Only a pending request can be cancelled."})
        wfh.status = WFHRequest.Status.CANCELLED
        wfh.save(update_fields=["status", "updated_at"])
        log_action(request.user, AuditLog.Action.UPDATE, instance=wfh,
                   changes={"event": "WFH_CANCEL"}, request=request)
        return Response(WFHRequestSerializer(wfh).data)

    def destroy(self, request, *args, **kwargs):
        wfh = self.get_object()
        if wfh.user_id != request.user.id and request.user.role not in (
                User.Roles.APPROVER, User.Roles.ADMIN):
            raise PermissionDenied("You can only delete your own request.")
        if wfh.status == WFHRequest.Status.APPROVED:
            raise ValidationError(
                {"detail": "An approved request cannot be deleted; cancel or reject it."})
        return super().destroy(request, *args, **kwargs)


def _refresh_attendance_for(wfh):
    """Re-evaluate any attendance rows the review just made (in)valid as WFH.

    Approving after the employee has already checked in must flip the day to
    WORK_FROM_HOME, and rejecting must flip it back. Only rows that already
    exist are touched — a review never creates attendance.
    """
    from ..models import Attendance
    from ..services import recompute_status
    from .resolver import policy_cache

    records = list(Attendance.objects.filter(
        employee_id=wfh.user_id, date__gte=wfh.start_date, date__lte=wfh.end_date,
    ).exclude(marked_by=Attendance.MarkedBy.HR))
    if not records:
        return
    with policy_cache(), transaction.atomic():
        for record in records:
            recompute_status(record)
            record.save()


class ResolvedPolicyView(viewsets.ViewSet):
    """GET /policy/resolved/?user=<uuid>&date=<iso> — which rules apply, and why.

    The answer to "why is this person marked late?" without reading the code.
    """
    permission_classes = [IsAuthenticated, IsHROrAdmin]

    def list(self, request):
        from .resolver import resolve_binding, resolve_shift, start_time_and_grace

        user_id = request.query_params.get("user") or str(request.user.id)
        try:
            user = User.objects.get(pk=user_id)
        except (User.DoesNotExist, ValueError, TypeError):
            return Response({"detail": "User not found."},
                            status=http_status.HTTP_404_NOT_FOUND)
        raw_date = request.query_params.get("date")
        day = timezone.localdate()
        if raw_date:
            from datetime import date as _date
            try:
                day = _date.fromisoformat(raw_date)
            except ValueError:
                raise ValidationError({"date": "Expected YYYY-MM-DD."})

        binding = resolve_binding(user, day)
        shift = resolve_shift(user, day, binding.policy)
        start, grace = start_time_and_grace(binding.policy, shift)
        return Response({
            "user": str(user.id),
            "date": day.isoformat(),
            "policy": {"name": binding.policy.name, "id": None}
            if binding.policy.is_fallback else AttendancePolicySerializer(binding.policy).data,
            "policy_source": "settings-fallback" if binding.policy.is_fallback else "assignment",
            "policy_effective_from": binding.effective_from,
            "shift": ShiftSerializer(shift).data if shift else None,
            "effective_start_time": start.strftime("%H:%M"),
            "effective_grace_minutes": grace,
        })
