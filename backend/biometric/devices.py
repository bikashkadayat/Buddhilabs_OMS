"""Organization-managed biometric devices: setup, connection test, pull sync.

The legacy NIF deployment connected to its terminal by IP and port and pulled
attendance on a timer. Everything needed to do that already existed here --
``zk_client`` reads the terminal, ``collector.sync_device`` turns what it reads
into punches through the one ingest path, ``derivation`` turns punches into
attendance -- but only an operator with shell access could drive it
(``register_device``, ``device_sync``, a crontab line). This module is the
layer that lets an organization administrator do the same from Settings:

* ``check_device_address`` -- refuse addresses the SERVER must never be made to
  connect to (loopback, link-local/cloud metadata, multicast).
* ``test_connection``      -- reachability, device information, users and
  attendance log, read-only, nothing written to attendance.
* ``run_pull_sync``        -- one logged, locked pull; what both "Sync now" and
  the scheduler call.
* ``devices_due``          -- which devices the scheduler should pull now.

TENANCY. Nothing here takes an organization argument. Every function is called
either from a request (the middleware has bound the tenant) or from
``device_sync_due``, which enters ``tenant_context(device.organization)``
before touching a device. Ingest stamps each punch from its device's
organization, so a device can only ever write into the tenant that owns it.

IDENTITY. A device row names an address, and an address is not an identity:
DHCP can hand 192.168.1.100 to a different terminal tomorrow, and on a shared
platform two organizations could type the same address. So the first
successful contact records the terminal's own serial number in
``hardware_serial``, which is unique PLATFORM-WIDE, and every later pull
refuses to import from a terminal reporting a different serial. Another
organization's terminal can therefore never be pulled into this tenant, even
if its address is entered here.
"""
import ipaddress
import logging
import socket

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import collector, device_probe, locking, zk_client
from .models import AttendancePunch, BiometricDevice, DeviceSyncLog
from .services import finish_sync_log, set_device_status

logger = logging.getLogger(__name__)

# Connection test reads are bounded: a terminal that takes longer than this to
# answer is reported as slow rather than holding an HTTP request open.
TEST_TIMEOUT_SECONDS = 15
USER_SAMPLE_SIZE = 25


class DeviceAddressError(ValueError):
    """The address is not one this server may connect to."""


class DeviceIdentityError(collector.CollectorError):
    """The terminal at this address is not the one registered."""


# ---------------------------------------------------------------------------
# Address policy
# ---------------------------------------------------------------------------
def _allow_private():
    return bool(getattr(settings, "BIOMETRIC_DEVICE_ALLOW_PRIVATE_HOSTS", True))


def _refusal(ip):
    """Why ``ip`` may not be dialled, or None if it may.

    The connection is made BY THE SERVER, so an address field is a way to make
    the server open sockets wherever a tenant administrator likes. The ZK
    handshake limits what such a socket can do, but it still answers "is
    anything listening on 10.0.0.5:6379" -- a port scanner with a nice form.
    Loopback, link-local (which includes 169.254.169.254, the cloud metadata
    service) and multicast are never a terminal, so they are always refused.
    Private ranges are where terminals live, so they are allowed unless the
    deployment turns them off (a cloud host that reaches customer LANs only
    through public addresses or a VPN can set
    BIOMETRIC_DEVICE_ALLOW_PRIVATE_HOSTS = False).
    """
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        # Development only: the ZK simulator and an SSH tunnel to a real
        # terminal both listen on 127.0.0.1. Never on in production.
        if getattr(settings, "BIOMETRIC_DEVICE_ALLOW_LOOPBACK", False):
            return None
        return "a loopback address (this server itself)"
    if ip.is_link_local:
        return "a link-local address (includes the cloud metadata service)"
    if ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return "not a device address"
    if ip.is_private and not _allow_private():
        return "a private address, which this deployment does not dial"
    return None


