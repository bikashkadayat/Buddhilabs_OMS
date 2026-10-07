"""Phase S10 Part 2: the eight attacks, from the attacker's side.

WHY THIS FILE EXISTS WHEN THE ISOLATION SUITES ALREADY PASS.

``test_phase_b_isolation``, ``test_phase_c_isolation``, ``test_rls`` and
``test_s3_isolation`` are written from the DEVELOPER's side: here is a
manager, here is what it filters. They prove the mechanisms work.

This file is written from the other side. Each test takes a capability a real
attacker would actually hold -- a valid session in tenant A, a signed URL
somebody emailed them, a plan code, a slug, a hostname -- and tries to turn it
into something it should not reach. The difference matters because the gaps
in this codebase have never been in the mechanism; they have been in the ONE
path that forgot to use it. Phase S9 found three of those.

EVERY TEST HERE ASSERTS A REFUSAL. A test in this file that starts passing
for the wrong reason -- because an endpoint moved, or a 404 replaced a 403 --
is a test that no longer proves anything, so each one also asserts that the
attacker's own legitimate equivalent SUCCEEDS. A refusal that refuses
everybody is not security, it is an outage.

RUN THIS UNDER ROW-LEVEL SECURITY. On SQLite there are no policies, so the
cross-tenant cases prove only that the ORM layer holds. The authoritative
run is `DJANGO_SETTINGS_MODULE=pg_rls_settings` as `nifn_app`
(NOSUPERUSER NOBYPASSRLS); `test_the_database_itself_is_the_last_line` below
is skipped anywhere else, and says so rather than passing quietly.

READING A TENANT'S ROWS TO CHECK AN ATTACK FAILED

Several tests below assert "the victim's row is still there" or "the count
did not change". Under row-level security those assertions MUST be made
inside the victim's own scope, because from outside it "the row was deleted"
and "I am not allowed to see the row" are the same answer -- so a check
written from no scope at all passes whether the attack succeeded or not.
`tenant_context(victim)` around the verification is therefore part of the
test's meaning, not boilerplate.

Likewise, a write the database REFUSES aborts the surrounding transaction in
PostgreSQL, so every expected-to-fail write is wrapped in its own
`transaction.atomic()` -- otherwise the assertion that follows it cannot run
at all and the test fails for the wrong reason.
"""
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from rest_framework.test import APIClient

from tenancy import domains, portal
from tenancy.context import no_tenant, tenant_context
from tenancy.exceptions import TenancyError
from tenancy.models import Organization, TenantDomain

pytestmark = pytest.mark.django_db

ON_POSTGRES = pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="row-level security is a PostgreSQL feature; on SQLite this would "
           "pass without proving anything")


# ---------------------------------------------------------------------------
# The cast: two tenants who do not know each other, and one operator.
# ---------------------------------------------------------------------------
@pytest.fixture
def victim(db, monthly_plan):
    from tenancy import services

    import datetime

    return services.provision_organization(
        name="Victim Hospital", slug="victim-hospital",
        document_prefix="VICH", email="admin@victim-hospital.org.np",
        plan=monthly_plan, today=datetime.date(2026, 6, 15))


@pytest.fixture
def attacker(db, monthly_plan):
    from tenancy import services

    import datetime

    return services.provision_organization(
        name="Attacker Traders", slug="attacker-traders",
        document_prefix="ATTR", email="admin@attacker-traders.com",
        plan=monthly_plan, today=datetime.date(2026, 6, 15))


def _admin_of(organization, username):
    with tenant_context(organization):
        return get_user_model().objects.create_user(
            username=username, email=f"{username}@{organization.slug}.test",
            password="x-Pentest-1", role="admin", organization=organization)


@pytest.fixture
def victim_admin(victim):
    return _admin_of(victim, "victim-admin")


@pytest.fixture
def attacker_admin(attacker):
    return _admin_of(attacker, "attacker-admin")


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


