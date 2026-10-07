"""Phase S9 Parts 3, 4 and 5: a hostname a customer proves they own.

THE GAP THIS CLOSES, STATED PLAINLY. ``Organization.domain`` has existed
since Phase S1: a single column, unique, writable from the console, and
matched by ``resolver.resolve()`` before any subdomain. Nothing verified it.
"Please point hr.abcschool.edu.np at us" was granted on the customer's word,
and the same form would have accepted a competitor's hostname, a bank's, or a
typo -- after which that name resolved to that tenant for anybody whose DNS
sent them there.

A hostname is a claim about something outside this platform. The only way to
settle it is to ask DNS, so that is what this module does.

    claim   ->  token issued, record name and value shown to the customer
    verify  ->  DNS is queried; found means verified, absent means failed
    activate->  the resolver begins serving the hostname

THE RESOLVER HONOURS ONLY `ACTIVE`, which is what makes verification mean
anything. A pending or failed domain resolves to nothing at all.

ONE TOKEN PER DOMAIN, not per tenant: proving `hr.abcschool.edu.np` must
prove nothing about `portal.abcschool.edu.np`, because a customer who can
publish a record on one subdomain cannot necessarily publish on another, and
an attacker who can publish on one they control certainly cannot on one they
do not.

WHY DNS LOOKUPS GO THROUGH AN ADAPTER. The library for this is dnspython,
which is not currently installed -- so the lookup is one function with a
clear "unavailable" answer rather than an import that fails at startup. The
whole flow is therefore testable without the library, the deployment is told
it needs it by ``tenancy.launch``, and nothing silently pretends to have
checked DNS when it has not.
"""
import logging
import re
import secrets

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from .exceptions import TenancyError

logger = logging.getLogger(__name__)

