"""Phase S7: a stranger asks for a workspace, and gets one.

THE SHAPE OF THE FLOW, AND WHY IT IS THIS SHAPE

    submit  ->  PendingRegistration (no tenant)  ->  email
                                                      |
                                    click the link    v
                            provision_organization()  ->  TRIAL workspace

Part 3 forbids creating a tenant before the email is verified, and that one
rule decides everything else here. Provisioning on submit would mean anybody
could, anonymously:

  * take a subdomain -- `nif`, `police`, a competitor's name -- and keep it;
  * appear on the platform dashboard as a customer, in the organization
    counts an operator reads to run the business;
  * cause the platform to send mail to an address they do not control;
  * consume a bootstrap each time, which is 76 rows and a document-number
    sequence.

So nothing is created until somebody proves they can read the mailbox they
registered with. Until then there is one row in one platform table, it holds
the subdomain, and it expires.

WHAT THIS MODULE DELIBERATELY DOES NOT DO

It does not take a payment, price a plan or collect a card. Part 5 is "14-day
trial, no billing workflow yet", and the brief's critical rule lists every
gateway by name. A self-registered tenant lands on a trial and an operator
decides what happens at the end of it -- which is also why `start_trial`
already existed in the console before this phase.
"""
import hashlib
import logging
import secrets

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import platform_audit, services, slugs
from .context import no_tenant
from .exceptions import TenancyError
from .models import Organization, PendingRegistration

logger = logging.getLogger(__name__)

# The shortest subdomain a stranger may claim through the public form. See
# `slug_status` for why this is stricter than the platform's own rule.
MIN_PUBLIC_SLUG_LENGTH = 3


class RegistrationClosed(TenancyError):
    """Public registration is not enabled on this deployment."""


class SlugUnavailable(TenancyError):
    """The requested subdomain is taken, reserved or malformed."""


class VerificationFailed(TenancyError):
    """The token is unknown, already used, or expired."""


def is_open():
    return bool(getattr(settings, "TENANCY_PUBLIC_REGISTRATION", False))


def require_open():
    if not is_open():
        raise RegistrationClosed(
            "This deployment does not accept public registrations.")


# ---------------------------------------------------------------------------
# Part 2: subdomain availability
# ---------------------------------------------------------------------------
def slug_status(value):
    """Why a subdomain is or is not available, in one dict.

    ONE FUNCTION FOR THE CHECKER AND THE FORM, deliberately. If the
    keystroke-time check and the submit-time validation were separate pieces
    of code they would eventually disagree, and the disagreement a user meets
    is the worst one: told it was available, then refused on submit.

    THE REASONS ARE DISTINGUISHED because they need different answers from a
    person. "Too short" is a typo, "reserved" means pick a different word,
    and "taken" means somebody else has it. A single "invalid" would send all
    three to the same dead end.
    """
    raw = (value or "").strip().lower()
    if not raw:
        return {"slug": raw, "available": False, "reason": "empty",
                "detail": "Choose a workspace address."}

    # A SELF-SERVICE MINIMUM, which the platform's own slug rules do not
    # impose. `validate_tenant_slug` allows a single character because a
    # one-letter DNS label is perfectly legal, and an operator creating a
    # tenant by hand may have a reason to use one. A public form is
    # different: one- and two-letter subdomains are scarce, memorable and
    # worth money, and handing them out to whoever submits a form first is
    # not a decision this endpoint should be making.
    if len(raw) < MIN_PUBLIC_SLUG_LENGTH:
        return {"slug": raw, "available": False, "reason": "too_short",
                "detail": (f"Use at least {MIN_PUBLIC_SLUG_LENGTH} "
                           f"characters.")}
    try:
        normalised = slugs.validate_tenant_slug(raw)
    except Exception as exc:                       # noqa: BLE001
        # `validate_tenant_slug` raises ValidationError with a message
        # written for a person; reserved words land here too.
        reason = "reserved" if "reserved" in str(exc).lower() else "invalid"
        return {"slug": raw, "available": False, "reason": reason,
                "detail": _first_message(exc)}

    with no_tenant():
        # `objects`, not `all_tenants`: Organization is a PLATFORM table
        # and has no tenant-scoped manager -- there is nothing to scope
        # it to, which is the same reason it carries no RLS policy.
        if Organization.objects.filter(slug=normalised).exists():
            return _taken(normalised)
        if (PendingRegistration.objects
                .filter(slug=normalised,
                        status__in=PendingRegistration.LIVE_STATUSES)
                .exists()):
            # Held by somebody mid-registration. Reported as taken rather
            # than as "reserved for 24 hours", because telling a stranger
            # that a registration for this subdomain is in progress is
            # telling them something about somebody else.
            return _taken(normalised)

    return {"slug": normalised, "available": True, "reason": None,
            "detail": f"{normalised} is available."}


