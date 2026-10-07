"""Phase S9: white-label branding and custom domains.

WHAT THESE TESTS ARE FOR. Phase S6 gave a tenant a logo and two colours and
applied them to the login page only; `Organization.domain` had existed since
S1 as an unverified column an operator could type anything into. Both were
reported as done. So the assertions here are mostly about the GAP between
"the field exists" and "the field reaches the customer's screen" -- and
about the one that is a security property rather than a cosmetic one: a
hostname is a claim about something outside this platform, and nothing but
DNS can settle it.
"""
import pytest

from tenancy import domains, portal, resolver
from tenancy.exceptions import TenancyError
from tenancy.models import (OrganizationBranding, PlatformAuditLog,
                            TenantDomain)

pytestmark = pytest.mark.django_db

# The smallest valid PNG, so an upload test exercises real image handling
# without carrying a fixture file.
_ONE_PIXEL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae"
    "426082")


@pytest.fixture(autouse=True)
def _hosts(settings):
    settings.TENANCY_ENABLED = True
    settings.TENANCY_BASE_DOMAIN = "platform.test"
    settings.TENANCY_PLATFORM_HOSTS = "admin.platform.test"


# CREATED INSIDE `tenant_context`, and that is not decoration. Under
# row-level security an INSERT into `users_user` carrying `organization=org`
# while no tenant is bound is refused by the policy -- "new row violates
# row-level security policy" -- which is precisely what the policy is for.
# The first draft of these two fixtures omitted it, and the whole class
# errored on PostgreSQL while passing on SQLite, where there are no
# policies to violate.
@pytest.fixture
def abc_admin(db, django_user_model, org):
    from tenancy.context import tenant_context

    with tenant_context(org):
        return django_user_model.objects.create_user(
            username="abc-admin", email="admin@abcschool.edu.np",
            password="x-Tenant-1", role="admin", organization=org)


@pytest.fixture
def abc_employee(db, django_user_model, org):
    from tenancy.context import tenant_context

    with tenant_context(org):
        return django_user_model.objects.create_user(
            username="abc-staff", email="staff@abcschool.edu.np",
            password="x-Tenant-1", role="employee", organization=org)


def _verified(domain, *, actor=None, monkeypatch=None, host="admin.platform.test"):
    """Push a domain through verification with DNS answering correctly."""
    expected = domain.expected_record_value(platform_host=host)
    monkeypatch.setattr(domains, "lookup",
                        lambda name, rtype: [expected.lower()])
    return domains.verify(domain, actor=actor)


