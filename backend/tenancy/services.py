"""The subscription lifecycle, and the only sanctioned way to move it.

ONE ENTRY POINT
---------------
``transition()`` is the only function permitted to change
``Subscription.status``. ``Subscription.save()`` refuses a status that changed
any other way (see ``DirectStatusChangeForbidden``), so this is enforced rather
than merely documented.

It is the only entry point because a status change is never just a status
change. It is three things that must happen together or not at all:

  1. the new status,
  2. a ``SubscriptionEvent`` row recording who/why/when,
  3. the ``Organization.subscription_*`` mirror the request path reads.

A bare ``sub.status = "active"; sub.save()`` would do the first and skip the
other two, leaving the platform admitting a tenant it has no record of
admitting. All three happen inside one ``transaction.atomic``.

THE LIFECYCLE
-------------
    TRIAL ──────┬──▶ ACTIVE ──▶ GRACE ──▶ SUSPENDED
                │       ▲          │           │
                │       └──────────┴───────────┘   (payment verified)
                │
                └──▶ EXPIRED / SUSPENDED / CANCELLED

``CANCELLED`` is not terminal: a customer who leaves and comes back is a real
case, and refusing it in code would only mean somebody did it in a shell.
"""
import logging

from django.db import transaction
from django.utils import timezone

from . import lifecycle, plans, resolver
from .exceptions import IllegalTransition
from .models import (Organization, OrganizationBranding, OrganizationSettings,
                     Subscription, SubscriptionEvent)
from .periods import add_days, add_months

logger = logging.getLogger(__name__)

S = Subscription.Status
E = SubscriptionEvent.Event

# Which statuses may follow which. Written out as data so the rule is readable
# and testable in one place, rather than inferred from a chain of ifs.
ALLOWED_TRANSITIONS = {
    # GRACE added in Phase S6, and it closes a guaranteed crash rather than
    # adding a feature. `advance_expired` below selects
    # `status__in=[ACTIVE, TRIAL]` past its period end and moves it to GRACE
    # -- so the first trial on the platform to expire raised
    # IllegalTransition, aborting the nightly sweep before it advanced ANY
    # tenant. It was also already the intended behaviour: provisioning sets
    # `grace_until = trial_end + plan.grace_days`, which only means something
    # if a trial can lapse into grace.
    S.TRIAL:     {S.ACTIVE, S.GRACE, S.EXPIRED, S.SUSPENDED, S.CANCELLED},
    S.ACTIVE:    {S.ACTIVE, S.GRACE, S.EXPIRED, S.SUSPENDED, S.CANCELLED},
    S.GRACE:     {S.ACTIVE, S.EXPIRED, S.SUSPENDED, S.CANCELLED},
    S.EXPIRED:   {S.ACTIVE, S.SUSPENDED, S.CANCELLED},
    S.SUSPENDED: {S.ACTIVE, S.GRACE, S.CANCELLED},
    # Win-back. A cancelled customer who returns is reactivated, not recreated.
    S.CANCELLED: {S.ACTIVE, S.TRIAL},
}

# Which organization status each subscription status implies. The Organization's
# own status is the operator's switch (a manual suspension survives a renewal);
# this map is only consulted for the statuses that are genuinely derived.
ORG_STATUS_FOR = {
    S.TRIAL: Organization.Status.TRIAL,
    S.ACTIVE: Organization.Status.ACTIVE,
    # Phase S6 Part 6: was ACTIVE, which hid the one state an operator most
    # needs to see -- "still admitted, but about to be locked out". A grace
    # tenant is admitted either way (Organization.is_admitted), so this
    # changed what the console shows, not who gets in.
    S.GRACE: Organization.Status.GRACE,
    S.SUSPENDED: Organization.Status.SUSPENDED,
    S.EXPIRED: Organization.Status.SUSPENDED,
    S.CANCELLED: Organization.Status.CANCELLED,
}


