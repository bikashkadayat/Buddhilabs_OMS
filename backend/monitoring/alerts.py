"""Alert rules, with deduplication as a first-class concern.

Phase 11, §6 of the approved design.

The hard part of alerting is not detecting a problem; it is not sending the
same alert 288 times over a weekend. A device that goes offline on Friday
evening would, with a naive implementation, fire every five minutes until
Monday — and the reliable result of that is a team that mutes the channel and
misses the next real incident.

So alerts fire on **state transitions only**: once when a condition starts,
once when it clears, and a single daily digest while it persists. State lives
in the audit log rather than the cache, deliberately — a Redis restart must not
re-fire every currently-active alert, and "when did this start" is a question
worth being able to answer months later.

Severity mapping is opinionated:

* **Critical** pages someone. Database down, backup missing, every terminal
  offline, a login-failure spike.
* **Warning** is email only.
* **Info** is recorded but never sent. Redis being down lives here on purpose:
  the system is *designed* to survive it, so paging at 03:00 for a condition
  that degrades nothing would train people to ignore alerts.
"""
import json
import logging
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from . import metrics

logger = logging.getLogger(__name__)

CRITICAL, WARNING, INFO = "critical", "warning", "info"

# metric key -> (severity, human title, what to do about it)
RULES = {
    # A department nobody owns. Task creation and assignment are NOT affected
    # (that dependency went in TASK-SIMPLIFICATION); what is affected is leave
    # routing, which falls through to HR, and department reporting, which has
    # no owner. WARNING rather than CRITICAL for exactly that reason: nothing
    # is blocked, and an alert that overstates its case is one people learn to
    # ignore.
    "departments_missing_head": (WARNING, "Department has no head",
                                 "Leave routing falls back to HR and department "
                                 "reporting has no owner. Task creation and "
                                 "assignment are unaffected. Assign a head in "
                                 "Administration -> Departments."),
    "db_reachable": (CRITICAL, "Database unreachable",
                     "The API cannot serve anything. Check the db container and disk."),
    "disk_free_pct": (CRITICAL, "Disk space low",
                      "Purge old backups/reports or grow the volume. A full disk "
                      "stops PostgreSQL writing."),
    "db_latency_ms": (WARNING, "Database slow",
                      "Check for a long-running query or a saturated host."),
    "redis_reachable": (INFO, "Redis unreachable",
                        "By design this degrades, it does not break: cache misses, "
                        "throttles fail open, dashboards poll instead of streaming. "
                        "Restart when convenient."),
    "redis_memory_pct": (WARNING, "Redis memory high",
                         "maxmemory-policy is noeviction, so at the ceiling Redis "
                         "REFUSES writes. Restart Redis or raise maxmemory."),
    "devices_online": (WARNING, "Device offline",
                       "Check power and network at the terminal. Punches spool "
                       "locally and sync on reconnect."),
    "punches_awaiting_derivation": (CRITICAL, "Punch queue growing",
                                    "Derivation is not keeping up. Check "
                                    "process_punches and the backend logs."),
    "punches_awaiting_mapping": (WARNING, "Punches awaiting mapping",
                                 "HR action: map the device users. Until then "
                                 "those punches never become attendance."),
    "unmapped_enrolments": (WARNING, "Unmapped device users",
                            "HR action: link each enrolment to an account."),
    "sync_failures_24h": (WARNING, "Device sync failures",
                          "Check the collector logs and the device clock."),
    "sync_success_rate": (WARNING, "Sync success rate low",
                          "Investigate the failing device before data is lost."),
    "reports_stuck": (WARNING, "Reports stuck generating",
                      "reap_stuck_reports clears these every 15 minutes; if they "
                      "recur, the worker is dying mid-run."),
    "reports_failed_24h": (WARNING, "Report generation failing",
                           "Check ReportRun.error for the cause."),
    "backup_backup": (CRITICAL, "Database backup missing",
                      "No successful backup in over a day. THIS IS THE ONE THAT "
                      "matters — fix before anything else."),
    "backup_backup_media": (CRITICAL, "Media backup missing",
                            "Attachments and evidence are unprotected. The "
                            "database only stores their paths."),
    "backup_backup_verified": (CRITICAL, "Backup not verified",
                               "The backup exists but has not been proven "
                               "restorable. Run backup_verify manually."),
    "login_failures_15m": (CRITICAL, "Login failure spike",
                           "Possible credential stuffing. Check the audit log for "
                           "the source addresses and the targeted accounts."),
    "login_lockouts_15m": (WARNING, "Accounts locking out",
                           "Either an attack or a real user who needs an unlock."),

    # --- Phase S11 Part 3: the SaaS platform itself (closes R7) ---------
    #
    # Until these existed, every rule above described the PREVIOUS product.
    # Nothing fired when the platform silently stopped producing workspaces,
    # or when a customer who had paid could not use the address they bought.
    #
    # Severity is set by WHO IS INCONVENIENCED AND HOW BADLY, not by how
    # unusual the condition is:
    #   * a customer who cannot work right now -> critical
    #   * a customer who is waiting on us       -> warning
    #   * work for office hours                 -> warning
    "saas_provisioning_stuck": (
        CRITICAL, "Tenant stuck provisioning",
        "A half-built workspace and a customer already waiting. Platform -> "
        "the tenant -> Health shows which step is missing; `Correct mirrors` "
        "repairs a drifted subscription. Provisioning is one atomic call, so "
        "a tenant in this state is broken rather than busy."),
    "saas_provisioning_failed": (
        CRITICAL, "Provisioning failing",
        "Verified registrations are producing no workspace. Check the "
        "platform log for `tenancy.services` errors, and the launch gate "
        "(`manage.py check --deploy`) for a missing dependency -- a trial "
        "plan or bootstrap catalogue that has gone away fails every signup."),
    "saas_registration_stalled": (
        CRITICAL, "Signup verified but no workspace",
        "The customer has done everything asked of them and been told so, "
        "and we owe them a workspace. Support runbook section 3. Re-run "
        "provisioning from the console; do not ask them to register again."),
    "saas_verification_failed": (
        WARNING, "Signups not completing verification",
        "Some of this is people abandoning a form. A CLIFF means mail is no "
        "longer being delivered -- which the platform cannot see, because "
        "its own send succeeded. Send yourself a test registration."),
    "saas_domain_failed": (
        WARNING, "Custom domain not verifying",
        "A customer paid for an address that does not work. Platform -> "
        "Custom domains: read `Why not` to them literally. Three or more "
        "checks means they have misread their DNS panel and will not work "
        "it out alone. Support runbook section 5c."),
    "saas_export_failed": (
        WARNING, "Export failed",
        "A customer -- often one who is leaving -- is waiting for data that "
        "is not coming. Re-run it from the console; the receipt records the "
        "error. Support runbook section 6."),
    "saas_restore_needed": (
        CRITICAL, "Tenant archived while still subscribed",
        "Their workspace is closed and their subscription says it should be "
        "open: either a restore that did not complete or a wrongly archived "
        "customer. Both need the same phone call. Platform -> the tenant -> "
        "Restore. Nothing was deleted."),
    "saas_subscription_expiry": (
        WARNING, "Subscriptions need attention",
        "Amber is renewals to chase in office hours. RED means tenants are "
        "already in grace, expired or suspended -- those customers cannot "
        "work this morning. Platform -> Subscriptions."),
    "saas_payment_backlog": (
        CRITICAL, "Payment receipts unreviewed",
        "Measured in HOURS on the oldest receipt, not in count: twenty "
        "today is a busy day, one from Friday still waiting on Monday is a "
        "customer who paid and is locked out. Platform -> Payments -> claim "
        "for review. Support runbook section 5b."),
    # THE PROBE'S OWN FAILURE NEEDS A RULE. `metrics.safe` turns an
    # exception in a probe into a single red metric keyed after the
    # function -- so without this entry, a SaaS probe that started raising
    # would take all nine alerts above offline and fire nothing in their
    # place. Monitoring that fails quiet is worse than none, because it is
    # trusted.
    "saas_platform": (
        CRITICAL, "SaaS monitoring itself is broken",
        "The platform health probe raised, so none of the SaaS alerts can "
        "fire. The metric detail carries the exception. Until it is fixed, "
        "provisioning, domain, export, payment and subscription problems "
        "are all unmonitored."),
    "saas_5xx_rate": (
        CRITICAL, "Server error rate elevated",
        "Customers never see internal errors by design, so nothing else "
        "will tell you. Check the error tracker, then the connection budget "
        "-- Phase S10 measured 14.5% of requests failing with 'remaining "
        "connection slots are reserved' at CONN_MAX_AGE>0 under ASGI."),
}

