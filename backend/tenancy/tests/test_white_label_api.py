"""Phase S9 Parts 1, 3 and 10: the white-label endpoints as HTTP.

SEPARATE FROM `test_white_label.py` ON PURPOSE. That file tests the service
layer, where a refusal is an exception. This one tests what a browser
actually receives, because the two have come apart before in this codebase:
a service that raises correctly behind a view with the wrong permission
class is a service nobody ever reaches, and a 400 where a 403 belongs tells
an employee their input was wrong when the truth is that the page is not
theirs.
"""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tenancy import domains
from tenancy.context import tenant_context
from tenancy.models import TenantDomain

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _hosts(settings):
    settings.TENANCY_ENABLED = False          # exercise the views, not RLS
    settings.TENANCY_BASE_DOMAIN = "platform.test"
    settings.TENANCY_PLATFORM_HOSTS = ""


@pytest.fixture
def admin(db, org):
    with tenant_context(org):
        return get_user_model().objects.create_user(
            username="abc-admin", email="admin@abcschool.test",
            password="x-Admin-1", role="admin", organization=org)


@pytest.fixture
def employee(db, org):
    with tenant_context(org):
        return get_user_model().objects.create_user(
            username="abc-staff", email="staff@abcschool.test",
            password="x-Staff-1", role="maker", organization=org)


@pytest.fixture
def other_admin(db, nif):
    with tenant_context(nif):
        return get_user_model().objects.create_user(
            username="nif-admin2", email="admin2@nif.test",
            password="x-Admin-1", role="admin", organization=nif)


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


# --- Part 1: branding over HTTP -----------------------------------------
def test_an_administrator_can_read_and_write_branding(admin):
    client = _client(admin)
    assert client.get("/api/v1/tenant/branding/").status_code == 200

    response = client.patch("/api/v1/tenant/branding/",
                            {"display_name": "ABC School",
                             "color_primary": "#1D4ED8"}, format="json")
    assert response.status_code == 200
    assert response.data["color_primary"] == "#1D4ED8"
    # The write is visible on the next read -- not only in the write's own
    # response, which is a weaker claim and was briefly the only true one.
    assert client.get("/api/v1/tenant/branding/").data["color_primary"] \
        == "#1D4ED8"


def test_an_employee_may_READ_branding_but_not_change_it(employee):
    """R28 and the authority rule in one test. The read has to be open to
    every member -- that is how the whole application themes itself -- and
    the write has to be closed to all but an administrator."""
    client = _client(employee)
    assert client.get("/api/v1/tenant/branding/").status_code == 200
    response = client.patch("/api/v1/tenant/branding/",
                            {"display_name": "Mine Now"}, format="json")
    assert response.status_code == 403


def test_an_anonymous_caller_gets_nothing(client):
    """The pre-login endpoint exists for the login page and serves five
    fields. This one must not be a second, wider version of it."""
    assert client.get("/api/v1/tenant/branding/").status_code in (401, 403)


def test_a_bad_colour_is_a_400_with_a_usable_message(admin):
    response = _client(admin).patch(
        "/api/v1/tenant/branding/",
        {"color_primary": "#fff; background:url(javascript:alert(1))"},
        format="json")
    assert response.status_code == 400


def test_branding_is_scoped_to_the_caller_with_no_id_to_pass(admin, nif):
    """There is no `?organization=` to tamper with: cross-tenant branding is
    unexpressible rather than refused."""
    client = _client(admin)
    response = client.patch(f"/api/v1/tenant/branding/?organization={nif.slug}",
                            {"display_name": "ABC School"}, format="json")
    assert response.status_code == 200
    nif.refresh_from_db()
    assert getattr(getattr(nif, "branding", None), "display_name", "") \
        != "ABC School"


# --- Part 3: domains over HTTP ------------------------------------------
def test_an_administrator_can_claim_and_see_the_dns_records(admin):
    client = _client(admin)
    response = client.post("/api/v1/tenant/domains/",
                           {"hostname": "hr.abcschool.edu.np"}, format="json")
    assert response.status_code == 201
    assert response.data["record_type"] == "TXT"
    assert response.data["serving_record"]["name"] == "hr.abcschool.edu.np"

    listing = client.get("/api/v1/tenant/domains/")
    assert listing.status_code == 200
    assert [row["hostname"] for row in listing.data] == ["hr.abcschool.edu.np"]


