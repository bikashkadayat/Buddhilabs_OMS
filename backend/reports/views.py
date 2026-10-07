"""Reports API: request (async), poll status, download, schedule, analytics."""
import threading

from django.conf import settings
from django.http import FileResponse, Http404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditLog
from audit.services import log_action
from config.uploads import harden_file_response
from users.admin_views import IsAdminOrSuperuser
from . import report_service
from .analytics import dashboard_analytics
from .models import ReportRun, ScheduledReport
from .permissions import CanUseReports, IsReportAdmin, allowed_report_types, is_admin, is_hr
from .serializers import (
    ReportRequestSerializer, ReportRunSerializer, ScheduledReportSerializer,
)


def _spawn_generation(run_id, organization_id):
    """Generate a report in a background thread (its own DB connection).

    THE THREAD BINDS ITS OWN TENANT (Phase S6), and it has to.

    `app.current_org` is CONNECTION state, and a thread gets its own
    connection. The request that spawned this one bound the tenant on the
    request's connection; this thread inherits nothing. Under row-level
    security an unbound connection sees no tenant rows at all, so
    `ReportRun.objects.get(pk=run_id)` would raise DoesNotExist for a row that
    plainly exists -- in a daemon thread, where the only trace is a log line
    nobody reads, and the report would simply never appear.

    The organization id is passed in rather than read here for the same
    reason: reading it would need the row this thread cannot yet see.
    """
    from django.db import connection

    from tenancy.context import tenant_context

    def _work():
        try:
            with tenant_context(organization_id):
                run = ReportRun.objects.get(pk=run_id)
                report_service.generate_report(run)
        finally:
            connection.close()

    threading.Thread(target=_work, daemon=True).start()


class ReportsViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [CanUseReports]
    serializer_class = ReportRunSerializer

    def get_queryset(self):
        qs = ReportRun.objects.select_related("requested_by").all()
        # A department head sees only what they requested. Someone else's run may
        # cover employees outside their department, and the file is downloadable.
        if not (is_admin(self.request.user) or is_hr(self.request.user)):
            qs = qs.filter(requested_by=self.request.user)
        # Admins see all; the hub shows "my" runs via ?mine=1.
        if self.request.query_params.get("mine"):
            qs = qs.filter(requested_by=self.request.user)
        return qs

    def _scoped_params(self, params):
        """Pin a department head's report to the employees they may see.

        Injected server-side rather than trusted from the request body, so a
        manager cannot widen their own scope by editing the payload.
        """
        from attendance.workforce.aggregates import scoped_employees

        user = self.request.user
        if is_admin(user) or is_hr(user):
            return params
        params = dict(params)
        params["employee_ids"] = [str(pk) for pk in
                                  scoped_employees(user).values_list("pk", flat=True)]
        return params

    @action(detail=False, methods=["post"], url_path="request")
    def request_report(self, request):
        serializer = ReportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        report_type = serializer.validated_data["report_type"]

        permitted = allowed_report_types(request.user)
        if permitted is not None and report_type not in permitted:
            return Response(
                {"detail": "You are not permitted to run this report type."},
                status=status.HTTP_403_FORBIDDEN)

        run = ReportRun.objects.create(
            report_type=report_type,
            params=self._scoped_params(serializer.validated_data.get("params") or {}),
            requested_by=request.user,
            status=ReportRun.Status.PENDING,
        )
        log_action(request.user, AuditLog.Action.CREATE, instance=run, request=request)

        if getattr(settings, "REPORTS_RUN_SYNC", False):
            report_service.generate_report(run)
            run.refresh_from_db()
        else:
            _spawn_generation(run.id, run.organization_id)

        return Response(ReportRunSerializer(run).data, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["get"], url_path="status")
    def report_status(self, request, pk=None):
        run = self.get_object()
        from documents.protected_media import signed_media_url
        file_url = signed_media_url(run.file.name, ttl=600, download=True) if run.file else None
        return Response({"id": str(run.id), "status": run.status, "error": run.error, "file_url": file_url})

    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        """Phase 11 (audit finding M3): the download is now audited.

        The *request* was already logged, but for exports containing
        payroll-relevant data, who actually took the file matters more than who
        asked for it — a run can be requested once and downloaded by anyone
        with access to it, repeatedly, and none of that was recorded.
        """
        run = self.get_object()
        if run.status != ReportRun.Status.READY or not run.file:
            return Response({"detail": f"Report is not ready (status: {run.status})."},
                            status=status.HTTP_409_CONFLICT)
        try:
            handle = run.file.open("rb")
        except FileNotFoundError:
            raise Http404("Report file is missing.")

        log_action(request.user, AuditLog.Action.OTHER, instance=run,
                   changes={"event": "REPORT_DOWNLOADED",
                            "report_type": run.report_type,
                            "requested_by": str(run.requested_by_id) if run.requested_by_id else None},
                   request=request)

        response = FileResponse(handle, as_attachment=True,
                                filename=run.file.name.split("/")[-1])
        return harden_file_response(response)


class ScheduledReportViewSet(viewsets.ModelViewSet):
    # Scheduled reports are org-wide and email their output, so they stay with
    # Admin and HR — a department head cannot schedule one.
    permission_classes = [IsReportAdmin]
    serializer_class = ScheduledReportSerializer
    queryset = ScheduledReport.objects.all()

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        log_action(self.request.user, AuditLog.Action.CREATE, instance=instance, request=self.request)

    def perform_update(self, serializer):
        instance = serializer.save()
        log_action(self.request.user, AuditLog.Action.UPDATE, instance=instance, request=self.request)

    def perform_destroy(self, instance):
        log_action(self.request.user, AuditLog.Action.DELETE, instance=instance, request=self.request)
        instance.delete()


class AnalyticsView(APIView):
    """GET /api/v1/reports/analytics/?year= - admin dashboard data."""
    permission_classes = [IsAdminOrSuperuser]

    def get(self, request):
        year = request.query_params.get("year")
        return Response(dashboard_analytics(int(year) if year else None))
