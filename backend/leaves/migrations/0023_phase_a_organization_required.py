"""Phase A tenant isolation, step 2 of 2: make `organization` required and
replace every global unique constraint with a per-organization one.

ORDER WITHIN THIS MIGRATION MATTERS.

  1. `organization` becomes NOT NULL -- safe, because 0022_phase_a_organization
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
        ("leaves", "0022_phase_a_organization"),
    ]

    operations = [
        migrations.AlterField(
            model_name='calendarevent',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='department',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='entitlementrule',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='holiday',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='leavepolicy',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='leavetype',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.RemoveConstraint(
            model_name='calendarevent',
            name='uniq_calendar_event_date_name',
        ),
        migrations.RemoveConstraint(
            model_name='entitlementrule',
            name='uniq_entitlement_category_type',
        ),
        migrations.AlterField(
            model_name='department',
            name='code',
            field=models.CharField(max_length=30),
        ),
        migrations.AlterField(
            model_name='holiday',
            name='date',
            field=models.DateField(),
        ),
        migrations.AlterField(
            model_name='leavetype',
            name='code',
            field=models.CharField(max_length=20),
        ),
        migrations.AddConstraint(
            model_name='calendarevent',
            constraint=models.UniqueConstraint(fields=('organization', 'date', 'name'), name='uniq_calendar_event_org_date_name'),
        ),
        migrations.AddConstraint(
            model_name='department',
            constraint=models.UniqueConstraint(fields=('organization', 'code'), name='uniq_department_org_code'),
        ),
        migrations.AddConstraint(
            model_name='entitlementrule',
            constraint=models.UniqueConstraint(fields=('organization', 'category', 'leave_type'), name='uniq_entitlement_org_category_type'),
        ),
        migrations.AddConstraint(
            model_name='holiday',
            constraint=models.UniqueConstraint(fields=('organization', 'date'), name='uniq_holiday_org_date'),
        ),
        migrations.AddConstraint(
            model_name='leavetype',
            constraint=models.UniqueConstraint(fields=('organization', 'code'), name='uniq_leavetype_org_code'),
        ),
    ]