# ---------------------------------------------------------------------------
# Part 1: the customer owns their own branding
# ---------------------------------------------------------------------------
class TestBrandingCentre:
    def test_an_administrator_can_set_their_own_branding(self, abc_admin, org):
        result = portal.update_branding(
            abc_admin, display_name="ABC School", color_primary="#1d4ed8",
            dashboard_welcome="Welcome to ABC School")
        assert result["display_name"] == "ABC School"
        assert result["color_primary"] == "#1D4ED8"
        assert result["dashboard_welcome"] == "Welcome to ABC School"

    def test_an_employee_cannot(self, abc_employee):
        with pytest.raises(portal.NotTenantAdmin):
            portal.update_branding(abc_employee, display_name="Mine Now")

    def test_but_an_employee_can_READ_it(self, abc_employee, abc_admin):
        """R28 in one assertion.

        If only administrators could read branding, only administrators
        could see a themed application -- and the entire point is that every
        employee's workspace looks like their employer's.
        """
        portal.update_branding(abc_admin, color_primary="#7C3AED",
                               display_name="ABC School")
        seen = portal.branding_for_member(abc_employee)
        assert seen["applicable"] is True
        assert seen["color_primary"] == "#7C3AED"
        assert seen["display_name"] == "ABC School"

    def test_blank_display_name_falls_back_to_the_legal_name(self, abc_employee,
                                                             org):
        seen = portal.branding_for_member(abc_employee)
        assert seen["display_name"] == org.name

    def test_a_platform_operator_has_no_tenant_branding(self, platform_user):
        assert portal.branding_for_member(platform_user) == {"applicable": False}

    @pytest.mark.parametrize("bad", [
        "red",
        "#1d4ed",
        "#1d4ed88",
        "1d4ed8",
        # The ones that matter: a colour is interpolated into a CSS custom
        # property, so anything accepted here reaches a style attribute on
        # every page of the application.
        "#fff; background: url(javascript:alert(1))",
        "red;}body{display:none",
        "var(--x)",
        "#1d4ed8 ; --x: y",
    ])
    def test_a_colour_that_is_not_a_hex_colour_is_refused(self, abc_admin, bad):
        with pytest.raises(TenancyError):
            portal.update_branding(abc_admin, color_primary=bad)

    def test_a_blank_colour_clears_it(self, abc_admin):
        portal.update_branding(abc_admin, color_primary="#1D4ED8")
        result = portal.update_branding(abc_admin, color_primary="")
        assert result["color_primary"] == ""

    def test_an_asset_field_outside_the_allow_list_is_refused(self, abc_admin):
        """`setattr(branding, field, file)` with a client-supplied name is a
        write to any column on the row -- including the organization FK."""
        with pytest.raises(TenancyError):
            portal.set_branding_asset(abc_admin, "organization", None)
        with pytest.raises(TenancyError):
            portal.set_branding_asset(abc_admin, "id", None)

    def test_every_change_is_audited_against_the_tenant(self, abc_admin, org):
        portal.update_branding(abc_admin, display_name="ABC School")
        entry = (PlatformAuditLog.objects
                 .filter(action=PlatformAuditLog.Action.BRANDING_UPDATED)
                 .first())
        assert entry is not None
        assert entry.organization_id == org.pk
        assert entry.changes["display_name"] == "ABC School"
        # The actor is recorded even though they are not platform staff: the
        # log's `actor` FK is reserved for operators, so a customer's own
        # change is attributed in `changes` instead.
        assert entry.changes["by"] == abc_admin.email

    def test_branding_urls_outlive_a_working_session(self, settings):
        """R27. A signed media URL lasts 300 seconds by default, which is
        right for a document attachment and wrong for a logo fetched on
        every page -- a dashboard left open over lunch got a 403 where the
        brand should be."""
        assert settings.BRANDING_URL_TTL >= 3600
        assert settings.BRANDING_URL_TTL > settings.MEDIA_SIGNED_URL_TTL


