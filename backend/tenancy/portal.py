"""Phase S8: the customer's own view of what they are paying for.

THE FIRST TENANT-FACING SURFACE IN THE TENANCY APP, and that is worth saying
out loud. Everything built in S6, S6.5, S6.75 and S7 is either platform
console (an operator looking at a customer) or public (a stranger signing
up). This module is the third audience: a customer's own administrator,
looking at their own subscription.

So the authority rule here is different from everywhere else in this package,
and narrower than the obvious one:

  * ``IsPlatformStaff``  -- wrong. This is not the console.
  * ``IsTenantUser``     -- too wide. Every employee would see what their
                            employer pays and could submit payment proof in
                            their name.
  * tenant ADMIN only    -- right, and it matches how the rest of the product
                            already gates configuration (``users.roles``
                            allow-lists "admin" for everything of this kind).

WHAT A TENANT MAY AND MAY NOT DO
--------------------------------
May: read their subscription, read the plans and prices, read the payment
instructions, open a payment for a plan (an upgrade or a renewal REQUEST),
attach proof of having paid, and read their own payment history.

May not: activate anything. Part 2 of the brief is explicit -- "no plan
activation occurs automatically" -- and that is not timidity about money, it
is the only honest arrangement when there is no gateway. The customer says
"I have paid", a human at the platform looks at the receipt, and the
subscription moves on verification. Everything a tenant does here stops at
``SUBMITTED``.

NO NEW SUBSCRIPTION LOGIC LIVES HERE (Part 6). Opening, submitting,
verifying and extending are all ``tenancy.payments`` and
``tenancy.services.transition``, which were built in an earlier phase and
already write ``SubscriptionEvent`` rows and audit entries. This module is a
door onto them, not a second implementation -- a parallel path that extended
a period by its own arithmetic is exactly how two sources of truth about
money appear.
"""
import logging

from django.utils import timezone

from . import payments, plans
from .exceptions import TenancyError

logger = logging.getLogger(__name__)


class NotTenantAdmin(TenancyError):
    """The caller is not an administrator of the organization."""


def require_tenant_admin(user):
    """Authenticated, belongs to an organization, and administers it.

    THREE CONDITIONS, NOT ONE. A platform operator is refused here as
    firmly as an employee is: they have no organization, so "their
    subscription" is not a question with an answer, and the console is where
    they work. Checking only `role == admin` would let an operator through
    on a role field that platform accounts do not even use.

    IT RE-READS THE ORGANIZATION FROM THE DATABASE, and that is a bug fix
    rather than caution. Returning `user.organization` hands back whatever
    Django cached on that user instance -- including its `subscription`
    relation. A customer who paid, had the payment verified, and then
    reloaded this page was shown their OLD plan and OLD status: the figures
    were stale exactly at the moment they most needed to be right, and the
    staleness was invisible because every other field looked correct.

    One query, on a page nobody loads in a loop, in exchange for a billing
    page that is never wrong.
    """
    from users import roles

    from .models import Organization

    organization_id = getattr(user, "organization_id", None)
    if (user is None or not getattr(user, "is_authenticated", False)
            or getattr(user, "is_platform_staff", False)
            or organization_id is None):
        raise NotTenantAdmin(
            "This page is for an organization's own administrator.")
    if not roles.is_admin(user):
        raise NotTenantAdmin(
            "Only an administrator of this organization may manage its "
            "subscription.")

    organization = (Organization.objects
                    .select_related("subscription", "subscription__plan",
                                    "subscription__plan_price")
                    .filter(pk=organization_id)
                    .first())
    if organization is None:                       # pragma: no cover
        raise NotTenantAdmin("Your organization could not be found.")
    return organization


