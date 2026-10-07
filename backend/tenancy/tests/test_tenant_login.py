"""Parts 4 and 5: tenant-aware login, and the User model rules behind it."""
import pytest
from django.contrib.auth import authenticate, get_user_model
from django.db import IntegrityError, transaction
from django.test import override_settings
from rest_framework.test import APIClient

from tenancy import services
from tenancy.context import tenant_context

pytestmark = pytest.mark.django_db

PASSWORD = "x-Shared-Addr-1"
SHARED = "admin@company.com"


@pytest.fixture
def other(db, monthly_plan):
    return services.provision_organization(
        name="ABC School", slug="abcschool", document_prefix="ABCS",
        email="admin@abcschool.edu.np", plan=monthly_plan)


@pytest.fixture
def two_admins(db, nif, other):
    """The same email address at two different companies -- Part 4's example."""
    User = get_user_model()
    with tenant_context(nif):
        a = User.objects.create_user(username="admin", email=SHARED,
                                     password=PASSWORD, role="admin")
    with tenant_context(other):
        b = User.objects.create_user(username="admin", email=SHARED,
                                     password=PASSWORD, role="admin")
    return a, b


# --- the collisions that used to be impossible --------------------------
def test_two_tenants_may_share_an_email_address(two_admins, nif, other):
    a, b = two_admins
    assert a.email == b.email == SHARED
    assert a.organization_id == nif.pk
    assert b.organization_id == other.pk


def test_two_tenants_may_share_a_username(two_admins):
    a, b = two_admins
    assert a.username == b.username == "admin"


def test_two_tenants_may_share_an_employee_id(nif, other):
    """Both tenants can issue EMP-0001, and neither can see the other's.

    ASSERTED PER TENANT, NOT BY A CROSS-TENANT COUNT (Phase S6).

    This used to count the rows of BOTH tenants from one connection. That
    cannot work once PostgreSQL row-level security is enforced, and the reason
    it cannot is the thing being tested: a connection bound to one tenant does
    not see another tenant's rows, whichever manager asked. Counting across
    tenants required the boundary to be absent.

    So each tenant is asked inside its own context, which proves the same
    property and one more besides -- that neither sees the other's row.
    """
    User = get_user_model()
    with tenant_context(nif):
        a = User.objects.create_user(username="e1", email="e1@nif.test",
                                     password=PASSWORD)
        User.objects.filter(pk=a.pk).update(employee_id="EMP-0001")
        assert User.objects.filter(employee_id="EMP-0001").count() == 1

    with tenant_context(other):
        b = User.objects.create_user(username="e1", email="e1@abc.test",
                                     password=PASSWORD)
        User.objects.filter(pk=b.pk).update(employee_id="EMP-0001")
        # Exactly one: its own. NIF's EMP-0001 is not visible here, and before
        # Phase S2's per-tenant constraint this insert would have been refused
        # outright.
        assert User.objects.filter(employee_id="EMP-0001").count() == 1
        assert not User.objects.filter(pk=a.pk).exists()

    assert a.pk != b.pk


# --- but never inside one tenant ---------------------------------------
def test_one_tenant_cannot_have_two_users_with_the_same_email(nif):
    User = get_user_model()
    with tenant_context(nif):
        User.objects.create_user(username="u1", email="dup@nif.test",
                                 password=PASSWORD)
        with pytest.raises(IntegrityError), transaction.atomic():
            User.objects.create_user(username="u2", email="dup@nif.test",
                                     password=PASSWORD)


def test_email_uniqueness_is_case_insensitive(nif):
    """"Admin@x" and "admin@x" must not both exist and then resolve differently."""
    User = get_user_model()
    with tenant_context(nif):
        User.objects.create_user(username="c1", email="Mixed@Nif.Test",
                                 password=PASSWORD)
        with pytest.raises(IntegrityError), transaction.atomic():
            User.objects.create_user(username="c2", email="mixed@nif.test",
                                     password=PASSWORD)


def test_one_tenant_cannot_have_two_users_with_the_same_username(nif):
    User = get_user_model()
    with tenant_context(nif):
        User.objects.create_user(username="same", email="s1@nif.test",
                                 password=PASSWORD)
        with pytest.raises(IntegrityError), transaction.atomic():
            User.objects.create_user(username="same", email="s2@nif.test",
                                     password=PASSWORD)