# ---------------------------------------------------------------------------
# Part 2: the branding reaches the rest of the product
# ---------------------------------------------------------------------------
class TestBrandingReachesEverything:
    def test_emails_carry_the_tenants_name_and_logo(self, org, abc_admin):
        """Before S9 every notification said "Nepal Internet Foundation" and
        linked `/NIF.png` -- so a customer's employees were emailed in a
        stranger's name, which is the white-label failure a customer notices
        first."""
        from tenancy.context import tenant_context

        from notifications import emails

        OrganizationBranding.objects.update_or_create(
            organization=org, defaults={"display_name": "ABC School",
                                        "color_primary": "#1D4ED8"})
        with tenant_context(org):
            branding = emails._branding()
        assert branding["org_name"] == "ABC School"
        assert branding["brand_color"] == "#1D4ED8"

    def test_the_rendered_email_IS_the_tenants_colour(self, org, abc_admin):
        """Part 6, and the trap this phase keeps finding: a value that is
        computed and then discarded.

        `_branding()` has returned `brand_color` since S6 and the template
        hard-coded NIF's #274095 for the header band and the button -- the
        largest coloured area in the message -- so a customer's staff got
        their own logo on a stranger's blue. Asserting on `_branding()`
        alone would have passed throughout.
        """
        from django.template.loader import render_to_string

        from tenancy.context import tenant_context

        portal.update_branding(abc_admin, color_primary="#7C3AED",
                               display_name="ABC School")
        with tenant_context(org):
            from notifications import emails

            html = render_to_string("emails/generic.html", {
                **emails._branding(),
                "title": "A notification",
                "body": "Something happened.",
                "recipient_name": "Sita",
                "action_url": "https://example.test/x",
            })
        assert "#7C3AED" in html
        assert "#274095" not in html
        assert "ABC School" in html

    def test_a_SEMANTIC_colour_is_not_re_branded(self, org, abc_admin):
        """A rejection rendered in a customer's corporate green stops
        reading as a rejection. Branding changes what the product looks
        like, not what its signals mean -- the same rule the front end's
        palette follows."""
        from django.template.loader import render_to_string

        from tenancy.context import tenant_context

        portal.update_branding(abc_admin, color_primary="#7C3AED")
        with tenant_context(org):
            from notifications import emails

            html = render_to_string("emails/balance_alert.html", {
                **emails._branding(),
                "title": "Low balance",
                "body": "You have two days left.",
                "recipient_name": "Sita",
            })
        assert "#F59E0B" in html, "the warning amber must survive branding"

    def test_emails_fall_back_to_the_platform_outside_a_tenant(self):
        from notifications import emails

        branding = emails._branding()
        # The PLATFORM's own name, which is the fallback when no tenant is
        # in context. Brand migration: this was Nepal Internet Foundation.
        assert branding["org_name"] == "Buddhi Labs"

    def test_a_letterhead_carries_the_tenants_own_details(self, org):
        """Part 7. The logo was tenant-aware since S3; the NAME, ADDRESS and
        TELEPHONE NUMBER beside it came from one deployment-wide dict, so
        every tenant's memos went out with their logo above NIF's address."""
        from documents.pdf import org_letterhead

        org.address = "Lalitpur, Nepal"
        org.phone = "+977-1-5555555"
        org.email = "office@abcschool.edu.np"
        org.site_url = "https://abcschool.edu.np"
        org.save()
        OrganizationBranding.objects.update_or_create(
            organization=org, defaults={"display_name": "ABC School"})
        org.refresh_from_db()

        head = org_letterhead(org)
        assert head["name"] == "ABC School"
        assert head["address"] == "Lalitpur, Nepal"
        assert head["tel"] == "+977-1-5555555"
        assert head["email"] == "office@abcschool.edu.np"
        # And none of the PLATFORM operator's details leaked into it.
        assert "Buddhi Labs" not in str(head.values())

    def test_the_document_footer_is_the_customers_own_words(self, org,
                                                            abc_admin):
        """`report_footer_text` has been on the model since Phase S1 and
        was collected by the console and PRINTED NOWHERE -- the branding
        editor describes it as "printed at the foot of PDFs", which was
        untrue. It replaces the assembled contact line rather than joining
        it, because a customer who writes their own footer is usually
        saying something else: a registration number, or "Confidential"."""
        from documents.pdf import org_letterhead

        portal.update_branding(
            abc_admin,
            report_footer_text="Confidential - ABC School internal use only")
        head = org_letterhead(org)
        assert head["footer_text"] == (
            "Confidential - ABC School internal use only")

    def test_the_footer_falls_back_to_the_contact_line(self, org):
        from documents.pdf import org_letterhead

        org.address = "Lalitpur, Nepal"
        org.phone = "+977-1-5555555"
        org.save()
        org.refresh_from_db()
        footer = org_letterhead(org)["footer_text"]
        assert "Lalitpur, Nepal" in footer
        assert "+977-1-5555555" in footer

    def test_a_footer_cannot_end_the_css_string_it_lands_in(self, org,
                                                             abc_admin):
        """The value is interpolated into ``content: "..."`` in the PDF
        stylesheet. Django's HTML autoescaping is worse than useless there
        -- it would turn a quotation mark into `&quot;`, which CSS does not
        decode -- and a raw quote would end the string and break the whole
        @page rule, so every page of every document would lose its footer.
        """
        from documents.pdf import org_letterhead

        portal.update_branding(
            abc_admin,
            report_footer_text='Say "hello" \\ and goodbye')
        footer = org_letterhead(org)["footer_text"]
        assert '"' not in footer
        assert "\\" not in footer
        assert "hello" in footer

    def test_a_letterhead_falls_back_for_the_platforms_own_deployment(self):
        from documents.pdf import org_letterhead

        assert org_letterhead(None).get("name")

    def test_replacing_a_logo_does_not_keep_printing_the_old_one(self, org,
                                                                 abc_admin):
        """The PDF letterhead logo is cached per tenant for the life of the
        process, and until Phase S9 nothing evicted it -- so a customer who
        uploaded a new logo kept getting the old one on every document until
        a restart. Across several workers that is worse than consistently
        stale: some documents carried the new mark and some the old, which
        reads as a printing fault rather than a bug."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        from documents import pdf
        from tenancy.keys import token_for

        pdf._LOGO_CACHE[token_for(org.pk)] = "data:image/png;base64,STALE"
        portal.set_branding_asset(
            abc_admin, "logo_letterhead",
            SimpleUploadedFile("logo.png", _ONE_PIXEL_PNG,
                               content_type="image/png"))
        assert token_for(org.pk) not in pdf._LOGO_CACHE

    def test_an_operators_upload_evicts_it_too(self, org, platform_user):
        """The console has its own branding writer. Two writers, one cache,
        and only one of them evicting is how this comes back."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        from documents import pdf
        from tenancy import console
        from tenancy.keys import token_for

        pdf._LOGO_CACHE[token_for(org.pk)] = "data:image/png;base64,STALE"
        console.set_branding_asset(
            platform_user, org, "logo_letterhead",
            SimpleUploadedFile("logo.png", _ONE_PIXEL_PNG,
                               content_type="image/png"))
        assert token_for(org.pk) not in pdf._LOGO_CACHE

    def test_a_letterhead_is_read_from_the_database_not_a_cached_instance(
            self, org, abc_admin):
        """`organization.branding` is a reverse one-to-one Django caches on
        the instance, and provisioning creates the row through that
        instance. An organization object held since before a branding change
        therefore answered with the OLD name -- and a PDF rendered from it
        went out under the previous letterhead."""
        from documents.pdf import org_letterhead

        stale = org                                 # as provisioning left it
        portal.update_branding(abc_admin, display_name="ABC School Kathmandu")
        assert org_letterhead(stale)["name"] == "ABC School Kathmandu"