# ---------------------------------------------------------------------------
# The mirror
# ---------------------------------------------------------------------------
def sync_subscription_mirror(subscription, *, save=True):
    """Refresh ``Organization.subscription_*`` from the subscription.

    The mirror exists because every HTTP request must answer "is this tenant
    admitted?", and that cannot be a join against a billing table on the hot
    path. The fields are ``editable=False``, so this function is the only thing
    that writes them.
    """
    org = subscription.organization
    org.subscription_status = subscription.status
    org.subscription_start = subscription.current_period_start
    org.subscription_expiry = subscription.current_period_end

    derived = ORG_STATUS_FOR.get(subscription.status)
    # PROVISIONING is skipped deliberately: the workspace is not finished
    # being built, and a subscription that says TRIAL must not open it before
    # the bootstrap has run. provisioning_complete() is what leaves that state.
    if derived is not None and org.status != Organization.Status.PROVISIONING:
        # Through the lifecycle, so an illegal derived move surfaces as a bug
        # rather than being written. No audit entry here -- the
        # SubscriptionEvent this same transaction writes IS the record.
        lifecycle.apply_derived(org, derived)

    if save:
        org.save(update_fields=["subscription_status", "subscription_start",
                                "subscription_expiry", "status", "updated_at"])
        # A suspension must take effect promptly, and the resolver caches the
        # record the request path gates on.
        resolver.forget(org)
    return org


def mirror_drift(organization):
    """``None`` if the mirror agrees with the subscription, else a description.

    Denormalisation without a reconciler is just a bug with a delay. The
    nightly job calls this for every organization; the test suite calls it
    after every transition.
    """
    sub = getattr(organization, "subscription", None)
    if sub is None:
        return None
    mismatches = []
    if organization.subscription_status != sub.status:
        mismatches.append(f"status {organization.subscription_status!r} != {sub.status!r}")
    if organization.subscription_start != sub.current_period_start:
        mismatches.append("start differs")
    if organization.subscription_expiry != sub.current_period_end:
        mismatches.append("expiry differs")
    return "; ".join(mismatches) or None


# ---------------------------------------------------------------------------
# The one entry point
# ---------------------------------------------------------------------------
@transaction.atomic
def transition(subscription, to_status, *, event=None, actor=None, payment=None,
               note="", period_start=None, period_end=None, plan=None,
               plan_price=None, force=False):
    """Move a subscription, record why, and refresh the mirror. Atomically.

    ``force`` bypasses the legality check and is for data repair only; it still
    writes the event, so a forced change is visible rather than silent.
    """
    # Lock the row: two reviewers verifying two payments for the same tenant at
    # the same moment must serialise, or the second overwrites the first's
    # period without anyone noticing.
    subscription = (Subscription.objects
                    .select_for_update()
                    .select_related("organization", "plan")
                    .get(pk=subscription.pk))

    from_status = subscription.status
    from_plan = subscription.plan

    if to_status != from_status:
        allowed = ALLOWED_TRANSITIONS.get(from_status, set())
        if to_status not in allowed and not force:
            raise IllegalTransition(
                f"Cannot move subscription {subscription.pk} from "
                f"'{from_status}' to '{to_status}'. Allowed from "
                f"'{from_status}': {sorted(allowed) or 'nothing'}.")

    if plan is not None:
        subscription.plan = plan
    if plan_price is not None:
        subscription.plan_price = plan_price
    if period_start is not None:
        subscription.current_period_start = period_start
    if period_end is not None:
        subscription.current_period_end = period_end
        subscription.renewal_date = period_end
        subscription.grace_until = add_days(
            period_end, subscription.plan.grace_days)

    if to_status == S.CANCELLED and subscription.cancelled_at is None:
        subscription.cancelled_at = timezone.now()
        if note:
            subscription.cancel_reason = note

    subscription.status = to_status
    # The guard in Subscription.save() looks for exactly this flag. Setting it
    # here, inside the only sanctioned mover, is what makes the guard mean
    # "went through transition()" rather than "was allowed by luck".
    subscription._status_change_authorised = True
    subscription.save()

    resolved_event = event or _infer_event(from_status, to_status)
    SubscriptionEvent.objects.create(
        organization=subscription.organization,
        subscription=subscription,
        event=resolved_event,
        from_status=from_status,
        to_status=to_status,
        from_plan=from_plan if from_plan != subscription.plan else None,
        to_plan=subscription.plan if from_plan != subscription.plan else None,
        period_start=subscription.current_period_start,
        period_end=subscription.current_period_end,
        actor=actor,
        payment=payment,
        note=note,
    )

    sync_subscription_mirror(subscription)
    logger.info("subscription %s: %s -> %s (%s)", subscription.pk,
                from_status, to_status, resolved_event)
    return subscription


