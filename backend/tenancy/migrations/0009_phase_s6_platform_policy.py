"""Re-apply every tenant policy with the platform-scope clause (Phase S6).

WHAT THIS FIXES, AND WHY IT IS NOT A REFINEMENT

The Phase S5 policy was ``organization_id = current_setting('app.current_org')``
and nothing else. A platform operator's ``users_user`` row has
``organization IS NULL`` -- that pairing is a database constraint, and it is
what makes "a company administrator cannot administer the platform"
structural rather than a flag somebody has to remember to check.

But ``NULL = anything`` is NULL, never true. So with row-level security
enabled, the application role could not see a platform account at all, and
nobody could sign in to the platform console. The S5 conformance gate missed
it because every test in it provisions tenants; none creates a platform
operator.

The new clause admits rows with no organization, and ONLY on a connection
that has explicitly declared platform scope (``app.platform = 'on'``). See
``tenancy.rls.PLATFORM_GUC`` for why that is declared rather than inferred
from "no tenant is bound" -- in one sentence: the middleware logs and
continues if a tenant bind fails, and inferring platform scope from an
absence would hand a tenant request every platform operator's password hash.

NOTHING A TENANT CAN REACH GOT WIDER. A tenant request never has platform
scope, so for it the policy is byte-for-byte the S5 behaviour.

The statements come from ``rls.enable_sql``, which DROPs the policy before
creating it, so this is idempotent. REVERSIBLE: the reverse re-creates the S5
form, which is a rollback to a strictly narrower policy -- safe, except that
platform sign-in stops working again, which is the state this migration
exists to leave.
"""
from django.db import migrations

from tenancy import rls

S5_POLICY = """CREATE POLICY "{name}" ON "{table}"
        USING (organization_id = NULLIF(
                   current_setting('{guc}', true), '')::uuid)
        WITH CHECK (organization_id = NULLIF(
                   current_setting('{guc}', true), '')::uuid);"""


def _tables(schema_editor):
    platform = rls.platform_tables()
    return [table for table, _nullable in rls.tenant_tables()
            if table not in platform]


def apply_platform_clause(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in _tables(schema_editor):
            for statement in rls.enable_sql(table):
                cursor.execute(statement)


def restore_s5_policy(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in _tables(schema_editor):
            cursor.execute(
                f'DROP POLICY IF EXISTS "{rls.POLICY_NAME}" ON "{table}";')
            cursor.execute(S5_POLICY.format(
                name=rls.POLICY_NAME, table=table, guc=rls.GUC))


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0008_phase_s6_status_override"),
        ("tenancy", "0006_phase_s5_row_level_security"),
    ]

    operations = [
        migrations.RunPython(apply_platform_clause, restore_s5_policy),
    ]
