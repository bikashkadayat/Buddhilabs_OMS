"""Resolve the tenant for every request, and put it in context.

INERT IN PHASE S1. With ``TENANCY_ENABLED = False`` this middleware:

  * makes the request's Organization available as ``request.organization``,
  * installs the tenant contextvar for the duration of the request,
  * falls back to the sole existing Organization when the host resolves to
    nothing,
  * and REFUSES NOTHING.

No 403 on a JWT/host mismatch, no 402 on an expired subscription, no rejection
of an unknown host. Those are Phase S3, and the code paths that will perform
them are written below as ``_enforce``, guarded by the flag, so the enforcement
logic is reviewable and tested now and inert until the isolation work it
depends on exists.

RESOLUTION IS LAZY, AND THAT IS NOT AN OPTIMISATION
---------------------------------------------------
Resolving eagerly here costs one query on EVERY request, including the ones
that never look at a tenant -- and in Phase S1 that is all of them, because no
business module consumes the tenant yet. The project's query-budget tests
(tasks/tests/test_performance.py, analytics/tests/test_performance.py) failed
on exactly that when this middleware first landed, and they were right to: a
per-request query on the hot path is a real regression, not a rounding error.

So the middleware installs a resolver and stops. Nothing is queried until
something asks. ``request.organization`` is a ``SimpleLazyObject`` for the same
reason ``request.user`` is, and the contextvar holds a deferred resolver (see
``tenancy.context._Holder``). The enforcement path forces resolution, which is
correct -- enforcing a boundary requires knowing which side you are on.

WHY THE FALLBACK IS SAFE, AND WHY IT IS TEMPORARY
-------------------------------------------------
NIF is reached today on a bare IP, on localhost, through Docker service names,
and on its own hostname. None of those is ``nif.<base domain>``, so host
resolution would miss on nearly every real request. Falling back to the only
organization that exists is therefore correct for a single-tenant deployment --
and it refuses to guess the moment a second organization exists, which is
exactly when guessing would start leaking.
"""
import logging

from django.conf import settings
from django.utils.functional import SimpleLazyObject

from . import context, resolver

logger = logging.getLogger(__name__)

# Paths that must answer before a tenant is admitted: health checks, the
# pre-login branding lookup, and the billing surface a suspended tenant needs
# in order to stop being suspended.
EXEMPT_PREFIXES = (
    "/api/v1/health",
    "/api/v1/tenant/public",
    # The platform console. Exempt because it has no tenant to resolve: it is
    # served on a platform host, where host resolution deliberately returns
    # None, and _enforce would otherwise answer 404 "Unknown workspace" to
    # every console request. Its own authority check is IsPlatformStaff plus
    # console.require_platform -- see tenancy/views.py.
    "/api/v1/platform/",
    "/api/platform/",
    "/static/",
    "/admin/",
)