# ---------------------------------------------------------------------------
# Part 1: what the customer is on
# ---------------------------------------------------------------------------
def subscription_state(user):
    """Everything Part 1 asks for, about the caller's own organization.

    THE DATES ARE REPORTED, NOT RECOMPUTED. `Subscription` already holds the
    period, the trial window and the grace deadline, and
    `days_until_expiry()` is the same method the platform console reads --
    so a customer and an operator looking at the same subscription on the
    same day see the same number. A second calculation here would eventually
    disagree with the one that decides whether they are let in.
    """
    organization = require_tenant_admin(user)
    subscription = getattr(organization, "subscription", None)
    if subscription is None:                       # pragma: no cover
        # Provisioning creates one in the same transaction as the
        # organization, so this is unreachable -- and worth reporting rather
        # than crashing if it ever is reached.
        logger.error("organization %s has no subscription", organization.slug)
        return {"available": False,
                "detail": "Your subscription record is missing. Please "
                          "contact support."}

    plan = subscription.plan
    price = getattr(subscription, "plan_price", None)
    today = timezone.localdate()
    days = subscription.days_until_expiry(today)

    return {
        "available": True,
        "organization": {
            "name": organization.name,
            "slug": organization.slug,
            "seats_used": organization.seat_count,
        },
        "plan": {
            "code": getattr(plan, "code", None),
            "name": getattr(plan, "name", None),
            "interval_months": getattr(plan, "interval_months", None),
            "included_seats": getattr(plan, "included_seats", None),
            "amount_minor": getattr(price, "amount_minor", None),
            "currency": getattr(price, "currency", "NPR"),
        },
        "status": subscription.status,
        "status_display": subscription.get_status_display(),
        # The workspace status as well as the billing status: they differ
        # exactly when an operator has intervened, and a customer who is
        # locked out while their subscription says ACTIVE needs to see both
        # or the page is lying to them.
        "workspace_status": organization.status,
        "is_admitted": organization.is_admitted,
        "starts_on": subscription.current_period_start,
        "expires_on": subscription.current_period_end,
        "days_remaining": days,
        "trial": {
            "is_trial": subscription.status == subscription.Status.TRIAL,
            "starts_on": subscription.trial_start,
            "ends_on": subscription.trial_end,
        },
        "grace": {
            "in_grace": subscription.status == subscription.Status.GRACE,
            "until": subscription.grace_until,
        },
        "auto_renew": subscription.auto_renew,
        # Part 7: one sentence a person can act on, chosen server-side so the
        # customer and the support runbook agree about what each state means.
        "explanation": _explain(organization, subscription, days),
        "renewal_due": bool(days is not None and days <= 30),
    }


def _explain(organization, subscription, days):
    """Part 7: the state, in a sentence written for the customer.

    SERVER-SIDE ON PURPOSE. The same six states are described in the support
    runbook and shown on this page, and a front end that invents its own
    wording is how a customer is told something different from what support
    reads.
    """
    S = subscription.Status
    status = subscription.status

    if not organization.is_admitted:
        if organization.status == organization.Status.ARCHIVED:
            return ("Your workspace is closed and its data is preserved. "
                    "Contact support to reopen it.")
        return ("Your workspace is currently closed. Contact support -- your "
                "data is intact.")
    if status == S.TRIAL:
        if days is None:
            return "You are on a trial."
        if days <= 0:
            return ("Your trial ends today. Choose a plan and send payment "
                    "to continue without interruption.")
        return (f"You are on a trial with {days} day"
                f"{'s' if days != 1 else ''} remaining. No payment details "
                f"were needed to start.")
    if status == S.GRACE:
        return ("Your subscription has lapsed and you are inside the grace "
                "period -- everything still works. Send payment to restore "
                "it before the grace period ends.")
    if status == S.ACTIVE:
        if days is not None and days <= 7:
            return (f"Your subscription renews in {days} day"
                    f"{'s' if days != 1 else ''}. Send payment to avoid an "
                    f"interruption.")
        return "Your subscription is active."
    if status == S.EXPIRED:
        return ("Your subscription has expired. Send payment to restore "
                "access -- nothing has been deleted.")
    if status == S.SUSPENDED:
        return ("Your subscription is suspended. Contact support; your data "
                "is intact.")
    if status == S.CANCELLED:
        return ("Your subscription is cancelled. Contact support to start "
                "again -- your data has been kept.")
    return "Contact support for the current state of your subscription."


