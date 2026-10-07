"""Part 4 + the phase goal: NIF is Tenant #1 and NOTHING ELSE CHANGED.

The last test in this file is the one the whole phase is judged by.
"""
import datetime

import pytest

from tenancy import services
from tenancy.models import (Organization, OrganizationBranding,
                            OrganizationSettings, Subscription,
                            SubscriptionEvent)

pytestmark = pytest.mark.django_db


def test_nif_exists_as_tenant_one_with_the_specified_identity(nif):
    assert nif.name == "Nepal Internet Foundation"
    assert nif.slug == "nif"
    assert nif.document_prefix == "NIFN"


def test_nif_is_the_only_organization():
    assert Organization.objects.count() == 1


def test_nif_has_settings_and_branding_rows_from_birth(nif):
    assert OrganizationSettings.objects.filter(organization=nif).exists()
    assert OrganizationBranding.objects.filter(organization=nif).exists()


def test_nifs_settings_are_entirely_empty_so_platform_defaults_apply(nif):
    """An empty settings row means "behave exactly as the product ships".

    This is what guarantees no behaviour change: every ATTENDANCE_* and TASK_*
    value still resolves to the settings.py default it does today.
    """
    settings_row = nif.settings
    for field in OrganizationSettings._meta.get_fields():
        if not hasattr(field, "attname") or field.name in (
                "id", "organization", "created_at", "updated_at"):
            continue
        value = getattr(settings_row, field.attname)
        # `{}` counts as empty: Phase S7 added `onboarding_steps`, a JSONField
        # whose default is an empty dict. A dict with nothing in it customises
        # nothing, which is exactly what this test is about -- and making the
        # column nullable instead, purely to satisfy an `in (None, "", False)`
        # literal, would push a None check onto every reader of it.
        assert value in (None, "", False) or value == {}, (
            f"{field.name} = {value!r}")


def test_nifs_branding_is_entirely_empty_so_it_looks_identical(nif):
    branding = nif.branding
    assert branding.logo_primary.name in (None, "")
    assert branding.color_primary == ""
    assert branding.display_name == ""
    assert branding.email_footer_html == ""


def test_nif_is_active_and_not_on_a_trial_clock(nif):
    """Seeding the platform owner as TRIAL would suspend its own HR system."""
    assert nif.status == Organization.Status.ACTIVE
    sub = nif.subscription
    assert sub.status == Subscription.Status.ACTIVE
    assert sub.trial_end is None
    assert sub.current_period_end == datetime.date(2099, 12, 31)
    assert sub.auto_renew is False


def test_nifs_mirror_agrees_with_its_subscription(nif):
    assert nif.subscription_status == Subscription.Status.ACTIVE
    assert nif.subscription_expiry == datetime.date(2099, 12, 31)
    assert services.mirror_drift(nif) is None
    assert services.reconcile_mirrors() == {}


def test_nif_is_admitted(nif):
    assert nif.is_admitted is True


def test_nifs_creation_is_recorded(nif):
    event = SubscriptionEvent.objects.get(organization=nif)
    assert event.event == SubscriptionEvent.Event.CREATED
    assert event.to_status == Subscription.Status.ACTIVE
    assert "Tenant #1" in event.note


def test_the_mirror_fields_are_not_editable():
    """Nothing but sync_subscription_mirror() may write them."""
    for name in ("subscription_status", "subscription_start",
                 "subscription_expiry", "seat_count", "storage_bytes"):
        assert Organization._meta.get_field(name).editable is False


# --- THE PHASE GOAL ----------------------------------------------------
def test_every_phase_b_row_belongs_to_nif(nif):
    """Phase S4 Step 2: backfill every row, verify every row, no orphans."""
    from django.apps import apps

    from tenancy import inventory

    problems = []
    for label in sorted(inventory.PHASE_B):
        model = apps.get_model(*label.split("."))
        if not model.objects.exists():
            continue
        foreign = model.objects.exclude(organization=nif).count()
        orphan = model.objects.filter(organization__isnull=True).count()
        if foreign or orphan:
            problems.append(f"{label}: {foreign} not-NIF, {orphan} orphan")
    assert problems == [], problems


def test_every_phase_a_row_belongs_to_nif(nif):
    """Phase S2 Step 3: "Every Phase A row must belong to NIF. No orphan rows."

    Asserted against the real, migration-seeded data: 28 models, and not one
    row owned by anybody else or by nobody.
    """
    from django.apps import apps

    from tenancy import inventory

    problems = []
    for label in sorted(inventory.PHASE_A):
        model = apps.get_model(*label.split("."))
        total = model.objects.count()
        if not total:
            continue
        foreign = model.objects.exclude(organization=nif).count()
        orphan = model.objects.filter(organization__isnull=True).count()
        if foreign or orphan:
            problems.append(f"{label}: {foreign} not-NIF, {orphan} orphan "
                            f"(of {total})")
    assert problems == [], problems


def test_every_phase_b_model_has_an_organization_column():
    """Phase S4's core claim, checked against the models themselves."""
    from django.apps import apps

    from tenancy import inventory

    missing = [label for label in sorted(inventory.PHASE_B)
               if not any(f.name == "organization"
                          for f in apps.get_model(*label.split("."))._meta.local_fields)]
    assert missing == [], f"Phase B models with no organization column: {missing}"


def test_every_phase_c_model_has_an_organization_column():
    """Phase S5 completed the last tier: all 106 business models are scoped."""
    from django.apps import apps

    from tenancy import inventory

    missing = [label for label in sorted(inventory.PHASE_C)
               if not any(f.name == "organization"
                          for f in apps.get_model(*label.split("."))._meta.local_fields)]
    assert missing == [], f"Phase C models with no organization column: {missing}"


def test_all_106_business_models_are_tenant_aware():
    from django.apps import apps

    from tenancy import inventory

    assert len(inventory.TENANT_SCOPED) == 106
    missing = [label for label in sorted(inventory.TENANT_SCOPED)
               if not any(f.name == "organization"
                          for f in apps.get_model(*label.split("."))._meta.local_fields)]
    assert missing == []


def test_nif_keeps_its_legacy_document_number_formats(nif):
    """The guarantee that makes Phase S2 invisible to NIF."""
    assert nif.legacy_number_formats is True
    assert nif.document_prefix == "NIFN"


def test_no_other_organization_carries_the_legacy_flag():
    """It is NIF's grandfathered exception, not a default."""
    from tenancy.models import Organization

    assert list(Organization.objects.filter(legacy_number_formats=True)
                .values_list("slug", flat=True)) == ["nif"]
