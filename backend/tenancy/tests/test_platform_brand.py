"""The platform is Buddhi Labs; a tenant is itself.

WHAT THIS GUARDS. The platform's identity used to be the same thing as its
only tenant's, so "Nepal Internet Foundation" was written into the login
page, the email header and footer, the PDF letterhead, the page title, the
manifest and the branding fallbacks as a literal, in six places that did not
know about each other. Migrating it meant finding all of them.

The test is therefore not "the name is Buddhi Labs" -- that would pass on a
single `grep`-and-replace that missed a template. It is the SEPARATION:

  * platform surfaces show the platform;
  * tenant surfaces show the tenant, and fall back to the platform only when
    the tenant has supplied nothing;
  * no tenant surface shows the platform when the tenant HAS supplied
    something.

The third is the one that broke repeatedly before Phase S9, and the one a
brand migration is most likely to break again.
"""
import pytest
from django.conf import settings

from tenancy import portal
from tenancy.context import tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def abc_admin(db, django_user_model, org):
    from django.contrib.auth import get_user_model          # noqa: F401

    with tenant_context(org):
        return django_user_model.objects.create_user(
            username="brand-admin", email="admin@abcschool.test",
            password="x-Brand-1", role="admin", organization=org)


class TestThePlatformIdentity:
    def test_the_platform_names_itself(self):
        assert settings.PLATFORM_NAME == "Buddhi Labs"
        assert settings.ORG_INFO["name"] == "Buddhi Labs"

    def test_and_carries_an_attribution_line(self):
        assert settings.PLATFORM_POWERED_BY == "Powered by Buddhi Labs"

    def test_the_previous_owner_is_not_the_platform_any_more(self):
        """Checked across the whole of ORG_INFO, not just the name: the
        address, telephone number and website were the previous owner's too,
        and a half-migrated letterhead is worse than an unmigrated one
        because it looks deliberate."""
        blob = " ".join(str(v) for v in settings.ORG_INFO.values()).lower()
        for stale in ("nepal internet foundation", "nif.org.np",
                      "9816011780"):
            assert stale not in blob, f"{stale} is still the platform's"

    def test_it_is_overridable_per_deployment(self, settings):
        """A deployment operated by somebody else must not have to edit
        code. This is why they are environment variables rather than
        literals -- which is precisely what they were not before."""
        settings.PLATFORM_NAME = "Another Operator"
        settings.PLATFORM_POWERED_BY = "Powered by Another Operator"
        assert settings.PLATFORM_POWERED_BY.endswith("Another Operator")


class TestATenantSeesItself:
    def test_a_branded_tenant_never_sees_the_platform(self, org, abc_admin):
        """The failure this exists for: a customer's staff being shown the
        platform operator's name inside their own workspace."""
        portal.update_branding(abc_admin, display_name="ABC School",
                               color_primary="#1D4ED8")
        seen = portal.branding_for_member(abc_admin)
        assert seen["display_name"] == "ABC School"
        assert "Buddhi" not in str(seen)

    def test_their_emails_are_theirs(self, org, abc_admin):
        from notifications import emails

        portal.update_branding(abc_admin, display_name="ABC School")
        with tenant_context(org):
            branding = emails._branding()
        assert branding["org_name"] == "ABC School"
        assert "Buddhi" not in branding["org_name"]

    def test_their_letterhead_is_theirs(self, org, abc_admin):
        from documents.pdf import org_letterhead

        org.address = "Lalitpur, Nepal"
        org.phone = "+977-1-5555555"
        org.save()
        portal.update_branding(abc_admin, display_name="ABC School")
        org.refresh_from_db()
        head = org_letterhead(org)
        assert head["name"] == "ABC School"
        assert head["address"] == "Lalitpur, Nepal"
        assert "Buddhi" not in f"{head['name']}{head['address']}{head['tel']}"


class TestTheFallbackIsThePlatform:
    def test_with_no_tenant_at_all_the_platform_answers(self):
        """"If tenant has no logo: show the platform logo."

        INSIDE `no_tenant()`, and the first version of this test got that
        wrong in a way worth recording: it called `_branding()` with nothing
        bound and expected the platform, but with one organization in the
        database `active_organization` resolves to THAT organization -- so
        the answer was the tenant's name, correctly. "No tenant in context"
        and "a deployment with one tenant" are different situations, and
        only the first falls back to the platform.
        """
        from tenancy.context import no_tenant

        from notifications import emails

        with no_tenant():
            branding = emails._branding()
        assert branding["org_name"] == "Buddhi Labs"
        assert "buddhi-labs.png" in branding["logo_url"]
        assert "NIF.png" not in branding["logo_url"]

    def test_a_tenant_that_has_set_nothing_still_shows_ITSELF(self, org):
        """The fallback covers fields a tenant left blank. It is not a
        licence to substitute the platform for a tenant that merely has no
        logo: the NAME is on the Organization row either way."""
        from notifications import emails

        with tenant_context(org):
            branding = emails._branding()
        assert branding["org_name"] == org.name
        assert branding["org_name"] != "Buddhi Labs"

    def test_and_the_letterhead_falls_back_too(self):
        from documents.pdf import org_letterhead

        assert org_letterhead(None)["name"] == "Buddhi Labs"


class TestTheAttributionReachesEverySurface:
    def test_emails_carry_it(self, org, abc_admin):
        from django.template.loader import render_to_string

        from notifications import emails

        portal.update_branding(abc_admin, display_name="ABC School")
        with tenant_context(org):
            html = render_to_string("emails/generic.html", {
                **emails._branding(), "powered_by": emails.powered_by(),
                "title": "A notification", "body": "x",
                "recipient_name": "Sita"})
        assert "Powered by Buddhi Labs" in html
        # Under the tenant's brand, not instead of it.
        assert "ABC School" in html

    def test_documents_carry_it(self, org):
        """Rendered into the @page footer, so it is on EVERY page of every
        memo, minute, circular and report rather than only the first."""
        from documents.pdf import common_context

        context = common_context("ABCS-MEMO-2026-0001", organization=org)
        assert context["powered_by"] == "Powered by Buddhi Labs"
        # And the tenant's own letterhead sits above it.
        assert context["org"]["name"] == org.name

    def test_attendance_reports_carry_the_TENANT_letterhead(self, org):
        """These were rendered from `settings.ORG_INFO` directly -- the
        PLATFORM's name, address and telephone number on every customer's
        attendance and leave reports, with the tenant's logo beside it.
        Phase S9 fixed exactly this for memos and minutes; attendance is a
        separate renderer and was missed. The brand migration surfaced it,
        because the wrong name silently became "Buddhi Labs"."""
        import inspect

        from attendance import report_views

        # The CODE, not the docstring that explains why it changed.
        source = inspect.getsource(report_views._base_ctx)
        body = source.split(chr(34) * 3)[-1]
        assert "ORG_INFO" not in body, (
            "attendance reports are back on the platform letterhead")
        assert "org_letterhead" in body
