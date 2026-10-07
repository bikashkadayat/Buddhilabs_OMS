"""Punch and roster ingestion.

Idempotent by construction: the collector re-sends a device's whole backlog on
every reconnect by design, so ingest must be able to absorb the same batch any
number of times and create nothing the second time round.

Dedup is enforced twice on purpose — once here by pre-filtering against what is
already stored (which gives exact created/duplicate counts for the sync log),
and once by the ``uniq_biometric_punch`` constraint via ``ignore_conflicts``
(which is what actually holds under concurrent requests).
"""
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import events
from .derivation import derive_daily_attendance
from .models import AttendancePunch, BiometricEmployee, DeviceSyncLog, punch_label
from .services import finish_sync_log, start_sync_log, touch_device
from tenancy.stamping import stamp_all

logger = logging.getLogger(__name__)


def _active_mappings(device):
    return {
        m.device_user_id: m
        for m in BiometricEmployee.objects.filter(
            device=device, is_active=True).select_related("user")
    }


def _existing_keys(device, timestamps):
    """Dedup keys already stored, bounded to this batch's time window.

    Scoped by min/max timestamp so the lookup stays index-friendly instead of
    scanning every punch the device has ever sent.
    """
    if not timestamps:
        return set()
    return set(
        AttendancePunch.objects.filter(
            device=device, timestamp__gte=min(timestamps), timestamp__lte=max(timestamps),
        ).values_list("employee_device_id", "timestamp", "punch")
    )


@transaction.atomic
def ingest_punches(device, punches, *, source, sync_type, client_ip=None,
                   queue_depth=0, derive=True):
    """Store a batch of punches and derive the attendance they imply.

    ``punches`` is a list of validated dicts: employee_id, timestamp (aware),
    punch, status, name. Returns a summary dict mirroring DeviceSyncLog.
    """
    log = start_sync_log(device, sync_type, client_ip=client_ip)
    log.queue_depth = queue_depth

    mappings = _active_mappings(device)
    existing = _existing_keys(device, [p["timestamp"] for p in punches])

    to_create, seen_in_batch = [], set()
    duplicates = unmapped = 0

    for p in punches:
        device_user_id = p["employee_id"]
        key = (device_user_id, p["timestamp"], p["punch"])
        # A single payload can legitimately repeat a punch (device replay); count
        # it once rather than letting bulk_create silently swallow it.
        if key in existing or key in seen_in_batch:
            duplicates += 1
            continue
        seen_in_batch.add(key)

        mapping = mappings.get(device_user_id)
        user = mapping.user if mapping else None
        if user is None:
            # Never dropped — an unmapped punch is recorded and surfaces in the
            # HR queue, where mapping it later backfills the attendance.
            unmapped += 1

        to_create.append(AttendancePunch(
            device=device,
            biometric_employee=mapping,
            employee_device_id=device_user_id,
            user=user,
            employee_name=p.get("name", "") or (mapping.device_name if mapping else ""),
            timestamp=p["timestamp"],
            local_date=timezone.localtime(p["timestamp"]).date(),
            punch=p["punch"],
            punch_label=punch_label(p["punch"]),
            verify_status=p.get("status", 0) or 0,
            source=source,
        ))

    if to_create:
        # ignore_conflicts covers the race where two requests carry the same
        # punch; the pre-filter above is what makes the counts exact.
        AttendancePunch.objects.bulk_create(stamp_all(to_create), batch_size=500, ignore_conflicts=True)
        _broadcast_punches(to_create)

    created = len(to_create)
    newest = max((p["timestamp"] for p in punches), default=None)

    if derive:
        _derive_affected(to_create)

    _update_device_health(device, newest=newest, queue_depth=queue_depth, succeeded=True)

    finish_sync_log(log, received=len(punches), created=created,
                    duplicate=duplicates, unmapped=unmapped)
    log.queue_depth = queue_depth
    log.save(update_fields=["queue_depth"])

    summary = {
        "received": len(punches), "created": created, "duplicate": duplicates,
        "unmapped": unmapped, "invalid": 0, "sync_log": str(log.pk),
        "last_punch_at": newest.isoformat() if newest else None,
    }
    logger.info("Ingest %s from %s: %s", sync_type, device.label, summary)
    return summary


