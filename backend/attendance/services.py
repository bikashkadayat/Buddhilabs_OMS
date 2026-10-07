"""Attendance business logic — reuses the existing Holiday / Leave / BS-date
systems so nothing is duplicated."""
import json
import logging
import urllib.parse
import urllib.request
from datetime import date as _date, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import asin, cos, radians, sin, sqrt

from django.conf import settings
from django.utils import timezone

from .models import Attendance

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")
_COORD = Decimal("0.000001")  # 6 decimal places (~0.11 m)


def parse_coordinates(data):
    """Extract (lat, lng, accuracy) from a check-in/out request body.

    Returns a dict with Decimal lat/lng (6dp) and float accuracy, or an empty
    dict when no valid coordinates are supplied. Location is optional, so
    anything malformed or out of range is silently dropped rather than raising —
    the check-in still succeeds without a location.
    """
    lat_raw = data.get("latitude", data.get("lat"))
    lng_raw = data.get("longitude", data.get("lng"))
    if lat_raw in (None, "") or lng_raw in (None, ""):
        return {}
    try:
        lat = Decimal(str(lat_raw)).quantize(_COORD)
        lng = Decimal(str(lng_raw)).quantize(_COORD)
    except (InvalidOperation, ValueError, TypeError):
        return {}
    if not (Decimal("-90") <= lat <= Decimal("90")) or not (Decimal("-180") <= lng <= Decimal("180")):
        return {}
    out = {"lat": lat, "lng": lng}
    acc_raw = data.get("accuracy")
    if acc_raw not in (None, ""):
        try:
            out["accuracy"] = float(acc_raw)
        except (ValueError, TypeError):
            pass

    # HOW the fix was obtained. Against a fixed vocabulary, because this is a
    # client-supplied string that is about to be stored and shown to HR as
    # provenance -- an arbitrary value would be an unvalidated label on
    # evidence. Anything unrecognised becomes `unknown`, which is the honest
    # answer rather than a silent drop.
    source = str(data.get("location_source") or "").strip().lower()
    valid = {c for c, _ in Attendance.LocationSource.choices}
    out["source"] = source if source in valid else (
        Attendance.LocationSource.UNKNOWN if source
        else Attendance.LocationSource.UNKNOWN)
    return out


def classify_accuracy(accuracy):
    """Infer provenance from the accuracy radius when the client is silent.

    The browser's Geolocation API never says what produced a fix, so a
    client that does not volunteer `location_source` leaves the question
    open. These bands are the usual shape of the answer: a satellite lock is
    tens of metres, Wi-Fi triangulation is hundreds, and an IP lookup is
    kilometres.

    A HEURISTIC, AND LABELLED AS ONE. It is used only when nothing better was
    reported, and `unknown` is returned rather than a guess when there is no
    accuracy either -- inventing `gps` would put false confidence on a pin
    somebody may later be disciplined over.
    """
    if accuracy is None:
        return Attendance.LocationSource.UNKNOWN
    try:
        metres = float(accuracy)
    except (TypeError, ValueError):
        return Attendance.LocationSource.UNKNOWN
    if metres <= 100:
        return Attendance.LocationSource.GPS
    if metres <= 2000:
        return Attendance.LocationSource.NETWORK
    return Attendance.LocationSource.IP


def travel_distance_m(record):
    """Metres between where the employee checked in and checked out.

    The brief's "Distance Between Points". Derived rather than stored: both
    coordinates are already on the row, so a column would be a third copy of
    the same fact that could disagree with the other two.

    None when either end is missing -- which is the normal state of a row
    before the employee has gone home.
    """
    if (record.check_in_lat is None or record.check_in_lng is None
            or record.check_out_lat is None or record.check_out_lng is None):
        return None
    return round(haversine_m(record.check_in_lat, record.check_in_lng,
                             record.check_out_lat, record.check_out_lng), 1)


EARTH_RADIUS_M = 6371008.8  # IUGG mean Earth radius


def haversine_m(lat1, lng1, lat2, lng2):
    """Great-circle distance in metres between two WGS-84 points.

    Accurate to well under a metre at city scale, which is far finer than any
    phone GPS fix — no geo database or PostGIS needed.
    """
    p1, p2 = radians(float(lat1)), radians(float(lat2))
    dphi = p2 - p1
    dlambda = radians(float(lng2) - float(lng1))
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(min(1.0, sqrt(a)))