# ===========================================================================
# 1. CROSS-TENANT READS
# ===========================================================================
class TestCrossTenantReads:
    def test_the_attacker_cannot_read_the_victims_people(
            self, attacker_admin, victim, victim_admin):
        """The baseline. Everything else in this file is a way around it."""
        with tenant_context(attacker_admin.organization):
            emails = set(get_user_model().objects.values_list("email",
                                                              flat=True))
        assert victim_admin.email not in emails
        # ...and the attacker DOES see their own, so this is isolation and
        # not an empty queryset.
        assert attacker_admin.email in emails

    def test_a_primary_key_the_attacker_somehow_learned_is_not_a_key(
            self, attacker_admin, victim_admin):
        """Ids leak -- from a URL somebody pasted, a support ticket, an
        export. Knowing one must not be the same as being allowed to read
        it."""
        with tenant_context(attacker_admin.organization):
            assert not get_user_model().objects.filter(
                pk=victim_admin.pk).exists()

    def test_the_subscription_portal_answers_only_about_the_caller(
            self, attacker_admin, victim):
        """Phase S8's endpoints take no organization argument at all, so this
        checks the consequence: there is no parameter to point elsewhere."""
        state = portal.subscription_state(attacker_admin)
        assert state["organization"]["slug"] == "attacker-traders"
        client = _client(attacker_admin)
        spoofed = client.get(
            f"/api/v1/tenant/subscription/?organization={victim.slug}")
        assert spoofed.status_code == 200
        assert spoofed.data["organization"]["slug"] == "attacker-traders"

    def test_branding_is_the_callers_branding_however_it_is_asked_for(
            self, attacker_admin, victim, victim_admin):
        portal.update_branding(victim_admin, display_name="Victim Hospital",
                               color_primary="#047857")
        seen = portal.branding_for_member(attacker_admin)
        assert seen["organization"]["slug"] == "attacker-traders"
        assert seen["color_primary"] != "#047857"

    @ON_POSTGRES
    def test_the_database_itself_is_the_last_line(self, attacker, victim,
                                                  attacker_admin,
                                                  victim_admin):
        """RAW SQL, bypassing every manager, every queryset and every view.

        This is the question the ORM cannot answer about itself: if a future
        phase adds a `.raw()`, a `cursor.execute`, a reporting view or a
        library that writes its own SQL, is the data still separated? The
        answer has to come from PostgreSQL, not from Python.
        """
        from tenancy.keys import token_for                 # noqa: F401

        with tenant_context(attacker):
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM users_user")
                mine = cursor.fetchone()[0]
                cursor.execute(
                    "SELECT count(*) FROM users_user WHERE organization_id = %s",
                    [str(victim.pk)])
                theirs = cursor.fetchone()[0]
        assert theirs == 0, (
            "raw SQL inside tenant A returned tenant B's rows: the RLS "
            "policy is not covering users_user")
        assert mine >= 1, "the attacker cannot see their own rows either"


# ===========================================================================
# 2. CROSS-TENANT WRITES
# ===========================================================================
class TestCrossTenantWrites:
    def test_the_attacker_cannot_attach_a_row_to_the_victim(
            self, attacker, victim):
        """Phase S2's stamping guard, from the attacker's side: a row that
        NAMES another organization while the request belongs to this one."""
        from leaves.models import LeaveType

        with tenant_context(attacker):
            with pytest.raises(Exception) as caught:
                LeaveType.objects.create(
                    organization=victim, code="STOLEN", name="Stolen",
                    annual_quota=5)
            assert caught.value is not None

    def test_the_attacker_cannot_edit_the_victims_row_by_id(
            self, attacker, victim, victim_admin):
        """An UPDATE is a read followed by a write, and the read is where
        this stops -- which is why `update()` on a filtered queryset
        matters: it never loads the row in Python at all."""
        with tenant_context(attacker):
            changed = get_user_model().objects.filter(
                pk=victim_admin.pk).update(role="employee")
        assert changed == 0
        with tenant_context(victim):
            victim_admin.refresh_from_db()
        assert victim_admin.role == "admin"

    def test_the_attacker_cannot_delete_the_victims_row_by_id(
            self, attacker, victim, victim_admin):
        with tenant_context(attacker):
            deleted, _ = get_user_model().objects.filter(
                pk=victim_admin.pk).delete()
        assert deleted == 0
        with tenant_context(victim):
            assert get_user_model().objects.filter(
                pk=victim_admin.pk).exists(), "the victim's row is gone"

    @ON_POSTGRES
    def test_the_attacker_cannot_move_their_own_row_into_the_victim(
            self, attacker, attacker_admin, victim):
        """The inside-out version, and the one a scoping filter alone CANNOT
        catch: the row is legitimately the attacker's, and they are changing
        whose it is. A read filter has nothing to refuse -- the row is
        visible, and the write is the attack.

        POSTGRES ONLY, and that is the finding rather than a caveat. What
        stops this is the RLS policy's `WITH CHECK` clause, which is
        evaluated against the row as it will be AFTER the update. Django's
        `pre_save` stamping guard covers the Phase A tables; `users_user` is
        not one of them, so on SQLite this write succeeds and nothing in
        Python objects. The database is the only thing standing here -- which
        is the argument for Phase S5 existing at all, stated as a test.
        """
        with tenant_context(attacker):
            attacker_admin.organization = victim
            with pytest.raises(Exception), transaction.atomic():
                attacker_admin.save(update_fields=["organization"])
        with tenant_context(attacker):
            attacker_admin.refresh_from_db()
        assert attacker_admin.organization_id == attacker.pk

    def test_an_employee_cannot_write_their_employers_branding(
            self, attacker):
        """Authority, not isolation -- and the two fail independently."""
        with tenant_context(attacker):
            clerk = get_user_model().objects.create_user(
                username="attacker-clerk", email="clerk@attacker.test",
                password="x-Pentest-1", role="maker", organization=attacker)
        with pytest.raises(portal.NotTenantAdmin):
            portal.update_branding(clerk, display_name="Mine")


