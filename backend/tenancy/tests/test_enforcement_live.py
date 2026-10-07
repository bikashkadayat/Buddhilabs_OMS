"""The five defects that only a REAL request found (Phase S6, closing R22).

Phase S6 shipped with 4,161 backend tests passing under enforced row-level
security -- and the platform was still unusable. Every one of those tests runs
through Django's test client against a test database with
``TENANCY_ENABLED = False``. That exercises views. It does not exercise
importing the project with enforcement on, the WSGI/ASGI middleware ordering,
DRF's authentication, or the shape of the login response the frontend reads.

Driving the console as an HTTP client against a real Daphne server with
``TENANCY_ENABLED=1`` and ``TENANCY_RLS_ENABLED=1`` found five defects in
about twenty minutes:

  1. the project could not be IMPORTED              (django-filter)
  2. ``manage.py migrate`` could not COMPLETE       (a data migration)
  3. every authenticated console request answered 500 (SimpleJWT)
  4. every platform sign-in answered 500            (the AuditLog guard)
  5. the console redirect never fired on first login (the login payload)

Each is pinned below. They are grouped in one module because they share a
cause worth stating once: a tenant-scoped DEFAULT manager is consulted by code
that never asked to be tenant-aware -- a filter library describing a lookup,
an auth backend resolving a token, a model's own integrity guard. The default
manager is the wrong place to be strict, and the right place is the moment a
read actually happens.
"""
import pytest
from django.test import override_settings

pytestmark = pytest.mark.django_db


# --- 1. the project must be IMPORTABLE with enforcement on ---------------
@override_settings(TENANCY_ENABLED=True)
def test_building_a_queryset_with_no_tenant_does_not_raise(nif):
    """django-filter calls ``Model._default_manager.all()`` at import time.

    It is resolving a lookup expression, not reading data. The first version
    raised here, so `config/urls.py` could not be imported with
    TENANCY_ENABLED=1 -- no requests, no management commands, no migrations.
    """
    from leaves.models import Leave

    queryset = Leave.objects.all()          # must not raise
    queryset = queryset.filter(reason="x")  # nor through a chain
    assert queryset is not None


@override_settings(TENANCY_ENABLED=True)
def test_but_reading_it_still_fails_closed_and_loudly(nif):
    """The refusal moved; it did not go away."""
    from leaves.models import Leave

    from tenancy.exceptions import TenantScopeMissing

    queryset = Leave.objects.all()
    with pytest.raises(TenantScopeMissing):
        list(queryset)
    with pytest.raises(TenantScopeMissing):
        Leave.objects.count()
    with pytest.raises(TenantScopeMissing):
        Leave.objects.exists()


@override_settings(TENANCY_ENABLED=True)
def test_the_deferred_scope_resolves_to_the_tenant_at_evaluation(nif, org):
    """A queryset built with no tenant takes the tenant it is READ in.

    Which is the only moment the answer is knowable, and is what makes
    import-time construction safe.
    """
    from leaves.models import Department

    from tenancy.context import tenant_context

    with tenant_context(org):
        Department.objects.create(name="Deferred", code="DEFER-ORG")

    built_outside = Department.objects.all()          # no tenant yet
    with tenant_context(org):
        codes = {d.code for d in built_outside}
    assert "DEFER-ORG" in codes

    with tenant_context(nif):
        assert not Department.objects.filter(code="DEFER-ORG").exists()


@override_settings(TENANCY_ENABLED=True)
def test_a_tenant_present_at_construction_is_still_bound_there(nif, org):
    """Unchanged behaviour, and the reason the change is surgical.

    A queryset built inside `tenant_context(A)` carries A's predicate wherever
    it is passed -- so nothing that works today starts depending on where it
    happens to be evaluated, and it never silently becomes the reader's.

    NOTE WHAT THE ASSERTION DELIBERATELY IS NOT: "read it as B and still see
    A's rows". With row-level security enforced the DATABASE applies its own
    predicate at evaluation time, so A's rows are invisible on a connection
    bound to B no matter what the ORM asked for -- the backstop doing exactly
    its job. Asserting the cross-tenant read succeeds would have been
    asserting RLS was off.
    """
    from leaves.models import Department

    from tenancy.context import tenant_context

    with tenant_context(nif):
        Department.objects.create(name="Theirs", code="NIF-ONLY")

    with tenant_context(org):
        Department.objects.create(name="Bound", code="BOUND-ORG")
        bound = Department.objects.all()
        assert bound._tenant_scope_pending is False, (
            "a tenant WAS in context, so it must have been bound here rather "
            "than deferred to whoever evaluates it")

    with tenant_context(nif):
        # `.all()` so this is a fresh clone and not a cached result set.
        assert "NIF-ONLY" not in {d.code for d in bound.all()}, (
            "the queryset re-bound itself to the tenant reading it")

    with tenant_context(org):
        assert {d.code for d in bound.all()} >= {"BOUND-ORG"}


