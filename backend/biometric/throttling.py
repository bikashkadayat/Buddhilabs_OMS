"""Rate limiting for device ingest.

Scoped per device rather than per user: the collectors authenticate as devices,
so DRF's UserRateThrottle would lump every terminal into one anonymous bucket
and let a single chatty device starve the rest.
"""
from rest_framework.throttling import SimpleRateThrottle

from .models import BiometricDevice


class DeviceRateThrottle(SimpleRateThrottle):
    """Per-device limit, generous enough for a full backlog flush.

    A first sync is ~22 bulk requests back to back, so the ceiling has to clear
    that comfortably or onboarding a device would throttle itself.

    NOTE: with no CACHES configured the project falls back to per-process
    LocMemCache, so the effective limit is multiplied by GUNICORN_WORKERS.
    Redis in Phase 8 makes this exact.
    """
    scope = "biometric_device"

    def get_cache_key(self, request, view):
        device = request.auth
        if not isinstance(device, BiometricDevice):
            return None  # not a device request — this throttle does not apply
        return self.cache_format % {"scope": self.scope, "ident": device.pk}
