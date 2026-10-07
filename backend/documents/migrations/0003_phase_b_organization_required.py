"""Phase B tenant isolation, step 2 of 2: make `organization` required and
replace the remaining global unique constraints with per-tenant ones.

ORDER WITHIN THIS MIGRATION MATTERS.

  1. `organization` becomes NOT NULL -- safe, because 0002_phase_b_organization
     backfilled every row and refused to finish if any was left over.
  2. The NEW composite constraints are ADDED.
  3. Only then is the OLD global `unique=True` dropped.

Adding before dropping means there is never an instant with no uniqueness at
all. Dropping first would open a window in which a duplicate could be written,
and that duplicate would then stop the new index from ever building.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0002_phase_b_organization"),
    ]

    operations = [
        migrations.AlterField(
            model_name='issueddocument',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='issueddocument',
            name='document_number',
            field=models.CharField(db_index=True, max_length=64),
        ),
        migrations.AddConstraint(
            model_name='issueddocument',
            constraint=models.UniqueConstraint(fields=('organization', 'document_number'), name='uniq_document_number_org'),
        ),
    ]
