"""Biometric device analytics -- infrastructure health, HR and Admin only.

Deliberately NOT scoped by employee: a terminal at the main gate belongs to the
organisation, not to a department, and a department head has nothing to do with
its firmware. The view gates this with ``IsOrgAnalytics``.

The metric that matters most here is mapping progress. An unmapped device user
produces punches that never become attendance, so every other number on every
other dashboard is quietly short by that person's days until someone maps them.
"""
from collections import defaultdict

from django.db.models import Count, Q, Sum

from biometric.models import AttendancePunch, BiometricDevice, BiometricEmployee, DeviceSyncLog

from .. import periods
from .base import bucket_expr, pct


def fleet():
    """Per-device state plus the fleet rollup. TWO queries."""
    devices = list(BiometricDevice.objects.filter(is_active=True).annotate(
        unmapped_count=Count("employees", filter=Q(employees__user__isnull=True,
                                                   employees__is_active=True)),
        mapped_count=Count("employees", filter=Q(employees__user__isnull=False,
                                                 employees__is_active=True)),
    ).order_by("name"))

    rollup = BiometricDevice.objects.filter(is_active=True).aggregate(
        total=Count("id"),
        online=Count("id", filter=Q(connection_status=BiometricDevice.Status.ONLINE)),
        offline=Count("id", filter=Q(connection_status=BiometricDevice.Status.OFFLINE)),
        unknown=Count("id", filter=Q(connection_status=BiometricDevice.Status.UNKNOWN)),
        pending_punches=Sum("pending_punches"),
        successful_batches=Sum("successful_batches"),
        failed_batches=Sum("failed_batches"))

    return {
        "summary": {
            "total": rollup["total"] or 0,
            "online": rollup["online"] or 0,
            "offline": (rollup["offline"] or 0) + (rollup["unknown"] or 0),
            "unknown": rollup["unknown"] or 0,
            "pending_punches": rollup["pending_punches"] or 0,
            "successful_batches": rollup["successful_batches"] or 0,
            "failed_batches": rollup["failed_batches"] or 0,
        },
        "devices": [{
            "id": str(device.pk),
            "name": device.name,
            "label": device.label,
            "location": device.location,
            "connection_status": device.connection_status,
            "last_seen_at": _iso(device.last_seen_at),
            "last_sync_at": _iso(device.last_sync_at),
            "last_punch_at": _iso(device.last_punch_at),
            "clock_drift_seconds": device.clock_drift_seconds,
            "pending_punches": device.pending_punches,
            "successful_batches": device.successful_batches,
            "failed_batches": device.failed_batches,
            "mapped_count": device.mapped_count,
            "unmapped_count": device.unmapped_count,
            "mapping_pct": pct(device.mapped_count,
                               device.mapped_count + device.unmapped_count),
        } for device in devices],
    }


def sync_health(window):
    """Ingest quality over the window. ONE query.

    ``partial`` batches count as neither success nor failure in the rate: they
    delivered some records, and folding them either way would misrepresent the
    fleet in whichever direction was chosen.
    """
    logs = DeviceSyncLog.objects.filter(
        started_at__date__gte=window.start, started_at__date__lte=window.end)
    stats = logs.aggregate(
        total=Count("id"),
        success=Count("id", filter=Q(status=DeviceSyncLog.Status.SUCCESS)),
        partial=Count("id", filter=Q(status=DeviceSyncLog.Status.PARTIAL)),
        failed=Count("id", filter=Q(status=DeviceSyncLog.Status.FAILED)),
        received=Sum("records_received"), created=Sum("records_created"),
        duplicate=Sum("records_duplicate"), unmapped=Sum("records_unmapped"),
        invalid=Sum("records_invalid"))

    total = stats["total"] or 0
    received = stats["received"] or 0
    return {
        "batches": total,
        "success": stats["success"] or 0,
        "partial": stats["partial"] or 0,
        "failed": stats["failed"] or 0,
        "success_rate_pct": pct(stats["success"] or 0, total),
        "failure_rate_pct": pct(stats["failed"] or 0, total),
        "records_received": received,
        "records_created": stats["created"] or 0,
        "records_duplicate": stats["duplicate"] or 0,
        "records_unmapped": stats["unmapped"] or 0,
        "records_invalid": stats["invalid"] or 0,
        "ingest_yield_pct": pct(stats["created"] or 0, received),
    }


def sync_trend(window):
    """Success rate per bucket. ONE query."""
    rows = (DeviceSyncLog.objects
            .filter(started_at__date__gte=window.start, started_at__date__lte=window.end)
            .annotate(bucket=bucket_expr(window.granularity, "started_at__date"))
            .values("bucket")
            .annotate(total=Count("id"),
                      success=Count("id", filter=Q(status=DeviceSyncLog.Status.SUCCESS)),
                      failed=Count("id", filter=Q(status=DeviceSyncLog.Status.FAILED)),
                      received=Sum("records_received")))
    collected = defaultdict(lambda: {"total": 0, "success": 0, "failed": 0, "received": 0})
    for row in rows:
        key = _key(row["bucket"], window)
        bucket = collected[key]
        bucket["total"] += row["total"]
        bucket["success"] += row["success"]
        bucket["failed"] += row["failed"]
        bucket["received"] += row["received"] or 0

    return [{
        "period": key,
        "label": periods.bucket_label(key, window.granularity),
        "batches": collected[key]["total"],
        "failed": collected[key]["failed"],
        "records_received": collected[key]["received"],
        "success_rate_pct": pct(collected[key]["success"], collected[key]["total"]),
    } for key in periods.buckets(window)]


def mapping_progress():
    """Both directions of the mapping gap. TWO queries.

    Device users with no account is the gap the HR command centre already shows.
    The reverse -- an active employee with no device enrolment at all -- is not
    reported anywhere today, and it is the reason a person can be silently
    absent from every biometric-derived figure.
    """
    from users.models import User

    enrolment = BiometricEmployee.objects.filter(is_active=True).aggregate(
        total=Count("id"),
        mapped=Count("id", filter=Q(user__isnull=False)),
        unmapped=Count("id", filter=Q(user__isnull=True)))

    employees_without_device = User.objects.filter(is_active=True).exclude(
        biometric_identities__is_active=True).count()

    total = enrolment["total"] or 0
    return {
        "device_users": total,
        "mapped": enrolment["mapped"] or 0,
        "unmapped": enrolment["unmapped"] or 0,
        "mapping_pct": pct(enrolment["mapped"] or 0, total),
        "employees_without_device": employees_without_device,
    }


def punch_backlog():
    """Punches ingested but not yet turned into attendance. ONE query."""
    return AttendancePunch.objects.filter(is_processed=False).count()


def _key(bucket, window):
    if bucket is None:
        return None
    return periods.bucket_key(
        bucket.date() if hasattr(bucket, "date") else bucket, window.granularity)


def _iso(value):
    return value.isoformat() if value else None


def dashboard(window):
    return {
        **fleet(),
        "sync": sync_health(window),
        "sync_trend": sync_trend(window),
        "mapping": mapping_progress(),
        "unprocessed_punches": punch_backlog(),
    }