# ---------------------------------------------------------------------------
# Part 2: the plans, and Part 3: how to pay
# ---------------------------------------------------------------------------
def available_plans(user):
    """What the customer could move to, priced from the database.

    PRICES ARE NEVER WRITTEN IN THE FRONT END. The brief lists four figures
    (NPR 999 / 2,599 / 4,999 / 9,990) and they are deliberately not in any
    template or component: `PlanPrice` rows are immutable and superseded by
    insertion, so "the price" is a question about a date, and a number typed
    into a page is a number that will be wrong after the first price change
    -- shown to the one audience that must never see a stale price.
    """
    organization = require_tenant_admin(user)
    current = getattr(getattr(organization, "subscription", None), "plan", None)
    current_months = getattr(current, "interval_months", 0) or 0

    out = []
    for plan in plans.purchasable_plans():
        try:
            price = plans.current_price(plan)
        except Exception:                          # noqa: BLE001 - NoActivePrice
            # A plan with no effective price cannot be sold, so it is not
            # offered. Silently, because this is a customer-facing list and
            # "misconfigured plan" is not their problem to read about.
            continue
        months = plan.interval_months or 1
        out.append({
            "code": plan.code,
            "name": plan.name,
            "description": plan.description,
            "interval_months": plan.interval_months,
            "included_seats": plan.included_seats,
            "trial_days": plan.trial_days,
            "amount_minor": price.amount_minor,
            "currency": price.currency,
            # Comparable at a glance (Part 2: "compare plans"), computed
            # here so every caller compares the same way.
            "monthly_equivalent_minor": round(price.amount_minor / months),
            "is_current": bool(current and plan.pk == current.pk),
            "is_upgrade": months > current_months,
        })
    return out


def payment_instructions(user):
    """Part 3: how to pay, from the console-managed rows. Never hardcoded.

    Returns every active method. The QR image is served through the signed
    media path like any other file, so the page can show it without the
    bucket being public.
    """
    require_tenant_admin(user)
    from .models import PaymentInstruction

    out = []
    for row in (PaymentInstruction.objects
                .filter(is_active=True)
                .order_by("sort_order", "label")):
        out.append({
            "method": row.method,
            "label": row.label,
            "account_name": row.account_name,
            "account_number": row.account_number,
            "bank_name": row.bank_name,
            "branch": row.branch,
            "esewa_id": row.esewa_id,
            "khalti_id": row.khalti_id,
            "qr_image": _signed(row.qr_image),
            "instructions_html": row.instructions_html,
        })
    return out


def _signed(image_field):
    """A signed URL for a platform-owned image, or None.

    `documents.protected_media` refuses the `platform/` prefix outright
    (Phase S6.5 hardening), which is right for export bundles and payment
    proofs -- and payment INSTRUCTIONS are the one thing under that prefix
    that every customer is meant to see. Served from MEDIA_URL directly
    rather than signed, because there is nothing confidential in a bank
    account the platform publishes to all its customers.
    """
    if not image_field:
        return None
    try:
        return image_field.url
    except Exception:                              # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Phase S9 Parts 1 and 2: the tenant's own branding
# ---------------------------------------------------------------------------
# Readable by EVERY member of the organization, writable only by an
# administrator. The asymmetry is the point: the whole application has to
# theme itself for the person using it -- which is R28, "tenant branding
# visible everywhere, not login page only" -- and that means every employee's
# browser needs the colours and the logo. Only an administrator chooses them.
BRANDING_FIELDS = (
    "display_name", "color_primary", "color_secondary", "color_accent",
    "login_tagline", "dashboard_welcome", "report_footer_text",
)

BRANDING_ASSETS = ("logo_primary", "logo_login", "logo_email",
                   "logo_letterhead")


def branding_for_member(user):
    """What the application should look like for this user. Any member.

    Returns the platform's own defaults when nothing is customised, so a
    caller never has to special-case "no branding" -- and blank means
    inherit, exactly as the model documents.
    """
    organization = getattr(user, "organization", None)
    if organization is None:
        return {"applicable": False}
    # READ FROM THE DATABASE, not from whatever the authentication layer
    # happened to attach. `organization.branding` is a reverse one-to-one
    # that Django caches on the instance, and provisioning creates the row
    # through that instance -- so an organization object carried over from
    # before a branding change answers with the branding as it was then.
    # One indexed lookup per page load, and the front end caches the result
    # for five minutes anyway.
    from .models import OrganizationBranding

    branding = (OrganizationBranding.objects
                .filter(organization=organization)
                .first())
    return branding_payload(organization, branding)


