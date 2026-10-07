"""Phase A tenant isolation, step 1 of 2: add `organization`, nullable, and
backfill every existing row to NIF (Tenant #1).

SPLIT INTO TWO MIGRATIONS ON PURPOSE. This one is additive and safe to deploy
on its own: the column exists, every row is populated, and nothing is enforced
yet. 0011_phase_a_organization_required is what makes it NOT NULL and rewrites the unique
constraints, so a deployment can soak between the two and roll the first one
back by dropping a column.

The backfill VERIFIES itself -- tenancy.migration_utils.backfill raises if a
single row is left without an owner, which is Step 3 of the phase brief
enforced by the migration rather than checked by hand afterwards.
"""
from django.db import migrations, models
from tenancy.migration_utils import backfill, unbackfill
import django.db.models.deletion

PHASE_A_MODELS = [
    "minutenumbersequence",
    "minutetype",
]


def forward(apps, schema_editor):
    backfill(apps, "minutes", PHASE_A_MODELS)


def backward(apps, schema_editor):
    unbackfill(apps, "minutes", PHASE_A_MODELS)


class Migration(migrations.Migration):

    dependencies = [
        ("minutes", "0009_seed_manual_minute_types"),
        # NIF must exist before anything can be backfilled to it.
        ("tenancy", "0004_organization_legacy_number_formats"),
    ]

    operations = [
        migrations.AddField(
            model_name='minutenumbersequence',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AddField(
            model_name='minutetype',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.RunPython(forward, backward),
    ]