def _infer_event(from_status, to_status):
    if to_status == S.GRACE:
        return E.ENTERED_GRACE
    if to_status == S.SUSPENDED:
        return E.SUSPENDED
    if to_status == S.CANCELLED:
        return E.CANCELLED
    if to_status == S.EXPIRED:
        return E.EXPIRED
    if to_status == S.TRIAL:
        return E.TRIAL_STARTED
    if to_status == S.ACTIVE:
        if from_status in (S.SUSPENDED, S.CANCELLED, S.EXPIRED):
            return E.REACTIVATED
        if from_status == S.ACTIVE:
            return E.RENEWED
        return E.ACTIVATED
    return E.CREATED


# ---------------------------------------------------------------------------
# Period extension
# ---------------------------------------------------------------------------
def extend_period(subscription, plan, *, today=None):
    """``(start, end)`` for a paid term of ``plan``, added to what exists.

    If the tenant still has paid time left (ACTIVE or GRACE with an end date in
    the future), the new term is appended to it -- paying early must never cost
    a customer the days they already hold. Otherwise the term starts today.
    """
    today = today or timezone.localdate()
    end = subscription.current_period_end
    if (subscription.status in (S.ACTIVE, S.GRACE) and end is not None
            and end >= today):
        start = end
    else:
        start = today
    return start, add_months(start, plan.interval_months)


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------
def provision_organization(**kwargs):
    """Create a tenant that is USABLE when this function returns.

    A THIN, NON-TRANSACTIONAL WRAPPER around the atomic build below, and the
    thinness is the point: it exists only to count the outcome (Phase S6.75
    Part 4). A counter incremented inside the atomic block would be rolled
    back by the very failure it was recording, so provisioning failures would
    count as nothing having happened -- which is precisely the series an
    operator needs when a deployment is quietly broken.

    Every caller reaches provisioning through here -- the console, the
    management command, self-service registration -- so this is the one place
    the figures can be complete.
    """
    from . import metrics
    from .models import PlatformMetric

    try:
        organization = _provision_organization(**kwargs)
    except Exception:
        # No organization to attribute it to: the row that would have carried
        # the tenant is the one that did not get created.
        metrics.bump(PlatformMetric.Key.PROVISION_FAILED)
        raise
    metrics.bump(PlatformMetric.Key.PROVISION_OK, organization=organization)
    return organization


