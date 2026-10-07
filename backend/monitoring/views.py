"""The monitoring API.

Separate from ``config.health_views``, which stays exactly as it is: ``/health/``
is the container liveness probe and must depend on as little as possible, while
this endpoint reads Redis, the audit log, device state and the backup
heartbeats. Merging them would let a slow monitoring query fail a healthcheck
and restart a container that was serving traffic perfectly well.
"""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from users.models import User

from . import alerts, heartbeat, metrics


class IsOperator(IsAuthenticated):
    """HR and Admin. Device health, backup state and login-failure counts are
    infrastructure, and a department head has no action to take on any of it."""

    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.role in (
            User.Roles.APPROVER, User.Roles.ADMIN)


class SystemHealthView(APIView):
    """GET /api/v1/monitoring/health/ — the full board."""
    permission_classes = [IsOperator]
    # This endpoint is polled by a dashboard every 30s and each call runs a
    # dozen probes; the default user throttle would let one open tab consume a
    # meaningful share of a user's budget.
    throttle_classes = []

    def get(self, request):
        return Response(metrics.collect())


class CronStatusView(APIView):
    """GET /api/v1/monitoring/cron/ — every registered job and how late it is."""
    permission_classes = [IsOperator]

    def get(self, request):
        return Response({
            "jobs": heartbeat.all_statuses(),
            "registered": len(heartbeat.CRON_JOBS),
        })


class AlertStatusView(APIView):
    """GET /api/v1/monitoring/alerts/ — currently firing rules and recent history."""
    permission_classes = [IsOperator]

    def get(self, request):
        return Response(alerts.current_state())
