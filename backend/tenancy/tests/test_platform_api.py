"""Parts 1, 2, 5, 7, 9 over HTTP: the Platform Admin Console API.

The most important tests in this file are the negative ones. A console that
works is table stakes; a console a tenant administrator can reach is a
platform-wide breach, and "no tenant admin can access these pages" (Part 2) is
the requirement that has to be proven rather than asserted.
"""
import pytest
from rest_framework.test import APIClient

from tenancy.models import Organization, PlatformAuditLog, Subscription

pytestmark = pytest.mark.django_db

O = Organization.Status
S = Subscription.Status


@pytest.fixture
def platform_api(platform_user):
    client = APIClient()
    client.force_authenticate(user=platform_user)
    return client


@pytest.fixture
def tenant_api(tenant_admin):
    client = APIClient()
    client.force_authenticate(user=tenant_admin)
    return client


# Every console route, with a method and a minimal body. Used by the
# authority tests below so a route added later without a permission class
# fails here rather than shipping.
def console_routes(slug):
    return [
        ("get", "/api/v1/platform/dashboard/", None),
        ("get", "/api/v1/platform/health/", None),
        ("get", "/api/v1/platform/plans/", None),
        ("get", "/api/v1/platform/organizations/", None),
        ("post", "/api/v1/platform/organizations/",
         {"name": "X", "slug": "xco", "document_prefix": "X",
          "email": "x@y.test"}),
        ("get", f"/api/v1/platform/organizations/{slug}/", None),
        ("patch", f"/api/v1/platform/organizations/{slug}/", {"name": "Y"}),
        ("get", f"/api/v1/platform/organizations/{slug}/usage/", None),
        ("get", f"/api/v1/platform/organizations/{slug}/health/", None),
        ("post", f"/api/v1/platform/organizations/{slug}/repair/", {}),
        ("post", f"/api/v1/platform/organizations/{slug}/suspend/",
         {"reason": "x"}),
        ("post", f"/api/v1/platform/organizations/{slug}/activate/", {}),
        ("post", f"/api/v1/platform/organizations/{slug}/cancel/",
         {"reason": "x"}),
        ("post", f"/api/v1/platform/organizations/{slug}/status/",
         {"status": "suspended"}),
        ("post", f"/api/v1/platform/organizations/{slug}/assign-plan/",
         {"plan_code": "monthly"}),
        ("post", f"/api/v1/platform/organizations/{slug}/change-plan/",
         {"plan_code": "monthly"}),
        ("post", f"/api/v1/platform/organizations/{slug}/start-trial/", {}),
        ("post", f"/api/v1/platform/organizations/{slug}/extend/",
         {"months": 1}),
        ("get", f"/api/v1/platform/organizations/{slug}/branding/", None),
        ("patch", f"/api/v1/platform/organizations/{slug}/branding/",
         {"display_name": "X"}),
        ("get", f"/api/v1/platform/organizations/{slug}/settings/", None),
        ("patch", f"/api/v1/platform/organizations/{slug}/settings/",
         {"escalation_hr_days": 9}),
        ("get", "/api/v1/platform/audit/", None),
        ("get", "/api/v1/platform/payments/", None),
        ("post", "/api/v1/platform/counters/refresh/", {}),
        ("post", "/api/v1/platform/mirrors/reconcile/", {}),
        # Phase S6.5. The export routes matter most on this list: a bundle is
        # a customer's entire workspace in one file, so a tenant reaching
        # even the LIST of them would be told that an export exists.
        ("get", f"/api/v1/platform/organizations/{slug}/exports/", None),
        ("post", f"/api/v1/platform/organizations/{slug}/exports/", {}),
        ("post", f"/api/v1/platform/organizations/{slug}/archive/",
         {"reason": "x"}),
        ("post", f"/api/v1/platform/organizations/{slug}/restore/", {}),
        # Client handover. The send route can reissue a customer's password
        # and email it, so it belongs on this list more than most.
        ("get", f"/api/v1/platform/organizations/{slug}/access/", None),
        ("post", f"/api/v1/platform/organizations/{slug}/access/send/", {}),
        ("get", "/api/v1/platform/recent/", None),
        # The Payment Center: deciding money, and where customers send it.
        ("post", "/api/v1/platform/payments/00000000-0000-0000-0000-000000000000/approve/", {}),
        ("post", "/api/v1/platform/payments/00000000-0000-0000-0000-000000000000/reject/", {"reason": "x"}),
        ("post", "/api/v1/platform/payments/00000000-0000-0000-0000-000000000000/request-info/", {"message": "x"}),
        ("get", "/api/v1/platform/payments/00000000-0000-0000-0000-000000000000/proof/", None),
        ("get", "/api/v1/platform/payment-methods/", None),
        ("post", "/api/v1/platform/payment-methods/", {"method": "esewa", "label": "x"}),
        ("patch", "/api/v1/platform/payment-methods/00000000-0000-0000-0000-000000000000/", {"label": "y"}),
        ("post", "/api/v1/platform/payment-methods/00000000-0000-0000-0000-000000000000/", {"state": "disabled"}),
        # Customer success: health is about every customer; the inbox holds
        # what customers wrote to the platform team.
        ("get", "/api/v1/platform/customer-health/", None),
        ("get", "/api/v1/platform/support/", None),
        ("patch", "/api/v1/platform/support/00000000-0000-0000-0000-000000000000/", {"status": "resolved"}),
    ]


