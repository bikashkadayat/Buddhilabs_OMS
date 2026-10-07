"""The platform console: the five surfaces, as a service layer. NO UI, NO HTTP.

Part 7 of Phase S1 asks for the architecture, not the screens. So this module
is the whole of the console's behaviour as plain functions, and the views that
will call them are a later phase. Writing it this way is not just scope
discipline -- it means the authority checks live where a management command and
a shell session also pass through them, rather than only in a DRF permission
class that an off-HTTP caller never reaches.

THE FIVE SURFACES
    Platform Dashboard        -> dashboard()
    Organization Management   -> list_organizations(), create_organization()
    Tenant Management         -> suspend(), activate()
    Subscription Management   -> change_plan(), extend()
    Payment Verification      -> tenancy.payments (already the service layer)

EVERY FUNCTION HERE CROSSES TENANTS BY DESIGN. That is what a platform console
is. It is therefore the one module permitted to query without a tenant in
context, and it does so inside an explicit ``no_tenant()`` block so the
crossing is a visible decision rather than an unset variable. Once Phase S3
adds the tenant-scoped default managers, that block is what keeps this module
working and keeps every other module honest.
"""
import logging

from django.db.models import Count, Q
from django.utils import timezone

from . import (archive, bootstrap, counters, export, lifecycle, metrics,
               plans, platform_audit, services)
from .context import no_tenant, tenant_context
from .exceptions import TenancyError
from .models import (Organization, Payment, Plan, PlatformAuditLog,
                     Subscription, SubscriptionEvent)

logger = logging.getLogger(__name__)

S = Subscription.Status


class NotPlatformStaff(TenancyError):
    """A non-platform account reached a platform console service."""


def require_platform(user):
    """The single authority check for every function in this module.

    Structural, not a role check: a platform user has ``is_platform_staff`` and
    NO organization, and the database refuses any other combination. So
    "company admins must never access platform administration" is enforced by
    the shape of the row.
    """
    if user is None or not getattr(user, "is_authenticated", True):
        raise NotPlatformStaff("Authentication required.")
    if not getattr(user, "is_platform_staff", False):
        raise NotPlatformStaff(
            "Platform administration is restricted to platform staff.")
    if getattr(user, "organization_id", None) is not None:
        raise NotPlatformStaff(
            "An account bound to an organization cannot administer the "
            "platform.")
    return user


# ---------------------------------------------------------------------------
# Platform Dashboard
# ---------------------------------------------------------------------------
def dashboard(actor):
    """Every widget Part 1 names, and not one cross-tenant read.

    THE CONSTRAINT THAT SHAPES THIS FUNCTION. Phase S5 put a row-level
    security policy on all 106 business tables, and the platform console
    queries with no tenant bound. So an aggregate that touches a tenant table
    -- ``Count("users")`` was the one that did -- returns 0 for every tenant,
    uniformly and silently.

    The answer here is NOT to give the console a BYPASSRLS role. It is that
    nothing on this dashboard reads tenant data at all: organizations, plans,
    subscriptions and payments are platform tables, and headcount and storage
    come from the counters that ``tenancy.counters`` maintains. Which means
    the platform console runs with no privilege over customer records
    whatsoever -- see tenancy/counters.py.
    """
    require_platform(actor)
    with no_tenant():
        by_subscription = dict(
            Organization.objects
            .values_list("subscription_status")
            .annotate(n=Count("id"))
            .values_list("subscription_status", "n"))
        by_org_status = dict(
            Organization.objects
            .values_list("status")
            .annotate(n=Count("id"))
            .values_list("status", "n"))

        summary = counters.seat_summary()

        payments_pending = Payment.objects.filter(
            status__in=[Payment.Status.SUBMITTED,
                        Payment.Status.UNDER_REVIEW]).count()

        # Phase S8: how the manual workflow is actually clearing, and what
        # is coming. Both windowed, because a lifetime total tells an
        # operator nothing about this month.
        from datetime import timedelta

        from django.utils import timezone as _tz
        from .models import Subscription as _Subscription

        since = _tz.now() - timedelta(days=30)
        verified_payments = Payment.objects.filter(
            status=Payment.Status.VERIFIED, verified_at__gte=since).count()
        rejected_payments = Payment.objects.filter(
            status=Payment.Status.REJECTED, updated_at__gte=since).count()
        horizon = _tz.localdate() + timedelta(days=30)
        expiring_soon = (_Subscription.objects
                         .filter(status__in=[_Subscription.Status.ACTIVE,
                                             _Subscription.Status.TRIAL,
                                             _Subscription.Status.GRACE],
                                 current_period_end__lte=horizon)
                         .count())

        O = Organization.Status
        return {
            # --- organizations ---
            "organizations_total": summary["organizations"],
            "organizations_active": by_org_status.get(O.ACTIVE, 0),
            "organizations_trial": by_org_status.get(O.TRIAL, 0),
            "organizations_grace": by_org_status.get(O.GRACE, 0),
            "organizations_suspended": by_org_status.get(O.SUSPENDED, 0),
            "organizations_cancelled": by_org_status.get(O.CANCELLED, 0),
            "organizations_provisioning": by_org_status.get(O.PROVISIONING, 0),
            "organizations_by_status": by_org_status,
            # Kept under its Phase S1 name: the subscription view of the same
            # population. The two differ when an operator has suspended a
            # tenant manually, which is exactly when a console needs both.
            "organizations_by_subscription_status": by_subscription,
            # --- people and storage (from the maintained counters) ---
            "users_total": summary["seats"],
            "storage_bytes": summary["storage_bytes"],
            # --- subscriptions and money ---
            "subscriptions_active": (
                by_subscription.get(S.ACTIVE, 0)
                + by_subscription.get(S.GRACE, 0)),
            "subscriptions_trial": by_subscription.get(S.TRIAL, 0),
            "revenue_forecast": revenue_forecast(actor),
            "payments_pending_verification": payments_pending,
            # --- Phase S8 Part 9 ---
            #
            # Two figures the revenue panel was missing, and they are the two
            # an operator acts on. "Pending" is work waiting on a human;
            # "verified in the last 30 days" is whether the manual workflow
            # is actually clearing -- a queue of 3 means nothing without
            # knowing whether 40 or 0 were settled last month.
            #
            # "Expiring soon" is the forward-looking one: a renewal nobody
            # chases becomes a suspension, and a suspension becomes a support
            # ticket from an office that cannot sign in.
            "payments_verified_30d": verified_payments,
            "payments_rejected_30d": rejected_payments,
            "subscriptions_expiring_30d": expiring_soon,
            "plan_usage": plan_usage(actor),
            # --- the executive summary (revenue dashboard) ---
            "revenue": revenue_summary(actor),
            # --- operations ---
            "system_health": system_health(actor),
        }


def revenue_summary(actor):
    """Money actually collected, and what is about to need a decision.

    COLLECTED IS NOT COMMITTED. ``revenue_forecast`` is what active
    subscriptions are worth per month; this is what verified payments brought
    in, by the date they were verified. An executive needs both: the forecast
    says where the business is heading, the cash says whether the manual
    payment workflow is keeping up with it.
    """
    from datetime import timedelta

    from django.db.models import Sum
    from django.utils import timezone as tz

    from .models import PlatformMetric

    require_platform(actor)
    now = tz.localtime()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_start = (month_start - timedelta(days=1)).replace(day=1)
    today = tz.localdate()
    with no_tenant():
        verified = Payment.objects.filter(status=Payment.Status.VERIFIED)
        this_month = verified.filter(verified_at__gte=month_start).aggregate(
            n=Sum("amount_minor"))["n"] or 0
        last_month = verified.filter(verified_at__gte=last_month_start,
                                     verified_at__lt=month_start).aggregate(
            n=Sum("amount_minor"))["n"] or 0
        trials = [{
            "name": sub.organization.name, "slug": sub.organization.slug,
            "ends_on": sub.trial_end,
            "days_left": (sub.trial_end - today).days if sub.trial_end else None,
        } for sub in (Subscription.objects
                      .filter(status=S.TRIAL, trial_end__isnull=False,
                              trial_end__lte=today + timedelta(days=7))
                      .select_related("organization")
                      .order_by("trial_end")[:8])]
        failed = PlatformMetric.objects.filter(
            key=PlatformMetric.Key.PROVISION_FAILED,
            day__gte=today - timedelta(days=30)).aggregate(
            n=Sum("count"))["n"] or 0
    forecast = revenue_forecast(actor)
    return {
        "currency": forecast["currency"],
        "collected_this_month_minor": this_month,
        "collected_last_month_minor": last_month,
        "mrr_minor": forecast["monthly_minor"],
        "arr_minor": forecast["annual_minor"],
        "trials_ending_7d": trials,
        "provisioning_failed_30d": failed,
    }


