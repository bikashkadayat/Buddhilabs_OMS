"""Pull attendance off the terminal and into the OMS, in-process.

Phase 12 requires the biometric device to be part of *this* system rather than
fed by a separate service, so this is the collector: it opens a read-only
session to the terminal, reads the roster and the attendance log, and hands both
to the same ``ingest`` functions the HTTP endpoint uses.

Reusing ``ingest_punches``/``ingest_roster`` rather than writing rows here is
the whole point. Dedup, unmapped handling, the sync log, device health rollups,
the live broadcast and attendance derivation all already live there and are
already tested. A second write path would drift from the first, and the drift
would show up as attendance that exists via one route and not the other.

IDENTITY
--------
The terminal's user id travels from the wire to ``AttendancePunch`` and
``BiometricEmployee`` untouched. Nothing here generates, renumbers or maps an
id — ``employee_id`` comes out of ``zk_client.parse_users`` and goes straight
into ingest as ``device_user_id``. The device is the source of truth; this
module is a pipe, and that is deliberate.
"""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.utils import timezone

from . import zk_client
from .ingest import ingest_punches, ingest_roster
from .models import AttendancePunch, DeviceSyncLog
from .services import touch_device

logger = logging.getLogger(__name__)

DEFAULT_CHUNK = 500
# Beyond this the terminal's clock is far enough out that imported punches would
# land on the wrong day, which is worse than not importing them.
CLOCK_DRIFT_ALARM_SECONDS = 300
# Slack on the high-water mark, in days. Absorbs clock drift and any punch that
# reaches the terminal's log out of order. Dedup makes re-reading these free.
HIGH_WATER_OVERLAP_DAYS = 2


class CollectorError(Exception):
    """The sync could not be completed."""


def device_zone(device):
    try:
        return ZoneInfo(device.device_timezone or settings.TIME_ZONE)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CollectorError(
            f"{device.label} has device_timezone={device.device_timezone!r}, "
            f"which is not a known timezone. Every punch's date depends on "
            f"this, so it is a hard error rather than a fallback.") from exc


def localise(naive, zone):
    """Attach the terminal's zone to its naive wall-clock reading.

    ``fold=0`` is explicit rather than incidental: if a terminal ever sits in a
    DST zone, an hour repeats, and the default would silently pick one. Choosing
    the first occurrence at least makes the behaviour stated and stable.
    Asia/Kathmandu has no DST, so today this never fires.
    """
    return naive.replace(tzinfo=zone, fold=0)


def measure_drift(client, zone):
    """Terminal clock minus server clock, in seconds. ``None`` if unreadable."""
    try:
        reported = client.device_time()
    except zk_client.ZKError as exc:
        logger.warning("Could not read the clock: %s", exc)
        return None
    return int((localise(reported, zone) - timezone.now()).total_seconds())


def high_water_floor(device, zone):
    """The oldest local date the recurring sync still bothers to consider.

    The terminal keeps its whole log, so every run reads every record it has
    ever stored. Ingest would discard the old ones correctly — dedup is what
    makes re-reading safe — but doing so every five minutes means re-scanning
    years of punches forever, and writing a sync-log row per chunk while doing
    it. The cost grows without bound for no benefit.

    So the recurring sync starts from the newest punch already stored, less
    ``HIGH_WATER_OVERLAP_DAYS``. The overlap is not decoration: a terminal whose
    clock is behind the server's can emit a punch timestamped earlier than one
    already ingested, and a floor with no slack would step straight over it.
    Dedup makes the overlap free.

    An explicit ``--since`` overrides this entirely — a backfill means "read the
    window I named", not "read what you have not seen".
    """
    if device.last_punch_at is None:
        return None

    high_water = timezone.localtime(device.last_punch_at, zone).date()

    # Clamp to today. A terminal whose clock has glitched writes records dated
    # years ahead — 192.168.77.201 holds seven stamped 2033-02-22 — and those
    # are real rows, so they legitimately become `last_punch_at`. Left
    # unclamped, the resume cursor jumps to 2033 and the recurring sync spends
    # the next seven years looking at a window that contains nothing.
    #
    # The failure is silent and total: the job succeeds every five minutes,
    # reports no error, and never collects another punch. Observed on the real
    # device, where the window came out as "2033-02-20 .. today" and skipped
    # 10,895 records.
    #
    # The cursor can never usefully be in the future, so capping it costs
    # nothing and removes the whole failure mode.
    today = timezone.localdate()
    if high_water > today:
        logger.warning(
            "%s reports a newest punch dated %s, which is in the future — "
            "the device clock has glitched. Resuming from today instead.",
            device.label, high_water)
        high_water = today

    return high_water - timedelta(days=HIGH_WATER_OVERLAP_DAYS)


def _window_filter(records, zone, since, until):
    """Keep punches whose *local* date falls inside the requested window.

    Filtering on local date, not UTC timestamp, because the window an operator
    types is a working-days window. With a +05:45 offset, a UTC comparison would
    drop the first morning and admit part of the day after.
    """
    kept, before, after = [], 0, 0
    for record in records:
        local_date = record["timestamp"].date()
        if since and local_date < since:
            before += 1
            continue
        if until and local_date > until:
            after += 1
            continue
        kept.append({**record, "timestamp": localise(record["timestamp"], zone)})
    return kept, before, after


def _chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def client_for(driver):
    """Pick the transport. ``None``/``"native"`` uses the built-in client.

    ``"pyzk"`` exists as insurance: the native client has never met the real
    firmware, and the device's owner already has pyzk talking to it. If the
    built-in reader stumbles on a firmware quirk, the import switches driver
    rather than stopping.
    """
    if driver in (None, "", "native"):
        return zk_client.ZKReadOnlyClient
    if driver == "pyzk":
        from .pyzk_driver import PyzkReadOnlyClient
        return PyzkReadOnlyClient
    raise CollectorError(f"unknown driver {driver!r} — use 'native' or 'pyzk'")


