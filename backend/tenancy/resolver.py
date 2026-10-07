"""Host -> Organization.

The hostname selects which tenant's *branding* and *workspace* a visitor is
looking at. It is deliberately NOT the authorization input: once a request is
authenticated, the organization comes from the JWT's ``org`` claim, and the
middleware's job is to check that the two agree. Resolving authority from a
user-supplied URL is the classic multi-tenant mistake; this module exists so
that the host is only ever used as a lookup key.

Resolution is cached, because it would otherwise add a query to every request
including unauthenticated ones. The cache is invalidated explicitly whenever an
Organization's slug, domain or status changes (``tenancy.services``).
"""
import logging
from django.conf import settings
from django.core.cache import cache

# How long a host -> organization mapping may be served from cache. Short,
# because a suspension must take effect promptly; the gate is re-checked against
# this same cached record on every request.
RESOLVE_TTL = 60

_MISS = "\x00miss"


logger = logging.getLogger(__name__)

def _cache_key(host):
    return f"tenancy:host:{host}"


def platform_hosts():
    """Hostnames that address the platform console rather than any tenant.

    Configured, not hardcoded: the development host differs from production and
    a misconfiguration here means the console is unreachable or, worse, that a
    tenant slug shadows it.
    """
    raw = getattr(settings, "TENANCY_PLATFORM_HOSTS", "") or ""
    if isinstance(raw, str):
        hosts = [h.strip().lower() for h in raw.split(",")]
    else:
        hosts = [str(h).strip().lower() for h in raw]
    return tuple(h for h in hosts if h)


def tenant_base_domain():
    """The domain tenant subdomains hang off, e.g. ``platform.com``."""
    return (getattr(settings, "TENANCY_BASE_DOMAIN", "") or "").strip().lower()


def normalise_host(host):
    """Lowercase, strip the port and any trailing dot."""
    if not host:
        return ""
    host = host.strip().lower()
    # IPv6 literals arrive bracketed ("[::1]:8000"); split the port off the tail
    # only, so the colons inside the brackets survive.
    if host.startswith("["):
        closing = host.find("]")
        if closing != -1:
            host = host[: closing + 1]
    elif ":" in host:
        host = host.split(":", 1)[0]
    return host.rstrip(".")


def is_platform_host(host):
    return normalise_host(host) in platform_hosts()


def slug_from_host(host):
    """The tenant slug a host names, or None.

    ``nif.platform.com`` -> ``nif`` when the base domain is ``platform.com``.
    Anything that is not a single label under the base domain returns None: a
    deeper name (``a.b.platform.com``) is not a tenant, because a wildcard
    certificate would not cover it.
    """
    host = normalise_host(host)
    if not host or is_platform_host(host):
        return None

    base = tenant_base_domain()
    if not base or not host.endswith("." + base):
        return None

    label = host[: -(len(base) + 1)]
    if not label or "." in label:
        return None
    return label


def _encode(organization, host=None):
    """``<id>|<slug>|<domain>`` -- everything needed to re-validate, no row.

    ``host`` is the hostname this entry was created FOR. When a tenant was
    reached through a verified custom domain, that hostname goes in the
    `domain` slot: the warm path then re-validates with a string comparison
    instead of a `TenantDomain` query, which is the whole point of caching
    this at all. Withdrawal is still immediate because `forget()` evicts
    every hostname a tenant has ever claimed.
    """
    domain = organization.domain or ""
    if host and normalise_host(host) != normalise_host(domain or ""):
        slug = slug_from_host(host)
        if not slug or slug != organization.slug:
            domain = normalise_host(host)
    return f"{organization.pk}|{organization.slug}|{domain}"


def _decode(raw):
    parts = str(raw).split("|")
    if len(parts) != 3:
        return None
    org_id, slug, domain = parts
    return org_id, slug, domain


def resolve_id(host):
    """The organization ID a host addresses, or None. NO ROW FETCH when warm.

    THIS IS THE HOT PATH (Phase S4). Once ``TenantManager`` is the default
    manager on 55 models, every request resolves its tenant -- so resolution
    has to cost nothing on a warm cache, or the project's query-budget tests
    (and the production latency they stand for) regress by a query per request.

    ``resolve()`` below still returns the full row for callers that need the
    object. This returns only the id, and re-validates the host against the
    SLUG AND DOMAIN carried in the cache entry rather than by re-reading the
    row -- so the Phase S3 protection against a stale mapping is kept without
    the fetch. ``Organization.save()`` calls ``forget()``, so a rename evicts
    immediately rather than waiting out the TTL.
    """
    host = normalise_host(host)
    if not host or is_platform_host(host):
        return None

    key = _cache_key(host)
    cached = cache.get(key)
    if cached == _MISS:
        return None
    if cached is not None:
        decoded = _decode(cached)
        if decoded is not None:
            org_id, slug, domain = decoded
            if (domain and domain == host) or slug_from_host(host) == slug:
                return org_id
        cache.delete(key)

    organization = _resolve_from_db(host)
    cache.set(key, _encode(organization, host) if organization else _MISS,
              RESOLVE_TTL)
    return str(organization.pk) if organization else None


