"""Phase A tenant isolation, step 2 of 2: make `organization` required and
replace every global unique constraint with a per-organization one.

ORDER WITHIN THIS MIGRATION MATTERS.

  1. `organization` becomes NOT NULL -- safe, because 0005_phase_a_organization
     backfilled every row and refused to finish if any was left over.
  2. The NEW composite constraints are ADDED.
  3. Only then is the OLD global `unique=True` dropped (the AlterField
     operations below).

Adding before dropping means there is never an instant with no uniqueness at
all. Dropping first would open a window in which a duplicate could be written,
and that duplicate would then stop the new index from ever building.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("appraisal", "0005_phase_a_organization"),
    ]

    operations = [
        migrations.AlterField(
            model_name='appraisalcycle',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='competency',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        # ADDED BEFORE THE DROPS BELOW -- see the attendance migration for why.
        migrations.AddConstraint(
            model_name='appraisalcycle',
            constraint=models.UniqueConstraint(fields=('organization', 'name'), name='uniq_appraisal_cycle_org_name'),
        ),
        migrations.AddConstraint(
            model_name='competency',
            constraint=models.UniqueConstraint(fields=('organization', 'code'), name='uniq_competency_org_code'),
        ),
        migrations.AlterField(
            model_name='appraisalcycle',
            name='name',
            field=models.CharField(max_length=150),
        ),
        migrations.AlterField(
            model_name='competency',
            name='code',
            field=models.SlugField(max_length=40),
        ),
    ]