# ===========================================================================
# 3. DOMAIN HIJACKING
# ===========================================================================
class TestDomainHijacking:
    @pytest.fixture(autouse=True)
    def _hosts(self, settings):
        settings.TENANCY_ENABLED = True
        settings.TENANCY_BASE_DOMAIN = "platform.test"
        settings.TENANCY_PLATFORM_HOSTS = "admin.platform.test"
        settings.ALLOWED_HOSTS = ["*"]

    # The attacker's own subdomain. The HTTP tests below go through it rather
    # than `testserver`, which with enforcement on resolves to no tenant --
    # so the request was refused by the middleware before reaching the view,
    # and the 404 that produced proved nothing about the view's own check.
    ATTACKER_HOST = "attacker-traders.platform.test"

    def test_claiming_a_hostname_does_not_make_it_resolve(self, attacker):
        """The whole point of Phase S9. Anybody may claim any name; a claim
        is a request to prove ownership, not a grant."""
        domains.claim(attacker, "www.victim-hospital.org.np")
        from tenancy import resolver

        assert resolver.resolve("www.victim-hospital.org.np") is None
        assert resolver.resolve_id("www.victim-hospital.org.np") is None

    def test_an_unverified_claim_is_not_an_allowed_host_either(self,
                                                               attacker):
        """So it cannot even reach the resolver to be refused by it."""
        from django.http.request import validate_host

        from tenancy.allowed_hosts import DynamicAllowedHosts

        domains.claim(attacker, "www.victim-hospital.org.np")
        allowed = DynamicAllowedHosts([".platform.test"])
        assert validate_host("www.victim-hospital.org.np", allowed) is False

    def test_the_attacker_cannot_take_a_name_the_victim_holds(
            self, attacker, victim):
        domains.claim(victim, "hr.victim-hospital.org.np")
        with pytest.raises(domains.InvalidDomain):
            domains.claim(attacker, "hr.victim-hospital.org.np")

    def test_and_the_refusal_does_not_say_who_holds_it(self, attacker,
                                                        victim):
        """Otherwise this endpoint is a directory of who is on the
        platform, queryable one hostname at a time."""
        domains.claim(victim, "hr.victim-hospital.org.np")
        with pytest.raises(domains.InvalidDomain) as caught:
            domains.claim(attacker, "hr.victim-hospital.org.np")
        message = str(caught.value).lower()
        assert "victim" not in message
        assert victim.name.lower() not in message

    def test_the_attacker_cannot_force_a_verification_on_it(
            self, attacker_admin, victim, monkeypatch):
        """The HTTP surface: the verify endpoint resolves the domain WITHIN
        the caller's organization, so another tenant's hostname is not a
        thing the URL can address."""
        domains.claim(victim, "hr.victim-hospital.org.np")
        # DNS would agree if it were asked -- the refusal must come first.
        monkeypatch.setattr(
            domains, "lookup",
            lambda name, rtype: [TenantDomain.objects.get(
                hostname="hr.victim-hospital.org.np")
                .expected_record_value(platform_host="admin.platform.test")
                .lower()])
        response = _client(attacker_admin).post(
            "/api/v1/tenant/domains/hr.victim-hospital.org.np/", {},
            format="json", HTTP_HOST=self.ATTACKER_HOST)
        assert response.status_code == 400, response.status_code
        held = TenantDomain.objects.get(hostname="hr.victim-hospital.org.np")
        assert held.status == TenantDomain.Status.PENDING
        assert held.check_count == 0, "the attacker triggered a DNS lookup"

    def test_the_attacker_cannot_withdraw_it(self, attacker_admin, victim,
                                              monkeypatch):
        domain = domains.claim(victim, "hr.victim-hospital.org.np")
        expected = domain.expected_record_value(
            platform_host="admin.platform.test")
        monkeypatch.setattr(domains, "lookup", lambda n, t: [expected.lower()])
        domains.verify(domain)

        response = _client(attacker_admin).delete(
            "/api/v1/tenant/domains/hr.victim-hospital.org.np/",
            HTTP_HOST=self.ATTACKER_HOST)
        assert response.status_code == 400, response.status_code
        assert domains.organization_for_host(
            "hr.victim-hospital.org.np").pk == victim.pk

    def test_nobody_can_claim_the_platforms_own_hostnames(self, attacker):
        """A customer who proved ownership of the console's hostname could
        serve the console from their own workspace."""
        for reserved in ("platform.test", "admin.platform.test",
                         "victim-hospital.platform.test",
                         "attacker-traders.platform.test"):
            with pytest.raises(domains.InvalidDomain):
                domains.claim(attacker, reserved)

    def test_a_suffix_that_merely_LOOKS_like_a_verified_name_is_refused(
            self, attacker, victim, monkeypatch):
        """`hr.victim-hospital.org.np.attacker.com` is the attacker's own
        domain and resolves to nothing here."""
        domain = domains.claim(victim, "hr.victim-hospital.org.np")
        expected = domain.expected_record_value(
            platform_host="admin.platform.test")
        monkeypatch.setattr(domains, "lookup", lambda n, t: [expected.lower()])
        domains.verify(domain)

        from django.http.request import validate_host

        from tenancy.allowed_hosts import DynamicAllowedHosts

        allowed = DynamicAllowedHosts([".platform.test"])
        assert validate_host(
            "hr.victim-hospital.org.np.attacker.com", allowed) is False
        assert domains.organization_for_host(
            "hr.victim-hospital.org.np.attacker.com") is None