def recent(actor, *, limit=6):
    """Part 9: what has just happened, five lists, one request.

    PLATFORM TABLES ONLY, for the same reason as ``dashboard``: the console
    holds no privilege over customer records, so nothing here may need it.

    SIGN-INS ARE TWO KINDS AND SAID TO BE. A customer's sign-ins live in
    their own workspace, where the console cannot and should not look. What
    the platform legitimately knows is (a) its own operators' sign-ins --
    their accounts belong to no tenant -- and (b) the one customer sign-in it
    has a stake in: a new administrator getting in for the first time, which
    ``handover.record_first_sign_in`` writes to the platform trail. Both are
    shown, labelled, and nothing else is implied.

    A registration is shown by organization name and status only. The
    applicant's email is personal data about somebody who may never become a
    customer; the funnel and the signup detail pages are where it belongs.
    """
    from django.contrib.auth import get_user_model

    from .models import PendingRegistration, TenantDomain

    require_platform(actor)
    User = get_user_model()
    with no_tenant():
        organizations = [
            {"name": o.name, "slug": o.slug, "status": o.status,
             "status_display": o.get_status_display(),
             "created_at": o.created_at}
            for o in Organization.objects.order_by("-created_at")[:limit]]

        domains = [
            {"hostname": d.hostname, "status": d.status,
             "status_display": d.get_status_display(),
             "organization_name": d.organization.name,
             "organization_slug": d.organization.slug,
             "created_at": d.created_at}
            for d in (TenantDomain.objects.select_related("organization")
                      .exclude(status=TenantDomain.Status.REMOVED)
                      .order_by("-created_at")[:limit])]

        payments = [
            {"reference": p.payment_reference, "status": p.status,
             "status_display": p.get_status_display(),
             "amount_minor": p.amount_minor, "currency": p.currency,
             "plan_name": p.plan.name,
             "organization_name": p.organization.name,
             "organization_slug": p.organization.slug,
             "updated_at": p.updated_at}
            for p in (Payment.objects.select_related("organization", "plan")
                      .order_by("-updated_at")[:limit])]

        registrations = [
            {"organization_name": r.organization_name, "slug": r.slug,
             "status": r.status, "status_display": r.get_status_display(),
             "created_at": r.created_at}
            for r in PendingRegistration.objects.order_by("-created_at")[:limit]]

        sign_ins = [
            {"kind": "client", "who": (row.changes or {}).get("email", ""),
             "organization_name": row.organization_name,
             "organization_slug": row.organization_slug,
             "at": row.created_at}
            for row in (PlatformAuditLog.objects
                        .filter(action=PlatformAuditLog.Action.ADMIN_FIRST_SIGN_IN)
                        .order_by("-created_at")[:limit])]
        sign_ins += _operator_sign_ins(User, limit)
        sign_ins.sort(key=lambda row: row["at"], reverse=True)

    return {"organizations": organizations, "domains": domains,
            "payments": payments, "registrations": registrations,
            "sign_ins": sign_ins[:limit]}


def _operator_sign_ins(User, limit):
    """Operators' sign-ins, read from the refresh tokens each one issued.

    Every sign-in outstands exactly one refresh token, so the token table is
    a sign-in log the platform already keeps -- and it is platform-global.
    A rotation also outstands one, so only the first token per session would
    be exact; reading ``created_at`` of the newest tokens per operator gives
    "last active" rather than a strict login list, and is labelled that way.
    """
    try:
        from rest_framework_simplejwt.token_blacklist.models import (
            OutstandingToken,
        )
    except Exception:                              # noqa: BLE001
        return []
    staff = {u.pk: u for u in User.all_tenants.filter(is_platform_staff=True)}
    if not staff:
        return []
    rows, seen = [], set()
    for token in (OutstandingToken.objects
                  .filter(user_id__in=list(staff))
                  .order_by("-created_at")[:limit * 4]):
        if token.user_id in seen:
            continue
        seen.add(token.user_id)
        user = staff[token.user_id]
        rows.append({"kind": "operator", "who": user.email,
                     "organization_name": "", "organization_slug": "",
                     "at": token.created_at})
    return rows


def plan_usage(actor):
    """``{plan code: subscription count}`` across the platform."""
    require_platform(actor)
    with no_tenant():
        return dict(
            Plan.objects
            .annotate(n=Count("subscriptions"))
            .values_list("code", "n"))


def revenue_forecast(actor, *, currency=plans.DEFAULT_CURRENCY):
    """Committed recurring revenue, in MINOR UNITS, normalised to a month.

    Counts a tenant once, at the price its OWN subscription was sold at
    (``plan_price``), not at the plan's current list price. A platform that
    forecasts from the price list overstates itself the moment prices rise,
    because the customers on last year's price are still paying last year's
    price -- and ``PlanPrice`` is immutable precisely so that stays knowable.

    A twelve-month plan contributes a twelfth of its price per month. That is
    the only arithmetic here, and it is integer division on minor units, so
    nothing rounds through a float.

    TRIALS ARE EXCLUDED. A trial is not revenue, and a forecast that counts
    trials is a forecast that always looks good.

    Returns ``{"monthly_minor", "annual_minor", "currency",
    "counted", "unpriced"}``. ``unpriced`` is the number of paying tenants
    whose price row could not be resolved -- shown rather than silently
    treated as zero, because a tenant billing nothing is either a mistake or a
    free arrangement, and the operator is the one who knows which.
    """
    require_platform(actor)
    monthly = 0
    counted = 0
    unpriced = 0
    with no_tenant():
        paying = (Subscription.objects
                  .filter(status__in=[S.ACTIVE, S.GRACE])
                  .select_related("plan", "plan_price"))
        for subscription in paying:
            price = subscription.plan_price
            if price is None or price.currency != currency:
                try:
                    price = plans.current_price(subscription.plan,
                                                currency=currency)
                except Exception:  # noqa: BLE001 - NoActivePrice
                    unpriced += 1
                    continue
            months = max(subscription.plan.interval_months, 1)
            monthly += price.amount_minor // months
            counted += 1
    return {
        "currency": currency,
        "monthly_minor": monthly,
        "annual_minor": monthly * 12,
        "counted": counted,
        "unpriced": unpriced,
    }


def system_health(actor):
    """Is the PLATFORM working? Not "is a tenant working" -- see tenant_health.

    Four things, chosen because each one is invisible until it has been broken
    for a while:

      * the database and the cache, because the cache is what the tenant
        resolver and every throttle run on;
      * payments waiting on a human, because the queue has no owner and
        nothing escalates it;
      * subscription mirror drift, because a denormalised column with no
        reconciler is a bug with a delay;
      * tenants stuck in PROVISIONING, because that is a half-built workspace
        and the customer is already waiting.
    """
    require_platform(actor)
    from django.core.cache import cache
    from django.db import connection

    database = "up"
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:  # noqa: BLE001
        database = "down"

    try:
        cache.set("platform:health", "1", 5)
        cache_state = "up" if cache.get("platform:health") == "1" else "degraded"
    except Exception:  # noqa: BLE001
        cache_state = "down"

    with no_tenant():
        stuck = list(
            Organization.objects
            .filter(status=Organization.Status.PROVISIONING)
            .values_list("slug", flat=True))
        drift = services.reconcile_mirrors()
        pending = Payment.objects.filter(
            status__in=[Payment.Status.SUBMITTED,
                        Payment.Status.UNDER_REVIEW]).count()

    problems = []
    if database != "up":
        problems.append("database")
    if cache_state == "down":
        problems.append("cache")
    if stuck:
        problems.append(f"{len(stuck)} organization(s) stuck provisioning")
    if drift:
        problems.append(f"{len(drift)} subscription mirror(s) drifted")

    return {
        "status": "ok" if not problems else "degraded",
        "database": database,
        "cache": cache_state,
        "payments_pending_verification": pending,
        "organizations_stuck_provisioning": stuck,
        "subscription_mirror_drift": drift,
        "problems": problems,
    }


