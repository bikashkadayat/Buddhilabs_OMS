"""Phase S7 Part 10: four organizations onboard themselves, start to finish.

    School · Hospital · NGO · SME

"All must work without platform intervention" is the requirement, and the
only honest way to test it is to never touch the platform console in this
file. There is no `platform_user` fixture here and no `console.` call: if any
step needed an operator, these tests could not pass.

Each organization goes the whole way -- submit, verify, provision, sign in,
read its own onboarding payload, use its own API -- and the four run against
one database, so every assertion is also an isolation assertion.
"""
import pytest
from django.core import mail
from rest_framework.test import APIClient

from tenancy.context import tenant_context
from tenancy.models import Organization, PendingRegistration

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ng-Pass-2026"

# Where a stranger goes to sign up. Not a tenant subdomain: the workspace
# they are creating does not resolve until the moment it exists.
PLATFORM_HOST = "admin.platform.test"

APPLICANTS = [
    {"organization_name": "Sunrise School", "slug": "sunrise-school",
     "industry": "education", "country": "NP",
     "organization_email": "office@sunrise-school.test",
     "admin_email": "principal@sunrise-school.test",
     "admin_name": "Asha Principal"},
    {"organization_name": "Valley Hospital", "slug": "valley-hospital",
     "industry": "healthcare", "country": "NP",
     "organization_email": "admin@valley-hospital.test",
     "admin_email": "director@valley-hospital.test",
     "admin_name": "Bibek Director"},
    {"organization_name": "Himal NGO", "slug": "himal-ngo",
     "industry": "nonprofit", "country": "NP",
     "organization_email": "info@himal-ngo.test",
     "admin_email": "lead@himal-ngo.test",
     "admin_name": "Chandra Lead"},
    {"organization_name": "Everest Traders", "slug": "everest-traders",
     "industry": "retail", "country": "NP",
     "organization_email": "accounts@everest-traders.test",
     "admin_email": "owner@everest-traders.test",
     "admin_name": "Deepa Owner"},
]


@pytest.fixture(autouse=True)
def open_registration(settings):
    """The configuration a self-service deployment actually has.

    TENANCY_ENABLED MATTERS HERE, and leaving it off made this file lie. With
    enforcement off, an unresolved host falls back to "the one organization",
    and the resolver's warm cache answers NIF -- so a request addressed to
    `sunrise-school.platform.test` was served NIF's leave types, and the
    test that noticed looked like a product bug. With it on, the host is
    authoritative, which is the only way a platform that sells subdomains can
    work.
    """
    settings.TENANCY_ENABLED = True
    settings.TENANCY_PUBLIC_REGISTRATION = True
    settings.TENANCY_BASE_DOMAIN = "platform.test"
    # A PLATFORM HOST IS REQUIRED, not optional, once enforcement is on --
    # which is a finding about the deployment and not just about this test.
    # With it empty, every request must resolve to a tenant, so the signup
    # endpoints are reachable only on an existing customer's hostname (where
    # they read as that customer inviting signups) and nowhere else at all.
    # `tenancy.checks.W003` says so.
    settings.TENANCY_PLATFORM_HOSTS = PLATFORM_HOST
    return settings


@pytest.fixture
def onboarded(db, monthly_plan):
    """All four, each through the public endpoints only.

    Returns ``[(spec, organization, access_token)]``. The fixture itself is
    the conformance claim: no operator, no console, no shell.
    """
    results = []
    for index, spec in enumerate(APPLICANTS):
        client = APIClient()
        body = {**spec, "password": PASSWORD,
                "password_confirmation": PASSWORD}
        # EACH APPLICANT ARRIVES FROM ITS OWN ADDRESS, because four
        # organizations are four customers. The registration throttle is
        # 3/hour KEYED BY IP, so submitting all four from one address is
        # correctly refused -- the first version of this fixture was, and the
        # rate limit was right. Four addresses is what four customers look
        # like; the ceiling itself is tested in `test_registration.py`.
        address = f"203.0.113.{index + 10}"
        submitted = client.post("/api/v1/register/", body, format="json",
                                REMOTE_ADDR=address, HTTP_HOST=PLATFORM_HOST)
        assert submitted.status_code == 202, submitted.json()

        token = mail.outbox[-1].body.split("token=")[1].split()[0].strip()
        verified = client.post("/api/v1/register/verify/", {"token": token},
                               format="json", REMOTE_ADDR=address,
                               HTTP_HOST=PLATFORM_HOST)
        assert verified.status_code == 200, verified.json()

        organization = Organization.objects.get(slug=spec["slug"])
        login = APIClient().post(
            "/api/v1/auth/login/",
            {"email": spec["admin_email"], "password": PASSWORD},
            format="json", HTTP_HOST=f"{spec['slug']}.platform.test")
        assert login.status_code == 200, login.data
        results.append((spec, organization, login.data["access"]))
    return results


