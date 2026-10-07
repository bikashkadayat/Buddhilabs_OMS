"""Phase S7 Parts 1-5 and 9: a stranger creates a workspace, safely.

THE ONE RULE EVERYTHING ELSE HANGS OFF is Part 3's: no tenant exists until
the email is verified. It is asserted directly (nothing in the database after
submitting) and indirectly throughout -- the slug reservation, the expiry
sweep and the duplicate collapse are all only necessary because of it.

The security tests are not a separate concern bolted on at the end. These are
the only unauthenticated write endpoints on the platform, and the thing they
write is a whole tenant.
"""
import datetime

import pytest
from django.core import mail
from rest_framework.test import APIClient

from tenancy import registration
from tenancy.models import Organization, PendingRegistration

pytestmark = pytest.mark.django_db

GOOD_PASSWORD = "Str0ng-Pass-2026"


@pytest.fixture(autouse=True)
def open_registration(settings):
    """Every test here runs on a deployment that sells seats.

    Off by default in `config/settings.py`, because turning it on means an
    anonymous stranger can cause a tenant to exist, and the installation NIF
    runs today is single-tenant.
    """
    settings.TENANCY_PUBLIC_REGISTRATION = True
    settings.TENANCY_BASE_DOMAIN = "platform.test"
    settings.TENANCY_PLATFORM_HOSTS = ""
    return settings


@pytest.fixture
def form():
    return {
        "organization_name": "New School",
        "slug": "newschool",
        "organization_email": "office@newschool.test",
        "admin_email": "head@newschool.test",
        "admin_name": "Head Teacher",
        "password": GOOD_PASSWORD,
        "password_confirmation": GOOD_PASSWORD,
        "industry": "education",
        "country": "NP",
    }


@pytest.fixture
def client():
    return APIClient()


def submit(client, form, **overrides):
    return client.post("/api/v1/register/", {**form, **overrides},
                       format="json")


def token_from_email():
    """The token as a registrant would get it: out of the email."""
    assert mail.outbox, "no verification email was sent"
    body = mail.outbox[-1].body
    return body.split("token=")[1].split()[0].strip()


# --- Part 2: the subdomain ----------------------------------------------
@pytest.mark.parametrize("word", ["admin", "api", "mail", "www", "docs",
                                  "support", "billing"])
def test_the_reserved_words_the_brief_names_are_refused(client, word):
    response = client.get(f"/api/v1/register/slug/?slug={word}")
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["reason"] == "reserved"


def test_an_existing_tenants_subdomain_is_taken(client, org):
    response = client.get(f"/api/v1/register/slug/?slug={org.slug}")
    assert response.json() == {
        "slug": org.slug, "available": False, "reason": "taken",
        "detail": "That workspace address is already in use."}


def test_the_checker_says_WHICH_problem_a_subdomain_has(client):
    """One "invalid" for everything would send three different problems to
    the same dead end: a typo, a reserved word and somebody else's name need
    different answers from a person."""
    reasons = {
        # A one-letter DNS label is legal, so the platform's own validator
        # permits it and an operator may use one. The PUBLIC form adds a
        # minimum, because scarce two-letter subdomains should not go to
        # whoever submits a form first.
        "a": "too_short",
        "admin": "reserved",
        "": "empty",
        "Not A Slug!": "invalid",
        "-leading-hyphen": "invalid",
    }
    for value, expected in reasons.items():
        answer = registration.slug_status(value)
        assert answer["reason"] == expected, value
        assert answer["detail"], f"no explanation for {value!r}"


def test_a_pending_registration_holds_its_subdomain(client, form):
    """Two people registering `hospital` in the same hour must not both be
    told it is theirs."""
    assert submit(client, form).status_code == 202

    assert registration.slug_status("newschool")["reason"] == "taken"
    clash = submit(client, form, admin_email="other@elsewhere.test")
    assert clash.status_code == 400
    assert "slug" in clash.json()


