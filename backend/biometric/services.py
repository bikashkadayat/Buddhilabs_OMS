"""Biometric service layer.

Covers device credential lifecycle, sync-log bookkeeping, and the employee
mapping / backfill workflow. Punch ingestion and daily-attendance derivation
land in Phase 5.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Max, Min
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from audit.models import AuditLog
from audit.services import log_action

from .models import (
    API_KEY_PREFIX_LENGTH,
    AttendancePunch,
    BiometricDevice,
    BiometricEmployee,
    DeviceSyncLog,
)

logger = logging.getLogger(__name__)


def device_id_sort_key(value):
    """Order device user IDs numerically, falling back to text.

    ``device_user_id`` is a CharField because a terminal may enrol ``NIF007`` as
    readily as ``7``. Sorting the column therefore gives 1, 10, 2 — which reads
    as a mistake in every report an operator checks a roster against, and makes
    a genuinely missing ID much harder to spot. Numeric IDs sort ahead of
    non-numeric ones so the common case is the tidy one.
    """
    try:
        return (0, int(value), "")
    except (TypeError, ValueError):
        return (1, 0, str(value))


@transaction.atomic
def rotate_device_api_key(device, actor=None):
    """Issue a new key for a device, invalidating the previous one immediately.

    Returns the raw key. This is the only moment it exists in plaintext — it is
    stored hashed, so a lost key can only be replaced, never recovered.
    """
    raw_key = device.set_api_key()
    device.save(update_fields=["api_key_prefix", "api_key_hash", "api_key_set_at", "updated_at"])
    logger.info(
        "Biometric device key rotated: device=%s label=%s actor=%s",
        device.pk, device.label, getattr(actor, "pk", None),
    )
    return raw_key


def resolve_device_by_key(raw_key):
    """Find the active device a presented key belongs to, or None.

    Two steps on purpose: an indexed lookup on the key prefix narrows to (almost
    always) a single row, then a constant-time hash compare decides. The prefix
    is not a secret — it only avoids a full-table scan per request.
    """
    if not raw_key:
        return None
    prefix = raw_key[:API_KEY_PREFIX_LENGTH]
    candidates = BiometricDevice.objects.filter(
        api_key_prefix=prefix, is_active=True,
    ).exclude(api_key_hash="")
    for device in candidates:
        if device.check_api_key(raw_key):
            return device
    logger.warning("Biometric auth failed for key prefix=%s", prefix)
    return None


def set_device_status(device, online, *, immediate=False):
    """Move a device between online/offline, broadcasting only on a real change.

    Announcing on every ingest would emit a device.online event per batch — 24
    of them during a first backlog sync — so the transition, not the state, is
    what gets published.
    """
    from . import events

    target = BiometricDevice.Status.ONLINE if online else BiometricDevice.Status.OFFLINE
    if device.connection_status == target:
        return False
    device.connection_status = target
    device.save(update_fields=["connection_status", "updated_at"])
    events.device_status_changed(device, online, immediate=immediate)
    logger.info("Device %s is now %s", device.label, target)
    return True


def touch_device(device, *, seen=True, synced=False, drift_seconds=None):
    """Record device liveness without a full model save."""
    fields = []
    now = timezone.now()
    was_offline = device.connection_status != BiometricDevice.Status.ONLINE
    if seen:
        device.last_seen_at = now
        device.connection_status = BiometricDevice.Status.ONLINE
        fields += ["last_seen_at", "connection_status"]
    if synced:
        device.last_sync_at = now
        fields.append("last_sync_at")
    if drift_seconds is not None:
        device.clock_drift_seconds = int(drift_seconds)
        fields.append("clock_drift_seconds")
    if fields:
        device.save(update_fields=fields + ["updated_at"])
    if seen and was_offline:
        from . import events
        events.device_status_changed(device, online=True)
    return device


def start_sync_log(device, sync_type, client_ip=None):
    return DeviceSyncLog.objects.create(
        device=device, sync_type=sync_type,
        status=DeviceSyncLog.Status.STARTED, client_ip=client_ip,
    )


def finish_sync_log(log, *, received=0, created=0, duplicate=0, unmapped=0, invalid=0, error=""):
    """Close a sync batch and classify the outcome.

    'partial' is its own state rather than a success: a batch that silently
    dropped unmapped or invalid records is exactly the case an operator needs
    surfaced, and folding it into 'success' is how attendance data goes quietly
    missing.
    """
    log.records_received = received
    log.records_created = created
    log.records_duplicate = duplicate
    log.records_unmapped = unmapped
    log.records_invalid = invalid
    log.error = error or ""
    log.finished_at = timezone.now()
    if error:
        log.status = DeviceSyncLog.Status.FAILED
    elif unmapped or invalid:
        log.status = DeviceSyncLog.Status.PARTIAL
    else:
        log.status = DeviceSyncLog.Status.SUCCESS
    log.save(update_fields=[
        "records_received", "records_created", "records_duplicate", "records_unmapped",
        "records_invalid", "error", "finished_at", "status",
    ])
    return log


# ===========================================================================
# Employee mapping
# ===========================================================================

def audit_mapping_change(actor, action, mapping, event, request=None, **extra):
    """Every mapping change goes to the central AuditLog — no parallel table."""
    changes = {
        "event": event,
        "device": mapping.device.label,
        "device_user_id": mapping.device_user_id,
        "user": str(mapping.user_id) if mapping.user_id else None,
        "user_name": mapping.user.get_full_name() if mapping.user else None,
    }
    changes.update(extra)
    return log_action(actor, action, instance=mapping, changes=changes, request=request)


def _assert_user_free_on_device(device, user, exclude_pk=None):
    """One employee may hold only one ACTIVE identity per device.

    The DB enforces this too, but catching it here turns an IntegrityError 500
    into a 400 that names the conflicting device ID.
    """
    clash = BiometricEmployee.objects.filter(
        device=device, user=user, is_active=True,
    ).exclude(pk=exclude_pk).first()
    if clash:
        raise ValidationError(
            f"{user.get_full_name()} is already mapped to device ID "
            f"{clash.device_user_id} on {device.name}. Unmap that first."
        )


@transaction.atomic
def map_employee(mapping, user, actor=None, effective_from=None, request=None):
    """Attach an OMS user to a device roster entry.

    Does NOT backfill historical punches — that is a separate, explicitly
    confirmed step (see ``backfill_punches``). Mapping and backfilling are kept
    apart on purpose: creating a mapping is routine, while re-attributing
    history is the operation that can silently corrupt someone's record.
    """
    if mapping.user_id and mapping.user_id != user.pk:
        raise ValidationError(
            "This device ID is already mapped. Use remap to reassign it, so the "
            "previous mapping is retired with its own validity window."
        )
    _assert_user_free_on_device(mapping.device, user, exclude_pk=mapping.pk)

    mapping.user = user
    mapping.is_active = True
    mapping.mapped_at = timezone.now()
    mapping.mapped_by = actor if getattr(actor, "is_authenticated", False) else None
    if effective_from is not None:
        mapping.effective_from = effective_from
    mapping.save(update_fields=[
        "user", "is_active", "mapped_at", "mapped_by", "effective_from", "updated_at",
    ])
    audit_mapping_change(
        actor, AuditLog.Action.UPDATE, mapping, "BIOMETRIC_MAPPING_CREATED", request,
        effective_from=str(mapping.effective_from) if mapping.effective_from else None)
    logger.info("Biometric mapping created: %s", mapping)
    return mapping


@transaction.atomic
def unmap_employee(mapping, actor=None, detach_punches=True, request=None):
    """Soft unmap. The row is retired, never deleted.

    Physically deleting would destroy the record that this device ID once
    belonged to this person, which is exactly the history a later occupant's
    backfill has to be bounded by.
    """
    previous_user = mapping.user
    detached = 0
    affected_days = []
    if detach_punches and previous_user is not None:
        detached_qs = AttendancePunch.objects.filter(
            device=mapping.device,
            employee_device_id=mapping.device_user_id,
            user=previous_user,
        )
        # Capture the days BEFORE clearing `user` — once detached, the punches no
        # longer point at the employee whose attendance rows need reverting, so
        # the derivation sweep could never find them again.
        affected_days = sorted(set(detached_qs.values_list("local_date", flat=True)))
        detached = detached_qs.update(
            user=None, biometric_employee=None, is_processed=False, processed_at=None)

    mapping.user = None
    mapping.is_active = False
    mapping.effective_until = timezone.localdate()
    mapping.save(update_fields=["user", "is_active", "effective_until", "updated_at"])

    # Roll the derived attendance back for every day we just detached.
    if affected_days:
        from .derivation import derive_daily_attendance
        for day in affected_days:
            derive_daily_attendance(previous_user, day)

    changes = {
        "previous_user": str(previous_user.pk) if previous_user else None,
        "previous_user_name": previous_user.get_full_name() if previous_user else None,
        "punches_detached": detached,
        "effective_until": str(mapping.effective_until),
    }
    audit_mapping_change(actor, AuditLog.Action.DELETE, mapping, "BIOMETRIC_MAPPING_UNMAPPED", request, **changes)
    logger.info("Biometric mapping unmapped: %s (%d punches detached)", mapping, detached)
    return mapping, detached


@transaction.atomic
def remap_employee(mapping, new_user, actor=None, effective_from=None, request=None):
    """Reassign a device ID to a different employee.

    Retires the current mapping *keeping its user* — that row is the historical
    record of who this device ID used to be, and the punches it attributed stay
    attributed. A fresh active row is created for the new employee and linked
    back via ``superseded_by``.
    """
    if mapping.user_id == getattr(new_user, "pk", None):
        raise ValidationError("This device ID is already mapped to that employee.")
    _assert_user_free_on_device(mapping.device, new_user, exclude_pk=mapping.pk)

    today = timezone.localdate()
    previous_user = mapping.user

    # Retire the old row first: the active-scoped unique constraint would
    # otherwise reject the replacement.
    #
    # The outgoing window closes YESTERDAY so the incoming one can open today.
    # Closing it today instead would push the replacement's start to tomorrow
    # and orphan the new employee's punches on the changeover day itself.
    mapping.is_active = False
    if mapping.effective_until is None:
        mapping.effective_until = today - timedelta(days=1)
    mapping.save(update_fields=["is_active", "effective_until", "updated_at"])

    replacement = BiometricEmployee.objects.create(
        device=mapping.device,
        device_user_id=mapping.device_user_id,
        user=new_user,
        device_name=mapping.device_name,
        privilege=mapping.privilege,
        card=mapping.card,
        group_id=mapping.group_id,
        is_active=True,
        # Defaults to the day after the previous occupant's window closes, so a
        # later backfill cannot reach back into their attendance.
        effective_from=effective_from or (mapping.effective_until + timedelta(days=1)),
        mapped_at=timezone.now(),
        mapped_by=actor if getattr(actor, "is_authenticated", False) else None,
    )
    mapping.superseded_by = replacement
    mapping.save(update_fields=["superseded_by", "updated_at"])

    audit_mapping_change(
        actor, AuditLog.Action.UPDATE, replacement, "BIOMETRIC_MAPPING_REMAPPED", request,
        previous_user=str(previous_user.pk) if previous_user else None,
        previous_user_name=previous_user.get_full_name() if previous_user else None,
        superseded_mapping=str(mapping.pk),
        effective_from=str(replacement.effective_from))
    logger.info("Biometric mapping remapped: %s -> %s", mapping, replacement)
    return replacement


# ===========================================================================
# Backfill — never automatic
# ===========================================================================

def implicit_backfill_floor(mapping):
    """Earliest date a backfill may reach, inferred from retired mappings.

    If this device ID previously belonged to someone else, their window's end
    is a hard floor: anything on or before it is their attendance, not this
    person's. Returns None when the device ID has no prior occupant.
    """
    previous_end = BiometricEmployee.objects.filter(
        device=mapping.device, device_user_id=mapping.device_user_id,
        effective_until__isnull=False,
    ).exclude(pk=mapping.pk).aggregate(latest=Max("effective_until"))["latest"]
    return previous_end + timedelta(days=1) if previous_end else None


def backfill_queryset(mapping, date_from=None, date_to=None):
    """Unattributed punches this mapping would claim, within its bounds."""
    qs = AttendancePunch.objects.filter(
        device=mapping.device,
        employee_device_id=mapping.device_user_id,
        user__isnull=True,
    )
    lower = date_from or mapping.effective_from or implicit_backfill_floor(mapping)
    upper = date_to or mapping.effective_until
    if lower:
        qs = qs.filter(local_date__gte=lower)
    if upper:
        qs = qs.filter(local_date__lte=upper)
    return qs, lower, upper


def preview_backfill(mapping, date_from=None, date_to=None):
    """What a backfill would do, without doing it.

    HR sees the count, the date range, the device and the employee before
    anything is written — the confirmation step the whole design turns on.
    """
    if not mapping.user_id:
        raise ValidationError("Map this device ID to an employee before previewing a backfill.")

    qs, lower, upper = backfill_queryset(mapping, date_from, date_to)
    bounds = qs.aggregate(first=Min("local_date"), last=Max("local_date"))
    total = qs.count()
    unbounded = lower is None

    return {
        "mapping_id": str(mapping.pk),
        "device": mapping.device.label,
        "device_name": mapping.device.name,
        "device_user_id": mapping.device_user_id,
        "user": str(mapping.user_id),
        "user_name": mapping.user.get_full_name(),
        "user_employee_id": mapping.user.employee_id,
        "records_count": total,
        "first_punch_date": bounds["first"],
        "last_punch_date": bounds["last"],
        "date_from": lower,
        "date_to": upper,
        "unbounded": unbounded,
        "warning": (
            "This mapping has no effective_from and this device ID has no recorded "
            "previous occupant, so the backfill would claim every unattributed punch "
            "for it. If the device ID was ever reused, this would take another "
            "employee's attendance. Set a start date, or pass allow_unbounded to override."
        ) if unbounded and total else "",
        "sample": list(
            qs.order_by("timestamp").values("local_date", "timestamp", "punch_label")[:10]
        ),
    }


@transaction.atomic
def backfill_punches(mapping, actor=None, date_from=None, date_to=None,
                     allow_unbounded=False, request=None):
    """Attach historical unmapped punches to this mapping's employee.

    Refuses to run unbounded unless explicitly overridden — that guard is the
    mitigation for recycled device IDs, where an unbounded claim would hand a
    departed employee's attendance to their replacement.
    """
    if not mapping.user_id:
        raise ValidationError("Map this device ID to an employee before backfilling.")

    qs, lower, upper = backfill_queryset(mapping, date_from, date_to)
    if lower is None and not allow_unbounded:
        raise ValidationError(
            "Refusing an unbounded backfill: this mapping has no start date and no "
            "previous occupant to bound it. Set effective_from, pass date_from, or "
            "explicitly allow_unbounded."
        )

    affected_days = sorted(set(qs.values_list("local_date", flat=True)))
    updated = qs.update(
        user_id=mapping.user_id, biometric_employee=mapping,
        is_processed=False, processed_at=None,
    )

    # Derive inline so the newly claimed history shows up immediately rather
    # than waiting for the cron sweep.
    if affected_days:
        from .derivation import derive_daily_attendance
        for day in affected_days:
            derive_daily_attendance(mapping.user, day)

    audit_mapping_change(
        actor, AuditLog.Action.UPDATE, mapping, "BIOMETRIC_BACKFILL_EXECUTED", request,
        records_updated=updated,
        date_from=str(lower) if lower else None,
        date_to=str(upper) if upper else None,
        unbounded=lower is None)
    logger.info("Biometric backfill: %s claimed %d punches", mapping, updated)
    return updated
