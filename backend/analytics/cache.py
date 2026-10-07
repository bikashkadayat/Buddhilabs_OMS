"""Analytics caching.

Runs on ``config.cache.ResilientRedisCache``, which degrades to a miss rather
than raising, so losing Redis makes analytics slower and never broken. With
``REDIS_URL`` unset -- dev, CI, pytest -- Django falls back to LocMemCache and
everything below still works, just per-process.

Three ideas do the work:

1. **Scope, not user, in the key.** Every department head in a department shares
   one entry; HR and Admin share the organisation entry. Keying by user id would
   give a cache that never hits.
2. **TTL by recency.** A window ending today is worth five minutes; a closed
   month is immutable and worth a day. Executives do not need sub-five-minute
   figures -- this phase is explicitly not transaction processing.
3. **A generation counter for history.** Ordinary punches do NOT invalidate
   anything (they ride the TTL). Only writes that change the PAST -- an applied
   correction, a backdated edit, a leave approval -- bump ``analytics:gen``,
   which is part of every key and therefore retires every closed-period entry at
   once.
"""
import hashlib
import logging

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

# Bump when a FORMULA changes, so a deploy can never serve yesterday's
# definition of a KPI out of a cache that still looks warm.
CODE_VERSION = "1"

# PER TENANT (Phase S3). This was the single string "analytics:gen", so one
# tenant applying a backdated correction retired EVERY tenant's cached
# analytics -- shared invalidation, which is both a correctness smell and a
# free cross-tenant denial of service. Each organization now owns its counter.
def generation_key(organization=None):
    from tenancy.keys import tenant_key

    return tenant_key("analytics", "gen", organization=organization)


GENERATION_TTL = None  # never expires on its own

# Seconds.
TTL_LIVE = 300        # window includes today
TTL_CLOSED = 86_400   # window ended in the past
TTL_DEVICES = 60      # infrastructure health decays fast and HR acts on it
TTL_META = 3_600


def generation(organization=None):
    """This tenant's cache generation. A cache outage returns 0 -- consistent,
    and the worst case is that a warm entry is reused for one TTL."""
    value = cache.get(generation_key(organization))
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def bump_generation(reason="", organization=None):
    """Retire this tenant's cached analytics payloads, and only this tenant's.

    ``organization=None`` means "the tenant in context", which is what a signal
    fired inside a request wants. With no context at all -- a management
    command, a nightly job -- there is no single answer, so every organization
    is bumped individually rather than one shared counter being moved. That is
    the difference between a platform-wide flush (a deliberate operator action)
    and one tenant's write silently invalidating everybody else's cache.

    Uses ``incr`` where possible and falls back to a set, because ``incr``
    raises on a missing key in Django's contract (the resilient backend turns
    that into None rather than an exception).
    """
    if organization is None:
        from tenancy.scoping import active_organization

        organization = active_organization(required=False)
        if organization is None:
            return _bump_every_tenant(reason)

    return _bump_one(organization, reason)


def _bump_one(organization, reason=""):
    key = generation_key(organization)
    current = cache.get(key)
    if current is None:
        cache.set(key, 1, GENERATION_TTL)
        new = 1
    else:
        new = cache.incr(key, 1)
        if new is None:  # cache degraded mid-call
            new = generation(organization)
    logger.debug("analytics cache generation for %s -> %s (%s)",
                 getattr(organization, "slug", organization), new,
                 reason or "unspecified")
    return new


def _bump_every_tenant(reason=""):
    """Flush every tenant's analytics cache, one counter at a time.

    Only reached when there is no tenant in context, i.e. a deliberate
    platform-wide action. Returns the number of organizations retired.
    """
    from tenancy.models import Organization

    count = 0
    for organization in Organization.objects.all().only("id", "slug"):
        _bump_one(organization, reason)
        count += 1
    logger.info("analytics cache flushed for %s organization(s) (%s)",
                count, reason or "unspecified")
    return count


def build_key(endpoint, scope_token, param_token, organization=None):
    """The cache key for one payload.

    The tenant appears TWICE on purpose: once inside ``scope_token`` (see
    analytics.scope.Scope.cache_token) and once in the generation counter this
    key reads. Either alone would isolate the entry; both means a mistake in
    one place does not silently reopen the hole.
    """
    from tenancy.keys import current_token, token_for

    token = token_for(organization) if organization is not None else current_token()
    digest = hashlib.sha1(
        f"{token}|{scope_token}|{param_token}".encode("utf-8")).hexdigest()[:16]
    return (f"analytics:v{CODE_VERSION}:t{token}:"
            f"g{generation(organization)}:{endpoint}:{digest}")


def ttl_for(window, endpoint=None, today=None):
    if endpoint == "devices":
        return TTL_DEVICES
    if endpoint == "meta":
        return TTL_META
    today = today or timezone.localdate()
    return TTL_LIVE if window is None or window.end >= today else TTL_CLOSED


def get_or_build(endpoint, scope, window, builder, *, extra_token="", today=None,
                 organization=None):
    """Return a cached payload or build, store and return a fresh one.

    The returned tuple is ``(payload, cached)`` so the view can stamp
    ``cached`` and ``generated_at`` onto the envelope. A dashboard number with
    no "as of" line is a number that gets argued about.

    THE TENANT COMES FROM THE SCOPE, NOT FROM CONTEXT, AND THAT IS A
    PERFORMANCE DECISION. ``Scope.organization_token`` is derived from
    ``request.user.organization_id`` -- a column on a row the authentication
    layer has already loaded -- so threading it through here costs NOTHING.
    Resolving it from the tenant contextvar instead would add a query to every
    analytics request, which the project's query-budget tests
    (analytics/tests/test_performance.py) correctly refuse.
    """
    scope_token = scope.cache_token() if scope is not None else "-"
    param_token = f"{window.cache_token() if window else '-'}|{extra_token}"
    if organization is None and scope is not None:
        organization = scope.organization_token or None
    key = build_key(endpoint, scope_token, param_token, organization=organization)

    hit = cache.get(key)
    if hit is not None:
        return hit, True

    payload = builder()
    cache.set(key, payload, ttl_for(window, endpoint, today))
    return payload, False


def invalidate_all(reason="", organization=None):
    """Public alias used by the signals module and by management commands.

    Despite the name this retires ONE tenant's payloads when a tenant is in
    context. "All" means all of that tenant's endpoints and periods, not all
    tenants -- see bump_generation.
    """
    return bump_generation(reason, organization=organization)
