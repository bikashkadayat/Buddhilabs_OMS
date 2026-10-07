"""The iClock / PUSH (ADMS) server — the endpoints a ZKTeco terminal dials out to.

These are not REST and cannot be made REST: the paths, the query parameters and
the plain-text bodies are all hardcoded in the device firmware. They live at the
web root (``/iclock/...``) for the same reason.

THE ACKNOWLEDGEMENT CONTRACT
----------------------------
``OK`` means "I have it, you may forget it". The terminal deletes its local copy
on that word. Every handler here therefore returns OK only after the data is
committed, and returns a non-2xx on *any* doubt — unknown serial, unparseable
body, database error. The device then keeps the records and retries on its
``ErrorDelay``. Losing a day of attendance to a cheerful acknowledgement is the
one failure this protocol makes easy, and the one this module is arranged to
prevent.

AUTHENTICATION, HONESTLY
------------------------
The protocol has none. A device identifies itself with ``?SN=<serial>`` in the
query string and that is the entire story — there is no key, no signature, and
no way to add one, because the firmware will not send what it does not have.

So the controls here are the ones actually available:

* the serial must match a **registered, active** device (an allow-list),
* an optional IP allow-list (``BIOMETRIC_PUSH_ALLOWED_IPS``), which is EMPTY by
  default — an out-of-the-box deployment does not restrict source addresses,
* per-serial throttling (``PUSH_RATE_PER_MINUTE``), plus a tighter per-source
  limit on unregistered serials (``PUSH_UNKNOWN_RATE_PER_MINUTE``) so the serial
  space cannot be walked at speed.

That is genuinely weaker than the HMAC-signed ingest API, and it should be
stated rather than glossed: **anyone who can reach this endpoint and knows a
device serial can post attendance.** The mitigation is network placement — the
terminal is on the LAN, so this path should not be exposed to the internet.
``BIOMETRIC_PUSH_ALLOWED_IPS`` is how you enforce that at the application layer
when the reverse proxy cannot.
"""
import logging

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, transaction
from django.http import HttpResponse, HttpResponseForbidden
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from config.client_ip import client_ip

from . import push_protocol
from .collector import device_zone, localise
from .ingest import ingest_punches, ingest_roster
from .models import AttendancePunch, BiometricDevice, DeviceSyncLog
from .services import touch_device

logger = logging.getLogger(__name__)

# Plain text, not JSON: the firmware's parser expects it and ignores anything else.
TEXT = "text/plain; charset=utf-8"


def _ok(body="OK"):
    return HttpResponse(f"{body}\n", content_type=TEXT)


def _retry_later(reason, status=503):
    """Tell the device to keep its data and come back.

    Deliberately never 200. A 4xx/5xx is what makes the terminal retain the
    records; that is the entire point of this helper existing separately.
    """
    logger.warning("PUSH refused: %s", reason)
    return HttpResponse(f"ERROR: {reason}\n", status=status, content_type=TEXT)


# Per-serial and per-source rate limits for the PUSH path.
#
# The module docstring has always listed throttling among the controls that
# compensate for this endpoint having no authentication, but nothing implemented
# it: these are plain django.views.View subclasses, so DRF's throttling never
# runs on them, and biometric.throttling.DeviceRateThrottle keys on
# ``request.auth`` being a BiometricDevice — only ever true on the HMAC-signed
# REST ingest path.
#
# Generous on purpose. A terminal flushing a backlog posts in bursts, and
# throttling real attendance away would be a worse failure than the one this
# prevents. The unknown-serial limit is the tighter of the two because that is
# the enumeration path: with an empty BIOMETRIC_PUSH_ALLOWED_IPS the only thing
# standing between a caller and the ingest is guessing a serial.
PUSH_RATE_PER_MINUTE = getattr(settings, "BIOMETRIC_PUSH_RATE_PER_MINUTE", 240)
PUSH_UNKNOWN_RATE_PER_MINUTE = getattr(settings, "BIOMETRIC_PUSH_UNKNOWN_RATE_PER_MINUTE", 20)
_WINDOW = 60