class TenantResolutionMiddleware:
    """Attach ``request.organization`` and maintain the tenant contextvar.

    Stateless. Nothing is stored on ``self`` between requests -- a middleware
    instance is shared by every request the process serves (and, under ASGI, by
    concurrent ones), so per-request state on the instance is a data race with
    a tenant id in it.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = self._host(request)

        # TWO resolvers, sharing one cache (Phase S4).
        #
        # `resolve_id` returns only the organization ID and needs NO row fetch
        # on a warm cache. That is what the tenant contextvar wants, and since
        # Phase S4 made TenantManager the default manager on 55 models, the
        # contextvar is now read on essentially every request -- so its cost is
        # the per-request cost of tenant-aware querying. It has to be zero, and
        # it is.
        #
        # `resolve_org` loads the row, and is only called if something actually
        # asks for `request.organization` (branding, the subscription gate).
        resolve_id = _memoised(lambda: self._resolve_id(host))
        resolve_org = _memoised(lambda: self._resolve(host))

        # Lazy, like request.user: costs nothing unless something asks.
        request.organization = SimpleLazyObject(resolve_org)
        token = context.set_current_org_lazy(resolve_id)
        try:
            # Phase S5: bind the tenant INSIDE THE DATABASE for this request.
            #
            # This is what turns the contextvar above -- which only the ORM
            # consults -- into a boundary the database itself enforces. After
            # this, a raw cursor.execute() is scoped too.
            #
            # THE MIDDLEWARE OPENS THE TRANSACTION ITSELF (Phase S6), and it
            # has to. `SET LOCAL` is transaction-scoped, which is the entire
            # safety argument for pooled connections -- the value is discarded
            # at COMMIT or ROLLBACK, so it cannot be inherited by the next
            # request that borrows the connection. Phase S5 relied on
            # ATOMIC_REQUESTS to supply that transaction, and ATOMIC_REQUESTS
            # wraps the VIEW, not the middleware stack
            # (BaseHandler.make_view_atomic). So the `SET LOCAL` ran in
            # autocommit, where PostgreSQL warns and discards it: the binding
            # silently did nothing, every tenant query would have returned
            # zero rows, and the application would have been inert the moment
            # RLS was switched on in production.
            #
            # Nothing caught it because the conformance gate bound the tenant
            # directly rather than through a request. The gate now has a test
            # that drives the middleware and asserts the GUC afterwards.
            #
            # Wrapping here rather than widening ATOMIC_REQUESTS also means
            # every later middleware runs inside the scoped transaction, which
            # is what we want: a query from one of those must be scoped too.
            with self._request_transaction():
                self._bind_database_tenant(resolve_id, host)
                if settings.TENANCY_ENABLED:
                    refusal = self._enforce(request, resolve_org())
                    if refusal is not None:
                        return refusal
                return self.get_response(request)
        finally:
            # Always reset. A contextvar left set would be inherited by the
            # next request served on this thread, which for a tenant id is the
            # whole failure mode this design exists to prevent.
            context.reset_current_org(token)

    # -- database binding (Phase S5) -------------------------------------
    @staticmethod
    def _request_transaction():
        """A transaction for ``SET LOCAL`` to be local to, or a no-op.

        Only when RLS is enforced. With it off there is nothing to bind, and
        wrapping every request in a transaction would be a behaviour change
        nobody asked for -- notably it would make ``transaction.on_commit``
        callbacks fire at the end of the request instead of immediately.
        """
        import contextlib

        from django.db import transaction

        from . import rls

        if not rls.is_enabled():
            return contextlib.nullcontext()
        return transaction.atomic()

    @staticmethod
    def _bind_database_tenant(resolve_id, host):
        """Set ``app.current_org`` and ``app.platform`` for this transaction.

        Deliberately BEFORE the view runs and never after: a view that queries
        before the binding would be unscoped, which is the one ordering mistake
        that would make RLS look like it was working while it was not.

        TWO GUCS, AND THE SECOND IS SET ON EVERY REQUEST (Phase S6).
        ``app.platform`` is 'on' only for a request that arrived on a platform
        host, and 'off' -- explicitly, not merely unset -- for every tenant
        request. It unlocks exactly one thing: rows that belong to no tenant,
        which is how a platform operator's own User row (organization IS NULL)
        becomes readable by the role that has to authenticate it.

        Setting 'off' explicitly is the point. A stale 'on' inherited from a
        pooled connection would let a tenant request read every platform
        account, so the value is written rather than assumed on every single
        request.

        Failures are logged, not raised, and they fail CLOSED in both
        directions: an unbound tenant sees no tenant rows, and an unset
        platform flag sees no unowned rows either.
        """
        from . import rls

        if not rls.is_enabled():
            return
        try:
            # ONE round trip for both, not two. See rls.bind: a `SET` cannot
            # be parameterised or batched, so the naive version added two
            # round trips to every request -- which the middleware's own
            # query-budget test caught.
            rls.bind(resolve_id(), platform=resolver.is_platform_host(host))
        except Exception:                      # pragma: no cover - defensive
            logger.exception("could not bind app.current_org; this request "
                             "will see no tenant-scoped rows")

    # -- resolution ------------------------------------------------------
    @staticmethod
    def _host(request):
        try:
            return resolver.normalise_host(request.get_host())
        except Exception:                      # pragma: no cover - defensive
            # get_host() raises DisallowedHost on a bad Host header. That is
            # CommonMiddleware's business to answer, not a reason for tenant
            # resolution to crash the request first.
            return ""

    def _resolve_id(self, host):
        """The tenant ID for this request. No row fetch on a warm cache."""
        if resolver.is_platform_host(host):
            return None
        try:
            org_id = resolver.resolve_id(host)
        except Exception:                      # pragma: no cover - defensive
            logger.warning("tenant id resolution failed for host %r", host,
                           exc_info=True)
            return None
        if org_id is not None:
            return org_id
        if settings.TENANCY_ENABLED:
            return None
        try:
            return resolver.default_organization_id(
                getattr(settings, "TENANCY_DEFAULT_SLUG", ""))
        except Exception:                      # pragma: no cover - defensive
            return None

    def _resolve(self, host):
        if resolver.is_platform_host(host):
            return None

        try:
            organization = resolver.resolve(host)
        except Exception:                      # pragma: no cover - defensive
            # Resolution must never be the reason a request 500s. In the
            # foundation phase a failure here falls through to the
            # single-tenant default, which is the behaviour that exists today.
            logger.warning("tenant resolution failed for host %r", host,
                           exc_info=True)
            organization = None

        return organization if organization is not None \
            else self._default_organization()

    @staticmethod
    def _default_organization():
        """The single-tenant fallback, or None once enforcement is on.

        The lookup itself (and its caching) lives in
        ``tenancy.resolver.default_organization``; this only decides whether a
        fallback is permitted at all.
        """
        if settings.TENANCY_ENABLED:
            return None
        try:
            return resolver.default_organization(
                getattr(settings, "TENANCY_DEFAULT_SLUG", ""))
        except Exception:                      # pragma: no cover - defensive
            # The table may not exist yet (first migrate, or a check run before
            # migrations). Degrade to "no tenant" rather than crashing boot.
            return None

    # -- enforcement (Phase S3; unreachable while TENANCY_ENABLED is False) --
    def _enforce(self, request, organization):
        """Refuse a request that does not belong to the resolved tenant.

        Written now, switched on later. Returns an HttpResponse to refuse with,
        or None to proceed.
        """
        from django.http import JsonResponse

        if any(request.path.startswith(prefix) for prefix in EXEMPT_PREFIXES):
            return None

        # A PLATFORM HOST IS NOT A WORKSPACE (Phase S6), so workspace
        # enforcement does not apply to it. Without this, a platform operator
        # could not even sign in once TENANCY_ENABLED was on: host resolution
        # on a platform host deliberately returns None, and the next line
        # answers 404 "Unknown workspace" to /api/v1/auth/login/.
        #
        # Nothing is granted here. Every console endpoint is still guarded by
        # IsPlatformStaff and by console.require_platform, and the database
        # still shows this connection only rows that belong to no tenant.
        if resolver.is_platform_host(self._host(request)):
            return None

        if organization is None:
            # 404, never 403: a 403 would confirm the workspace exists.
            return JsonResponse({"detail": "Unknown workspace."}, status=404)

        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            # A platform account has no tenant workspace to be in.
            if getattr(user, "is_platform_staff", False):
                return JsonResponse(
                    {"detail": "Platform accounts cannot access a tenant "
                               "workspace."}, status=403)
            # The authoritative tenant is the USER's, never the URL's. A valid
            # token replayed against another tenant's subdomain is refused here.
            if (user.organization_id is not None
                    and user.organization_id != organization.pk):
                logger.warning(
                    "cross-tenant request refused: user %s (org %s) on org %s",
                    user.pk, user.organization_id, organization.pk)
                return JsonResponse(
                    {"detail": "This account does not belong to this "
                               "workspace."}, status=403)

        if not organization.is_admitted:
            return JsonResponse(
                {"detail": "This workspace is not active. Please contact your "
                           "administrator."}, status=402)
        return None


def _memoised(function):
    """Call ``function`` at most once, then return the same answer.

    Shared by ``request.organization`` and the contextvar so that the two
    cannot resolve independently and double the cost.
    """
    sentinel = object()
    cell = {"value": sentinel}

    def wrapper():
        if cell["value"] is sentinel:
            cell["value"] = function()
        return cell["value"]

    return wrapper
