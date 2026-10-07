"""PostgreSQL Row-Level Security: the control that makes isolation fail closed.

WHY THIS EXISTS WHEN THERE IS ALREADY A TenantManager
-----------------------------------------------------
``TenantManager`` is discipline. It is good discipline -- it is on all 106
business models and it is tested -- but it is enforced by the application, and
the application has at least four ways round it:

    Model.all_tenants.all()          the deliberate escape hatch
    Model._base_manager.all()        Django's own internals use this
    cursor.execute("SELECT ...")     raw SQL
    a new model that nobody switched

RLS moves the boundary into the database. The policy below is attached to the
TABLE, so it applies to every statement from the application role regardless of
which manager, which ORM path, or which hand-written SQL produced it. A
developer who writes ``Model.objects.all()`` and forgets the tenant gets ZERO
ROWS, never another tenant's rows.

THE THREE THINGS THAT MAKE IT ACTUALLY WORK
-------------------------------------------
1. **A non-superuser, NOBYPASSRLS application role.** A superuser, and the
   table OWNER, bypass RLS silently. ``FORCE ROW LEVEL SECURITY`` closes the
   owner case; the role grants close the rest. Without this the policies are
   decorative -- they exist, they are listed in pg_policies, and they filter
   nothing.

2. **``app.current_org`` set per transaction.** ``SET LOCAL`` is
   transaction-scoped, which is exactly right: it cannot leak onto the next
   request that reuses the connection. With ``CONN_MAX_AGE`` set -- and this
   project sets 60 -- a plain ``SET`` would do precisely that.

3. **A NULL-safe policy.** ``current_setting('app.current_org', true)`` returns
   NULL rather than raising when the setting is absent, and
   ``organization_id = NULL`` is NULL, not true -- so a statement issued with
   no tenant set matches NOTHING. That is the fail-closed default, and it is
   the reason the second argument to current_setting must be ``true``.
"""
import logging

from django.conf import settings
from django.db import connection

logger = logging.getLogger(__name__)

# The GUC the policies read. A custom GUC must contain a dot or PostgreSQL
# rejects it as an unrecognised configuration parameter.
GUC = "app.current_org"

# The second GUC, added in Phase S6. 'on' means "this connection is serving the
# PLATFORM, not a tenant", and the only thing it unlocks is rows that belong to
# NO tenant.
#
# WHY A SECOND GUC AND NOT A WIDER POLICY
#
# A platform operator's User row has `organization IS NULL` by database
# constraint -- that pairing is what makes "a company admin cannot administer
# the platform" structural. But `organization_id = <anything>` is never true
# for NULL, so under the Phase S5 policy a platform account was invisible to
# the very role that has to authenticate it: with RLS on, nobody could sign in
# to the platform console at all. The S5 conformance gate did not catch it
# because it only ever provisioned tenants.
#
# The tempting fix is to let the policy pass NULL-organization rows whenever no
# tenant is bound. That is wrong in one specific, serious way: the middleware
# logs and continues if binding fails, so a TENANT request whose bind failed
# would then be able to read every platform operator's row -- password hashes
# included. "Fail closed" has to mean closed.
#
# So platform scope is DECLARED rather than inferred from an absence. The
# middleware sets it 'on' only for a request that arrived on a platform host
# and 'off' explicitly for everything else, and `no_tenant()` sets it for the
# console service layer. A failed bind leaves it off, and NULL rows stay
# invisible.
PLATFORM_GUC = "app.platform"

POLICY_NAME = "tenant_isolation"

# Tables that are NOT tenant-scoped and must never get a policy: the tenant
# registry itself, the billing records about tenants, and Django's own
# infrastructure. Derived from tenancy.inventory so there is one source of
# truth.
def platform_tables():
    from django.apps import apps

    from .inventory import PLATFORM_GLOBAL

    tables = set()
    for label in PLATFORM_GLOBAL:
        app_label, model_name = label.split(".")
        try:
            tables.add(apps.get_model(app_label, model_name)._meta.db_table)
        except LookupError:                    # pragma: no cover
            continue
    return tables


def tenant_tables():
    """``[(table, is_nullable)]`` for every table that gets a policy.

    ``is_nullable`` matters: five Phase C tables and ``users_user`` keep a
    nullable ``organization_id`` for documented reasons, and a NULL there must
    not be readable by a tenant. The policy is the same either way -- NULL
    never equals the current org -- so this is returned for the test suite to
    assert against rather than to branch on.
    """
    from django.apps import apps

    from .inventory import TENANT_SCOPED

    rows = []
    for label in sorted(TENANT_SCOPED):
        app_label, model_name = label.split(".")
        model = apps.get_model(app_label, model_name)
        field = model._meta.get_field("organization")
        rows.append((model._meta.db_table, field.null))
    return rows


