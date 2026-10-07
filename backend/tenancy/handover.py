"""Handing a new organization over to its customer.

THE PROBLEM THIS EXISTS FOR. Provisioning built the whole workspace in one
request, and then left the operator to tell the customer, by hand, four
things: where to go, which address to sign in with, the temporary password,
and that it must be changed. Each was retyped from a different part of the
screen. A mistyped hostname or a password read down a phone line is a
customer who cannot get in on day one, and a support call that should never
have happened.

So the console now produces ONE package per organization -- the login URL,
the administrator, the plan and the trial -- and the same facts feed the copy
buttons, the welcome message and the email. One source, so the email cannot
say one URL while the screen says another.

THE PASSWORD IS STILL NEVER STORED. That rule (``services.create_tenant_admin``)
does not bend for convenience, and it shapes the email action:

  * At creation, the console holds the password for the length of one dialog.
    "Email these details" sends it back to the server, which CHECKS it
    against the account's hash before mailing it. The server never had to
    remember it, and cannot be made to mail arbitrary text to a customer as
    if it were their password.

  * Later, nobody has the password -- so "email sign-in details" ISSUES A NEW
    temporary one and mails that. It is only allowed while the administrator
    has not yet chosen their own password, which is exactly the window in
    which replacing it costs nobody anything. Once they have signed in and
    set their own, there is nothing of ours to send, and the action says so
    rather than resetting a password somebody is using.

Both paths leave ``must_change_password`` set, so whatever travels by email
works once, until the customer replaces it.
"""
import logging
import secrets

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .context import no_tenant, tenant_context
from .exceptions import TenancyError

logger = logging.getLogger(__name__)


class AccessDeliveryFailed(Exception):
    """The mail server refused the message. Nothing was changed."""


# ---------------------------------------------------------------------------
# Where the customer signs in
# ---------------------------------------------------------------------------
def subdomain_url(organization):
    """``https://<slug>.<base domain>/``, which works from the moment of creation.

    With no base domain configured (a development or single-host deployment)
    every tenant shares the one frontend, so that is the address to give.
    """
    base = (getattr(settings, "TENANCY_BASE_DOMAIN", "") or "").strip()
    if not base:
        return getattr(settings, "FRONTEND_URL", "").rstrip("/") + "/"
    return f"https://{organization.slug}.{base}/"


def _custom_domain(organization):
    """The customer's own hostname, if they have claimed one. Serving first."""
    from .models import TenantDomain

    with no_tenant():
        domains = list(TenantDomain.objects
                       .filter(organization=organization)
                       .exclude(status=TenantDomain.Status.REMOVED)
                       .order_by("-is_primary", "created_at"))
    serving = [d for d in domains
               if d.status in TenantDomain.SERVING_STATUSES]
    chosen = (serving or domains or [None])[0]
    if chosen is None:
        return None
    is_serving = chosen.status in TenantDomain.SERVING_STATUSES
    return {
        "hostname": chosen.hostname,
        "url": f"https://{chosen.hostname}/",
        "status": chosen.status,
        "status_display": chosen.get_status_display(),
        "serving": is_serving,
    }


def login_url(organization, custom=None):
    """The ONE address to hand a customer.

    Their own domain only once it is actually serving. A claimed domain whose
    DNS has not been proven goes nowhere, and sending a new customer there
    is the exact first-day failure this module exists to prevent.
    """
    if custom is None:
        custom = _custom_domain(organization)
    if custom and custom["serving"]:
        return custom["url"]
    return subdomain_url(organization)


