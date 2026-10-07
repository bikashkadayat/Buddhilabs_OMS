"""The current organization, per request / per task.

WHY A CONTEXTVAR AND NOT A THREAD-LOCAL
---------------------------------------
This project serves WebSockets through Channels (``biometric/consumers.py`` is
an ``AsyncJsonWebsocketConsumer``). A thread-local set in an async consumer
either leaks into the next coroutine scheduled on that thread or vanishes
across an ``await`` -- both of which, for a tenant id, mean serving one
customer's data to another. ``contextvars`` is the only primitive that is
correct under both WSGI threads and asyncio tasks.

THE ZERO VALUE IS "UNSET", NOT "ALL"
------------------------------------
``current_org_id()`` returns None when nothing has been set. Callers that need
a tenant must use ``require_org_id()``, which raises. Nothing in this module
ever falls back to "every organization" -- a fallback is how a cron job with no
request ends up writing rows stamped with the wrong owner.

The one deliberate exception lives in ``tenancy.middleware``, which during the
foundation phase (``TENANCY_ENABLED = False``) resolves to the sole existing
organization. That is a migration affordance with a flag on it, not a default.
"""
import contextlib
import contextvars
import logging

from .exceptions import TenantScopeMissing

logger = logging.getLogger(__name__)

def coerce_org_id(value):
    """Normalise an organization id to ``uuid.UUID``.

    THE TYPE MATTERS, AND IT IS NOT COSMETIC.

    ``Organization.pk`` is a ``UUIDField``, so a model's ``organization_id`` is
    a ``uuid.UUID``. Phase S4 added an id-only resolver hot path
    (``resolver.resolve_id``) that reads the id out of the cache, where it is a
    STRING -- and ``uuid.UUID("...") != "..."`` in Python. So any comparison
    between the tenant in context and a row's owner would have been silently
    False for the SAME tenant.

    That is not a hypothetical: ``TenantScopedModel.save()`` raises
    ``CrossTenantWrite`` on exactly that comparison, which would have turned
    "this row belongs to the current tenant" into a refusal to save.

    Normalising here, at the one place the context value is produced, fixes
    every consumer at once. A value that is not a UUID at all (a slug, a
    sentinel) is returned unchanged rather than raising -- the caller's own
    lookup will fail more informatively than a parse error here would.
    """
    import uuid

    if value is None or value is NO_TENANT or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return value


class _Holder:
    """Either a known organization id, or a way to find one when first asked.

    RESOLUTION IS LAZY, AND THAT IS A PERFORMANCE DECISION WITH TEETH.
    Resolving the tenant eagerly in middleware costs one query on EVERY
    request, including the ones that never look at a tenant. The project has
    query-budget tests (tasks/tests/test_performance.py,
    analytics/tests/test_performance.py) that caught exactly that and were
    right to: a per-request query on the hot path is a real regression, and the
    correct answer is to not make it until something actually asks.

    So the middleware installs a resolver and nothing more. A request that
    touches no tenant data pays nothing; the first caller that asks triggers
    one lookup, and the answer is memoised for the rest of the request.
    Django's own ``request.user`` works the same way, for the same reason.
    """

    __slots__ = ("_resolver", "_resolved", "_value")

    def __init__(self, *, value=None, resolver=None):
        self._resolver = resolver
        self._resolved = resolver is None
        # Coerced on BOTH paths. `tenant_context("<uuid string>")` is a real
        # caller -- the resolver's cheap hot path returns a string -- and
        # leaving it uncoerced here would reintroduce the type mismatch that
        # `coerce_org_id` exists to prevent.
        self._value = coerce_org_id(value)

    @property
    def org_id(self):
        if not self._resolved:
            organization = self._resolver()
            self._value = coerce_org_id(
                getattr(organization, "pk", organization))
            self._resolved = True
        return None if self._value is NO_TENANT else self._value

    @property
    def is_explicitly_unset(self):
        """True only inside ``no_tenant()``."""
        return self._resolved and self._value is NO_TENANT


# Holds a _Holder, not a bare id: see the class docstring for why resolution is
# deferred. The id inside is immutable, cheap to copy into a new asyncio task,
# and cannot carry a stale related-object cache across a request boundary.
_current_org_id = contextvars.ContextVar("tenancy_current_org", default=None)


def current_org_id():
    """The organization id in context, or None if none has been set.

    Triggers resolution on first call within a request, then memoises.
    """
    holder = _current_org_id.get()
    return holder.org_id if holder is not None else None