@transaction.atomic
def _provision_organization(*, name, slug, document_prefix, email,
                            plan=None, status=None, actor=None,
                            trial_days=None, today=None, bootstrap=True,
                            admin_email=None, admin_name="",
                            admin_password=None,
                            request=None, **org_fields):
    """The build itself. ALL OF IT OR NONE OF IT -- see the wrapper above.

    Phase S6 Part 3's critical goal, in order:

      1. Organization, in ``PROVISIONING`` -- nobody may be let in yet.
      2. Settings and Branding, so no policy lookup is a null check.
      3. Subscription on a trial, so the request path has something to gate on.
      4. Bootstrap configuration (``tenancy.bootstrap``) -- departments, leave
         types, shifts, an attendance policy, minute types, task templates,
         competencies and the entitlement matrix.
      5. Optionally the first administrator, who is the only person able to
         create anybody else.
      6. ``PROVISIONING -> TRIAL``, which is the moment the workspace opens.

    ALL OF IT OR NONE OF IT. One ``atomic`` block around the lot, because the
    states in between are each a tenant that half exists: an Organization with
    no Subscription cannot be gated, and -- as Phase S5 found the hard way --
    one with no LeaveType cannot approve a day's leave. A half-provisioned
    tenant is the thing this phase exists to make impossible, so it must not be
    reachable even by a crash.

    STEP 6 IS WHAT MAKES THE ORDER MATTER. Until it runs, ``is_admitted`` is
    False for any status, so a request arriving mid-provision is refused rather
    than served an empty workspace.

    :param bootstrap: seed the configuration. Default True; Part 3 is
        explicit that provisioning takes no manual steps. False exists for the
        tests that assert what a bare tenant looks like, and for an import
        that will supply its own configuration.
    :param admin_email: create the tenant's first admin account.
    :returns: the Organization.
    """
    from . import bootstrap as bootstrap_module
    from . import platform_audit
    from .models import PlatformAuditLog

    today = today or timezone.localdate()

    org = Organization(
        name=name, slug=slug, document_prefix=document_prefix, email=email,
        created_by=actor, **org_fields)
    org.full_clean(exclude=["created_by"])
    org.save()

    OrganizationSettings.objects.create(organization=org)
    OrganizationBranding.objects.create(organization=org)

    if plan is None:
        plan = plans.purchasable_plans().first()
        if plan is None:
            raise IllegalTransition(
                "No purchasable plan exists; seed plans before provisioning.")

    trial_days = plan.trial_days if trial_days is None else trial_days
    trial_end = add_days(today, trial_days)

    subscription = Subscription.objects.create(
        organization=org, plan=plan, status=S.TRIAL,
        trial_start=today, trial_end=trial_end,
        current_period_start=today, current_period_end=trial_end,
        grace_until=add_days(trial_end, plan.grace_days),
        renewal_date=trial_end,
    )
    SubscriptionEvent.objects.create(
        organization=org, subscription=subscription, event=E.CREATED,
        to_status=S.TRIAL, to_plan=plan, period_start=today,
        period_end=trial_end, actor=actor,
        note=f"Organization provisioned on the {plan.code} plan.")

    platform_audit.record(
        actor, PlatformAuditLog.Action.TENANT_CREATED, organization=org,
        changes={"slug": slug, "name": name, "plan": plan.code,
                 "document_prefix": document_prefix,
                 "trial_days": trial_days},
        request=request)

    bootstrap_counts = {}
    if bootstrap:
        bootstrap_counts = bootstrap_module.bootstrap_organization(
            org, actor=actor, request=request)

    admin_user = None
    if admin_email:
        admin_user = create_tenant_admin(
            org, email=admin_email, full_name=admin_name,
            password=admin_password, actor=actor, request=request)

    # The workspace opens HERE, and not before.
    target = status or Organization.Status.TRIAL
    if target == Organization.Status.PROVISIONING:
        # An explicit request to leave it closed. Honoured, because an import
        # that will load data before anyone is admitted is a real workflow --
        # but it means the caller owns finishing it.
        logger.warning(
            "organization %s left in PROVISIONING at the caller's request; "
            "nobody can use it until lifecycle.transition_organization moves "
            "it on", org.slug)
    else:
        lifecycle.transition_organization(
            org, target, actor=actor, request=request,
            reason="Provisioning complete.")

    sync_subscription_mirror(subscription)
    org.provisioning_report = {
        "bootstrap": bootstrap_counts,
        "admin_user": getattr(admin_user, "email", None),
        "gaps": bootstrap_module.verify_organization(org) if bootstrap else {},
    }
    # In-process only, never persisted. The console reads it once to show the
    # operator the temporary password; after this response it is unrecoverable
    # and the account has to be reset instead, which is the right default.
    org._admin_initial_password = getattr(admin_user, "initial_password", None)
    return org


@transaction.atomic
def create_tenant_admin(organization, *, email, full_name="", password=None,
                        actor=None, request=None):
    """The tenant's first administrator. Returns the User.

    A workspace with configuration and no people cannot be used, and nobody
    inside the tenant can create the first account -- there is nobody to do
    it. So the platform does, once, and then stops: every subsequent account
    is created by this user through the tenant's own User Management. That
    boundary is deliberate. The platform operator should not be routinely
    creating a customer's employees.

    ``must_change_password`` is set, so the temporary password cannot become a
    permanent one. The generated password is returned on the instance as
    ``initial_password`` and is NEVER stored or logged -- the console shows it
    to the operator once, which is the only moment it can be handed over.
    """
    import secrets

    from django.contrib.auth import get_user_model

    from . import platform_audit
    from .context import tenant_context
    from .models import PlatformAuditLog

    User = get_user_model()
    generated = password or secrets.token_urlsafe(12)
    local = (email or "").split("@")[0][:120] or "admin"
    first, _, last = (full_name or "").partition(" ")

    with tenant_context(organization):
        from users.services import generate_employee_id

        user = User(
            username=local, email=email, first_name=first or "Account",
            last_name=last or "Administrator", role=User.Roles.ADMIN,
            employee_type=User.EmployeeType.SYSTEM_ADMIN,
            employment_type=User.EmploymentType.PERMANENT,
            employee_id=generate_employee_id(organization),
            date_of_joining=timezone.localdate(),
            organization=organization, is_platform_staff=False,
            must_change_password=True, is_staff=False, is_superuser=False,
        )
        user.set_password(generated)
        user.save()

    platform_audit.record(
        actor, PlatformAuditLog.Action.ADMIN_USER_CREATED,
        organization=organization,
        changes={"email": email, "role": User.Roles.ADMIN},
        note="First administrator created with the tenant.", request=request)

    # Attribute, not a column. It exists for the length of this response.
    user.initial_password = generated
    return user


