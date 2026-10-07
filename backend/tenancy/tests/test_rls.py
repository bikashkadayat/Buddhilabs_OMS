"""PostgreSQL Row-Level Security: the two-tenant conformance gate.

SKIPPED ENTIRELY ON SQLITE, and that is the honest shape of this file. SQLite
has no row-level security, so the development and CI database cannot enforce
the boundary these tests are about -- there, isolation rests on TenantManager
alone. Run the suite against PostgreSQL before a release; that is what makes
the gate a gate.

    DJANGO_SETTINGS_MODULE=... pytest tenancy/tests/test_rls.py

WHAT IS BEING PROVED
--------------------
Not "a policy exists" -- pg_policies will happily list a policy that filters
nothing, because a superuser and the table OWNER bypass RLS silently. What is
proved is the behaviour an attacker would actually meet:

    no app.current_org        -> 0 rows, through the ORM, through the
                                 `all_tenants` escape hatch, AND through raw
                                 cursor.execute()
    app.current_org = tenant  -> that tenant's rows and no others
    a write for another tenant -> refused by WITH CHECK
"""
import uuid

import pytest
from django.db import connection, transaction

from tenancy import rls

def _rls_enforced():
    """Is this run actually configured to ENFORCE row-level security?

    Gating the module on the flag, not merely on the backend, matters. The
    ordinary PostgreSQL suite runs as the MIGRATION role, which is BYPASSRLS on
    purpose -- a backfill is a cross-tenant rewrite. In that run these tests
    would fail for the right reason and the wrong purpose, so they skip.

    They are a GATE, not a unit test: run deliberately, as the application
    role, with TENANCY_RLS_ENABLED on, before a release.

        DJANGO_SETTINGS_MODULE=<app-role settings> TENANCY_RLS_ENABLED=1 \
            pytest tenancy/tests/test_rls.py
    """
    from django.conf import settings

    return (connection.vendor == "postgresql"
            and getattr(settings, "TENANCY_RLS_ENABLED", False))


pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        not _rls_enforced(),
        reason="RLS gate: needs PostgreSQL with TENANCY_RLS_ENABLED and the "
               "NOBYPASSRLS application role"),
]