def office_location():
    """(lat, lng, radius_m) of THIS TENANT'S office, or None when unset.

    PER TENANT (Phase APP-ATTENDANCE). This read the deployment-wide
    ATTENDANCE_OFFICE_LAT/LNG, so every customer's geofence was measured
    against one point -- see `attendance.config` for why that was worse than
    being unset. `config.value` falls back to the Django setting, so a
    deployment that has configured only that is unaffected.

    Returning None disables every geofence behaviour downstream, so the
    feature stays opt-in: with no office configured nothing is measured or
    flagged.
    """
    from . import config

    lat = str(config.value("office_lat", "ATTENDANCE_OFFICE_LAT", "") or "").strip()
    lng = str(config.value("office_lng", "ATTENDANCE_OFFICE_LNG", "") or "").strip()
    if not lat or not lng:
        return None
    try:
        radius = float(config.value("office_radius_m",
                                    "ATTENDANCE_OFFICE_RADIUS_M", 150) or 150)
        return float(lat), float(lng), radius
    except (TypeError, ValueError):
        logger.warning("Invalid office latitude/longitude/radius for this "
                       "tenant — geofence disabled")
        return None


def distance_from_office(lat, lng):
    """Metres between a captured coordinate and the office, rounded to 0.1 m.

    None when no office is configured or no coordinate was captured.
    """
    office = office_location()
    if not office or lat is None or lng is None:
        return None
    olat, olng, _ = office
    return round(haversine_m(lat, lng, olat, olng), 1)


def is_within_office(distance_m):
    """True/False against the configured radius; None when it can't be judged."""
    office = office_location()
    if not office or distance_m is None:
        return None
    return distance_m <= office[2]


def location_required():
    """Whether a browser location is mandatory to check in / out.

    PER TENANT, with the deployment setting as the fallback. Default True: a
    check-in with no coordinates is refused so every record carries a
    location. A customer whose staff work on location-less desktops turns it
    off for themselves rather than for the whole platform.
    """
    from . import config

    return config.flag("require_location", "ATTENDANCE_REQUIRE_LOCATION",
                       default=True)


def max_accuracy_m():
    """The widest fix this tenant will accept, in metres.

    A 3,000 m "accuracy" is an IP lookup, not a location. Per tenant because
    a field NGO in the hills and an office with Wi-Fi triangulation have
    genuinely different floors for what counts as knowing where somebody is.
    """
    from . import config

    try:
        return float(config.value("max_accuracy_m",
                                  "ATTENDANCE_MAX_ACCURACY_M", 100) or 100)
    except (TypeError, ValueError):
        return 100.0


def office_name():
    """What this tenant calls its office, for the employee's own screen."""
    from . import config

    return str(config.value("office_name", "ATTENDANCE_OFFICE_NAME", "") or "")


def _google_reverse_geocode(lat, lng, key):
    """Building-level address from the Google Geocoding API. "" on any failure."""
    params = urllib.parse.urlencode({"latlng": f"{lat},{lng}", "key": key})
    url = f"https://maps.googleapis.com/maps/api/geocode/json?{params}"
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.info("google reverse_geocode failed for %s,%s: %s", lat, lng, exc)
        return ""
    if payload.get("status") != "OK":
        logger.info("google reverse_geocode status=%s for %s,%s", payload.get("status"), lat, lng)
        return ""
    results = payload.get("results") or []
    # Google orders results most-specific first, so the first entry is the
    # street address / premise rather than the city or postal area.
    return (results[0].get("formatted_address", "") if results else "")[:255]


