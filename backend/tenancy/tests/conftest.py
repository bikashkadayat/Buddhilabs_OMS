import datetime

import pytest

from tenancy import services
from tenancy.models import Organization, Plan, PlanPrice

TODAY = datetime.date(2026, 6, 15)


@pytest.fixture
def annual_plan(db):
    return Plan.objects.get(code="annual")


@pytest.fixture
def monthly_plan(db):
    return Plan.objects.get(code="monthly")


@pytest.fixture
def nif(db):
    """Tenant #1, as the migration seeded it."""
    return Organization.objects.get(slug="nif")


@pytest.fixture
def org(db, monthly_plan):
    """A second organization, provisioned through the service layer.

    Phase S1 has no isolation yet, so this exists only to exercise the
    lifecycle -- it is NOT a claim that two tenants are safe to run side by
    side. That is Phase S3.
    """
    return services.provision_organization(
        name="ABC School", slug="abcschool", document_prefix="ABCS",
        email="admin@abcschool.edu.np", plan=monthly_plan, today=TODAY)


@pytest.fixture
def platform_user(db, django_user_model):
    """A platform operator: is_platform_staff, and NO organization.

    Created inside `no_tenant()` (Phase S6). That pairing is a database
    constraint, and under row-level security a row with `organization IS NULL`
    is invisible to -- and unwritable by -- a connection that has not declared
    platform scope. `no_tenant()` is that declaration, and it is what the
    middleware and the console do on the real paths too.
    """
    from tenancy.context import no_tenant

    with no_tenant():
        return django_user_model.objects.create_user(
            username="platform-ops", email="ops@platform.test",
            password="x-Platform-1", is_platform_staff=True, organization=None)


@pytest.fixture
def tenant_admin(db, django_user_model, nif):
    return django_user_model.objects.create_user(
        username="nif-admin", email="admin@nif.test", password="x-Tenant-1",
        role="admin", organization=nif)
