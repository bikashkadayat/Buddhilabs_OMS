"""Branding and a domain, supplied when the tenant is created.

THE WORKFLOW THIS REPLACES. A platform operator created a tenant, handed it
over, and the customer's staff signed in on the first day to a workspace
wearing the platform's colours. Branding was a second, separate step that
somebody had to remember -- on the one day the branding matters most.

AND THE DEFECT IT CLOSES. The creation form already accepted `domain`, and
it wrote `Organization.domain`: the single unverified column Phase S9
replaced and which the resolver still honours. So an operator typing a
hostname was GRANTING it -- a competitor's, a bank's, or a typo -- with no
DNS proof anywhere in the path. Phase S9 built the verification that this
endpoint walked around.
"""
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from tenancy import console, domains, resolver
from tenancy.context import no_tenant, tenant_context
from tenancy.models import Organization, OrganizationBranding, TenantDomain

pytestmark = pytest.mark.django_db

# The smallest valid PNG, so uploads exercise real image handling.
ONE_PIXEL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae"
    "426082")


def png(name="logo.png"):
    return SimpleUploadedFile(name, ONE_PIXEL_PNG, content_type="image/png")


@pytest.fixture(autouse=True)
def _hosts(settings):
    settings.TENANCY_ENABLED = True
    settings.TENANCY_BASE_DOMAIN = "buddhilabs.com"
    settings.TENANCY_PLATFORM_HOSTS = "admin.buddhilabs.com"
    settings.ALLOWED_HOSTS = ["*"]


def create(platform_user, monthly_plan, **extra):
    base = dict(name="ABC School", slug="abcschool", document_prefix="ABCS",
                email="admin@abcschool.edu.np", plan=monthly_plan)
    base.update(extra)
    return console.create_organization(platform_user, **base)


# ---------------------------------------------------------------------------
# one upload, every surface
# ---------------------------------------------------------------------------
class TestLogoAtCreation:
    def test_one_logo_fills_all_four_slots(self, platform_user, monthly_plan):
        """"No second branding step": the header, the sign-in page, every
        notification email and every PDF letterhead, from one upload."""
        org = create(platform_user, monthly_plan, logo=png())
        with no_tenant():
            row = OrganizationBranding.objects.get(organization=org)
        for slot in ("logo_primary", "logo_login", "logo_email",
                     "logo_letterhead"):
            assert getattr(row, slot), f"{slot} was left empty"

    def test_each_slot_is_independently_replaceable_afterwards(
            self, platform_user, monthly_plan):
        """Which is why the file is written four times rather than read
        through a fallback chain: a customer who later uploads a wide
        letterhead lockup keeps their square mark everywhere else."""
        org = create(platform_user, monthly_plan, logo=png())
        with no_tenant():
            row = OrganizationBranding.objects.get(organization=org)
            before = row.logo_primary.name
            row.logo_letterhead.save("wide.png", png("wide.png"), save=True)
            row.refresh_from_db()
        assert row.logo_primary.name == before
        assert "wide" in row.logo_letterhead.name

    def test_the_favicon_is_stored_on_the_organization(self, platform_user,
                                                        monthly_plan):
        """It lives there rather than on the branding row -- modelled in
        Phase S1 as identity, before branding was its own record."""
        org = create(platform_user, monthly_plan, favicon=png("icon.png"))
        with no_tenant():
            org.refresh_from_db()
        assert org.favicon

    def test_a_tenant_created_without_one_inherits_the_platform(
            self, platform_user, monthly_plan):
        """Which is what every tenant did before this existed, so the
        change cannot break an existing workflow."""
        org = create(platform_user, monthly_plan)
        with no_tenant():
            row = OrganizationBranding.objects.filter(organization=org).first()
        assert row is None or not row.logo_primary

    def test_the_logo_reaches_the_login_page_immediately(self, platform_user,
                                                          monthly_plan,
                                                          client):
        """The point of the whole change: the customer's first visit."""
        create(platform_user, monthly_plan, logo=png(),
               color_primary="#1D4ED8")
        response = client.get("/api/v1/tenant/public/branding/",
                              HTTP_HOST="abcschool.buddhilabs.com")
        assert response.status_code == 200
        assert response.data["known"] is True
        assert response.data["logo_login"], "no logo on the login page"
        assert response.data["color_primary"] == "#1D4ED8"

    def test_and_the_PDF_letterhead_uses_it(self, platform_user,
                                             monthly_plan):
        from documents.pdf import logo_data_uri

        org = create(platform_user, monthly_plan, logo=png())
        with tenant_context(org):
            uri = logo_data_uri(org)
        assert uri and uri.startswith("data:image/")