def reverse_geocode(lat, lng):
    """Best-effort human-readable address for a coordinate. Returns a short
    address string, or "" on any failure — never raises and never blocks the
    check-in for more than a few seconds.

    Uses the Google Geocoding API when ATTENDANCE_GOOGLE_GEOCODE_KEY is set
    (better POI/building names in Nepal, no rate-limit at check-in rush hour),
    otherwise OpenStreetMap Nominatim. Disable entirely with
    ATTENDANCE_REVERSE_GEOCODE=0; point ATTENDANCE_NOMINATIM_URL at a
    self-hosted instance for higher volume.
    """
    if not getattr(settings, "ATTENDANCE_REVERSE_GEOCODE", True):
        return ""
    google_key = getattr(settings, "ATTENDANCE_GOOGLE_GEOCODE_KEY", "") or ""
    if google_key:
        addr = _google_reverse_geocode(lat, lng, google_key)
        if addr:
            return addr
        # fall through to Nominatim so a bad key/quota doesn't lose the address
    base = getattr(settings, "ATTENDANCE_NOMINATIM_URL", "https://nominatim.openstreetmap.org/reverse")
    # zoom=18 asks Nominatim for building-level detail (house/road) rather than
    # the neighbourhood-level result zoom=16 returns.
    params = urllib.parse.urlencode({
        "lat": str(lat), "lon": str(lng), "format": "jsonv2", "zoom": "18", "addressdetails": "1",
    })
    req = urllib.request.Request(
        f"{base}?{params}",
        headers={"User-Agent": getattr(settings, "ATTENDANCE_GEOCODE_USER_AGENT", "NIF-OfficeManagement/1.0")},
    )
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # network error, timeout, bad JSON — location stays coord-only
        logger.info("reverse_geocode failed for %s,%s: %s", lat, lng, exc)
        return ""
    addr = payload.get("address") or {}
    # Build a building/street-level label, most specific part first:
    #   <building / POI name>, <house-no road>, <locality>, <city>
    # so the result reads like "NIF Office, 12 Thamel Marg, Thamel, Kathmandu"
    # instead of just "Thamel, Kathmandu".
    poi = (payload.get("name") or addr.get("amenity") or addr.get("building")
           or addr.get("office") or addr.get("shop") or addr.get("tourism") or "")
    house = addr.get("house_number") or ""
    road = addr.get("road") or addr.get("pedestrian") or addr.get("footway") or ""
    street = " ".join(p for p in (house, road) if p).strip()
    locality = (addr.get("neighbourhood") or addr.get("suburb") or addr.get("village")
                or addr.get("town") or addr.get("city_district") or "")
    city = addr.get("city") or addr.get("municipality") or addr.get("county") or addr.get("state") or ""
    parts = [p for p in (poi, street, locality, city) if p]
    short = ", ".join(dict.fromkeys(parts))  # dedupe while preserving order
    return (short or payload.get("display_name") or "")[:255]


def _parse_time(raw, default_h, default_m):
    try:
        h, m = (int(x) for x in str(raw).split(":"))
        return time(h, m)
    except (ValueError, AttributeError):
        return time(default_h, default_m)


def office_start_time():
    """When this tenant's day starts. `OrganizationSettings.office_start` is
    a TimeField, so when a tenant has set one it is returned directly rather
    than parsed from a string."""
    from . import config

    own = config.value("office_start", None)
    if own is not None:
        return own
    return _parse_time(getattr(settings, "ATTENDANCE_OFFICE_START", "10:00"),
                       10, 0)


def absent_cutoff_time():
    """Local time after which *today* becomes eligible to be counted Absent.

    Per tenant: a school closing at 16:00 and a hospital running shifts do
    not share a moment at which "has not turned up" becomes Absent.
    """
    from . import config

    own = config.value("absent_cutoff", None)
    if own is not None:
        return own
    return _parse_time(getattr(settings, "ATTENDANCE_ABSENT_CUTOFF", "18:00"),
                       18, 0)


def attendance_tracking_start():
    """Global floor date before which no day is ever Absent (feature go-live).
    None when unset."""
    from . import config

    own = config.value("tracking_start", None)
    if own is not None:
        return own
    raw = (getattr(settings, "ATTENDANCE_TRACKING_START", "") or "").strip()
    if not raw:
        return None
    try:
        y, m, d = (int(x) for x in raw.split("-"))
        return _date(y, m, d)
    except (ValueError, AttributeError):
        return None


def absent_floor(employee):
    """The earliest date an absence can EVER be counted for this employee.

    Hard lower bound = the ACCOUNT REGISTRATION date (User.date_joined): attendance
    cannot exist before the employee's account existed in the system. A backdated
    ``date_of_joining`` can only push this LATER, never earlier — so we take the
    later of (registration, date_of_joining). The optional global tracking-start
    pushes it later still. A day before this floor is Not Applicable — never Absent.
    """
    dj = getattr(employee, "date_joined", None)
    registration = timezone.localtime(dj).date() if dj else None
    join = getattr(employee, "date_of_joining", None)

    if registration and join:
        floor = max(registration, join)   # never earlier than registration
    else:
        floor = registration or join

    start = attendance_tracking_start()
    if floor and start:
        return max(floor, start)
    return floor or start  # None only if the account has no registration date


def _absent_day_reached(d, today, now=None):
    """True when day `d` is 'past enough' to judge as Absent: any strictly-past
    day, or today only after the check-in window (cut-off) has closed. Future
    days are never reached."""
    if d < today:
        return True
    if d == today:
        now = now or now_local()
        return now.timetz().replace(tzinfo=None) >= absent_cutoff_time()
    return False


