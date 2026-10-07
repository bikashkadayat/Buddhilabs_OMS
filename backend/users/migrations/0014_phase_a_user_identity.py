"""Phase A, step 2 of 2 for users: tenant-scoped identity, enforced by the database.

FOUR THINGS HAPPEN HERE, AND EACH CLOSES A NAMED DEFECT.

1. `user_tenant_has_organization` completes the tenant/platform XOR that Phase
   S1 could only half-enforce. 0013 backfilled every account, so the rule
   "a user is a tenant user XOR a platform user, never neither" is now a check
   constraint.

2. `username` loses its GLOBAL unique index and gains two scoped ones. Two
   companies must both be able to have an "admin"; AbstractUser's unique=True
   forbade it. There are two constraints rather than one because PostgreSQL
   treats NULLs as distinct in a unique index -- a single
   (organization, username) index would not stop two PLATFORM users (where
   organization IS NULL) from colliding.

3. `email` gains the constraints that make tenant-aware login SOUND. Login did
   `User.objects.get(email=email)` against a column with no unique constraint
   at all, so two users sharing an address raised MultipleObjectsReturned -- an
   uncaught 500 on the login endpoint. With at most one row per
   (organization, lower(email)) that ambiguity cannot exist. The empty string
   is excluded because `email` is blank=True and legitimately unset on some
   accounts; an unconditional index would allow only one such user per tenant.

4. `employee_id` moves from global to per-organization uniqueness.
"""
import django.contrib.auth.validators
import django.db.models.functions.text
import users.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0013_phase_a_user_backfill"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("tenancy", "0004_organization_legacy_number_formats"),
    ]

    operations = [
        migrations.AlterModelManagers(
            name='user',
            managers=[
                ('objects', users.models.TenantUserManager()),
            ],
        ),
        migrations.AlterField(
            model_name='user',
            name='employee_id',
            field=models.CharField(blank=True, editable=False, max_length=32, null=True),
        ),
        migrations.AlterField(
            model_name='user',
            name='username',
            field=models.CharField(error_messages={'unique': 'A user with that username already exists.'}, help_text='Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.', max_length=150, validators=[django.contrib.auth.validators.UnicodeUsernameValidator()], verbose_name='username'),
        ),
        migrations.AddConstraint(
            model_name='user',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('is_platform_staff', False), ('organization__isnull', False)), models.Q(('is_platform_staff', True), ('organization__isnull', True)), _connector='OR'), name='user_tenant_has_organization'),
        ),
        migrations.AddConstraint(
            model_name='user',
            constraint=models.UniqueConstraint(condition=models.Q(('organization__isnull', False)), fields=('organization', 'username'), name='uniq_user_org_username'),
        ),
        migrations.AddConstraint(
            model_name='user',
            constraint=models.UniqueConstraint(condition=models.Q(('organization__isnull', True)), fields=('username',), name='uniq_platform_user_username'),
        ),
        migrations.AddConstraint(
            model_name='user',
            constraint=models.UniqueConstraint(django.db.models.functions.text.Lower('email'), models.F('organization'), condition=models.Q(('organization__isnull', False), models.Q(('email', ''), _negated=True)), name='uniq_user_org_email_ci'),
        ),
        migrations.AddConstraint(
            model_name='user',
            constraint=models.UniqueConstraint(django.db.models.functions.text.Lower('email'), condition=models.Q(('organization__isnull', True), models.Q(('email', ''), _negated=True)), name='uniq_platform_user_email_ci'),
        ),
        migrations.AddConstraint(
            model_name='user',
            constraint=models.UniqueConstraint(condition=models.Q(('employee_id__isnull', False)), fields=('organization', 'employee_id'), name='uniq_user_org_employee_id'),
        ),
    ]
