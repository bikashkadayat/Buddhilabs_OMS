"""Phase B tenant isolation, step 1 of 2: add `organization`, nullable, and
backfill every existing row to NIF (Tenant #1).

Split in two for the same reason every Phase A app was: this half is purely
additive and safe to deploy alone -- the column exists, every row is populated,
and nothing is enforced yet. 0003_phase_b_organization_required is what makes it NOT NULL and
rewrites the remaining global unique constraints.

The backfill VERIFIES itself: tenancy.migration_utils.backfill raises if a
single row is left without an owner, so an orphan stops the migration instead
of surfacing later as an opaque NOT NULL failure.
"""
from django.conf import settings
from django.db import migrations, models
from tenancy.migration_utils import backfill, unbackfill
import django.db.models.deletion

PHASE_B_MODELS = [
    "issueddocument",
]


def forward(apps, schema_editor):
    backfill(apps, "documents", PHASE_B_MODELS)


def backward(apps, schema_editor):
    unbackfill(apps, "documents", PHASE_B_MODELS)


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0001_initial"),
        # NIF must exist before anything can be backfilled to it.
        ("tenancy", "0004_organization_legacy_number_formats"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='issueddocument',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.RunPython(forward, backward),
    ]
