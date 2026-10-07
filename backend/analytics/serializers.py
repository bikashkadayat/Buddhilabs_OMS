"""The response envelope, and the contract every analytics payload keeps.

One envelope for nine endpoints means the frontend has one loading/error/stale
pattern instead of nine, and one place to assert chart-data integrity in tests.

``sanitise`` is not decoration. Recharts plots a ``Decimal`` serialised as a
string, and a ``NaN``, as zero -- silently, with no error anywhere. A dashboard
that quietly reads zero is worse than one that fails, so every payload is walked
once on the way out.
"""
import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone


def envelope(*, window, scope, data, cached=False, ttl=None, generated_at=None):
    """Wrap a payload with everything the UI needs to explain what it is showing."""
    stamp = generated_at or timezone.localtime()
    payload = {
        "window": window.as_dict() if window is not None else None,
        "scope": scope.as_dict() if scope is not None else None,
        "generated_at": stamp.isoformat(),
        "cached": cached,
        "data": sanitise(data),
    }
    if ttl:
        payload["stale_after"] = (stamp + timedelta(seconds=ttl)).isoformat()
    return payload


def sanitise(value):
    """Recursively make a payload safe to hand to a chart library.

    * ``Decimal`` -> float. Serialised as a string it becomes a zero on an axis.
    * ``NaN`` / ``inf`` -> None. JSON has no literal for either, and Python's
      encoder emits bare ``NaN``, which ``JSON.parse`` rejects outright.
    * dates and times -> ISO strings, so no view has to remember to do it.
    """
    if isinstance(value, dict):
        return {key: sanitise(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitise(inner) for inner in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    return value