# ---------------------------------------------------------------------------
# Organization Management (Part 2)
# ---------------------------------------------------------------------------
def list_organizations(actor, *, status=None, search=None,
                       organization_status=None):
    """The tenant list. ``status`` filters on the SUBSCRIPTION mirror.

    ``organization_status`` filters on the organization's own status instead.
    Both exist because they answer different questions -- "who has not paid"
    and "who cannot get in" -- and they differ exactly when an operator has
    intervened.
    """
    require_platform(actor)
    with no_tenant():
        queryset = (Organization.objects
                    .select_related("subscription", "subscription__plan")
                    .order_by("name"))
        if status:
            queryset = queryset.filter(subscription_status=status)
        if organization_status:
            queryset = queryset.filter(status=organization_status)
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(slug__icontains=search)
                | Q(email__icontains=search))
        return queryset


def create_organization(actor, *, request=None, **fields):
    """Provision a tenant. The ONLY sanctioned way one comes into existence.

    There is still no self-service registration, by instruction. A tenant
    exists because a platform operator created it, and ``created_by`` records
    who.

    ONE CALL IS THE WHOLE JOB (Phase S6 Part 3). This returns an organization
    that is bootstrapped, optionally has its first administrator, and is out
    of PROVISIONING -- so "tenant creation is one-click" is true of the
    service layer and not only of the screen in front of it.
    """
    require_platform(actor)

    # Branding and the custom domain are applied AFTER the tenant exists,
    # because both need its primary key -- the media path is keyed by tenant
    # token, and a domain claim is a row pointing at the organization. They
    # are popped here rather than passed through so `provision_organization`
    # keeps the one job it has.
    branding = {key: fields.pop(key, None)
                for key in ("logo", "favicon", "color_primary",
                            "color_secondary")}
    domain = fields.pop("domain", None)

    with no_tenant():
        organization = services.provision_organization(
            actor=actor, request=request, **fields)

        if any(v for v in branding.values()):
            _apply_initial_branding(organization, actor=actor,
                                    request=request, **branding)

        if domain:
            _claim_initial_domain(organization, domain, actor=actor,
                                  request=request)

    logger.info("organization %s provisioned by platform user %s",
                organization.slug, actor.pk)
    return organization


def _apply_initial_branding(organization, *, logo=None, favicon=None,
                            color_primary="", color_secondary="",
                            actor=None, request=None):
    """Brand a tenant at the moment it is created.

    PRIVATE, like `_claim_initial_domain` beside it. Both are steps inside
    `create_organization`, which has already checked the caller is platform
    staff; neither takes an actor to authorise. `test_platform_console` has
    a guard that every PUBLIC console function is covered by the authority
    boundary test, and it caught the first version of this function being
    public with no `require_platform` of its own -- a module-level function
    anybody could import and call against any tenant.

    ONE LOGO BECOMES ALL FOUR. The operator uploads a single image and it is
    written to `logo_primary`, `logo_login`, `logo_email` and
    `logo_letterhead` -- the application header, the sign-in page, the top of
    every notification email and every PDF letterhead.

    Writing it four times rather than teaching four readers to fall back
    through a chain is deliberate: each slot is independently replaceable
    afterwards. A customer who later uploads a wide monochrome lockup for
    their letterhead changes that one and keeps the square mark everywhere
    else, which a fallback chain would not allow without a second flag per
    slot.

    The FAVICON lives on `Organization`, not on the branding row -- modelled
    in Phase S1 as identity, before branding existed as its own record.
    """
    from .models import OrganizationBranding, PlatformAuditLog

    row, _ = OrganizationBranding.objects.get_or_create(
        organization=organization)

    changed = []
    if logo is not None:
        for slot in ("logo_primary", "logo_login", "logo_email",
                     "logo_letterhead"):
            # `save=False` then one save: four ImageField assignments would
            # otherwise be four writes of the same file.
            getattr(row, slot).save(logo.name, logo, save=False)
            logo.seek(0)
        changed.append("logo")
    if color_primary:
        row.color_primary = color_primary
        changed.append("color_primary")
    if color_secondary:
        row.color_secondary = color_secondary
        changed.append("color_secondary")
    if changed:
        row.save()

    if favicon is not None:
        organization.favicon = favicon
        organization.save(update_fields=["favicon", "updated_at"])
        changed.append("favicon")

    if changed:
        platform_audit.record(
            actor, PlatformAuditLog.Action.BRANDING_CHANGED,
            organization=organization,
            changes={"set_at_creation": changed},
            note="Branding supplied when the tenant was created, so the "
                 "customer never has to upload it again.",
            request=request)

    # The PDF letterhead logo is cached per tenant for the life of the
    # process; a tenant created in this process must not inherit a cached
    # miss from before it existed.
    from documents.pdf import forget_logo

    forget_logo(organization)
    return row


def _claim_initial_domain(organization, hostname, *, actor=None,
                          request=None):
    """Claim a custom hostname for a tenant being created.

    A CLAIM, NOT A GRANT, and that distinction is the whole of Phase S9.
    This endpoint used to write `Organization.domain` -- the single
    unverified column -- which the resolver honours, so an operator typing a
    hostname was granting it outright: a competitor's name, a bank's or a
    typo would have resolved to this tenant.

    The tenant's SUBDOMAIN works the moment it exists, so "open under its
    assigned domain" is satisfied without this. A custom domain begins
    resolving when DNS confirms the customer owns it, and not before.

    A refusal here does NOT undo the tenant. The workspace is already built
    and usable on its subdomain; losing it because a hostname was mistyped
    would be a far worse outcome than an operator claiming the domain again
    from the tenant's own page.
    """
    from . import domains

    try:
        return domains.claim(organization, hostname, actor=actor,
                             request=request)
    except TenancyError as exc:
        logger.warning("tenant %s was created but its domain %r could not be "
                       "claimed: %s", organization.slug, hostname, exc)
        return None


# Fields a platform operator may edit after creation.
#
# An ALLOW-LIST, not a block-list. `slug` is absent because it is the tenant's
# hostname and renaming it breaks every bookmark and every signed media URL;
# `document_prefix` is absent because it is embedded in document numbers
# already issued, and a document number is a historical record. Both are
# changeable, but through a deliberate migration path rather than a form.
EDITABLE_FIELDS = frozenset({
    "name", "email", "phone", "address", "industry", "country", "timezone",
    "site_url", "domain", "fiscal_calendar",
})


def update_organization(actor, organization, *, request=None, **fields):
    """Edit a tenant's details. Records exactly what changed."""
    require_platform(actor)
    _refuse_if_archived(organization, "update")
    rejected = sorted(set(fields) - EDITABLE_FIELDS)
    if rejected:
        raise TenancyError(
            f"These fields cannot be edited from the platform console: "
            f"{rejected}. Allowed: {sorted(EDITABLE_FIELDS)}.")

    changes = {}
    with no_tenant():
        for field, value in fields.items():
            before = getattr(organization, field)
            if before == value:
                continue
            setattr(organization, field, value)
            changes[field] = {"from": before, "to": value}
        if not changes:
            return organization
        organization.full_clean(exclude=["created_by"])
        organization.save()

    platform_audit.record(
        actor, PlatformAuditLog.Action.TENANT_UPDATED,
        organization=organization, changes=changes, request=request)
    return organization


