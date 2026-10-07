"""Part 9: tenant middleware, resolver, JWT claim, organization context.

FOUNDATION ONLY. The point of most of these tests is that nothing is REFUSED
yet: the resolution and the claims work, and the enforcement path is proven to
be switched off. The last section switches it on with an override and checks
that it would do the right thing, so Phase S3 inherits tested code rather than
untested code.
"""
import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings
from rest_framework_simplejwt.tokens import RefreshToken

from tenancy import context, resolver, tokens
from tenancy.context import current_org_id, no_tenant, tenant_context
from tenancy.exceptions import TenantScopeMissing
from tenancy.middleware import TenantResolutionMiddleware
from tenancy.models import Organization
from tenancy.slugs import RESERVED_SLUGS, validate_tenant_slug

pytestmark = pytest.mark.django_db


# --- the context var ----------------------------------------------------
def test_context_is_unset_by_default():
    assert current_org_id() is None


def test_require_org_raises_rather_than_defaulting():
    """The zero value is "unset", never "every organization"."""
    with pytest.raises(TenantScopeMissing):
        context.require_org_id()


def test_tenant_context_sets_and_restores(nif):
    assert current_org_id() is None
    with tenant_context(nif):
        assert current_org_id() == nif.pk
        assert context.current_organization() == nif
    assert current_org_id() is None


def test_tenant_context_restores_even_when_the_block_raises(nif):
    with pytest.raises(ValueError):
        with tenant_context(nif):
            raise ValueError("boom")
    assert current_org_id() is None


def test_tenant_context_nests(nif, org):
    with tenant_context(nif):
        with tenant_context(org):
            assert current_org_id() == org.pk
        assert current_org_id() == nif.pk


def test_no_tenant_clears_the_context_explicitly(nif):
    with tenant_context(nif):
        with no_tenant():
            assert current_org_id() is None
        assert current_org_id() == nif.pk


# --- slug validation ----------------------------------------------------
@pytest.mark.parametrize("slug", ["nif", "abc-school", "a1", "x" * 63])
def test_valid_slugs_are_accepted(slug):
    assert validate_tenant_slug(slug) == slug


@pytest.mark.parametrize("slug", [
    "", "x" * 64,            # too long for a DNS label
    "-leading", "trailing-",  # invalid DNS label
    "Upper", "under_score", "has.dot", "has space",
    "admin", "api", "www", "billing",   # reserved
    "12345",                  # all numeric
])
def test_invalid_slugs_are_refused(slug):
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        validate_tenant_slug(slug)


def test_every_reserved_slug_is_actually_refused():
    from django.core.exceptions import ValidationError

    for slug in RESERVED_SLUGS:
        with pytest.raises(ValidationError):
            validate_tenant_slug(slug)


# --- host resolution ----------------------------------------------------
@override_settings(TENANCY_BASE_DOMAIN="platform.test",
                   TENANCY_PLATFORM_HOSTS="admin.platform.test")
def test_a_tenant_subdomain_resolves_to_its_organization(nif):
    assert resolver.resolve("nif.platform.test") == nif


@override_settings(TENANCY_BASE_DOMAIN="platform.test",
                   TENANCY_PLATFORM_HOSTS="admin.platform.test")
def test_the_port_and_case_are_ignored(nif):
    assert resolver.resolve("NIF.Platform.Test:8000") == nif


@override_settings(TENANCY_BASE_DOMAIN="platform.test",
                   TENANCY_PLATFORM_HOSTS="admin.platform.test")
def test_the_platform_console_host_resolves_to_no_tenant():
    assert resolver.resolve("admin.platform.test") is None
    assert resolver.is_platform_host("admin.platform.test") is True


@override_settings(TENANCY_BASE_DOMAIN="platform.test", TENANCY_PLATFORM_HOSTS="")
def test_an_unknown_subdomain_resolves_to_nothing():
    assert resolver.resolve("nobody.platform.test") is None