# ---------------------------------------------------------------------------
# The preconditions. If any of these is wrong, every test below passes
# vacuously -- which is exactly how a decorative RLS setup looks.
# ---------------------------------------------------------------------------
def test_the_application_role_cannot_bypass_row_level_security():
    """THE precondition. A superuser or BYPASSRLS role ignores every policy."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT rolsuper, rolbypassrls FROM pg_roles "
                       "WHERE rolname = current_user")
        superuser, bypass = cursor.fetchone()
    assert not superuser, "the application role must not be a superuser"
    assert not bypass, "the application role must be NOBYPASSRLS"


def test_every_tenant_scoped_table_has_rls_enabled_and_forced():
    """FORCE matters: without it the table OWNER bypasses the policy."""
    report = rls.policy_report()
    missing_rls, missing_force, missing_policy = [], [], []
    for table, _nullable in rls.tenant_tables():
        state = report.get(table)
        if state is None:
            missing_rls.append(table); continue
        if not state["rls"]:
            missing_rls.append(table)
        if not state["forced"]:
            missing_force.append(table)
        if not any(p["name"] == rls.POLICY_NAME for p in state["policies"]):
            missing_policy.append(table)
    assert missing_rls == [], f"RLS not enabled: {missing_rls}"
    assert missing_force == [], f"RLS not FORCED: {missing_force}"
    assert missing_policy == [], f"no tenant policy: {missing_policy}"


def test_no_platform_table_has_a_tenant_policy():
    """The tenant registry and the billing records about tenants are global.

    A policy on tenancy_organization would make the platform console unable to
    list its own customers -- and would be a circular dependency besides, since
    resolving the tenant requires reading that table.
    """
    report = rls.policy_report()
    policed = [t for t in rls.platform_tables()
               if report.get(t, {}).get("policies")]
    assert policed == [], policed


def test_the_policy_is_null_safe():
    """`current_setting(..., true)` must be the missing_ok form.

    Without the second argument PostgreSQL RAISES on an unset GUC, which would
    turn every unscoped query into a 500 instead of an empty result -- and a
    500 that names the mechanism is a worse outcome than zero rows.
    """
    report = rls.policy_report()
    table = rls.tenant_tables()[0][0]
    policy = next(p for p in report[table]["policies"]
                  if p["name"] == rls.POLICY_NAME)
    assert "current_setting('app.current_org'::text, true)" in policy["using"]
    assert "NULLIF" in policy["using"]


# ---------------------------------------------------------------------------
# The behaviour
# ---------------------------------------------------------------------------
@pytest.fixture
def two_tenants(db):
    """NIF plus ABC School, each with one Department. SELF-PROVISIONING.

    Every test in this module is ``transaction=True``, so Django TRUNCATES
    every table on teardown -- including the migration-seeded Organization and
    Plan rows. A gate that depended on those surviving would pass once and then
    fail for the rest of the run, which is exactly what happened the first time
    this was written.

    So the fixture creates what it needs, from nothing, every time.
    """
    import datetime

    from leaves.models import Department

    from tenancy.context import tenant_context
    from tenancy.models import Organization, Plan, PlanPrice, Subscription

    # BOTH GUCs, not just the tenant: a leftover
    # `app.platform = 'on'` from an earlier block would make the
    # "nothing is visible" assertions below pass for the wrong reason.
    rls.reset()

    plan, _ = Plan.objects.get_or_create(
        code="gate-plan",
        defaults={"name": "Gate", "interval_months": 1, "trial_days": 14,
                  "grace_days": 7, "sort_order": 999})
    PlanPrice.objects.get_or_create(
        plan=plan, currency="NPR", effective_from=datetime.date(2020, 1, 1),
        defaults={"amount_minor": 1})

    orgs = []
    for slug, name, prefix in (("nif", "Nepal Internet Foundation", "NIFN"),
                               ("abcschool", "ABC School", "ABC")):
        org = Organization.objects.filter(slug=slug).first()
        if org is None:
            org = Organization.objects.create(
                name=name, slug=slug, document_prefix=prefix,
                email=f"admin@{slug}.test", status=Organization.Status.ACTIVE,
                subscription_status="active")
            Subscription.objects.create(
                organization=org, plan=plan, status="active",
                current_period_start=datetime.date(2020, 1, 1),
                current_period_end=datetime.date(2099, 12, 31))
        orgs.append(org)
    nif, abc = orgs

    for org, code in ((nif, "GATE-NIF"), (abc, "GATE-ABC")):
        rls.set_current_org(org.pk, local=False)
        with tenant_context(org):
            Department.all_tenants.get_or_create(
                organization=org, code=code,
                defaults={"name": f"Dept {code}"})
    yield nif, abc
    rls.reset()


def _raw_count(table):
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT count(*) FROM {table}")
        return cursor.fetchone()[0]


def test_with_no_tenant_bound_the_database_returns_zero_rows(two_tenants):
    """Part 4, stated exactly: forget the filter, get NOTHING.

    Checked three ways, because the point is that it holds no matter how the
    query was written: the scoped manager, the deliberate escape hatch, and
    hand-written SQL that never touched the ORM at all.
    """
    from leaves.models import Department

    rls.reset()
    assert Department.objects.count() == 0
    assert Department.all_tenants.count() == 0, (
        "the application-level escape hatch must not escape the DATABASE")
    assert _raw_count("leaves_department") == 0, "raw SQL is not scoped"


def test_each_tenant_sees_only_its_own_rows(two_tenants):
    from leaves.models import Department

    nif, abc = two_tenants
    rls.set_current_org(nif.pk, local=False)
    nif_codes = set(Department.all_tenants.values_list("code", flat=True))
    rls.set_current_org(abc.pk, local=False)
    abc_codes = set(Department.all_tenants.values_list("code", flat=True))

    assert "GATE-NIF" in nif_codes and "GATE-ABC" not in nif_codes
    assert "GATE-ABC" in abc_codes and "GATE-NIF" not in abc_codes
    assert not nif_codes & abc_codes


def test_raw_sql_sees_exactly_what_the_orm_sees(two_tenants):
    """An attacker who reaches raw SQL gains nothing."""
    from leaves.models import Department

    nif, abc = two_tenants
    for org in (nif, abc):
        rls.set_current_org(org.pk, local=False)
        assert _raw_count("leaves_department") == Department.all_tenants.count()


def test_a_write_for_another_tenant_is_refused(two_tenants):
    """WITH CHECK, not just USING. USING alone would permit the INSERT."""
    from django.db.utils import ProgrammingError

    from leaves.models import Department

    nif, abc = two_tenants
    rls.set_current_org(nif.pk, local=False)
    with pytest.raises((ProgrammingError, Exception)) as caught:
        with transaction.atomic():
            Department.all_tenants.create(
                organization=abc, code="SMUGGLED", name="Smuggled")
    assert "policy" in str(caught.value).lower()


def test_an_unknown_tenant_id_sees_nothing(two_tenants):
    from leaves.models import Department

    rls.set_current_org(uuid.uuid4(), local=False)
    assert Department.all_tenants.count() == 0


# ---------------------------------------------------------------------------
# Part 6: connection safety. The failure mode CONN_MAX_AGE makes possible.
# ---------------------------------------------------------------------------
def test_set_local_does_not_survive_its_transaction(two_tenants):
    """The property that makes pooled connections safe.

    `SET LOCAL` is discarded at COMMIT or ROLLBACK, so one request's tenant
    cannot be inherited by the next request that borrows the same connection.
    A plain `SET` would persist for the life of the connection -- and with
    CONN_MAX_AGE=60 that is many requests.
    """
    nif, _abc = two_tenants
    rls.reset_current_org()
    with transaction.atomic():
        rls.set_current_org(nif.pk)          # local=True
        assert rls.current_org() == str(nif.pk)
    assert rls.current_org() is None, "SET LOCAL leaked past its transaction"


def test_a_b_a_on_one_connection_never_crosses(two_tenants):
    """Part 6's exact scenario: tenant A, then B, then A again.

    Same process, same pooled connection, three transactions. Each must see
    only its own rows, and the third must not inherit anything from the second.
    """
    from leaves.models import Department

    nif, abc = two_tenants
    rls.reset_current_org()
    seen = []
    for org in (nif, abc, nif):
        with transaction.atomic():
            rls.set_current_org(org.pk)
            seen.append(set(Department.all_tenants
                            .values_list("code", flat=True)))
        # Between transactions the binding is gone, so nothing is visible.
        assert Department.all_tenants.count() == 0

    assert "GATE-NIF" in seen[0] and "GATE-ABC" not in seen[0]
    assert "GATE-ABC" in seen[1] and "GATE-NIF" not in seen[1]
    assert seen[2] == seen[0], "the third request did not get a clean binding"


def test_the_connection_is_actually_reused(two_tenants):
    """Otherwise the test above proves nothing about pooling."""
    from django.conf import settings

    assert settings.DATABASES["default"].get("CONN_MAX_AGE"), (
        "this test only means something with persistent connections")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        first = cursor.fetchone()[0]
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        assert cursor.fetchone()[0] == first


# ---------------------------------------------------------------------------
# Part 8: the gate, across every business domain
# ---------------------------------------------------------------------------
DOMAIN_TABLES = [
    ("tasks", "tasks_task"),
    ("leave", "leaves_leave"),
    ("attendance", "attendance_attendance"),
    ("memo", "memos_memo"),
    ("minute", "minutes_minute"),
    ("circular", "circulars_circular"),
    ("inventory", "inventory_inventoryitem"),
    ("documents", "documents_issueddocument"),
    ("notifications", "notifications_notification"),
    ("audit log", "audit_auditlog"),
    ("analytics source", "biometric_attendancepunch"),
]


@pytest.mark.parametrize("domain,table", DOMAIN_TABLES,
                         ids=[d for d, _ in DOMAIN_TABLES])
def test_every_business_domain_returns_nothing_without_a_tenant(domain, table,
                                                                 two_tenants):
    """One parametrised case per domain Part 8 names."""
    rls.set_current_org(None, local=False)
    assert _raw_count(table) == 0, f"{domain} ({table}) leaked with no tenant"


# ---------------------------------------------------------------------------
# The platform-scope clause (Phase S6)
# ---------------------------------------------------------------------------
#
# WHAT IT IS FOR. A platform operator's User row has `organization IS NULL` by
# database constraint -- that pairing is what makes "a company administrator
# cannot administer the platform" structural. But `NULL = anything` is NULL,
# never true, so under the Phase S5 policy that row was invisible to the very
# role that has to authenticate it: with RLS on, nobody could sign in to the
# platform console. These tests pin both halves of the fix -- that platform
# scope admits unowned rows, and that nothing else changed.
def _make_platform_user():
    from django.contrib.auth import get_user_model

    from tenancy.context import no_tenant

    User = get_user_model()
    with no_tenant():
        return User.all_tenants.create(
            username="gate-ops", email="gate-ops@platform.test",
            is_platform_staff=True, organization=None)


def test_the_policy_admits_unowned_rows_only_in_declared_platform_scope():
    report = rls.policy_report()
    table = rls.tenant_tables()[0][0]
    policy = next(p for p in report[table]["policies"]
                  if p["name"] == rls.POLICY_NAME)
    using = policy["using"]
    assert "organization_id IS NULL" in using
    assert "current_setting('app.platform'::text, true)" in using
    # And the tenant clause is still there and unchanged.
    assert "current_setting('app.current_org'::text, true)" in using


def test_a_platform_account_can_be_created_and_read_in_platform_scope(two_tenants):
    """The capability Phase S5 had removed without anyone noticing."""
    from django.contrib.auth import get_user_model

    from tenancy.context import no_tenant

    User = get_user_model()
    operator = _make_platform_user()

    with no_tenant():
        assert User.all_tenants.filter(pk=operator.pk).exists()


def test_a_tenant_cannot_see_a_platform_account(two_tenants):
    """The row holds a password hash for the most privileged account there is.

    A tenant-bound connection must not reach it through ANY manager, and must
    not reach it through raw SQL either.
    """
    from django.contrib.auth import get_user_model

    from tenancy.context import tenant_context

    User = get_user_model()
    operator = _make_platform_user()
    nif, _abc = two_tenants

    # local=False: these tests are `transaction=True`, so they run in
    # autocommit. `SET LOCAL` outside a transaction block is a no-op
    # (PostgreSQL warns and moves on), which would leave whatever the
    # fixture last bound in place and make this pass for the wrong tenant.
    rls.bind(nif.pk, platform=False, local=False)
    with tenant_context(nif):
        assert not User.objects.filter(pk=operator.pk).exists()
        assert not User.all_tenants.filter(pk=operator.pk).exists()
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM users_user WHERE id = %s",
                       [str(operator.pk)])
        assert cursor.fetchone()[0] == 0


def test_an_unowned_row_is_invisible_without_platform_scope(two_tenants):
    """FAIL CLOSED, and this is the case that chose the design.

    The cheaper policy would admit unowned rows whenever no tenant is bound.
    The middleware logs and continues when a tenant bind fails, so that
    version would hand a TENANT request every platform operator's row. Scope
    has to be declared, not inferred from an absence.
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()
    operator = _make_platform_user()

    rls.reset()                        # no tenant AND no platform scope
    assert not User.all_tenants.filter(pk=operator.pk).exists()

    rls.bind(None, platform=True, local=False)   # declared
    assert User.all_tenants.filter(pk=operator.pk).exists()