# ---------------------------------------------------------------------------
# Scheduled lifecycle advance (called by the existing cron container)
# ---------------------------------------------------------------------------
def advance_expired(*, today=None, actor=None):
    """Move every subscription whose date has passed to its next status.

    Two indexed sweeps, in this order:

      1. ACTIVE/TRIAL past ``current_period_end``  -> GRACE
      2. GRACE past ``grace_until``                -> SUSPENDED

    Returns ``{"entered_grace": n, "suspended": n}``. Idempotent: running it
    twice in one day is a no-op the second time.
    """
    today = today or timezone.localdate()
    counts = {"entered_grace": 0, "suspended": 0}

    due = (Subscription.objects
           .filter(status__in=[S.ACTIVE, S.TRIAL],
                   current_period_end__lt=today)
           .select_related("organization", "plan"))
    for sub in due:
        transition(sub, S.GRACE, actor=actor,
                   note=f"Period ended {sub.current_period_end}.")
        counts["entered_grace"] += 1

    lapsed = (Subscription.objects
              .filter(status=S.GRACE, grace_until__lt=today)
              .select_related("organization", "plan"))
    for sub in lapsed:
        transition(sub, S.SUSPENDED, actor=actor,
                   note=f"Grace period ended {sub.grace_until}.")
        counts["suspended"] += 1

    return counts


def reconcile_mirrors():
    """Report every organization whose mirror disagrees with its subscription.

    READ-ONLY, DESPITE THE NAME. It is what the platform health check and the
    nightly job call, and the health check is a GET -- a detector that quietly
    writes is a detector nobody can run twice to confirm a finding.
    ``repair_mirrors`` is the half that writes.
    """
    drift = {}
    for org in Organization.objects.select_related("subscription").all():
        problem = mirror_drift(org)
        if problem:
            drift[org.slug] = problem
            logger.error("subscription mirror drift for %s: %s", org.slug, problem)
    return drift


def repair_mirrors():
    """Correct every drifted mirror, and report what was corrected.

    WHY THIS EXISTS. Drift was detected, logged at ERROR and surfaced on the
    console -- and then nothing anywhere corrected it. ``reconcile_mirrors``
    reports; the nightly job reports; the console endpoint named "reconcile"
    reported. So the one actionable signal the platform raises about its own
    denormalisation had no remedy short of a shell, and the mirror is not
    cosmetic: ``Organization.subscription_status`` and ``subscription_expiry``
    are what every request reads to decide whether a tenant is admitted. A
    stale mirror locks a paying customer out, or admits a lapsed one, and
    `editable=False` means not even the admin could fix it by hand.

    ``sync_subscription_mirror`` is the only writer of those columns and
    already exists, so repairing is calling it for the rows that disagree --
    through the lifecycle, so an illegal derived status still surfaces as a
    bug rather than being written.

    :returns: ``{slug: what was corrected}``, empty when nothing had drifted.
    """
    repaired = {}
    for org in Organization.objects.select_related("subscription").all():
        problem = mirror_drift(org)
        if not problem:
            continue
        sync_subscription_mirror(org.subscription)
        repaired[org.slug] = problem
        logger.warning("repaired subscription mirror for %s: %s",
                       org.slug, problem)
    return repaired