# --- 2. migrations are not tenant-scoped work ----------------------------
@override_settings(TENANCY_ENABLED=True)
def test_enforcement_stands_down_while_migrations_run(nif):
    """`leaves/0014_backfill_categories` reads the LIVE User model.

    It cannot declare a later `users` dependency without making Django reject
    every production database that already applied it -- its own docstring
    says so. With enforcement on it raised, and `migrate` died a third of the
    way through: a deployment could not be brought up at all with the flag
    the whole SaaS launch depends on.

    A migration reads and rewrites the whole database by definition, and runs
    as the BYPASSRLS migration role for the same reason. So enforcement
    stands down for the run.
    """
    from leaves.models import Leave

    from tenancy.context import (_enter_migrations, _exit_migrations,
                                 migrations_running)
    from tenancy.exceptions import TenantScopeMissing

    assert migrations_running() is False
    with pytest.raises(TenantScopeMissing):
        Leave.objects.count()

    _enter_migrations()
    try:
        assert migrations_running() is True
        Leave.objects.count()        # must not raise
    finally:
        _exit_migrations()

    assert migrations_running() is False
    with pytest.raises(TenantScopeMissing):
        Leave.objects.count()


def test_the_migration_signals_are_actually_connected():
    """A flag nothing sets is a flag that does not work.

    Membership in the LIVE receiver list rather than sending the signals:
    `post_migrate` is also where contenttypes and auth permissions hang, and
    firing it here would run those against the test database for a sender
    they were not expecting.
    """
    from django.db.models.signals import post_migrate, pre_migrate

    from tenancy.context import _enter_migrations, _exit_migrations

    def live(signal):
        receivers = signal._live_receivers(None)
        if isinstance(receivers, tuple):          # Django >= 5 returns a pair
            return [r for group in receivers for r in group]
        return list(receivers)

    assert _enter_migrations in live(pre_migrate), (
        "pre_migrate has no tenancy receiver, so migrate would fail with "
        "enforcement on")
    assert _exit_migrations in live(post_migrate), (
        "post_migrate has no tenancy receiver, so enforcement would stay off "
        "for the rest of the process -- the dangerous direction")


# --- 3. JWT must be able to resolve a platform operator ------------------
def test_jwt_resolves_a_platform_operator(platform_user):
    """SimpleJWT looked the token's subject up through the SCOPED manager.

    A platform operator's row has `organization IS NULL`, and a console
    request has no tenant bound -- so the scoped manager refused and EVERY
    authenticated request to the console answered 500.

    Run inside `no_tenant()`, which is what a console request actually is:
    the middleware declares platform scope on a console host, and under RLS
    that declaration is what makes an unowned row readable at all. Without it
    this test asserts the fix against a connection the real request never
    has.
    """
    from rest_framework_simplejwt.tokens import RefreshToken

    from tenancy.authentication import TenantJWTAuthentication
    from tenancy.context import no_tenant

    token = RefreshToken.for_user(platform_user).access_token
    with no_tenant():
        assert TenantJWTAuthentication().get_user(token) == platform_user


@override_settings(TENANCY_ENABLED=True)
def test_jwt_refuses_a_token_replayed_against_another_tenant(nif, org):
    """The boundary the middleware cannot enforce for an API request.

    `TenantResolutionMiddleware._enforce` reads `request.user`, which for a
    JWT call is AnonymousUser -- DRF authenticates inside the view, long
    after. So this is the first place the token's subject is known.
    """
    from django.contrib.auth import get_user_model
    from rest_framework_simplejwt.exceptions import AuthenticationFailed
    from rest_framework_simplejwt.tokens import RefreshToken

    from tenancy.authentication import TenantJWTAuthentication
    from tenancy.context import tenant_context

    User = get_user_model()
    with tenant_context(org):
        member = User.objects.create_user(
            username="replay", email="replay@org.test", password="x-Replay-1")

    token = RefreshToken.for_user(member).access_token
    auth = TenantJWTAuthentication()

    with tenant_context(org):
        assert auth.get_user(token) == member          # its own workspace

    with tenant_context(nif):
        with pytest.raises(AuthenticationFailed):      # somebody else's
            auth.get_user(token)