def check_device_address(host, port):
    """Validate ``host``:``port`` and return the IP to actually connect to.

    Returning the resolved IP, and connecting to THAT rather than re-resolving
    the name, closes the DNS-rebinding gap: a hostname that resolves to a
    public address while being checked and to 127.0.0.1 a moment later would
    otherwise pass the check and still reach loopback.
    """
    host = (host or "").strip()
    if not host:
        raise DeviceAddressError("Enter the device's IP address.")
    if any(c in host for c in "/:@ ") and not _is_ipv6_literal(host):
        raise DeviceAddressError(
            "Enter just the address, e.g. 192.168.1.100 -- no http://, port or path.")
    try:
        port = int(port)
    except (TypeError, ValueError) as exc:
        raise DeviceAddressError("Port must be a number, usually 4370.") from exc
    if not 1 <= port <= 65535:
        raise DeviceAddressError("Port must be between 1 and 65535 (usually 4370).")

    try:
        candidates = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError) as exc:
            raise DeviceAddressError(f"{host} could not be resolved.") from exc
        candidates = [ipaddress.ip_address(info[4][0]) for info in infos]
        if not candidates:
            raise DeviceAddressError(f"{host} could not be resolved.")

    for ip in candidates:
        reason = _refusal(ip)
        if reason:
            raise DeviceAddressError(f"{host} is {reason}; it cannot be a biometric device.")
    return str(candidates[0]), port


def _is_ipv6_literal(host):
    try:
        return ipaddress.ip_address(host).version == 6
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
def bind_hardware_serial(device, reported):
    """Record the terminal's serial on first contact; refuse a different one.

    Raises ``DeviceIdentityError`` when the address now answers with another
    terminal, or when the serial already belongs to a device registered
    elsewhere on the platform. A terminal whose firmware does not report a
    serial is accepted -- refusing it would make old hardware unusable -- and
    the caller surfaces that as a warning.
    """
    reported = (reported or "").strip()
    if not reported:
        return False
    if device.hardware_serial:
        if device.hardware_serial != reported:
            raise DeviceIdentityError(
                f"The terminal at {device.host}:{device.port} reports serial "
                f"{reported}, but this device was registered as "
                f"{device.hardware_serial}. Nothing was imported. If the "
                f"terminal was replaced, clear the recorded serial on this "
                f"device and test the connection again.")
        return False
    device.hardware_serial = reported
    try:
        with transaction.atomic():
            device.save(update_fields=["hardware_serial", "updated_at"])
    except IntegrityError as exc:
        device.hardware_serial = ""
        raise DeviceIdentityError(
            f"The terminal with serial {reported} is already registered to "
            f"another account on this platform. A terminal can belong to one "
            f"organization only; nothing was imported.") from exc
    return True


# ---------------------------------------------------------------------------
# Connection test
# ---------------------------------------------------------------------------
def _driver_for(device_type):
    # Both supported types speak the ZK protocol; the native client is the
    # read-only one. Kept as a function so a future type has one place to go.
    return collector.client_for("native")


def read_terminal(host, port, *, comm_key=0, timeout=None, device_type=None,
                  include_logs=True, zone=None):
    """Connect, read everything a connection test reports, disconnect.

    Read-only, and returns plain data -- no model is touched -- so it serves
    both an unsaved form ("Test before saving") and a registered device.
    """
    from zoneinfo import ZoneInfo

    zone = zone or ZoneInfo(settings.TIME_ZONE)
    timeout = timeout or TEST_TIMEOUT_SECONDS
    transport = _driver_for(device_type)
    with transport(host, port, timeout=timeout, comm_key=comm_key) as client:
        info = client.device_info() if hasattr(client, "device_info") else {}
        sizes = client.sizes()
        drift = collector.measure_drift(client, zone)
        roster = client.users()
        logs = client.attendance() if include_logs else None

    result = {
        "device_info": info,
        "sizes": sizes,
        "clock": {"drift_seconds": drift},
        "users": {
            "count": len(roster),
            "sample": [{"device_user_id": u["employee_id"], "name": u.get("name", "")}
                       for u in roster[:USER_SAMPLE_SIZE]],
        },
    }
    if logs is not None:
        stamps = [r["timestamp"] for r in logs if r.get("timestamp")]
        result["attendance"] = {
            "count": len(logs),
            "earliest": min(stamps).isoformat() if stamps else None,
            "latest": max(stamps).isoformat() if stamps else None,
        }
    return result


