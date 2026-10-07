"""Device authentication for the ingest endpoints.

A collector is not a person: it holds a long-lived per-device key rather than a
30-minute JWT, and it signs each request so a captured payload cannot be
replayed or tampered with in transit.

    X-API-KEY     the device key issued in admin (shown once, stored hashed)
    X-TIMESTAMP   unix seconds, must be within the clock-skew window
    X-SIGNATURE   HMAC-SHA256 over method, path, timestamp and body digest

SECURITY NOTE — the HMAC secret is ``BiometricDevice.api_key_hash``, i.e.
sha256(raw_key). Verifying a signature requires the server to hold a usable
shared secret, so hashing cannot prevent forgery by someone who has already
read the database; what it does buy is that the *raw* key never exists
server-side, so a database leak cannot be replayed against anything else that
trusts that key. Surviving a database compromise would require asymmetric
signing (device holds a private key, server stores only the public half).
"""
import hashlib
import hmac
import logging
import time

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from rest_framework import authentication, exceptions, permissions

from .models import BiometricDevice
from .services import resolve_device_by_key

logger = logging.getLogger(__name__)

HEADER_KEY = "HTTP_X_API_KEY"
HEADER_TIMESTAMP = "HTTP_X_TIMESTAMP"
HEADER_SIGNATURE = "HTTP_X_SIGNATURE"

DEFAULT_CLOCK_SKEW_SECONDS = 300


def clock_skew_allowance():
    return int(getattr(settings, "BIOMETRIC_CLOCK_SKEW_SECONDS", DEFAULT_CLOCK_SKEW_SECONDS))


def build_signing_string(method, path, timestamp, body):
    """Canonical string both sides sign.

    Method and path are included so a signature captured for /punch/ cannot be
    replayed against /bulk-sync/, and the body digest makes the payload
    tamper-evident.
    """
    body_digest = hashlib.sha256(body or b"").hexdigest()
    return f"{method.upper()}\n{path}\n{timestamp}\n{body_digest}"


def sign(secret, method, path, timestamp, body):
    return hmac.new(
        secret.encode("utf-8"),
        build_signing_string(method, path, timestamp, body).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class DeviceAPIKeyAuthentication(authentication.BaseAuthentication):
    """Authenticate a collector by API key + request signature.

    Sets ``request.auth`` to the BiometricDevice. ``request.user`` stays
    anonymous on purpose — a device is not a person, and letting it occupy
    request.user would quietly grant it whatever a User is allowed to do.
    """

    def authenticate(self, request):
        raw_key = request.META.get(HEADER_KEY, "").strip()
        if not raw_key:
            return None  # let other authenticators (JWT) have a turn

        device = resolve_device_by_key(raw_key)
        if device is None:
            raise exceptions.AuthenticationFailed("Unknown or inactive device key.")

        self._verify_signature(request, device, raw_key)
        return (AnonymousUser(), device)

    def authenticate_header(self, request):
        # Drives a 401 (rather than 403) when credentials are missing/bad.
        return "X-API-KEY"

    def _verify_signature(self, request, device, raw_key):
        timestamp = request.META.get(HEADER_TIMESTAMP, "").strip()
        signature = request.META.get(HEADER_SIGNATURE, "").strip()
        if not timestamp or not signature:
            raise exceptions.AuthenticationFailed(
                "X-TIMESTAMP and X-SIGNATURE headers are required.")

        try:
            sent_at = int(timestamp)
        except ValueError:
            raise exceptions.AuthenticationFailed("X-TIMESTAMP must be unix seconds.")

        skew = abs(int(time.time()) - sent_at)
        if skew > clock_skew_allowance():
            # Bounds the replay window. Deliberately the ONLY replay control on
            # this path -- see the note on nonce rejection below.
            raise exceptions.AuthenticationFailed(
                f"Request timestamp is {skew}s out of date; check the device clock.")

        # Read the raw body BEFORE DRF parses it. Django caches _body, and DRF's
        # parser falls back to that cache, so this does not consume the stream.
        body = request.body if hasattr(request, "body") else b""
        expected = sign(device.api_key_hash, request.method, request.path, timestamp, body)

        if not hmac.compare_digest(expected, signature):
            logger.warning("Biometric signature mismatch for device=%s path=%s",
                           device.label, request.path)
            raise exceptions.AuthenticationFailed("Invalid request signature.")

        # ------------------------------------------------------------------
        # NOT rejecting replayed signatures, deliberately (Phase 11, M8).
        #
        # The audit proposed a nonce cache so an identical signed request could
        # not be sent twice inside the skew window. It was implemented, and the
        # existing ingest tests immediately showed why it is wrong here:
        # `test_replaying_a_valid_request_creates_no_duplicates`,
        # `test_full_backlog_replay_is_free_the_second_time`,
        # `test_duplicate_single_punch_returns_200_not_201` and
        # `test_roster_sync_is_idempotent` all failed.
        #
        # Those tests are not incidental -- they encode how the collectors
        # actually behave. A terminal whose response times out RETRIES the same
        # batch, byte for byte. Rejecting the retry would break at-least-once
        # delivery and silently lose attendance on any flaky link, which is a
        # far more likely and more damaging event than the attack a nonce
        # prevents (an attacker who can capture a signed request in flight has
        # already broken TLS).
        #
        # Every ingest endpoint is idempotent by database constraint, which is a
        # STRONGER property than replay rejection: a replay is not merely
        # detected, it is harmless. The finding is therefore closed as
        # "mitigated by design" rather than by adding a control that trades a
        # real availability property for a theoretical security gain.
        # ------------------------------------------------------------------


class IsRegisteredDevice(permissions.BasePermission):
    """Allow only a request authenticated as an active biometric device."""
    message = "A valid device API key is required."

    def has_permission(self, request, view):
        return isinstance(request.auth, BiometricDevice) and request.auth.is_active