@override_settings(TENANCY_ENABLED=True)
def test_jwt_refuses_a_platform_operator_on_a_tenant_host(nif, platform_user):
    """A platform account has no workspace to be in."""
    from rest_framework_simplejwt.exceptions import AuthenticationFailed
    from rest_framework_simplejwt.tokens import RefreshToken

    from tenancy.authentication import TenantJWTAuthentication
    from tenancy.context import tenant_context

    token = RefreshToken.for_user(platform_user).access_token
    with tenant_context(nif):
        with pytest.raises(AuthenticationFailed):
            TenantJWTAuthentication().get_user(token)


def test_the_configured_authentication_class_is_the_tenant_aware_one():
    """Writing the class is half of it; wiring it is the other half."""
    from django.conf import settings

    classes = settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]
    assert "tenancy.authentication.TenantJWTAuthentication" in classes
    assert ("rest_framework_simplejwt.authentication.JWTAuthentication"
            not in classes), "the unscoped class is still the default"


# --- 4. the AuditLog immutability guard must be tenant-independent ------
def test_the_audit_immutability_guard_works_in_platform_scope(nif):
    """It asked "does this pk exist?" through the SCOPED manager.

    In platform scope that has no tenant, so with enforcement on it RAISED --
    and since every sign-in writes a LOGIN row, every platform sign-in
    answered 500.
    """
    from audit.models import AuditLog

    from tenancy.context import no_tenant

    with no_tenant():
        entry = AuditLog(action=AuditLog.Action.OTHER, object_repr="probe",
                          changes={})
        entry._organization_decided = True
        entry.save()                                   # must not raise

        with pytest.raises(ValueError):                # and still immutable
            entry.object_repr = "edited"
            entry.save()


def test_the_audit_guard_sees_another_tenants_row(nif, org):
    """The subtler half: bound to A, an existing row owned by B was invisible,
    so the guard passed and an immutable row could be overwritten.

    EITHER REFUSAL IS A PASS, and the distinction is the point. Without
    database policies the ORM guard is the only thing standing there, and it
    only works because it asks through `all_tenants`; with RLS enforced the
    policy refuses the write first and the guard never gets the chance. What
    must hold in both deployments is that the row owned by somebody else is
    still intact afterwards.
    """
    from django.db import DatabaseError, transaction

    from audit.models import AuditLog

    from tenancy.context import tenant_context

    with tenant_context(org):
        entry = AuditLog.objects.create(action=AuditLog.Action.OTHER,
                                         object_repr="theirs", changes={})

    with tenant_context(nif):
        clash = AuditLog(pk=entry.pk, action=AuditLog.Action.OTHER,
                          object_repr="overwritten", changes={})
        clash._organization_decided = True
        # The savepoint is not decoration: a policy refusal aborts the
        # transaction, and without it the assertion below could not query.
        with pytest.raises((ValueError, DatabaseError)), transaction.atomic():
            clash.save()

    with tenant_context(org):
        assert AuditLog.objects.get(pk=entry.pk).object_repr == "theirs"


# --- 5. the login payload must say which application to show ------------
def test_the_login_response_says_whether_the_account_is_platform_staff(
        platform_user, tenant_admin):
    """The frontend's login() reads THIS block, not /auth/user/.

    Without the flag here, a platform operator's first sign-in redirected to
    the tenant workspace, and only a page reload put them in the console.

    Each account signs in on the host it would really use -- the operator on
    the console host, the administrator on their workspace host. That is not
    ceremony: `platform_login_allowed` refuses an operator on a tenant host
    by design, and under RLS the operator's unowned row is only reachable on
    a connection that has declared platform scope, which is what arriving on
    the console host causes.
    """
    from rest_framework.test import APIClient

    from tenancy.context import no_tenant, tenant_context

    with no_tenant():
        platform_user.set_password("x-Payload-1")
        platform_user.save(update_fields=["password"])
    with tenant_context(tenant_admin.organization_id):
        tenant_admin.set_password("x-Payload-1")
        tenant_admin.save(update_fields=["password"])

    cases = ((platform_user, "admin.platform.test", True, None),
             (tenant_admin, "nif.platform.test", False, "nif"))
    with override_settings(TENANCY_BASE_DOMAIN="platform.test",
                           TENANCY_PLATFORM_HOSTS="admin.platform.test"):
        for user, host, expected_platform, expected_slug in cases:
            response = APIClient().post(
                "/api/v1/auth/login/",
                {"email": user.email, "password": "x-Payload-1"},
                format="json", HTTP_HOST=host)
            assert response.status_code == 200, (host, response.data)
            block = response.data["user"]
            assert block["is_platform_staff"] is expected_platform
            assert block["organization_slug"] == expected_slug