# ===========================================================================
# 4. JWT MANIPULATION
# ===========================================================================
class TestJWTManipulation:
    @pytest.fixture(autouse=True)
    def _hosts(self, settings):
        settings.TENANCY_ENABLED = True
        settings.TENANCY_BASE_DOMAIN = "platform.test"
        settings.TENANCY_PLATFORM_HOSTS = "admin.platform.test"

    @staticmethod
    def _bind(organization):
        from tenancy import context

        return context.set_current_org(organization)

    def test_a_valid_token_replayed_on_another_tenants_host_is_refused(
            self, attacker_admin, victim):
        """THE ATTACK THIS PRODUCT IS MOST EXPOSED TO, because the token is
        genuine. The attacker signs in legitimately and sends the token to
        the victim's subdomain.

        Note what is NOT trusted: there is no org claim in the token being
        compared. The check reads `user.organization_id` from the database
        against the organization the HOST resolved to, so forging a claim
        achieves nothing -- there is nothing to forge.
        """
        from rest_framework.exceptions import AuthenticationFailed

        from tenancy.authentication import TenantJWTAuthentication

        token = self._bind(victim)
        try:
            with pytest.raises(AuthenticationFailed) as caught:
                TenantJWTAuthentication()._refuse_wrong_tenant(attacker_admin)
            assert caught.value.detail["code"] == "wrong_workspace"
        finally:
            from tenancy import context

            context.reset_current_org(token)

    def test_a_platform_token_is_refused_inside_any_workspace(
            self, platform_user, victim):
        """An operator's token is the most valuable one on the platform, so
        it is also the one that must not work anywhere a tenant lives."""
        from rest_framework.exceptions import AuthenticationFailed

        from tenancy.authentication import TenantJWTAuthentication

        token = self._bind(victim)
        try:
            with pytest.raises(AuthenticationFailed) as caught:
                TenantJWTAuthentication()._refuse_wrong_tenant(platform_user)
            assert caught.value.detail["code"] == "platform_account_on_tenant_host"
        finally:
            from tenancy import context

            context.reset_current_org(token)

    def test_a_tenant_token_is_refused_on_the_console_host(
            self, attacker_admin):
        """Served would mean an UNSCOPED session, which is worse than a
        refusal: a tenant user with no tenant bound."""
        from rest_framework.exceptions import AuthenticationFailed

        from tenancy.authentication import TenantJWTAuthentication

        with no_tenant():
            with pytest.raises(AuthenticationFailed) as caught:
                TenantJWTAuthentication()._refuse_wrong_tenant(attacker_admin)
        assert caught.value.detail["code"] == "no_workspace"

    def test_a_tampered_signature_is_rejected_before_any_of_that(
            self, attacker_admin):
        """The ordinary half, asserted so the interesting half above cannot
        be the only thing standing between a forged token and a session."""
        from rest_framework_simplejwt.exceptions import TokenError
        from rest_framework_simplejwt.tokens import AccessToken

        issued = str(AccessToken.for_user(attacker_admin))
        head, payload, signature = issued.split(".")
        forged = f"{head}.{payload}.{signature[:-4]}AAAA"
        with pytest.raises(TokenError):
            AccessToken(forged)

    def test_a_token_naming_a_user_that_does_not_exist_is_rejected(self):
        """Which is also what must happen to a DISMISSED employee's token
        before it expires: the account is the authority, not the token."""
        from rest_framework.exceptions import AuthenticationFailed
        from rest_framework_simplejwt.tokens import AccessToken

        from tenancy.authentication import TenantJWTAuthentication

        token = AccessToken()
        token["user_id"] = str(uuid.uuid4())
        with pytest.raises(AuthenticationFailed) as caught:
            TenantJWTAuthentication().get_user(token)
        assert caught.value.detail["code"] == "user_not_found"

    def test_a_deactivated_account_cannot_keep_using_its_token(
            self, attacker_admin):
        from rest_framework.exceptions import AuthenticationFailed
        from rest_framework_simplejwt.tokens import AccessToken

        from tenancy.authentication import TenantJWTAuthentication

        issued = AccessToken.for_user(attacker_admin)
        # `tenant_context`, not `set_current_org`: this one reaches the
        # DATABASE, and `get_user` reads a row. With only the context
        # variable bound, row-level security hides every tenant row and the
        # answer is "user not found" -- still a refusal, but not this one.
        with tenant_context(attacker_admin.organization):
            attacker_admin.is_active = False
            attacker_admin.save(update_fields=["is_active"])

            with pytest.raises(AuthenticationFailed) as caught:
                TenantJWTAuthentication().get_user(issued)
        assert caught.value.detail["code"] == "user_inactive"