@override_settings(TENANCY_BASE_DOMAIN="platform.test", TENANCY_PLATFORM_HOSTS="")
def test_a_deeper_name_is_not_a_tenant():
    """A wildcard certificate does not cover a.b.platform.test."""
    assert resolver.slug_from_host("a.b.platform.test") is None


@override_settings(TENANCY_BASE_DOMAIN="platform.test", TENANCY_PLATFORM_HOSTS="")
def test_a_custom_domain_resolves(nif):
    nif.domain = "hr.nif.org.np"
    nif.save(update_fields=["domain"])
    resolver.forget(nif)
    assert resolver.resolve("hr.nif.org.np") == nif


@override_settings(TENANCY_BASE_DOMAIN="platform.test", TENANCY_PLATFORM_HOSTS="")
def test_resolution_is_cached_and_invalidated_on_change(nif):
    assert resolver.resolve("nif.platform.test") == nif
    nif.slug = "nif-renamed"
    nif.save(update_fields=["slug"])
    # Without invalidation the old name would keep resolving for a minute.
    resolver.forget(Organization.objects.get(pk=nif.pk))
    assert resolver.resolve("nif.platform.test") is None
    assert resolver.resolve("nif-renamed.platform.test") == nif


# --- JWT claims ---------------------------------------------------------
def test_a_tenant_users_token_carries_its_organization(tenant_admin, nif):
    token = tokens.stamp(RefreshToken.for_user(tenant_admin), tenant_admin)
    assert tokens.org_id_from(token) == str(nif.pk)
    assert tokens.is_platform_token(token) is False


def test_a_platform_users_token_is_marked_and_carries_no_organization(
        platform_user):
    token = tokens.stamp(RefreshToken.for_user(platform_user), platform_user)
    assert tokens.is_platform_token(token) is True
    assert tokens.org_id_from(token) is None


def test_the_claim_survives_onto_the_access_token(tenant_admin, nif):
    """SimpleJWT copies custom claims, so /auth/refresh/ needs no change."""
    refresh = tokens.stamp(RefreshToken.for_user(tenant_admin), tenant_admin)
    assert refresh.access_token[tokens.ORG_CLAIM] == str(nif.pk)


def test_login_stamps_the_claim_end_to_end(tenant_admin, nif):
    from rest_framework.test import APIClient

    tenant_admin.set_password("x-Login-Test-1")
    tenant_admin.save()
    response = APIClient().post("/api/v1/auth/login/", {
        "email": tenant_admin.email, "password": "x-Login-Test-1"}, format="json")
    assert response.status_code == 200, response.data

    from rest_framework_simplejwt.tokens import AccessToken
    assert AccessToken(response.data["access"])[tokens.ORG_CLAIM] == str(nif.pk)


def test_an_ordinary_user_is_now_always_stamped_with_a_tenant(
        django_user_model, nif):
    """Phase S2 closed the "user with no organization" case entirely.

    In Phase S1 a freshly created user had organization = NULL and its token
    carried no `org` claim. The S2 backfill plus `user_tenant_has_organization`
    make that state unreachable: a tenant user without an organization is now
    refused by the database, and TenantUserManager stamps the tenant for every
    caller that does not pass one.
    """
    user = django_user_model.objects.create_user(
        username="legacy", email="legacy@nif.test", password="x-Legacy-1")
    assert user.organization_id == nif.pk
    token = tokens.stamp(RefreshToken.for_user(user), user)
    assert tokens.org_id_from(token) == str(nif.pk)
    assert tokens.is_platform_token(token) is False


# --- middleware: INERT in this phase -----------------------------------
def _call(path="/api/v1/leaves/", host="testserver", user=None, *, touch=True):
    """Run one request through the middleware.

    ``touch`` controls whether the view ASKS for the organization. Resolution is
    lazy, so a view that never asks must cost nothing -- which is the property
    test_resolution_is_lazy below pins.
    """
    request = RequestFactory().get(path, HTTP_HOST=host)
    request.user = user or AnonymousUser()
    sentinel = object()
    captured = {}

    def get_response(req):
        if touch:
            organization = req.organization
            # SimpleLazyObject proxies equality but not identity, so unwrap to
            # a plain value for the assertions below.
            captured["organization"] = (
                None if organization is None or not bool(organization)
                else Organization.objects.get(pk=organization.pk))
            captured["context"] = current_org_id()
        return sentinel

    result = TenantResolutionMiddleware(get_response)(request)
    return result, sentinel, captured