def test_platform_scope_does_not_reveal_one_tenants_rows_to_another(two_tenants):
    """The clause admits NULLs, and NOTHING else.

    Belt and braces: even a connection that has (wrongly) declared platform
    scope while a tenant is bound must not see the other tenant's rows.
    """
    from leaves.models import Department

    nif, abc = two_tenants
    rls.bind(nif.pk, platform=True, local=False)

    codes = set(Department.all_tenants.values_list("code", flat=True))
    assert "GATE-NIF" in codes
    assert "GATE-ABC" not in codes, (
        "platform scope widened tenant visibility, which it must never do")


def test_platform_scope_does_not_survive_its_transaction(two_tenants):
    """Same guarantee as the tenant binding, for the same pooled-connection
    reason: a leftover 'on' would let the next request that borrows this
    connection read every platform account."""
    # INSIDE a transaction here, deliberately: that is the only place
    # `SET LOCAL` means anything, and the guarantee under test is that the
    # value dies with the transaction.
    with transaction.atomic():
        rls.bind(None, platform=True)
        assert rls.platform_mode() is True
    assert rls.platform_mode() is False


def test_the_middleware_actually_binds_the_tenant(two_tenants):
    """THE test this file was missing, and the bug it found.

    Every other test here binds the tenant by calling `rls.set_current_org`
    directly, so none of them exercised the path a real request takes. That
    path was broken: `SET LOCAL` is transaction-scoped, Phase S5 relied on
    ATOMIC_REQUESTS to supply the transaction, and ATOMIC_REQUESTS wraps the
    VIEW rather than the middleware stack. The middleware's binding therefore
    ran in autocommit, where PostgreSQL warns and discards it -- so with RLS
    enabled in production every tenant query would have returned zero rows.

    This drives the middleware and reads the GUC from inside the request,
    which is the only place it can be observed: it is deliberately gone again
    by the time the transaction ends.
    """
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory

    from tenancy.middleware import TenantResolutionMiddleware

    nif, _abc = two_tenants
    rls.reset()

    seen = {}

    def view(request):
        from leaves.models import Department

        seen["org"] = rls.current_org()
        seen["platform"] = rls.platform_mode()
        seen["rows"] = set(Department.objects.values_list("code", flat=True))
        return "ok"

    request = RequestFactory().get("/api/v1/leaves/", HTTP_HOST="testserver")
    request.user = AnonymousUser()
    TenantResolutionMiddleware(view)(request)

    assert seen["org"] == str(nif.pk), (
        "the middleware did not bind the tenant for the request")
    assert "GATE-NIF" in seen["rows"], "the request saw none of its own rows"
    assert "GATE-ABC" not in seen["rows"], (
        "the request saw another tenant's rows")
    # And it is gone afterwards, which is the pooled-connection guarantee.
    assert rls.current_org() is None