# --- the deployment checks ----------------------------------------------
def test_the_checks_are_silent_on_the_configuration_running_today():
    """They must not cry wolf on the single-tenant deployment."""
    from tenancy.checks import tenancy_configuration

    assert tenancy_configuration(None) == []


@override_settings(TENANCY_ENABLED=True, TENANCY_BASE_DOMAIN="",
                   TENANCY_PLATFORM_HOSTS="", TENANCY_RLS_ENABLED=False)
def test_the_checks_name_every_half_configured_deployment():
    from tenancy.checks import tenancy_configuration

    ids = {problem.id for problem in tenancy_configuration(None)}
    assert ids == {"tenancy.E001", "tenancy.W001", "tenancy.W002"}


@override_settings(TENANCY_ENABLED=True, TENANCY_BASE_DOMAIN="platform.test",
                   TENANCY_PLATFORM_HOSTS="", TENANCY_RLS_ENABLED=True)
def test_a_single_host_deployment_is_warned_about_but_permitted():
    """It is a weaker posture, not a broken one -- and it is what NIF runs.

    The console's host guard stands down to match, so an operator who can log
    in can also use the console. Refusing there while permitting sign-in was
    the shape of a bug.
    """
    from tenancy.checks import tenancy_configuration

    ids = {problem.id for problem in tenancy_configuration(None)}
    assert ids == {"tenancy.W001"}