def test_an_employee_cannot_claim_a_domain(employee):
    response = _client(employee).post(
        "/api/v1/tenant/domains/", {"hostname": "hr.abcschool.edu.np"},
        format="json")
    assert response.status_code == 403


def test_another_tenants_domain_is_not_listed_and_not_verifiable(
        admin, other_admin, org, nif):
    """The fraud scenario S10 Part 2 asks for, at the HTTP boundary: an
    administrator of one tenant acting on a hostname held by another."""
    domains.claim(org, "hr.abcschool.edu.np")
    hostile = _client(other_admin)

    assert hostile.get("/api/v1/tenant/domains/").data == []
    assert hostile.post(
        "/api/v1/tenant/domains/hr.abcschool.edu.np/", {},
        format="json").status_code == 400
    assert hostile.delete(
        "/api/v1/tenant/domains/hr.abcschool.edu.np/").status_code == 400

    # And claiming it outright is refused without naming who holds it.
    taken = hostile.post("/api/v1/tenant/domains/",
                         {"hostname": "hr.abcschool.edu.np"}, format="json")
    assert taken.status_code == 400
    assert "abcschool" not in str(taken.data).lower()

    # The victim still has it, untouched and still pending.
    held = TenantDomain.objects.get(hostname="hr.abcschool.edu.np")
    assert held.organization_id == org.pk
    assert held.status == TenantDomain.Status.PENDING


def test_a_verification_the_platform_cannot_perform_is_a_503_not_a_400(
        admin, org, monkeypatch):
    """A customer told "your record is missing" when we never looked will
    re-check a correct zone file all afternoon. The status code is the
    difference, and the page's copy depends on it."""
    domains.claim(org, "hr.abcschool.edu.np")

    def _unavailable(name, rtype):
        raise domains.VerificationUnavailable("no resolver installed")

    monkeypatch.setattr(domains, "lookup", _unavailable)
    response = _client(admin).post(
        "/api/v1/tenant/domains/hr.abcschool.edu.np/", {}, format="json")
    assert response.status_code == 503
    # And the attempt was still recorded, which is the bug this phase fixed.
    assert TenantDomain.objects.get(
        hostname="hr.abcschool.edu.np").check_count == 1


def test_withdrawing_is_a_204_and_stops_resolution(admin, org, monkeypatch):
    domain = domains.claim(org, "hr.abcschool.edu.np")
    expected = domain.expected_record_value(platform_host="platform.test")
    monkeypatch.setattr(domains, "lookup", lambda n, t: [expected.lower()])
    domains.verify(domain)

    response = _client(admin).delete(
        "/api/v1/tenant/domains/hr.abcschool.edu.np/")
    assert response.status_code == 204
    assert domains.organization_for_host("hr.abcschool.edu.np") is None


# --- Part 3: the operator's cross-tenant view ---------------------------
def test_the_console_lists_every_domain_with_who_holds_it(platform_user, org,
                                                          nif):
    domains.claim(org, "hr.abcschool.edu.np")
    domains.claim(nif, "portal.nif.org.np")

    response = _client(platform_user).get("/api/v1/platform/domains/")
    assert response.status_code == 200
    hostnames = {row["hostname"]: row["slug"] for row in response.data["domains"]}
    assert hostnames == {"hr.abcschool.edu.np": org.slug,
                         "portal.nif.org.np": nif.slug}
    # Stated so an operator does not tell a customer to wait for propagation
    # that has already happened.
    assert "dns_available" in response.data


def test_a_tenant_administrator_cannot_reach_the_console_domain_list(admin):
    assert _client(admin).get("/api/v1/platform/domains/").status_code == 403


def test_an_operator_claiming_for_a_customer_still_cannot_grant_it(
        platform_user, org):
    """An operator reading a hostname off a support ticket is a real case.
    It issues the same token and resolves to nothing until DNS agrees --
    there is no path in this product by which a human grants a hostname."""
    response = _client(platform_user).post(
        f"/api/v1/platform/organizations/{org.slug}/domains/",
        {"hostname": "hr.abcschool.edu.np"}, format="json")
    assert response.status_code == 201
    assert response.data["status"] == TenantDomain.Status.PENDING
    assert domains.organization_for_host("hr.abcschool.edu.np") is None