# ---------------------------------------------------------------------------
# Parts 3 and 4: a hostname is proved, not asserted
# ---------------------------------------------------------------------------
class TestDomainClaim:
    def test_claiming_resolves_nothing(self, org, platform_user):
        domains.claim(org, "hr.abcschool.edu.np", actor=platform_user)
        assert resolver.resolve("hr.abcschool.edu.np") is None

    def test_verification_is_what_makes_it_resolve(self, org, platform_user,
                                                   monkeypatch):
        domain = domains.claim(org, "hr.abcschool.edu.np", actor=platform_user)
        domain = _verified(domain, actor=platform_user, monkeypatch=monkeypatch)
        assert domain.status == TenantDomain.Status.ACTIVE
        assert domain.verified_at is not None
        assert domain.is_primary is True
        found = resolver.resolve("hr.abcschool.edu.np")
        assert found is not None and found.pk == org.pk

    def test_a_missing_record_fails_and_says_why(self, org, platform_user,
                                                 monkeypatch):
        domain = domains.claim(org, "hr.abcschool.edu.np")
        monkeypatch.setattr(domains, "lookup", lambda name, rtype: [])
        domain = domains.verify(domain, actor=platform_user)
        assert domain.status == TenantDomain.Status.FAILED
        assert "Nothing found" in domain.last_error
        assert resolver.resolve("hr.abcschool.edu.np") is None

    def test_a_wrong_record_reports_what_was_actually_there(self, org,
                                                            monkeypatch):
        domain = domains.claim(org, "hr.abcschool.edu.np")
        monkeypatch.setattr(domains, "lookup",
                            lambda name, rtype: ["some-other-value"])
        domain = domains.verify(domain)
        assert domain.status == TenantDomain.Status.FAILED
        assert "some-other-value" in domain.last_error

    def test_an_unavailable_resolver_is_NOT_a_failed_domain(self, org,
                                                            monkeypatch):
        """The distinction this test exists for: "we looked and it is not
        there" and "we could not look" must never be collapsed, or somebody
        spends an afternoon re-checking a zone file that was right."""
        domain = domains.claim(org, "hr.abcschool.edu.np")

        def _unavailable(name, rtype):
            raise domains.VerificationUnavailable("no resolver here")

        monkeypatch.setattr(domains, "lookup", _unavailable)
        with pytest.raises(domains.VerificationUnavailable):
            domains.verify(domain)
        domain.refresh_from_db()
        assert domain.status == TenantDomain.Status.PENDING
        assert domain.check_count == 1
        assert "no resolver" in domain.last_error

    def test_one_token_per_domain_not_per_tenant(self, org):
        """Publishing a record on one subdomain proves nothing about
        another: a customer who can write on `hr.` may not be able to write
        on `portal.`, and an attacker who controls one they own certainly
        cannot write on one they do not."""
        a = domains.claim(org, "hr.abcschool.edu.np")
        b = domains.claim(org, "portal.abcschool.edu.np")
        assert a.verification_token != b.verification_token

    def test_proving_one_domain_does_not_activate_the_other(self, org,
                                                            monkeypatch):
        a = domains.claim(org, "hr.abcschool.edu.np")
        domains.claim(org, "portal.abcschool.edu.np")
        _verified(a, monkeypatch=monkeypatch)
        assert resolver.resolve("portal.abcschool.edu.np") is None

    @pytest.mark.parametrize("bad", [
        "", "not a hostname", "nodot", "-leading.example.com",
        "a..b.example.com", "x" * 260 + ".com",
        "under_score.example.com", ".example.com", "hr..com",
    ])
    def test_a_malformed_hostname_is_refused(self, org, bad):
        with pytest.raises(domains.InvalidDomain):
            domains.claim(org, bad)

    @pytest.mark.parametrize("reserved", [
        "platform.test", "admin.platform.test", "abcschool.platform.test",
        "anything.platform.test",
    ])
    def test_the_platforms_own_names_cannot_be_claimed(self, org, reserved):
        """A customer who proved ownership of the platform's own domain
        could serve the console's hostname from their own workspace."""
        with pytest.raises(domains.InvalidDomain):
            domains.claim(org, reserved)

    @pytest.mark.parametrize("pasted", [
        "  HTTPS://HR.AbcSchool.Edu.Np.  ",
        "http://hr.abcschool.edu.np/login",
        "hr.abcschool.edu.np:8000",
        "hr.abcschool.edu.np/",
        "HR.ABCSCHOOL.EDU.NP",
    ])
    def test_a_pasted_url_is_normalised_rather_than_refused(self, org, pasted):
        """Somebody copying the address out of their browser bar brings the
        scheme, the port and the path with it. Refusing that would be
        technically correct and would read as "your domain is invalid"."""
        assert domains.claim(org, pasted).hostname == "hr.abcschool.edu.np"

    def test_another_tenants_hostname_is_refused_without_saying_why(
            self, org, nif, monkeypatch):
        """The message must be the same whoever asks. "Another customer has
        that domain" turns this endpoint into a list of who is on the
        platform."""
        domains.claim(org, "hr.abcschool.edu.np")
        with pytest.raises(domains.InvalidDomain) as caught:
            domains.claim(nif, "hr.abcschool.edu.np")
        message = str(caught.value)
        assert "not available" in message
        assert "abcschool" not in message.lower()
        assert org.name not in message

    def test_re_claiming_your_own_withdrawn_domain_issues_a_new_token(
            self, org, monkeypatch):
        domain = domains.claim(org, "hr.abcschool.edu.np")
        first = domain.verification_token
        _verified(domain, monkeypatch=monkeypatch)
        domains.remove(domain)
        again = domains.claim(org, "hr.abcschool.edu.np")
        assert again.pk == domain.pk
        assert again.status == TenantDomain.Status.PENDING
        assert again.verification_token != first

    def test_withdrawing_stops_resolution_immediately(self, org, monkeypatch):
        domain = domains.claim(org, "hr.abcschool.edu.np")
        _verified(domain, monkeypatch=monkeypatch)
        assert resolver.resolve("hr.abcschool.edu.np") is not None
        domains.remove(domain)
        assert resolver.resolve("hr.abcschool.edu.np") is None

    def test_the_instructions_name_BOTH_records(self, org):
        """Every support conversation about custom domains is somebody who
        published the verification record and expected traffic to arrive."""
        domain = domains.claim(org, "hr.abcschool.edu.np")
        sheet = domains.instructions(domain)
        assert sheet["record_type"] == "TXT"
        assert domain.verification_token in sheet["record_value"]
        assert sheet["serving_record"]["name"] == "hr.abcschool.edu.np"
        assert sheet["serving_record"]["value"] == "admin.platform.test"
        assert sheet["record_name"] != sheet["serving_record"]["name"]

    def test_a_cname_claim_asks_for_a_cname(self, org):
        domain = domains.claim(org, "hr.abcschool.edu.np",
                               method=TenantDomain.Method.CNAME)
        assert domains.instructions(domain)["record_type"] == "CNAME"

    def test_claims_and_verifications_are_audited(self, org, platform_user,
                                                  monkeypatch):
        domain = domains.claim(org, "hr.abcschool.edu.np", actor=platform_user)
        _verified(domain, actor=platform_user, monkeypatch=monkeypatch)
        actions = set(PlatformAuditLog.objects
                      .filter(organization=org)
                      .values_list("action", flat=True))
        assert PlatformAuditLog.Action.DOMAIN_CLAIMED in actions
        assert PlatformAuditLog.Action.DOMAIN_VERIFIED in actions