def tenant_explicitly_unset():
    """True inside ``no_tenant()``: "platform-wide" rather than "not set".

    Callers that have a single-tenant fallback must check this, or that
    fallback will quietly re-scope a block that was opened precisely to escape
    it. See ``_NoTenant``.
    """
    holder = _current_org_id.get()
    return bool(holder is not None and holder.is_explicitly_unset)


def require_org_id():
    """The organization id in context. Raises if unset.

    Use this wherever proceeding without a tenant would be a silent correctness
    bug rather than a recoverable condition.
    """
    org_id = _current_org_id.get()
    if org_id is None:
        raise TenantScopeMissing(
            "No organization in context. Wrap this call in "
            "tenancy.context.tenant_context(org), or run it inside a request "
            "handled by TenantResolutionMiddleware.")
    return org_id


def current_organization():
    """The Organization in context, or None. Hits the database."""
    org_id = current_org_id()
    if org_id is None:
        return None
    from .models import Organization

    return Organization.objects.filter(pk=org_id).first()


def set_current_org(org_or_id):
    """Set the context directly and return the token needed to restore it.

    Prefer ``tenant_context`` -- it cannot leak. This exists for middleware,
    which sets on the way in and resets on the way out across two methods.
    """
    org_id = getattr(org_or_id, "pk", org_or_id)
    return _current_org_id.set(_Holder(value=org_id))


def set_current_org_lazy(resolver):
    """Install a callable that will be asked for the organization on first use.

    ``resolver`` is called at most once per context and may return an
    Organization, an id, or None. Used by the middleware so that a request
    which never touches tenant data costs no query at all.
    """
    return _current_org_id.set(_Holder(resolver=resolver))


def reset_current_org(token):
    """Restore whatever was in context before the matching ``set_current_org``."""
    _current_org_id.reset(token)


@contextlib.contextmanager
def tenant_context(org_or_id):
    """Run a block as one organization, in the ORM **and in the database**.

        with tenant_context(org):
            ...

    Required by every management command, cron job and migration that touches
    tenant data, because none of those has a request to resolve from. The reset
    is in a ``finally``, so an exception inside the block cannot leave the
    tenant id set for whatever runs next on this thread or task.

    IT ALSO BINDS ``app.current_org`` WHEN RLS IS ON (Phase S5), and that is
    not a convenience. Once row-level security is enforced, "this work is for
    tenant X" has to mean the same thing to the database as it does to the
    ORM -- otherwise a management command that enters a tenant context would
    read zero rows, and a write would be refused by the policy's WITH CHECK
    for a tenant it is legitimately acting as. The previous binding is restored
    on the way out, so nesting works.
    """
    token = set_current_org(org_or_id)
    previous = _bind_database(current_org_id())
    # Entering a tenant means leaving platform scope (Phase S6). The console
    # nests this inside `no_tenant()` constantly -- provisioning runs the
    # bootstrap as the new tenant -- and leaving platform scope on would mean
    # unowned rows stayed writable throughout. Nothing relies on that, so it
    # is withdrawn and restored rather than left for later to depend on.
    previous_platform = _bind_platform_mode(False)
    try:
        yield current_org_id()
    finally:
        _current_org_id.reset(token)
        if previous_platform is not _UNBOUND:
            _bind_platform_mode(previous_platform)
        if previous is not _UNBOUND:
            _bind_database(previous)


# Sentinel: "the database was not bound, so there is nothing to restore".
_UNBOUND = object()


class _NoTenant:
    """Marker for "deliberately NO tenant", as distinct from "not set yet".

    THE DISTINCTION IS LOAD-BEARING (Phase S5). Before this, `no_tenant()` put
    None in the contextvar -- which is exactly what an unset context looks
    like. So `active_organization()` could not tell them apart, applied its
    single-tenant fallback, and handed back NIF inside a block whose entire
    purpose was to be platform-wide. The platform console would have been
    silently scoped to one customer, and `resolve_for_audit` would have filed
    an anonymous failed-login probe in that customer's audit trail.
    """

    __slots__ = ()

    def __repr__(self):                        # pragma: no cover - debugging
        return "<no tenant>"


NO_TENANT = _NoTenant()