# Cron jobs raise one shared alert rather than one per job, so a cron container
# that dies does not produce fifteen separate emails describing one fact.
CRON_ALERT = (CRITICAL, "Scheduled jobs not running",
              "One or more cron jobs are overdue. Check the cron container: "
              "`docker compose ps cron` and `docker compose logs cron`.")

DIGEST_INTERVAL_HOURS = 24


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------
def evaluate(board=None):
    """Turn the metrics board into the set of currently-firing alerts.

    Collects WITH the platform section (Phase S11). `metrics.collect()`
    leaves it out by default because the tenant-facing monitoring views
    serve that board to a customer's own administrators; the alerter has no
    HTTP caller and is the one place that should see everything.
    """
    board = board or metrics.collect(platform=True)
    firing = []

    for section in board["sections"]:
        if section["key"] == "cron":
            firing.extend(_cron_alert(section))
            continue
        for metric in section["metrics"]:
            rule = RULES.get(metric["key"])
            if rule is None or metric["state"] == metrics.GREEN:
                continue
            severity, title, action = rule
            # An amber on a critical rule is a warning, not a page. Only the red
            # state earns the rule's full severity.
            effective = severity if metric["state"] == metrics.RED else WARNING
            if severity == INFO:
                effective = INFO
            firing.append({
                "key": metric["key"], "severity": effective, "title": title,
                "action": action, "state": metric["state"],
                "value": metric["value"], "unit": metric.get("unit"),
                "detail": metric.get("detail"), "section": section["label"],
            })
    return firing