def test_an_unresolved_host_falls_back_to_the_only_organization(nif):
    """Why NIF keeps working: it is reached on an IP, not on nif.<domain>."""
    result, sentinel, captured = _call(host="10.0.0.5")
    assert result is sentinel                        # not refused
    assert captured["organization"] == nif
    assert captured["context"] == nif.pk


def test_the_context_is_reset_after_the_response(nif):
    _call(host="10.0.0.5")
    assert current_org_id() is None


@override_settings(TENANCY_DEFAULT_SLUG="")
def test_the_fallback_refuses_to_guess_once_two_organizations_exist(nif, org):
    """With two tenants there is no safe guess, so it makes none.

    TENANCY_DEFAULT_SLUG is pinned empty, because "no safe guess" is only
    true when no default tenant has been NAMED. A deployment that names one
    has given the shim an answer, and resolving it is correct. Without the
    override this asserted a property of the environment.
    """
    result, sentinel, captured = _call(host="10.0.0.5")
    assert result is sentinel                        # still not refused
    assert captured["organization"] is None


@override_settings(TENANCY_BASE_DOMAIN="platform.test", TENANCY_PLATFORM_HOSTS="")
def test_nothing_is_refused_while_tenancy_is_disabled(nif, org, tenant_admin):
    """The whole safety property of Phase S1, stated as a test.

    A user from one organization, on another organization's subdomain, with an
    expired subscription, is still served. Every one of those is a 4xx in Phase
    S3 and a no-op now.
    """
    from tenancy import services
    from tenancy.models import Subscription

    services.transition(org.subscription, Subscription.Status.SUSPENDED)
    result, sentinel, _ = _call(host="abcschool.platform.test", user=tenant_admin)
    assert result is sentinel


def test_tenancy_is_off_by_default():
    from django.conf import settings

    assert settings.TENANCY_ENABLED is False


# --- middleware: the Phase S3 path, exercised under an override --------
@override_settings(TENANCY_ENABLED=True, TENANCY_BASE_DOMAIN="platform.test",
                   TENANCY_PLATFORM_HOSTS="")
def test_enforcement_refuses_a_cross_tenant_request(nif, org, tenant_admin):
    """tenant_admin belongs to NIF; abcschool.platform.test is not NIF."""
    result, sentinel, _ = _call(host="abcschool.platform.test", user=tenant_admin)
    assert result is not sentinel
    assert result.status_code == 403


@override_settings(TENANCY_ENABLED=True, TENANCY_BASE_DOMAIN="platform.test",
                   TENANCY_PLATFORM_HOSTS="")
def test_enforcement_allows_a_user_on_their_own_subdomain(nif, tenant_admin):
    result, sentinel, captured = _call(host="nif.platform.test", user=tenant_admin)
    assert result is sentinel
    assert captured["organization"] == nif


@override_settings(TENANCY_ENABLED=True, TENANCY_BASE_DOMAIN="platform.test",
                   TENANCY_PLATFORM_HOSTS="")
def test_enforcement_refuses_a_platform_account_on_a_tenant_workspace(
        nif, platform_user):
    result, sentinel, _ = _call(host="nif.platform.test", user=platform_user)
    assert result is not sentinel
    assert result.status_code == 403


@override_settings(TENANCY_ENABLED=True, TENANCY_BASE_DOMAIN="platform.test",
                   TENANCY_PLATFORM_HOSTS="")
def test_enforcement_refuses_an_unknown_workspace_with_404_not_403(nif):
    """404, never 403: a 403 confirms the workspace exists."""
    result, sentinel, _ = _call(host="nobody.platform.test")
    assert result is not sentinel
    assert result.status_code == 404


@override_settings(TENANCY_ENABLED=True, TENANCY_BASE_DOMAIN="platform.test",
                   TENANCY_PLATFORM_HOSTS="")