# ---------------------------------------------------------------------------
# Who the customer is
# ---------------------------------------------------------------------------
def administrator(organization):
    """The organization's first administrator, or None.

    The console created them, and the audit trail recorded which address it
    used -- that is the account being handed over. An organization that
    registered itself, or predates the console, has no such entry, and the
    earliest active administrator is the honest stand-in.
    """
    from django.contrib.auth import get_user_model

    from .models import PlatformAuditLog

    User = get_user_model()
    with no_tenant():
        changes = (PlatformAuditLog.objects
                   .filter(organization=organization,
                           action=PlatformAuditLog.Action.ADMIN_USER_CREATED)
                   .order_by("created_at")
                   .values_list("changes", flat=True)
                   .first()) or {}
    email = (changes or {}).get("email")

    with tenant_context(organization):
        active = User.objects.filter(is_active=True)
        user = active.filter(email__iexact=email).first() if email else None
        if user is None:
            user = (active.filter(role=User.Roles.ADMIN)
                    .order_by("date_joined").first())
    return user


def _administrator_summary(user):
    if user is None:
        return None
    awaiting = bool(user.must_change_password)
    return {
        "name": user.get_full_name() or user.username,
        "email": user.email,
        "last_login": user.last_login,
        "signed_in": user.last_login is not None,
        # True from creation until they choose their own password. While it
        # holds, the console may issue a fresh temporary password; after it,
        # the password is theirs and the console must not touch it.
        "awaiting_first_sign_in": awaiting,
    }


# ---------------------------------------------------------------------------
# The package
# ---------------------------------------------------------------------------
def package(organization):
    """Everything a customer needs to get in, and nothing secret.

    The temporary password is deliberately absent. Only the creation
    response carries it, once; this is what the organization page shows
    every day after that.
    """
    custom = _custom_domain(organization)
    admin = administrator(organization)
    subscription = getattr(organization, "subscription", None)

    trial = None
    plan = None
    if subscription is not None:
        plan = {"code": subscription.plan.code, "name": subscription.plan.name}
        if subscription.trial_end and subscription.status == "trial":
            remaining = (subscription.trial_end - timezone.localdate()).days
            total = None
            if subscription.trial_start:
                total = (subscription.trial_end - subscription.trial_start).days
            trial = {"days": total, "ends_on": subscription.trial_end,
                     "days_remaining": max(remaining, 0)}

    return {
        "organization": {"name": organization.name,
                         "slug": organization.slug},
        "login_url": login_url(organization, custom),
        "workspace_url": subdomain_url(organization),
        "custom_domain": custom,
        "administrator": _administrator_summary(admin),
        "plan": plan,
        "trial": trial,
        # A workspace that is suspended or archived will refuse the customer
        # however correct the details are. Said beside the details, so
        # nobody sends a welcome email into a locked door.
        "can_sign_in": bool(organization.is_admitted),
        "support_email": getattr(settings, "PLATFORM_SUPPORT_EMAIL", ""),
        "powered_by": getattr(settings, "PLATFORM_POWERED_BY",
                              "Powered by Buddhi Labs"),
    }