# ---------------------------------------------------------------------------
# Part 5: both addresses work, and neither reaches the wrong tenant
# ---------------------------------------------------------------------------
class TestMultiDomainResolution:
    def test_the_subdomain_keeps_working_after_a_custom_domain_is_added(
            self, org, monkeypatch):
        domain = domains.claim(org, "hr.abcschool.edu.np")
        _verified(domain, monkeypatch=monkeypatch)
        by_custom = resolver.resolve("hr.abcschool.edu.np")
        by_slug = resolver.resolve(f"{org.slug}.platform.test")
        assert by_custom.pk == by_slug.pk == org.pk

    def test_two_tenants_two_domains_no_crossing(self, org, nif, monkeypatch):
        a = domains.claim(org, "hr.abcschool.edu.np")
        b = domains.claim(nif, "portal.nif.org.np")
        _verified(a, monkeypatch=monkeypatch)
        _verified(b, monkeypatch=monkeypatch)
        assert resolver.resolve("hr.abcschool.edu.np").pk == org.pk
        assert resolver.resolve("portal.nif.org.np").pk == nif.pk

    def test_an_unknown_hostname_resolves_to_nothing(self):
        assert resolver.resolve("hr.someone-elses-company.com") is None

    def test_the_resolver_cache_does_not_outlive_a_withdrawal(self, org,
                                                              monkeypatch):
        """The cache is keyed by hostname, so a withdrawal has to evict the
        hostname rather than the slug -- otherwise a domain taken away for
        cause keeps resolving until an entry expires."""
        domain = domains.claim(org, "hr.abcschool.edu.np")
        _verified(domain, monkeypatch=monkeypatch)
        resolver.resolve("hr.abcschool.edu.np")      # populate the cache
        domains.remove(domain)
        assert resolver.resolve("hr.abcschool.edu.np") is None


