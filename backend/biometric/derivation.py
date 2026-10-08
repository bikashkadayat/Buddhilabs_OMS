"""Attendance derivation engine.

Turns immutable raw punches into the business record the rest of the system
already reads:

    AttendancePunch (many per day)  ->  attendance.Attendance (one per day)

Design constraints this file exists to honour:

* ``attendance.services.resolve_day_status`` is NOT modified. A derived row
  carries a real ``check_in``, so it wins over Holiday/Leave through that
  resolver's existing first branch — no precedence change was needed anywhere.
* Raw punches are never mutated except for their ``is_processed`` bookkeeping.
* An HR correction is never overwritten.
* Every operation is idempotent: deriving a day once or a hundred times leaves
  the database in the same state.

Precedence for authoring a row:  HR > Biometric > App.

MIXED MODE (App + Biometric). When a day has both an employee's own app
check-in and device punches, the row takes the EARLIEST VALID CHECK-IN and the
LATEST VALID CHECK-OUT from either source. Neither source is discarded: the app
times stay in ``browser_check_in``/``browser_check_out``, every punch stays in
``AttendancePunch``, ``check_in_source``/``check_out_source`` record which one
won each end, and a change in the merged result is written to the audit log.
In Biometric-only mode the device times alone are used (the app times are
still snapshotted, so switching modes later loses nothing).
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from attendance import services as attendance_services
from attendance.models import Attendance
from attendance.policy.resolver import policy_cache

from . import events
from .models import ENTRY_PUNCHES, EXIT_PUNCHES, AttendancePunch

logger = logging.getLogger(__name__)


class NaiveTimestampError(ValueError):
    """A punch arrived without timezone information.

    Devices report naive local time. With USE_TZ=True Django would read that as
    UTC, putting every Asia/Kathmandu punch 5h45m out — so naive timestamps are
    rejected at every boundary rather than silently coerced.
    """


def require_aware(dt, context=""):
    if dt is None:
        return None
    if timezone.is_naive(dt):
        raise NaiveTimestampError(
            f"Naive timestamp {dt!r}{' for ' + context if context else ''}. "
            "Punch timestamps must carry an explicit UTC offset."
        )
    return dt


def _is_hr_locked(record):
    """HR corrections outrank the device and must survive any re-derivation.

    Both fields are checked because ``marked_by`` predates ``source``: a row
    written by the existing ManualAttendanceView before this migration ran is
    identified by ``marked_by`` alone.
    """
    if record is None:
        return False
    return (record.source == Attendance.Source.HR
            or record.marked_by == Attendance.MarkedBy.HR)


def select_boundary_punches(punches):
    """Pick the day's check-in and check-out from its punches.

    Entry codes are 0/3/4 (check_in, break_in, overtime_in) and exit codes are
    1/2/5 (check_out, break_out, overtime_out).

    Falls back to the earliest/latest punch of any type when a day has only one
    class of punch. Without that fallback a forgotten punch-in would render a
    fully worked day as Absent, which is worse than a slightly imprecise span.

    Overtime punches deliberately extend the span — the live device has recorded
    1,523 overtime_in and 866 overtime_out events, so excluding them would
    understate hours. Break punches are counted but NOT deducted; net-of-breaks
    is a policy decision that belongs to the Phase 9 rule engine.
    """
    ordered = sorted(punches, key=lambda p: p.timestamp)
    if not ordered:
        return None, None

    entries = [p for p in ordered if p.punch in ENTRY_PUNCHES]
    exits = [p for p in ordered if p.punch in EXIT_PUNCHES]

    first = entries[0] if entries else ordered[0]
    last = exits[-1] if exits else ordered[-1]

    # A single punch is an arrival, not a zero-length day: leave check_out unset
    # so recompute_status() reports Present/Late with 0.00 hours, exactly as a
    # browser check-in without a check-out already does.
    if last.timestamp <= first.timestamp:
        return first, None
    return first, last


# A punch stamped further ahead of the server clock than this is not a real
# punch yet -- it is a terminal whose clock has glitched (the main-gate device
# holds seven records dated 2033). "Earliest VALID check-in / latest VALID
# check-out" means such a punch can never become a day's check-out.
FUTURE_PUNCH_TOLERANCE = timedelta(minutes=5)


def valid_punches(punches, now=None):
    """The punches that may set a day's boundaries."""
    limit = (now or timezone.now()) + FUTURE_PUNCH_TOLERANCE
    return [p for p in punches if p.timestamp <= limit]