def sync_device(device, *, host=None, comm_key=None, since=None, until=None,
                include_roster=True, include_punches=True, dry_run=False,
                source=None, chunk_size=None, derive=True, timeout=None,
                driver=None, identity_check=None):
    """Read the terminal and ingest what it holds. Returns a summary dict.

    ``dry_run`` reads everything and writes nothing — the mode to use before a
    production import, because it produces the exact counts the import will
    produce without touching a row.

    ``identity_check(device, serial)`` is called with the serial the terminal
    reports, BEFORE either table is read; raising from it aborts the sync with
    nothing read and nothing written. ``devices.run_pull_sync`` uses it to
    refuse a terminal that is not the one this device row registered.
    """
    zone = device_zone(device)
    address = host or device.host
    if not address:
        raise CollectorError(
            f"{device.label} has no host recorded. Set BiometricDevice.host to "
            f"the terminal's LAN address, or pass --host.")

    chunk_size = chunk_size or getattr(settings, "BIOMETRIC_MAX_BATCH", DEFAULT_CHUNK)
    comm_key = comm_key if comm_key is not None else getattr(
        settings, "BIOMETRIC_DEVICE_COMM_KEY", 0)
    timeout = timeout or getattr(settings, "BIOMETRIC_DEVICE_TIMEOUT", zk_client.DEFAULT_TIMEOUT)

    # No explicit window means the recurring sync, which starts from where the
    # last one finished rather than re-reading years of log every five minutes.
    floor = None if since else high_water_floor(device, zone)
    effective_since = since or floor

    summary = {
        "device": device.label, "host": address, "dry_run": dry_run,
        "window": {"since": effective_since.isoformat() if effective_since else None,
                   "until": until.isoformat() if until else None,
                   "from_high_water_mark": since is None and floor is not None},
        "roster": None, "punches": None, "clock": {}, "warnings": [],
    }

    transport = client_for(driver)
    with transport(address, device.port, timeout=timeout,
                   comm_key=comm_key) as client:
        summary["sizes"] = client.sizes()

        if identity_check is not None:
            info = client.device_info() if hasattr(client, "device_info") else {}
            summary["device_info"] = info
            if not dry_run:
                identity_check(device, info.get("serial_number"))

        drift = measure_drift(client, zone)
        summary["clock"] = {"drift_seconds": drift}
        if drift is not None and abs(drift) > CLOCK_DRIFT_ALARM_SECONDS:
            summary["warnings"].append(
                f"The terminal's clock is {drift}s from the server's. Punches "
                f"near midnight will be filed on the wrong day until it is "
                f"corrected on the device.")

        roster = client.users() if include_roster else None
        raw_punches = client.attendance() if include_punches else None

    # The session is closed before anything is written: a long transaction must
    # not hold a terminal connection open, and the terminal's pool is small.
    if roster is not None:
        summary["roster"] = _apply_roster(device, roster, dry_run=dry_run)

    if raw_punches is not None:
        summary["punches"] = _apply_punches(
            device, raw_punches, zone=zone, since=effective_since, until=until,
            dry_run=dry_run, source=source, chunk_size=chunk_size, derive=derive)

    if not dry_run:
        touch_device(device, seen=True, synced=True, drift_seconds=drift)

    return summary


def _apply_roster(device, roster, *, dry_run):
    detail = {"read": len(roster),
              "device_user_ids": [r["employee_id"] for r in roster]}
    if dry_run:
        detail["dry_run"] = "no rows written"
        return detail
    detail.update(ingest_roster(device, roster))
    return detail


def _apply_punches(device, raw, *, zone, since, until, dry_run, source,
                   chunk_size, derive):
    punches, before, after = _window_filter(raw, zone, since, until)
    detail = {
        "read": len(raw), "in_window": len(punches),
        "skipped_before_window": before, "skipped_after_window": after,
        "device_user_ids": sorted({p["employee_id"] for p in punches}),
    }
    if punches:
        detail["earliest"] = min(p["timestamp"] for p in punches).isoformat()
        detail["latest"] = max(p["timestamp"] for p in punches).isoformat()

    if dry_run:
        detail["dry_run"] = "no rows written"
        return detail

    # HISTORY, not LIVE, even on the 5-minute run: nothing here is streamed.
    # Every punch is read out of the terminal's stored log, which is exactly
    # what HISTORY means. Labelling a pulled punch LIVE would misdescribe how it
    # arrived, and `source` exists to answer precisely that question.
    source = source or AttendancePunch.Source.HISTORY

    # Nothing new: return without writing a sync log. The recurring job runs 288
    # times a day and an empty-batch row for each would bury the batches that
    # actually did something. `touch_device` still records that the terminal
    # answered, which is what the offline detector reads.
    if not punches:
        detail.update(received=0, created=0, duplicate=0, unmapped=0)
        return detail

    totals = {"received": 0, "created": 0, "duplicate": 0, "unmapped": 0}
    for batch in _chunks(punches, chunk_size):
        result = ingest_punches(
            device, batch, source=source,
            sync_type=DeviceSyncLog.SyncType.HISTORY, derive=derive)
        for field in totals:
            totals[field] += result.get(field, 0)
    detail.update(totals)
    if not derive:
        detail["derivation"] = (
            "skipped — run `rederive_attendance` before trusting any report "
            "for this window")
    return detail


def parse_day(value):
    """``YYYY-MM-DD`` -> date, with an error an operator can act on."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise CollectorError(f"{value!r} is not a date in YYYY-MM-DD form") from exc