def resolve_day_status(*, record, is_holiday_day, is_leave_day, d, floor, today, now=None):
    """Canonical per-day attendance status shared by the dashboard, calendar,
    history and reports/PDFs. Returns an Attendance.Status value, or None when
    the day is Not Applicable (excluded — NOT Absent, NOT counted).

    A day is Absent ONLY when it is a working day with no check-in, on/after the
    employee's tracking floor, and already 'past enough' (see _absent_day_reached).
    """
    if record and record.check_in:
        return record.status  # a real check-in is ground truth, always shown
    # Nothing of ANY kind exists before the account was registered in the system
    # (checked before holiday/leave so pre-registration days render as Not Applicable).
    if floor is None or d < floor:
        return None
    if is_holiday_day:
        return Attendance.Status.HOLIDAY
    if is_leave_day:
        return Attendance.Status.ON_LEAVE
    if record:  # manual HR row without a check-in — respect the stored status
        return record.status
    if not _absent_day_reached(d, today, now):
        return None  # today mid-day, or a future date — Not Applicable
    return Attendance.Status.ABSENT


def full_day_hours():
    from . import config

    return Decimal(str(config.value("full_day_hours",
                                    "ATTENDANCE_FULL_DAY_HOURS", 8)))


def half_day_hours():
    from . import config

    return Decimal(str(config.value("half_day_hours",
                                    "ATTENDANCE_HALF_DAY_HOURS", 5)))


def now_local():
    """Current time in the configured (Asia/Kathmandu) timezone."""
    return timezone.localtime(timezone.now())


def is_holiday(d):
    """Saturday (Nepal weekly holiday) or an active public holiday."""
    from leaves.models import Holiday

    if d.weekday() == 5:
        return True
    return Holiday.objects.filter(is_active=True, date=d).exists()


def holiday_name(d):
    from leaves.models import Holiday

    if d.weekday() == 5:
        return "Saturday (Weekly Holiday)"
    h = Holiday.objects.filter(is_active=True, date=d).values_list("name", flat=True).first()
    return h


def has_approved_leave(employee, d):
    from leaves.models import Leave

    return Leave.objects.filter(
        user=employee, status=Leave.Status.APPROVED, is_deleted=False,
        start_date__lte=d, end_date__gte=d,
    ).exists()


def compute_working_hours(check_in, check_out):
    if not check_in or not check_out or check_out <= check_in:
        return ZERO
    hours = Decimal((check_out - check_in).total_seconds()) / Decimal(3600)
    return hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def is_late(check_in_local):
    """DEPRECATED — kept for backwards compatibility, no longer used internally.

    Hardcodes the global office-start rule and is therefore blind to per-employee
    policies, shifts, and the arrival boundaries added in Phase 8.1. Use
    ``attendance.policy.resolver.status_boundaries()`` with
    ``attendance.policy.engine.late_minutes()`` instead.
    """
    return check_in_local.timetz().replace(tzinfo=None) > office_start_time()


def recompute_status(record):
    """Set status, hours, overtime, lateness and comp-off on a record.

    The single seam every write path goes through — browser check-in/out,
    biometric derivation and biometric revert — which is why making this one
    function policy-aware (Phase 8) made all of them policy-aware at once.

    Delegates to ``attendance.policy.engine``. With no policy rows configured
    the engine resolves the settings fallback, whose values are exactly the
    ``ATTENDANCE_*`` defaults, so behaviour is unchanged from Phase 7.
    """
    from .policy import engine

    return engine.evaluate(record)


def effective_status(employee, d, record=None):
    """Status for a single day, layering auto-integration over any stored row.

    Priority: real check-in > Holiday > Approved Leave > Absent (only a past-enough
    working day on/after the employee's tracking floor) > Not Applicable (None).
    Delegates to resolve_day_status so every view agrees.
    """
    return resolve_day_status(
        record=record,
        is_holiday_day=is_holiday(d),
        is_leave_day=has_approved_leave(employee, d),
        d=d,
        floor=absent_floor(employee),
        today=now_local().date(),
    )


def month_days(year, month):
    d = _date(year, month, 1)
    while d.month == month:
        yield d
        d += timedelta(days=1)


def build_calendar(employee, year, month):
    """Per-day status for an employee's month, plus summary counts."""
    from config.nepali_dates import to_bs

    records = {a.date: a for a in Attendance.objects.filter(
        employee=employee, date__year=year, date__month=month)}
    days, counts = [], {s.value: 0 for s in Attendance.Status}
    for d in month_days(year, month):
        rec = records.get(d)
        status = effective_status(employee, d, rec)
        if status is None:
            continue
        counts[status] = counts.get(status, 0) + 1
        days.append({
            "date": d.isoformat(),
            "date_bs": to_bs(d),
            "status": status,
            "check_in": rec.check_in.isoformat() if rec and rec.check_in else None,
            "check_out": rec.check_out.isoformat() if rec and rec.check_out else None,
            "working_hours": str(rec.working_hours) if rec else "0.00",
            "holiday_name": holiday_name(d) if status == Attendance.Status.HOLIDAY else None,
        })
    return {"year": year, "month": month, "days": days, "summary": counts}