def organization_detail(actor, slug):
    require_platform(actor)
    with no_tenant():
        organization = (Organization.objects
                        .select_related("subscription", "subscription__plan",
                                        "subscription__plan_price",
                                        "settings", "branding")
                        .get(slug=slug))
        return {
            "organization": organization,
            "subscription": getattr(organization, "subscription", None),
            "events": list(organization.subscription_events.all()[:50]),
            "payments": list(organization.payments.all()[:50]),
            "audit": list(platform_audit.entries(organization=organization,
                                                 limit=50)),
            # The maintained counter, NOT a count. See counters.py.
            "users": organization.seat_count,
            "storage_bytes": organization.storage_bytes,
            "mirror_drift": services.mirror_drift(organization),
        }


def organization_usage(actor, organization):
    """What this tenant is consuming, against what its plan includes.

    Reads the maintained counters only, so it needs no tenant-table access
    and stays one row. ``over_seats`` is reported rather than enforced: Phase
    S6 does not meter, and a platform that locks a customer out of their own
    HR system over a seat count without warning them first is not a platform
    anybody keeps.
    """
    require_platform(actor)
    with no_tenant():
        subscription = getattr(organization, "subscription", None)
        plan = getattr(subscription, "plan", None)
        included = getattr(plan, "included_seats", None)
        return {
            "slug": organization.slug,
            "seats_used": organization.seat_count,
            "seats_included": included,
            "over_seats": bool(included and organization.seat_count > included),
            "storage_bytes": organization.storage_bytes,
            "plan": getattr(plan, "code", None),
            "billing_mode": getattr(plan, "billing_mode", None),
            "counters_updated_at": organization.updated_at,
        }


def tenant_health(actor, organization):
    """Is this tenant's WORKSPACE complete and open?

    Distinct from ``system_health``, which asks about the platform. This is
    the check that would have caught the Phase S5 finding -- a provisioned
    tenant with no leave types -- and it is the assertion the conformance test
    makes.

    THE VERDICT IS A SEPARATE FIELD FROM THE REASON, and both are returned.
    The brief asks for one of two answers -- "Tenant Ready" or "Provisioning
    Incomplete" -- and an operator needs exactly that at a glance. But a
    verdict with no detail is not actionable, so `configuration_gaps` still
    names every missing row and `repair` is the remedy.

    NOTE WHAT "READY" MEANS: configuration complete AND admitted. A fully
    bootstrapped tenant whose subscription has lapsed is not ready, because
    nobody there can sign in -- so reporting it as ready would be reporting
    on the half of the question the customer cannot see.
    """
    require_platform(actor)
    with no_tenant():
        gaps = bootstrap.verify_organization(organization)
        subscription = getattr(organization, "subscription", None)
        ready = not gaps and organization.is_admitted
        drift = services.mirror_drift(organization)
        return {
            "slug": organization.slug,
            "status": organization.status,
            "subscription_status": organization.subscription_status,
            "is_admitted": organization.is_admitted,
            "configuration_gaps": gaps,
            "ready": ready,
            "verdict": "Tenant Ready" if ready else "Provisioning Incomplete",
            # Kept as well as `ready`: the console and the conformance tests
            # written in Phase S6 read this name.
            "usable": ready,
            "expires_on": organization.subscription_expiry,
            "days_until_expiry": (
                subscription.days_until_expiry() if subscription else None),
            "mirror_drift": drift,
            # --- Phase S6.5 Part 7 ---
            #
            # The four questions an operator has about a tenant that are not
            # "is it configured": how much of it is there, does its billing
            # mirror still agree, is it open or closed, and could we hand it
            # back today. Each is cheap -- counters and platform tables, no
            # tenant scan -- for the reason the dashboard is: under RLS the
            # console cannot count tenant rows at all.
            "storage": {
                "bytes": organization.storage_bytes,
                "seats": organization.seat_count,
            },
            "pending_drift": {
                "has_drift": bool(drift),
                "detail": drift,
                # Named so the console does not have to know that correcting
                # drift is a different endpoint from detecting it.
                "remedy": "POST /api/v1/platform/mirrors/reconcile/",
            },
            "subscription_state": {
                "status": getattr(subscription, "status", None),
                "plan": getattr(getattr(subscription, "plan", None), "code",
                                None),
                "period_end": getattr(subscription, "current_period_end", None),
                "auto_renew": getattr(subscription, "auto_renew", None),
                "preserved_through_archive": True,
            },
            "archive_state": archive.archive_state(organization),
            "export_readiness": export_readiness(actor, organization),
        }


def _refuse_if_archived(organization, action):
    """An archived workspace generates no activity (Phase S6.5 Part 2).

    THE RULE, AND WHY IT IS NOT JUST TIDINESS. Archiving promises a faithful
    restore, which is only worth anything if nothing changes in the meantime.
    Without this guard the console's own verbs quietly worked around the
    archive: ``activate`` clears the status pin and moves the subscription, so
    an operator reaching for the button they use every day would have left a
    tenant whose billing was ACTIVE, whose workspace was ARCHIVED, and whose
    pin -- the thing stopping the next renewal reopening it -- was gone.

    READS AND THE EXPORT ARE DELIBERATELY NOT GUARDED. Health, usage, the
    audit trail and especially ``create_export`` must all keep working on an
    archived tenant: handing a departed customer their data is the main reason
    to archive rather than cancel, and it often happens weeks afterwards.
    """
    if organization.status != Organization.Status.ARCHIVED:
        return
    raise TenancyError(
        f"{organization.slug} is archived, so '{action}' is refused: an "
        f"archived workspace must not change while its data is being "
        f"preserved. Restore it first.")


def export_readiness(actor, organization):
    """Could this tenant be handed back today, and when was it last?

    "Ready" here is not a promise that an export will succeed -- only running
    one proves that, which is why the last attempt's outcome is reported
    rather than a prediction. What it does say is whether anything structural
    stands in the way: a workspace still being provisioned has no coherent
    dataset to export, and a tenant whose configuration has gaps would export
    a bundle that is faithful but incomplete as a workspace.
    """
    require_platform(actor)
    from .models import TenantExport

    with no_tenant():
        last = (TenantExport.objects
                .filter(organization=organization)
                .order_by("-created_at")
                .first())
        ready = organization.status != organization.Status.PROVISIONING
        return {
            "can_export": ready,
            "blocked_by": (None if ready else
                           "the workspace is still being provisioned"),
            "last_export": None if last is None else {
                "id": str(last.pk),
                "status": last.status,
                "contents": last.contents,
                "created_at": last.created_at,
                "completed_at": last.completed_at,
                "rows": last.row_count,
                "media_files": last.media_count,
                "bytes": last.size_bytes,
                "sha256": last.sha256,
                "downloadable": last.is_downloadable,
                "expires_at": last.expires_at,
                "error": last.error,
            },
            "exports_held": TenantExport.objects.filter(
                organization=organization).count(),
        }


# ---------------------------------------------------------------------------
# Phase S6.5: export, archive, restore
# ---------------------------------------------------------------------------
def create_export(actor, organization, *, contents=None, request=None):
    """Export a tenant's data. Audited by ``tenancy.export``.

    Runs in ``no_tenant()`` like every other console call, and the builder
    enters ``tenant_context`` for the tenant's own tables -- so the one place
    that crosses into customer data is a single, greppable block inside
    ``tenancy/export.py`` rather than this module.
    """
    require_platform(actor)
    with no_tenant():
        return export.create_export(organization, actor=actor,
                                    contents=contents, request=request)


def list_exports(actor, organization=None):
    """Every export, newest first. Platform-only -- a tenant cannot list these."""
    require_platform(actor)
    from .models import TenantExport

    with no_tenant():
        queryset = (TenantExport.objects
                    .select_related("organization", "requested_by")
                    .order_by("-created_at"))
        if organization is not None:
            queryset = queryset.filter(organization=organization)
        return list(queryset)


def get_export(actor, organization, export_id):
    require_platform(actor)
    from .models import TenantExport

    with no_tenant():
        try:
            return TenantExport.objects.get(pk=export_id,
                                            organization=organization)
        except TenantExport.DoesNotExist:
            raise TenancyError("No such export for this organization.") from None