def _bind_database(org_id):
    """Bind ``app.current_org``, returning the previous value to restore.

    Returns ``_UNBOUND`` when RLS is off or the backend is not PostgreSQL, so
    the caller knows to skip the restore entirely rather than issuing a
    pointless round trip.
    """
    from . import rls

    if not rls.is_enabled():
        return _UNBOUND
    try:
        previous = rls.current_org()
        rls.set_current_org(org_id, local=False)
        return previous
    except Exception:  # noqa: BLE001 - never let a GUC write break the caller
        logger.warning("could not bind app.current_org for tenant_context",
                       exc_info=True)
        return _UNBOUND


def _bind_platform_mode(enabled):
    """Declare platform scope in the database, returning the previous value.

    Mirrors ``_bind_database``: ``_UNBOUND`` means "RLS is off, skip the
    restore".
    """
    from . import rls

    if not rls.is_enabled():
        return _UNBOUND
    try:
        previous = rls.platform_mode()
        rls.set_platform_mode(enabled, local=False)
        return previous
    except Exception:  # noqa: BLE001 - never let a GUC write break the caller
        logger.warning("could not bind app.platform for no_tenant",
                       exc_info=True)
        return _UNBOUND


@contextlib.contextmanager
def no_tenant():
    """Run a block with NO organization in context -- PLATFORM scope.

    For deliberate platform-wide work (the platform console, nightly usage
    snapshots). Explicit and greppable, so "this query crosses tenants" is a
    visible decision rather than an accident of an unset variable.

    IT ALSO DECLARES PLATFORM SCOPE TO THE DATABASE (Phase S6). Under
    row-level security, that unlocks exactly one thing: rows belonging to no
    tenant. The case that forced it is a platform operator's own User row --
    ``organization IS NULL`` by constraint, and therefore invisible to a
    policy that tests ``organization_id = <tenant>``. Without this, creating
    or authenticating a platform account was impossible with RLS on.

    It does NOT widen access to tenant data by one row. ``app.current_org`` is
    cleared here, so every tenant-owned row stays invisible; a console that
    forgets ``all_tenants`` still gets nothing. See ``tenancy.rls.PLATFORM_GUC``.
    """
    token = _current_org_id.set(_Holder(value=NO_TENANT))
    previous = _bind_database(None)
    previous_platform = _bind_platform_mode(True)
    try:
        yield
    finally:
        _current_org_id.reset(token)
        if previous_platform is not _UNBOUND:
            _bind_platform_mode(previous_platform)
        if previous is not _UNBOUND:
            _bind_database(previous)


# ---------------------------------------------------------------------------
# Migrations are not tenant-scoped work (Phase S6)
# ---------------------------------------------------------------------------
#
# A data migration reads and rewrites the WHOLE database by definition -- that
# is what distinguishes it from application code -- and it runs as the
# migration role, which bypasses row-level security for exactly the same
# reason. So tenant enforcement has to stand down for the duration of a
# migration run, or `manage.py migrate` cannot complete with
# TENANCY_ENABLED=1.
#
# That is not hypothetical. `leaves/0014_backfill_categories` reads the LIVE
# User model (its own docstring explains why it cannot declare a later `users`
# dependency without making Django reject every production database that has
# already applied it). With enforcement on and no tenant in context, that read
# raised TenantScopeMissing and migrate died a third of the way through --
# so a deployment could not be brought up at all with the flag the whole SaaS
# launch depends on.
#
# WHY A MODULE-LEVEL FLAG AND NOT A CONTEXTVAR: Django emits `pre_migrate` and
# `post_migrate` ONCE each around the entire run (see
# django.core.management.commands.migrate), and migrations are single
# threaded. A contextvar would not survive the span between two separate
# signal handlers.
_MIGRATING = False


def migrations_running():
    """True while `manage.py migrate` is executing."""
    return _MIGRATING


def _enter_migrations(**kwargs):
    global _MIGRATING
    _MIGRATING = True


def _exit_migrations(**kwargs):
    global _MIGRATING
    _MIGRATING = False


def connect_migration_signals():
    """Attach the pre/post_migrate pair. Called from TenancyConfig.ready().

    `post_migrate` also fires after a test-database flush with no matching
    `pre_migrate`; clearing an already-clear flag is harmless, and leaving it
    SET would be the dangerous direction, so the clear is the one that must
    never be missed.
    """
    from django.db.models.signals import post_migrate, pre_migrate

    pre_migrate.connect(_enter_migrations,
                        dispatch_uid="tenancy.migrations.enter")
    post_migrate.connect(_exit_migrations,
                         dispatch_uid="tenancy.migrations.exit")