ROUTE_IDS = [f"{method}:{path}" for method, path, _ in console_routes("SLUG")]


# --- Part 2: "No tenant admin can access these pages." -----------------
@pytest.mark.parametrize("index", range(len(ROUTE_IDS)), ids=ROUTE_IDS)
def test_a_tenant_admin_is_refused_by_every_console_route(index, tenant_api,
                                                           org):
    method, path, body = console_routes(org.slug)[index]
    response = getattr(tenant_api, method)(path, body, format="json")
    assert response.status_code == 403, (
        f"{method.upper()} {path} answered {response.status_code} to a "
        f"tenant administrator")


@pytest.mark.parametrize("index", range(len(ROUTE_IDS)), ids=ROUTE_IDS)
def test_an_anonymous_caller_is_refused_by_every_console_route(index, org):
    method, path, body = console_routes(org.slug)[index]
    response = getattr(APIClient(), method)(path, body, format="json")
    assert response.status_code in (401, 403), (
        f"{method.upper()} {path} answered {response.status_code} to an "
        f"anonymous caller")


def test_a_django_superuser_is_not_a_platform_operator(django_user_model, nif,
                                                        org):
    """Every admin guard in this project is an allow-list, including this one.

    Widening it to Django's staff flag would hand the platform console to
    anybody who had ever been given admin access for an unrelated reason.
    """
    from tenancy.context import tenant_context

    with tenant_context(nif):
        root = django_user_model.objects.create_superuser(
            username="root", email="root@x.test", password="x-Root-1")
    client = APIClient()
    client.force_authenticate(user=root)
    assert client.get("/api/v1/platform/dashboard/").status_code == 403


def test_the_console_is_refused_on_a_tenant_host_once_tenancy_is_on(
        settings, platform_api, org):
    """Part 1: the console is served on its own hostname.

    A platform session established on a customer's subdomain is one XSS away
    from being a platform session the customer can drive.
    """
    settings.TENANCY_ENABLED = True
    settings.TENANCY_BASE_DOMAIN = "platform.test"
    settings.TENANCY_PLATFORM_HOSTS = "admin.platform.test"

    refused = platform_api.get("/api/v1/platform/dashboard/",
                               HTTP_HOST="abcschool.platform.test")
    assert refused.status_code == 403
    assert "platform host" in refused.json()["detail"]

    allowed = platform_api.get("/api/v1/platform/dashboard/",
                               HTTP_HOST="admin.platform.test")
    assert allowed.status_code == 200


# --- Part 1: the dashboard --------------------------------------------
def test_the_dashboard_returns_every_widget_part_one_names(platform_api, nif,
                                                            org):
    data = platform_api.get("/api/v1/platform/dashboard/").json()
    for key in ("organizations_total", "organizations_active",
                "organizations_suspended", "organizations_trial",
                "users_total", "subscriptions_active", "revenue_forecast",
                "storage_bytes", "system_health"):
        assert key in data, f"dashboard is missing the '{key}' widget"
    assert data["organizations_total"] == 2


def test_the_dashboard_counts_users_without_reading_a_tenant_table(
        platform_api, nif, tenant_admin):
    """The counter, not a join. See tenancy/counters.py for why that matters."""
    from tenancy import counters

    counters.refresh_all(storage=False)
    data = platform_api.get("/api/v1/platform/dashboard/").json()
    assert data["users_total"] >= 1


