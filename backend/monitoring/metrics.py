"""System health metrics, each with an explicit green/amber/red threshold.

Phase 11, §5 of the approved design. Deliberately NOT Prometheus + Grafana:
this is a single-host deployment for one organisation with no dedicated ops
team, and a monitoring stack nobody maintains is worse than a page inside the
app people already sign into. The metrics are shaped for a ``/metrics``
exporter, so that door stays open.

Two rules run through this module.

**Every check is individually wrapped.** A monitoring page that 500s because
one probe failed tells you nothing at the moment you most need it. A failed
probe becomes a red metric with the error attached, which is itself the signal.

**Thresholds live beside the measurement, not in the UI.** Otherwise the
dashboard, the alert rule and the runbook each get their own opinion about what
"too many" means, and they drift.
"""
import logging
import shutil
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.utils import timezone

from . import heartbeat

logger = logging.getLogger(__name__)

GREEN, AMBER, RED = "green", "amber", "red"

# Rank for rolling many metrics up into one overall verdict.
SEVERITY = {GREEN: 0, AMBER: 1, RED: 2}


def _metric(key, label, value, state, *, unit=None, detail=None, thresholds=None):
    return {
        "key": key, "label": label, "value": value, "state": state,
        "unit": unit, "detail": detail, "thresholds": thresholds,
    }


def _band(value, amber, red, *, higher_is_worse=True):
    """Map a number onto a state. Used everywhere so the direction of 'bad' is
    stated once per metric rather than re-reasoned at each call site."""
    if value is None:
        return AMBER
    if higher_is_worse:
        return RED if value >= red else (AMBER if value >= amber else GREEN)
    return RED if value <= red else (AMBER if value <= amber else GREEN)