def test_blank_emails_do_not_collide(nif):
    """`email` is blank=True; an unconditional index would allow only one."""
    User = get_user_model()
    with tenant_context(nif):
        User.objects.create_user(username="b1", email="", password=PASSWORD)
        User.objects.create_user(username="b2", email="", password=PASSWORD)
    assert User.objects.filter(email="").count() == 2


def make_platform_user(**fields):
    """Create a platform operator, inside platform scope.

    A platform account has `organization IS NULL`, and under row-level
    security a NULL never matches `organization_id = <tenant>` -- so the row
    is invisible to, and unwritable by, a connection that has not declared
    platform scope (Phase S6). `no_tenant()` is that declaration, and it is
    what the middleware does for a request on a platform host and what
    `create_platform_admin` does on the command line.
    """
    from tenancy.context import no_tenant

    User = get_user_model()
    with no_tenant():
        return User.objects.create_user(
            password=PASSWORD, is_platform_staff=True, **fields)


# --- two platform accounts cannot collide either ------------------------
def test_two_platform_accounts_cannot_share_a_username():
    """NULLs are distinct in a unique index, so this needs its own constraint."""
    make_platform_user(username="ops", email="ops1@platform.test")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_platform_user(username="ops", email="ops2@platform.test")


def test_two_platform_accounts_cannot_share_an_email():
    make_platform_user(username="ops1", email="ops@platform.test")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_platform_user(username="ops2", email="ops@platform.test")


# --- the login path ----------------------------------------------------
@override_settings(TENANCY_BASE_DOMAIN="platform.test", TENANCY_PLATFORM_HOSTS="")
def test_the_same_email_logs_into_different_accounts_per_subdomain(
        two_admins, nif, other):
    """Part 4's example, end to end through the real endpoint."""
    a, b = two_admins

    for host, expected in (("nif.platform.test", a),
                           ("abcschool.platform.test", b)):
        response = APIClient().post(
            "/api/v1/auth/login/",
            {"email": SHARED, "password": PASSWORD},
            format="json", HTTP_HOST=host)
        assert response.status_code == 200, (host, response.data)
        assert response.data["user"]["id"] == str(expected.id), host


@override_settings(TENANCY_BASE_DOMAIN="platform.test", TENANCY_PLATFORM_HOSTS="")
def test_login_no_longer_raises_multipleobjectsreturned(two_admins):
    """The 500 this phase was required to close.

    Before Phase S2 this request hit `User.objects.get(email=...)` with two
    matching rows and returned HTTP 500. It must now resolve to exactly one.
    """
    response = APIClient().post(
        "/api/v1/auth/login/", {"email": SHARED, "password": PASSWORD},
        format="json", HTTP_HOST="nif.platform.test")
    assert response.status_code == 200


@override_settings(TENANCY_BASE_DOMAIN="platform.test", TENANCY_PLATFORM_HOSTS="")
def test_a_wrong_password_is_still_refused(two_admins):
    response = APIClient().post(
        "/api/v1/auth/login/", {"email": SHARED, "password": "wrong-password"},
        format="json", HTTP_HOST="nif.platform.test")
    assert response.status_code == 400


@override_settings(TENANCY_BASE_DOMAIN="platform.test", TENANCY_PLATFORM_HOSTS="")
def test_the_auth_backend_is_tenant_scoped_too(two_admins, nif, other):
    """`authenticate()` goes through EmailBackend, which must also be scoped."""
    from django.test import RequestFactory

    from tenancy.context import tenant_context

    a, b = two_admins
    for host, expected in (("nif.platform.test", a),
                           ("abcschool.platform.test", b)):
        request = RequestFactory().post("/", HTTP_HOST=host)
        request.organization = None
        # The tenant is bound in the DATABASE as well, because that is what
        # TenantResolutionMiddleware does before any view or backend runs.
        # Without it, row-level security hides the very row the backend is
        # supposed to find -- and the backend's own scoping, which is what
        # this test is about, would never be reached.
        with tenant_context(expected.organization):
            assert authenticate(request, username=SHARED,
                                password=PASSWORD) == expected, host


