"""Scheduled-job heartbeats.

Phase 11 audit finding M5. ``health_views.CRON_EVENTS`` listed four events while
the crontab ran eleven jobs, so seven of them -- including the nightly backup --
reported nothing at all. A job that silently stops looked identical to a job
with nothing to do.

Every job now writes a ``SYSTEM_<JOB>_OK`` audit entry on success and a
``SYSTEM_<JOB>_FAILED`` entry on failure, and each is registered here with the
interval it is expected to run at. That turns "last run" into "last run, and is
that late" -- which is the only form of the question monitoring can act on.

The registry is the single source for the monitoring dashboard, the alert rules
and the health endpoint, so a job added to the crontab without an entry here
shows up as unregistered rather than silently unmonitored.
"""
import logging
from contextlib import contextmanager

from django.utils import timezone

logger = logging.getLogger(__name__)

# Job key -> (human label, expected interval in minutes, is it critical).
# `critical` marks the jobs whose silence is an incident rather than a nuisance.
CRON_JOBS = {
    # One entry, not two: the weekly and monthly cron lines invoke the SAME
    # command with different arguments, so they share a heartbeat and either one
    # keeps it fresh. Two keys would show a permanent amber on whichever ran
    # less often.
    "RECOMPUTE_SUMMARIES": ("Leave summaries (weekly + monthly)", 1440, False),
    "INTEGRITY_AUDIT": ("Data integrity audit", 1440, False),
    "SCHEDULED_REPORTS": ("Scheduled report delivery", 60, False),
    "PURGE_REPORTS": ("Expired report purge", 1440, False),
    "PURGE_DRAFTS": ("Expired autosave draft purge", 1440, False),
    "WEEKLY_DIGEST": ("Weekly digest email", 10080, False),
    "RECONCILE_NOTIFICATIONS": ("Notification reconciliation", 1440, False),
    # Critical and short: this is the only thing that moves attendance from the
    # terminal into the OMS. If it stops, nobody is recorded as present and the
    # dashboards look plausible right up until payroll.
    # Written by `device_sync_due` (every minute, all tenants), and by the
    # older `device_sync` / `device_sync_loop` when an operator runs them.
    "DEVICE_SYNC": ("Biometric device collection", 10, True),
    "PROCESS_PUNCHES": ("Punch derivation safety net", 10, True),
    "CHECK_DEVICE_HEALTH": ("Device health sweep", 5, True),
    "REAP_STUCK_REPORTS": ("Stuck report reaper", 15, False),
    "CHECK_ALERTS": ("Alert evaluation", 5, True),
    "BACKUP": ("Database backup", 1440, True),
    "BACKUP_MEDIA": ("Media backup", 1440, True),
    "BACKUP_VERIFIED": ("Backup restore verification", 1440, True),
    "YEAR_END": ("Year-end processing", 525600, False),
    # Phase T4. Daily. Critical, because its failure is SILENT in the worst
    # way: nobody gets chased, no escalation climbs, and the first symptom
    # is a deadline nobody was reminded about. Everything else in this
    # registry fails visibly; this one fails by nothing happening.
    "TASK_REMINDERS": ("Task reminders and escalation", 1440, True),
    # Phase ASSET-LIFECYCLE-DISPOSAL. Daily. Not critical: a missed night delays
    # a warranty or AMC alert by a day inside a 60-day window, and the dashboard
    # tiles show the same assets whether or not the job ran.
    "ASSET_LIFECYCLE_ALERTS": ("Asset warranty, AMC and end-of-life alerts", 1440, False),
    # Phase T5. Daily. Not critical: a missed snapshot loses one day of
    # evidence granularity, which is recoverable with --date. A missed
    # reminder is not.
    "TASK_EVIDENCE_SNAPSHOT": ("Task evidence snapshot", 1440, False),
    # Phase RELEASE. Daily. Not critical in the way TASK_REMINDERS is: a missed
    # night delays a nudge about an ADVISORY deadline, and the event
    # notifications that tell somebody it is their turn are not routed through
    # here. A missed task reminder means nobody is chased about a hard due date;
    # a missed appraisal reminder means somebody is nudged tomorrow instead.
    "APPRAISAL_REMINDERS": ("Appraisal deadline reminders", 1440, False),
    # Customer Success 2.0. SLA alerts are critical: their whole point is
    # that an overdue ticket no longer goes unnoticed, and a silent alert job
    # puts that straight back. The daily customer signals are not -- a missed
    # day delays an outreach task by a day.
    "SUPPORT_SLA_ALERTS": ("Support SLA alerts", 15, True),
    "SUCCESS_CUSTOMERS": ("Customer success signals", 1440, False),
}

# How late a job may be before it is amber / red, as a multiple of its interval.
AMBER_MULTIPLIER = 1.5
RED_MULTIPLIER = 2.0


def event_name(job, suffix="OK"):
    return f"SYSTEM_{job}_{suffix}"


def record(job, *, ok=True, detail=None, actor=None):
    """Write the heartbeat. Never raises.

    A monitoring write must not be able to fail the job it is monitoring -- that
    would turn an observability feature into an availability risk.
    """
    from audit.models import AuditLog
    from audit.services import log_action

    try:
        changes = {"event": event_name(job, "OK" if ok else "FAILED"), "job": job}
        if detail:
            changes["detail"] = detail if isinstance(detail, dict) else str(detail)[:500]
        return log_action(actor, AuditLog.Action.OTHER, changes=changes)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to record heartbeat for %s", job)
        return None


@contextmanager
def heartbeat(job, detail=None):
    """Wrap a management command's work.

    Success writes OK; an exception writes FAILED **and re-raises**, so the job
    still fails loudly for cron while the failure is also durable in the audit
    log. Swallowing here would make a broken job look healthy, which is the
    exact failure this module exists to prevent.
    """
    try:
        yield
    except Exception as exc:  # noqa: BLE001
        record(job, ok=False, detail=f"{type(exc).__name__}: {exc}")
        raise
    record(job, ok=True, detail=detail)


def last_run(job):
    """(timestamp, ok) of the most recent heartbeat, or (None, None)."""
    from audit.models import AuditLog

    entry = (AuditLog.objects
             .filter(changes__job=job)
             .order_by("-created_at")
             .values("created_at", "changes")
             .first())
    if not entry:
        return None, None
    return entry["created_at"], entry["changes"].get("event", "").endswith("_OK")


def status(job):
    """Green / amber / red for one job, with the numbers behind the verdict."""
    label, interval_minutes, critical = CRON_JOBS.get(job, (job, 1440, False))
    timestamp, ok = last_run(job)
    payload = {
        "job": job,
        "label": label,
        "interval_minutes": interval_minutes,
        "critical": critical,
        "last_run": timestamp.isoformat() if timestamp else None,
        "last_run_ok": ok,
    }
    if timestamp is None:
        # Never run is RED, not missing data: a job that has never fired is the
        # most likely kind of broken, and "no data" reads as "not my problem".
        payload.update(state="red", minutes_since=None, reason="never_run")
        return payload

    minutes = (timezone.now() - timestamp).total_seconds() / 60
    payload["minutes_since"] = round(minutes, 1)
    if ok is False:
        payload.update(state="red", reason="last_run_failed")
    elif minutes > interval_minutes * RED_MULTIPLIER:
        payload.update(state="red", reason="overdue")
    elif minutes > interval_minutes * AMBER_MULTIPLIER:
        payload.update(state="amber", reason="late")
    else:
        payload.update(state="green", reason="on_schedule")
    return payload


def all_statuses():
    return [status(job) for job in CRON_JOBS]