def _over_limit(key, limit):
    """Fixed-window counter. True when THIS request takes the count past `limit`.

    Uses add()+incr() so the TTL is set exactly once per window. A cache blip
    fails OPEN: dropping real punches to protect against a hypothetical flood is
    the wrong trade for this endpoint.
    """
    if limit <= 0:
        return False
    try:
        if cache.add(key, 1, _WINDOW):
            return False
        return cache.incr(key) > limit
    except ValueError:
        # Key expired between add() and incr() — treat as the first of a window.
        try:
            cache.add(key, 1, _WINDOW)
        except Exception:  # noqa: BLE001
            pass
        return False
    except Exception:  # noqa: BLE001
        logger.warning("PUSH throttle cache unavailable; allowing the request")
        return False


def _too_many(reason):
    """429 — non-2xx, so the terminal keeps its records and retries."""
    logger.warning("PUSH throttled: %s", reason)
    response = HttpResponse(f"ERROR: {reason}\n", status=429, content_type=TEXT)
    response["Retry-After"] = str(_WINDOW)
    return response


def _resolve_device(request):
    """Find the device by serial, or explain why we will not accept its data."""
    serial = (request.GET.get("SN") or request.GET.get("sn") or "").strip()
    if not serial:
        return None, _retry_later("no SN in the request", status=400)

    if _over_limit(f"iclock:rate:serial:{serial}", PUSH_RATE_PER_MINUTE):
        return None, _too_many(
            f"serial {serial!r} exceeded {PUSH_RATE_PER_MINUTE} requests/min")

    allowed = [ip.strip() for ip in
               getattr(settings, "BIOMETRIC_PUSH_ALLOWED_IPS", "").split(",") if ip.strip()]
    if allowed:
        source = client_ip(request)
        if source not in allowed:
            # 403, not 503: this is not a transient condition, and a device that
            # will never be allowed should not be encouraged to hammer us.
            logger.warning("PUSH from disallowed IP %s (serial %s)", source, serial)
            return None, HttpResponseForbidden("forbidden\n", content_type=TEXT)

    # `.first()` is unambiguous because `uniq_biometric_device_serial_global`
    # makes a non-blank serial unique PLATFORM-WIDE (Phase S3). That constraint
    # is what turns serial -> tenant into a function: the device row is the only
    # thing in an unauthenticated iClock request that says which organization
    # the punches belong to, so two tenants holding one serial would mean this
    # line picking a tenant at random.
    device = BiometricDevice.objects.filter(serial_number=serial,
                                            is_active=True).first()
    if device is None:
        # Enumeration guard. Keyed by SOURCE, not serial: a caller walking the
        # serial space presents a new serial every request, so a per-serial
        # counter would never register it.
        if _over_limit(f"iclock:rate:unknown:{client_ip(request)}",
                       PUSH_UNKNOWN_RATE_PER_MINUTE):
            return None, _too_many(
                f"too many unregistered-serial requests from this source "
                f"(limit {PUSH_UNKNOWN_RATE_PER_MINUTE}/min)")
        # 401 rather than 200: an unknown serial must NOT be told OK, or a
        # misconfigured device cheerfully erases attendance we never stored.
        # Retrying forever is the correct, safe behaviour until an admin
        # registers the serial.
        return None, _retry_later(
            f"serial {serial!r} is not a registered active device — register it "
            f"in admin (BiometricDevice.serial_number) and the buffered records "
            f"will arrive on the next retry", status=401)
    return device, None


@method_decorator(csrf_exempt, name="dispatch")
class IClockBaseView(View):
    """Shared plumbing. CSRF-exempt because the client is a fingerprint
    terminal with no cookies, no session and no ability to carry a token."""

    def dispatch(self, request, *args, **kwargs):
        device, refusal = _resolve_device(request)
        if refusal is not None:
            return refusal
        self.device = device
        return super().dispatch(request, *args, **kwargs)

    def body_text(self, request):
        # Firmware encodings vary and some ship names in GB18030. Replace rather
        # than fail: a mangled name is recoverable, a rejected batch of punches
        # is not.
        for encoding in ("utf-8", "gb18030", "latin-1"):
            try:
                return request.body.decode(encoding)
            except UnicodeDecodeError:
                continue
        return request.body.decode("utf-8", errors="replace")