def as_tenant(spec, access):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    client.defaults["HTTP_HOST"] = f"{spec['slug']}.platform.test"
    return client


# --- the gate -----------------------------------------------------------
def test_all_four_onboarded_with_no_platform_involvement(onboarded):
    from tenancy.models import PlatformAuditLog

    assert len(onboarded) == 4
    for spec, organization, _ in onboarded:
        assert organization.status == Organization.Status.TRIAL
        assert organization.is_admitted is True
        # Not one audit entry for these tenants names an operator, because
        # not one operator was involved.
        actors = set(PlatformAuditLog.objects
                     .filter(organization=organization)
                     .values_list("actor_id", flat=True))
        assert actors == {None}, f"{spec['slug']} needed a human: {actors}"


def test_each_workspace_is_fully_configured(onboarded):
    """The S6 bootstrap, reached through the public door."""
    from tenancy import bootstrap

    for spec, organization, _ in onboarded:
        assert bootstrap.verify_organization(organization) == {}, spec["slug"]


def test_each_one_is_on_a_fourteen_day_trial(onboarded):
    for spec, organization, _ in onboarded:
        subscription = organization.subscription
        assert organization.subscription_status == "trial", spec["slug"]
        assert (subscription.trial_end - subscription.trial_start).days == 14


def test_the_industries_they_typed_are_kept(onboarded):
    """Collected by Part 1 and therefore worth storing -- a field a customer
    fills in and nobody reads is a field that should not be on the form."""
    for spec, organization, _ in onboarded:
        assert organization.industry == spec["industry"]
        assert organization.country == spec["country"]


# --- each one's own first login -----------------------------------------
def test_each_administrator_sees_their_own_onboarding_payload(onboarded):
    for spec, organization, access in onboarded:
        response = as_tenant(spec, access).get("/api/v1/tenant/onboarding/")
        assert response.status_code == 200, response.content[:200]
        body = response.json()

        assert body["applicable"] is True
        assert body["show_wizard"] is True
        assert body["organization"]["slug"] == spec["slug"]
        assert body["organization"]["name"] == spec["organization_name"]
        assert body["health"]["verdict"] == "Tenant Ready"
        assert body["subscription"]["is_trial"] is True
        # Part 6's "already works" list: everything the bootstrap provided.
        assert all(item["ready"] for item in body["ready"]), body["ready"]
        # The Getting Started steps (eight since the customer-success phase),
        # none of them done yet.
        assert len(body["checklist"]) == 8
        assert body["progress"] == {"completed": 0, "total": 8, "percent": 0}


def test_the_checklist_ticks_itself_as_the_work_is_actually_done(onboarded):
    """Measured from the tenant's own rows, not remembered.

    A checklist stored as five booleans starts lying the first time somebody
    undoes something -- to the person least able to tell, in their first hour.
    Every one of this brief's five steps leaves evidence behind, so every one
    of them is measured.
    """
    import datetime

    from django.contrib.auth import get_user_model

    from leaves.models import LeaveType
    from tasks.models import Task

    spec, organization, access = onboarded[0]
    client = as_tenant(spec, access)
    User = get_user_model()

    def checklist():
        return {step["key"]: step["done"]
                for step in client.get("/api/v1/tenant/onboarding/")
                .json()["checklist"]}

    assert checklist() == {"logo": False, "team": False,
                           "leave_policy": False, "attendance": False,
                           "first_task": False, "department": False,
                           "invite": False, "approve_leave": False}

    # Step 2: invite the team. More than one active user, because
    # provisioning created the administrator.
    with tenant_context(organization):
        staff = User.objects.create_user(
            username="teacher-1", email="teacher1@sunrise-school.test",
            password="x-Teacher-1", organization=organization)
    assert checklist()["team"] is True

    # Step 3: review the leave policy. Compared per code, so changing sick
    # leave to annual leave's seeded value still counts as a change.
    with tenant_context(organization):
        sick = LeaveType.objects.get(code="SICK")
        sick.default_days_per_year = 15
        sick.save(update_fields=["default_days_per_year"])
    assert checklist()["leave_policy"] is True

    # Step 4: review attendance.
    from attendance.policy.models import Shift

    with tenant_context(organization):
        shift = Shift.objects.get(code="general")
        shift.start_time = datetime.time(9, 30)
        shift.save(update_fields=["start_time"])
    assert checklist()["attendance"] is True

    # Step 5: create the first task.
    from tasks.services import generate_task_number

    with tenant_context(organization):
        Task.objects.create(title="First task", created_by=staff,
                            task_number=generate_task_number(),
                            due_date=datetime.date(2026, 8, 1))
    assert checklist()["first_task"] is True

    progress = client.get("/api/v1/tenant/onboarding/").json()["progress"]
    assert progress == {"completed": 4, "total": 8, "percent": 50}