def _snapshot_browser_times(record):
    """Preserve an employee's own check-in before device punches are merged in."""
    if record.source in Attendance.SELF_SOURCES and record.browser_check_in is None:
        record.browser_check_in = record.check_in
        record.browser_check_out = record.check_out
        record.app_source = record.source


def merge_day_times(punches, app_in, app_out, app_source, *, include_app=True):
    """The merge rule, as a pure function so it can be tested on its own.

    Returns ``(check_in, check_in_source, check_out, check_out_source)``.

    * check-in  = the earliest of the first device punch and the app check-in;
    * check-out = the latest of the last device punch and the app check-out;
    * a lone device punch that comes AFTER an earlier app check-in is that
      day's departure, not a second arrival -- otherwise "checked in on the
      phone at 9, punched out at the gate at 6" would read as no check-out.
    """
    biometric = Attendance.Source.BIOMETRIC
    first, last = select_boundary_punches(punches)
    check_in, in_source = (first.timestamp, biometric) if first else (None, "")
    check_out, out_source = (last.timestamp, biometric) if last else (None, "")

    if include_app:
        app_source = app_source or Attendance.Source.BROWSER
        if app_in is not None and (check_in is None or app_in < check_in):
            check_in, in_source = app_in, app_source
        if app_out is not None and (check_out is None or app_out > check_out):
            check_out, out_source = app_out, app_source
        if check_out is None and punches and in_source != biometric:
            latest = max(p.timestamp for p in punches)
            if latest > check_in:
                check_out, out_source = latest, biometric

    if check_out is not None and check_in is not None and check_out <= check_in:
        check_out, out_source = None, ""
    return check_in, in_source, check_out, out_source


def _audit_merge(record, before, app_in, app_out, punches):
    """Write the merge to the audit log -- only when it changed something.

    Derivation runs on every sync and is idempotent, so logging every run
    would bury the one entry that matters under hundreds that say nothing.
    """
    after = (record.check_in, record.check_out)
    if after == before:
        return
    try:
        from audit.models import AuditLog
        from audit.services import log_action

        def iso(value):
            return value.isoformat() if value else None

        log_action(None, AuditLog.Action.UPDATE, instance=record, changes={
            "event": "ATTENDANCE_SOURCES_MERGED",
            "rule": "earliest valid check-in, latest valid check-out",
            "app": {"source": record.app_source, "check_in": iso(app_in),
                    "check_out": iso(app_out)},
            "biometric": {"first_punch": iso(punches[0].timestamp),
                          "last_punch": iso(punches[-1].timestamp),
                          "punch_count": len(punches)},
            "before": {"check_in": iso(before[0]), "check_out": iso(before[1])},
            "after": {"check_in": iso(record.check_in), "check_in_source": record.check_in_source,
                      "check_out": iso(record.check_out), "check_out_source": record.check_out_source},
        })
    except Exception:                                   # noqa: BLE001
        # The audit trail must never be the reason attendance fails to derive.
        logger.warning("could not audit attendance merge for %s %s",
                       record.employee_id, record.date, exc_info=True)


