"""Bikram Sambat (BS) date helpers.

Thin, dependency-isolated wrapper around `nepali_datetime` so the rest of the
codebase can convert AD dates/datetimes to BS strings without importing the lib
directly (and so a conversion failure never breaks an API response).
"""
from __future__ import annotations

import datetime as _dt

try:
    import nepali_datetime as _nd
except Exception:  # pragma: no cover - library always present in prod image
    _nd = None


def to_bs(value):
    """Return the BS date string 'YYYY-MM-DD' for an AD date/datetime, or None."""
    if value is None or _nd is None:
        return None
    if isinstance(value, _dt.datetime):
        value = value.date()
    if not isinstance(value, _dt.date):
        return None
    try:
        return _nd.date.from_datetime_date(value).strftime("%Y-%m-%d")
    except Exception:
        return None
