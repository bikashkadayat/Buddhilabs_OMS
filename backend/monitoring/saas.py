"""Phase S11 Part 3: the platform's own health, as alertable metrics.

CLOSES R7. Phase S10 found that `monitoring/alerts.py` held fifteen rules
that all worked and all described the PREVIOUS product -- database, disk,
Redis, biometric devices, punch queues, backups. Nothing fired when the SaaS
platform itself was in trouble: provisioning silently failing, a customer
stuck on a domain they have paid for, receipts ageing unreviewed, or the 5xx
rate climbing.

NOT IN THE TENANT-FACING BOARD, AND THIS IS THE IMPORTANT PART.
`monitoring/views.py` serves `metrics.collect()` to `IsOperator`, which is a
TENANT role -- an HR or Admin user inside a customer's workspace. Every
number in this module is platform-wide: how many tenants are stuck, which
hostnames are failing, what the payment backlog is across all customers.
Putting it in the default board would hand every customer's administrator a
running commentary on every other customer.

So `metrics.collect()` excludes this section unless asked, the tenant-facing
views never ask, and `check_alerts` (a cron job with no HTTP caller) does.
`test_saas_alerts.py` asserts that boundary directly.

EVERY QUERY RUNS IN `no_tenant()`. These are platform-global tables, and
under row-level security a platform connection is the only one that can read
them at all.
"""
import logging

from django.utils import timezone

from .metrics import AMBER, GREEN, RED, _band, _metric, safe

logger = logging.getLogger(__name__)


def _hours_ago(hours):
    return timezone.now() - timezone.timedelta(hours=hours)


@safe
def saas_platform():
    """Ten metrics, covering the nine alert categories S11 Part 3 names."""
    from tenancy.context import no_tenant

    with no_tenant():
        return [
            _provisioning_stuck(),
            _provisioning_failed(),
            _registration_stalled(),
            _verification_failed(),
            _domains_failing(),
            _exports_failed(),
            _restore_needed(),
            _subscription_expiry(),
            _payment_backlog(),
            _server_error_rate(),
        ]


# ---------------------------------------------------------------------------
# 1 + 2. provisioning
# ---------------------------------------------------------------------------
def _provisioning_stuck():
    """A workspace half-built, with the customer already waiting.

    RED at one, not at three. Provisioning is a single atomic call that
    takes under a second; a tenant still in this state is not busy, it is
    broken, and there is exactly one person inconvenienced per occurrence.
    """
    from tenancy.models import Organization

    stuck = list(Organization.objects
                 .filter(status=Organization.Status.PROVISIONING)
                 .values_list("slug", flat=True)[:10])
    return _metric(
        "saas_provisioning_stuck", "Tenants stuck provisioning", len(stuck),
        RED if stuck else GREEN, unit="tenants",
        detail=", ".join(stuck) or None)


def _provisioning_failed():
    """Attempts that failed outright, from the platform's own counters."""
    from django.db.models import Sum

    from tenancy.models import PlatformMetric

    since = timezone.localdate() - timezone.timedelta(days=1)
    failed = (PlatformMetric.objects
              .filter(key=PlatformMetric.Key.PROVISION_FAILED, day__gte=since)
              .aggregate(n=Sum("count"))["n"] or 0)
    return _metric(
        "saas_provisioning_failed", "Provisioning failures (24h)", failed,
        _band(failed, amber=1, red=3), unit="failures",
        detail="A verified registration that produced no workspace."
        if failed else None)


# ---------------------------------------------------------------------------
# 3 + 4. registration and verification
# ---------------------------------------------------------------------------
def _registration_stalled():
    """Verified their email, and still has no workspace.

    THE WORST STATE IN THE PRODUCT. The customer has done everything asked
    of them and been told so; the platform owes them a workspace and has not
    produced one. Phase S7's own copy promises support will be in touch, so
    somebody has to know.
    """
    from tenancy.models import PendingRegistration

    stalled = list(
        PendingRegistration.objects
        .filter(status=PendingRegistration.Status.VERIFIED,
                verified_at__lt=_hours_ago(1))
        .values_list("slug", flat=True)[:10])
    return _metric(
        "saas_registration_stalled", "Verified signups with no workspace",
        len(stalled), RED if stalled else GREEN, unit="signups",
        detail=", ".join(stalled) or None)


def _verification_failed():
    """Registrations that expired unverified, or failed outright.

    Some of this is normal -- people abandon forms. It is graded on VOLUME
    for that reason: a handful is churn, a cliff is a mail transport that
    has stopped delivering, which is invisible from everywhere else because
    the platform's own send succeeded.
    """
    from tenancy.models import PendingRegistration

    statuses = [PendingRegistration.Status.EXPIRED]
    if hasattr(PendingRegistration.Status, "FAILED"):
        statuses.append(PendingRegistration.Status.FAILED)
    count = (PendingRegistration.objects
             .filter(status__in=statuses, updated_at__gte=_hours_ago(24))
             .count())
    return _metric(
        "saas_verification_failed", "Signups never verified (24h)", count,
        _band(count, amber=3, red=10), unit="signups",
        detail="If this is a cliff rather than a trickle, suspect mail "
               "delivery -- the platform's own send succeeded."
        if count else None)