def _revert(record):
    """A day lost all its punches — undo the biometric derivation.

    Happens when a mapping is unmapped or remapped. Rows the engine created
    outright are deleted; rows that started as a browser check-in are restored
    from the snapshot. HR rows are never touched.
    """
    if record is None or _is_hr_locked(record):
        return record

    if record.source != Attendance.Source.BIOMETRIC:
        return record

    if record.browser_check_in is None:
        logger.info("Reverting derived attendance: deleting %s %s",
                    record.employee_id, record.date)
        record.delete()
        return None

    app_source = record.app_source or Attendance.Source.BROWSER
    record.check_in = record.browser_check_in
    record.check_out = record.browser_check_out
    record.browser_check_in = None
    record.browser_check_out = None
    record.source = app_source
    record.app_source = ""
    record.check_in_source = app_source
    record.check_out_source = app_source if record.check_out else ""
    record.marked_by = Attendance.MarkedBy.SELF
    record.first_punch_at = None
    record.last_punch_at = None
    record.punch_count = 0
    record.device = None
    attendance_services.recompute_status(record)
    record.save()
    logger.info("Reverted derived attendance to browser: %s %s",
                record.employee_id, record.date)
    return record


@transaction.atomic
def derive_daily_attendance(user, day, force=False):
    """Build (or rebuild) one employee-day from that day's punches.

    Idempotent: driven entirely by the punches that exist right now, never by
    the record's previous contents. ``force=True`` overrides HR protection and
    is only reachable from ``rederive_attendance --force``.

    Returns the Attendance row, or None if it was reverted away.
    """
    # MODE GATE (app-attendance phase). A tenant set to app-only has decided
    # its attendance comes from the application; deriving device punches into
    # attendance anyway would overwrite the employee's own check-in -- which
    # is exactly what `browser_check_in` exists to make recoverable, and a
    # recovery nobody should need.
    #
    # The PUNCHES ARE STILL STORED AND STILL MARKED PROCESSED. They are raw
    # evidence from hardware the customer owns, and discarding them because
    # of a settings flag would lose data that cannot be re-read off the
    # device later. Only the derivation is skipped.
    from attendance import config as attendance_config

    if not attendance_config.biometric_allowed():
        logger.debug("biometric derivation skipped for %s on %s: this "
                     "organization is set to app-only attendance", user, day)
        _mark_processed(list(AttendancePunch.objects
                             .filter(user=user, local_date=day)))
        return Attendance.objects.filter(employee=user, date=day).first()

    punches = list(
        AttendancePunch.objects.select_for_update()
        .filter(user=user, local_date=day)
        .order_by("timestamp")
    )
    record = Attendance.objects.select_for_update().filter(employee=user, date=day).first()

    if _is_hr_locked(record) and not force:
        # HR wins on times and status; punch metadata is still recorded so the
        # raw evidence behind the correction stays visible.
        if punches:
            _apply_punch_metadata(record, punches)
            record.save(update_fields=["first_punch_at", "last_punch_at", "punch_count",
                                       "device", "updated_at"])
        _mark_processed(punches)
        return record

    for p in punches:
        require_aware(p.timestamp, context=f"user {user.pk} on {day}")
    usable = valid_punches(punches)
    if not usable:
        # Nothing but future-dated punches: a terminal clock fault. They stay
        # in the raw log as evidence; they do not make an attendance day.
        _mark_processed(punches)
        return _revert(record)

    before = (None, None)
    if record is None:
        record = Attendance(employee=user, date=day)
    else:
        before = (record.check_in, record.check_out)
        _snapshot_browser_times(record)

    include_app = attendance_config.attendance_mode() == attendance_config.MODE_BOTH
    (record.check_in, record.check_in_source,
     record.check_out, record.check_out_source) = merge_day_times(
        usable, record.browser_check_in, record.browser_check_out,
        record.app_source, include_app=include_app)
    record.source = Attendance.Source.BIOMETRIC
    record.marked_by = Attendance.MarkedBy.SYSTEM
    _apply_punch_metadata(record, punches)

    # Reuse the existing status/hours rules verbatim — the engine decides WHICH
    # times a day has, never what those times mean.
    attendance_services.recompute_status(record)
    record.save()
    if include_app and record.browser_check_in is not None:
        _audit_merge(record, before, record.browser_check_in,
                     record.browser_check_out, usable)

    _mark_processed(punches)
    # Queued for after commit, and best-effort — a broken channel layer must
    # never fail a derivation. See biometric/events.py.
    events.attendance_updated(record)
    return record