def branding_payload(organization, branding):
    """The same answer, from a row the caller already has in hand.

    SPLIT OUT BECAUSE OF A BUG A TEST FOUND. `update_branding` saved the row
    and then called `branding_for_member(user)` to report the result -- which
    reads `user.organization.branding`, a reverse one-to-one Django caches on
    the organization object the request loaded BEFORE the save. So the
    response to a successful save carried the OLD branding. On the settings
    page that renders as the form snapping back to the previous colour the
    instant you press Save, which is indistinguishable from the save having
    silently failed; the value was in the database the whole time.

    It survived a first reading because the fixture's display name happened
    to equal the organization's legal name, so the one field that was checked
    looked right for the wrong reason.
    """
    return {
        "applicable": True,
        "organization": {"name": organization.name,
                         "slug": organization.slug},
        "display_name": (getattr(branding, "display_name", "")
                         or organization.name),
        "color_primary": getattr(branding, "color_primary", "") or "",
        "color_secondary": getattr(branding, "color_secondary", "") or "",
        "color_accent": getattr(branding, "color_accent", "") or "",
        "login_tagline": getattr(branding, "login_tagline", "") or "",
        "dashboard_welcome": getattr(branding, "dashboard_welcome", "") or "",
        "report_footer_text": getattr(branding, "report_footer_text", "") or "",
        "logo_primary": _branding_asset_url(branding, "logo_primary"),
        "logo_login": _branding_asset_url(branding, "logo_login"),
        "logo_email": _branding_asset_url(branding, "logo_email"),
        "logo_letterhead": _branding_asset_url(branding, "logo_letterhead"),
        "favicon": _branding_asset_url(organization, "favicon"),
    }


def _branding_asset_url(holder, field):
    """A LONG-LIVED signed URL for a branding image. Closes R27.

    The default media signature lasts 300 seconds, which is right for a
    document attachment and wrong for a logo: it is fetched on every page of
    a session, a dashboard left open over lunch re-requests it, and the
    answer after five minutes is a 403 where the brand should be. R27 was
    raised for exactly that.

    Branding images are not confidential -- they are what the customer shows
    the world -- so the only thing the signature is doing here is keeping the
    media view from serving arbitrary paths. A day is long enough that no
    realistic session outlives it and short enough that a replaced logo stops
    being served the same day.
    """
    from django.conf import settings

    from documents.protected_media import signed_media_url

    image = getattr(holder, field, None) if holder is not None else None
    if not image:
        return None
    ttl = int(getattr(settings, "BRANDING_URL_TTL", 86400))
    return signed_media_url(image.name, ttl=ttl,
                            organization=getattr(holder, "organization",
                                                 holder))


def update_branding(user, *, request=None, **fields):
    """Set the tenant's own branding. Administrator only, and audited.

    THE COLOURS ARE VALIDATED HERE, not trusted. They are interpolated into
    a stylesheet as CSS custom properties, so a value that is not a hex
    colour is a value that ends up inside a `style` attribute -- which is the
    short road from "customise your brand" to script injection on every page
    of the application.
    """
    organization = require_tenant_admin(user)
    from .models import OrganizationBranding, PlatformAuditLog

    branding, _ = OrganizationBranding.objects.get_or_create(
        organization=organization)

    changed = {}
    for field in BRANDING_FIELDS:
        if field not in fields:
            continue
        value = fields[field]
        if field.startswith("color_"):
            value = _validate_colour(field, value)
        setattr(branding, field, value or "")
        changed[field] = value or ""

    if changed:
        branding.save(update_fields=list(changed) + ["updated_at"])
        from . import platform_audit

        platform_audit.record(
            None, PlatformAuditLog.Action.BRANDING_UPDATED,
            organization=organization,
            changes={**changed, "by": getattr(user, "email", "")},
            note="The customer changed their own branding.",
            request=request)
    return branding_payload(organization, branding)


def _validate_colour(field, value):
    import re

    text = (value or "").strip()
    if not text:
        return ""
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", text):
        raise TenancyError(
            f"{field.replace('_', ' ')} must be a six-digit hex colour, "
            f"like #1D4ED8.")
    return text.upper()