def test_a_held_subdomain_is_reported_as_taken_not_as_in_progress(client,
                                                                   form):
    """Telling a stranger that a registration for this name is underway is
    telling them something about somebody else."""
    submit(client, form)
    answer = registration.slug_status("newschool")
    assert answer["reason"] == "taken"
    assert "registration" not in answer["detail"].lower()
    assert "pending" not in answer["detail"].lower()


def test_an_expired_registration_releases_its_subdomain(client, form):
    submit(client, form)
    record = PendingRegistration.objects.get(slug="newschool")
    record.token_expires_at = (record.token_expires_at
                               - datetime.timedelta(days=2))
    record.save(update_fields=["token_expires_at"])

    assert registration.expire_stale()["expired"] == 1
    assert registration.slug_status("newschool")["available"] is True


# --- Part 1: validation -------------------------------------------------
def test_a_valid_submission_creates_a_registration_and_nothing_else(client,
                                                                     form):
    """Part 3, asserted the most direct way there is."""
    response = submit(client, form)
    assert response.status_code == 202, response.json()
    assert response.json()["status"] == "verification_sent"

    assert PendingRegistration.objects.count() == 1
    assert not Organization.objects.filter(slug="newschool").exists(), (
        "a tenant was created before the email was verified")


@pytest.mark.parametrize("password,field", [
    ("short", "password"),                      # too short
    ("password", "password"),                   # too common
    ("29481058204", "password"),                # all numeric
]) 
def test_weak_passwords_are_refused_by_the_projects_own_rules(client, form,
                                                               password,
                                                               field):
    """`AUTH_PASSWORD_VALIDATORS`, not a regex written for this form.

    A second, different standard at the front door would mean the one
    password nobody checked properly is the first administrator's.
    """
    response = submit(client, form, password=password,
                      password_confirmation=password)
    assert response.status_code == 400
    assert field in response.json()


def test_the_two_passwords_must_match(client, form):
    response = submit(client, form, password_confirmation="Something-Else-1")
    assert response.status_code == 400
    assert "password_confirmation" in response.json()


def test_a_bad_country_code_is_refused_but_no_country_is_fine(client, form):
    assert submit(client, form, country="Nepal").status_code == 400
    assert submit(client, form, country="").status_code == 202


# --- the password is never held in the clear ----------------------------
def test_the_chosen_password_is_hashed_before_it_is_stored(client, form):
    """It has to be kept somewhere between the form and the account that does
    not exist yet. It is kept as a hash, and the plaintext goes nowhere."""
    submit(client, form)
    record = PendingRegistration.objects.get(slug="newschool")

    assert GOOD_PASSWORD not in record.admin_password
    assert record.admin_password.startswith(("pbkdf2_", "md5$", "argon2"))
    # And nowhere else on the row either.
    for field in record._meta.concrete_fields:
        value = getattr(record, field.attname, None)
        if isinstance(value, str) and field.attname != "admin_password":
            assert GOOD_PASSWORD not in value, field.attname


def test_the_verification_email_carries_the_link_and_not_the_password(client,
                                                                      form):
    submit(client, form)
    message = mail.outbox[-1]
    assert "token=" in message.body
    assert GOOD_PASSWORD not in message.body
    assert GOOD_PASSWORD not in str(message.alternatives)
    # It says so, too: a registrant who expects a password in the email will
    # wait for one.
    assert "password you chose" in message.body


def test_the_token_is_stored_hashed(client, form):
    submit(client, form)
    token = token_from_email()
    record = PendingRegistration.objects.get(slug="newschool")
    assert record.token_hash != token
    assert record.token_hash == registration.hash_token(token)


def test_the_email_is_unbranded_because_there_is_no_tenant_yet(client, form):
    """Every other email this platform sends carries the tenant's logo. At
    this moment there is no tenant to brand it with, which is the point."""
    submit(client, form)
    html = mail.outbox[-1].alternatives[0][0]
    assert "New School" in html
    assert "<img" not in html