def record_export_download(actor, export_row, *, request=None):
    """Stamp and audit a download. Called by the view that streams the file.

    AUDITED SEPARATELY FROM CREATION because they answer different questions.
    "We produced an export" is a platform action; "somebody took a copy of a
    customer's entire dataset off the platform" is the one a security review
    asks about, and it can happen repeatedly, to a bundle made months ago, by
    an operator who was not the one who made it.
    """
    require_platform(actor)
    from .models import PlatformAuditLog

    with no_tenant():
        export_row.downloaded_at = timezone.now()
        export_row.download_count = (export_row.download_count or 0) + 1
        export_row.last_downloaded_by = (actor if getattr(actor, "pk", None)
                                         else None)
        export_row.save(update_fields=["downloaded_at", "download_count",
                                       "last_downloaded_by"])
        platform_audit.record(
            actor, PlatformAuditLog.Action.EXPORT_DOWNLOADED,
            organization=export_row.organization,
            changes={"export_id": str(export_row.pk),
                     "sha256": export_row.sha256,
                     "bytes": export_row.size_bytes,
                     "download_count": export_row.download_count},
            note=(f"Export downloaded ({export_row.row_count} rows, "
                  f"{export_row.media_count} media file(s))."),
            request=request)
    return export_row


def archive_organization(actor, organization, *, reason, request=None):
    """Close a workspace and preserve it. See ``tenancy.archive``."""
    require_platform(actor)
    with no_tenant():
        return archive.archive_organization(organization, actor=actor,
                                            reason=reason, request=request)


def restore_organization(actor, organization, *, note="", to_status=None,
                         request=None):
    """Reopen an archived workspace in the state it was closed in."""
    require_platform(actor)
    with no_tenant():
        return archive.restore_organization(
            organization, actor=actor, note=note, to_status=to_status,
            request=request)


def launch_readiness(actor):
    """Phase S6.75 Part 5: is this platform fit to sell workspaces?

    A DIFFERENT QUESTION FROM `system_health`, which is why it is a
    different function. Health asks whether the platform is working; this
    asks whether a customer arriving in the next five minutes would get a
    working workspace. A platform with a healthy database, a warm cache and
    no purchasable plan is perfectly healthy and completely unfit to sell.
    """
    require_platform(actor)
    from . import launch

    with no_tenant():
        return launch.audit()


def event_dashboard(actor, *, days=30):
    """Phase S6.75 Part 4: the eight series the brief names.

    Six come from `PlatformAuditLog`, which is platform-global. Two --
    logins -- come from a maintained counter, because a sign-in is recorded
    in the TENANT's audit trail and under row-level security the console
    counts zero of those. See `tenancy.metrics`.
    """
    require_platform(actor)
    from . import metrics

    with no_tenant():
        return metrics.dashboard(days=max(1, min(int(days or 30), 365)))


def registration_funnel(actor, *, days=30):
    """Phase S7 Part 11: who started, verified, and got a workspace.

    Counted from the registration rows themselves, so the funnel cannot
    drift from the thing it describes. See `tenancy.metrics`.
    """
    require_platform(actor)
    from . import metrics

    with no_tenant():
        return metrics.registration_funnel(
            days=max(1, min(int(days or 30), 365)))


def repair_mirrors(actor, *, request=None):
    """Correct every drifted subscription mirror. Audited, per tenant.

    ONE AUDIT ENTRY PER ORGANIZATION rather than one for the run. A mirror
    repair changes whether a named customer is admitted, so it has to be
    findable on that customer's trail -- an operator asking "why did this
    tenant's access change on Tuesday" will be reading one organization's
    history, not a list of maintenance jobs.
    """
    require_platform(actor)
    with no_tenant():
        repaired = services.repair_mirrors()
        for slug, problem in repaired.items():
            organization = Organization.objects.filter(slug=slug).first()
            platform_audit.record(
                actor, PlatformAuditLog.Action.SUBSCRIPTION_CHANGED,
                organization=organization,
                changes={"mirror_repaired": problem},
                note=("Subscription mirror corrected from the subscription "
                      f"record: {problem}."),
                request=request)
    return {"repaired": repaired}


def repair_configuration(actor, organization, *, request=None):
    """Re-run the bootstrap on an existing tenant. Fills gaps, overwrites nothing.

    Exists because the gap it closes is real and already happened: every
    tenant provisioned before Phase S6 has no configuration at all. It is
    also the remedy a ``tenant_health`` finding points at.
    """
    require_platform(actor)
    _refuse_if_archived(organization, "repair configuration")
    with no_tenant():
        created = bootstrap.bootstrap_organization(
            organization, actor=actor, request=request)
    remaining = bootstrap.verify_organization(organization)
    # The SAME definition of ready as `tenant_health`, deliberately: a repair
    # that reported "Tenant Ready" for a suspended tenant would contradict the
    # health screen the operator just came from.
    ready = not remaining and organization.is_admitted
    return {"created": created,
            "remaining_gaps": remaining,
            "ready": ready,
            "verdict": "Tenant Ready" if ready else "Provisioning Incomplete"}


# ---------------------------------------------------------------------------
# Tenant Management (Part 2 / Part 6)
# ---------------------------------------------------------------------------
def suspend(actor, organization, *, reason, request=None):
    """Lock a tenant out. DELETES NOTHING -- suspension is a gate.

    A reason is required because this is the action a customer will phone
    about.
    """
    require_platform(actor)
    _refuse_if_archived(organization, "suspend")
    if not (reason or "").strip():
        raise TenancyError("A suspension reason is required.")
    with no_tenant():
        subscription = services.transition(
            organization.subscription, S.SUSPENDED,
            actor=actor, note=reason.strip())
        # PIN IT. The subscription is now SUSPENDED and the mirror has
        # derived the same thing -- but a payment clearing tomorrow would
        # move the subscription to ACTIVE and take the workspace with it.
        # An operator suspension is a decision about the customer, not about
        # their balance, so it outlives the balance changing.
        organization.refresh_from_db()
        lifecycle.transition_organization(
            organization, Organization.Status.SUSPENDED, actor=actor,
            reason=reason.strip(), audit=False, request=request,
            override=True)
    platform_audit.record(
        actor, PlatformAuditLog.Action.TENANT_SUSPENDED,
        organization=organization,
        changes={"subscription_status": S.SUSPENDED},
        note=reason.strip(), request=request)
    return subscription


def activate(actor, organization, *, note="", request=None):
    """Let a tenant back in without taking a payment (operator override).

    CLEARS ANY STICKY SUSPENSION. If this tenant's status was pinned by an
    operator (``Organization.status_override``), letting them back in is the
    decision to stop pinning it -- otherwise the tenant would be admitted
    now and silently re-suspended the next time the mirror ran.
    """
    require_platform(actor)
    _refuse_if_archived(organization, "activate")
    with no_tenant():
        lifecycle.clear_override(organization, actor=actor, request=request)
        subscription = organization.subscription
        start, end = services.extend_period(subscription, subscription.plan)
        subscription = services.transition(
            subscription, S.ACTIVE, actor=actor,
            period_start=start, period_end=end,
            note=note or "Reactivated by platform administration.")
    platform_audit.record(
        actor, PlatformAuditLog.Action.TENANT_ACTIVATED,
        organization=organization,
        changes={"period_start": str(start), "period_end": str(end)},
        note=note, request=request)
    return subscription


def cancel(actor, organization, *, reason, request=None):
    """End the relationship. STILL DELETES NOTHING.

    Cancellation is not deletion and is not terminal: a customer who comes
    back is reactivated, keeping their history. Erasing a tenant's data is a
    separate, irreversible operation with its own retention policy, and it is
    deliberately not a button on this console.
    """
    require_platform(actor)
    _refuse_if_archived(organization, "cancel")
    if not (reason or "").strip():
        raise TenancyError("A cancellation reason is required.")
    with no_tenant():
        subscription = services.transition(
            organization.subscription, S.CANCELLED,
            actor=actor, note=reason.strip())
    platform_audit.record(
        actor, PlatformAuditLog.Action.TENANT_CANCELLED,
        organization=organization, changes={"subscription_status": S.CANCELLED},
        note=reason.strip(), request=request)
    return subscription