# ===========================================================================
# 5. SIGNED URL ABUSE
# ===========================================================================
class TestSignedUrlAbuse:
    def test_a_link_for_the_victims_file_cannot_be_minted_inside_the_attacker(
            self, attacker, victim):
        """Phase S3 binds a media link to a tenant at BOTH ends. This is the
        signing end: the attacker holds a path -- from an export manifest, a
        log, a pasted URL -- and asks for a link to it."""
        from documents.protected_media import signed_media_url
        from tenancy.keys import token_for

        victim_path = f"org/{token_for(victim.pk)}/documents/secret.pdf"
        with tenant_context(attacker):
            assert signed_media_url(victim_path) is None

    def test_and_the_victim_can_mint_one_for_their_own(self, victim):
        """So the refusal above is a tenant check, not a broken signer."""
        from documents.protected_media import signed_media_url
        from tenancy.keys import token_for

        own = f"org/{token_for(victim.pk)}/documents/secret.pdf"
        with tenant_context(victim):
            assert signed_media_url(own) is not None

    def test_a_forged_signature_is_refused_at_the_serving_end(self, client,
                                                               victim):
        from tenancy.keys import token_for

        path = f"org/{token_for(victim.pk)}/documents/secret.pdf"
        response = client.get("/api/v1/media/", {
            "p": path, "e": "99999999999", "s": "0" * 64,
            "o": token_for(victim.pk)})
        assert response.status_code in (403, 404)

    def test_an_expired_link_stops_working(self, victim):
        """Checked at the SERVING end, because the expiry is in the query
        string and therefore under the attacker's control -- which is why it
        is covered by the signature."""
        import time

        from django.test import Client

        from documents.protected_media import _sign
        from tenancy.keys import token_for

        path = f"org/{token_for(victim.pk)}/documents/secret.pdf"
        past = int(time.time()) - 10
        signature = _sign(path, past, org=token_for(victim.pk))
        response = Client().get("/api/v1/media/", {
            "p": path, "e": past, "s": signature, "o": token_for(victim.pk)})
        assert response.status_code in (403, 404)

    def test_moving_the_expiry_forward_invalidates_the_signature(self,
                                                                  victim):
        """The obvious next move after the test above."""
        import time

        from django.test import Client

        from documents.protected_media import _sign
        from tenancy.keys import token_for

        path = f"org/{token_for(victim.pk)}/documents/secret.pdf"
        past = int(time.time()) - 10
        signature = _sign(path, past, org=token_for(victim.pk))
        response = Client().get("/api/v1/media/", {
            "p": path, "e": int(time.time()) + 3600, "s": signature,
            "o": token_for(victim.pk)})
        assert response.status_code in (403, 404)

    def test_the_platform_media_prefix_is_unreachable_from_a_tenant_link(
            self, attacker):
        """Payment proofs and export bundles live outside `org/`. A tenant
        must not be able to sign its way in."""
        from documents.protected_media import signed_media_url
        from tenancy.uploads import PLATFORM_PREFIX

        with tenant_context(attacker):
            assert signed_media_url(
                f"{PLATFORM_PREFIX}payments/receipt.pdf") is None

    def test_a_path_traversal_out_of_the_tenant_prefix_is_refused(self,
                                                                   attacker,
                                                                   victim):
        """`org/<mine>/../<theirs>/secret.pdf` passes the tenant check by
        itself, because that reads the FIRST path segment and the traversal
        is in the third.

        REFUSED AT BOTH ENDS, and it matters which end found it. The serving
        view has rejected `..` since Phase 11, so the attack always failed;
        the SIGNER did not, so it minted a link that then 403'd. Phase S10
        gave both ends the same check -- an asymmetry like that is how the
        Phase S9 branding bug happened, where one end normalised a token and
        the other did not.
        """
        import time

        from django.test import Client

        from documents.protected_media import _sign, signed_media_url
        from tenancy.keys import token_for

        escape = (f"org/{token_for(attacker.pk)}/../"
                  f"{token_for(victim.pk)}/documents/secret.pdf")
        with tenant_context(attacker):
            assert signed_media_url(escape) is None, "the signer minted it"

        # And even with a VALID signature -- which an attacker who learned
        # SECRET_KEY would have, and which this test constructs directly --
        # the serving end still refuses the path.
        exp = int(time.time()) + 3600
        served = Client().get("/api/v1/media/", {
            "p": escape, "e": exp,
            "s": _sign(escape, exp, org=token_for(attacker.pk)),
            "o": token_for(attacker.pk)})
        assert served.status_code in (403, 404)