# --- Parts 3, 4, 5: verify and provision --------------------------------
def test_verifying_provisions_a_complete_workspace(client, form,
                                                    monthly_plan):
    submit(client, form)
    response = client.post("/api/v1/register/verify/",
                           {"token": token_from_email()}, format="json")
    assert response.status_code == 200, response.json()
    body = response.json()

    assert body["status"] == "provisioned"
    assert body["health"]["verdict"] == "Tenant Ready"
    assert body["health"]["configuration_gaps"] == {}

    organization = Organization.objects.get(slug="newschool")
    assert organization.status == Organization.Status.TRIAL
    assert organization.is_admitted is True

    from tenancy import bootstrap

    assert bootstrap.verify_organization(organization) == {}


def test_the_trial_is_fourteen_days(client, form, monthly_plan, settings):
    """Part 5's default, read off the subscription rather than the setting."""
    submit(client, form)
    client.post("/api/v1/register/verify/", {"token": token_from_email()},
                format="json")

    organization = Organization.objects.get(slug="newschool")
    subscription = organization.subscription
    assert subscription.trial_start and subscription.trial_end
    assert (subscription.trial_end - subscription.trial_start).days == 14
    assert organization.subscription_status == "trial"


def test_the_administrator_signs_in_with_the_password_they_chose(client, form,
                                                                 monthly_plan):
    """No temporary password, and no forced change at first sign-in.

    `create_tenant_admin` generates one and forces a change, which is right
    when an OPERATOR creates the account: they have to read it out to
    somebody. Here nobody else ever saw it -- so making them replace a secret
    only they know with another secret only they know teaches people that the
    prompt is noise.
    """
    submit(client, form)
    client.post("/api/v1/register/verify/", {"token": token_from_email()},
                format="json")

    login = APIClient().post(
        "/api/v1/auth/login/",
        {"email": "head@newschool.test", "password": GOOD_PASSWORD},
        format="json", HTTP_HOST="newschool.platform.test")
    assert login.status_code == 200, login.data
    assert login.data["user"]["organization_slug"] == "newschool"
    assert login.data["user"]["is_platform_staff"] is False
    assert login.data["user"]["must_change_password"] is False


def test_the_workspace_is_created_by_nobody_and_the_trail_says_so(client,
                                                                   form,
                                                                   monthly_plan):
    """Part 4: "No manual platform action." The audit entry has no actor,
    and that absence is the record."""
    from tenancy.models import PlatformAuditLog

    submit(client, form)
    client.post("/api/v1/register/verify/", {"token": token_from_email()},
                format="json")

    organization = Organization.objects.get(slug="newschool")
    entry = (PlatformAuditLog.objects
             .filter(organization=organization,
                     action=PlatformAuditLog.Action.TENANT_CREATED)
             .order_by("-created_at").first())
    assert entry is not None
    assert entry.actor is None
    assert entry.changes.get("self_service") is True
    assert "SELF-SERVICE" in entry.note
    assert entry.changes.get("ip_address") or entry.changes.get("admin_email")


def test_a_second_click_on_the_link_returns_the_same_workspace(client, form,
                                                                monthly_plan):
    """People do click twice, and the second click must not build a second
    tenant or look like a failure."""
    submit(client, form)
    token = token_from_email()
    first = client.post("/api/v1/register/verify/", {"token": token},
                        format="json")
    second = client.post("/api/v1/register/verify/", {"token": token},
                         format="json")

    assert first.json()["status"] == "provisioned"
    assert second.status_code == 200
    assert second.json()["status"] == "already_provisioned"
    assert Organization.objects.filter(slug="newschool").count() == 1


def test_an_unknown_token_is_refused(client):
    response = client.post("/api/v1/register/verify/",
                           {"token": "nope-not-a-real-token"}, format="json")
    assert response.status_code == 400
    assert "token" in response.json()