def test_enforcement_gates_a_suspended_workspace_with_402(org, tenant_admin):
    from tenancy import services
    from tenancy.models import Subscription

    services.transition(org.subscription, Subscription.Status.SUSPENDED)
    result, sentinel, _ = _call(host="abcschool.platform.test")
    assert result is not sentinel
    assert result.status_code == 402


@override_settings(TENANCY_ENABLED=True, TENANCY_BASE_DOMAIN="platform.test",
                   TENANCY_PLATFORM_HOSTS="")
def test_enforcement_exempts_health_and_pre_login_branding(org):
    from tenancy import services
    from tenancy.models import Subscription

    services.transition(org.subscription, Subscription.Status.SUSPENDED)
    for path in ("/api/v1/health/", "/api/v1/tenant/public/"):
        result, sentinel, _ = _call(path=path, host="abcschool.platform.test")
        assert result is sentinel, f"{path} should be exempt"


@override_settings(TENANCY_ENABLED=True, TENANCY_BASE_DOMAIN="platform.test",
                   TENANCY_PLATFORM_HOSTS="")
def test_a_grace_period_workspace_is_still_admitted(org):
    from tenancy import services
    from tenancy.models import Subscription

    services.transition(org.subscription, Subscription.Status.ACTIVE,
                        period_start=org.subscription.trial_start,
                        period_end=org.subscription.trial_end)
    services.transition(Subscription.objects.get(pk=org.subscription.pk),
                        Subscription.Status.GRACE)
    result, sentinel, _ = _call(host="abcschool.platform.test")
    assert result is sentinel


@override_settings(TENANCY_BASE_DOMAIN="platform.test", TENANCY_PLATFORM_HOSTS="")
def test_a_freed_slug_claimed_by_another_tenant_never_serves_the_old_one(nif):
    """Regression. Found by test_resolution_is_cached_and_invalidated_on_change.

    The cache stores host -> organization id. After NIF renames itself, the
    entry under the OLD hostname still points at NIF. If `resolve()` trusted
    that id, then a DIFFERENT tenant taking the freed slug would be handed
    NIF's workspace for up to the cache TTL -- a cross-tenant resolution with
    no bad query anywhere in it.

    So `resolve()` re-validates the row against the host and ignores a cache
    entry the row no longer agrees with.
    """
    from tenancy import services

    # Warm the cache for nif.platform.test -> NIF.
    assert resolver.resolve("nif.platform.test") == nif

    # NIF renames itself, freeing the slug. Deliberately WITHOUT calling
    # forget(), to prove resolve() is safe on its own.
    Organization.objects.filter(pk=nif.pk).update(slug="nif-renamed")

    # Another tenant claims it.
    other = services.provision_organization(
        name="Not NIF", slug="nif", document_prefix="XYZ",
        email="admin@other.test")

    resolved = resolver.resolve("nif.platform.test")
    assert resolved == other, "the freed slug must resolve to its new owner"
    assert resolved != nif


def test_resolution_is_lazy_so_an_untouched_request_costs_no_query(
        nif, django_assert_num_queries, tenant_binding_queries):
    """The property that keeps the project's query budgets intact.

    This middleware originally resolved eagerly and added one query to every
    request, which broke eleven query-budget tests in tasks/ and analytics/.
    They were right: nothing may be queried until something asks.

    ROW-LEVEL SECURITY TAKES THE LAZINESS AWAY, AND THAT IS NOT A BUG
    (Phase S6). Once RLS is enforced, the middleware has to bind
    `app.current_org` BEFORE the view runs -- a view that queried before the
    binding would be unscoped, which is the one ordering mistake that makes
    RLS look like it is working while it is not. Binding requires knowing the
    tenant, so resolution can no longer be deferred until something asks.

    The cost is one cached resolution plus one `set_config` round trip, and it
    buys a boundary the application cannot forget. So the laziness is asserted
    where it still holds -- with RLS off -- and the price is asserted where it
    does not.
    """
    from tenancy import rls

    if rls.is_enabled():
        # The tenant is resolved and bound eagerly. Warm, the resolution is
        # cached and free, so what remains is exactly the binding overhead.
        _call(host="10.0.0.5", touch=False)         # warm the host cache
        with django_assert_num_queries(tenant_binding_queries):
            result, sentinel, _ = _call(host="10.0.0.5", touch=False)
        assert result is sentinel
        return

    with django_assert_num_queries(0):
        result, sentinel, _ = _call(host="10.0.0.5", touch=False)
    assert result is sentinel