def test_the_middleware_writes_platform_mode_off_for_a_tenant_request(two_tenants):
    """Explicitly 'off', never merely left unset.

    A stale 'on' inherited from a pooled connection is the whole exposure, so
    the value is written on every request rather than assumed. Observed from
    inside the request, because the binding is transaction-scoped by design.
    """
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory

    from tenancy.middleware import TenantResolutionMiddleware

    # A leftover 'on', as a pooled connection might carry.
    rls.bind(None, platform=True, local=False)

    seen = {}

    def view(request):
        seen["platform"] = rls.platform_mode()
        return "ok"

    request = RequestFactory().get("/api/v1/leaves/", HTTP_HOST="testserver")
    request.user = AnonymousUser()
    TenantResolutionMiddleware(view)(request)

    assert seen["platform"] is False, (
        "a tenant request inherited platform scope")


# ---------------------------------------------------------------------------
# Background threads (Phase S6)
# ---------------------------------------------------------------------------
#
# THE UNIT OF TENANT BINDING IS A CONNECTION, NOT A REQUEST.
#
# `app.current_org` is connection state. A thread gets its own connection and
# inherits nothing, so anything that spawns one has to bind the tenant inside
# it. Two production sites do spawn threads -- report generation and
# notification email -- and both were silently broken under RLS: the report
# thread could not see the ReportRun row it was given the id of, and the email
# thread could not write the NotificationLog row that records whether the
# message was ever sent.
#
# These tests assert the rule rather than the two call sites, because the next
# thread somebody adds will have the same problem.
def test_a_thread_sees_nothing_until_it_binds_its_own_tenant(two_tenants):
    import threading

    from django.db import connection as default_connection
    from leaves.models import Department

    from django.db.backends.signals import connection_created

    nif, _abc = two_tenants
    rls.bind(nif.pk, platform=False, local=False)
    # The spawning thread can see its tenant's rows.
    assert Department.objects.filter(code="GATE-NIF").exists()

    # THE TEST HARNESS BINDS EVERY NEW CONNECTION, so it has to stand down for
    # the length of this test or the unbound case cannot exist. That hook is
    # the harness standing in for the middleware (see conftest.py); here we
    # are testing what happens WITHOUT one, which is what production code in a
    # thread actually faces.
    from conftest import _bind_on_connection_created

    connection_created.disconnect(dispatch_uid="conftest.rls_bind")

    seen = {}

    def worker(bind):
        from tenancy.context import tenant_context

        try:
            if bind:
                with tenant_context(nif):
                    seen[bind] = Department.objects.filter(
                        code="GATE-NIF").exists()
            else:
                seen[bind] = Department.objects.filter(
                    code="GATE-NIF").exists()
        finally:
            default_connection.close()

    try:
        for bind in (False, True):
            thread = threading.Thread(target=worker, args=(bind,))
            thread.start()
            thread.join()
    finally:
        connection_created.connect(_bind_on_connection_created,
                                   dispatch_uid="conftest.rls_bind")

    assert seen[False] is False, (
        "an unbound thread saw tenant rows -- the binding is leaking between "
        "connections")
    assert seen[True] is True, (
        "a thread that entered tenant_context still could not see its own "
        "tenant's rows")


def test_the_report_thread_binds_the_tenant_it_was_given(two_tenants):
    """reports.views._spawn_generation takes the organization id for this."""
    import inspect

    from reports import views

    source = inspect.getsource(views._spawn_generation)
    assert "tenant_context" in source, (
        "the report generation thread does not bind a tenant; under RLS it "
        "cannot see the ReportRun it was asked to generate")
    assert "organization_id" in inspect.signature(
        views._spawn_generation).parameters


def test_the_email_thread_binds_the_recipients_tenant(two_tenants):
    """notifications.emails.dispatch_email does the same for its send thread."""
    import inspect

    from notifications import emails

    source = inspect.getsource(emails._send_in_tenant)
    assert "tenant_context" in source, (
        "the notification email thread does not bind a tenant; under RLS the "
        "NotificationLog row that records delivery cannot be written")