def _diagnose_unreachable(host, port):
    """Tell "nothing there" apart from "something there that is not a terminal"."""
    tcp = device_probe.probe_tcp(host, port, timeout=4)
    if tcp.get("tcp_open"):
        return ("not_zk", f"{host}:{port} accepts connections but did not answer "
                          f"as a ZK terminal. Check the device type and port.")
    return ("offline", f"No response from {host}:{port}. Check the device is "
                       f"powered on, on the network, and reachable from this server.")


def test_connection(*, host, port, comm_key=0, device_type=None, device=None,
                    actor=None, include_logs=True):
    """The "Test Connection" button. Returns a result dict; never raises for a
    device that is simply not there -- that is a result, not an error.

    With ``device``, the outcome is also recorded: a TEST sync-log row, the
    online/offline status, and the device information read.
    """
    started = timezone.now()
    result = {"host": host, "port": port, "ok": False, "status": "offline",
              "message": "", "tested_at": started.isoformat(), "warnings": []}
    try:
        address, port = check_device_address(host, port)
    except DeviceAddressError as exc:
        result.update(status="invalid", message=str(exc))
        return result

    zone = collector.device_zone(device) if device else None
    try:
        read = read_terminal(address, port, comm_key=comm_key, device_type=device_type,
                             include_logs=include_logs, zone=zone)
    except zk_client.ZKAuthError:
        result.update(status="auth_required", message=(
            "The device answered but rejected the communication key. Enter the "
            "COMM key set on the device (Comm > Security), or 0 if none is set."))
    except zk_client.ZKError as exc:
        status, message = _diagnose_unreachable(address, port)
        result.update(status=status, message=message, detail=str(exc))
    except OSError as exc:
        result.update(status="offline", message=f"Connection failed: {exc}")
    else:
        result.update(read)
        result.update(ok=True, status="online", message="Device online.")
        serial = read["device_info"].get("serial_number")
        if not serial:
            result["warnings"].append(
                "The device did not report a serial number, so it cannot be "
                "pinned to this account by identity. It will still sync.")
        drift = read["clock"].get("drift_seconds")
        if drift is not None and abs(drift) > collector.CLOCK_DRIFT_ALARM_SECONDS:
            result["warnings"].append(
                f"The device clock is {drift}s from the server's. Punches near "
                f"midnight will land on the wrong day until it is corrected on the device.")
        if device is not None and serial:
            try:
                bind_hardware_serial(device, serial)
            except DeviceIdentityError as exc:
                result.update(ok=False, status="identity_mismatch", message=str(exc))

    if device is not None:
        _record_test(device, result, actor=actor, started=started)
    return result


def _record_test(device, result, *, actor, started):
    log = DeviceSyncLog.objects.create(
        device=device, sync_type=DeviceSyncLog.SyncType.TEST,
        trigger=DeviceSyncLog.Trigger.TEST, triggered_by=actor,
        status=DeviceSyncLog.Status.STARTED, started_at=started)
    finish_sync_log(log, received=(result.get("attendance") or {}).get("count", 0),
                    error="" if result["ok"] else result["message"])
    fields = ["updated_at"]
    if result["ok"]:
        device.device_info = {**(result.get("device_info") or {}),
                              "users": result["users"]["count"],
                              "records": (result.get("sizes") or {}).get("records"),
                              "users_capacity": (result.get("sizes") or {}).get("users_capacity"),
                              "records_capacity": (result.get("sizes") or {}).get("records_capacity")}
        device.device_info_at = timezone.now()
        device.last_seen_at = timezone.now()
        drift = result["clock"].get("drift_seconds")
        if drift is not None:
            device.clock_drift_seconds = drift
            fields.append("clock_drift_seconds")
        fields += ["device_info", "device_info_at", "last_seen_at"]
    device.save(update_fields=fields)
    set_device_status(device, result["ok"])


