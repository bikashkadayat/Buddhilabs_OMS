"""Phase A tenant isolation, step 2 of 2: make `organization` required and
replace every global unique constraint with a per-organization one.

ORDER WITHIN THIS MIGRATION MATTERS.

  1. `organization` becomes NOT NULL -- safe, because 0010_phase_a_organization
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
        ("attendance", "0010_phase_a_organization"),
    ]

    operations = [
        migrations.AlterField(
            model_name='attendancepolicy',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='employeeshift',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='policyassignment',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='shift',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.RemoveConstraint(
            model_name='policyassignment',
            name='uniq_open_global_policy_assignment',
        ),
        migrations.RemoveConstraint(
            model_name='policyassignment',
            name='uniq_open_department_policy_assignment',
        ),
        migrations.RemoveConstraint(
            model_name='policyassignment',
            name='uniq_open_user_policy_assignment',
        ),
        # ADDED BEFORE THE DROPS BELOW. Both `code` and `name` were
        # unique=True standalone on Shift, and `name` on AttendancePolicy. The
        # composite replacements go in first so there is never an instant with
        # no uniqueness at all -- dropping first would open a window in which a
        # duplicate could be written, and that duplicate would then stop the
        # new index from ever building.
        migrations.AddConstraint(
            model_name='shift',
            constraint=models.UniqueConstraint(fields=('organization', 'code'), name='uniq_shift_org_code'),
        ),
        migrations.AddConstraint(
            model_name='shift',
            constraint=models.UniqueConstraint(fields=('organization', 'name'), name='uniq_shift_org_name'),
        ),
        migrations.AddConstraint(
            model_name='attendancepolicy',
            constraint=models.UniqueConstraint(fields=('organization', 'name'), name='uniq_attendance_policy_org_name'),
        ),
        migrations.AlterField(
            model_name='attendancepolicy',
            name='name',
            field=models.CharField(max_length=100),
        ),
        migrations.AlterField(
            model_name='shift',
            name='code',
            field=models.SlugField(max_length=40),
        ),
        migrations.AlterField(
            model_name='shift',
            name='name',
            field=models.CharField(max_length=60),
        ),
        migrations.AddConstraint(
            model_name='policyassignment',
            constraint=models.UniqueConstraint(condition=models.Q(('effective_until__isnull', True), ('scope', 'global')), fields=('organization', 'scope'), name='uniq_open_global_policy_assignment'),
        ),
        migrations.AddConstraint(
            model_name='policyassignment',
            constraint=models.UniqueConstraint(condition=models.Q(('effective_until__isnull', True), ('scope', 'department')), fields=('organization', 'department'), name='uniq_open_department_policy_assignment'),
        ),
        migrations.AddConstraint(
            model_name='policyassignment',
            constraint=models.UniqueConstraint(condition=models.Q(('effective_until__isnull', True), ('scope', 'user')), fields=('organization', 'user'), name='uniq_open_user_policy_assignment'),
        ),
    ]