def _broadcast_punches(punches):
    """Announce new punches to the live dashboard.

    Capped: a first-sync backlog is ~11k punches, and replaying all of history
    into every open browser would be a self-inflicted denial of service for zero
    benefit — nobody watches a live feed to see what happened in May. Live
    batches are single punches, so the cap only ever trims a backfill.
    """
    limit = getattr(settings, "BIOMETRIC_BROADCAST_LIMIT", 50)
    if len(punches) > limit:
        logger.info("Skipping punch broadcast for a %d-punch batch (limit %d)",
                    len(punches), limit)
        return
    for punch in punches:
        events.punch_recorded(punch)


def _derive_affected(punches):
    """Rebuild the attendance days this batch touched.

    Inline rather than queued: a punch that has landed but produced no
    attendance row looks to HR exactly like a punch that never arrived. The
    cron sweep behind this is a safety net, not the primary path.
    """
    pairs = {(p.user_id, p.local_date) for p in punches if p.user_id}
    if not pairs:
        return
    from django.contrib.auth import get_user_model
    users = get_user_model().objects.in_bulk({uid for uid, _ in pairs})
    for user_id, day in sorted(pairs, key=lambda x: (str(x[0]), x[1])):
        user = users.get(user_id)
        if user is not None:
            derive_daily_attendance(user, day)


def _update_device_health(device, *, newest=None, queue_depth=0, succeeded=True):
    fields = ["last_seen_at", "connection_status", "pending_punches", "updated_at"]
    device.pending_punches = queue_depth
    if succeeded:
        device.successful_batches += 1
        device.last_sync_at = timezone.now()
        fields += ["successful_batches", "last_sync_at"]
    else:
        device.failed_batches += 1
        fields.append("failed_batches")
    if newest and (device.last_punch_at is None or newest > device.last_punch_at):
        device.last_punch_at = newest
        fields.append("last_punch_at")
    touch_device(device, seen=True)
    device.save(update_fields=[f for f in fields if f not in ("last_seen_at", "connection_status")])
    return device


def record_failed_batch(device, sync_type, error, *, client_ip=None, received=0, queue_depth=0):
    """Log a batch the server could not accept, so failures are visible too."""
    log = start_sync_log(device, sync_type, client_ip=client_ip)
    log.queue_depth = queue_depth
    log.save(update_fields=["queue_depth"])
    _update_device_health(device, queue_depth=queue_depth, succeeded=False)
    return finish_sync_log(log, received=received, error=error)


@transaction.atomic
def ingest_roster(device, employees, *, client_ip=None):
    """Upsert the device's enrolled-user roster.

    Deliberately never assigns ``user``: linking a device ID to an employee is
    an HR decision (Phase 4), and auto-mapping on a name match is exactly how
    one person's attendance ends up filed under another's.
    """
    log = start_sync_log(device, DeviceSyncLog.SyncType.ROSTER, client_ip=client_ip)
    existing = {
        m.device_user_id: m
        for m in BiometricEmployee.objects.filter(device=device, is_active=True)
    }

    created = updated = 0
    now = timezone.now()
    for emp in employees:
        device_user_id = emp["employee_id"]
        mapping = existing.get(device_user_id)
        if mapping is None:
            BiometricEmployee.objects.create(
                device=device, device_user_id=device_user_id,
                device_name=emp.get("name", ""), privilege=emp.get("privilege", 0) or 0,
                card=emp.get("card", 0) or 0, group_id=emp.get("group_id", "") or "",
                last_synced_at=now,
            )
            created += 1
            continue

        changed = (
            mapping.device_name != emp.get("name", "")
            or mapping.privilege != (emp.get("privilege", 0) or 0)
            or mapping.card != (emp.get("card", 0) or 0)
            or mapping.group_id != (emp.get("group_id", "") or "")
        )
        mapping.device_name = emp.get("name", "")
        mapping.privilege = emp.get("privilege", 0) or 0
        mapping.card = emp.get("card", 0) or 0
        mapping.group_id = emp.get("group_id", "") or ""
        mapping.last_synced_at = now
        mapping.save(update_fields=["device_name", "privilege", "card", "group_id",
                                    "last_synced_at", "updated_at"])
        updated += 1 if changed else 0

    touch_device(device, seen=True, synced=True)
    finish_sync_log(log, received=len(employees), created=created, duplicate=len(employees) - created)

    unmapped_total = BiometricEmployee.objects.filter(
        device=device, is_active=True, user__isnull=True).count()
    summary = {
        "received": len(employees), "created": created, "updated": updated,
        "unmapped_total": unmapped_total, "sync_log": str(log.pk),
    }
    logger.info("Roster sync from %s: %s", device.label, summary)
    return summary
