"""Personal biometric attendance — the backend as a privacy gatekeeper.

The morx dashboard (`biometric` service) serves the *entire* punch dataset over
an unauthenticated `/api/data`, so it must never be exposed to an individual
employee directly. Instead this module fetches that dataset server-side, over the
internal Docker network, and hands back only the rows belonging to the requesting
user — matched by their `biometric_id` (the ZK terminal's employee id, set by HR).

Nothing here writes; it is a read-only projection of what the terminal recorded.
"""

import json
import logging
import time
import urllib.request
from datetime import datetime

from django.conf import settings
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

# The whole dataset is a single ~240 KB JSON; caching it briefly means a burst of
# employees opening their dashboards costs one upstream fetch, not one each.
_CACHE = {"at": 0.0, "data": None}
_CACHE_TTL = 30.0  # seconds


def _fetch_dataset():
    now = time.monotonic()
    if _CACHE["data"] is not None and now - _CACHE["at"] < _CACHE_TTL:
        return _CACHE["data"]
    url = settings.BIOMETRIC_INTERNAL_URL.rstrip("/") + "/api/data"
    with urllib.request.urlopen(url, timeout=6) as resp:
        data = json.load(resp)
    _CACHE["data"] = data
    _CACHE["at"] = now
    return data


def _office_start_minutes():
    raw = getattr(settings, "ATTENDANCE_OFFICE_START", "10:00")
    try:
        h, m = (int(x) for x in raw.split(":"))
        return h * 60 + m
    except (ValueError, AttributeError):
        return 10 * 60


def _median(values):
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _clock(minute):
    if minute is None:
        return None
    minute = int(round(minute))
    return f"{minute // 60:02d}:{minute % 60:02d}"


# morx stores each punch's wall-clock time as an epoch second parsed at UTC, so
# reading it back at UTC reproduces exactly what the terminal displayed.
_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def employee_attendance(dataset, bid, window_days=90, date_from=None, date_to=None, limit=400):
    """Fold the whole dataset down to one employee's day-by-day attendance.

    Range selection, in priority order:
      * date_from / date_to (inclusive) — a custom window;
      * window_days — the most recent N days (None = all of history), anchored to
        the newest punch or today, whichever is earlier, so a terminal whose clock
        ran ahead (this data has some) doesn't push "last 90 days" into the future.
    """
    employees = dataset.get("employees", [])
    index = next((i for i, e in enumerate(employees) if str(e.get("id")) == str(bid)), None)
    if index is None:
        return None  # id set on the user but unknown to the device

    person = employees[index]
    rec = dataset.get("records", {})
    e_col, t_col = rec.get("e", []), rec.get("t", [])

    lo = hi = None
    if date_from or date_to:
        lo = date_from.toordinal() if date_from else None
        hi = date_to.toordinal() if date_to else None
    elif window_days and t_col:
        newest = min(datetime.utcfromtimestamp(max(t_col)).date(), datetime.utcnow().date())
        lo = newest.toordinal() - (window_days - 1)
        hi = newest.toordinal()

    # (date -> {first, last, count}) built in one pass over this person's punches.
    days = {}
    for i in range(len(t_col)):
        if e_col[i] != index:
            continue
        dt = datetime.utcfromtimestamp(t_col[i])
        key = dt.date()
        ordinal = key.toordinal()
        if (lo is not None and ordinal < lo) or (hi is not None and ordinal > hi):
            continue
        minute = dt.hour * 60 + dt.minute
        d = days.get(key)
        if d is None:
            days[key] = {"first": minute, "last": minute, "count": 1, "wd": dt.weekday()}
        else:
            d["first"] = min(d["first"], minute)
            d["last"] = max(d["last"], minute)
            d["count"] += 1

    late_after = _office_start_minutes()
    arrivals, departures, spans, punches = [], [], [], 0
    rows = []
    for key in sorted(days):
        d = days[key]
        arrivals.append(d["first"])
        departures.append(d["last"])
        if d["count"] > 1:
            spans.append(d["last"] - d["first"])
        punches += d["count"]
        span = d["last"] - d["first"] if d["count"] > 1 else None
        rows.append({
            "date": key.isoformat(),
            "weekday": _WEEKDAYS[d["wd"]],
            "first": _clock(d["first"]),
            "last": _clock(d["last"]) if d["count"] > 1 else None,
            # Numeric forms for charting: minutes-of-day for in/out, span in hours.
            "first_min": d["first"],
            "last_min": d["last"] if d["count"] > 1 else None,
            "hours": round(span / 60, 2) if span is not None else None,
            "punches": d["count"],
            "late": d["first"] > late_after,
        })

    rows.reverse()  # most recent first
    last_seen = None
    if days:
        last_key = max(days)
        last_seen = f"{last_key.isoformat()} {_clock(days[last_key]['last'])}"

    return {
        "employee": {"id": str(person.get("id")), "name": person.get("name")},
        "summary": {
            "days_present": len(days),
            "punches": punches,
            "late_days": sum(1 for r in rows if r["late"]),
            "median_arrival": _clock(_median(arrivals)),
            "median_departure": _clock(_median(departures)),
            "median_span_minutes": int(_median(spans)) if spans else None,
            "last_seen": last_seen,
            "office_start": _clock(late_after),
        },
        "days": rows[:limit],
    }