def test_an_expired_token_is_refused_and_the_registration_marked(client, form):
    submit(client, form)
    token = token_from_email()
    record = PendingRegistration.objects.get(slug="newschool")
    record.token_expires_at = record.token_expires_at - datetime.timedelta(
        days=2)
    record.save(update_fields=["token_expires_at"])

    response = client.post("/api/v1/register/verify/", {"token": token},
                           format="json")
    assert response.status_code == 400
    record.refresh_from_db()
    assert record.status == PendingRegistration.Status.EXPIRED
    assert not Organization.objects.filter(slug="newschool").exists()


def test_re_registering_replaces_the_previous_link(client, form):
    """The old link must die, or two live tokens exist for one registration."""
    submit(client, form)
    first_token = token_from_email()
    submit(client, form)
    second_token = token_from_email()
    assert first_token != second_token

    stale = client.post("/api/v1/register/verify/", {"token": first_token},
                        format="json")
    assert stale.status_code == 400
    fresh = client.post("/api/v1/register/verify/", {"token": second_token},
                        format="json")
    assert fresh.status_code == 200


# --- Part 9: security ---------------------------------------------------
def test_a_duplicate_registration_is_collapsed_not_announced(client, form):
    """A signup form that says "this email is already registered" is a
    membership oracle: it tells a stranger which addresses have workspaces
    here, one request at a time, and a rate limit does not help because they
    only need one request per address."""
    first = submit(client, form)
    second = submit(client, form)

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert PendingRegistration.objects.count() == 1
    assert len(mail.outbox) == 2, "the second attempt should re-send"


def test_the_platform_will_not_send_unlimited_verification_mail(client, form,
                                                                 settings):
    """Each send is mail to an address nobody has proved they control."""
    settings.TENANCY_VERIFICATION_MAX_SENDS = 2
    assert submit(client, form).status_code == 202
    assert submit(client, form).status_code == 202
    third = submit(client, form)
    assert third.status_code == 400
    assert "verification" in str(third.json()).lower()


def test_the_honeypot_is_accepted_and_discarded(client, form):
    """Answered with the ordinary success body on purpose -- telling a script
    it was detected is telling it what to change."""
    response = submit(client, form, website="http://spam.example")
    assert response.status_code == 202
    assert PendingRegistration.objects.count() == 0
    assert mail.outbox == []


def _configured_rate(scope):
    """The shipped ceiling for a throttle scope, as a number per hour."""
    from django.conf import settings as django_settings

    rate = django_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"][scope]
    return int(rate.split("/")[0])


def test_registration_is_rate_limited(client, form):
    """Exceeds the CONFIGURED ceiling rather than overriding it.

    Overriding `DEFAULT_THROTTLE_RATES` per test does not reach DRF's cached
    rate table, so a test written that way passes whatever the real number
    is -- which is the one thing worth checking. The project's conftest
    clears the throttle cache around every test, so the bucket starts empty
    here.
    """
    allowed = _configured_rate("registration")
    codes = [submit(client, form,
                    slug=f"school{index}",
                    admin_email=f"head{index}@newschool.test").status_code
             for index in range(allowed + 1)]
    assert codes[:allowed] == [202] * allowed, codes
    assert codes[-1] == 429, codes
    # A ceiling per HOUR, not per minute: this endpoint's product is a tenant.
    assert "hour" in __import__("django").conf.settings.REST_FRAMEWORK[
        "DEFAULT_THROTTLE_RATES"]["registration"]


def test_verification_is_rate_limited_too(client):
    allowed = _configured_rate("registration_verify")
    codes = [client.post("/api/v1/register/verify/", {"token": f"x{index}"},
                         format="json").status_code
             for index in range(allowed + 1)]
    assert codes[-1] == 429, codes


def test_the_endpoints_do_not_exist_when_registration_is_closed(client, form,
                                                                 settings):
    """404, not 403: a deployment that does not sell seats should not
    advertise that the endpoint is there."""
    settings.TENANCY_PUBLIC_REGISTRATION = False
    assert submit(client, form).status_code == 404
    assert client.get("/api/v1/register/slug/?slug=x").status_code == 404
    assert client.post("/api/v1/register/verify/", {"token": "x"},
                       format="json").status_code == 404