def enable_sql(table):
    """Turn RLS on for one table and attach the tenant policy.

    FORCE ROW LEVEL SECURITY is not optional. Without it the table's OWNER --
    which in most deployments is the role migrations run as -- reads and writes
    every tenant's rows while pg_policies cheerfully lists a policy that looks
    like it is protecting them.

    USING governs what a statement can SEE; WITH CHECK governs what it can
    WRITE. Both are needed: USING alone would let a tenant INSERT a row owned
    by somebody else.

    THE SECOND CLAUSE (Phase S6) admits rows that belong to NO tenant, and
    ONLY on a connection that has declared platform scope -- see PLATFORM_GUC
    for why that declaration is explicit rather than inferred from the absence
    of a tenant. Six models keep a nullable ``organization`` for documented
    reasons (``inventory.NULLABLE_ORGANIZATION``); the most important is
    ``users.User``, where NULL is precisely what identifies a platform
    operator.

    A tenant request never has platform scope, so the first clause is the only
    one that applies to it, and `organization_id = <tenant>` is still never
    true for NULL. Nothing a tenant can reach got wider.
    """
    tenant_match = (f"organization_id = NULLIF("
                    f"current_setting('{GUC}', true), '')::uuid")
    platform_match = (f"(organization_id IS NULL AND "
                      f"current_setting('{PLATFORM_GUC}', true) = 'on')")
    return [
        f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;',
        f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY;',
        f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{table}";',
        f'''CREATE POLICY "{POLICY_NAME}" ON "{table}"
                USING ({tenant_match} OR {platform_match})
                WITH CHECK ({tenant_match} OR {platform_match});''',
    ]


def disable_sql(table):
    return [
        f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{table}";',
        f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY;',
        f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY;',
    ]


# ---------------------------------------------------------------------------
# Per-request / per-transaction tenant binding
# ---------------------------------------------------------------------------
def set_current_org(org_id, *, local=True):
    """Bind ``app.current_org`` for this transaction.

    ``SET LOCAL`` by default, and that is the whole safety argument: the value
    is discarded at COMMIT or ROLLBACK, so a pooled connection cannot carry one
    tenant's id into the next request that borrows it. ``local=False`` exists
    only for the test suite, which needs a value that survives outside an
    explicit transaction.

    Parameterised through the driver, not interpolated: this value arrives from
    a JWT claim and a Host header.
    """
    if connection.vendor != "postgresql":
        return False
    statement = "SET LOCAL" if local else "SET"
    with connection.cursor() as cursor:
        if org_id is None:
            cursor.execute(f"{statement} {GUC} TO ''")
        else:
            cursor.execute(f"{statement} {GUC} TO %s", [str(org_id)])
    return True


def set_platform_mode(enabled, *, local=True):
    """Declare (or withdraw) platform scope for this transaction.

    Always called with an explicit value, never left unset: the middleware
    sets it 'off' for every tenant request precisely so a stale 'on' cannot be
    inherited. Like ``set_current_org``, ``SET LOCAL`` by default so it cannot
    outlive its transaction on a pooled connection.
    """
    if connection.vendor != "postgresql":
        return False
    statement = "SET LOCAL" if local else "SET"
    with connection.cursor() as cursor:
        cursor.execute(f"{statement} {PLATFORM_GUC} TO %s",
                       ["on" if enabled else "off"])
    return True


def bind(org_id, *, platform=False, local=True):
    """Set BOTH GUCs in ONE round trip. The per-request path.

    ``SET`` cannot be parameterised and cannot be batched with parameters, so
    the obvious implementation is two ``cursor.execute`` calls -- two network
    round trips added to every single request, which a query-budget test
    noticed immediately and was right to.

    ``set_config(name, value, is_local)`` is the function form of ``SET``, so
    two of them fit in one parameterised SELECT. ``is_local=true`` is exactly
    ``SET LOCAL``: discarded at COMMIT or ROLLBACK, which is the whole safety
    argument for pooled connections.

    Both values are always written, never left to whatever the connection last
    held. A stale ``app.platform = 'on'`` inherited from a previous request
    would let a tenant read every platform operator's row.
    """
    if connection.vendor != "postgresql":
        return False
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config(%s, %s, %s), set_config(%s, %s, %s)",
            [GUC, "" if org_id is None else str(org_id), local,
             PLATFORM_GUC, "on" if platform else "off", local])
    return True


def platform_mode():
    """Whether this connection has declared platform scope. For tests."""
    if connection.vendor != "postgresql":
        return False
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT current_setting('{PLATFORM_GUC}', true)")
        row = cursor.fetchone()
    return bool(row) and row[0] == "on"


def current_org():
    """What the database currently thinks the tenant is. For tests and health."""
    if connection.vendor != "postgresql":
        return None
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT NULLIF(current_setting('{GUC}', true), '')")
        row = cursor.fetchone()
    return row[0] if row else None


def reset_current_org():
    """Clear the binding. Used on the way out of a request, belt and braces."""
    return set_current_org(None, local=False)


def reset():
    """Clear BOTH bindings: no tenant, and no platform scope.

    The one to reach for in a test teardown. ``reset_current_org`` alone
    leaves ``app.platform`` at whatever the last block set, and a leftover
    'on' makes the next test's "nothing is visible" assertion pass or fail on
    what ran before it.
    """
    return bind(None, platform=False, local=False)


def is_enabled():
    """Is RLS actually on for this deployment?"""
    return (connection.vendor == "postgresql"
            and getattr(settings, "TENANCY_RLS_ENABLED", False))


# ---------------------------------------------------------------------------
# Introspection, for the conformance tests and the platform console
# ---------------------------------------------------------------------------
def policy_report():
    """``{table: {"rls": bool, "forced": bool, "policies": [...]}}``."""
    if connection.vendor != "postgresql":
        return {}
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = current_schema() AND c.relkind = 'r'
        """)
        state = {name: {"rls": rls, "forced": forced, "policies": []}
                 for name, rls, forced in cursor.fetchall()}
        cursor.execute("""
            SELECT tablename, policyname, qual, with_check
              FROM pg_policies WHERE schemaname = current_schema()
        """)
        for table, policy, qual, with_check in cursor.fetchall():
            if table in state:
                state[table]["policies"].append(
                    {"name": policy, "using": qual, "check": with_check})
    return state