# ---------------------------------------------------------------------------
# 5. custom domains
# ---------------------------------------------------------------------------
def _domains_failing():
    """A customer who has paid for an address that does not work.

    Counts both outright failures and claims that have been CHECKED THREE
    OR MORE TIMES without succeeding -- which Phase S9's console surfaces as
    the column that matters, because somebody pressing "Check now" eleven
    times has misread their DNS panel and will not work it out alone.
    """
    from django.db.models import Q

    from tenancy.models import TenantDomain

    rows = list(
        TenantDomain.objects
        .filter(Q(status=TenantDomain.Status.FAILED)
                | Q(check_count__gte=3,
                    status=TenantDomain.Status.PENDING))
        .values_list("hostname", flat=True)[:10])
    return _metric(
        "saas_domain_failed", "Custom domains not verifying", len(rows),
        _band(len(rows), amber=1, red=3), unit="domains",
        detail=", ".join(rows) or None)


# ---------------------------------------------------------------------------
# 6 + 7. exports and restores
# ---------------------------------------------------------------------------
def _exports_failed():
    """A leaving customer waiting for data that is not coming."""
    from tenancy.models import TenantExport

    rows = list(
        TenantExport.objects
        .filter(status=TenantExport.Status.FAILED,
                created_at__gte=_hours_ago(24))
        .values_list("organization__slug", flat=True)[:10])
    return _metric(
        "saas_export_failed", "Exports failed (24h)", len(rows),
        _band(len(rows), amber=1, red=3), unit="exports",
        detail=", ".join(rows) or None)


def _restore_needed():
    """Archived, and still paying.

    THERE IS NO "RESTORE FAILED" RECORD TO COUNT, because a restore is a
    single console action that either completes or raises. What can be
    detected -- and is the state a failed restore actually leaves behind --
    is a tenant whose workspace is closed while its subscription says it
    should be open. That is also what a wrongly-archived customer looks
    like, and both need the same phone call.
    """
    from tenancy.models import Organization, Subscription

    rows = list(
        Organization.objects
        .filter(status=Organization.Status.ARCHIVED,
                subscription__status__in=[Subscription.Status.ACTIVE,
                                          Subscription.Status.TRIAL,
                                          Subscription.Status.GRACE])
        .values_list("slug", flat=True)[:10])
    return _metric(
        "saas_restore_needed", "Archived tenants with a live subscription",
        len(rows), RED if rows else GREEN, unit="tenants",
        detail=", ".join(rows) or None)


# ---------------------------------------------------------------------------
# 8. subscriptions
# ---------------------------------------------------------------------------
def _subscription_expiry():
    """Renewals nobody has chased, and tenants already locked out.

    AMBER FOR EXPIRING, RED FOR EXPIRED. The two mean different things to
    whoever is on call: the first is sales work that can wait for office
    hours, the second is a customer who cannot work this morning.
    """
    from tenancy.models import Subscription

    today = timezone.localdate()
    soon = Subscription.objects.filter(
        status__in=[Subscription.Status.ACTIVE, Subscription.Status.TRIAL],
        current_period_end__range=(today, today + timezone.timedelta(days=7)),
    ).count()
    lapsed = Subscription.objects.filter(
        status__in=[Subscription.Status.GRACE, Subscription.Status.EXPIRED,
                    Subscription.Status.SUSPENDED]).count()

    state = RED if lapsed else (AMBER if soon else GREEN)
    bits = []
    if lapsed:
        bits.append(f"{lapsed} in grace, expired or suspended")
    if soon:
        bits.append(f"{soon} expiring within 7 days")
    return _metric(
        "saas_subscription_expiry", "Subscriptions needing attention",
        lapsed + soon, state, unit="subscriptions",
        detail="; ".join(bits) or None)


# ---------------------------------------------------------------------------
# 9. money waiting on us
# ---------------------------------------------------------------------------
def _payment_backlog():
    """How long the OLDEST unreviewed receipt has been waiting.

    MEASURED IN HOURS, NOT COUNT, and that is the whole point. Twenty
    receipts submitted this morning is a busy day; one receipt submitted on
    Friday and still unreviewed on Monday is a customer who paid and is
    locked out, and a count cannot tell those apart.
    """
    from tenancy.models import Payment

    oldest = (Payment.objects
              .filter(status__in=[Payment.Status.SUBMITTED,
                                  Payment.Status.UNDER_REVIEW])
              .order_by("submitted_at", "created_at")
              .first())
    if oldest is None:
        return _metric("saas_payment_backlog", "Oldest unreviewed payment",
                       0, GREEN, unit="hours")
    since = oldest.submitted_at or oldest.created_at
    hours = max(0.0, (timezone.now() - since).total_seconds() / 3600.0)
    return _metric(
        "saas_payment_backlog", "Oldest unreviewed payment", round(hours, 1),
        _band(hours, amber=24, red=72), unit="hours",
        detail=f"{oldest.payment_reference} from "
               f"{getattr(oldest.organization, 'slug', '?')}")


# ---------------------------------------------------------------------------
# 10. the platform answering badly
# ---------------------------------------------------------------------------
def _server_error_rate():
    """The alert the Phase S10 load test needed and did not have.

    Suppressed below a floor of requests, because 1 error in 3 requests is
    100% and means nothing -- a rate on a tiny sample is how a monitoring
    system teaches people to ignore it.
    """
    from . import request_stats

    total, errors, rate = request_stats.window()
    if total < 20:
        return _metric(
            "saas_5xx_rate", "Server error rate", round(rate, 2), GREEN,
            unit="%", detail=f"{errors}/{total} in the last "
                             f"{request_stats.WINDOW_MINUTES}m -- too few "
                             f"requests to judge")
    return _metric(
        "saas_5xx_rate", "Server error rate", round(rate, 2),
        _band(rate, amber=1.0, red=5.0), unit="%",
        detail=f"{errors} of {total} responses in the last "
               f"{request_stats.WINDOW_MINUTES}m were 5xx")