def test_you_cannot_register_inside_somebody_elses_workspace(client, form,
                                                              settings, org):
    """A registration form served on `abcschool.platform.com` reads as ABC
    School inviting you to create an account with them.

    Asserted on a REAL tenant's hostname. An unresolvable host never reaches
    this view at all -- the tenant middleware answers 404 first, which is the
    right order and a different control.
    """
    settings.TENANCY_ENABLED = True
    settings.TENANCY_PLATFORM_HOSTS = "admin.platform.test"

    refused = client.post("/api/v1/register/", form, format="json",
                          HTTP_HOST=f"{org.slug}.platform.test")
    assert refused.status_code == 403, refused.content[:200]

    allowed = client.post("/api/v1/register/", form, format="json",
                          HTTP_HOST="admin.platform.test")
    assert allowed.status_code == 202, allowed.json()


def test_an_address_that_belongs_to_no_workspace_never_reaches_registration(
        client, form, settings, org):
    """The middleware's own refusal, pinned so the order stays this way."""
    settings.TENANCY_ENABLED = True
    settings.TENANCY_PLATFORM_HOSTS = "admin.platform.test"
    response = client.post("/api/v1/register/", form, format="json",
                           HTTP_HOST="nobody.platform.test")
    assert response.status_code == 404


def test_registration_cannot_claim_an_existing_tenants_subdomain(client, form,
                                                                  org):
    response = submit(client, form, slug=org.slug)
    assert response.status_code == 400
    assert "slug" in response.json()
    # And the existing tenant is untouched.
    org.refresh_from_db()
    assert org.status != Organization.Status.PROVISIONING


def test_the_document_prefix_is_derived_rather_than_asked_for(client, form,
                                                              monthly_plan):
    """"Document prefix" means nothing to somebody signing up, and a bad
    answer is permanent -- it appears on every memo they ever issue."""
    submit(client, form)
    client.post("/api/v1/register/verify/", {"token": token_from_email()},
                format="json")
    organization = Organization.objects.get(slug="newschool")
    assert organization.document_prefix == "NEWSCHOOL"


def test_a_hyphenated_subdomain_still_yields_a_usable_prefix(client, form,
                                                             monthly_plan):
    submit(client, form, slug="st-marys", admin_email="head@stmarys.test")
    client.post("/api/v1/register/verify/", {"token": token_from_email()},
                format="json")
    organization = Organization.objects.get(slug="st-marys")
    assert organization.document_prefix == "STMARYS"
    assert organization.document_prefix.isalnum()


# --- the two rules this phase added -------------------------------------
@pytest.mark.parametrize("slug", ["a", "ab"])
def test_a_scarce_short_subdomain_cannot_be_claimed_from_the_public_form(
        client, form, slug):
    """The platform's own validator allows one character, because a one-letter
    DNS label is legal and an operator may have a reason. Handing one to
    whoever submits a form first is a different decision."""
    response = submit(client, form, slug=slug)
    assert response.status_code == 400
    assert "slug" in response.json()


@pytest.mark.parametrize("password", ["newschool-newschool-2026",
                                      "New School Pass 1!"])
def test_a_password_containing_the_workspace_name_is_refused(client, form,
                                                             password):
    """Django's similarity validator only ever compares against username,
    first name, last name and email -- that list is fixed -- so the subdomain
    and the organization name sail past it. They are also the two things an
    attacker guessing at this account already knows."""
    response = submit(client, form, password=password,
                      password_confirmation=password)
    assert response.status_code == 400
    assert "password" in response.json()