def set_organization_status(actor, organization, status, *, reason="",
                            request=None):
    """The operator's direct override of the workspace gate (Part 6).

    Separate from ``suspend``/``activate``, which move the SUBSCRIPTION and
    let the mirror derive the organization status from it. This moves the
    organization alone -- for the cases where the two must disagree, which is
    what ``Organization.status`` is for:

      * finishing a provisioning that was deliberately left open;
      * suspending for abuse while the customer is fully paid up, where a
        payment clearing tomorrow must NOT re-open the workspace.

    Validated against the lifecycle, so an impossible move is refused rather
    than written.
    """
    require_platform(actor)
    _refuse_if_archived(organization, "set status")
    # PIN ONLY THE GATES. Choosing SUSPENDED or CANCELLED is a decision about
    # the customer and must survive their next payment; choosing TRIAL or
    # ACTIVE -- finishing a provisioning, say -- is a decision about where
    # they are in their subscription, and the subscription should go on
    # owning it. Pinning those would freeze the tenant's status forever.
    gates = {Organization.Status.SUSPENDED, Organization.Status.CANCELLED}
    with no_tenant():
        if status not in gates:
            lifecycle.clear_override(organization, actor=actor,
                                     request=request)
        return lifecycle.transition_organization(
            organization, status, actor=actor, reason=reason, request=request,
            override=status in gates)


# ---------------------------------------------------------------------------
# Subscription Management (Part 5)
# ---------------------------------------------------------------------------
def change_plan(actor, organization, plan, *, note="", request=None):
    """Move a tenant to a different plan, pinning the price it is sold at.

    Does NOT change the period. Moving plan mid-term and silently re-dating
    the subscription would either hand the customer free time or take paid
    time away; which of those is right is a commercial decision, so the
    operator makes it explicitly with ``extend``.
    """
    require_platform(actor)
    _refuse_if_archived(organization, "change plan")
    with no_tenant():
        subscription = organization.subscription
        previous = subscription.plan
        price = plans.current_price(plan)
        subscription = services.transition(
            subscription, subscription.status,
            event=SubscriptionEvent.Event.PLAN_CHANGED,
            actor=actor, plan=plan, plan_price=price,
            note=note or f"Plan changed to {plan.code} by platform admin.")
    platform_audit.record(
        actor, PlatformAuditLog.Action.PLAN_CHANGED, organization=organization,
        changes={"plan": {"from": previous.code, "to": plan.code},
                 "price_minor": price.amount_minor,
                 "currency": price.currency},
        note=note, request=request)
    return subscription


def assign_plan(actor, organization, plan, *, months=None, note="",
                request=None):
    """Put a tenant on a plan AND sell it a term. The paid-activation path.

    ``change_plan`` alone leaves the period untouched, which is right for a
    correction and wrong for a sale. This is the sale: the plan changes and a
    term of that plan's length is added, so the subscription comes out ACTIVE
    with a real end date.

    ``months`` defaults to the plan's own interval, which is the normal case.
    """
    require_platform(actor)
    _refuse_if_archived(organization, "assign plan")
    with no_tenant():
        subscription = organization.subscription
        previous = subscription.plan
        price = plans.current_price(plan)
        start, end = services.extend_period(subscription, plan)
        if months:
            from .periods import add_months

            end = add_months(start, months)
        subscription = services.transition(
            subscription, S.ACTIVE,
            event=(SubscriptionEvent.Event.PLAN_CHANGED
                   if previous != plan else SubscriptionEvent.Event.ACTIVATED),
            actor=actor, plan=plan, plan_price=price,
            period_start=start, period_end=end,
            note=note or f"Assigned the {plan.code} plan by platform admin.")
    platform_audit.record(
        actor, PlatformAuditLog.Action.SUBSCRIPTION_CHANGED,
        organization=organization,
        changes={"plan": {"from": previous.code, "to": plan.code},
                 "period_start": str(start), "period_end": str(end),
                 "price_minor": price.amount_minor},
        note=note, request=request)
    return subscription


def start_trial(actor, organization, *, plan=None, days=None, note="",
                request=None):
    """Put a tenant (back) on a trial.

    For a tenant that cancelled and is being re-evaluated, and for one whose
    trial an operator wants to restart after a failed onboarding. The trial
    length defaults to the plan's own.
    """
    require_platform(actor)
    _refuse_if_archived(organization, "start trial")
    with no_tenant():
        subscription = organization.subscription
        plan = plan or subscription.plan
        days = plan.trial_days if days is None else days
        if days < 1:
            raise TenancyError("A trial must be at least one day.")
        from django.utils import timezone

        from .periods import add_days

        start = timezone.localdate()
        end = add_days(start, days)
        subscription.trial_start = start
        subscription.trial_end = end
        subscription = services.transition(
            subscription, S.TRIAL,
            event=SubscriptionEvent.Event.TRIAL_STARTED,
            actor=actor, plan=plan, period_start=start, period_end=end,
            note=note or f"{days}-day trial started by platform admin.")
    platform_audit.record(
        actor, PlatformAuditLog.Action.TRIAL_STARTED,
        organization=organization,
        changes={"plan": plan.code, "days": days, "ends": str(end)},
        note=note, request=request)
    return subscription


def extend(actor, organization, *, months, note="", request=None):
    """Add paid time without a payment -- a credit, a goodwill gesture, a fix."""
    require_platform(actor)
    _refuse_if_archived(organization, "extend")
    if months < 1:
        raise TenancyError("months must be at least 1.")
    from .periods import add_months

    with no_tenant():
        subscription = organization.subscription
        base = subscription.current_period_end
        new_end = add_months(base, months)
        subscription = services.transition(
            subscription, S.ACTIVE,
            event=SubscriptionEvent.Event.EXTENDED,
            actor=actor,
            period_start=subscription.current_period_start,
            period_end=new_end,
            note=note or f"Extended by {months} month(s) by platform admin.")
    platform_audit.record(
        actor, PlatformAuditLog.Action.SUBSCRIPTION_EXTENDED,
        organization=organization,
        changes={"months": months, "period_end": {"from": str(base),
                                                  "to": str(new_end)}},
        note=note, request=request)
    return subscription


# ---------------------------------------------------------------------------
# Branding (Part 7)
# ---------------------------------------------------------------------------
# The branding fields a platform operator may set. Images are handled
# separately (they are uploads, not values) -- see set_branding_asset.
BRANDING_FIELDS = frozenset({
    "display_name", "color_primary", "color_secondary", "login_tagline",
    "dashboard_welcome", "report_footer_text", "email_footer_html",
    "letterhead_header_html", "letterhead_footer_html",
})

BRANDING_ASSETS = frozenset({
    "logo_primary", "logo_login", "logo_email", "logo_letterhead",
})

# Part 7 also names a logo and a FAVICON, and those two live on
# ``Organization`` rather than ``OrganizationBranding`` -- they were modelled
# in Phase S1 as identity, before branding existed as its own record. Rather
# than move two columns (which would invalidate every stored file path), this
# routes them to the right row. The console sees one list of assets either way.
ORGANIZATION_ASSETS = frozenset({"logo", "favicon"})


def branding(actor, organization):
    require_platform(actor)
    with no_tenant():
        return organization.branding


def set_branding(actor, organization, *, request=None, **fields):
    """Set a tenant's branding. Validated, audited, and sanitised on render.

    The three ``*_html`` fields are tenant-authored markup. They are stored as
    given and passed through ``common.html_sanitizer`` at every render site --
    sanitising on the way IN would silently discard markup the author can see
    they typed, and would have to be repeated anyway the first time a field is
    populated by any other path.
    """
    require_platform(actor)
    _refuse_if_archived(organization, "set branding")
    rejected = sorted(set(fields) - BRANDING_FIELDS)
    if rejected:
        raise TenancyError(
            f"Not settable branding fields: {rejected}. Allowed: "
            f"{sorted(BRANDING_FIELDS)}. Logos are uploads -- use "
            f"set_branding_asset().")

    changes = {}
    with no_tenant():
        row = organization.branding
        for field, value in fields.items():
            before = getattr(row, field)
            if before == value:
                continue
            setattr(row, field, value)
            # Record WHETHER the html changed, not its content: a letterhead
            # body in an audit row makes the trail unreadable and duplicates
            # data that is already versioned by the row itself.
            changes[field] = ("changed" if field.endswith("_html")
                              else {"from": before, "to": value})
        if not changes:
            return row
        row.full_clean()
        row.save()

    platform_audit.record(
        actor, PlatformAuditLog.Action.BRANDING_CHANGED,
        organization=organization, changes=changes, request=request)
    return row