# How long "this deployment serves no custom domain at all" is believed.
# Short, because the answer flips the moment a customer's first domain is
# verified -- and `forget_custom_domains()` evicts it on that event anyway,
# so this is only the backstop for a process that missed the eviction.
CUSTOM_FLAG_KEY = "tenancy:any-custom-domain"
CUSTOM_FLAG_TTL = 60


def custom_domains_possible(host):
    """Could ``host`` be a verified custom domain? Cheap, and usually no.

    WHY THIS GUARD EXISTS. Phase S9 made the resolver consult `TenantDomain`,
    and a query-budget test caught the cost immediately: EVERY request grew a
    lookup, including on the deployments that have no custom domain at all
    and including for hosts that cannot possibly be one.

    Two questions, cheapest first:

    1. Is the host under the platform's own base domain? Then it is not a
       custom domain BY CONSTRUCTION -- `tenancy.domains.validate` refuses to
       let anybody claim a name there, precisely so a customer cannot serve
       the console's hostname from their workspace. No query needed.

    2. Does this deployment serve any custom domain whatsoever? One cached
       boolean, so the single-tenant deployment NIF runs today -- and every
       new SaaS deployment before its first custom domain -- pays nothing.
    """
    base = tenant_base_domain()
    if base and (host == base or host.endswith("." + base)):
        return False

    cached = cache.get(CUSTOM_FLAG_KEY)
    if cached is not None:
        return cached == "1"

    from .models import TenantDomain

    exists = TenantDomain.objects.filter(
        status__in=TenantDomain.SERVING_STATUSES).exists()
    cache.set(CUSTOM_FLAG_KEY, "1" if exists else "0", CUSTOM_FLAG_TTL)
    return exists


def forget_custom_domains():
    """Drop the "any custom domain?" flag. Called when one is verified."""
    cache.delete(CUSTOM_FLAG_KEY)


def _resolve_from_db(host):
    from .models import Organization

    # A VERIFIED CUSTOM DOMAIN FIRST (Phase S9), and in BOTH resolvers.
    #
    # This function is reached from `resolve_id`, which is what the
    # middleware calls -- and until this was added here, `resolve_id` knew
    # only about `Organization.domain` and the slug. So a customer could
    # claim a hostname, publish the record, watch it verify, and it would
    # still not resolve on a real request: every S9 resolution test went
    # through `resolve()`, the slower path that nothing in the request cycle
    # uses. The feature was complete and unreachable.
    if custom_domains_possible(host):
        from . import domains

        organization = domains.organization_for_host(host)
        if organization is not None:
            return organization

    organization = Organization.objects.filter(domain=host).first()
    if organization is None:
        slug = slug_from_host(host)
        if slug:
            organization = Organization.objects.filter(slug=slug).first()
    return organization


def default_organization_id(slug=""):
    """The fallback organization's ID, or None. NO ROW FETCH when warm."""
    key = f"{DEFAULT_KEY}:{slug}" if slug else DEFAULT_KEY
    cached = cache.get(key)
    if cached == _MISS:
        return None
    if cached is not None:
        decoded = _decode(cached)
        if decoded is not None:
            return decoded[0]
        cache.delete(key)

    organization = default_organization(slug)
    return str(organization.pk) if organization else None


def resolve(host):
    """The Organization a host addresses, or None.

    Matches a custom ``domain`` first (an exact hostname a customer owns), then
    a ``slug`` under the platform's base domain. Returns None for the platform
    console host and for anything unrecognised -- the caller decides what an
    unresolved host means, because that answer differs between the foundation
    phase and enforcement.
    """
    host = normalise_host(host)
    if not host or is_platform_host(host):
        return None

    from .models import Organization

    key = _cache_key(host)
    cached = cache.get(key)
    if cached == _MISS:
        return None
    if cached is not None:
        decoded = _decode(cached)
        org = (Organization.objects.filter(pk=decoded[0]).first()
               if decoded else None)
        # RE-VALIDATE, never trust the cached id on its own. A cache entry
        # outlives the row it describes: an organization renamed (or given a
        # different domain) a moment ago still has an entry under its OLD
        # hostname. Serving it would be wrong twice over -- the tenant is
        # reachable on a name it has given up, and if a DIFFERENT tenant later
        # claims that freed slug, this host would serve the wrong workspace
        # until the entry expired. So the cache is only ever a lookup shortcut;
        # the row itself is the authority on which hosts it answers to.
        if org is not None and _matches(org, host):
            return org
        cache.delete(key)
        # Fall through and resolve from the database.

    # A VERIFIED CUSTOM DOMAIN FIRST (Phase S9). `TenantDomain` is the
    # authority on custom hostnames because it is the only one that knows
    # whether ownership was ever proved: a claim that DNS has not confirmed
    # is `pending` and resolves to nothing at all.
    #
    # Behind the same guard `resolve_id` uses, so the two cannot disagree
    # about which hostnames are worth a lookup.
    org = None
    if custom_domains_possible(host):
        from . import domains

        org = domains.organization_for_host(host)

    if org is None:
        # The legacy single column, still honoured. Removing it outright
        # would break every deployment that already has one set, and those
        # were put there by an operator by hand -- see `tenancy.domains` for
        # why that is exactly the arrangement S9 replaces.
        org = Organization.objects.filter(domain=host).first()

    if org is None:
        slug = slug_from_host(host)
        if slug:
            org = Organization.objects.filter(slug=slug).first()

    cache.set(key, _encode(org, host) if org else _MISS, RESOLVE_TTL)
    return org