# ===========================================================================
# 6. EXPORT ABUSE
# ===========================================================================
class TestExportAbuse:
    def test_a_tenant_cannot_ask_for_an_export_at_all(self, attacker_admin,
                                                       attacker):
        """Phase S6.5 put export on the CONSOLE, not in the product. A
        tenant administrator -- the most privileged tenant account -- has no
        endpoint for it, which is why there is nothing here to scope."""
        client = _client(attacker_admin)
        for path in (f"/api/v1/platform/organizations/{attacker.slug}/exports/",
                     "/api/v1/platform/organizations/"):
            assert client.get(path).status_code in (401, 403), path

    def test_a_tenant_cannot_export_the_victim(self, attacker_admin, victim):
        client = _client(attacker_admin)
        response = client.post(
            f"/api/v1/platform/organizations/{victim.slug}/exports/", {},
            format="json")
        assert response.status_code in (401, 403)

    def test_a_tenant_cannot_download_a_bundle_by_guessing_its_id(
            self, attacker_admin, victim, platform_user):
        from tenancy import console

        with no_tenant():
            receipt = console.create_export(platform_user, victim)
        client = _client(attacker_admin)
        response = client.get(
            f"/api/v1/platform/organizations/{victim.slug}/exports/"
            f"{receipt.pk}/download/")
        assert response.status_code in (401, 403)

    def test_an_operator_cannot_reach_one_tenants_bundle_through_another(
            self, platform_user, victim, attacker):
        """The id is a UUID, but the URL carries a slug as well, and the two
        must have to agree -- otherwise the slug is decoration and the id is
        the only authorisation."""
        from tenancy import console

        with no_tenant():
            receipt = console.create_export(platform_user, victim)
        client = _client(platform_user)
        response = client.get(
            f"/api/v1/platform/organizations/{attacker.slug}/exports/"
            f"{receipt.pk}/download/")
        assert response.status_code in (400, 403, 404)

    def test_a_bundle_contains_exactly_one_tenant(self, platform_user, victim,
                                                   attacker, attacker_admin):
        """The integrity claim S6.5 makes, checked adversarially: a second
        tenant exists and has data, so a leak has something to leak."""
        from tenancy import console

        with no_tenant():
            receipt = console.create_export(platform_user, victim)
        assert receipt.manifest["integrity"]["cross_tenant_rows"] == []
        assert receipt.manifest["integrity"]["dangling_references"] == []

        import zipfile

        with receipt.file.open("rb") as handle:
            with zipfile.ZipFile(handle) as bundle:
                blob = b"".join(
                    bundle.read(name) for name in bundle.namelist()
                    if name.endswith(".json")).decode("utf-8", "replace")
        assert str(attacker.pk) not in blob
        assert attacker_admin.email not in blob
        assert str(victim.pk) in blob