# ---------------------------------------------------------------------------
# Part 5, on the path a real request actually takes
# ---------------------------------------------------------------------------
class TestTheMiddlewarePathResolvesCustomDomains:
    """WHY THIS CLASS EXISTS, written down because it is the lesson.

    The resolver has TWO entry points. `resolve()` returns the row and is
    what every other test in this file used. `resolve_id()` returns only the
    id, needs no row fetch when warm, and is what `TenantResolutionMiddleware`
    calls -- so it is the only one that runs on a real request.

    S9 taught `resolve()` about custom domains and not `resolve_id()`. Every
    resolution test passed, and a customer could have claimed a hostname,
    published the record, watched it verify, and found that it did not work:
    the feature was complete and unreachable. Nothing in a green suite said
    so, because nothing in the suite used the path the product uses.
    """

    def test_a_verified_domain_resolves_through_the_MIDDLEWARE_resolver(
            self, org, monkeypatch):
        domain = domains.claim(org, "hr.abcschool.edu.np")
        _verified(domain, monkeypatch=monkeypatch)
        assert resolver.resolve_id("hr.abcschool.edu.np") == str(org.pk)

    def test_an_unverified_claim_does_not(self, org):
        domains.claim(org, "hr.abcschool.edu.np")
        assert resolver.resolve_id("hr.abcschool.edu.np") is None

    def test_a_withdrawn_domain_stops_resolving_there_too(self, org,
                                                           monkeypatch):
        domain = domains.claim(org, "hr.abcschool.edu.np")
        _verified(domain, monkeypatch=monkeypatch)
        resolver.resolve_id("hr.abcschool.edu.np")        # warm the cache
        domains.remove(domain)
        assert resolver.resolve_id("hr.abcschool.edu.np") is None

    def test_the_warm_path_costs_no_query_at_all(self, org, monkeypatch):
        """The caching this depends on. A custom hostname matches neither
        `Organization.domain` nor a slug, so without encoding the hostname
        it was resolved for, every warm request would re-query
        `TenantDomain` -- giving straight back the saving the guard makes."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        domain = domains.claim(org, "hr.abcschool.edu.np")
        _verified(domain, monkeypatch=monkeypatch)
        resolver.resolve_id("hr.abcschool.edu.np")        # cold
        with CaptureQueriesContext(connection) as captured:
            assert resolver.resolve_id("hr.abcschool.edu.np") == str(org.pk)
        assert len(captured) == 0, (
            f"warm resolution should cost nothing, ran {len(captured)}")

    def test_a_platform_subdomain_is_never_looked_up_as_a_custom_domain(
            self, org):
        """The free half of the guard. Nothing under the base domain can be
        a custom domain -- `validate()` refuses to let anybody claim one --
        so the question needs no query."""
        assert resolver.custom_domains_possible(
            f"{org.slug}.platform.test") is False
        assert resolver.custom_domains_possible("platform.test") is False
        assert resolver.custom_domains_possible("hr.abcschool.edu.np") is False

    def test_a_deployment_with_no_custom_domain_pays_nothing_per_request(
            self, org):
        """What the query-budget test caught. Every request grew a
        `TenantDomain` lookup, including on the single-tenant deployment
        that has no custom domain and never will."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        resolver.custom_domains_possible("some.external.example")  # warm flag
        with CaptureQueriesContext(connection) as captured:
            assert resolver.custom_domains_possible(
                "some.external.example") is False
        assert len(captured) == 0

    def test_the_flag_flips_the_moment_the_first_domain_verifies(
            self, org, monkeypatch):
        """And is evicted on that event, rather than waiting out its TTL --
        otherwise the first customer to verify a domain finds it ignored for
        up to a minute, which reads as the verification not having worked."""
        assert resolver.custom_domains_possible("hr.abcschool.edu.np") is False
        domain = domains.claim(org, "hr.abcschool.edu.np")
        _verified(domain, monkeypatch=monkeypatch)
        assert resolver.custom_domains_possible("hr.abcschool.edu.np") is True