def _apply_punch_metadata(record, punches):
    record.first_punch_at = punches[0].timestamp
    record.last_punch_at = punches[-1].timestamp
    record.punch_count = len(punches)
    record.device = punches[-1].device


def _mark_processed(punches):
    ids = [p.pk for p in punches if not p.is_processed]
    if ids:
        AttendancePunch.objects.filter(pk__in=ids).update(
            is_processed=True, processed_at=timezone.now())


def pending_days(queryset=None):
    """Distinct (user, local_date) pairs awaiting derivation.

    Unmapped punches are excluded on purpose — they are blocked on HR making a
    mapping decision, not on this engine, and would otherwise look like a stuck
    queue forever.
    """
    qs = queryset if queryset is not None else AttendancePunch.objects.all()
    return list(
        qs.filter(is_processed=False, user__isnull=False)
          .values_list("user_id", "local_date")
          .order_by("user_id", "local_date")
          .distinct()
    )


def unmapped_backlog_count(queryset=None):
    qs = queryset if queryset is not None else AttendancePunch.objects.all()
    return qs.filter(is_processed=False, user__isnull=True).count()


def process_unprocessed_punches(limit_days=None):
    """Derive every employee-day that has unprocessed punches.

    Called inline after ingest and again by the cron sweep. Returns a summary
    dict for the caller to log or print.
    """
    from django.contrib.auth import get_user_model

    pairs = pending_days()
    if limit_days is not None:
        pairs = pairs[:limit_days]

    users = get_user_model().objects.in_bulk({uid for uid, _ in pairs})
    derived = reverted = failed = 0
    # Memoise policy/shift resolution for the sweep: without it every
    # employee-day re-resolves the same assignments (Phase 8 risk R8).
    with policy_cache():
        for user_id, day in pairs:
            user = users.get(user_id)
            if user is None:  # user deleted between the query and now
                continue
            try:
                result = derive_daily_attendance(user, day)
            except Exception:
                failed += 1
                logger.exception("Derivation failed for user=%s date=%s", user_id, day)
                continue
            derived += 1 if result is not None else 0
            reverted += 1 if result is None else 0

    summary = {
        "days_processed": len(pairs),
        "derived": derived,
        "reverted": reverted,
        "failed": failed,
        "unmapped_backlog": unmapped_backlog_count(),
    }
    logger.info("Punch processing summary: %s", summary)
    return summary


def rederive_range(start, end, users=None, force=False):
    """Rebuild every employee-day in a date range from raw punches.

    The audit tool: run it after a mapping change, a backfill, or any time the
    derived rows are in doubt. Reads punches regardless of ``is_processed``.
    """
    from django.contrib.auth import get_user_model

    qs = AttendancePunch.objects.filter(
        user__isnull=False, local_date__gte=start, local_date__lte=end)
    if users:
        qs = qs.filter(user__in=users)

    pairs = list(qs.values_list("user_id", "local_date").order_by("user_id", "local_date").distinct())
    user_map = get_user_model().objects.in_bulk({uid for uid, _ in pairs})

    derived = reverted = failed = 0
    with policy_cache():
        for user_id, day in pairs:
            user = user_map.get(user_id)
            if user is None:
                continue
            try:
                result = derive_daily_attendance(user, day, force=force)
            except Exception:
                failed += 1
                logger.exception("Re-derivation failed for user=%s date=%s", user_id, day)
                continue
            derived += 1 if result is not None else 0
            reverted += 1 if result is None else 0
    return {"days": len(pairs), "derived": derived, "reverted": reverted, "failed": failed}