# ===========================================================================
# 7. ARCHIVE ABUSE
# ===========================================================================
class TestArchiveAbuse:
    def test_a_tenant_cannot_archive_anybody_including_itself(
            self, attacker_admin, attacker, victim):
        client = _client(attacker_admin)
        for slug in (attacker.slug, victim.slug):
            response = client.post(
                f"/api/v1/platform/organizations/{slug}/archive/",
                {"reason": "x"}, format="json")
            assert response.status_code in (401, 403), slug

    def test_a_tenant_cannot_restore_anybody(self, attacker_admin, victim,
                                              platform_user):
        from tenancy import archive

        with no_tenant():
            archive.archive_organization(victim, actor=platform_user,
                                         reason="support request")
        response = _client(attacker_admin).post(
            f"/api/v1/platform/organizations/{victim.slug}/restore/", {},
            format="json")
        assert response.status_code in (401, 403)
        victim.refresh_from_db()
        assert victim.status == Organization.Status.ARCHIVED

    def test_an_archived_tenants_own_administrator_cannot_reopen_it(
            self, victim, victim_admin, platform_user):
        """Archiving is a platform decision -- usually non-payment -- so the
        customer must not be able to undo it. The gate is the console's, and
        the customer has no console."""
        from tenancy import archive

        with no_tenant():
            archive.archive_organization(victim, actor=platform_user,
                                         reason="non-payment")
        response = _client(victim_admin).post(
            f"/api/v1/platform/organizations/{victim.slug}/restore/", {},
            format="json")
        assert response.status_code in (401, 403)

    def test_archiving_destroys_nothing(self, victim, victim_admin,
                                         platform_user):
        """Phase S6.5 Part 4 is "design only, do NOT permanently delete".
        The adversarial reading: archive must not be a delete with a nicer
        name, because that is the one operation no restore can undo."""
        from tenancy import archive

        with tenant_context(victim):
            before = get_user_model().objects.count()
        assert before >= 1, "the victim has no data, so this proves nothing"

        with no_tenant():
            archive.archive_organization(victim, actor=platform_user,
                                         reason="non-payment")
        with tenant_context(victim):
            assert get_user_model().objects.count() == before, (
                "archiving removed rows")

        with no_tenant():
            archive.restore_organization(victim, actor=platform_user)
            victim.refresh_from_db()
        assert victim.status != Organization.Status.ARCHIVED
        with tenant_context(victim):
            assert get_user_model().objects.count() == before


