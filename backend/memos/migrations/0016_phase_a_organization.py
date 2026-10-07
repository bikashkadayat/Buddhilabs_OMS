"""Phase A tenant isolation, step 1 of 2: add `organization`, nullable, and
backfill every existing row to NIF (Tenant #1).

SPLIT INTO TWO MIGRATIONS ON PURPOSE. This one is additive and safe to deploy
on its own: the column exists, every row is populated, and nothing is enforced
yet. 0017_phase_a_organization_required is what makes it NOT NULL and rewrites the unique
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
    "memonumbersequence",
    "memotemplate",
]


def forward(apps, schema_editor):
    backfill(apps, "memos", PHASE_A_MODELS)


def backward(apps, schema_editor):
    unbackfill(apps, "memos", PHASE_A_MODELS)


class Migration(migrations.Migration):

    dependencies = [
        ("memos", "0015_pdf_form_fields"),
        # NIF must exist before anything can be backfilled to it.
        ("tenancy", "0004_organization_legacy_number_formats"),
    ]

    operations = [
        migrations.AddField(
            model_name='memonumbersequence',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AddField(
            model_name='memotemplate',
            name='organization',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.RunPython(forward, backward),
    ]