# ---------------------------------------------------------------------------
# Sending it
# ---------------------------------------------------------------------------
def send_access(organization, *, actor, password=None, request=None):
    """Email the administrator their sign-in details. Returns a receipt.

    ``password`` given: the one the console showed at creation. It must
    still be the account's password and still be temporary, or nothing is
    sent.

    ``password`` omitted: a new temporary password is issued and sent, but
    only while the administrator has not yet chosen their own.

    If the mail server refuses, the reissue is rolled back, so the password
    the customer may already hold still works. Nothing changes unless the
    email actually went out.
    """
    from . import platform_audit
    from .models import PlatformAuditLog

    admin = administrator(organization)
    if admin is None:
        raise TenancyError(
            "This organization has no administrator account yet, so there is "
            "nobody to send sign-in details to. Add one first.")
    if not admin.email:
        raise TenancyError(
            "The administrator account has no email address to send to.")
    if not admin.must_change_password:
        raise TenancyError(
            f"{admin.email} has already chosen their own password, so there "
            f"is no temporary one to send. If they forget it, another "
            f"administrator in their workspace can reset it.")

    reissued = not password
    if password:
        with tenant_context(organization):
            if not admin.check_password(password):
                raise TenancyError(
                    "That is not this account's current temporary password, "
                    "so it was not sent. Use “Email new sign-in details” "
                    "on the organization page instead.")
        temporary = password
    else:
        temporary = secrets.token_urlsafe(12)

    details = package(organization)
    with transaction.atomic():
        if reissued:
            with tenant_context(organization):
                admin.set_password(temporary)
                admin.must_change_password = True
                admin.save(update_fields=["password", "must_change_password"])
        try:
            _deliver(organization, admin, temporary, details,
                     reissued=reissued)
        except Exception as exc:                   # noqa: BLE001
            # Raised inside the atomic block ON PURPOSE: it undoes the
            # reissue, so a customer holding the earlier password is not
            # locked out by an email that never arrived.
            logger.warning("access email to %s failed", admin.email,
                           exc_info=True)
            raise AccessDeliveryFailed(
                f"The email to {admin.email} could not be sent "
                f"({type(exc).__name__}). Nothing was changed: any password "
                f"already given to them still works.") from exc

        if reissued:
            platform_audit.record(
                actor, PlatformAuditLog.Action.ACCESS_REISSUED,
                organization=organization,
                changes={"email": admin.email},
                note="A new temporary password was issued and emailed. The "
                     "previous one no longer works.",
                request=request)
        platform_audit.record(
            actor, PlatformAuditLog.Action.ACCESS_SENT,
            organization=organization,
            changes={"email": admin.email, "login_url": details["login_url"],
                     "reissued": reissued},
            note=f"Sign-in details emailed to {admin.email}.",
            request=request)

    return {"sent_to": admin.email, "login_url": details["login_url"],
            "reissued": reissued, "sent_at": timezone.now()}


def _deliver(organization, admin, temporary, details, *, reissued):
    """Render and send, synchronously, raising on failure.

    SYNCHRONOUS, unlike notification email. The operator pressed a button and
    is waiting to be told whether the customer has their details. A
    background send would answer "sent" before anything had been tried.

    In the tenant's context, so the header carries THEIR logo, name and
    colour -- this is the first message the customer receives from their own
    workspace, and it should look like it.
    """
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    from notifications.emails import _branding, powered_by

    with tenant_context(organization):
        brand = _branding()
    context = {
        **brand,
        "powered_by": powered_by(),
        "recipient_name": admin.first_name if admin.first_name not in (
            "", "Account") else "",
        "organization_name": organization.name,
        "login_url": details["login_url"],
        "email": admin.email,
        "temporary_password": temporary,
        "trial": details["trial"],
        "plan": details["plan"],
        "support_email": details["support_email"],
        "reissued": reissued,
    }
    subject = (f"Your new sign-in details for {organization.name}"
               if reissued else f"Your {organization.name} workspace is ready")
    text = render_to_string("emails/workspace_access.txt", context)
    html = render_to_string("emails/workspace_access.html", context)
    message = EmailMultiAlternatives(subject, text,
                                     settings.DEFAULT_FROM_EMAIL, [admin.email])
    message.attach_alternative(html, "text/html")
    message.send(fail_silently=False)


# ---------------------------------------------------------------------------
# Closing the loop
# ---------------------------------------------------------------------------
def record_first_sign_in(user, request=None):
    """Note on the platform trail that a client administrator got in.

    Called when a forced first-login password change succeeds, which is the
    moment the handover is genuinely complete: the customer reached their
    workspace with the details they were sent, and the temporary password is
    now dead. Without this the console could only guess, from silence,
    whether a new customer ever arrived.

    Never raises. A sign-in must not fail because a record of it could not
    be written.
    """
    from . import platform_audit
    from .models import Organization, PlatformAuditLog

    try:
        if user.organization_id is None or user.role != user.Roles.ADMIN:
            return
        with no_tenant():
            organization = Organization.objects.get(pk=user.organization_id)
        platform_audit.record(
            None, PlatformAuditLog.Action.ADMIN_FIRST_SIGN_IN,
            organization=organization,
            changes={"email": user.email},
            note=f"{user.email} signed in and chose their own password. "
                 f"Handover complete.",
            request=request)
    except Exception:                              # noqa: BLE001
        logger.warning("could not record first sign-in for user %s",
                       getattr(user, "pk", None), exc_info=True)
