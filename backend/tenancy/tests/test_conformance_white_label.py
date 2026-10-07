"""Phase S9 Part 9: three tenants, three brands, three addresses.

    ABC School   ·   XYZ Hospital   ·   Demo NGO

WHAT THIS ANSWERS THAT THE OTHER S9 TESTS DO NOT. Those use one tenant, so
they prove the feature works. They cannot prove it works PER TENANT -- and
every white-label failure in this codebase so far has been of that shape: a
value that was right because there was only one of it. `ORG_INFO` on a PDF
letterhead, `/NIF.png` in an email, one blue in the stylesheet.

With three tenants, "every tenant sees NIF's branding" and "every tenant
sees the first tenant's branding" are both visible as the wrong
cardinality, which a pair cannot distinguish.
"""
import datetime

import pytest

from tenancy import domains, portal, resolver, services
from tenancy.context import tenant_context
from tenancy.models import OrganizationBranding, TenantDomain

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 6, 15)

# Each tenant gets a distinct brand and a distinct hostname, in a different
# TLD, so nothing can pass by sharing a suffix.
BRANDS = [
    dict(slug="abc-school", name="ABC School", document_prefix="ABCS",
         email="admin@abc-school.edu.np", industry="education", country="NP",
         display_name="ABC School Kathmandu", color_primary="#1D4ED8",
         color_secondary="#F59E0B", tagline="Learning, organised.",
         hostname="hr.abc-school.edu.np",
         address="Baneshwor, Kathmandu", phone="+977-1-4100001"),
    dict(slug="xyz-hospital", name="XYZ Hospital", document_prefix="XYZH",
         email="admin@xyz-hospital.org.np", industry="healthcare",
         country="NP", display_name="XYZ Hospital", color_primary="#047857",
         color_secondary="#0EA5E9", tagline="Care, coordinated.",
         hostname="staff.xyz-hospital.org.np",
         address="Pulchowk, Lalitpur", phone="+977-1-4100002"),
    dict(slug="demo-ngo", name="Demo NGO", document_prefix="DNGO",
         email="admin@demo-ngo.org.np", industry="nonprofit", country="NP",
         display_name="Demo NGO Nepal", color_primary="#7C3AED",
         color_secondary="#DC2626", tagline="Fieldwork, tracked.",
         hostname="portal.demo-ngo.org", address="Pokhara, Kaski",
         phone="+977-61-410003"),
]


@pytest.fixture(autouse=True)
def _hosts(settings):
    settings.TENANCY_ENABLED = True
    settings.TENANCY_BASE_DOMAIN = "platform.test"
    settings.TENANCY_PLATFORM_HOSTS = "admin.platform.test"


@pytest.fixture
def branded(db, monthly_plan):
    """Three provisioned tenants, each branded through the customer-facing
    service -- `portal.update_branding`, not a direct model write, so the
    path a customer actually uses is what is under test."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    out = []
    for spec in BRANDS:
        organization = services.provision_organization(
            name=spec["name"], slug=spec["slug"],
            document_prefix=spec["document_prefix"], email=spec["email"],
            industry=spec["industry"], country=spec["country"],
            plan=monthly_plan, today=TODAY,
            admin_email=f"root@{spec['slug']}.test", admin_name="Root Admin")
        organization.address = spec["address"]
        organization.phone = spec["phone"]
        organization.email = spec["email"]
        organization.save()

        with tenant_context(organization):
            admin = User.objects.create_user(
                username=f"{spec['slug']}-admin",
                email=f"admin@{spec['slug']}.test", password="x-Conform-1",
                role="admin", organization=organization)
            staff = User.objects.create_user(
                username=f"{spec['slug']}-staff",
                email=f"staff@{spec['slug']}.test", password="x-Conform-1",
                role="maker", organization=organization)

        portal.update_branding(
            admin, display_name=spec["display_name"],
            color_primary=spec["color_primary"],
            color_secondary=spec["color_secondary"],
            login_tagline=spec["tagline"])
        out.append({"spec": spec, "org": organization, "admin": admin,
                    "staff": staff})
    return out


def _verify(domain, host="admin.platform.test", monkeypatch=None):
    expected = domain.expected_record_value(platform_host=host)
    monkeypatch.setattr(domains, "lookup", lambda n, t: [expected.lower()])
    return domains.verify(domain)


# --- branding, per tenant -----------------------------------------------
def test_each_tenant_has_its_own_brand_and_no_two_share_one(branded):
    seen = {}
    for row in branded:
        payload = portal.branding_for_member(row["admin"])
        seen[row["org"].slug] = (payload["display_name"],
                                 payload["color_primary"])
        assert payload["display_name"] == row["spec"]["display_name"]
        assert payload["color_primary"] == row["spec"]["color_primary"]
    assert len(set(seen.values())) == 3


def test_an_ORDINARY_EMPLOYEE_of_each_tenant_sees_their_employers_brand(
        branded):
    """The whole of R28 in one assertion, three times. If branding were
    administrator-only, every employee of every tenant would still be
    looking at the platform's blue."""
    for row in branded:
        payload = portal.branding_for_member(row["staff"])
        assert payload["applicable"] is True
        assert payload["color_primary"] == row["spec"]["color_primary"]
        assert payload["login_tagline"] == row["spec"]["tagline"]


