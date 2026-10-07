"""Phase A tenant isolation, step 2 of 2: make `organization` required and
replace every global unique constraint with a per-organization one.

ORDER WITHIN THIS MIGRATION MATTERS.

  1. `organization` becomes NOT NULL -- safe, because 0012_phase_a_organization
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
        ("tasks", "0012_phase_a_organization"),
    ]

    operations = [
        migrations.AlterField(
            model_name='departmentgoal',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='taskgroup',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='tasknumbersequence',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='tasktemplate',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='tasktemplategroup',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='tasktemplateitem',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.RemoveConstraint(
            model_name='tasktemplate',
            name='task_template_name_unique',
        ),
        migrations.AlterField(
            model_name='tasknumbersequence',
            name='year',
            field=models.PositiveIntegerField(),
        ),
        migrations.AddConstraint(
            model_name='tasknumbersequence',
            constraint=models.UniqueConstraint(fields=('organization', 'year'), name='uniq_task_sequence_org_year'),
        ),
        migrations.AddConstraint(
            model_name='tasktemplate',
            constraint=models.UniqueConstraint(fields=('organization', 'name'), name='task_template_org_name_unique'),
        ),
    ]