def _taken(normalised):
    return {"slug": normalised, "available": False, "reason": "taken",
            "detail": "That workspace address is already in use."}


def _first_message(exc):
    messages = getattr(exc, "messages", None)
    return messages[0] if messages else str(exc)


# ---------------------------------------------------------------------------
# Part 1: register
# ---------------------------------------------------------------------------
@transaction.atomic
def register(*, organization_name, slug, organization_email, admin_email,
             password, admin_name="", industry="", country="", request=None):
    """Record a registration and return ``(registration, raw_token)``.

    The raw token is returned, never stored: the caller mails it and drops it.

    DUPLICATE SUBMISSIONS ARE COLLAPSED, NOT REFUSED. Somebody who registers,
    loses the email and registers again should get another email, not an
    error telling them an account already exists -- that message is also how
    an attacker enumerates addresses. The second submission replaces the
    first's token (so the old link dies) and is counted, with a ceiling: the
    platform will send a few verification emails to an unproven address, not
    an unlimited number on request.
    """
    require_open()

    availability = slug_status(slug)
    if not availability["available"]:
        # Unless the holder IS this registrant, re-submitting their own.
        existing = _live_for(admin_email)
        if not (existing and existing.slug == availability["slug"]):
            raise SlugUnavailable(availability["detail"])

    normalised_slug = availability["slug"]
    admin_email = (admin_email or "").strip().lower()
    token = secrets.token_urlsafe(32)

    existing = _live_for(admin_email)
    if existing is not None:
        if existing.verification_sends >= _max_sends():
            raise TenancyError(
                "Too many verification emails have been sent for this "
                "address. Please try again later or contact support.")
        existing.organization_name = organization_name
        existing.slug = normalised_slug
        existing.industry = industry or existing.industry
        existing.country = (country or existing.country or "")[:2]
        existing.organization_email = organization_email
        existing.admin_name = admin_name or existing.admin_name
        existing.admin_password = make_password(password)
        existing.token_hash = hash_token(token)
        existing.token_expires_at = _expiry()
        existing.verification_sends += 1
        existing.status = PendingRegistration.Status.PENDING
        existing.ip_address = _ip(request) or existing.ip_address
        existing.user_agent = _agent(request) or existing.user_agent
        existing.save()
        logger.info("registration re-sent for %s (%s)", normalised_slug,
                    admin_email)
        return existing, token

    try:
        registration = PendingRegistration.objects.create(
            organization_name=organization_name.strip(),
            slug=normalised_slug,
            industry=industry or "",
            country=(country or "")[:2],
            organization_email=(organization_email or "").strip().lower(),
            admin_name=(admin_name or "").strip(),
            admin_email=admin_email,
            # Hashed here, at the edge. The plaintext does not travel further
            # into the platform than this line.
            admin_password=make_password(password),
            token_hash=hash_token(token),
            token_expires_at=_expiry(),
            verification_sends=1,
            ip_address=_ip(request),
            user_agent=_agent(request),
        )
    except IntegrityError as exc:
        # The partial unique indexes. Reached when two submissions race for
        # the same subdomain between the availability check above and this
        # INSERT -- which is exactly what the constraint is for.
        raise SlugUnavailable(
            "That workspace address was just taken. Please choose another."
        ) from exc

    logger.info("registration recorded for %s (%s)", normalised_slug,
                admin_email)
    return registration, token


def held_by(slug, admin_email):
    """Is this subdomain held by THIS registrant's own live registration?

    The difference between "somebody else has it" and "you already asked for
    it", which is the difference between refusing a form and re-sending an
    email.
    """
    existing = _live_for(admin_email)
    return bool(existing and existing.slug == (slug or "").strip().lower())


def _live_for(admin_email):
    with no_tenant():
        return (PendingRegistration.objects
                .filter(admin_email=(admin_email or "").strip().lower(),
                        status__in=PendingRegistration.LIVE_STATUSES)
                .first())