def safe(fn):
    """Run a probe; turn any failure into a red metric rather than a 500."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Health probe %s failed", fn.__name__)
            return [_metric(fn.__name__, fn.__name__.replace("_", " ").title(),
                            None, RED, detail=f"{type(exc).__name__}: {exc}"[:200])]
    return wrapper


# ---------------------------------------------------------------------------
# database
# ---------------------------------------------------------------------------
@safe
def database():
    """The one probe that has to survive its own subject being down.

    This used to let the connection error escape to @safe, which returns a
    single red metric keyed after the FUNCTION -- "database". No rule in
    monitoring.alerts.RULES matches that key, and db_reachable was only ever
    emitted on the success path, so a stopped Postgres produced a red tile on
    the health board and paged precisely nobody. Tested against a genuinely
    stopped database during PRODUCTION-DEPLOYMENT-HARDENING, not reasoned about.

    Disk space does not need the database and is reported either way: "the
    database is down" and "the disk is full" are frequently the same incident,
    and the second is the half that says why.
    """
    import time

    results = []
    try:
        started = time.perf_counter()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001
        connection.close()  # so the next probe re-dials instead of reusing it
        results.append(_metric("db_reachable", "Database reachable", False, RED,
                               detail=f"{type(exc).__name__}: {exc}"[:200]))
    else:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        results.append(_metric("db_reachable", "Database reachable", True, GREEN))
        results.append(_metric("db_latency_ms", "Database latency", latency_ms,
                               _band(latency_ms, 50, 200), unit="ms",
                               thresholds={"amber": 50, "red": 200}))

    usage = shutil.disk_usage(str(settings.BASE_DIR))
    free_pct = round(usage.free / usage.total * 100, 1)
    results.append(_metric("disk_free_pct", "Disk free", free_pct,
                           _band(free_pct, 20, 10, higher_is_worse=False), unit="%",
                           detail=f"{usage.free // (1024 ** 3)} GB of "
                                  f"{usage.total // (1024 ** 3)} GB free",
                           thresholds={"amber": 20, "red": 10}))
    return results


# ---------------------------------------------------------------------------
# redis
# ---------------------------------------------------------------------------
@safe
def redis():
    if not getattr(settings, "REDIS_URL", ""):
        # Not an error. Without REDIS_URL the app runs on in-memory backends by
        # design, so flagging it red would train people to ignore the board.
        return [_metric("redis", "Redis", "not configured", GREEN,
                        detail="Running on in-memory backends by design.")]

    from django.core.cache import cache

    cache.set("monitoring:ping", "1", 10)
    reachable = cache.get("monitoring:ping") == "1"
    metrics = [_metric("redis_reachable", "Redis reachable", reachable,
                       GREEN if reachable else RED)]

    memory = _redis_memory()
    if memory is not None:
        used_pct, used_mb, max_mb = memory
        metrics.append(_metric(
            "redis_memory_pct", "Redis memory", used_pct,
            _band(used_pct, 60, 85), unit="%",
            detail=f"{used_mb} MB of {max_mb} MB",
            thresholds={"amber": 60, "red": 85}))
    return metrics


def _redis_memory():
    """Used vs maxmemory, or None when it cannot be read.

    Matters because the deployment runs `noeviction`: at the ceiling Redis
    starts REFUSING writes rather than evicting, which surfaces as missing
    dashboard events rather than as an obvious outage.
    """
    try:
        from django.core.cache import cache

        client = cache._cache.get_client()
        info = client.info("memory")
        used = info.get("used_memory", 0)
        maximum = info.get("maxmemory", 0) or 0
        if not maximum:
            return None
        return (round(used / maximum * 100, 1),
                round(used / (1024 ** 2), 1), round(maximum / (1024 ** 2), 1))
    except Exception:  # noqa: BLE001 -- LocMemCache and older clients have no
        # get_client; an unreadable figure is not a failure.
        return None


# ---------------------------------------------------------------------------
# devices and ingest queues
# ---------------------------------------------------------------------------
@safe
def devices():
    from biometric.models import AttendancePunch, BiometricDevice, BiometricEmployee

    total = BiometricDevice.objects.filter(is_active=True).count()
    online = BiometricDevice.objects.filter(
        is_active=True, connection_status=BiometricDevice.Status.ONLINE).count()
    offline = total - online

    if total == 0:
        device_state = AMBER  # no terminals registered is worth noticing
    elif offline == 0:
        device_state = GREEN
    elif online == 0:
        device_state = RED    # every terminal down: nobody's attendance is landing
    else:
        device_state = AMBER

    unmapped = BiometricEmployee.objects.filter(
        is_active=True, user__isnull=True).count()
    pending = AttendancePunch.objects.filter(is_processed=False)
    awaiting_derivation = pending.filter(user__isnull=False).count()
    awaiting_mapping = pending.filter(user__isnull=True).count()

    return [
        _metric("devices_online", "Devices online", f"{online}/{total}",
                device_state, detail=f"{offline} offline"),
        # Separated deliberately (this is what the Phase 7 health view already
        # got right): punches awaiting MAPPING are blocked on an HR decision,
        # not on a stuck worker. Conflating them makes a normal queue look like
        # an outage and hides a real one.
        _metric("punches_awaiting_derivation", "Punches awaiting derivation",
                awaiting_derivation, _band(awaiting_derivation, 50, 500),
                thresholds={"amber": 50, "red": 500}),
        _metric("punches_awaiting_mapping", "Punches awaiting mapping",
                awaiting_mapping, _band(awaiting_mapping, 1, 100),
                detail="Blocked on an HR mapping decision, not on the system.",
                thresholds={"amber": 1, "red": 100}),
        _metric("unmapped_enrolments", "Unmapped device users", unmapped,
                _band(unmapped, 1, 20), thresholds={"amber": 1, "red": 20}),
    ]


@safe
def sync_health():
    from biometric.models import DeviceSyncLog

    since = timezone.now() - timedelta(hours=24)
    logs = DeviceSyncLog.objects.filter(started_at__gte=since)
    total = logs.count()
    failed = logs.filter(status=DeviceSyncLog.Status.FAILED).count()

    if not total:
        return [_metric("sync_failures_24h", "Failed syncs (24h)", 0, GREEN,
                        detail="No ingest batches in the last 24 hours.")]
    return [
        _metric("sync_failures_24h", "Failed syncs (24h)", failed,
                _band(failed, 1, 5), thresholds={"amber": 1, "red": 5}),
        _metric("sync_success_rate", "Sync success rate (24h)",
                round(logs.filter(status=DeviceSyncLog.Status.SUCCESS).count()
                      / total * 100, 1),
                _band(round(logs.filter(status=DeviceSyncLog.Status.SUCCESS).count()
                            / total * 100, 1), 95, 80, higher_is_worse=False),
                unit="%", thresholds={"amber": 95, "red": 80}),
    ]


# ---------------------------------------------------------------------------
# queues
# ---------------------------------------------------------------------------
@safe
def queues():
    from attendance.workforce.models import AttendanceCorrectionRequest
    from leaves.models import Leave
    from reports.models import ReportRun

    stuck_cutoff = timezone.now() - timedelta(minutes=30)
    stuck_reports = ReportRun.objects.filter(
        status=ReportRun.Status.GENERATING, created_at__lt=stuck_cutoff).count()
    failed_reports = ReportRun.objects.filter(
        status=ReportRun.Status.FAILED,
        created_at__gte=timezone.now() - timedelta(hours=24)).count()

    open_corrections = AttendanceCorrectionRequest.objects.filter(
        status__in=AttendanceCorrectionRequest.OPEN_STATUSES).count()
    pending_leave = Leave.objects.filter(
        is_deleted=False,
        status__in=[Leave.Status.PENDING, Leave.Status.PENDING_HR]).count()

    return [
        _metric("reports_stuck", "Reports stuck generating", stuck_reports,
                _band(stuck_reports, 1, 2),
                detail="Cleared automatically by reap_stuck_reports.",
                thresholds={"amber": 1, "red": 2}),
        _metric("reports_failed_24h", "Failed reports (24h)", failed_reports,
                _band(failed_reports, 1, 5), thresholds={"amber": 1, "red": 5}),
        # Approval backlogs are workload, not system failures, so they are
        # reported without a red state: nobody should be paged because HR has
        # not opened their queue yet.
        _metric("open_corrections", "Open correction requests", open_corrections,
                GREEN, detail="Workload, not a fault."),
        _metric("pending_leave", "Leave awaiting a decision", pending_leave,
                GREEN, detail="Workload, not a fault."),
    ]


# ---------------------------------------------------------------------------
# backups and scheduled jobs
# ---------------------------------------------------------------------------
@safe
def backups():
    metrics = []
    for job, label in (("BACKUP", "Database backup"),
                       ("BACKUP_MEDIA", "Media backup"),
                       ("BACKUP_VERIFIED", "Backup verified")):
        entry = heartbeat.status(job)
        hours = (round(entry["minutes_since"] / 60, 1)
                 if entry["minutes_since"] is not None else None)
        # 26 hours, not 24: a daily job must not go amber merely because today's
        # run has not reached its slot yet.
        state = RED if hours is None else _band(hours, 26, 48)
        if entry["last_run_ok"] is False:
            state = RED
        metrics.append(_metric(
            f"backup_{job.lower()}", label, hours, state, unit="h ago",
            detail=("Never run — this is the failure H4 was about."
                    if hours is None else entry["reason"]),
            thresholds={"amber": 26, "red": 48}))
    return metrics


@safe
def cron_jobs():
    """One metric per registered job. Replaces the 4-of-11 coverage (M5)."""
    return [
        _metric(f"cron_{entry['job'].lower()}", entry["label"],
                entry["minutes_since"], entry["state"], unit="min ago",
                detail=entry["reason"],
                thresholds={"interval_minutes": entry["interval_minutes"]})
        for entry in heartbeat.all_statuses()
    ]


# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------
@safe
def authentication():
    from audit.models import AuditLog

    since = timezone.now() - timedelta(minutes=15)
    failures = AuditLog.objects.filter(
        created_at__gte=since, changes__event="LOGIN_FAILED").count()
    lockouts = AuditLog.objects.filter(
        created_at__gte=since, changes__event="LOGIN_LOCKED").count()

    return [
        _metric("login_failures_15m", "Failed logins (15 min)", failures,
                _band(failures, 20, 200),
                detail="A spike is credential stuffing until proven otherwise.",
                thresholds={"amber": 20, "red": 200}),
        _metric("login_lockouts_15m", "Account lockouts (15 min)", lockouts,
                _band(lockouts, 1, 5), thresholds={"amber": 1, "red": 5}),
    ]


# ---------------------------------------------------------------------------
# composition
# ---------------------------------------------------------------------------
# Every key this module can emit, including the conditional ones.
#
# Needed because several metrics only appear under conditions a test cannot
# always create: the Redis metrics require REDIS_URL and a client exposing
# `maxmemory`, and the sync rate requires at least one ingest batch. Without a
# declared list, "does every alert rule reference a real metric?" could only be
@safe
def governance():
    """
    Whether every department has somebody answerable for it
    (Phase DEPARTMENT-GOVERNANCE-HARDENING).

    ON THE HEALTH BOARD RATHER THAN ONLY IN AN ADMIN PAGE, because an unowned
    department is a configuration gap with consequences: leave routing falls
    back to HR, and department reporting has no owner. It does NOT stop task
    work - that dependency was removed in TASK-SIMPLIFICATION, and this text
    used to say otherwise.

    AMBER AT ONE, not red. There is no acceptable number of departments with
    nobody answerable for them, but nothing is broken while it is true, and a
    board that shows a fixable configuration gap in the same colour as a dead
    database teaches people to read past both.
    """
    from leaves.governance import governance_summary

    summary = governance_summary()
    missing = summary["missing_head"]
    return [
        _metric("departments_total", "Departments", summary["active"], GREEN,
                detail=f"{summary['total']} recorded, {summary['active']} active"),
        _metric("departments_missing_head", "Departments missing a head", missing,
                AMBER if missing else GREEN,
                detail=(", ".join(summary["missing_head_names"])[:200]
                        if missing
                        else "Every active department has an active head."),
                thresholds={"amber": 1, "red": 1}),
    ]


# answered against whatever the current environment happened to produce — and
# would have passed while three rules were quietly dead.
#
# Adding a metric means adding it here; the test below enforces that.
ALL_METRIC_KEYS = {
    "departments_total", "departments_missing_head",
    "db_reachable", "db_latency_ms", "disk_free_pct",
    "redis", "redis_reachable", "redis_memory_pct",
    "devices_online", "punches_awaiting_derivation", "punches_awaiting_mapping",
    "unmapped_enrolments",
    "sync_failures_24h", "sync_success_rate",
    "reports_stuck", "reports_failed_24h", "open_corrections", "pending_leave",
    "backup_backup", "backup_backup_media", "backup_backup_verified",
    "login_failures_15m", "login_lockouts_15m",
    # --- Phase S11 Part 3: the SaaS platform (closes R7) ----------------
    #
    # DECLARED HERE, EMITTED ONLY BY THE PLATFORM BOARD. `collect()` leaves
    # the `saas` section out unless asked, because the tenant-facing
    # monitoring views serve that board to a customer's own administrators
    # -- see `collect`. This set is the declaration that keeps
    # `test_every_alert_rule_maps_to_a_declared_metric` honest: a rule keyed
    # on a metric that no longer exists never fires and nothing would say so.
    "saas_platform",
    "saas_provisioning_stuck", "saas_provisioning_failed",
    "saas_registration_stalled", "saas_verification_failed",
    "saas_domain_failed", "saas_export_failed", "saas_restore_needed",
    "saas_subscription_expiry", "saas_payment_backlog", "saas_5xx_rate",
}

SECTIONS = (
    ("governance", "Governance", governance),
    ("database", "Database", database),
    ("redis", "Redis", redis),
    ("devices", "Devices & ingest", devices),
    ("sync", "Sync health", sync_health),
    ("queues", "Queues", queues),
    ("backups", "Backups", backups),
    ("cron", "Scheduled jobs", cron_jobs),
    ("auth", "Authentication", authentication),
)


# Phase S11: the platform's own health. NOT in SECTIONS, and the omission is
# the security control -- see `collect`.
PLATFORM_SECTIONS = (
    ("saas", "SaaS platform", "monitoring.saas.saas_platform"),
)


def collect(platform=False):
    """The board. Each section is independently fault-tolerant.

    ``platform`` adds the SaaS section, and DEFAULTS TO FALSE ON PURPOSE.
    `monitoring/views.py` serves this to `IsOperator`, which is a TENANT
    role -- an HR or Admin user inside one customer's workspace. The SaaS
    metrics are platform-wide: how many tenants are stuck provisioning,
    which hostnames are failing, the payment backlog across all customers.
    Including them by default would hand every customer's administrator a
    running commentary on every other customer.

    So the tenant-facing views call this with no argument, and
    `check_alerts` -- a cron job with no HTTP caller -- passes True.
    """
    sections, worst = [], GREEN
    probes = list(SECTIONS)
    if platform:
        for key, label, path in PLATFORM_SECTIONS:
            module, _, name = path.rpartition(".")
            import importlib

            probes.append((key, label,
                           getattr(importlib.import_module(module), name)))
    for key, label, probe in probes:
        metrics = probe()
        state = _rollup(metrics)
        if SEVERITY[state] > SEVERITY[worst]:
            worst = state
        sections.append({"key": key, "label": label, "state": state,
                         "metrics": metrics})

    return {
        "status": worst,
        "generated_at": timezone.localtime().isoformat(),
        "sections": sections,
        "summary": {
            "red": sum(1 for s in sections if s["state"] == RED),
            "amber": sum(1 for s in sections if s["state"] == AMBER),
            "green": sum(1 for s in sections if s["state"] == GREEN),
        },
    }


def _rollup(metrics):
    worst = GREEN
    for metric in metrics:
        if SEVERITY.get(metric["state"], 0) > SEVERITY[worst]:
            worst = metric["state"]
    return worst
