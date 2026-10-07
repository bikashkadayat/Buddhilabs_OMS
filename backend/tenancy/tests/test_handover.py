"""Client handover: the package, the email, and closing the loop.

THE PROPERTY THAT MATTERS MOST is the one about the password. It is never
stored -- that rule predates this file -- so every test about sending it is
really a test that the convenience did not quietly undo the rule: the
console cannot mail arbitrary text as a customer's password, cannot replace
a password a customer is already using, and does not change anything when
the mail server refuses.
"""
import pytest
from django.core import mail
from rest_framework.test import APIClient

from tenancy.context import tenant_context
from tenancy.models import Organization, PlatformAuditLog

pytestmark = pytest.mark.django_db

# The organization `created` makes, on the base domain that fixture sets.
HOST = "abcsch.buddhilabs.com"


@pytest.fixture
def platform_api(platform_user):
    client = APIClient()
    client.force_authenticate(user=platform_user)
    return client


@pytest.fixture
def created(platform_api, settings):
    """A workspace made the way an operator makes one, with its first admin."""
    settings.TENANCY_BASE_DOMAIN = "buddhilabs.com"
    settings.PLATFORM_SUPPORT_EMAIL = "help@buddhilabs.com"
    response = platform_api.post("/api/v1/platform/organizations/", {
        "name": "ABC School", "slug": "abcsch", "document_prefix": "ABCH",
        "email": "billing@abc.test", "admin_email": "admin@abc.test",
        "admin_name": "Sita Sharma",
    }, format="json")
    assert response.status_code == 201, response.json()
    return response.json()


def _admin(slug, django_user_model, email="admin@abc.test"):
    org = Organization.objects.get(slug=slug)
    with tenant_context(org):
        return django_user_model.objects.get(email=email)


# --- Part 1: the summary, from the creation response itself ---------------
def test_creation_returns_the_whole_handover_package(created):
    access = created["access"]
    assert access["login_url"] == "https://abcsch.buddhilabs.com/"
    assert access["organization"] == {"name": "ABC School", "slug": "abcsch"}
    assert access["administrator"]["email"] == "admin@abc.test"
    assert access["administrator"]["name"] == "Sita Sharma"
    assert access["administrator"]["awaiting_first_sign_in"] is True
    assert access["administrator"]["signed_in"] is False
    assert access["plan"]["code"]
    assert access["trial"]["days"] > 0
    assert access["can_sign_in"] is True
    assert access["support_email"] == "help@buddhilabs.com"
    # The password travels beside the package, once -- never inside it,
    # because the package is also what the organization page reads later.
    assert "temporary_password" not in access
    assert created["admin_initial_password"]


def test_the_package_is_readable_later_and_never_carries_the_password(
        created, platform_api):
    body = platform_api.get(
        "/api/v1/platform/organizations/abcsch/access/").json()
    assert body["login_url"] == "https://abcsch.buddhilabs.com/"
    assert created["admin_initial_password"] not in str(body)


def test_a_claimed_domain_is_not_handed_out_until_it_serves(
        created, platform_api):
    """A new customer sent to an unverified hostname reaches nothing."""
    from tenancy.models import TenantDomain

    org = Organization.objects.get(slug="abcsch")
    domain = TenantDomain.objects.create(
        organization=org, hostname="hr.abc.edu.np", verification_token="t")
    body = platform_api.get(
        "/api/v1/platform/organizations/abcsch/access/").json()
    assert body["custom_domain"]["hostname"] == "hr.abc.edu.np"
    assert body["custom_domain"]["serving"] is False
    assert body["login_url"] == "https://abcsch.buddhilabs.com/"

    domain.status = TenantDomain.Status.ACTIVE
    domain.save()
    body = platform_api.get(
        "/api/v1/platform/organizations/abcsch/access/").json()
    assert body["login_url"] == "https://hr.abc.edu.np/"


# --- Part 4: email delivery -----------------------------------------------
def test_send_credentials_mails_the_password_the_dialog_showed(
        created, platform_api, django_user_model):
    password = created["admin_initial_password"]
    response = platform_api.post(
        "/api/v1/platform/organizations/abcsch/access/send/",
        {"password": password}, format="json")
    assert response.status_code == 200, response.json()
    assert response.json()["reissued"] is False

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["admin@abc.test"]
    assert "ABC School" in message.subject
    body = message.body
    for expected in ("https://abcsch.buddhilabs.com/", "admin@abc.test",
                     password, "help@buddhilabs.com", "Powered by"):
        assert expected in body, expected
    # Unchanged, and still temporary: the email is a delivery, not a reset.
    admin = _admin("abcsch", django_user_model)
    assert admin.check_password(password)
    assert admin.must_change_password is True
    assert PlatformAuditLog.objects.filter(
        organization__slug="abcsch",
        action=PlatformAuditLog.Action.ACCESS_SENT).exists()


def test_the_console_cannot_mail_arbitrary_text_as_a_password(
        created, platform_api):
    """Checked against the hash, so the server never had to remember it."""
    response = platform_api.post(
        "/api/v1/platform/organizations/abcsch/access/send/",
        {"password": "something-else"}, format="json")
    assert response.status_code == 400
    assert mail.outbox == []