def test_a_step_can_be_ticked_by_a_customer_who_does_not_need_it(onboarded):
    """A checklist that cannot be satisfied is one people learn to ignore.

    A customer who runs one shift and does not care about attendance rules
    should be able to put the step away. The tick can only ADD, never
    remove, so it cannot contradict the workspace.
    """
    spec, organization, access = onboarded[1]
    client = as_tenant(spec, access)

    ticked = client.post("/api/v1/tenant/onboarding/",
                         {"step_done": "attendance"}, format="json")
    assert ticked.status_code == 200
    assert {step["key"]: step["done"]
            for step in ticked.json()["checklist"]}["attendance"] is True
    # And the payload still says it was not MEASURED, so the console can tell
    # a tick from evidence.
    assert {step["key"]: step["measured"]
            for step in ticked.json()["checklist"]}["attendance"] is False


def test_an_unknown_step_is_refused(onboarded):
    spec, organization, access = onboarded[1]
    response = as_tenant(spec, access).post(
        "/api/v1/tenant/onboarding/", {"step_done": "not_a_step"},
        format="json")
    assert "not a checklist step" in response.json()["error"]


def test_dismissing_the_wizard_sticks(onboarded):
    spec, organization, access = onboarded[2]
    client = as_tenant(spec, access)

    dismissed = client.post("/api/v1/tenant/onboarding/",
                            {"dismissed": True}, format="json")
    assert dismissed.json()["show_wizard"] is False
    assert client.get("/api/v1/tenant/onboarding/").json()["show_wizard"] \
        is False


# --- isolation, with four self-registered tenants in one database -------
def test_one_administrator_cannot_see_another_organizations_onboarding(
        onboarded):
    """There is no id to pass, which is the strongest version of this: the
    payload is derived from the signed-in user's own organization."""
    for spec, organization, access in onboarded:
        body = as_tenant(spec, access).get(
            "/api/v1/tenant/onboarding/").json()
        assert body["organization"]["slug"] == spec["slug"]
        for other in APPLICANTS:
            if other["slug"] == spec["slug"]:
                continue
            assert other["organization_name"] not in str(body)


def test_each_new_tenant_sees_only_its_own_people(onboarded):
    for spec, organization, access in onboarded:
        response = as_tenant(spec, access).get("/api/v1/users/")
        assert response.status_code == 200
        rows = (response.json() if isinstance(response.json(), list)
                else response.json().get("results", []))
        assert len(rows) == 1, f"{spec['slug']} sees {len(rows)} users"
        assert rows[0]["email"] == spec["admin_email"]


def test_each_new_tenant_can_use_the_product_immediately(onboarded):
    """Leave types reachable through their own API -- the exact lookup that
    failed at the end of Phase S5, now reached by a customer who registered
    themselves."""
    for spec, organization, access in onboarded:
        response = as_tenant(spec, access).get("/api/v1/leave-types/")
        assert response.status_code == 200
        rows = (response.json() if isinstance(response.json(), list)
                else response.json().get("results", []))
        codes = {row["code"] for row in rows}
        assert {"ANNUAL", "SICK", "UNPAID", "SPECIAL"} <= codes, spec["slug"]


def test_their_document_numbers_do_not_collide(onboarded):
    """Four tenants, four prefixes, four independent sequences."""
    from memos.services import generate_memo_number

    numbers = []
    for spec, organization, _ in onboarded:
        with tenant_context(organization):
            numbers.append(generate_memo_number("administrative"))
    assert len(set(numbers)) == 4, numbers
    for spec, organization, _ in onboarded:
        assert any(number.startswith(organization.document_prefix)
                   for number in numbers)


def test_every_registration_is_traceable_to_the_workspace_it_became(
        onboarded):
    for spec, organization, _ in onboarded:
        record = PendingRegistration.objects.get(slug=spec["slug"])
        assert record.status == PendingRegistration.Status.PROVISIONED
        assert record.organization_id == organization.pk
        assert record.verified_at and record.provisioned_at