def month_summary_for(dataset, bid, date_from, date_to):
    """Compact biometric presence for one employee over an inclusive date range.

    Built for the team table: returns None when the id is unknown to the device,
    otherwise a small dict (days present, late days, typical in/out, last seen).
    """
    result = employee_attendance(dataset, bid, date_from=date_from, date_to=date_to)
    if result is None:
        return None
    s = result["summary"]
    return {
        "days_present": s["days_present"],
        "late_days": s["late_days"],
        "median_arrival": s["median_arrival"],
        "median_departure": s["median_departure"],
        "last_seen": s["last_seen"],
    }


class MyBiometricView(APIView):
    """The logged-in employee's own biometric punches — and only their own."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        bid = (request.user.biometric_id or "").strip()
        if not bid:
            # Not linked yet: a clean, explicit "nothing to show" the UI can act on
            # rather than an error.
            return Response({"configured": False})

        try:
            dataset = _fetch_dataset()
        except Exception:  # noqa: BLE001 — upstream/network issues are all "unavailable"
            logger.exception("biometric: could not reach %s", settings.BIOMETRIC_INTERNAL_URL)
            return Response(
                {"configured": True, "available": False,
                 "detail": "The biometric service is unreachable right now."},
                status=503,
            )

        # Custom range wins; otherwise a recent-days window (default 90, "all" = full).
        def _parse_date(value):
            try:
                return datetime.strptime(value, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                return None

        date_from = _parse_date(request.query_params.get("from"))
        date_to = _parse_date(request.query_params.get("to"))
        if date_from or date_to:
            result = employee_attendance(dataset, bid, date_from=date_from, date_to=date_to)
        else:
            raw = (request.query_params.get("days") or "90").lower()
            window = None if raw == "all" else max(1, min(int(raw) if raw.isdigit() else 90, 3650))
            result = employee_attendance(dataset, bid, window_days=window)
        if result is None:
            return Response(
                {"configured": True, "available": True, "found": False,
                 "detail": "Your biometric id is not recognised by the device."},
            )

        # Today's punches, computed in the office timezone so "today" is correct
        # regardless of the viewer's clock. Powers the live dashboard widget.
        from django.utils import timezone
        today = timezone.localdate()
        today_view = employee_attendance(dataset, bid, date_from=today, date_to=today)
        today_row = today_view["days"][0] if today_view and today_view["days"] else None

        return Response({
            "configured": True,
            "available": True,
            "found": True,
            "generated_at": dataset.get("generated_at"),
            "today": today_row,
            "today_date": today.isoformat(),
            **result,
        })