def _cron_alert(section):
    late = [m for m in section["metrics"] if m["state"] in (metrics.AMBER, metrics.RED)]
    if not late:
        return []
    severity, title, action = CRON_ALERT
    worst = metrics.RED if any(m["state"] == metrics.RED for m in late) else metrics.AMBER
    return [{
        "key": "cron_overdue", "severity": severity if worst == metrics.RED else WARNING,
        "title": title, "action": action, "state": worst,
        "value": len(late), "unit": "jobs",
        "detail": ", ".join(m["label"] for m in late[:6]),
        "section": section["label"],
    }]


# ---------------------------------------------------------------------------
# state, held in the audit log
#
# Every function below touches the database, and the database is one of the
# things these alerts exist to report on. Until this was tested against a
# genuinely stopped Postgres, `check_alerts` died with OperationalError before
# reaching _notify, so the one outage that most needs to reach a human - the
# database being down - was the single outage the alerter could not report. The
# probes already degrade to red metrics; only this state layer failed hard.
#
# So state is now best-effort and delivery is not. Losing the state means losing
# deduplication, and while the database is down the same alert goes out every
# five minutes. That is the right way round: a repeated page during an outage is
# noise somebody can act on, and silence is not.
# ---------------------------------------------------------------------------
def _last_event(key):
    from audit.models import AuditLog

    try:
        return (AuditLog.objects
                .filter(changes__alert_key=key)
                .order_by("-created_at")
                .values("created_at", "changes")
                .first())
    except Exception:  # noqa: BLE001
        # No history readable means no evidence anybody has been told, and
        # _should_notify reads None as "new". Erring towards sending.
        logger.warning("Alert history unreadable for %s; treating it as new.",
                       key, exc_info=True)
        return None


def _record(key, event, payload):
    from audit.models import AuditLog
    from audit.services import log_action

    try:
        return log_action(None, AuditLog.Action.OTHER,
                          changes={"event": f"ALERT_{event}", "alert_key": key,
                                   **payload})
    except Exception:  # noqa: BLE001
        logger.warning("Could not record alert %s in the audit log; the "
                       "notification itself still goes out.", key, exc_info=True)
        return None


def _should_notify(alert):
    """Fire on transition, then once a day while it persists.

    Returns (notify, reason) so the caller can log why it stayed quiet — a
    suppressed alert that nobody can explain looks like a broken alerter.
    """
    last = _last_event(alert["key"])
    if last is None:
        return True, "new"

    event = last["changes"].get("event", "")
    if event == "ALERT_RESOLVED":
        return True, "recurred"

    age = timezone.now() - last["created_at"]
    if age > timedelta(hours=DIGEST_INTERVAL_HOURS):
        return True, "digest"

    # Escalation is worth a second message: amber on Friday becoming red on
    # Saturday is new information, not a repeat.
    if last["changes"].get("state") != metrics.RED and alert["state"] == metrics.RED:
        return True, "escalated"

    return False, "already_firing"


