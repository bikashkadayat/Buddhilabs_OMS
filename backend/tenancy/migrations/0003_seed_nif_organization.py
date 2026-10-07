"""NIF becomes Tenant #1.

FOUNDATION ONLY. This creates the Organization row (plus its settings, branding
and subscription) and NOTHING ELSE. It does not touch one existing business
row: no user is attached, no department, no task, no leave. Those are the Phase
S2 backfill, and keeping them out of here is what makes this migration
reversible by deleting four rows.

WHY NIF'S SUBSCRIPTION IS ACTIVE WITH A FAR-FUTURE EXPIRY
---------------------------------------------------------
NIF owns the platform; it is not a paying customer. Seeding it as TRIAL would
start a 14-day clock that ends with the platform owner's own HR system entering
a grace period and then suspending itself. ACTIVE until 2099 is the honest
representation of "this tenant is not billed", and it keeps the subscription
mirror -- which the request path reads on every request -- truthful rather than
special-cased.

IDEMPOTENT AND SAFE ON A POPULATED DATABASE. If an organization with slug 'nif'
already exists it is left untouched. If some OTHER organization already exists,
this migration still only adds NIF; it never adopts or rewrites another tenant.
"""
import datetime
import uuid

from django.db import migrations

SLUG = "nif"
NAME = "Nepal Internet Foundation"
DOCUMENT_PREFIX = "NIFN"
EMAIL = "nepalinternetfoundation@gmail.com"

# Not billed. See the module docstring.
PERIOD_START = datetime.date(2020, 1, 1)
PERIOD_END = datetime.date(2099, 12, 31)

# The plan NIF is recorded against. The annual plan, because a 12-month term is
# the closest shape to "indefinite"; the subscription is not charged either way.
PLAN_CODE = "annual"


def seed(apps, schema_editor):
    Organization = apps.get_model("tenancy", "Organization")
    OrganizationSettings = apps.get_model("tenancy", "OrganizationSettings")
    OrganizationBranding = apps.get_model("tenancy", "OrganizationBranding")
    Plan = apps.get_model("tenancy", "Plan")
    Subscription = apps.get_model("tenancy", "Subscription")
    SubscriptionEvent = apps.get_model("tenancy", "SubscriptionEvent")

    if Organization.objects.filter(slug=SLUG).exists():
        return

    plan = Plan.objects.filter(code=PLAN_CODE).first() or Plan.objects.first()
    if plan is None:
        # 0002 seeds the plans, so this cannot happen in a normal run. Refusing
        # loudly beats creating a tenant with no subscription, which the request
        # path could not gate.
        raise RuntimeError(
            "No Plan rows exist; tenancy.0002_seed_plans must run first.")

    org = Organization.objects.create(
        id=uuid.uuid4(),
        name=NAME,
        slug=SLUG,
        status="active",
        email=EMAIL,
        country="NP",
        timezone="Asia/Kathmandu",
        document_prefix=DOCUMENT_PREFIX,
        fiscal_calendar="BS",
        database_alias="default",
        # The mirror, written directly here because tenancy.services is not
        # importable from a migration (it resolves live models, not historical
        # ones). Kept consistent with the subscription created below; the
        # reconciler in tenancy.services.mirror_drift verifies it afterwards,
        # and tenancy/tests/test_nif_seed.py asserts it.
        subscription_status="active",
        subscription_start=PERIOD_START,
        subscription_expiry=PERIOD_END,
    )

    # Every tenant has settings and branding rows from birth, so no policy
    # lookup or branding read has to cope with their absence. Both are entirely
    # empty, which means "inherit the platform default" -- that is what keeps
    # NIF's behaviour byte-identical after this migration.
    OrganizationSettings.objects.create(organization=org)
    OrganizationBranding.objects.create(organization=org)

    subscription = Subscription.objects.create(
        id=uuid.uuid4(),
        organization=org,
        plan=plan,
        status="active",
        current_period_start=PERIOD_START,
        current_period_end=PERIOD_END,
        grace_until=PERIOD_END + datetime.timedelta(days=plan.grace_days),
        renewal_date=PERIOD_END,
        auto_renew=False,
    )

    SubscriptionEvent.objects.create(
        id=uuid.uuid4(),
        organization=org,
        subscription=subscription,
        event="created",
        to_status="active",
        to_plan=plan,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        note="NIF seeded as Tenant #1 by tenancy.0003_seed_nif_organization. "
             "Platform owner; not billed.",
    )


def unseed(apps, schema_editor):
    """Delete NIF's tenancy rows, but refuse if anything was attached to them.

    PROTECT on User.organization means a reverse migration after the Phase S2
    backfill would fail on the driver's integrity error. Checking first turns
    that into a clear refusal.
    """
    Organization = apps.get_model("tenancy", "Organization")
    User = apps.get_model("users", "User")

    org = Organization.objects.filter(slug=SLUG).first()
    if org is None:
        return
    if User.objects.filter(organization=org).exists():
        raise RuntimeError(
            "Users are attached to the NIF organization; reverse the Phase S2 "
            "user backfill before reversing this migration.")
    if org.payments.exists():
        raise RuntimeError(
            "Payments exist for the NIF organization; they are financial "
            "records and this migration will not delete them.")

    # ORDER MATTERS. Subscription.organization and Payment.organization are
    # PROTECT, so the Organization cannot be deleted while either exists --
    # Django raises ProtectedError, not a cascade. Events first (they FK the
    # subscription), then the subscription, then the organization.
    SubscriptionEvent = apps.get_model("tenancy", "SubscriptionEvent")
    Subscription = apps.get_model("tenancy", "Subscription")
    SubscriptionEvent.objects.filter(organization=org).delete()
    Subscription.objects.filter(organization=org).delete()
    Organization.objects.filter(pk=org.pk).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0002_seed_plans"),
        # The reverse path reads users.User to check nothing is attached.
        ("users", "0012_user_is_platform_staff_user_organization_and_more"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