def test_revenue_forecast_excludes_trials(platform_api, nif, org):
    """A forecast that counts trials is a forecast that always looks good.

    NIF is ACTIVE and counts; `org` is on a trial and must not. So the
    assertion is on which tenants are counted, not on a bare total.
    """
    forecast = platform_api.get(
        "/api/v1/platform/dashboard/").json()["revenue_forecast"]
    assert org.subscription.status == S.TRIAL
    assert forecast["counted"] == 1, (
        "only the one ACTIVE tenant may be counted")


def test_revenue_forecast_normalises_a_long_plan_to_one_month(platform_api,
                                                               nif, org,
                                                               annual_plan):
    """A twelve-month plan contributes a twelfth of its price per month.

    Measured as the DELTA from activating one more tenant, so the test says
    nothing about how many other tenants happen to exist.
    """
    from tenancy import plans

    before = platform_api.get(
        "/api/v1/platform/dashboard/").json()["revenue_forecast"]

    platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/assign-plan/",
        {"plan_code": "annual"}, format="json")

    after = platform_api.get(
        "/api/v1/platform/dashboard/").json()["revenue_forecast"]
    price = plans.current_price(annual_plan)

    assert after["counted"] == before["counted"] + 1
    assert after["monthly_minor"] - before["monthly_minor"] == (
        price.amount_minor // annual_plan.interval_months)
    assert after["annual_minor"] == after["monthly_minor"] * 12


def test_system_health_reports_a_tenant_stuck_in_provisioning(platform_api, db,
                                                               monthly_plan):
    from tenancy import services

    from .conftest import TODAY

    services.provision_organization(
        name="Stuck", slug="stuckco", document_prefix="STK",
        email="a@stuck.test", plan=monthly_plan, today=TODAY,
        status=O.PROVISIONING, bootstrap=False)

    health = platform_api.get("/api/v1/platform/health/").json()
    assert "stuckco" in health["organizations_stuck_provisioning"]
    assert health["status"] == "degraded"


# --- Part 2 / 3: one-click provisioning --------------------------------
def test_provisioning_one_request_produces_a_usable_tenant(platform_api):
    response = platform_api.post("/api/v1/platform/organizations/", {
        "name": "Sunrise Hospital", "slug": "sunrise",
        "document_prefix": "SUNR", "email": "admin@sunrise.test",
        "plan_code": "monthly",
    }, format="json")
    assert response.status_code == 201, response.json()
    body = response.json()

    assert body["slug"] == "sunrise"
    assert body["status"] == O.TRIAL
    assert body["is_admitted"] is True
    # The receipt: what was created, and nothing missing. Counted against the
    # catalogue rather than a literal, so adding a platform default is one
    # edit in `tenancy.bootstrap` and not a hunt through the tests.
    from tenancy import bootstrap

    assert body["provisioning"]["gaps"] == {}
    assert (body["provisioning"]["bootstrap"]["leave_types"]
            == len(bootstrap.LEAVE_TYPES))
    assert (body["provisioning"]["bootstrap"]["inventory_categories"]
            == len(bootstrap.INVENTORY_CATEGORIES))


def test_provisioning_can_create_the_first_administrator(platform_api,
                                                          django_user_model):
    response = platform_api.post("/api/v1/platform/organizations/", {
        "name": "Kathmandu Co", "slug": "ktmco", "document_prefix": "KTM",
        "email": "billing@ktm.test", "admin_email": "admin@ktm.test",
        "admin_name": "Asha Shrestha",
    }, format="json")
    assert response.status_code == 201, response.json()

    # Shown ONCE. This is the only moment the password can be handed over.
    password = response.json()["admin_initial_password"]
    assert password

    # The tenant's own administrator, so read as that tenant: under row-level
    # security a connection bound elsewhere cannot see the row.
    from tenancy.context import tenant_context
    from tenancy.models import Organization

    created = Organization.objects.get(slug="ktmco")
    with tenant_context(created):
        user = django_user_model.objects.get(email="admin@ktm.test")
    assert user.role == "admin"
    assert user.organization.slug == "ktmco"
    assert user.is_platform_staff is False
    assert user.must_change_password is True, (
        "a temporary password must not be able to become a permanent one")
    assert user.check_password(password)


def test_the_initial_password_is_never_returned_again(platform_api):
    platform_api.post("/api/v1/platform/organizations/", {
        "name": "Once Co", "slug": "onceco", "document_prefix": "ONCE",
        "email": "b@once.test", "admin_email": "admin@once.test",
    }, format="json")
    detail = platform_api.get("/api/v1/platform/organizations/onceco/").json()
    assert "admin_initial_password" not in detail


def test_a_duplicate_workspace_address_is_refused(platform_api, org):
    response = platform_api.post("/api/v1/platform/organizations/", {
        "name": "Clash", "slug": org.slug, "document_prefix": "CLSH",
        "email": "c@clash.test",
    }, format="json")
    assert response.status_code == 400
    assert "slug" in response.json()


def test_provisioning_is_all_or_nothing(platform_api, monkeypatch):
    """A half-provisioned tenant must not be reachable even by a crash."""
    from tenancy import bootstrap

    def explode(*args, **kwargs):
        raise RuntimeError("bootstrap failed halfway")

    monkeypatch.setattr(bootstrap, "bootstrap_organization", explode)

    with pytest.raises(RuntimeError):
        platform_api.post("/api/v1/platform/organizations/", {
            "name": "Doomed", "slug": "doomed", "document_prefix": "DOOM",
            "email": "d@doom.test",
        }, format="json")

    assert not Organization.objects.filter(slug="doomed").exists(), (
        "a failed bootstrap left an Organization row behind")


# --- Part 2: editing ---------------------------------------------------
def test_an_operator_can_edit_the_editable_fields(platform_api, org):
    response = platform_api.patch(
        f"/api/v1/platform/organizations/{org.slug}/",
        {"name": "ABC School Pvt Ltd", "phone": "+977-1-5555555"},
        format="json")
    assert response.status_code == 200
    assert response.json()["name"] == "ABC School Pvt Ltd"


@pytest.mark.parametrize("field,value", [
    ("slug", "renamed"),
    ("document_prefix", "NEW"),
    ("status", "active"),
    ("subscription_status", "active"),
    ("seat_count", 9999),
    ("storage_bytes", 0),
])
def test_the_fields_that_must_never_come_from_a_request_are_ignored(
        platform_api, org, field, value):
    """An allow-list, not a block-list.

    `slug` is the tenant's hostname and `document_prefix` is embedded in
    document numbers already issued; `status` is the workspace gate; the rest
    are mirrors and counters. None of them may move through a form.
    """
    before = getattr(org, field)
    platform_api.patch(f"/api/v1/platform/organizations/{org.slug}/",
                       {field: value}, format="json")
    org.refresh_from_db()
    assert getattr(org, field) == before


# --- Part 5: subscription management -----------------------------------
def test_assign_plan_activates_and_dates_the_subscription(platform_api, org):
    response = platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/assign-plan/",
        {"plan_code": "annual", "note": "Paid by bank transfer"},
        format="json")
    assert response.status_code == 200, response.json()
    subscription = response.json()["subscription"]
    assert subscription["status"] == S.ACTIVE
    assert subscription["plan_code"] == "annual"
    assert subscription["current_period_end"] is not None