# ---------------------------------------------------------------------------
# colours
# ---------------------------------------------------------------------------
class TestColoursAtCreation:
    def test_both_are_stored(self, platform_user, monthly_plan):
        org = create(platform_user, monthly_plan,
                     color_primary="#1D4ED8", color_secondary="#F59E0B")
        with no_tenant():
            row = OrganizationBranding.objects.get(organization=org)
        assert row.color_primary == "#1D4ED8"
        assert row.color_secondary == "#F59E0B"

    @pytest.mark.parametrize("bad", [
        "red", "#1d4ed", "#fff; background:url(javascript:alert(1))",
        "var(--x)", "1D4ED8",
    ])
    def test_a_colour_that_is_not_a_colour_is_refused_at_the_edge(
            self, platform_user, monthly_plan, bad):
        """Same rule the customer's own branding page applies, for the same
        reason: the value is interpolated into a CSS custom property."""
        from tenancy.serializers import ProvisionSerializer

        serializer = ProvisionSerializer(data={
            "name": "X", "slug": "xco", "document_prefix": "XCO",
            "email": "a@x.test", "color_primary": bad})
        assert not serializer.is_valid()
        assert "color_primary" in serializer.errors


# ---------------------------------------------------------------------------
# the domain: claimed, not granted
# ---------------------------------------------------------------------------
class TestDomainAtCreation:
    def test_the_subdomain_works_the_moment_the_tenant_exists(
            self, platform_user, monthly_plan):
        """"The organization must immediately open under its assigned
        domain" -- which the subdomain does, with no DNS step at all."""
        org = create(platform_user, monthly_plan)
        found = resolver.resolve("abcschool.buddhilabs.com")
        assert found is not None and found.pk == org.pk

    def test_a_custom_domain_is_CLAIMED_not_granted(self, platform_user,
                                                     monthly_plan):
        """THE DEFECT THIS CLOSES. This used to write the unverified
        `Organization.domain` column, which the resolver honours -- so an
        operator typing a hostname granted it outright."""
        org = create(platform_user, monthly_plan,
                     domain="hr.abcschool.edu.np")
        with no_tenant():
            claim = TenantDomain.objects.get(hostname="hr.abcschool.edu.np")
        assert claim.organization_id == org.pk
        assert claim.status == TenantDomain.Status.PENDING
        # And it resolves to nothing until DNS says otherwise.
        assert resolver.resolve("hr.abcschool.edu.np") is None
        assert resolver.resolve_id("hr.abcschool.edu.np") is None

    def test_the_legacy_column_is_not_written(self, platform_user,
                                               monthly_plan):
        """Belt and braces: the column still exists and the resolver still
        honours it, so the test that matters is that creation stops
        populating it."""
        org = create(platform_user, monthly_plan,
                     domain="hr.abcschool.edu.np")
        with no_tenant():
            org.refresh_from_db()
        assert not org.domain

    def test_it_begins_serving_once_DNS_agrees(self, platform_user,
                                                monthly_plan, monkeypatch):
        org = create(platform_user, monthly_plan,
                     domain="hr.abcschool.edu.np")
        with no_tenant():
            claim = TenantDomain.objects.get(hostname="hr.abcschool.edu.np")
            expected = claim.expected_record_value(
                platform_host="admin.buddhilabs.com")
            monkeypatch.setattr(domains, "lookup",
                                lambda n, t: [expected.lower()])
            domains.verify(claim)
        found = resolver.resolve("hr.abcschool.edu.np")
        assert found is not None and found.pk == org.pk

    def test_the_platforms_own_hostnames_cannot_be_claimed(self,
                                                            platform_user,
                                                            monthly_plan):
        from tenancy.serializers import ProvisionSerializer

        serializer = ProvisionSerializer(data={
            "name": "X", "slug": "xco", "document_prefix": "XCO",
            "email": "a@x.test", "domain": "admin.buddhilabs.com"})
        assert not serializer.is_valid()
        assert "domain" in serializer.errors

    def test_a_bad_domain_does_not_cost_the_operator_the_TENANT(
            self, platform_user, monthly_plan):
        """The workspace is already built and usable on its subdomain.
        Losing it because a hostname was mistyped would be far worse than
        claiming the domain again from the tenant's own page."""
        org = console.create_organization(
            platform_user, name="ABC School", slug="abcschool",
            document_prefix="ABCS", email="admin@abcschool.edu.np",
            plan=monthly_plan, domain="hr.abcschool.edu.np")
        # Somebody else already holds it; the second tenant still gets built.
        second = console.create_organization(
            platform_user, name="Copycat", slug="copycat",
            document_prefix="COPY", email="admin@copycat.test",
            plan=monthly_plan, domain="hr.abcschool.edu.np")
        assert second.pk
        with no_tenant():
            assert Organization.objects.filter(slug="copycat").exists()
            held = TenantDomain.objects.get(hostname="hr.abcschool.edu.np")
        assert held.organization_id == org.pk


# ---------------------------------------------------------------------------
# the audit trail
# ---------------------------------------------------------------------------
def test_branding_set_at_creation_is_audited(platform_user, monthly_plan):
    from tenancy.models import PlatformAuditLog

    org = create(platform_user, monthly_plan, logo=png(),
                 color_primary="#1D4ED8")
    with no_tenant():
        entry = (PlatformAuditLog.objects
                 .filter(organization=org,
                         action=PlatformAuditLog.Action.BRANDING_CHANGED)
                 .first())
    assert entry is not None
    assert "logo" in entry.changes["set_at_creation"]
    assert "color_primary" in entry.changes["set_at_creation"]