def test_resending_later_issues_a_new_temporary_password(
        created, platform_api, django_user_model):
    old = created["admin_initial_password"]
    response = platform_api.post(
        "/api/v1/platform/organizations/abcsch/access/send/", {},
        format="json")
    assert response.status_code == 200, response.json()
    assert response.json()["reissued"] is True
    # The new password went to the customer, not back to the console.
    assert "password" not in str(response.json()).lower().replace(
        "reissued", "")

    admin = _admin("abcsch", django_user_model)
    assert not admin.check_password(old)
    assert admin.must_change_password is True
    assert len(mail.outbox) == 1
    assert "new sign-in details" in mail.outbox[0].subject.lower()
    assert PlatformAuditLog.objects.filter(
        organization__slug="abcsch",
        action=PlatformAuditLog.Action.ACCESS_REISSUED).exists()


def test_a_password_the_customer_chose_is_never_replaced(
        created, platform_api, django_user_model):
    admin = _admin("abcsch", django_user_model)
    with tenant_context(admin.organization):
        admin.set_password("x-Their-Own-1")
        admin.must_change_password = False
        admin.save()

    response = platform_api.post(
        "/api/v1/platform/organizations/abcsch/access/send/", {},
        format="json")
    assert response.status_code == 400
    assert "already chosen" in response.json()["detail"]
    assert mail.outbox == []
    assert _admin("abcsch", django_user_model).check_password("x-Their-Own-1")


def test_a_mail_failure_changes_nothing(created, platform_api,
                                        django_user_model, monkeypatch):
    """The customer may already hold the old password. Keep it working."""
    from django.core.mail import EmailMultiAlternatives

    def refuse(self, *args, **kwargs):
        raise ConnectionRefusedError("smtp down")

    monkeypatch.setattr(EmailMultiAlternatives, "send", refuse)
    old = created["admin_initial_password"]
    response = platform_api.post(
        "/api/v1/platform/organizations/abcsch/access/send/", {},
        format="json")
    assert response.status_code == 502
    assert "Nothing was changed" in response.json()["detail"]
    assert _admin("abcsch", django_user_model).check_password(old)
    assert not PlatformAuditLog.objects.filter(
        organization__slug="abcsch",
        action__in=[PlatformAuditLog.Action.ACCESS_SENT,
                    PlatformAuditLog.Action.ACCESS_REISSUED]).exists()


def test_the_email_wears_the_tenants_brand_not_the_platforms(
        created, platform_api):
    from tenancy.models import OrganizationBranding

    OrganizationBranding.objects.filter(
        organization__slug="abcsch").update(color_primary="#7C3AED",
                                            display_name="ABC School")
    platform_api.post("/api/v1/platform/organizations/abcsch/access/send/",
                      {"password": created["admin_initial_password"]},
                      format="json")
    html = mail.outbox[0].alternatives[0][0]
    assert "#7C3AED" in html
    assert "ABC School" in html
    # The platform appears once, as attribution, in the footer.
    assert "Powered by" in html


def test_an_organization_without_an_administrator_says_so(platform_api, org):
    response = platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/access/send/", {},
        format="json")
    assert response.status_code == 400
    assert "no administrator" in response.json()["detail"]


# --- Closing the loop ------------------------------------------------------
def test_the_first_password_change_records_that_the_client_got_in(
        created, django_user_model):
    admin = _admin("abcsch", django_user_model)
    client = APIClient()
    client.force_authenticate(user=admin)
    # On the customer's own hostname, which is what binds their organization
    # for the request. On `testserver` the request would be bound to the
    # default organization, and row-level security would (rightly) hide this
    # administrator's row from it.
    response = client.post("/api/v1/auth/change-password/", {
        "current_password": created["admin_initial_password"],
        "new_password": "x-Brand-New-Pass-9",
    }, format="json", HTTP_HOST=HOST)
    assert response.status_code == 200, response.json()
    entry = PlatformAuditLog.objects.get(
        organization__slug="abcsch",
        action=PlatformAuditLog.Action.ADMIN_FIRST_SIGN_IN)
    assert entry.changes["email"] == "admin@abc.test"

    # A later, voluntary change is not a first sign-in.
    with tenant_context(admin.organization):
        admin.refresh_from_db()
    client.force_authenticate(user=admin)
    client.post("/api/v1/auth/change-password/", {
        "current_password": "x-Brand-New-Pass-9",
        "new_password": "x-Another-Pass-10",
    }, format="json", HTTP_HOST=HOST)
    assert PlatformAuditLog.objects.filter(
        organization__slug="abcsch",
        action=PlatformAuditLog.Action.ADMIN_FIRST_SIGN_IN).count() == 1


# --- Part 9: the dashboard's recent lists ---------------------------------
def test_recent_lists_what_just_happened(created, platform_api):
    body = platform_api.get("/api/v1/platform/recent/").json()
    assert set(body) == {"organizations", "domains", "payments",
                         "registrations", "sign_ins"}
    assert body["organizations"][0]["slug"] == "abcsch"


# --- Part 6: the first-login wizard ---------------------------------------
def test_the_wizard_says_where_the_workspace_lives(created,
                                                   django_user_model):
    admin = _admin("abcsch", django_user_model)
    client = APIClient()
    client.force_authenticate(user=admin)
    body = client.get("/api/v1/tenant/onboarding/").json()
    assert body["organization"]["login_url"] == "https://abcsch.buddhilabs.com/"
    keys = [item["key"] for item in body["ready"]]
    assert keys[0] == "organization"
    assert "documents" in keys
