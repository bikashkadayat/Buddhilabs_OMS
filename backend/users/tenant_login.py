"""Tenant-aware credential lookup.

THE DEFECT THIS CLOSES
----------------------
Login resolved a user with::

    User.objects.get(email=email)

and ``email`` had no unique constraint at all (``EmailField(blank=True)``, since
users/0001). Two users sharing an address therefore raised
``MultipleObjectsReturned`` -- an uncaught 500 on the single most important
endpoint in the system. That was already a latent bug with one tenant; with two
tenants it is a certainty, because "admin@company.com" is exactly the address
every new customer's first account uses.

THE STRATEGY: EMAIL + TENANT, WHERE THE TENANT COMES FROM THE HOST
------------------------------------------------------------------
Three options were on the table (see the phase report). This module implements
**email scoped by the organization resolved from the request**, because:

* the credential a person types stays the one they already know -- no "which
  company?" dropdown, no synthetic ``email@slug`` logins, no change for NIF;
* the tenant is taken from the HOST, which is infrastructure the user cannot
  choose by typing something different into a form; and
* it is backed by a database constraint rather than by care. With
  ``uniq_user_org_email_ci`` in place, at most one row can match
  ``(organization, lower(email))``, so the ambiguity that produced the 500 is
  not merely handled -- it cannot exist.

The lookup still uses ``.first()`` rather than ``.get()``. That is belt and
braces on purpose: a constraint that is correct today should not be the only
thing standing between a bad migration and a 500 on the login endpoint.

PLATFORM ACCOUNTS
-----------------
A platform operator has ``organization IS NULL``, so a tenant-scoped lookup can
never return one -- which is the point. They are matched only when the request
arrives on a platform console host. If no console host is configured
(``TENANCY_PLATFORM_HOSTS`` empty, which is the default and the single-host
deployment NIF runs today), the console necessarily shares the host, and
platform accounts are matched there as well.
"""
import logging

from django.contrib.auth import get_user_model

from tenancy import resolver

logger = logging.getLogger(__name__)


def organization_for(request):
    """The organization a login attempt is scoped to, or None.

    ``TenantResolutionMiddleware`` has already resolved this and attached it;
    reading it back costs nothing and keeps one resolution per request. The
    fallback is for callers with no middleware in front of them (the Django
    admin login form, a management command, a test calling the backend
    directly).
    """
    organization = getattr(request, "organization", None) if request else None
    if organization is not None and bool(organization):
        return organization
    if request is None:
        return None
    try:
        return resolver.resolve(resolver.normalise_host(request.get_host()))
    except Exception:                          # pragma: no cover - defensive
        return None


def platform_login_allowed(request):
    """May a PLATFORM account authenticate on this request's host?

    True on a configured console host, and true everywhere when no console host
    is configured at all -- in that deployment there is only one hostname, so
    refusing platform logins on it would lock the console out entirely.
    """
    if not resolver.platform_hosts():
        return True
    if request is None:
        return False
    try:
        return resolver.is_platform_host(request.get_host())
    except Exception:                          # pragma: no cover - defensive
        return False


def find_by_email(email, *, request=None, organization=None):
    """The one user this (email, tenant) pair identifies, or None.

    Never raises ``MultipleObjectsReturned``: the query is scoped to one
    organization and ordered, and the database constraint guarantees at most
    one match in the first place.
    """
    email = (email or "").strip()
    if not email:
        return None

    User = get_user_model()

    # `all_tenants`, NOT `objects` (Phase S5).
    #
    # Phase S5 made User.objects tenant-scoped, which is what closes R4 -- but
    # login runs BEFORE a user is known, and this function has to be able to
    # find two different kinds of account:
    #
    #   * a TENANT user, which the explicit `.filter(organization=...)` below
    #     scopes just as tightly as the manager would have; and
    #   * a PLATFORM user, which has organization IS NULL and therefore cannot
    #     be found by a manager that filters on a tenant at all. With
    #     `objects`, the platform branch below resolved
    #     `organization__isnull=True AND organization_id = <tenant>`, which is
    #     always empty -- platform sign-in broke silently, caught by
    #     tenancy/tests/test_tenant_login.py.
    #
    # The scoping is not lost, it is moved: every branch below names the
    # organization it wants, and under PostgreSQL RLS the database enforces it
    # a second time regardless of which manager was used.
    base = User.all_tenants.select_related(
        "department_ref", "organization").filter(email__iexact=email)

    if organization is None:
        organization = organization_for(request)

    if organization is not None:
        user = base.filter(organization=organization).order_by("date_joined").first()
        if user is not None:
            return user

    if platform_login_allowed(request):
        # PLATFORM SCOPE, DECLARED (Phase S6). A platform account has
        # organization IS NULL, and under row-level security a NULL never
        # matches `organization_id = <tenant>` -- so without this the lookup
        # finds nothing and nobody can sign in to the console. `no_tenant()`
        # is what tells the database this connection is serving the platform,
        # and the only thing it unlocks is rows that belong to no tenant.
        #
        # Correct even when the middleware has already declared it: the
        # request path sets the same flag, and this makes the function work
        # off a request too (a management command, a test).
        from tenancy.context import no_tenant

        with no_tenant():
            return (base.filter(organization__isnull=True,
                                is_platform_staff=True)
                    .order_by("date_joined").first())

    if organization is None:
        # No tenant resolved and platform login not permitted here. Refusing is
        # the only safe answer: an unscoped lookup is how one tenant's
        # credentials get checked against another tenant's rows.
        logger.warning("login attempt on a host that resolves to no tenant")
    return None
