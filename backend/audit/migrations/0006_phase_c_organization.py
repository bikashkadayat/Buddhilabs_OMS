"""Phase C tenant isolation, step 1 of 2: add `organization`, nullable, and
backfill the rows that have an owner to derive.

PHASE C IS NOT PHASE A OR B, AND THE DIFFERENCE IS IN THIS FILE.

These are append-only logs, derived summaries and report records. Five of them
keep a NULLABLE organization on purpose -- audit.AuditLog (a failed login for
an address belonging to nobody has no tenant, and inventing one would hide the
probe), plus four whose own parent foreign key is nullable. Those are NOT
backfilled to NIF and NOT tightened in 0007_phase_c_organization_required: a NULL there is
the correct answer, and a NULL-organization row is invisible under the RLS
policy, which is the right fail-closed behaviour for a platform-level record.

Everything else is backfilled and verified exactly as Phase A and B were.
"""
from django.db import migrations, models
from tenancy.migration_utils import backfill, unbackfill
import django.db.models.deletion

# EMPTY ON PURPOSE. Every model this migration touches keeps a nullable
# organization, so there is nothing to backfill and nothing to tighten -- see
# the module docstring.
PHASE_C_MODELS = []


def forward(apps, schema_editor):
    if PHASE_C_MODELS:
        backfill(apps, "audit", PHASE_C_MODELS)


def backward(apps, schema_editor):
    if PHASE_C_MODELS:
        unbackfill(apps, "audit", PHASE_C_MODELS)


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0005_alter_auditlog_action"),
        ("tenancy", "0004_organization_legacy_number_formats"),
    ]

    operations = [
        migrations.AddField(
            model_name='auditlog',
            name='organization',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.RunPython(forward, backward),
    ]