def hash_token(token):
    """SHA-256, not a password hasher.

    A password hasher is deliberately slow to make guessing expensive; this
    token is 32 bytes from ``secrets``, so there is nothing to guess and the
    only job is that a database reader cannot use what they find. A fast hash
    also means the verification endpoint does not become a way to spend the
    platform's CPU.
    """
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _expiry():
    hours = int(getattr(settings, "TENANCY_VERIFICATION_TTL_HOURS", 24))
    return timezone.now() + timezone.timedelta(hours=hours)


def _max_sends():
    return int(getattr(settings, "TENANCY_VERIFICATION_MAX_SENDS", 3))


def _ip(request):
    if request is None:
        return None
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")
    return (forwarded[0].strip() or request.META.get("REMOTE_ADDR")) or None


def _agent(request):
    if request is None:
        return ""
    return (request.META.get("HTTP_USER_AGENT") or "")[:300]


# ---------------------------------------------------------------------------
# Parts 3 and 4: verify, then provision
# ---------------------------------------------------------------------------
def verify(token, *, request=None):
    """Verify an emailed token and create the workspace. Idempotent.

    Returns ``(registration, organization, created)``. ``created`` is False
    when the link had already been used -- a second click answers with the
    workspace rather than an error, because people do click twice and the
    second click must not look like a failure.

    DELIBERATELY NOT ONE BIG TRANSACTION, and the reason is a bug this had
    when it was: the refusals here WRITE. An expired link marks the
    registration EXPIRED, which is what releases the held subdomain -- and
    inside ``atomic`` that write was rolled back by the exception raised
    immediately after it, so the row stayed PENDING for ever and the
    subdomain stayed held by a registration nobody could complete.

    The part that must be atomic is the part that builds something, and
    ``_provision`` is wrapped on its own. Marking a registration dead, and
    counting an attempt, are facts that have to survive the refusal that
    reports them.
    """
    require_open()
    digest = hash_token(token)

    with no_tenant():
        registration = (PendingRegistration.objects
                        .filter(token_hash=digest)
                        .first())
        if registration is None:
            raise VerificationFailed(
                "That verification link is not valid. It may have been "
                "replaced by a newer one.")

        registration.verification_attempts += 1
        registration.save(update_fields=["verification_attempts"])

        if registration.status == PendingRegistration.Status.PROVISIONED:
            return registration, registration.organization, False

        if registration.status in (PendingRegistration.Status.EXPIRED,
                                   PendingRegistration.Status.CANCELLED):
            raise VerificationFailed(
                "That registration is no longer active. Please register "
                "again.")

        if registration.is_expired:
            registration.status = PendingRegistration.Status.EXPIRED
            registration.save(update_fields=["status"])
            raise VerificationFailed(
                "That verification link has expired. Please register again.")

        # `_provision` says whether IT built the workspace. Inferring that
        # from the row afterwards cannot work: the loser of a double-click
        # also finds it PROVISIONED, by the winner.
        organization, created = _provision(registration, request=request)
        return registration, organization, created