# ---------------------------------------------------------------------------
# Pull sync
# ---------------------------------------------------------------------------
def run_pull_sync(device, *, trigger, actor=None, timeout=None):
    """Pull one device once, and log it whatever happens.

    "Every sync logged": a PULL row is written for a success, a failure and a
    skip alike, and the device's last-sync fields are updated from it. The
    per-batch HISTORY rows that ingest writes still sit beneath it.

    Returns ``{"status", "message", "sync_log", "summary"}``.
    """
    now = timezone.now()
    log = DeviceSyncLog.objects.create(
        device=device, sync_type=DeviceSyncLog.SyncType.PULL, trigger=trigger,
        triggered_by=actor, status=DeviceSyncLog.Status.STARTED, started_at=now)
    device.last_sync_attempt_at = now
    device.save(update_fields=["last_sync_attempt_at", "updated_at"])

    summary = None
    try:
        address, port = check_device_address(device.host, device.port)
        # Locked on the row's primary key, which is unique platform-wide; a
        # label is only unique within one organization.
        with locking.device_lock(f"device-{device.pk}"):
            summary = collector.sync_device(
                device, host=address, comm_key=device.comm_key,
                timeout=timeout, source=AttendancePunch.Source.HISTORY,
                driver=None, identity_check=bind_hardware_serial)
    except locking.CollectorBusy:
        log.status = DeviceSyncLog.Status.SKIPPED
        log.error = "Another sync of this device was already running."
        log.finished_at = timezone.now()
        log.save(update_fields=["status", "error", "finished_at"])
        return {"status": "skipped", "message": log.error, "sync_log": str(log.pk),
                "summary": None}
    except (DeviceAddressError, collector.CollectorError, zk_client.ZKError,
            OSError) as exc:
        message = str(exc)
        if isinstance(exc, zk_client.ZKAuthError):
            message = ("The device rejected the communication key. Update the "
                       "COMM key on this device's settings.")
        finish_sync_log(log, error=message)
        _finish_device(device, status=DeviceSyncLog.Status.FAILED, error=message, imported=0)
        set_device_status(device, False)
        logger.warning("pull sync of %s failed: %s", device.label, message)
        return {"status": "failed", "message": message, "sync_log": str(log.pk),
                "summary": summary}

    punches = summary.get("punches") or {}
    finish_sync_log(
        log, received=punches.get("in_window", 0), created=punches.get("created", 0),
        duplicate=punches.get("duplicate", 0), unmapped=punches.get("unmapped", 0))
    info = dict(device.device_info or {})
    info.update(summary.get("device_info") or {})
    sizes = summary.get("sizes") or {}
    if sizes:
        info.update(users=sizes.get("users"), records=sizes.get("records"))
    device.device_info = info
    device.device_info_at = timezone.now()
    device.save(update_fields=["device_info", "device_info_at", "updated_at"])
    _finish_device(device, status=log.status, error="", imported=punches.get("created", 0))

    created = punches.get("created", 0)
    message = (f"Imported {created} new punch{'es' if created != 1 else ''}."
               if created else "Up to date - no new punches.")
    if punches.get("unmapped"):
        message += (f" {punches['unmapped']} belong to device users not yet "
                    f"mapped to an employee.")
    return {"status": log.status, "message": message, "sync_log": str(log.pk),
            "summary": summary}


def _finish_device(device, *, status, error, imported):
    device.last_sync_status = status
    device.last_sync_error = error
    device.last_sync_imported = imported
    device.save(update_fields=["last_sync_status", "last_sync_error",
                               "last_sync_imported", "updated_at"])


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------
def is_due(device, now=None):
    """Whether the scheduler should pull ``device`` now."""
    if not device.is_active or not device.host:
        return False
    interval = device.sync_interval_minutes or 0
    if interval <= 0:
        return False
    if device.last_sync_attempt_at is None:
        return True
    now = now or timezone.now()
    # Thirty seconds of slack, so a cron that fires at :00 every minute does
    # not miss a 5-minute device whose last attempt finished at :05:10 and
    # push it to :06.
    elapsed = (now - device.last_sync_attempt_at).total_seconds()
    return elapsed >= interval * 60 - 30


def devices_due(now=None):
    """Active, scheduled, pull devices of the CURRENT tenant that are due."""
    now = now or timezone.now()
    candidates = (BiometricDevice.objects
                  .filter(is_active=True, sync_interval_minutes__gt=0)
                  .exclude(host=""))
    return [d for d in candidates if is_due(d, now)]
