"""Tenant namespacing for everything that is NOT a database row.

WHY THIS MODULE EXISTS
----------------------
Phase S2 added ``organization_id`` to 28 tables, and that closes exactly the
leaks a foreign key can close. It closes none of these:

  * a cache entry keyed by something two tenants can both produce,
  * a WebSocket group every tenant's dashboard joins,
  * a login-lockout counter keyed on a bare email address,
  * a file path with no owner in it.

Each of those is a cross-tenant path with no bad query anywhere in it, which is
why no amount of queryset discipline reaches them. They all need the same
thing -- a tenant in the key -- so the key-building lives here, once, rather
than being reinvented per module.

THE TOKEN IS SHORT AND STABLE
-----------------------------
A UUID is 36 characters, and Channels caps a group name at 100. The token is
therefore the UUID's hex digits with no dashes (32 chars), which is still
collision-free and leaves room for a prefix and a suffix.

``NO_TENANT`` is a deliberate sentinel rather than an empty string: "this key
belongs to no tenant" and "somebody forgot to pass the tenant" must not produce
the same key, because the second one silently pools two tenants into one entry.
"""
import logging

logger = logging.getLogger(__name__)

# Keys and group names for genuinely platform-wide things (the platform
# console's own counters, a health probe). Visibly not a tenant id.
NO_TENANT = "_platform"

# What a key gets when a tenant could not be resolved AT ALL. Distinct from
# NO_TENANT so the two cannot be confused in a Redis dump, and logged every
# time, because in Phase S3 it should never happen: the compatibility shim
# resolves the single organization while TENANCY_ENABLED is False.
UNRESOLVED = "_unresolved"


def token_for(org_or_id):
    """A short, stable, group-name-safe token for one organization."""
    if org_or_id is None:
        return UNRESOLVED
    value = getattr(org_or_id, "pk", org_or_id)
    return str(value).replace("-", "")


def current_token(*, required=False):
    """The token for the tenant in context.

    ``required=False`` returns ``UNRESOLVED`` rather than raising, because a
    cache key is not worth failing a request over -- and an unresolved token
    still produces an ISOLATED key, just an unhelpfully named one. It is logged
    so the condition is visible rather than silent.
    """
    from .scoping import active_organization_id

    org_id = active_organization_id(required=required)
    if org_id is None:
        logger.warning("tenant cache key built with no organization in context",
                       stack_info=False)
        return UNRESOLVED
    return token_for(org_id)


def tenant_key(*parts, organization=None, separator=":"):
    """``t:<token>:<part>:<part>`` -- a cache key nobody else can collide with.

    Pass ``organization`` when it is in hand; otherwise the tenant comes from
    context. The ``t:`` prefix makes a tenant-scoped key obvious at a glance in
    a Redis keyspace dump, and greppable in this codebase.
    """
    token = token_for(organization) if organization is not None else current_token()
    return separator.join(("t", token, *(str(p) for p in parts)))


def platform_key(*parts, separator=":"):
    """A key that is deliberately NOT tenant-scoped.

    Explicit and greppable, so "this is shared across tenants" is a decision
    somebody made rather than a tenant id somebody forgot.
    """
    return separator.join(("t", NO_TENANT, *(str(p) for p in parts)))


def group_name(*parts, organization=None):
    """A Channels group name, tenant-scoped.

    Channels permits ASCII alphanumerics, hyphens, underscores and periods, and
    caps the name at 100 characters. Parts are joined with periods to match the
    convention already used by biometric.events, and the result is asserted
    short enough -- a silently truncated group name would mean a subscriber
    joining a DIFFERENT group from the one the publisher sends to, which fails
    as "the dashboard just never updates".
    """
    token = token_for(organization) if organization is not None else current_token()
    name = ".".join((str(parts[0]), token, *(str(p) for p in parts[1:])))
    if len(name) > 100:                        # pragma: no cover - guard
        raise ValueError(
            f"Channels group name is {len(name)} characters, over the 100 "
            f"character limit: {name!r}")
    return name