def test_change_plan_does_not_re_date_the_period(platform_api, org):
    """Moving plan mid-term must not hand out or take away paid time."""
    before = platform_api.get(
        f"/api/v1/platform/organizations/{org.slug}/").json()["subscription"]
    response = platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/change-plan/",
        {"plan_code": "annual"}, format="json")
    after = response.json()["subscription"]
    assert after["plan_code"] == "annual"
    assert after["current_period_end"] == before["current_period_end"]


def test_extend_adds_months_to_the_existing_term(platform_api, org):
    before = platform_api.get(
        f"/api/v1/platform/organizations/{org.slug}/").json()["subscription"]
    response = platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/extend/",
        {"months": 3}, format="json")
    after = response.json()["subscription"]
    assert after["current_period_end"] > before["current_period_end"]
    assert after["status"] == S.ACTIVE


def test_extending_by_zero_months_is_refused(platform_api, org):
    response = platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/extend/",
        {"months": 0}, format="json")
    assert response.status_code == 400


def test_start_trial_restarts_a_trial(platform_api, org):
    response = platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/start-trial/",
        {"days": 30}, format="json")
    assert response.status_code == 200
    assert response.json()["subscription"]["status"] == S.TRIAL


def test_an_unknown_plan_code_is_refused(platform_api, org):
    response = platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/assign-plan/",
        {"plan_code": "platinum-unlimited"}, format="json")
    assert response.status_code == 400


