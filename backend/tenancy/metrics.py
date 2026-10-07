"""Phase S6.75 Part 4: the counters, and the dashboards built from them.

TWO SOURCES, FOR ONE REASON.

  * ``PlatformAuditLog`` answers six of the eight series the brief asks for --
    tenant creation, subscription events, archives, restores, exports -- for
    free, because it is platform-global and already records every one of
    them with a timestamp.

  * ``PlatformMetric`` answers the other two. A sign-in is recorded in
    ``audit.AuditLog``, which is tenant-scoped and policed by row-level
    security, so a console query with no tenant bound counts zero of them.
    The counter is the only way to put logins on a platform dashboard
    without handing the console a way to read customer rows.

COUNTING MUST NEVER BREAK THE THING IT COUNTS. Every write here is wrapped:
a metrics failure must not fail a sign-in, and a dashboard is not worth an
outage. That is also why the counter is incremented with an UPDATE rather
than read-modify-write -- two simultaneous sign-ins would otherwise lose one
of the increments, and a metric that undercounts under load is worse than
none, because it reads as a quiet drop in traffic.
"""
import logging

from django.db.models import Count, F, Sum
from django.utils import timezone

logger = logging.getLogger(__name__)


def bump(key, *, organization=None, when=None, by=1):
    """Add to a daily counter. Never raises.

    ``organization`` may be None: a failed sign-in for an address belonging
    to nobody has no tenant, and inventing one would hide the probe.
    """
    from .models import PlatformMetric

    day = (when or timezone.localdate())
    try:
        organization_id = getattr(organization, "pk", organization)
        updated = (PlatformMetric.objects
                   .filter(day=day, key=key, organization_id=organization_id)
                   .update(count=F("count") + by))
        if not updated:
            PlatformMetric.objects.create(
                day=day, key=key, organization_id=organization_id, count=by)
    except Exception:                              # noqa: BLE001
        # A dashboard is not worth an outage. Logged at debug, because a
        # racing insert on the unique constraint is expected and benign --
        # the next request's UPDATE path will find the row.
        logger.debug("metric %s could not be recorded", key, exc_info=True)


def series(*, days=30, organization=None):
    """Every dashboard series the brief names, over a window.

    Returned as ``{key: {"total": n, "by_day": {date: n}}}`` so the console
    can draw a sparkline or print a number without a second request.
    """
    from .models import PlatformAuditLog, PlatformMetric

    since = timezone.localdate() - timezone.timedelta(days=days - 1)
    out = {}

    # --- the counters (logins, provisioning outcomes, platform email) ---
    counters = (PlatformMetric.objects
                .filter(day__gte=since)
                .values("key", "day")
                .annotate(total=Sum("count")))
    if organization is not None:
        counters = counters.filter(organization=organization)
    for row in counters:
        entry = out.setdefault(row["key"], {"total": 0, "by_day": {}})
        entry["total"] += row["total"] or 0
        entry["by_day"][row["day"].isoformat()] = (
            entry["by_day"].get(row["day"].isoformat(), 0)
            + (row["total"] or 0))

    # --- the audit trail (everything else) ------------------------------
    trail = (PlatformAuditLog.objects
             .filter(created_at__date__gte=since)
             .values("action", "created_at__date")
             .annotate(total=Count("id")))
    if organization is not None:
        trail = trail.filter(organization=organization)
    for row in trail:
        entry = out.setdefault(row["action"], {"total": 0, "by_day": {}})
        day = row["created_at__date"].isoformat()
        entry["total"] += row["total"]
        entry["by_day"][day] = entry["by_day"].get(day, 0) + row["total"]

    return out


# The eight series Part 4 names, mapped onto where each one comes from, so a
# reader can see at a glance which are counted and which are derived.
DASHBOARDS = [
    ("Tenant creation", "tenant_created", "audit"),
    ("Provisioning succeeded", "provision_ok", "counter"),
    ("Provisioning failed", "provision_failed", "counter"),
    ("Login succeeded", "login_ok", "counter"),
    ("Login failed", "login_failed", "counter"),
    ("Subscription events", "subscription_changed", "audit"),
    ("Archive events", "tenant_archived", "audit"),
    ("Export events", "export_created", "audit"),
]