# --- Phase S7: the registration posture checks --------------------------
@override_settings(TENANCY_ENABLED=True, TENANCY_BASE_DOMAIN="platform.test",
                   TENANCY_PLATFORM_HOSTS="admin.platform.test",
                   TENANCY_RLS_ENABLED=True,
                   TENANCY_PUBLIC_REGISTRATION=True,
                   TENANCY_PUBLIC_BASE_URL="https://app.platform.test",
                   EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend")
def test_a_correctly_configured_signup_deployment_is_silent():
    from tenancy.checks import tenancy_configuration

    assert tenancy_configuration(None) == []


@override_settings(TENANCY_ENABLED=True, TENANCY_BASE_DOMAIN="platform.test",
                   TENANCY_PLATFORM_HOSTS="", TENANCY_RLS_ENABLED=True,
                   TENANCY_PUBLIC_REGISTRATION=True,
                   TENANCY_PUBLIC_BASE_URL="",
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_signup_on_every_hostname_with_no_mail_is_flagged():
    """Three mistakes that each leave a platform which boots cleanly.

    A signup form on a customer's own hostname, an email backend that sends
    nothing -- so every registration stops at "check your email" and no
    tenant is ever created -- and a verification link built from a guess.
    """
    from tenancy.checks import tenancy_configuration

    ids = {problem.id for problem in tenancy_configuration(None)}
    assert {"tenancy.W003", "tenancy.W004", "tenancy.W005"} <= ids


@override_settings(TENANCY_ENABLED=True, TENANCY_BASE_DOMAIN="platform.test",
                   TENANCY_PLATFORM_HOSTS="admin.platform.test",
                   TENANCY_RLS_ENABLED=True,
                   TENANCY_PUBLIC_REGISTRATION=False,
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_the_signup_checks_say_nothing_when_signup_is_off():
    """Which is the deployment NIF runs today: a locmem email backend is not
    a problem for an installation that never sends a verification email."""
    from tenancy.checks import tenancy_configuration

    ids = {problem.id for problem in tenancy_configuration(None)}
    assert not {"tenancy.W003", "tenancy.W004", "tenancy.W005"} & ids


# ---------------------------------------------------------------------------
# The refresh endpoint, which runs before authentication and outside a tenant
# ---------------------------------------------------------------------------
# ON THE CONSOLE'S OWN HOSTNAME, which is the situation that produced the
# bug: a platform host resolves to NO tenant deliberately, so every scoped
# read on that path has nothing bound. `testserver` would be refused by the
# middleware before reaching the view, which would test the middleware.
CONSOLE_HOSTS = dict(TENANCY_ENABLED=True,
                     TENANCY_BASE_DOMAIN="buddhilabs.com",
                     TENANCY_PLATFORM_HOSTS="admin.buddhilabs.com",
                     ALLOWED_HOSTS=["*"])


@override_settings(**CONSOLE_HOSTS)
def test_a_platform_operator_can_refresh_their_session(platform_user):
    """Found by watching the console's own server log, not by a test.

    `/api/v1/auth/refresh/` pre-validates the token's user so a stale token
    gives a clean 401 instead of a 500. It did that with `User.objects`,
    which is tenant-scoped -- and refresh runs with NO tenant in context: it
    is reached before authentication, and the console's hostname resolves to
    no tenant at all.

    So the guard raised `TenantScopeMissing` and the endpoint answered 500.
    A platform operator's session could never be refreshed; they were signed
    out the moment their access token expired, with a 500 in the log and
    nothing on screen to explain it.
    """
    from rest_framework.test import APIClient
    from rest_framework_simplejwt.tokens import RefreshToken

    from tenancy.context import no_tenant

    with no_tenant():
        refresh = str(RefreshToken.for_user(platform_user))

    response = APIClient().post("/api/v1/auth/refresh/",
                                {"refresh": refresh}, format="json",
                                HTTP_HOST="admin.buddhilabs.com")
    assert response.status_code == 200, getattr(response, "data", response.content)
    assert "access" in response.data


@override_settings(**CONSOLE_HOSTS)
def test_and_a_token_for_a_deleted_account_still_gets_a_clean_401(nif):
    """The behaviour the original guard existed for, kept."""
    import uuid

    from rest_framework.test import APIClient
    from rest_framework_simplejwt.tokens import RefreshToken

    token = RefreshToken()
    token["user_id"] = str(uuid.uuid4())
    response = APIClient().post("/api/v1/auth/refresh/",
                                {"refresh": str(token)}, format="json",
                                HTTP_HOST="admin.buddhilabs.com")
    assert response.status_code == 401


@override_settings(**CONSOLE_HOSTS)
def test_the_operators_old_refresh_token_is_really_blacklisted(platform_user):
    """Rotation must do its bookkeeping, not merely avoid crashing.

    The fix reimplements SimpleJWT's `validate` (its user lookup is inline
    and cannot be hooked), so the rotation behaviour is now OURS to keep
    correct: blacklist the presented token, re-jti, and hand back a new one.
    Without this test a future upgrade could change the library's semantics
    and nothing here would notice.
    """
    from rest_framework.test import APIClient
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

    from tenancy.context import no_tenant
    from tenancy.tokens import TenantSafeRefreshToken

    with no_tenant():
        first = str(TenantSafeRefreshToken.for_user(platform_user))

    client = APIClient()
    rotated = client.post("/api/v1/auth/refresh/", {"refresh": first},
                          format="json", HTTP_HOST="admin.buddhilabs.com")
    assert rotated.status_code == 200
    second = rotated.data["refresh"]
    assert second != first, "ROTATE_REFRESH_TOKENS is on; a new token is due"

    # The old one is on the blacklist, attributed to the operator -- which is
    # the part that needed the cross-tenant read to work at all.
    row = BlacklistedToken.objects.select_related("token").first()
    assert row is not None, "the presented token was not blacklisted"
    assert row.token.user_id == platform_user.pk

    # And it is refused from here on.
    replay = client.post("/api/v1/auth/refresh/", {"refresh": first},
                         format="json", HTTP_HOST="admin.buddhilabs.com")
    assert replay.status_code == 401


@override_settings(**CONSOLE_HOSTS)
def test_a_platform_operator_can_sign_out(platform_user):
    """The same bug, on the other end of the session, found while fixing it.

    `LogoutView` blacklists the caller's refresh token, and `blacklist()`
    looks the user up with the tenant-scoped manager too. A platform
    operator's request carries platform scope rather than a tenant, so
    signing out answered 500 -- and because the 500 came from the blacklist
    write itself, the token was left VALID. "Log out" revoked nothing.
    """
    from rest_framework.test import APIClient
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

    from tenancy.context import no_tenant
    from tenancy.tokens import TenantSafeRefreshToken

    with no_tenant():
        refresh = TenantSafeRefreshToken.for_user(platform_user)

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    response = client.post("/api/v1/auth/logout/", {"refresh": str(refresh)},
                           format="json", HTTP_HOST="admin.buddhilabs.com")
    assert response.status_code == 205, getattr(response, "data", response.content)
    assert BlacklistedToken.objects.count() == 1

    # Revoked for real: the token cannot mint another access token.
    after = APIClient().post("/api/v1/auth/refresh/", {"refresh": str(refresh)},
                             format="json", HTTP_HOST="admin.buddhilabs.com")
    assert after.status_code == 401