def test_there_is_no_route_that_sets_a_subscription_status_directly(
        platform_api, org):
    """Status moves through an ACTION, never a field.

    Each action carries different required input -- a suspension needs a
    reason, an extension needs months -- and that is what makes the audit
    trail readable afterwards.
    """
    response = platform_api.patch(
        f"/api/v1/platform/organizations/{org.slug}/",
        {"subscription_status": "active"}, format="json")
    org.refresh_from_db()
    assert org.subscription_status == S.TRIAL
    assert response.status_code == 200   # accepted, and ignored


# --- Part 6: lifecycle over HTTP --------------------------------------
def test_suspend_requires_a_reason(platform_api, org):
    assert platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/suspend/",
        {}, format="json").status_code == 400
    assert platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/suspend/",
        {"reason": "   "}, format="json").status_code == 400


def test_suspend_then_activate_round_trips(platform_api, org):
    platform_api.post(f"/api/v1/platform/organizations/{org.slug}/suspend/",
                      {"reason": "Non-payment"}, format="json")
    org.refresh_from_db()
    assert org.is_admitted is False

    platform_api.post(f"/api/v1/platform/organizations/{org.slug}/activate/",
                      {"note": "Paid"}, format="json")
    org.refresh_from_db()
    assert org.is_admitted is True


def test_an_illegal_lifecycle_move_is_a_400_not_a_500(platform_api, org):
    response = platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/status/",
        {"status": O.PROVISIONING}, format="json")
    assert response.status_code == 400
    assert "cannot move" in response.json()["detail"].lower()


def test_cancelling_deletes_nothing(platform_api, org):
    """Cancellation is a gate, not a delete.

    Counted from INSIDE the cancelled tenant. Under row-level security a
    connection bound to a different tenant sees none of these rows, so an
    outside count would read zero and "nothing was deleted" would be
    indistinguishable from "everything was".
    """
    from leaves.models import LeaveType

    from tenancy.context import tenant_context

    with tenant_context(org):
        before = LeaveType.objects.count()
    assert before > 0, "the fixture should have bootstrapped leave types"

    platform_api.post(f"/api/v1/platform/organizations/{org.slug}/cancel/",
                      {"reason": "Customer left"}, format="json")
    org.refresh_from_db()
    assert org.status == O.CANCELLED

    with tenant_context(org):
        assert LeaveType.objects.count() == before, (
            "cancellation is not deletion")


# --- Part 7: branding --------------------------------------------------
def test_an_operator_can_set_branding(platform_api, org):
    response = platform_api.patch(
        f"/api/v1/platform/organizations/{org.slug}/branding/",
        {"display_name": "ABC School", "color_primary": "#0F766E",
         "login_tagline": "Learning, together"}, format="json")
    assert response.status_code == 200
    assert response.json()["color_primary"] == "#0F766E"


def test_an_invalid_colour_is_refused(platform_api, org):
    response = platform_api.patch(
        f"/api/v1/platform/organizations/{org.slug}/branding/",
        {"color_primary": "teal"}, format="json")
    assert response.status_code == 400


def test_branding_cannot_be_re_pointed_at_another_tenant(platform_api, org,
                                                          nif):
    """`organization` is excluded from the serializer, so this is ignored."""
    platform_api.patch(
        f"/api/v1/platform/organizations/{org.slug}/branding/",
        {"display_name": "Hijacked", "organization": str(nif.pk)},
        format="json")
    nif.branding.refresh_from_db()
    org.branding.refresh_from_db()
    assert org.branding.display_name == "Hijacked"
    assert nif.branding.display_name != "Hijacked"