def dashboard(*, days=30):
    """Part 4's eight series, in the order the brief lists them."""
    data = series(days=days)
    panels = []
    for label, key, source in DASHBOARDS:
        entry = data.get(key, {"total": 0, "by_day": {}})
        panels.append({
            "label": label, "key": key, "source": source,
            "total": entry["total"], "by_day": entry["by_day"],
        })

    # Two derived figures worth their own line, because they are what an
    # operator actually watches: the proportion of attempts that worked.
    provisioned = data.get("provision_ok", {}).get("total", 0)
    failed = data.get("provision_failed", {}).get("total", 0)
    logins = data.get("login_ok", {}).get("total", 0)
    bad_logins = data.get("login_failed", {}).get("total", 0)

    return {
        "window_days": days,
        "panels": panels,
        "rates": {
            "provisioning_success": _rate(provisioned, provisioned + failed),
            "login_success": _rate(logins, logins + bad_logins),
        },
        "totals": {
            "provisioning_attempts": provisioned + failed,
            "login_attempts": logins + bad_logins,
        },
    }


def _rate(part, whole):
    """A percentage, or None when nothing happened.

    None rather than 100: a window with no provisioning attempts has no
    success rate, and showing 100% for it would read as a healthy signal
    where there is no signal at all.
    """
    if not whole:
        return None
    return round(100 * part / whole, 1)


# ---------------------------------------------------------------------------
# Phase S7 Part 11: the registration funnel
# ---------------------------------------------------------------------------
def registration_funnel(*, days=30):
    """Who started, who verified, who got a workspace, and who is on trial.

    COUNTED FROM `PendingRegistration` ITSELF, not from a separate event
    stream, and that is deliberate: the row IS the funnel. Each registration
    carries the timestamps of every stage it reached -- created, verified,
    provisioned -- so the counts cannot drift from the thing they describe,
    and a stage that is missing from the numbers is a stage that genuinely
    did not happen.

    THE RATES ARE THE POINT, NOT THE TOTALS. "41 registrations" says nothing
    an operator can act on. "Of 41, 12 never opened the email" names a
    deliverability problem, and "of 29 verified, 3 failed to provision"
    names a platform fault -- and those two need completely different
    people. So each stage reports both its count and its conversion from the
    stage before it.

    DROP-OFF IS REPORTED SEPARATELY FROM FAILURE, because they are not the
    same event. A registrant who never clicks the link has dropped off -- the
    platform did nothing wrong and cannot fix it. A verified registration
    with no workspace is a platform failure, and somebody should be woken up.
    """
    from .models import PendingRegistration

    since = timezone.now() - timezone.timedelta(days=days - 1)
    rows = list(PendingRegistration.objects
                .filter(created_at__gte=since)
                .values("status", "verified_at", "provisioned_at",
                        "verification_sends", "created_at"))

    started = len(rows)
    verified = sum(1 for r in rows if r["verified_at"])
    provisioned = sum(1 for r in rows if r["provisioned_at"])
    expired = sum(1 for r in rows
                  if r["status"] == PendingRegistration.Status.EXPIRED)
    # Verified but never provisioned: the platform's own failures. Every one
    # of these is a customer who was told their email was confirmed and has
    # no workspace.
    stalled = sum(1 for r in rows
                  if r["verified_at"] and not r["provisioned_at"])
    awaiting = sum(1 for r in rows
                   if r["status"] == PendingRegistration.Status.PENDING)
    resends = sum(max(0, (r["verification_sends"] or 1) - 1) for r in rows)

    # Trial activations, from the platform trail rather than from here: a
    # workspace reaches TRIAL through `provision_organization`, which audits
    # it, and that is also the path an OPERATOR-created tenant takes. The
    # funnel is about self-service; this figure is about the platform.
    from .models import PlatformAuditLog

    trials = (PlatformAuditLog.objects
              .filter(action=PlatformAuditLog.Action.TRIAL_STARTED,
                      created_at__gte=since)
              .count())

    stages = [
        {"key": "started", "label": "Registered", "count": started,
         "of_previous": None},
        {"key": "verified", "label": "Email verified", "count": verified,
         "of_previous": _rate(verified, started)},
        {"key": "provisioned", "label": "Workspace created",
         "count": provisioned, "of_previous": _rate(provisioned, verified)},
    ]

    return {
        "window_days": days,
        "stages": stages,
        "rates": {
            "verification": _rate(verified, started),
            "provision_success": _rate(provisioned, verified),
            "provision_failure": _rate(stalled, verified),
            "overall": _rate(provisioned, started),
        },
        "drop_off": {
            # Nothing is wrong with the platform here -- somebody changed
            # their mind, or the mail went to spam.
            "awaiting_verification": awaiting,
            "expired_unverified": expired,
            "verification_resends": resends,
        },
        "failures": {
            # Somebody should be woken up for these.
            "verified_without_workspace": stalled,
        },
        "trial_activations": trials,
    }