@transaction.atomic
def _provision(registration, *, request=None):
    """Turn a verified registration into a working workspace.

    :returns: ``(organization, created)``. ``created`` is False when another
        click built it first -- see the lock below.

    ATOMIC, because a verified registration with no workspace is the worst
    outcome available here: the token is spent, the subdomain is held, and
    the registrant has an email telling them they are ready. Either all of
    it happens or none of it does.

    AND IT RE-READS THE ROW UNDER A LOCK, which is where the double-click
    goes. `verify` deliberately runs outside a transaction so that its
    refusals can record themselves -- but that also means two clicks
    arriving together both get past its status check. Without the lock the
    second one reaches `provision_organization` and dies on the slug's
    unique constraint, which is an IntegrityError, which is a 500 on a link
    somebody simply clicked twice. Here the loser waits, re-reads, sees
    PROVISIONED and returns the workspace the winner built.

    Uses the SAME ``provision_organization`` the console uses -- not a
    parallel path. That is the point of the phase: self-service is a new way
    to reach provisioning, not a second implementation of it, so a tenant
    created by a stranger at midnight is byte-for-byte the same shape as one
    an operator creates by hand, bootstrap and all.
    """
    from .models import PlatformAuditLog

    locked = (PendingRegistration.objects
              .select_for_update()
              .filter(pk=registration.pk)
              .first())
    if locked is not None and locked.status == (
            PendingRegistration.Status.PROVISIONED):
        # The other click won while this one was waiting on the lock.
        return locked.organization, False

    registration.status = PendingRegistration.Status.VERIFIED
    registration.verified_at = timezone.now()
    registration.save(update_fields=["status", "verified_at"])

    try:
        organization = _provision_workspace(registration, request)
    except Exception as exc:
        # THE OPERATOR'S MESSAGE IS NOT THE CUSTOMER'S MESSAGE.
        #
        # `provision_organization` refuses with things like "No purchasable
        # plan exists; seed plans before provisioning" -- correct, actionable,
        # and addressed to whoever runs the platform. A stranger who has just
        # clicked a link in their email is not that person, and telling them
        # about our plan table is both useless to them and more than they
        # should know about our configuration.
        #
        # ANY exception, not just `TenancyError`. A refusal the platform
        # wrote and a bug nobody expected are the same event from the
        # registrant's side -- their workspace did not get built -- and Part
        # 10 of the launch-readiness brief is explicit that customers never
        # receive internal errors. Narrowing this to the exceptions we
        # thought of is how the one we did not think of reaches a stranger as
        # a stack trace.
        #
        # The real reason goes to the log, where somebody can act on it. The
        # whole `_provision` block is atomic, so this registration rolls back
        # to PENDING and the link still works once the platform is fixed.
        logger.error("self-service provisioning of %s failed: %s",
                     registration.slug, exc)
        # THE BRIEF'S OWN WORDING (Part 9). Quoted rather than paraphrased
        # because a customer-facing sentence is a product decision, and the
        # sentence somebody else wrote for this moment is the one to use.
        raise VerificationFailed(
            "Your email has been verified, but we could not complete "
            "workspace setup. Please contact support."
        ) from exc

    _install_chosen_password(organization, registration)
    registration.organization = organization
    registration.status = PendingRegistration.Status.PROVISIONED
    registration.provisioned_at = timezone.now()
    registration.save(update_fields=["organization", "status",
                                     "provisioned_at"])

    platform_audit.record(
        None, PlatformAuditLog.Action.TENANT_CREATED,
        organization=organization,
        changes={"self_service": True,
                 "registration_id": str(registration.pk),
                 "admin_email": registration.admin_email,
                 "verified_at": registration.verified_at.isoformat(),
                 "ip_address": registration.ip_address},
        note=("Created by SELF-SERVICE registration after email "
              "verification. No platform operator was involved."),
        request=request)

    logger.info("self-service provisioned %s for %s", organization.slug,
                registration.admin_email)
    return organization, True


def _provision_workspace(registration, request):
    """The one call that builds the tenant. Same entry point as the console.

    Self-service is a new way to REACH provisioning, not a second
    implementation of it -- so a workspace created by a stranger at midnight
    is the same shape as one an operator creates by hand, bootstrap and all.
    """
    return services.provision_organization(
        name=registration.organization_name,
        slug=registration.slug,
        document_prefix=_document_prefix(registration.slug),
        email=registration.organization_email,
        industry=registration.industry,
        plan=_trial_plan(),
        trial_days=int(getattr(settings, "TENANCY_SELF_SERVICE_TRIAL_DAYS",
                               14)),
        # No actor: nobody at the platform did this, which is the whole
        # point of the phase. The audit entry says "self-service".
        actor=None,
        admin_email=registration.admin_email,
        admin_name=registration.admin_name,
        request=request,
        # OMITTED WHEN BLANK, not passed as "". `Organization.country` has a
        # default of "NP" and is not `blank=True`, so handing it an empty
        # string overrides a valid default with an invalid value --
        # `full_clean` then raises ValidationError from inside provisioning,
        # which reached the registrant as a 500 on the verification link.
        # Country is optional on the form by design; the model's default is
        # what "not stated" means.
        **({"country": registration.country} if registration.country else {}),
    )


def _install_chosen_password(organization, registration):
    """Give the administrator the password they chose at registration.

    ``create_tenant_admin`` generates a temporary one and sets
    ``must_change_password``, which is right when an OPERATOR creates the
    account: the operator has to read the password out to somebody, so it
    must not become permanent. Here nobody else ever saw it -- the registrant
    typed it into the form and the platform only ever held the hash. Forcing
    a change at first sign-in would be asking them to replace a secret only
    they know with another secret only they know, which teaches people that
    the prompt is noise.
    """
    from django.contrib.auth import get_user_model

    from .context import tenant_context

    User = get_user_model()
    with tenant_context(organization):
        user = (User.objects.filter(email=registration.admin_email)
                .order_by("date_joined").first())
        if user is None:                           # pragma: no cover
            logger.error("self-service admin missing for %s",
                         organization.slug)
            return None
        # The hash is installed directly: this function never has, and must
        # never have, the plaintext.
        user.password = registration.admin_password
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])
        return user