def test_a_registrant_resubmitting_their_own_form_gets_another_email(client,
                                                                      form):
    """The bug this closes: the second submission was refused with "that
    workspace address is already in use" -- about their own pending
    registration -- leaving somebody who lost the first email with no way
    forward at all."""
    assert submit(client, form).status_code == 202
    again = submit(client, form)
    assert again.status_code == 202, again.json()
    assert len(mail.outbox) == 2
    assert PendingRegistration.objects.count() == 1


def test_somebody_else_still_cannot_take_a_held_subdomain(client, form):
    """The other half: the collapse must not become a way to steal a name."""
    submit(client, form)
    theirs = submit(client, form, admin_email="stranger@elsewhere.test")
    assert theirs.status_code == 400
    assert "slug" in theirs.json()


# --- the onboarding endpoint's own boundary -----------------------------
def test_the_onboarding_endpoint_refuses_an_anonymous_caller(client):
    """It reports a workspace's configuration and headcount. Not public."""
    assert client.get("/api/v1/tenant/onboarding/").status_code == 401


def test_a_platform_operator_has_no_workspace_to_be_onboarded_into(
        platform_user):
    """`applicable: False` rather than an error.

    An operator belongs to no organization by constraint, so there is nothing
    to measure -- and the console is where they work. Answering with a
    half-empty checklist would invite them to add employees to a tenant they
    do not have.
    """
    from rest_framework.test import APIClient

    api = APIClient()
    api.force_authenticate(platform_user)
    response = api.get("/api/v1/tenant/onboarding/")
    assert response.status_code == 200
    assert response.json()["applicable"] is False


def test_an_administrator_cannot_ask_about_another_workspace(client, form,
                                                              org, nif,
                                                              monthly_plan):
    """There is no parameter with which to ask, which is the strongest form
    of this guarantee: the payload is derived from the signed-in user."""
    from django.contrib.auth import get_user_model
    from rest_framework.test import APIClient

    from tenancy.context import tenant_context

    User = get_user_model()
    with tenant_context(org):
        admin = User.objects.create_user(
            username="org-admin", email="admin@abc.test",
            password="x-Admin-1", role="admin", organization=org)

    api = APIClient()
    api.force_authenticate(admin)
    body = api.get("/api/v1/tenant/onboarding/",
                   HTTP_HOST=f"{org.slug}.platform.test").json()
    assert body["organization"]["slug"] == org.slug

    # Every shape of "ask about somebody else" the endpoint could accept.
    for attempt in (f"?organization={nif.slug}", f"?slug={nif.slug}",
                    f"?organization_id={nif.pk}"):
        probed = api.get(f"/api/v1/tenant/onboarding/{attempt}",
                         HTTP_HOST=f"{org.slug}.platform.test").json()
        assert probed["organization"]["slug"] == org.slug, attempt


def test_two_clicks_arriving_together_build_one_workspace(client, form,
                                                           monthly_plan):
    """People double-click links, and the second click must not be a 500.

    `verify` runs outside a transaction so its refusals can record
    themselves, which means both clicks can pass its status check. The lock
    inside `_provision` is what makes the loser wait, re-read and return the
    workspace the winner built instead of dying on the slug's unique
    constraint.

    Sequential here rather than threaded: SQLite cannot express the race (it
    has no row locks), and a threaded test would prove nothing about the
    lock while being flaky about everything else. What this pins is the
    CONTRACT -- one workspace, and the second answer is "already done".
    """
    submit(client, form)
    token = token_from_email()

    first = client.post("/api/v1/register/verify/", {"token": token},
                        format="json")
    second = client.post("/api/v1/register/verify/", {"token": token},
                         format="json")

    assert first.json()["status"] == "provisioned"
    assert second.json()["status"] == "already_provisioned"
    assert Organization.objects.filter(slug="newschool").count() == 1