def _matches(organization, host):
    """Does ``organization`` currently answer to ``host``?

    Re-checked against the database rather than trusted from the cache, for
    the reason the block above records: a cache entry outlives the row it
    describes. With custom domains that matters more, not less -- a domain
    withdrawn or failed a moment ago must stop serving at once, and a cached
    "yes" would keep it answering for the rest of the TTL.
    """
    if organization.domain and normalise_host(organization.domain) == host:
        return True

    from .models import TenantDomain

    if TenantDomain.objects.filter(
            organization=organization, hostname=host,
            status__in=TenantDomain.SERVING_STATUSES).exists():
        return True

    slug = slug_from_host(host)
    return bool(slug) and slug == organization.slug


DEFAULT_KEY = "tenancy:default-org"


def default_organization(slug=""):
    """The organization an unresolved host falls back to, or None. Cached.

    This is the hot path for the whole of Phase S1: NIF is reached on an IP, on
    localhost and through Docker service names, none of which is a tenant
    subdomain, so nearly every real request lands here. Doing it uncached would
    put a query on every request that asks for a tenant.

    Returns None once more than one organization exists -- with two tenants
    there is no safe guess, and a wrong guess is a cross-tenant write. The
    ``_MISS`` sentinel is cached too, so "there is no single default" is also
    answered without a query.
    """
    key = f"{DEFAULT_KEY}:{slug}" if slug else DEFAULT_KEY
    cached = cache.get(key)
    if cached == _MISS:
        return None

    from .models import Organization

    if cached is not None:
        decoded = _decode(cached)
        organization = (Organization.objects.filter(pk=decoded[0]).first()
                        if decoded else None)
        if organization is not None:
            return organization
        cache.delete(key)

    if slug:
        organization = Organization.objects.filter(slug=slug).first()
    else:
        candidates = list(Organization.objects.all()[:2])
        organization = candidates[0] if len(candidates) == 1 else None

    cache.set(key, _encode(organization) if organization else _MISS, RESOLVE_TTL)
    return organization


def forget_default():
    """Drop the cached fallback. Called whenever an Organization is created."""
    cache.delete(DEFAULT_KEY)


def forget(organization):
    """Drop cached host mappings for an organization.

    Called whenever slug, domain or status changes. Clears the PREVIOUS slug and
    domain as well as the current ones -- ``Organization.from_db`` remembers
    what the database said, so a rename does not leave the old hostname
    resolving for another minute. ``resolve()`` re-validates anyway; this is the
    cheap path that makes a rename take effect immediately rather than on
    expiry.
    """
    base = tenant_base_domain()
    hosts = set()

    for domain in (organization.domain,
                   getattr(organization, "_loaded_domain", None)):
        if domain:
            hosts.add(normalise_host(domain))

    if base:
        for slug in (organization.slug,
                     getattr(organization, "_loaded_slug", None)):
            if slug:
                hosts.add(f"{slug}.{base}")

    # Every custom hostname this tenant has ever claimed, not only the ones
    # serving now (Phase S9). A domain that was just WITHDRAWN is exactly the
    # one whose cache entry has to go: leaving it would keep a hostname the
    # customer removed answering for the rest of the TTL.
    try:
        from .models import TenantDomain

        hosts.update(
            TenantDomain.objects
            .filter(organization=organization)
            .values_list("hostname", flat=True))
    except Exception:                              # noqa: BLE001
        # Called from `Organization.save()`, which runs during migrations
        # before this table exists. A cache miss is harmless; a failed save
        # is not.
        logger.debug("could not list custom domains while forgetting cache",
                     exc_info=True)

    for host in hosts:
        cache.delete(_cache_key(host))

    # The fallback caches an organization id too, and a rename or a new tenant
    # changes whether there IS a single default. Cheaper to drop it than to
    # reason about when it still holds.
    forget_default()