def test_a_tenant_sees_only_its_own_branding(client, org, nif, settings):
    """Part 7's verification, from the tenant side.

    The pre-login endpoint answers for the host it was asked on, and serves
    only the public payload -- a name, a logo, a favicon, two colours and a
    tagline.
    """
    settings.TENANCY_BASE_DOMAIN = "platform.test"
    org.branding.display_name = "ABC School"
    org.branding.color_primary = "#0F766E"
    org.branding.save()
    nif.branding.display_name = "Nepal Internet Foundation"
    nif.branding.save()

    response = client.get("/api/v1/tenant/public/branding/",
                          HTTP_HOST="abcschool.platform.test")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "ABC School"
    assert body["color_primary"] == "#0F766E"
    # And nothing else about the tenant. This exact-set assertion is the
    # allow-list guard: an unauthenticated prober must not be handed
    # headcount, plan or subscription status, so widening the payload has to
    # be a deliberate edit here.
    assert set(body) == {"name", "logo_login", "favicon", "color_primary",
                         "color_secondary", "login_tagline", "known",
                         # Phase S7: a capability of the deployment, not a
                         # fact about this customer -- the sign-in page needs
                         # it to decide whether to offer signup.
                         "registration_open",
                         # Phase S11 Part 7: likewise the advertised trial
                         # length. It is the public OFFER, identical for
                         # everybody and already printed on the signup page,
                         # so it reveals nothing about this tenant -- and
                         # without it the signup page promised nothing and
                         # disclaimed a card, which is the one screen where
                         # the offer has to be stated.
                         "trial_days"}


def test_a_tenants_favicon_reaches_the_browser_tab(client, org, settings):
    """It could be uploaded and stored, and then nothing served it.

    `console.set_branding_asset` has accepted a favicon since Phase S6 and
    routes it to `Organization.favicon` -- but it was absent from the one
    payload a browser can read before anybody signs in, which is the only
    moment a tab icon can be applied. So every customer who uploaded their own
    icon still got the platform's.
    """
    from django.core.files.uploadedfile import SimpleUploadedFile

    settings.TENANCY_BASE_DOMAIN = "platform.test"
    # A 1x1 GIF: the smallest thing ImageField will accept as an image.
    org.favicon = SimpleUploadedFile(
        "icon.gif",
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff"
        b"\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01"
        b"\x00\x01\x00\x00\x02\x02D\x01\x00;",
        content_type="image/gif")
    org.save(update_fields=["favicon"])

    response = client.get("/api/v1/tenant/public/branding/",
                          HTTP_HOST="abcschool.platform.test")
    assert response.status_code == 200
    favicon = response.json()["favicon"]
    assert favicon, "the tenant's favicon never reaches the login page"
    # Served through the signed-media path like every other tenant file, so it
    # is not readable by guessing a URL.
    assert "icon" in favicon


def test_the_public_branding_endpoint_does_not_enumerate_customers(client,
                                                                    settings):
    """An unknown host gets the platform default, not a 404.

    A 404 here would turn the login page into a customer-list oracle.
    """
    settings.TENANCY_ENABLED = True
    settings.TENANCY_BASE_DOMAIN = "platform.test"
    response = client.get("/api/v1/tenant/public/branding/",
                          HTTP_HOST="doesnotexist.platform.test")
    assert response.status_code == 200
    assert response.json()["known"] is False


# --- Part 9: the audit trail ------------------------------------------
def test_every_console_action_is_recorded(platform_api, org, monthly_plan):
    platform_api.post(f"/api/v1/platform/organizations/{org.slug}/suspend/",
                      {"reason": "Non-payment"}, format="json")
    platform_api.post(f"/api/v1/platform/organizations/{org.slug}/activate/",
                      {}, format="json")
    platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/change-plan/",
        {"plan_code": "annual"}, format="json")
    platform_api.patch(
        f"/api/v1/platform/organizations/{org.slug}/branding/",
        {"display_name": "Renamed"}, format="json")
    platform_api.post(f"/api/v1/platform/organizations/{org.slug}/extend/",
                      {"months": 1}, format="json")

    entries = platform_api.get(
        f"/api/v1/platform/audit/?organization={org.slug}").json()
    actions = {entry["action"] for entry in entries}
    A = PlatformAuditLog.Action
    for expected in (A.TENANT_CREATED, A.TENANT_SUSPENDED, A.TENANT_ACTIVATED,
                     A.PLAN_CHANGED, A.BRANDING_CHANGED,
                     A.SUBSCRIPTION_EXTENDED):
        assert expected in actions, f"{expected} was not recorded"


