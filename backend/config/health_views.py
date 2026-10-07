"""Health-check endpoints for Docker HEALTHCHECK and monitoring."""
import shutil

from django.conf import settings
from django.db import connection
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditLog
from users.admin_views import IsAdminOrSuperuser

CRON_EVENTS = [
    "SYSTEM_RECOMPUTE_BALANCES",
    "SYSTEM_RECOMPUTE_SUMMARIES",
    "SYSTEM_YEAR_END",
    "SYSTEM_INTEGRITY_AUDIT",
]


def _db_up():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return True
    except Exception:
        return False


class HealthView(APIView):
    """GET /api/v1/health/ - public liveness probe with DB status."""
    permission_classes = [AllowAny]

    # No throttling: DRF's throttles touch the cache on every request, and a
    # liveness probe must depend on as little as possible. With the cache on
    # Redis, throttling here would let a Redis outage fail the container
    # healthcheck and the deploy gate for an API that is otherwise fine.
    throttle_classes = []

    def get(self, request):
        db_ok = _db_up()
        body = {"status": "ok" if db_ok else "degraded", "database": "up" if db_ok else "down"}
        code = status.HTTP_200_OK if db_ok else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(body, status=code)


def _redis_status():
    """Round-trip the cache. 'not_configured' is a valid state, not a failure —
    without REDIS_URL the app runs on in-memory backends by design."""
    if not getattr(settings, "REDIS_URL", ""):
        return {"configured": False, "status": "not_configured"}
    try:
        from django.core.cache import cache

        cache.set("health:ping", "1", 5)
        ok = cache.get("health:ping") == "1"
    except Exception as exc:
        return {"configured": True, "status": "down", "error": str(exc)[:200]}
    return {"configured": True, "status": "up" if ok else "degraded"}


def _unprocessed_punches():
    """Punches waiting on derivation, split by cause.

    'unmapped' is blocked on an HR decision, not on the system — separating the
    two stops a queue of unmapped punches from looking like a stuck worker.
    """
    try:
        from biometric.models import AttendancePunch

        pending = AttendancePunch.objects.filter(is_processed=False)
        return {
            "awaiting_derivation": pending.filter(user__isnull=False).count(),
            "awaiting_mapping": pending.filter(user__isnull=True).count(),
        }
    except Exception:
        return {"awaiting_derivation": None, "awaiting_mapping": None}


class DetailedHealthView(APIView):
    """GET /api/v1/health/detailed/ - admin-only deep status."""
    permission_classes = [IsAdminOrSuperuser]

    def get(self, request):
        from leaves.models import LeaveDayRecord

        usage = shutil.disk_usage(str(settings.BASE_DIR))
        last_runs = {}
        for event in CRON_EVENTS:
            entry = AuditLog.objects.filter(changes__event=event).order_by("-created_at").first()
            last_runs[event] = entry.created_at if entry else None

        return Response({
            "status": "ok" if _db_up() else "degraded",
            "database": "up" if _db_up() else "down",
            # Deliberately NOT part of the liveness probe at /health/: realtime
            # is an enhancement, so a Redis blip must never fail the deploy gate
            # or restart a container that is serving the API perfectly well.
            "redis": _redis_status(),
            "queues": {
                "pending_leave_days": LeaveDayRecord.objects.filter(
                    status=LeaveDayRecord.Status.PENDING
                ).count(),
                "unprocessed_punches": _unprocessed_punches(),
            },
            "disk": {"total": usage.total, "used": usage.used, "free": usage.free},
            "last_cron_runs": last_runs,
        })
