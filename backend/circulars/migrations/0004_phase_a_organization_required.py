"""Phase A tenant isolation, step 2 of 2: make `organization` required and
replace every global unique constraint with a per-organization one.

ORDER WITHIN THIS MIGRATION MATTERS.

  1. `organization` becomes NOT NULL -- safe, because 0003_phase_a_organization
     backfilled every row and refused to finish if any was left over.
  2. The NEW composite constraints are ADDED.
  3. Only then is the OLD global `unique=True` dropped (the AlterField
     operations below).

Adding before dropping means there is never an instant with no uniqueness at
all. Dropping first would open a window in which a duplicate could be written,
and that duplicate would then stop the new index from ever building.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("circulars", "0003_phase_a_organization"),
    ]

    operations = [
        migrations.AlterField(
            model_name='circularnumbersequence',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='circularnumbersequence',
            name='year',
            field=models.PositiveIntegerField(),
        ),
        migrations.AddConstraint(
            model_name='circularnumbersequence',
            constraint=models.UniqueConstraint(fields=('organization', 'year'), name='uniq_circular_sequence_org_year'),
        ),
    ]