def test_no_tenant_can_change_another_tenants_brand(branded):
    """There is no organization argument to misuse, so this checks the
    consequence: an administrator's write lands on their own row only."""
    first, second, third = branded
    portal.update_branding(first["admin"], color_primary="#111111")
    assert portal.branding_for_member(
        second["admin"])["color_primary"] == second["spec"]["color_primary"]
    assert portal.branding_for_member(
        third["admin"])["color_primary"] == third["spec"]["color_primary"]


def test_each_tenants_emails_are_branded_as_that_tenant(branded):
    """Before this phase every one of these returned "Nepal Internet
    Foundation" -- the same wrong answer three times, which is why one
    tenant could not reveal it."""
    from notifications import emails

    names = set()
    for row in branded:
        with tenant_context(row["org"]):
            branding = emails._branding()
        assert branding["org_name"] == row["spec"]["display_name"]
        assert branding["brand_color"] == row["spec"]["color_primary"]
        names.add(branding["org_name"])
    assert len(names) == 3
    # Nor the platform operator's name, which is what an unbranded tenant
    # would fall back to. Brand migration: this was Nepal Internet Foundation.
    assert "Buddhi Labs" not in names


def test_each_tenants_documents_carry_that_tenants_letterhead(branded):
    """Part 7. The logo was tenant-aware already; the name, address and
    telephone number printed beside it were not."""
    from documents.pdf import org_letterhead

    for row in branded:
        head = org_letterhead(row["org"])
        assert head["name"] == row["spec"]["display_name"]
        assert head["address"] == row["spec"]["address"]
        assert head["tel"] == row["spec"]["phone"]
    heads = [org_letterhead(row["org"])["address"] for row in branded]
    assert len(set(heads)) == 3


# --- domains, per tenant ------------------------------------------------
def test_each_tenant_proves_and_keeps_its_own_hostname(branded, monkeypatch):
    claimed = []
    for row in branded:
        domain = domains.claim(row["org"], row["spec"]["hostname"])
        assert resolver.resolve(row["spec"]["hostname"]) is None, (
            "an unverified claim must resolve to nothing")
        claimed.append((row, _verify(domain, monkeypatch=monkeypatch)))

    for row, domain in claimed:
        assert domain.status == TenantDomain.Status.ACTIVE
        found = resolver.resolve(row["spec"]["hostname"])
        assert found is not None and found.pk == row["org"].pk


def test_no_hostname_resolves_to_the_wrong_tenant(branded, monkeypatch):
    """With three tenants, a resolver that returns "some other tenant" has
    the wrong answer rather than merely the wrong one of two."""
    for row in branded:
        _verify(domains.claim(row["org"], row["spec"]["hostname"]),
                monkeypatch=monkeypatch)

    for row in branded:
        resolved = resolver.resolve(row["spec"]["hostname"])
        assert resolved.pk == row["org"].pk
        for other in branded:
            if other is row:
                continue
            assert resolved.pk != other["org"].pk


def test_every_tenant_keeps_its_subdomain_as_well(branded, monkeypatch):
    """Part 5: both addresses work. A customer who adds a domain and loses
    the address their staff have bookmarked has been given a downgrade."""
    for row in branded:
        _verify(domains.claim(row["org"], row["spec"]["hostname"]),
                monkeypatch=monkeypatch)
    for row in branded:
        by_slug = resolver.resolve(f"{row['org'].slug}.platform.test")
        by_custom = resolver.resolve(row["spec"]["hostname"])
        assert by_slug is not None
        assert by_slug.pk == by_custom.pk == row["org"].pk


def test_one_tenant_cannot_claim_anothers_hostname(branded):
    first, second, _ = branded
    domains.claim(first["org"], first["spec"]["hostname"])
    with pytest.raises(domains.InvalidDomain):
        domains.claim(second["org"], first["spec"]["hostname"])


def test_withdrawing_one_tenants_domain_leaves_the_others_serving(
        branded, monthly_plan, monkeypatch):
    rows = []
    for row in branded:
        rows.append((row, _verify(domains.claim(row["org"],
                                                row["spec"]["hostname"]),
                                  monkeypatch=monkeypatch)))
    (first, first_domain) = rows[0]
    domains.remove(first_domain)

    assert resolver.resolve(first["spec"]["hostname"]) is None
    for row, _domain in rows[1:]:
        assert resolver.resolve(row["spec"]["hostname"]).pk == row["org"].pk


def test_no_tenant_may_claim_a_platform_hostname(branded):
    for row in branded:
        for reserved in ("platform.test", "admin.platform.test",
                         f"{row['org'].slug}.platform.test"):
            with pytest.raises(domains.InvalidDomain):
                domains.claim(row["org"], reserved)


def test_branding_rows_are_one_per_tenant_and_reached_only_by_the_fk(branded):
    """Three tenants, three distinct branding rows.

    NOT A ROW-LEVEL-SECURITY ASSERTION, and the first draft of this test
    wrongly expected one. `OrganizationBranding` is PLATFORM_GLOBAL in
    `tenancy.inventory`: it is part of the tenant REGISTRY, like
    `Organization` and `Subscription`, so an unfiltered query inside a
    tenant's scope legitimately sees every row. What keeps a tenant to its
    own branding is that every reader reaches it through the organization
    foreign key, never by an unfiltered query -- so that is what is checked.
    """
    for row in branded:
        mine = list(OrganizationBranding.objects.filter(
            organization=row["org"]))
        assert len(mine) == 1
        assert mine[0].display_name == row["spec"]["display_name"]

    names = {OrganizationBranding.objects.get(
        organization=row["org"]).display_name for row in branded}
    assert len(names) == 3