def test_repeated_access_within_a_request_resolves_only_once(nif):
    """request.organization and the contextvar must SHARE one resolution.

    They are wired to the same memoised callable, so three accesses cost
    whatever the first one cost and nothing more.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    request = RequestFactory().get("/api/v1/leaves/", HTTP_HOST="10.0.0.5")
    request.user = AnonymousUser()
    seen = {}

    def get_response(req):
        with CaptureQueriesContext(connection) as first:
            assert req.organization.pk == nif.pk          # resolves
        seen["first"] = len(first)
        with CaptureQueriesContext(connection) as rest:
            assert current_org_id() == nif.pk             # shares it
            assert req.organization.pk == nif.pk          # memoised
            assert req.organization.slug == "nif"
        seen["rest"] = len(rest)
        return None

    TenantResolutionMiddleware(get_response)(request)
    assert seen["first"] >= 1
    assert seen["rest"] == 0, (
        f"later accesses cost {seen['rest']} extra queries; the resolution is "
        f"not being shared")


def test_the_fallback_caches_the_lookup_but_still_reads_the_row(
        nif, tenant_binding_queries):
    """The single-tenant fallback is the hot path for the whole of Phase S1.

    Cold costs two queries (host lookup, then the "is there exactly one
    organization?" scan). Warm costs ONE: the cache remembers WHICH
    organization, and the row is then fetched by primary key.

    The row is deliberately NOT cached whole. Organization carries the
    subscription mirror that the Phase S3 admission gate reads on every
    request; serving that from a 60-second cache would mean a suspended tenant
    keeps working for up to a minute after suspension. One indexed primary-key
    fetch is the right price for a gate that is always current.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    def one_request():
        request = RequestFactory().get("/api/v1/leaves/", HTTP_HOST="10.0.0.5")
        request.user = AnonymousUser()
        with CaptureQueriesContext(connection) as captured:
            TenantResolutionMiddleware(lambda req: req.organization.pk)(request)
        return len(captured)

    # THE PER-REQUEST BINDING IS COUNTED, NOT EXCLUDED (Phase S6). See the
    # `tenant_binding_queries` fixture for the breakdown -- one `set_config`
    # round trip, plus the savepoint pair a test sees because the middleware
    # opens a transaction for `SET LOCAL` to be local to.
    binding = tenant_binding_queries

    cold = one_request()
    warm = one_request()
    assert cold == 2 + binding, (
        f"expected {2 + binding} cold queries, got {cold}")
    assert warm == 1 + binding, (
        f"expected {1 + binding} warm queries (primary-key fetch"
        + (" plus the tenant binding" if binding else "")
        + f"), got {warm} -- the host and default-organization lookups should "
        f"both be cached")


def test_the_middleware_keeps_no_per_request_state_on_itself(nif):
    """A middleware instance is shared across requests and across threads."""
    middleware = TenantResolutionMiddleware(lambda req: None)
    before = dict(middleware.__dict__)
    request = RequestFactory().get("/", HTTP_HOST="10.0.0.5")
    request.user = AnonymousUser()
    middleware(request)
    assert middleware.__dict__ == before


def test_a_disallowed_host_header_does_not_crash_resolution(nif):
    """DisallowedHost is CommonMiddleware's answer to give, not ours to crash on."""
    request = RequestFactory().get("/", HTTP_HOST="evil.example.com\x00")
    request.user = AnonymousUser()
    sentinel = object()
    assert TenantResolutionMiddleware(lambda req: sentinel)(request) is sentinel