def set_branding_asset(actor, organization, field, uploaded, *, request=None):
    """Attach one branding image. Stored under the tenant's own media prefix.

    Returns the branding row in every case, including for the two assets that
    are stored on the Organization -- the caller asked to set the tenant's
    branding and should get the tenant's branding back, not have to know which
    table a column happens to sit in.
    """
    require_platform(actor)
    _refuse_if_archived(organization, "set branding asset")
    if field not in BRANDING_ASSETS | ORGANIZATION_ASSETS:
        raise TenancyError(
            f"'{field}' is not a branding asset. Allowed: "
            f"{sorted(BRANDING_ASSETS | ORGANIZATION_ASSETS)}.")
    with no_tenant():
        target = (organization if field in ORGANIZATION_ASSETS
                  else organization.branding)
        setattr(target, field, uploaded)
        target.save(update_fields=[field, "updated_at"])
        row = organization.branding
    platform_audit.record(
        actor, PlatformAuditLog.Action.BRANDING_CHANGED,
        organization=organization,
        changes={field: (getattr(target, field).name
                         if getattr(target, field) else None)},
        request=request)

    # Phase S9: the same eviction the customer-facing writer does. An
    # operator who fixes a customer's logo for them must not leave the old
    # one printing on documents.
    from documents.pdf import forget_logo

    forget_logo(organization)
    return row


def settings_for(actor, organization):
    require_platform(actor)
    with no_tenant():
        return organization.settings


def set_settings(actor, organization, *, request=None, **fields):
    """Set a tenant's operational policy. NULL means inherit the platform."""
    require_platform(actor)
    _refuse_if_archived(organization, "set settings")
    from .models import OrganizationSettings

    allowed = {
        field.name for field in OrganizationSettings._meta.get_fields()
        if getattr(field, "editable", False)
        and field.name not in ("id", "organization", "created_at", "updated_at")
    }
    rejected = sorted(set(fields) - allowed)
    if rejected:
        raise TenancyError(f"Not settable settings fields: {rejected}.")

    changes = {}
    with no_tenant():
        row = organization.settings
        for field, value in fields.items():
            before = getattr(row, field)
            if before == value:
                continue
            setattr(row, field, value)
            changes[field] = {"from": str(before), "to": str(value)}
        if not changes:
            return row
        row.full_clean()
        row.save()
    platform_audit.record(
        actor, PlatformAuditLog.Action.SETTINGS_CHANGED,
        organization=organization, changes=changes, request=request)
    return row


# ---------------------------------------------------------------------------
# Platform audit (Part 9)
# ---------------------------------------------------------------------------
def audit_trail(actor, *, organization=None, action=None, limit=200):
    require_platform(actor)
    with no_tenant():
        return platform_audit.entries(organization=organization,
                                      action=action, limit=limit)


# ---------------------------------------------------------------------------
# Payment Verification
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Payment decisions, from the console
# ---------------------------------------------------------------------------
# THE GAP THESE CLOSE. The service functions (tenancy.payments) were complete
# and tested, and nothing in the product called them: approving a customer's
# payment took an engineer with a Django shell. Every paid conversion waited
# on server access. These are the console's verbs for the same functions,
# plus the three things a shell session never did: the platform audit entry,
# the customer being told, and one step instead of two.
def _payment(payment_id):
    with no_tenant():
        try:
            return (Payment.objects
                    .select_related("organization", "plan", "plan_price")
                    .get(pk=payment_id))
        except (Payment.DoesNotExist, ValueError, TypeError):
            raise TenancyError("That payment does not exist.")


def approve_payment(actor, payment_id, *, note="", request=None):
    """Accept the payment: the subscription activates or extends, now.

    ONE STEP. The service requires a claim before a decision so two
    operators cannot decide the same payment; the console makes the claim
    and the decision in the same transaction when nobody has claimed it yet.
    A payment someone ELSE is already reviewing is not taken from them.
    """
    from django.db import transaction

    from . import payments as payment_services

    require_platform(actor)
    payment = _payment(payment_id)
    with no_tenant(), transaction.atomic():
        if payment.status == Payment.Status.SUBMITTED:
            payment = payment_services.claim_for_review(payment, actor)
        elif (payment.status == Payment.Status.UNDER_REVIEW
              and payment.reviewer_id not in (None, actor.pk)):
            raise TenancyError(
                f"{payment.reviewer.email} is reviewing this payment. "
                f"Leave it to them, or ask them to release it.")
        payment, subscription = payment_services.verify_payment(
            payment, actor, note=note)
        platform_audit.record(
            actor, PlatformAuditLog.Action.PAYMENT_VERIFIED,
            organization=payment.organization,
            changes={"reference": payment.payment_reference,
                     "plan": payment.plan.code,
                     "amount_minor": payment.amount_minor,
                     "currency": payment.currency,
                     "period_end": str(subscription.current_period_end)},
            note=note or f"Payment {payment.payment_reference} approved.",
            request=request)
    _tell_customer(payment, "approved", subscription=subscription)
    return payment, subscription


def reject_payment(actor, payment_id, *, reason, request=None):
    """Refuse the payment. The reason is what the customer will read."""
    from django.db import transaction

    from . import payments as payment_services

    require_platform(actor)
    payment = _payment(payment_id)
    with no_tenant(), transaction.atomic():
        if payment.status == Payment.Status.SUBMITTED:
            payment = payment_services.claim_for_review(payment, actor)
        payment = payment_services.reject_payment(payment, actor, reason=reason)
        platform_audit.record(
            actor, PlatformAuditLog.Action.PAYMENT_REJECTED,
            organization=payment.organization,
            changes={"reference": payment.payment_reference,
                     "reason": payment.rejection_reason},
            note=f"Payment {payment.payment_reference} rejected.",
            request=request)
    _tell_customer(payment, "rejected")
    return payment


def request_payment_information(actor, payment_id, *, message, request=None):
    """Ask the customer for what is missing, without rejecting anything."""
    from django.db import transaction

    from . import payments as payment_services

    require_platform(actor)
    payment = _payment(payment_id)
    with no_tenant(), transaction.atomic():
        payment = payment_services.request_information(payment, actor,
                                                       message=message)
        platform_audit.record(
            actor, PlatformAuditLog.Action.PAYMENT_INFO_REQUESTED,
            organization=payment.organization,
            changes={"reference": payment.payment_reference,
                     "message": payment.review_message},
            note=f"More information requested on {payment.payment_reference}.",
            request=request)
    _tell_customer(payment, "needs_info")
    return payment


def _notify_in_app(payment, outcome, admin):
    """The decision in the administrator's notification centre too. In-app
    only -- the branded email above already went. Never raises."""
    if admin is None:
        return
    try:
        from notifications.models import Category, Notification

        category, title = {
            "approved": (Category.PAYMENT_APPROVED,
                         f"Payment {payment.payment_reference} approved — your subscription is active"),
            "rejected": (Category.PAYMENT_REJECTED,
                         f"Payment {payment.payment_reference} couldn't be confirmed"),
            "needs_info": (Category.PAYMENT_INFO_REQUESTED,
                           f"We need more about payment {payment.payment_reference}"),
        }[outcome]
        body = payment.rejection_reason or payment.review_message or ""
        with tenant_context(payment.organization):
            Notification.objects.create(
                recipient=admin, category=category, title=title[:255], body=body,
                action_url="/settings/subscription")
    except Exception:                              # noqa: BLE001
        logger.warning("in-app payment notification failed", exc_info=True)