def test_nifs_own_login_is_unchanged(nif):
    """Part 7: no behaviour change visible to NIF.

    One tenant, default host, email login -- exactly as today.
    """
    User = get_user_model()
    with tenant_context(nif):
        User.objects.create_user(username="nifstaff", email="staff@nif.test",
                                 password=PASSWORD, role="maker")
    response = APIClient().post(
        "/api/v1/auth/login/", {"email": "staff@nif.test", "password": PASSWORD},
        format="json")
    assert response.status_code == 200, response.data
    assert response.data["user"]["email"] == "staff@nif.test"


def test_a_platform_account_cannot_log_in_on_a_tenant_host(nif, other):
    """Platform accounts have organization NULL, so a scoped lookup misses them."""
    make_platform_user(username="ops", email="ops@platform.test")

    with override_settings(TENANCY_BASE_DOMAIN="platform.test",
                           TENANCY_PLATFORM_HOSTS="admin.platform.test"):
        response = APIClient().post(
            "/api/v1/auth/login/",
            {"email": "ops@platform.test", "password": PASSWORD},
            format="json", HTTP_HOST="nif.platform.test")
        assert response.status_code == 400


def test_a_platform_account_logs_in_on_the_console_host():
    make_platform_user(username="ops", email="ops@platform.test")

    with override_settings(TENANCY_BASE_DOMAIN="platform.test",
                           TENANCY_PLATFORM_HOSTS="admin.platform.test"):
        response = APIClient().post(
            "/api/v1/auth/login/",
            {"email": "ops@platform.test", "password": PASSWORD},
            format="json", HTTP_HOST="admin.platform.test")
        assert response.status_code == 200, response.data


def test_platform_accounts_may_log_in_anywhere_when_no_console_host_is_set():
    """The single-host deployment NIF runs today -- the console shares the host."""
    make_platform_user(username="ops", email="ops@platform.test")
    response = APIClient().post(
        "/api/v1/auth/login/",
        {"email": "ops@platform.test", "password": PASSWORD}, format="json")
    assert response.status_code == 200, response.data


# --- Part 5: the completed XOR -----------------------------------------
def test_a_tenant_user_must_have_an_organization(nif):
    """A row that is neither a tenant user nor a platform account is refused.

    ``django.db.Error`` rather than ``IntegrityError``: under row-level
    security the refusal arrives earlier and from a different place -- a row
    with no organization cannot satisfy the policy's WITH CHECK on a
    tenant-bound connection, so it is rejected as InsufficientPrivilege before
    the check constraint is evaluated. Same outcome, and the outcome is what
    matters: the row does not exist.
    """
    from django.db import Error

    User = get_user_model()
    user = User(username="nowhere", email="nowhere@x.test",
                is_platform_staff=False)
    with pytest.raises(Error), transaction.atomic():
        # Bypass save()'s stamping to reach the database rule itself.
        User.objects.bulk_create([user])

    assert not User.all_tenants.filter(username="nowhere").exists()


def test_a_platform_user_must_not_have_an_organization(nif):
    from django.core.exceptions import ValidationError

    User = get_user_model()
    with pytest.raises(ValidationError):
        User(username="hybrid", email="h@x.test", is_platform_staff=True,
             organization=nif).save()


def test_the_manager_stamps_the_tenant_for_callers_that_do_not_know_about_it(nif):
    """Why ~107 existing creation sites did not have to change."""
    User = get_user_model()
    user = User.objects.create_user(username="unaware", email="u@nif.test",
                                    password=PASSWORD)
    assert user.organization_id == nif.pk


def test_createsuperuser_produces_a_tenant_admin_not_a_platform_operator(nif):
    User = get_user_model()
    root = User.objects.create_superuser(username="root", email="root@nif.test",
                                         password=PASSWORD)
    assert root.organization_id == nif.pk
    assert root.is_platform_staff is False


def test_get_by_natural_key_disambiguates_by_tenant(two_admins, nif, other):
    """ModelBackend, createsuperuser and the admin all go through here."""
    User = get_user_model()
    a, b = two_admins
    with tenant_context(nif):
        assert User.objects.get_by_natural_key("admin") == a
    with tenant_context(other):
        assert User.objects.get_by_natural_key("admin") == b


def test_get_by_natural_key_is_unchanged_with_one_tenant(nif):
    User = get_user_model()
    with tenant_context(nif):
        user = User.objects.create_user(username="solo", email="solo@nif.test",
                                        password=PASSWORD)
    assert User.objects.get_by_natural_key("solo") == user