def test_an_audit_entry_names_who_did_it(platform_api, org, platform_user):
    platform_api.post(f"/api/v1/platform/organizations/{org.slug}/suspend/",
                      {"reason": "Non-payment, 3 reminders"}, format="json")
    entry = next(e for e in platform_api.get(
        f"/api/v1/platform/audit/?organization={org.slug}").json()
        if e["action"] == PlatformAuditLog.Action.TENANT_SUSPENDED)
    assert entry["actor_email"] == platform_user.email
    assert entry["note"] == "Non-payment, 3 reminders"
    assert entry["organization_slug"] == org.slug


def test_the_audit_trail_records_what_changed_not_just_that_it_changed(
        platform_api, org):
    platform_api.patch(f"/api/v1/platform/organizations/{org.slug}/",
                       {"name": "New Name"}, format="json")
    entry = next(e for e in platform_api.get(
        f"/api/v1/platform/audit/?organization={org.slug}").json()
        if e["action"] == PlatformAuditLog.Action.TENANT_UPDATED)
    assert entry["changes"]["name"] == {"from": "ABC School",
                                        "to": "New Name"}


def test_the_audit_trail_does_not_store_letterhead_bodies(platform_api, org):
    """A letterhead in an audit row makes the trail unreadable."""
    platform_api.patch(
        f"/api/v1/platform/organizations/{org.slug}/branding/",
        {"letterhead_header_html": "<p>" + "x" * 5000 + "</p>"},
        format="json")
    entry = next(e for e in platform_api.get(
        f"/api/v1/platform/audit/?organization={org.slug}").json()
        if e["action"] == PlatformAuditLog.Action.BRANDING_CHANGED)
    assert entry["changes"]["letterhead_header_html"] == "changed"


def test_an_audit_entry_cannot_be_edited_or_deleted(org):
    """An audit trail somebody can edit is a narrative, not evidence."""
    entry = PlatformAuditLog.objects.filter(organization=org).first()
    assert entry is not None

    entry.note = "nothing to see here"
    with pytest.raises(ValueError):
        entry.save()
    with pytest.raises(ValueError):
        entry.delete()


def test_the_platform_trail_survives_the_tenant_being_removed(org):
    """The platform's record of what it did must outlive the tenant.

    Cancelling a customer and purging their data must not take the evidence
    of the cancellation with it -- hence SET_NULL and the denormalised slug.
    """
    entry = PlatformAuditLog.objects.filter(organization=org).first()
    slug = entry.organization_slug
    assert slug == org.slug

    PlatformAuditLog.objects.filter(organization=org).update(organization=None)
    entry.refresh_from_db()
    assert entry.organization_id is None
    assert entry.organization_slug == slug, (
        "the entry no longer names which customer it was about")


def test_a_failed_audit_write_does_not_block_the_action(platform_api, org,
                                                        monkeypatch):
    """Losing one audit row is bad; being unable to suspend is worse."""
    from tenancy.models import PlatformAuditLog

    def explode(*args, **kwargs):
        raise RuntimeError("audit table unavailable")

    monkeypatch.setattr(PlatformAuditLog.objects, "create", explode,
                        raising=False)
    response = platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/suspend/",
        {"reason": "Non-payment"}, format="json")
    assert response.status_code == 200
    org.refresh_from_db()
    assert org.is_admitted is False


# ---------------------------------------------------------------------------
# Phase S6.5: export, archive and restore over HTTP
# ---------------------------------------------------------------------------
def test_an_operator_exports_a_tenant_in_one_request(platform_api, org):
    """201 with a finished receipt, not an accepted job.

    Synchronous on purpose -- see `tenancy.export` -- so the operator who
    pressed the button learns the outcome rather than being handed a row to
    poll and a status that might never change.
    """
    response = platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/exports/", {},
        format="json")
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["status"] == "ready", body
    assert body["row_count"] > 0
    assert len(body["sha256"]) == 64
    assert body["is_downloadable"] is True
    # The per-table summary, which is what makes the receipt readable.
    assert body["tables"]["leaves.LeaveType"] == 4


def test_the_bundle_downloads_with_its_checksum_in_a_header(platform_api, org):
    export_id = platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/exports/", {},
        format="json").json()["id"]

    response = platform_api.get(
        f"/api/v1/platform/organizations/{org.slug}/exports/{export_id}"
        f"/download/")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip" or \
        response["Content-Disposition"].startswith("attachment")
    # So a customer can verify the copy they received against the receipt
    # without a second request.
    assert len(response["X-Export-SHA256"]) == 64
    assert b"".join(response.streaming_content)[:2] == b"PK"