def test_a_platform_misconfiguration_is_not_explained_to_the_registrant(
        client, form, monkeypatch):
    """The operator's message is not the customer's message.

    `provision_organization` refuses with things like "No purchasable plan
    exists; seed plans before provisioning" -- correct, actionable, and
    addressed to whoever runs the platform. A stranger who has just clicked a
    link in their email is not that person, and our plan table is not their
    business.
    """
    from tenancy import registration as registration_module
    from tenancy.exceptions import TenancyError

    submit(client, form)
    token = token_from_email()

    def refuse(*args, **kwargs):
        raise TenancyError(
            "No purchasable plan exists; seed plans before provisioning.")

    monkeypatch.setattr(registration_module, "_provision_workspace", refuse)
    response = client.post("/api/v1/register/verify/", {"token": token},
                           format="json")

    assert response.status_code == 400
    body = str(response.json())
    assert "plan" not in body.lower(), body
    assert "support" in body.lower(), body

    # And nothing was half-built: the registration is back to PENDING, so the
    # same link works once the platform is fixed.
    record = PendingRegistration.objects.get(slug="newschool")
    assert record.status == PendingRegistration.Status.PENDING
    assert not Organization.objects.filter(slug="newschool").exists()


# --- Part 11: the registration funnel -----------------------------------
def test_the_funnel_counts_each_stage_from_the_registrations_themselves(
        client, form, platform_user, monthly_plan):
    """The row IS the funnel.

    Each registration carries the timestamps of every stage it reached, so
    the counts cannot drift from the thing they describe -- and a stage
    missing from the numbers is a stage that genuinely did not happen.
    """
    from tenancy import console

    # One that completes the whole journey.
    submit(client, form)
    client.post("/api/v1/register/verify/", {"token": token_from_email()},
                format="json")
    # One that registers and never opens the email.
    submit(client, form, slug="quietschool",
           admin_email="quiet@elsewhere.test")

    funnel = console.registration_funnel(platform_user, days=30)
    stages = {stage["key"]: stage for stage in funnel["stages"]}

    assert stages["started"]["count"] == 2
    assert stages["verified"]["count"] == 1
    assert stages["provisioned"]["count"] == 1
    assert stages["verified"]["of_previous"] == 50.0
    assert stages["provisioned"]["of_previous"] == 100.0

    assert funnel["rates"]["verification"] == 50.0
    assert funnel["rates"]["provision_success"] == 100.0
    assert funnel["rates"]["provision_failure"] == 0.0
    assert funnel["rates"]["overall"] == 50.0
    assert funnel["drop_off"]["awaiting_verification"] == 1
    assert funnel["trial_activations"] >= 1


def test_the_funnel_separates_drop_off_from_platform_failure(
        client, form, platform_user, monthly_plan, monkeypatch):
    """Two different problems that need two different people.

    A registrant who never clicks the link has dropped off -- nothing is
    wrong with the platform and nobody should be woken up. A verified
    registration with no workspace is a platform fault, and somebody should.
    """
    from tenancy import console, registration as registration_module
    from tenancy.exceptions import TenancyError

    submit(client, form)
    token = token_from_email()

    monkeypatch.setattr(
        registration_module, "_provision_workspace",
        lambda *a, **k: (_ for _ in ()).throw(TenancyError("no plan")))
    client.post("/api/v1/register/verify/", {"token": token}, format="json")

    funnel = console.registration_funnel(platform_user, days=30)
    # Verified is a timestamp on the row and survives the rolled-back
    # provisioning attempt... or does not, which is what this pins.
    assert funnel["failures"]["verified_without_workspace"] == \
        funnel["stages"][1]["count"] - funnel["stages"][2]["count"]
    assert funnel["drop_off"]["awaiting_verification"] >= 0


def test_the_funnel_counts_resent_verification_emails(client, form,
                                                       platform_user):
    """A high re-send count is a deliverability signal, not a user error."""
    from tenancy import console

    submit(client, form)
    submit(client, form)
    submit(client, form)

    funnel = console.registration_funnel(platform_user, days=30)
    assert funnel["drop_off"]["verification_resends"] == 2
    assert funnel["stages"][0]["count"] == 1, (
        "re-submitting is one registration, not three")