def payment_proof(actor, payment_id):
    """The receipt file a customer uploaded, for an operator to look at."""
    require_platform(actor)
    payment = _payment(payment_id)
    if not payment.proof:
        raise TenancyError("This payment has no receipt attached.")
    return payment


def _tell_customer(payment, outcome, *, subscription=None):
    """Email the customer the decision. Never raises: the decision stands.

    In the customer's own branding, to the organization's billing address
    and its administrator -- the person who submitted the receipt is
    usually one of the two, and the other needs to know the workspace is
    paid for.
    """
    from django.conf import settings as dj_settings
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    from notifications.emails import _branding, powered_by

    from . import handover
    from .models import PlatformMetric

    organization = payment.organization
    try:
        admin = handover.administrator(organization)
        # In the workspace first: it must not depend on the mail server.
        _notify_in_app(payment, outcome, admin)
        recipients = sorted({address for address in (
            organization.email, getattr(admin, "email", "")) if address})
        if not recipients:
            return False
        with tenant_context(organization):
            brand = _branding()
        context = {
            **brand, "powered_by": powered_by(), "outcome": outcome,
            "organization_name": organization.name,
            "reference": payment.payment_reference,
            "plan_name": payment.plan.name,
            "amount": f"{payment.currency} {payment.amount_minor / 100:,.2f}",
            "reason": payment.rejection_reason,
            "message": payment.review_message,
            "period_end": getattr(subscription, "current_period_end", None),
            "subscription_url": handover.login_url(organization).rstrip("/")
                                + "/settings/subscription",
            "support_email": getattr(dj_settings, "PLATFORM_SUPPORT_EMAIL", ""),
        }
        subject = {
            "approved": f"Payment received — {organization.name} is active",
            "rejected": f"We couldn't confirm your payment {payment.payment_reference}",
            "needs_info": f"We need a little more about payment {payment.payment_reference}",
        }[outcome]
        message = EmailMultiAlternatives(
            subject, render_to_string("emails/payment_decision.txt", context),
            dj_settings.DEFAULT_FROM_EMAIL, recipients)
        message.attach_alternative(
            render_to_string("emails/payment_decision.html", context), "text/html")
        message.send(fail_silently=False)
        metrics.bump(PlatformMetric.Key.EMAIL_SENT, organization=organization)
        return True
    except Exception:                              # noqa: BLE001
        logger.warning("payment decision email for %s failed",
                       payment.payment_reference, exc_info=True)
        try:
            metrics.bump(PlatformMetric.Key.EMAIL_FAILED, organization=organization)
        except Exception:                          # noqa: BLE001
            pass
        return False


# ---------------------------------------------------------------------------
# Payment methods: where customers send money, managed in the console
# ---------------------------------------------------------------------------
# These rows were editable only through Django admin, which an operator should
# not need. Nothing about payment details is hardcoded anywhere: the customer's
# subscription page reads exactly these rows.
PAYMENT_METHOD_FIELDS = ("method", "label", "account_name", "account_number",
                         "bank_name", "branch", "esewa_id", "khalti_id",
                         "sort_order")


def _instruction_text(html):
    from django.utils.html import strip_tags
    import html as html_lib

    text = (html or "").replace("</p>", "\n\n").replace("<br>", "\n").replace("<br/>", "\n")
    return html_lib.unescape(strip_tags(text)).strip()


def _instruction_html(text):
    """Plain text from the console, made safe HTML for every customer's page.

    Escaped THEN sanitized: an operator types words, not markup, and a
    compromised operator account must not be able to put a script on every
    tenant's subscription page.
    """
    from django.utils.html import escape, linebreaks

    from common.html_sanitizer import sanitize_html

    text = (text or "").strip()
    return sanitize_html(linebreaks(escape(text))) if text else ""


def _payment_method_row(row):
    return {
        "id": str(row.id), "method": row.method, "label": row.label,
        "account_name": row.account_name, "account_number": row.account_number,
        "bank_name": row.bank_name, "branch": row.branch,
        "esewa_id": row.esewa_id, "khalti_id": row.khalti_id,
        "instructions": _instruction_text(row.instructions_html),
        "qr_image": row.qr_image.url if row.qr_image else None,
        "sort_order": row.sort_order, "state": row.state,
        "updated_at": row.updated_at,
    }


def list_payment_methods(actor, *, include_archived=False):
    from .models import PaymentInstruction

    require_platform(actor)
    with no_tenant():
        rows = PaymentInstruction.objects.order_by("sort_order", "label")
        if not include_archived:
            rows = rows.filter(archived_at__isnull=True)
        return [_payment_method_row(row) for row in rows]


def save_payment_method(actor, data, *, method_id=None, qr_image=None,
                        request=None):
    """Create or edit one payment method. Returns its row."""
    from .models import Payment, PaymentInstruction

    require_platform(actor)
    known = {code for code, _ in Payment.Method.choices}
    with no_tenant():
        row = (PaymentInstruction.objects.get(pk=method_id) if method_id
               else PaymentInstruction())
        for field in PAYMENT_METHOD_FIELDS:
            if field in data:
                value = data[field]
                if field == "sort_order":
                    try:
                        value = max(0, min(int(value), 1000))
                    except (TypeError, ValueError):
                        raise TenancyError("Order must be a whole number.")
                else:
                    value = (value or "").strip()
                setattr(row, field, value)
        if "instructions" in data:
            row.instructions_html = _instruction_html(data["instructions"])
        if row.method not in known:
            raise TenancyError("Choose a payment type from the list.")
        if not row.label:
            raise TenancyError("Give the payment method a name customers will recognise.")
        # Each type must carry what a customer needs to actually pay with it.
        if row.method == "bank_transfer" and not (row.account_number and row.bank_name):
            raise TenancyError("A bank transfer needs the bank name and account number.")
        if row.method == "esewa" and not row.esewa_id:
            raise TenancyError("Add the eSewa ID customers should pay to.")
        if row.method == "khalti" and not row.khalti_id:
            raise TenancyError("Add the Khalti ID customers should pay to.")
        if qr_image is not None:
            row.qr_image = qr_image
        if row.method in ("qr", "fonepay") and not row.qr_image:
            raise TenancyError("Upload the QR code customers should scan.")
        created = row._state.adding
        row.save()
        platform_audit.record(
            actor, PlatformAuditLog.Action.PAYMENT_METHOD_CHANGED,
            changes={"method": row.method, "label": row.label,
                     "created": created},
            note=f"Payment method “{row.label}” {'added' if created else 'edited'}.",
            request=request)
        return _payment_method_row(row)


def set_payment_method_state(actor, method_id, state, *, request=None):
    """active / disabled / archived. Archive keeps the row: payments name it."""
    from django.utils import timezone as tz

    from .models import PaymentInstruction

    require_platform(actor)
    if state not in ("active", "disabled", "archived"):
        raise TenancyError("Unknown state.")
    with no_tenant():
        row = PaymentInstruction.objects.get(pk=method_id)
        before = row.state
        if state == "archived":
            row.is_active = False
            row.archived_at = row.archived_at or tz.now()
        else:
            row.archived_at = None
            row.is_active = state == "active"
        row.save(update_fields=["is_active", "archived_at", "updated_at"])
        platform_audit.record(
            actor, PlatformAuditLog.Action.PAYMENT_METHOD_CHANGED,
            changes={"label": row.label, "from": before, "to": state},
            note=f"Payment method “{row.label}”: {before} → {state}.",
            request=request)
        return _payment_method_row(row)


def recent_payment_decisions(actor, *, limit=50):
    """Approved and rejected payments, newest decision first."""
    require_platform(actor)
    with no_tenant():
        return list(Payment.objects
                    .filter(status__in=[Payment.Status.VERIFIED,
                                        Payment.Status.REJECTED])
                    .select_related("organization", "plan", "reviewer",
                                    "verified_by", "created_by")
                    .order_by("-updated_at")[:limit])


def verification_queue(actor):
    """Everything awaiting a platform decision."""
    require_platform(actor)
    from . import payments as payment_services

    with no_tenant():
        return payment_services.pending_queue()