class CdataView(IClockBaseView):
    """``/iclock/cdata`` — handshake on GET, data delivery on POST."""

    def get(self, request):
        """First contact. The reply is the device's whole configuration."""
        zone = device_zone(self.device)
        touch_device(self.device, seen=True)
        logger.info("PUSH handshake from %s (serial %s)",
                    self.device.label, self.device.serial_number)
        return _ok(push_protocol.handshake_response(
            self.device.serial_number,
            stamp=request.GET.get("Stamp", "0"),
            op_stamp=request.GET.get("OpStamp", "0"),
            timezone_offset=push_protocol.timezone_offset_hours(zone, timezone.now()),
        ).rstrip("\n"))

    def post(self, request):
        table = (request.GET.get("table") or "").strip().upper()
        body = self.body_text(request)

        if table == "ATTLOG":
            return self._attendance(request, body)
        if table == "OPERLOG":
            return self._operlog(request, body)

        # OPTIONS, BIODATA, fingerprint templates and others we do not consume.
        # Acknowledging is correct here: we are not storing them and never will,
        # so making the device retain them forever would fill its buffer and
        # eventually cost us the attendance we DO want.
        logger.info("PUSH table %r from %s ignored (%d bytes)",
                    table or "<none>", self.device.label, len(body))
        return _ok()

    # -- attendance ---------------------------------------------------------
    def _attendance(self, request, body):
        records, errors = push_protocol.parse_attlog(body)

        if errors and not records:
            return _retry_later(
                f"could not parse any of {len(errors)} attendance line(s): "
                f"{errors[0]}", status=400)

        zone = device_zone(self.device)
        punches = [{**r, "timestamp": localise(r["timestamp"], zone)} for r in records]

        try:
            with transaction.atomic():
                summary = ingest_punches(
                    self.device, punches,
                    source=AttendancePunch.Source.LIVE,
                    sync_type=DeviceSyncLog.SyncType.LIVE,
                    client_ip=client_ip(request))
        except DatabaseError as exc:
            # The one case where saying OK would destroy data: we failed to
            # store it. Make the device keep its copy.
            logger.exception("PUSH attendance from %s failed to store", self.device.label)
            return _retry_later(f"storage failed: {exc}", status=503)

        if errors:
            # Partial success. The device cannot retry individual lines, so
            # acknowledge what was stored and record the rest loudly — refusing
            # the batch would replay the good records forever without ever
            # fixing the bad one.
            logger.error("PUSH from %s: %d unparseable line(s), first: %s",
                         self.device.label, len(errors), errors[0])

        logger.info("PUSH attendance from %s: %s", self.device.label, summary)
        return _ok(f"OK: {summary['created']}")

    # -- roster -------------------------------------------------------------
    def _operlog(self, request, body):
        users, others = push_protocol.parse_operlog(body)
        if users:
            try:
                with transaction.atomic():
                    ingest_roster(self.device, users, client_ip=client_ip(request))
            except DatabaseError as exc:
                logger.exception("PUSH roster from %s failed to store", self.device.label)
                return _retry_later(f"storage failed: {exc}", status=503)
            logger.info("PUSH roster from %s: %d user record(s)",
                        self.device.label, len(users))
        return _ok(f"OK: {len(users) + others}")


class GetRequestView(IClockBaseView):
    """``/iclock/getrequest`` — the device asking "anything for me?".

    Answering ``OK`` means "nothing to do". This is also the device's liveness
    beat: it polls here every ``Delay`` seconds, so a device that stops calling
    is a device that has gone away, and ``last_seen_at`` is what the offline
    detector reads.

    No command queue is implemented, deliberately. Commands from this endpoint
    can create, delete and renumber users on the terminal — and the device is
    the source of truth for identity. An OMS that can rewrite device user IDs
    is precisely what the go-live requirement forbids, so the capability is
    absent rather than merely unused.
    """

    def get(self, request):
        touch_device(self.device, seen=True)
        return _ok()

    post = get


class DeviceCmdView(IClockBaseView):
    """``/iclock/devicecmd`` — results of commands we never send. Acknowledge."""

    def post(self, request):
        touch_device(self.device, seen=True)
        return _ok()

    get = post


class PingView(IClockBaseView):
    """``/iclock/ping`` — a bare liveness check on some firmware."""

    def get(self, request):
        touch_device(self.device, seen=True)
        return _ok()

    post = get