# ===========================================================================
# 8. CONSOLE ABUSE
# ===========================================================================
class TestConsoleAbuse:
    def test_every_console_route_refuses_a_tenant_administrator(
            self, attacker_admin, victim):
        """Swept rather than sampled: a console endpoint added without the
        mixin is exactly the kind of thing one spot-check misses."""
        client = _client(attacker_admin)
        paths = [
            "/api/v1/platform/dashboard/",
            "/api/v1/platform/health/",
            "/api/v1/platform/organizations/",
            "/api/v1/platform/plans/",
            "/api/v1/platform/audit/",
            "/api/v1/platform/payments/",
            "/api/v1/platform/launch-readiness/",
            "/api/v1/platform/registration-funnel/",
            "/api/v1/platform/domains/",
            "/api/v1/platform/events/",
            f"/api/v1/platform/organizations/{victim.slug}/",
            f"/api/v1/platform/organizations/{victim.slug}/usage/",
            f"/api/v1/platform/organizations/{victim.slug}/health/",
            f"/api/v1/platform/organizations/{victim.slug}/branding/",
            f"/api/v1/platform/organizations/{victim.slug}/settings/",
            f"/api/v1/platform/organizations/{victim.slug}/domains/",
        ]
        served = [p for p in paths
                  if client.get(p).status_code not in (401, 403, 404)]
        assert served == [], f"console routes reachable by a tenant: {served}"

    def test_every_console_route_refuses_an_anonymous_caller(self, client,
                                                             victim):
        paths = ["/api/v1/platform/dashboard/",
                 "/api/v1/platform/organizations/",
                 "/api/v1/platform/audit/",
                 f"/api/v1/platform/organizations/{victim.slug}/"]
        served = [p for p in paths
                  if client.get(p).status_code not in (401, 403, 404)]
        assert served == []

    def test_an_ordinary_employee_cannot_become_platform_staff(self,
                                                               attacker):
        """Privilege escalation by self-service: the flag that opens the
        console is paired with `organization IS NULL` by a database
        constraint, so setting it on a tenant user cannot produce a working
        operator."""
        from django.db.utils import IntegrityError

        with tenant_context(attacker):
            clerk = get_user_model().objects.create_user(
                username="climber", email="climber@attacker.test",
                password="x-Pentest-1", role="maker", organization=attacker)
            clerk.is_platform_staff = True
            with pytest.raises((IntegrityError, TenancyError, Exception)):
                clerk.save(update_fields=["is_platform_staff"])

    def test_the_platform_audit_trail_cannot_be_edited_or_erased(
            self, platform_user, victim):
        """An attacker who reached the console would go for the log first.
        It is append-only, so there is nothing to tidy up afterwards."""
        from tenancy import archive
        from tenancy.models import PlatformAuditLog

        with no_tenant():
            archive.archive_organization(victim, actor=platform_user,
                                         reason="pen test")
            entry = PlatformAuditLog.objects.filter(organization=victim).first()
            assert entry is not None
            with pytest.raises(Exception):
                entry.note = "nothing happened"
                entry.save(update_fields=["note"])
            with pytest.raises(Exception):
                entry.delete()

    def test_a_tenant_cannot_provision_itself_a_second_workspace(
            self, attacker_admin):
        """Seats are what the platform charges for, so "create another
        organization" is the most direct route around the bill."""
        response = _client(attacker_admin).post(
            "/api/v1/platform/organizations/",
            {"name": "Free Extra", "slug": "free-extra",
             "document_prefix": "FREE", "email": "x@free-extra.test"},
            format="json")
        assert response.status_code in (401, 403)
        assert not Organization.objects.filter(slug="free-extra").exists()