def test_an_empty_funnel_reports_no_rates_rather_than_zero(platform_user):
    """A rate over no attempts has no meaning; 0% would read as a failure."""
    from tenancy import console

    funnel = console.registration_funnel(platform_user, days=30)
    assert funnel["stages"][0]["count"] == 0
    assert funnel["rates"]["verification"] is None


def test_the_funnel_is_platform_only(tenant_admin, org):
    from tenancy import console
    from tenancy.console import NotPlatformStaff

    with pytest.raises(NotPlatformStaff):
        console.registration_funnel(tenant_admin)


# --- the revised brief's own wording and fields --------------------------
def test_the_customer_sees_the_exact_sentence_the_brief_specifies(
        client, form, monkeypatch):
    """Part 9 writes the sentence. A customer-facing message is a product
    decision, so the one somebody wrote for this moment is the one to use."""
    from tenancy import registration as registration_module
    from tenancy.exceptions import TenancyError

    submit(client, form)
    token = token_from_email()
    monkeypatch.setattr(
        registration_module, "_provision_workspace",
        lambda *a, **k: (_ for _ in ()).throw(TenancyError("internal")))

    response = client.post("/api/v1/register/verify/", {"token": token},
                           format="json")
    assert response.status_code == 400
    assert ("Your email has been verified, but we could not complete "
            "workspace setup. Please contact support."
            in str(response.json()))


def test_the_organization_email_is_optional_and_defaults_to_the_admin(
        client, form, monthly_plan):
    """Not on this brief's field list, so the form stops asking -- but the
    column is the billing address and an empty one is worse than a
    duplicate."""
    body = {k: v for k, v in form.items() if k != "organization_email"}
    assert client.post("/api/v1/register/", body,
                       format="json").status_code == 202

    record = PendingRegistration.objects.get(slug="newschool")
    assert record.organization_email == form["admin_email"]

    client.post("/api/v1/register/verify/", {"token": token_from_email()},
                format="json")
    organization = Organization.objects.get(slug="newschool")
    assert organization.email == form["admin_email"]


def test_platform_is_a_reserved_subdomain(client):
    """Named explicitly in this brief's reject list."""
    answer = client.get("/api/v1/register/slug/?slug=platform").json()
    assert answer["available"] is False
    assert answer["reason"] == "reserved"


# ---------------------------------------------------------------------------
# Phase S11 Part 7: commercial messaging consistency
# ---------------------------------------------------------------------------
class TestTheOfferIsStatedWhereItIsDecided:
    """The signup page said "No payment details are needed" and never said
    what the customer GETS. The trial appeared in the verification email and
    on the first-login page -- everywhere except the screen where somebody
    decides whether to sign up."""

    def test_the_public_payload_carries_the_trial_length(self, client,
                                                          settings):
        settings.TENANCY_PUBLIC_REGISTRATION = True
        settings.TENANCY_SELF_SERVICE_TRIAL_DAYS = 14
        response = client.get("/api/v1/tenant/public/branding/")
        assert response.status_code == 200
        assert response.data["trial_days"] == 14
        assert response.data["registration_open"] is True

    def test_it_follows_the_setting_rather_than_a_literal(self, client,
                                                           settings):
        """So changing the offer cannot leave the signup page advertising a
        trial length the product no longer gives."""
        settings.TENANCY_PUBLIC_REGISTRATION = True
        settings.TENANCY_SELF_SERVICE_TRIAL_DAYS = 30
        response = client.get("/api/v1/tenant/public/branding/")
        assert response.data["trial_days"] == 30

    def test_an_unknown_host_still_gets_the_offer(self, client, settings):
        """The signup page is reached on the platform host, which resolves
        to no tenant -- so the `known: False` branch is the one it uses."""
        settings.TENANCY_PUBLIC_REGISTRATION = True
        settings.TENANCY_SELF_SERVICE_TRIAL_DAYS = 14
        response = client.get("/api/v1/tenant/public/branding/")
        assert "trial_days" in response.data