def _document_prefix(slug):
    """A document prefix derived from the subdomain.

    An operator types one by hand; a registrant is not asked for one, because
    "document prefix" means nothing to somebody signing up and a bad answer
    is permanent -- it appears on every memo and leave letter the
    organization ever issues. Derived from the subdomain, uppercase, letters
    and digits only, capped at the column width, with a fallback for a slug
    that reduces to nothing.
    """
    cleaned = "".join(ch for ch in (slug or "").upper() if ch.isalnum())
    return (cleaned[:10] or "ORG")


def _trial_plan():
    """The plan a self-registered trial starts on.

    The cheapest public plan, by sort order, because the trial is not a sale:
    it is the shape the workspace has while somebody decides. An operator
    changes it when a plan is actually bought, which is `console.assign_plan`
    and already exists.
    """
    from . import plans

    purchasable = list(plans.purchasable_plans())
    return purchasable[0] if purchasable else None


# ---------------------------------------------------------------------------
# The verification email
# ---------------------------------------------------------------------------
def send_verification_email(registration, token, *, request=None):
    """Mail the link. Never raises; returns True when it went out.

    ITS OWN SENDER, NOT ``notifications.emails``. That module writes a
    ``NotificationLog`` row (tenant-scoped) against a ``User`` (tenant-scoped)
    and renders with tenant branding -- and at this moment there is no tenant,
    no user and no branding. Reaching for it here would mean inventing all
    three for an address nobody has verified.

    A FAILED SEND IS LOGGED, NOT RAISED, and the API still answers the same
    way. Two reasons: the caller must not learn from the response whether mail
    to that address succeeded, and a registration whose email bounced is
    recoverable by registering again, where a 500 at the end of a signup form
    is not recoverable by anybody.
    """
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    context = {
        "organization_name": registration.organization_name,
        "admin_email": registration.admin_email,
        "workspace_host": _workspace_host(registration.slug),
        "workspace_url": f"https://{_workspace_host(registration.slug)}/",
        "verify_url": verification_url(token),
        "ttl_hours": int(getattr(settings, "TENANCY_VERIFICATION_TTL_HOURS",
                                 24)),
        "trial_days": int(getattr(settings,
                                  "TENANCY_SELF_SERVICE_TRIAL_DAYS", 14)),
    }
    subject = f"Confirm your email to create {registration.organization_name}"
    try:
        text = render_to_string("emails/verify_registration.txt", context)
        html = render_to_string("emails/verify_registration.html", context)
        message = EmailMultiAlternatives(
            subject, text, settings.DEFAULT_FROM_EMAIL,
            [registration.admin_email])
        message.attach_alternative(html, "text/html")
        message.send(fail_silently=False)
        return True
    except Exception:                              # noqa: BLE001
        logger.warning("verification email to %s failed",
                       registration.admin_email, exc_info=True)
        return False


def verification_url(token):
    """The link in the email.

    Points at the PLATFORM host, not a tenant subdomain: the workspace it
    will create does not resolve yet, so a link to it would 404 until the
    moment the link itself is used.
    """
    base = (getattr(settings, "TENANCY_PUBLIC_BASE_URL", "") or "").rstrip("/")
    if not base:
        hosts = [h for h in (settings.TENANCY_PLATFORM_HOSTS or "").split(",")
                 if h.strip()]
        host = hosts[0].strip() if hosts else (
            settings.TENANCY_BASE_DOMAIN or "localhost")
        base = f"https://{host}"
    return f"{base}/verify-email?token={token}"


def _workspace_host(slug):
    domain = (settings.TENANCY_BASE_DOMAIN or "").strip()
    return f"{slug}.{domain}" if domain else slug


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------
def expire_stale(*, now=None):
    """Mark unverified registrations past their TTL as expired.

    Run by ``manage.py tenancy_expire_registrations``. This is what releases
    a held subdomain: the partial unique index only covers the live statuses,
    so moving a row to EXPIRED frees the name for somebody else.
    """
    now = now or timezone.now()
    with no_tenant():
        stale = (PendingRegistration.objects
                 .filter(status=PendingRegistration.Status.PENDING,
                         token_expires_at__lte=now))
        released = list(stale.values_list("slug", flat=True))
        updated = stale.update(status=PendingRegistration.Status.EXPIRED)
    if updated:
        logger.info("expired %d registration(s), released: %s", updated,
                    released)
    return {"expired": updated, "released_slugs": released}
