"""Realtime event broadcasting.

Two rules govern everything here, and both exist because the alternative is a
production incident:

1. **Broadcast on commit, never inside the transaction.** Ingest and derivation
   both run in ``transaction.atomic``. A ``group_send`` from inside one reaches
   the browser before the commit lands, the browser refetches, and it reads the
   *old* data — a live dashboard that is reliably one event behind.

2. **A broadcast may never raise.** ``group_send`` throws when Redis is
   unreachable. If that propagated, a Redis outage would start failing punch
   ingestion: the dashboard's message broker would be taking down the system of
   record. Every send here is best-effort and swallows its own errors.

Event names are versioned (``.v1``). Channels derives the consumer handler name
by replacing dots with underscores, so ``attendance.punch.v1`` dispatches to
``attendance_punch_v1`` — adding a v2 later means adding a method, not breaking
clients still speaking v1.
"""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# --- event types ----------------------------------------------------------
EVENT_PUNCH = "attendance.punch.v1"
EVENT_ATTENDANCE_UPDATED = "attendance.updated.v1"
EVENT_DEVICE_ONLINE = "device.online.v1"
EVENT_DEVICE_OFFLINE = "device.offline.v1"
EVENT_CONNECTION_READY = "connection.ready.v1"

# --- groups ---------------------------------------------------------------
#
# EVERY GROUP NAME CARRIES THE TENANT (Phase S3).
#
# These were the plain strings "attendance.all" and "devices". Every org-wide
# reader on the platform joined the same two groups, so a punch at one
# company's gate was pushed, live, into every other company's HR dashboard.
# There is no query in that path and no serializer -- the leak is the broker,
# which is why Phase S2's 28 organization columns did not touch it.
#
# Group names are ephemeral (in-memory or Redis, never persisted), so renaming
# them needs no migration: the worst case is that sockets open at deploy time
# stop receiving until they reconnect.
#
# The per-user and per-department groups are keyed on UUIDs and were therefore
# already collision-free. They carry the tenant too, for one reason: a group
# name should say who it belongs to, so a Redis keyspace dump during an
# incident is readable instead of being a list of bare UUIDs.
from tenancy.keys import group_name as _group_name


def group_all(organization=None):
    """Everyone in ONE tenant who may see the whole organisation."""
    return _group_name("attendance", "all", organization=organization)


def group_devices(organization=None):
    """One tenant's terminal-health feed."""
    return _group_name("devices", organization=organization)


def group_for_department(department_id, organization=None):
    return _group_name("attendance", "dept", department_id,
                       organization=organization)


def group_for_user(user_id, organization=None):
    return _group_name("attendance", "user", user_id,
                       organization=organization)


def _send_now(groups, event_type, payload):
    """Fire the message. Never raises — see rule 2 in the module docstring."""
    try:
        layer = get_channel_layer()
    except Exception:
        logger.warning("No channel layer configured; dropping %s", event_type)
        return 0

    if layer is None:
        return 0

    message = {"type": event_type, "payload": payload}
    sent = 0
    for group in groups:
        try:
            async_to_sync(layer.group_send)(group, message)
            sent += 1
        except Exception:
            # Redis down, layer misconfigured, event loop weirdness — the punch
            # is already committed, so this is cosmetic. Log and carry on.
            logger.warning("Failed to broadcast %s to %s", event_type, group, exc_info=True)
    return sent


def broadcast(groups, event_type, data, *, immediate=False):
    """Queue a broadcast for after the current transaction commits.

    ``immediate=True`` skips the on_commit hook — only for callers that are
    genuinely not inside a transaction (management commands), where on_commit
    would fire straight away anyway but the explicit form documents intent.
    """
    groups = [g for g in groups if g]
    if not groups:
        return

    payload = {"at": timezone.now().isoformat(), "data": data}
    if immediate:
        _send_now(groups, event_type, payload)
    else:
        transaction.on_commit(lambda: _send_now(groups, event_type, payload))


# ---------------------------------------------------------------------------
# Domain events
# ---------------------------------------------------------------------------

def _audience_for(user, organization=None):
    """Which groups may see events about this employee.

    Mirrors attendance.views.AttendanceListView's role scoping exactly: HR/Admin
    see everything, a department head sees their own department, an employee
    sees only themselves. Nobody may learn over a socket what they could not
    fetch over REST.

    ``organization`` is taken from the employee's own row when not supplied, so
    a punch ingested by `device_sync` -- which has no request and therefore no
    tenant context -- still addresses the right tenant's groups.
    """
    if organization is None:
        organization = getattr(user, "organization_id", None)

    groups = [group_all(organization)]
    if user is None:
        return groups
    groups.append(group_for_user(user.pk, organization))
    department_id = getattr(user, "department_ref_id", None)
    if department_id:
        groups.append(group_for_department(department_id, organization))
    return groups


def punch_recorded(punch):
    """A raw punch landed. Unmapped punches still broadcast, to HR only."""
    user = punch.user
    data = {
        "punch_id": str(punch.pk),
        "user": str(user.pk) if user else None,
        "employee_name": user.get_full_name() if user else (punch.employee_name or "Unmapped"),
        "employee_code": (user.employee_id if user else None),
        "department": (user.department_name if user else None),
        "device": punch.device.label,
        "device_user_id": punch.employee_device_id,
        "punch": punch.punch,
        "punch_label": punch.punch_label,
        "timestamp": punch.timestamp.isoformat(),
        "local_date": punch.local_date.isoformat(),
        "is_mapped": user is not None,
    }
    # An unmapped punch is an HR problem, not the whole org's — the org-wide
    # group only. The tenant comes from the DEVICE, which is the one thing an
    # unmapped punch does identify: biometric.BiometricDevice is a Phase A
    # model and carries its organization.
    device_org = punch.device.organization_id
    audience = (_audience_for(user, device_org) if user
                else [group_all(device_org)])
    broadcast(audience, EVENT_PUNCH, data)


def attendance_updated(record):
    """A daily attendance row was created or changed by derivation."""
    user = record.employee
    data = {
        "user": str(user.pk),
        "employee_name": user.get_full_name(),
        "employee_code": user.employee_id,
        "department": user.department_name,
        "date": record.date.isoformat(),
        "status": record.status,
        "check_in": record.check_in.isoformat() if record.check_in else None,
        "check_out": record.check_out.isoformat() if record.check_out else None,
        "working_hours": str(record.working_hours),
        "source": record.source,
        "punch_count": record.punch_count,
    }
    broadcast(_audience_for(user, user.organization_id),
              EVENT_ATTENDANCE_UPDATED, data)


def device_status_changed(device, online, *, immediate=False):
    data = {
        "device": str(device.pk),
        "label": device.label,
        "name": device.name,
        "connection_status": device.connection_status,
        "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
        "last_sync_at": device.last_sync_at.isoformat() if device.last_sync_at else None,
        "pending_punches": device.pending_punches,
        "failed_batches": device.failed_batches,
    }
    # The device owns the tenant; a health event must reach that tenant's HR
    # and nobody else's.
    broadcast([group_devices(device.organization_id)],
              EVENT_DEVICE_ONLINE if online else EVENT_DEVICE_OFFLINE,
              data, immediate=immediate)
