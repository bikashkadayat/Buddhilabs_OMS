"""The correction request API.

Role scoping is deliberately identical to ``AttendanceListView``: nobody can see
a correction for an employee whose attendance they could not already read.
"""
from django.http import FileResponse, Http404
from rest_framework import status as http_status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from audit.models import AuditLog
from audit.services import log_action
from config.uploads import harden_file_response
from users.models import User

from . import routing, services
from .models import AttendanceCorrectionRequest
from .serializers import AttendanceCorrectionSerializer, CorrectionActionSerializer

Status = AttendanceCorrectionRequest.Status


class AttendanceCorrectionViewSet(viewsets.ModelViewSet):
    """Employee -> department head -> HR.

    ``queue=manager`` and ``queue=hr`` return the two approval stages, already
    role-scoped, so the frontend needs no filtering logic of its own.
    """
    serializer_class = AttendanceCorrectionSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = (AttendanceCorrectionRequest.objects
              .select_related("employee", "employee__department_ref", "manager",
                              "hr_actor", "rejected_by")
              .order_by("-attendance_date", "-created_at"))
        qs = routing.scoped_corrections(qs, self.request.user)

        params = self.request.query_params
        queue = params.get("queue")
        if queue == "manager":
            qs = qs.filter(status=Status.PENDING)
        elif queue == "hr":
            qs = qs.filter(status=Status.MANAGER_APPROVED)
        elif queue == "open":
            qs = qs.filter(status__in=Status.values[:2])
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        if params.get("employee"):
            qs = qs.filter(employee_id=params["employee"])
        if params.get("mine"):
            qs = qs.filter(employee=self.request.user)
        if params.get("date_from"):
            qs = qs.filter(attendance_date__gte=params["date_from"])
        if params.get("date_to"):
            qs = qs.filter(attendance_date__lte=params["date_to"])
        return qs

    def perform_create(self, serializer):
        actor = self.request.user
        # Admin is an oversight role: it approves corrections rather than
        # raising them, exactly as it does for leave.
        if actor.role == User.Roles.ADMIN:
            raise PermissionDenied(
                "Admins review corrections; they do not raise them.")
        correction = serializer.save(employee=actor)
        services.submit(correction, actor=actor, request=self.request)

    def update(self, request, *args, **kwargs):
        correction = self.get_object()
        if correction.employee_id != request.user.id:
            raise PermissionDenied("You can only edit your own request.")
        if not correction.is_open:
            raise ValidationError(
                {"detail": "A decided request can no longer be edited."})
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        correction = self.get_object()
        if correction.employee_id != request.user.id:
            raise PermissionDenied("You can only delete your own request.")
        if correction.status == Status.HR_APPROVED:
            raise ValidationError(
                {"detail": "An applied correction cannot be deleted; revert it instead."})
        return super().destroy(request, *args, **kwargs)

    # -- workflow actions --------------------------------------------------
    def _action_payload(self, request):
        serializer = CorrectionActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def _run(self, fn, correction, **kwargs):
        try:
            fn(correction, **kwargs)
        except services.WorkflowError as exc:
            raise ValidationError({"detail": str(exc)})
        return Response(AttendanceCorrectionSerializer(
            correction, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Stage-aware: the department head at `pending`, HR at `manager_approved`."""
        correction = self.get_object()
        payload = self._action_payload(request)

        if correction.status == Status.PENDING:
            if not routing.can_act_as_manager(request.user, correction):
                raise PermissionDenied(
                    "Only this employee's department head (or HR) can approve "
                    "this stage, and never their own request.")
            return self._run(services.manager_approve, correction,
                             actor=request.user, remarks=payload["remarks"],
                             request=request)

        if correction.status == Status.MANAGER_APPROVED:
            if not routing.can_act_as_hr(request.user):
                raise PermissionDenied("Only HR or Admin can finalise a correction.")
            return self._run(services.hr_approve, correction,
                             actor=request.user, remarks=payload["remarks"],
                             request=request)

        raise ValidationError(
            {"detail": f"This request is already "
                       f"{correction.get_status_display().lower()}."})

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        correction = self.get_object()
        payload = self._action_payload(request)
        allowed = (routing.can_act_as_hr(request.user)
                   or (correction.status == Status.PENDING
                       and routing.can_act_as_manager(request.user, correction)))
        if not allowed:
            raise PermissionDenied("You cannot decide this request.")
        return self._run(services.reject, correction, actor=request.user,
                         reason=payload["reason"], request=request)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        correction = self.get_object()
        return self._run(services.cancel, correction, actor=request.user,
                         request=request)

    @action(detail=True, methods=["post"])
    def revert(self, request, pk=None):
        """Undo an applied correction from its snapshot. HR/Admin only."""
        correction = self.get_object()
        if not routing.can_act_as_hr(request.user):
            raise PermissionDenied("Only HR or Admin can revert an applied correction.")
        payload = self._action_payload(request)
        return self._run(services.revert, correction, actor=request.user,
                         reason=payload["reason"], request=request)

    @action(detail=True, methods=["get"])
    def attachment(self, request, pk=None):
        """Authenticated download. get_object() has already role-scoped it.

        Phase 11 (audit findings M2 + M3): the response now carries the same
        nosniff + sandbox CSP headers as every other file this system serves,
        and the download is audited. Reading someone else's evidence is a
        privacy-relevant act, and until now nothing recorded that it happened.
        """
        correction = self.get_object()
        if not correction.attachment:
            raise Http404("No attachment on this request.")

        log_action(request.user, AuditLog.Action.OTHER, instance=correction,
                   changes={"event": "CORRECTION_ATTACHMENT_DOWNLOADED",
                            "employee": str(correction.employee_id),
                            "attendance_date": correction.attendance_date.isoformat()},
                   request=request)

        response = FileResponse(
            correction.attachment.open("rb"), as_attachment=True,
            filename=correction.attachment.name.rsplit("/", 1)[-1])
        return harden_file_response(response)

    @action(detail=False, methods=["get"], url_path="queue-counts")
    def queue_counts(self, request):
        """What is waiting on this user, for a nav badge."""
        qs = routing.scoped_corrections(
            AttendanceCorrectionRequest.objects.all(), request.user)
        return Response({
            "manager_stage": qs.filter(status=Status.PENDING).count(),
            "hr_stage": (qs.filter(status=Status.MANAGER_APPROVED).count()
                         if routing.can_act_as_hr(request.user) else 0),
            "mine_open": qs.filter(employee=request.user,
                                   status__in=[Status.PENDING,
                                               Status.MANAGER_APPROVED]).count(),
        }, status=http_status.HTTP_200_OK)