# ---------------------------------------------------------------------------
# Part 3: the host check, which ran before anything else S9 built
# ---------------------------------------------------------------------------
class TestDjangoLetsAVerifiedDomainThrough:
    """The defect that made the whole feature inert, and its fix.

    `ALLOWED_HOSTS` is checked inside `HttpRequest.get_host()`, before any
    middleware this project owns, and `config.settings` refuses to start
    with `*` in it once DEBUG is off. So a customer could claim a hostname,
    publish the record, watch it verify, point the name at us -- and every
    request to it was answered "Invalid HTTP_HOST header" having never
    reached the resolver that knew the domain was theirs.

    Not visible from any resolver test, because the resolver never ran.
    """

    @staticmethod
    def _validate(host, allowed):
        from django.http.request import validate_host

        return validate_host(host, allowed)

    def test_a_static_list_alone_refuses_a_verified_custom_domain(self):
        """The bug, pinned, so the fix cannot be removed as redundant."""
        assert self._validate("hr.abcschool.edu.np",
                              [".platform.test", "admin.platform.test"]) is False

    def test_the_dynamic_list_accepts_one(self, org, monkeypatch):
        from tenancy.allowed_hosts import DynamicAllowedHosts

        allowed = DynamicAllowedHosts([".platform.test",
                                       "admin.platform.test"])
        assert self._validate("hr.abcschool.edu.np", allowed) is False

        domain = domains.claim(org, "hr.abcschool.edu.np")
        _verified(domain, monkeypatch=monkeypatch)
        assert self._validate("hr.abcschool.edu.np", allowed) is True

    def test_and_still_refuses_everything_else(self, org, monkeypatch):
        """The reason this is not a wildcard. Only hostnames a customer
        PROVED they own are accepted; an arbitrary Host header is refused
        exactly as before."""
        from tenancy.allowed_hosts import DynamicAllowedHosts

        allowed = DynamicAllowedHosts([".platform.test"])
        _verified(domains.claim(org, "hr.abcschool.edu.np"),
                  monkeypatch=monkeypatch)
        for hostile in ("evil.example.com", "localhost",
                        "hr.abcschool.edu.np.evil.com", "169.254.169.254"):
            assert self._validate(hostile, allowed) is False, hostile

    def test_an_unverified_claim_is_not_an_allowed_host(self, org):
        """Otherwise claiming a hostname -- which anybody may do, for any
        name -- would make the platform answer on it."""
        from tenancy.allowed_hosts import DynamicAllowedHosts

        allowed = DynamicAllowedHosts([".platform.test"])
        domains.claim(org, "hr.abcschool.edu.np")
        assert self._validate("hr.abcschool.edu.np", allowed) is False

    def test_a_withdrawn_domain_stops_being_an_allowed_host_at_once(
            self, org, monkeypatch):
        from tenancy.allowed_hosts import DynamicAllowedHosts

        allowed = DynamicAllowedHosts([".platform.test"])
        domain = domains.claim(org, "hr.abcschool.edu.np")
        _verified(domain, monkeypatch=monkeypatch)
        assert self._validate("hr.abcschool.edu.np", allowed) is True
        domains.remove(domain)
        assert self._validate("hr.abcschool.edu.np", allowed) is False

    def test_the_configured_patterns_are_checked_FIRST(self, org,
                                                        monkeypatch):
        """`validate_host` short-circuits on `any()`, so the ordinary
        request -- on the platform's own domain -- must never reach the
        dynamic part and spend a cache lookup on it."""
        from tenancy.allowed_hosts import DynamicAllowedHosts

        allowed = DynamicAllowedHosts([".platform.test"])
        _verified(domains.claim(org, "hr.abcschool.edu.np"),
                  monkeypatch=monkeypatch)
        entries = list(allowed)
        assert entries[0] == ".platform.test"
        assert entries.index("hr.abcschool.edu.np") > 0

    def test_it_still_behaves_like_the_list_the_rest_of_django_expects(self):
        """`config.settings` refuses to boot with a wildcard in this
        setting, and Django has its own ALLOWED_HOSTS system check. Both
        read it as a plain list, so subclassing one is the cheap way to keep
        them working."""
        from tenancy.allowed_hosts import DynamicAllowedHosts

        allowed = DynamicAllowedHosts(["example.com"])
        assert isinstance(allowed, list)
        assert bool(allowed) is True
        assert "*" not in allowed
        assert "example.com" in allowed

    def test_it_degrades_to_the_static_list_with_no_table(self, monkeypatch):
        """Read on the path of every request, including the first one a
        container serves -- before migrations have run, there is no table,
        and that must not become a 500 on the platform's own hostname."""
        from tenancy import allowed_hosts
        from tenancy.models import TenantDomain

        allowed_hosts.forget()

        def _no_table(*args, **kwargs):
            from django.db.utils import ProgrammingError

            raise ProgrammingError(
                'relation "tenancy_tenantdomain" does not exist')

        monkeypatch.setattr(TenantDomain.objects.__class__, "filter",
                            _no_table)
        allowed = allowed_hosts.DynamicAllowedHosts([".platform.test"])
        assert list(allowed) == [".platform.test"]
        assert "anything.else" not in allowed
