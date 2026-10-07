"""Phase A, step 1 of 2 for users: every existing account becomes a NIF user.

The column was added (nullable) in Phase S1; this populates it. It is split
from the constraint work in 0014 for the same reason every other Phase A app is
split: this migration is additive and reversible, and a deployment can soak
between the two.

PLATFORM ACCOUNTS ARE SKIPPED. A platform operator has organization = NULL by
definition, and `user_platform_staff_has_no_org` (Phase S1) already refuses any
other combination -- so stamping one here would fail the constraint that is
already in place.
"""
from django.db import migrations

from tenancy.migration_utils import default_organization_id


def forward(apps, schema_editor):
    User = apps.get_model("users", "User")
    org_id = default_organization_id(apps)

    User.objects.filter(organization__isnull=True, is_platform_staff=False) \
        .update(organization_id=org_id)

    # Step 3 of the phase brief, enforced rather than checked by hand. Any
    # tenant account left without an owner would fail the NOT NULL-equivalent
    # check constraint added in 0014, so refusing here gives a clear error
    # instead of an opaque integrity failure one migration later.
    orphans = User.objects.filter(organization__isnull=True,
                                  is_platform_staff=False).count()
    if orphans:
        raise RuntimeError(
            f"{orphans} user(s) still have no organization after the Phase A "
            f"backfill. Refusing to continue: user_tenant_has_organization in "
            f"0014 would reject this database.")


def backward(apps, schema_editor):
    User = apps.get_model("users", "User")
    User.objects.filter(is_platform_staff=False).update(organization_id=None)


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0012_user_is_platform_staff_user_organization_and_more"),
        ("tenancy", "0004_organization_legacy_number_formats"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