def test_the_manifest_has_its_own_endpoint(platform_api, org):
    export_id = platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/exports/", {},
        format="json").json()["id"]

    response = platform_api.get(
        f"/api/v1/platform/organizations/{org.slug}/exports/{export_id}"
        f"/manifest/")
    assert response.status_code == 200
    manifest = response.json()
    assert manifest["integrity"]["dangling_references"] == []
    assert manifest["organization"]["slug"] == org.slug


def test_one_tenants_export_cannot_be_fetched_through_another(platform_api,
                                                               org, nif):
    """The id is a uuid, but the route is slug-addressed, and both must agree.

    Otherwise an operator with a bundle id could pull it through any
    customer's URL, and the audit entry would name the wrong tenant.
    """
    export_id = platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/exports/", {},
        format="json").json()["id"]

    response = platform_api.get(
        f"/api/v1/platform/organizations/{nif.slug}/exports/{export_id}"
        f"/download/")
    assert response.status_code == 400, response.content[:200]


def test_a_failed_export_cannot_be_downloaded(platform_api, org, monkeypatch):
    from tenancy import export as export_module

    monkeypatch.setattr(export_module, "_build",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("disk full")))
    body = platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/exports/", {},
        format="json").json()
    assert body["status"] == "failed"
    assert body["is_downloadable"] is False

    response = platform_api.get(
        f"/api/v1/platform/organizations/{org.slug}/exports/{body['id']}"
        f"/download/")
    assert response.status_code == 400
    assert "failed" in str(response.json()).lower()


def test_a_bundle_is_not_reachable_through_the_media_view(platform_api, org,
                                                           client):
    """The hardening Phase S6.5 added, tested from the outside.

    The media view is AllowAny and treats a signature as the credential,
    which is right for an avatar and catastrophic for a file containing a
    whole customer's workspace. Export bundles live under `platform/`, which
    the signer and the view now both refuse -- so there is no signature to
    obtain in the first place.
    """
    from documents.protected_media import signed_media_url

    platform_api.post(f"/api/v1/platform/organizations/{org.slug}/exports/",
                      {}, format="json")
    from tenancy.models import TenantExport

    stored = TenantExport.objects.get(organization=org).file.name
    assert stored.startswith("platform/")
    assert signed_media_url(stored, organization=org) is None, (
        "the signer minted a public link to a tenant export bundle")

    # And a hand-made request for the path is refused at the serving end too.
    response = client.get(f"/api/v1/media/?p={stored}&e=9999999999&s=x")
    assert response.status_code == 403


def test_archiving_and_restoring_over_http(platform_api, org):
    archive_response = platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/archive/",
        {"reason": "Customer closed"}, format="json")
    assert archive_response.status_code == 200, archive_response.json()
    body = archive_response.json()
    assert body["organization"]["status"] == "archived"
    assert body["archive"]["is_archived"] is True
    assert body["archive"]["can_restore"] is True

    restore_response = platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/restore/",
        {"note": "Customer returned"}, format="json")
    assert restore_response.status_code == 200, restore_response.json()
    restored = restore_response.json()
    assert restored["organization"]["status"] == "trial"
    assert restored["archive"]["is_archived"] is False


def test_an_archive_over_http_requires_a_reason(platform_api, org):
    response = platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/archive/",
        {"reason": "  "}, format="json")
    assert response.status_code == 400


def test_the_console_refuses_to_change_an_archived_tenant_over_http(
        platform_api, org):
    platform_api.post(f"/api/v1/platform/organizations/{org.slug}/archive/",
                      {"reason": "Closed"}, format="json")

    response = platform_api.post(
        f"/api/v1/platform/organizations/{org.slug}/activate/", {},
        format="json")
    assert response.status_code == 400
    assert "archived" in str(response.json()).lower()


def test_tenant_health_over_http_carries_the_s6_5_sections(platform_api, org):
    response = platform_api.get(
        f"/api/v1/platform/organizations/{org.slug}/health/")
    assert response.status_code == 200
    body = response.json()
    for section in ("storage", "pending_drift", "subscription_state",
                    "archive_state", "export_readiness"):
        assert section in body, section
    assert body["export_readiness"]["can_export"] is True