def set_branding_asset(user, field, uploaded, *, request=None):
    """Attach one branding image. Administrator only.

    The field name is checked against an allow-list rather than passed
    through: `setattr(branding, field, uploaded)` with a client-supplied
    name is a write to any column on the row.
    """
    organization = require_tenant_admin(user)
    from .models import OrganizationBranding, PlatformAuditLog

    if field not in BRANDING_ASSETS and field != "favicon":
        raise TenancyError(
            f"'{field}' is not a branding image. Allowed: "
            f"{sorted(BRANDING_ASSETS) + ['favicon']}.")

    branding = getattr(organization, "branding", None)
    if field == "favicon":
        # Lives on `Organization`, not on the branding row -- modelled in
        # Phase S1 as identity, before branding existed as its own record.
        organization.favicon = uploaded
        organization.save(update_fields=["favicon", "updated_at"])
    else:
        branding, _ = OrganizationBranding.objects.get_or_create(
            organization=organization)
        setattr(branding, field, uploaded)
        branding.save(update_fields=[field, "updated_at"])

    from . import platform_audit

    platform_audit.record(
        None, PlatformAuditLog.Action.BRANDING_UPDATED,
        organization=organization,
        changes={field: bool(uploaded), "by": getattr(user, "email", "")},
        note=f"The customer replaced their {field.replace('_', ' ')}.",
        request=request)

    # The PDF letterhead logo is cached per tenant for the life of the
    # process and nothing used to evict it, so a replaced logo kept printing
    # the old mark until a restart.
    from documents.pdf import forget_logo

    forget_logo(organization)
    return branding_payload(organization, branding)


# ---------------------------------------------------------------------------
# Parts 2 and 4: request a plan, then say you have paid
# ---------------------------------------------------------------------------
def request_plan(user, plan_code, *, request=None):
    """Open a payment for a plan. ACTIVATES NOTHING.

    This is Part 2's "request upgrade" and "request renewal" -- one
    operation, because they are the same act from the platform's side: a
    customer has chosen a plan and owes money for it. The difference is only
    which plan, and `verify_payment` already distinguishes a renewal from an
    activation by looking at the subscription it lands on.

    The amount is resolved SERVER-SIDE by `payments.create_payment` from the
    effective `PlanPrice` and written onto the row, where nothing downstream
    may change it. A client that posts an amount is ignored, because this
    endpoint does not accept one.
    """
    organization = require_tenant_admin(user)
    from .models import Payment, PlatformAuditLog

    plan = _plan_or_refuse(plan_code)

    # ONE OPEN REQUEST AT A TIME. A customer who clicks twice, or changes
    # their mind between plans, should not leave a trail of unpaid payment
    # references for an operator to reconcile by hand -- and two open
    # payments for the same tenant is the state in which somebody pays the
    # wrong reference.
    existing = (Payment.objects
                .filter(organization=organization,
                        status__in=[Payment.Status.AWAITING_PROOF])
                .order_by("-created_at")
                .first())
    if existing is not None:
        if existing.plan_id == plan.pk:
            return existing
        # Supersede it: the customer has chosen differently before paying.
        existing.status = Payment.Status.CANCELLED
        existing.save(update_fields=["status"])
        logger.info("payment %s superseded by a new plan request for %s",
                    existing.payment_reference, organization.slug)

    payment = payments.create_payment(organization, plan, actor=user)

    from . import platform_audit

    platform_audit.record(
        None, PlatformAuditLog.Action.PAYMENT_REQUESTED,
        organization=organization,
        changes={"plan": plan.code, "reference": payment.payment_reference,
                 "amount_minor": payment.amount_minor,
                 "currency": payment.currency,
                 "requested_by": getattr(user, "email", "")},
        note=(f"The customer requested the {plan.code} plan and owes "
              f"{payment.currency} {payment.amount_minor / 100:.2f}. "
              f"Nothing is active until a payment is verified."),
        request=request)
    return payment


def _plan_or_refuse(plan_code):
    """The plan, if it is one a customer may actually buy.

    Looked up through `purchasable_plans()` rather than by code alone, so a
    retired or non-public plan cannot be requested by anybody who knows its
    code -- including an old price somebody was once quoted.
    """
    plan = plans.purchasable_plans().filter(code=(plan_code or "")).first()
    if plan is None:
        raise TenancyError("That plan is not available.")
    try:
        plans.current_price(plan)
    except Exception as exc:                       # noqa: BLE001
        raise TenancyError("That plan is not available.") from exc
    return plan