# A hostname, conservatively: labels of letters, digits and hyphens, at least
# one dot, no leading or trailing hyphen, 253 characters at most.
_HOSTNAME = re.compile(
    r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

# Hostnames nobody may claim, whatever DNS says. A customer who proved
# ownership of the platform's own domain could point it at their own tenant
# and serve the console's hostname from their workspace.
def _forbidden_suffixes():
    base = (getattr(settings, "TENANCY_BASE_DOMAIN", "") or "").strip().lower()
    hosts = [h.strip().lower() for h
             in (getattr(settings, "TENANCY_PLATFORM_HOSTS", "") or "")
             .split(",") if h.strip()]
    out = set(hosts)
    if base:
        out.add(base)
    return out


class InvalidDomain(TenancyError):
    """The hostname is malformed, reserved, or already claimed."""


class VerificationUnavailable(TenancyError):
    """DNS lookups cannot be performed in this deployment."""


# ---------------------------------------------------------------------------
# The DNS adapter
# ---------------------------------------------------------------------------
def dns_available():
    try:
        import dns.resolver                        # noqa: F401
    except Exception:                              # noqa: BLE001
        return False
    return True


def lookup(name, record_type):
    """Every value published at ``name`` for ``record_type``, lowercased.

    Raises ``VerificationUnavailable`` when the deployment cannot resolve
    DNS at all, which is a different answer from "the record is not there"
    and must not be confused with it: telling a customer their record is
    missing when we never looked is how somebody spends an afternoon
    re-checking a zone file that was right all along.
    """
    try:
        import dns.resolver
    except Exception as exc:                       # noqa: BLE001
        raise VerificationUnavailable(
            "This deployment cannot perform DNS lookups, so domain "
            "ownership cannot be verified. Install dnspython."
        ) from exc

    try:
        answers = dns.resolver.resolve(name, record_type, lifetime=5.0)
    except dns.resolver.NXDOMAIN:
        return []
    except dns.resolver.NoAnswer:
        return []
    except Exception as exc:                       # noqa: BLE001
        # A timeout or a SERVFAIL is not "absent" either. Reported as a
        # failure to check rather than a failure to find.
        raise VerificationUnavailable(
            f"DNS lookup for {name} failed: {type(exc).__name__}") from exc

    values = []
    for answer in answers:
        text = answer.to_text().strip().strip('"')
        values.append(text.lower().rstrip("."))
    return values


# ---------------------------------------------------------------------------
# Part 3: claim
# ---------------------------------------------------------------------------
def normalise(hostname):
    host = (hostname or "").strip().lower().rstrip(".")
    if host.startswith("http://") or host.startswith("https://"):
        host = host.split("//", 1)[1]
    return host.split("/", 1)[0].split(":", 1)[0]


def validate(hostname):
    """The hostname, normalised, or a refusal that says which rule it broke."""
    host = normalise(hostname)
    if not host:
        raise InvalidDomain("Enter the address you want to use.")
    if not _HOSTNAME.match(host):
        raise InvalidDomain(
            "That is not a valid domain name. Use something like "
            "hr.yourcompany.com -- letters, digits and hyphens only.")
    for suffix in _forbidden_suffixes():
        if host == suffix or host.endswith(f".{suffix}"):
            raise InvalidDomain(
                f"'{host}' belongs to the platform and cannot be claimed. "
                f"Your workspace already has an address there.")
    return host


@transaction.atomic
def claim(organization, hostname, *, method=None, actor=None, request=None):
    """Record a claim and issue the proof the customer must publish.

    NOTHING RESOLVES YET. The domain is PENDING until DNS says otherwise,
    which is the difference between this and the column it replaces.
    """
    from .models import PlatformAuditLog, TenantDomain

    host = validate(hostname)
    method = method or TenantDomain.Method.TXT

    existing = TenantDomain.objects.filter(hostname=host).first()
    if existing is not None:
        if existing.organization_id != organization.pk:
            # Deliberately the same message whoever asks: "another customer
            # has that domain" tells a stranger which hostnames are on this
            # platform.
            raise InvalidDomain(
                "That address is not available. If you own it and believe "
                "this is wrong, contact support.")
        if existing.status == TenantDomain.Status.REMOVED:
            existing.status = TenantDomain.Status.PENDING
            existing.verification_token = secrets.token_hex(16)
            existing.method = method
            existing.last_error = ""
            existing.save(update_fields=["status", "verification_token",
                                         "method", "last_error",
                                         "updated_at"])
        return existing

    domain = TenantDomain.objects.create(
        organization=organization,
        hostname=host,
        method=method,
        verification_token=secrets.token_hex(16),
        created_by=actor if getattr(actor, "pk", None) else None,
    )

    from . import platform_audit

    platform_audit.record(
        actor if getattr(actor, "is_platform_staff", False) else None,
        PlatformAuditLog.Action.DOMAIN_CLAIMED,
        organization=organization,
        changes={"hostname": host, "method": method},
        note=(f"{host} claimed. It resolves to nothing until ownership is "
              f"verified."),
        request=request)
    return domain


def instructions(domain):
    """What to publish, in the form a customer pastes into a DNS panel."""
    hosts = [h.strip() for h
             in (getattr(settings, "TENANCY_PLATFORM_HOSTS", "") or "")
             .split(",") if h.strip()]
    platform_host = hosts[0] if hosts else (
        getattr(settings, "TENANCY_BASE_DOMAIN", "") or "")
    return {
        "hostname": domain.hostname,
        "method": domain.method,
        "record_name": domain.expected_record_name,
        "record_type": "TXT" if domain.method == "txt" else "CNAME",
        "record_value": domain.expected_record_value(
            platform_host=platform_host),
        # And what to point the hostname ITSELF at, which is a separate
        # record and the one people forget: proving ownership does not make
        # traffic arrive.
        "serving_record": {
            "name": domain.hostname,
            "type": "CNAME",
            "value": platform_host,
            "note": ("This is what sends visitors to us. The verification "
                     "record above only proves the domain is yours."),
        },
        "status": domain.status,
        "last_error": domain.last_error,
    }


# ---------------------------------------------------------------------------
# Part 4: verify
# ---------------------------------------------------------------------------
def verify(domain, *, actor=None, request=None, activate=True):
    """Ask DNS whether the proof is published. Returns the domain.

    ``activate`` moves a verified domain straight to serving, which is what
    a customer expects -- they published a record and want their address to
    work. It is separable because an operator may want to verify without
    switching traffic during a migration.

    NOT ATOMIC AS A WHOLE, AND THAT IS THE POINT -- FOUND BY A TEST.

    The first version of this function wrapped everything in one
    transaction, including the DNS query. Two things were wrong with that.

    The smaller one: a 5-second network call was made while holding
    ``SELECT ... FOR UPDATE`` on the row, so a customer pressing "Check now"
    twice queued the second press behind the first lookup's timeout.

    The one that actually broke a feature: when the lookup could not be
    performed, this code deliberately recorded the attempt and the reason
    WITHOUT marking the domain failed -- and then re-raised, which rolled
    the whole transaction back and discarded exactly that bookkeeping. The
    attempt counter stayed at zero and the explanation was never stored, so
    an operator looking at the console saw a domain nobody had ever checked.
    The care taken over the "we could not look" case was invisible in the
    database.

    So the lookup happens outside any transaction, and each of the two
    outcomes commits on its own.
    """
    from .models import TenantDomain

    expected = domain.expected_record_value(
        platform_host=_platform_host()).lower()
    record_type = "TXT" if domain.method == TenantDomain.Method.TXT else "CNAME"
    name = domain.expected_record_name

    try:
        found = lookup(name, record_type)
    except VerificationUnavailable as exc:
        # NOT marked failed: we did not look. A domain the platform could
        # not check must not be reported to the customer as wrong.
        _record_attempt(domain, last_error=str(exc)[:300])
        raise

    return _settle(domain, expected, found, record_type, actor=actor,
                   request=request, activate=activate)


@transaction.atomic
def _record_attempt(domain, *, last_error=""):
    """Note that a check was made, and why it answered nothing useful."""
    from .models import TenantDomain

    (TenantDomain.objects
     .filter(pk=domain.pk)
     .update(check_count=models.F("check_count") + 1,
             last_checked_at=timezone.now(),
             last_error=last_error,
             updated_at=timezone.now()))
    domain.refresh_from_db()
    return domain


@transaction.atomic
def _settle(domain, expected, found, record_type, *, actor=None, request=None,
            activate=True):
    """Apply what DNS said. Locks the row; does no network I/O."""
    from .models import PlatformAuditLog, TenantDomain

    domain = TenantDomain.objects.select_for_update().get(pk=domain.pk)
    domain.check_count += 1
    domain.last_checked_at = timezone.now()

    if expected in found:
        domain.status = (TenantDomain.Status.ACTIVE if activate
                         else TenantDomain.Status.VERIFIED)
        domain.verified_at = domain.verified_at or timezone.now()
        domain.last_error = ""
        if not TenantDomain.objects.filter(
                organization=domain.organization, is_primary=True
        ).exclude(pk=domain.pk).exists():
            domain.is_primary = True
        domain.save()

        from . import platform_audit, resolver

        platform_audit.record(
            actor if getattr(actor, "is_platform_staff", False) else None,
            PlatformAuditLog.Action.DOMAIN_VERIFIED,
            organization=domain.organization,
            changes={"hostname": domain.hostname, "method": domain.method,
                     "status": domain.status},
            note=f"{domain.hostname} verified by DNS and is now serving.",
            request=request)
        # The resolver caches host -> organization; a newly active domain
        # must take effect now rather than when a cache entry expires. And
        # the "does this deployment serve any custom domain?" flag has just
        # flipped for the FIRST domain on a deployment -- without evicting
        # it, the resolver would keep skipping the lookup it now needs.
        resolver.forget(domain.organization)
        resolver.forget_custom_domains()
        # And the ALLOWED_HOSTS cache: until this hostname is in there,
        # Django answers 400 before the resolver is ever consulted.
        from . import allowed_hosts

        allowed_hosts.forget()
        return domain

    domain.status = TenantDomain.Status.FAILED
    domain.last_error = (
        f"No {record_type} record at {domain.expected_record_name} with the "
        f"expected value."
        + (f" Found: {', '.join(found[:3])}" if found else " Nothing found.")
    )[:300]
    domain.save()
    logger.info("domain %s failed verification: %s", domain.hostname,
                domain.last_error)
    return domain


def _platform_host():
    hosts = [h.strip() for h
             in (getattr(settings, "TENANCY_PLATFORM_HOSTS", "") or "")
             .split(",") if h.strip()]
    return hosts[0] if hosts else (
        getattr(settings, "TENANCY_BASE_DOMAIN", "") or "")


@transaction.atomic
def remove(domain, *, actor=None, request=None):
    """Withdraw a domain. Kept as a record that it was once claimed."""
    from .models import PlatformAuditLog, TenantDomain

    from . import platform_audit, resolver

    organization = domain.organization
    domain.status = TenantDomain.Status.REMOVED
    domain.is_primary = False
    domain.save(update_fields=["status", "is_primary", "updated_at"])

    platform_audit.record(
        actor if getattr(actor, "is_platform_staff", False) else None,
        PlatformAuditLog.Action.DOMAIN_REMOVED,
        organization=organization,
        changes={"hostname": domain.hostname},
        note=f"{domain.hostname} withdrawn; it no longer resolves.",
        request=request)
    resolver.forget(organization)
    resolver.forget_custom_domains()
    from . import allowed_hosts

    allowed_hosts.forget()
    return domain


# ---------------------------------------------------------------------------
# Part 5: resolution
# ---------------------------------------------------------------------------
def organization_for_host(host):
    """The organization serving ``host`` through a verified custom domain.

    Returns None for anything not ACTIVE, which is the enforcement: a
    pending claim and a failed check both resolve to nothing.
    """
    from .models import TenantDomain

    host = normalise(host)
    if not host:
        return None
    domain = (TenantDomain.objects
              .select_related("organization")
              .filter(hostname=host,
                      status__in=TenantDomain.SERVING_STATUSES)
              .first())
    return domain.organization if domain else None


def for_organization(organization):
    """Every domain a tenant has claimed, newest state first."""
    from .models import TenantDomain

    return list(TenantDomain.objects
                .filter(organization=organization)
                .exclude(status=TenantDomain.Status.REMOVED))