def process(board=None):
    """Evaluate, notify on changes, and resolve what has cleared."""
    firing = evaluate(board)
    firing_keys = {alert["key"] for alert in firing}
    sent, resolved, suppressed = [], [], []

    for alert in firing:
        notify, reason = _should_notify(alert)
        _record(alert["key"], "FIRING", {
            "severity": alert["severity"], "state": alert["state"],
            "title": alert["title"], "value": alert["value"],
            "notified": notify, "reason": reason,
        })
        if notify and alert["severity"] != INFO:
            _notify(alert, reason)
            sent.append(alert["key"])
        else:
            suppressed.append(alert["key"])

    for key in _active_keys() - firing_keys:
        rule = RULES.get(key)
        title = rule[1] if rule else (CRON_ALERT[1] if key == "cron_overdue" else key)
        _record(key, "RESOLVED", {"title": title})
        _notify({"key": key, "title": title, "severity": INFO,
                 "action": "", "state": metrics.GREEN, "value": None,
                 "detail": "", "section": ""}, "resolved", resolved=True)
        resolved.append(key)

    return {"firing": sorted(firing_keys), "notified": sent,
            "suppressed": suppressed, "resolved": resolved}


def _active_keys():
    """Alerts currently in a firing state, from their most recent event.

    Empty when the history cannot be read: with no record of what was firing,
    nothing can honestly be declared resolved. A false "RESOLVED" is worse than
    a missing one - it tells people to stop looking.
    """
    from audit.models import AuditLog

    since = timezone.now() - timedelta(days=7)
    latest = {}
    try:
        rows = list(AuditLog.objects
                    .filter(created_at__gte=since, changes__alert_key__isnull=False)
                    .order_by("created_at")
                    .values("created_at", "changes"))
    except Exception:  # noqa: BLE001
        logger.warning("Alert history unreadable; nothing can be resolved on "
                       "this run.", exc_info=True)
        return set()
    for row in rows:
        latest[row["changes"]["alert_key"]] = row["changes"].get("event")
    return {key for key, event in latest.items() if event == "ALERT_FIRING"}


# ---------------------------------------------------------------------------
# delivery
# ---------------------------------------------------------------------------
def _recipients():
    raw = getattr(settings, "ALERT_EMAILS", "") or ""
    return [address.strip() for address in raw.split(",") if address.strip()]


def _notify(alert, reason, resolved=False):
    """Email and/or webhook. Never raises -- a failed notification must not
    fail the alert run, or one bad SMTP config silences everything."""
    prefix = "RESOLVED" if resolved else alert["severity"].upper()
    subject = f"[NIF {prefix}] {alert['title']}"
    body = _body(alert, reason, resolved)

    recipients = _recipients()
    if recipients:
        try:
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, recipients,
                      fail_silently=False)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send alert email for %s", alert["key"])

    url = getattr(settings, "ALERT_WEBHOOK_URL", "") or ""
    if url:
        try:
            payload = json.dumps({"text": f"*{subject}*\n{body}"}).encode()
            request = urllib.request.Request(
                url, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(request, timeout=10)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to post alert webhook for %s", alert["key"])

    if not recipients and not url:
        logger.warning("Alert %s fired but no ALERT_EMAILS or ALERT_WEBHOOK_URL "
                       "is configured — nobody was told.", alert["key"])


def _body(alert, reason, resolved):
    if resolved:
        return (f"{alert['title']} has cleared.\n\n"
                f"Checked at {timezone.localtime():%Y-%m-%d %H:%M %Z}.")
    lines = [
        alert["title"],
        "",
        f"Severity : {alert['severity'].upper()}",
        f"Section  : {alert['section']}",
        f"Value    : {alert['value']}{' ' + alert['unit'] if alert.get('unit') else ''}",
    ]
    if alert.get("detail"):
        lines.append(f"Detail   : {alert['detail']}")
    lines += [
        "",
        "What to do:",
        f"  {alert['action']}",
        "",
        f"Reason for this message: {reason}",
        f"Checked at {timezone.localtime():%Y-%m-%d %H:%M %Z}.",
        "",
        "Runbook: docs/RUNBOOK.md",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# read model for the dashboard
# ---------------------------------------------------------------------------
def current_state():
    from audit.models import AuditLog

    firing = evaluate()
    recent = (AuditLog.objects
              .filter(created_at__gte=timezone.now() - timedelta(days=7),
                      changes__alert_key__isnull=False)
              .order_by("-created_at")
              .values("created_at", "changes")[:50])
    return {
        "firing": firing,
        "configured": bool(_recipients() or getattr(settings, "ALERT_WEBHOOK_URL", "")),
        "recipients": len(_recipients()),
        "history": [{
            "at": row["created_at"].isoformat(),
            "event": row["changes"].get("event"),
            "key": row["changes"].get("alert_key"),
            "title": row["changes"].get("title"),
            "severity": row["changes"].get("severity"),
            "notified": row["changes"].get("notified"),
        } for row in recent],
    }
