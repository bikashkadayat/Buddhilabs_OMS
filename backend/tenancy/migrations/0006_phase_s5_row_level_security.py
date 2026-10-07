"""Enable PostgreSQL Row-Level Security on every tenant-scoped table.

RUNS LAST, AND ON POSTGRESQL ONLY.

It must run after every Phase A/B/C column migration, because a policy
referencing `organization_id` cannot be created on a table that has no such
column. The dependency list below names the final migration of each app for
exactly that reason.

ON SQLITE THIS IS A NO-OP. SQLite has no row-level security at all, so the
development and CI database cannot enforce it. That is stated plainly rather
than papered over: on SQLite, isolation rests on TenantManager alone, and the
RLS conformance tests skip. The two-tenant gate must be run against PostgreSQL
before this reaches production -- which is what tenancy/tests/test_rls.py does.

REVERSIBLE. The reverse drops the policies and disables RLS, so a deployment
that needs to roll back is a migration away rather than a database surgery.
"""
from django.db import migrations

from tenancy import rls


def enable(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    platform = rls.platform_tables()
    with schema_editor.connection.cursor() as cursor:
        for table, _nullable in rls.tenant_tables():
            if table in platform:          # defensive; should never happen
                continue
            for statement in rls.enable_sql(table):
                cursor.execute(statement)


def disable(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table, _nullable in rls.tenant_tables():
            for statement in rls.disable_sql(table):
                cursor.execute(statement)


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0005_phase_s3_media_path_length"),
        # Every tenant-scoped column must exist before a policy can name it.
        ("appraisal", "0010_phase_c_organization_required"),
        ("attendance", "0014_phase_b_organization_required"),
        ("audit", "0006_phase_c_organization"),
        ("biometric", "0014_phase_s5_biometric_managers"),
        ("circulars", "0009_phase_c_organization_required"),
        ("documents", "0003_phase_b_organization_required"),
        ("drafts", "0004_phase_c_organization_required"),
        ("inventory", "0018_phase_c_organization_required"),
        ("leaves", "0027_phase_c_organization_required"),
        ("memos", "0022_phase_c_organization_required"),
        ("minutes", "0017_phase_c_organization_required"),
        ("notifications", "0025_phase_c_organization_required"),
        ("reports", "0006_phase_c_organization"),
        ("tasks", "0018_phase_c_organization_required"),
        ("users", "0017_phase_s5_user_managers"),
    ]

    operations = [migrations.RunPython(enable, disable)]