def submit_payment_proof(user, payment_reference, *, method, transaction_id="",
                         paid_at=None, proof=None, payer_note="",
                         request=None):
    """Attach the receipt. Puts the payment in the platform's review queue.

    THE CUSTOMER'S SIDE ENDS HERE. The next move is a human at the platform
    looking at the proof; nothing in this call touches the subscription.
    """
    organization = require_tenant_admin(user)
    from .models import Payment, PlatformAuditLog

    payment = _own_payment(organization, payment_reference)
    payment = payments.submit_proof(
        payment, method=method, transaction_id=transaction_id,
        paid_at=paid_at, proof=proof, payer_note=payer_note, actor=user)

    from . import platform_audit

    platform_audit.record(
        None, PlatformAuditLog.Action.PAYMENT_SUBMITTED,
        organization=organization,
        changes={"reference": payment.payment_reference,
                 "method": payment.method,
                 "transaction_id": payment.transaction_id,
                 "paid_at": str(payment.paid_at) if payment.paid_at else None,
                 "has_proof": bool(payment.proof),
                 "submitted_by": getattr(user, "email", "")},
        note=(f"The customer submitted proof for "
              f"{payment.payment_reference} and it is awaiting review."),
        request=request)
    return payment


def _own_payment(organization, payment_reference):
    """Their payment, by reference, or a refusal that reveals nothing.

    Filtered by organization, so a reference belonging to another customer
    answers exactly as a reference that does not exist -- a payment
    reference is sequential per tenant and would otherwise be a way to
    confirm that another organization has one.
    """
    from .models import Payment

    payment = (Payment.objects
               .filter(organization=organization,
                       payment_reference=(payment_reference or "").strip())
               .first())
    if payment is None:
        raise TenancyError("No such payment for this organization.")
    return payment


def payment_history(user):
    """Their own payments, newest first. Part 7's status trail."""
    organization = require_tenant_admin(user)
    from .models import Payment

    rows = (Payment.objects
            .filter(organization=organization)
            .select_related("plan")
            .order_by("-created_at"))
    return [{
        "reference": row.payment_reference,
        "plan": getattr(row.plan, "code", None),
        "amount_minor": row.amount_minor,
        "currency": row.currency,
        "status": row.status,
        "status_display": row.get_status_display(),
        "method": row.method,
        "transaction_id": row.transaction_id,
        "paid_at": row.paid_at,
        "submitted_at": row.submitted_at,
        "verified_at": row.verified_at,
        # The reason is the whole point of a rejection: the customer cannot
        # fix what they are not told.
        "rejection_reason": row.rejection_reason,
        "review_message": row.review_message,
        "explanation": _explain_payment(row),
        "next_step": _next_step(row),
        "can_submit_proof": row.status in (row.Status.AWAITING_PROOF,
                                           row.Status.REJECTED,
                                           row.Status.NEEDS_INFO),
    } for row in rows]


def _explain_payment(payment):
    """Part 7, per payment, in the customer's own terms."""
    P = payment.Status
    if payment.status == P.AWAITING_PROOF:
        return ("Waiting for your payment. Send the amount using any method "
                "below, then upload the receipt.")
    if payment.status == P.SUBMITTED:
        return "We have your receipt and will review it shortly."
    if payment.status == P.UNDER_REVIEW:
        return "Somebody at our end is checking this payment now."
    if payment.status == P.VERIFIED:
        return "Payment confirmed and applied to your subscription."
    if payment.status == P.REJECTED:
        return (payment.rejection_reason
                or "We could not confirm this payment. Please check the "
                   "details and submit again.")
    if payment.status == P.NEEDS_INFO:
        return (payment.review_message
                or "We need a little more information before we can confirm "
                   "this payment.")
    if payment.status == P.CANCELLED:
        return "This request was replaced by a newer one."
    return ""


def _next_step(payment):
    """The one thing the customer should do now, or None."""
    P = payment.Status
    if payment.status == P.AWAITING_PROOF:
        return "Pay using one of the methods below, then upload your receipt."
    if payment.status == P.REJECTED:
        return ("Check the reason above, then upload the receipt again with "
                "the correct details.")
    if payment.status == P.NEEDS_INFO:
        return "Upload the receipt again, including what we asked for."
    return None
