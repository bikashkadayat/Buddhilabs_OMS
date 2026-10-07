import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _notifications_sync(settings):
    """
    Send notification emails synchronously in tests so background threads never
    race the shared mail.outbox (or the sqlite connection). Production keeps the
    async thread behaviour (NOTIFICATIONS_RUN_SYNC defaults False).
    """
    settings.NOTIFICATIONS_RUN_SYNC = True


@pytest.fixture(autouse=True)
def _reset_throttle_cache():
    """
    H6 adds global DRF throttling backed by the cache. Reset it around every
    test so accumulated request counts never leak between tests and trip 429s.

    WARMS THE TENANT HOST MAPPING AFTER CLEARING (Phase S4).

    Since TenantManager became the default manager on the Phase B models,
    every request resolves its tenant. That resolution is cached and costs
    nothing warm -- but `cache.clear()` above makes the FIRST request of every
    test pay for it, which showed up as four query-budget tests failing: they
    compare "cost for 3 rows" against "cost for 20 rows" in the same test, and
    only the first of the two requests was paying the one-time lookup.

    Priming it here removes a fixed, one-off cost from those comparisons
    without weakening what they assert -- they measure how cost GROWS with row
    count, and a constant is not growth. Wrapped because most tests have no
    database at all.
    """
    cache.clear()
    _warm_tenant_resolution()
    yield
    cache.clear()


# ---------------------------------------------------------------------------
# Query budgets, and what the tenant binding costs
# ---------------------------------------------------------------------------
@pytest.fixture
def tenant_binding_queries():
    """Extra round trips per HTTP request that the tenant binding costs.

    ZERO normally. THREE when row-level security is enforced, and the
    breakdown matters because only one of them exists in production:

      1 ``set_config`` round trip -- ``TenantResolutionMiddleware`` writes
        ``app.current_org`` and ``app.platform`` for the request. Both in one
        statement, which is why ``tenancy.rls.bind`` exists; the first version
        used two ``SET`` statements and these budgets said so.

      2 SAVEPOINT / RELEASE SAVEPOINT -- the middleware opens a transaction
        for ``SET LOCAL`` to be local to. In production that is the outermost
        transaction and costs no logged statement. Inside a test, which is
        already wrapped in one, it becomes a savepoint pair.

    IT IS BUDGETED RATHER THAN EXCLUDED, deliberately. A budget that quietly
    ignored per-request cost would stop being a measure of what the request
    costs.
    """
    try:
        from tenancy import rls

        return 3 if rls.is_enabled() else 0
    except Exception:  # noqa: BLE001 - tenancy unavailable
        return 0


# ---------------------------------------------------------------------------
# Binding the tenant inside the database, for an RLS-enforced run
# ---------------------------------------------------------------------------
#
# WHY THIS IS ITS OWN FIXTURE, AND WHY IT ASKS FOR `db` (Phase S6, R18)
#
# Phase S5 bound the tenant from inside `_reset_throttle_cache` above. That
# fixture is autouse and requests no database fixture, so pytest sets it up
# BEFORE pytest-django has unblocked database access -- and the resolver's
# first query therefore raised
#
#     RuntimeError: Database access not allowed, use the "django_db" mark
#
# which the bare `except` swallowed. The binding silently did nothing. Under
# TENANCY_RLS_ENABLED that is not a quiet degradation: with no
# `app.current_org` set, every INSERT into a tenant table is refused by the
# policy's WITH CHECK, so the full suite failed at the first fixture that
# creates a user, with "new row violates row-level security policy".
#
# This fixture forces the ordering by REQUESTING the database fixture the test
# is already using. It never introduces database access to a test that had
# none -- `_uses_database` is checked first -- and it is a no-op on SQLite and
# on any PostgreSQL run with RLS off, which is every run but the gate.
# THE UNIT OF TENANT BINDING IS A CONNECTION, NOT A TEST (Phase S6, R18)
#
# This took three attempts to get right, and each wrong answer is worth
# recording because the same mistake is available in application code.
#
#   1. Binding from an autouse fixture that requested no database fixture ran
#      before pytest-django unblocked the database, so the resolver's query
#      raised and the bare `except` swallowed it. The binding silently did
#      nothing, and every INSERT into a tenant table was refused.
#
#   2. Binding per test cannot cover a MODULE-scoped fixture: pytest sets
#      those up first, so their writes happen before any per-test binding
#      exists. Several suites build their world that way, and under RLS every
#      one of those writes was refused.
#
#   3. Binding once per SESSION outside any transaction looked like it fixed
#      that -- and did, until the first `transaction=True` test, whose
#      teardown calls `connections.close_all()`. A new connection carries no
#      session GUC, so the binding was gone for the rest of the run. The
#      failure count did not move at all between attempts 2 and 3, which is
#      what gave it away.
#
# So the bind is attached to CONNECTION CREATION. That is the real boundary:
# `app.current_org` is connection state, and anything that opens its own
# connection -- a new pool entry, a thread, a Channels worker -- must bind it
# or see nothing. The production path is the same rule in a different place:
# TenantResolutionMiddleware binds per request, inside the request's
# transaction. A background thread has to do it itself, which is why
# tasks/tests/test_numbering.py enters a `tenant_context` inside each thread.
_DEFAULT_ORG_ID = None


def _bind_on_connection_created(sender, connection, **kwargs):
    """Bind the default tenant on every connection this run opens.

    Uses a cached id and issues exactly one `SET`, so it adds no query to
    connection setup -- and critically, it cannot recurse: resolving the
    organization would need a connection, and we are inside creating one.
    """
    global _DEFAULT_ORG_ID

    if connection.vendor != "postgresql" or _DEFAULT_ORG_ID is None:
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET app.current_org TO %s", [str(_DEFAULT_ORG_ID)])
    except Exception:  # noqa: BLE001 - a half-built connection; the per-test
        pass           # bind and tenant_context() still apply


@pytest.fixture(scope="session", autouse=True)
def _bind_tenant_for_rls_session(request):
    # Every exit path yields: this is a generator fixture, and a bare `return`
    # before the yield makes pytest fail every test in the session with
    # "did not yield a value".
    if not _rls_configured():
        yield
        return
    try:
        blocker = request.getfixturevalue("django_db_blocker")
        request.getfixturevalue("django_db_setup")
    except Exception:  # noqa: BLE001 - no database configured for this run
        yield
        return

    global _DEFAULT_ORG_ID
    from django.db.backends.signals import connection_created

    from tenancy import resolver

    with blocker.unblock():
        # Resolve ONCE, here, where a connection already exists and the
        # database is unblocked.
        _DEFAULT_ORG_ID = resolver.default_organization_id(_default_slug())
        _bind_default_tenant_for_rls()

    connection_created.connect(_bind_on_connection_created,
                               dispatch_uid="conftest.rls_bind")
    yield
    connection_created.disconnect(dispatch_uid="conftest.rls_bind")


@pytest.fixture(autouse=True)
def _bind_tenant_for_rls(request):
    if not _rls_configured():
        yield
        return

    for name in ("transactional_db", "live_server", "db"):
        if name in request.fixturenames:
            request.getfixturevalue(name)
            break
    else:
        if not _uses_database(request):
            yield
            return
        request.getfixturevalue("db")

    _bind_default_tenant_for_rls()
    yield


def _rls_configured():
    """Is this run enforcing row-level security? Settings only, NO database.

    Deliberately does not touch the connection: this is consulted from an
    autouse fixture on every test in the suite, including the ~400 with no
    database at all.
    """
    try:
        from django.conf import settings

        if not getattr(settings, "TENANCY_RLS_ENABLED", False):
            return False
        return "postgresql" in settings.DATABASES["default"]["ENGINE"]
    except Exception:  # noqa: BLE001 - settings not configured
        return False


def _bind_default_tenant_for_rls():
    """Bind `app.current_org` for the test, when RLS is enforced.

    Only does anything on PostgreSQL with TENANCY_RLS_ENABLED. It is the test
    harness standing in for TenantResolutionMiddleware: a request binds the
    tenant in the database, and a test that calls the ORM directly has no
    request to do it. Without this, every RLS-enabled run would see zero rows
    everywhere -- which is RLS working correctly and tells us nothing.

    `local=False` because there is no request transaction to scope to here;
    the value is session-level and PostgreSQL reverts it when the test's own
    transaction rolls back, so each test rebinds its own.

    Tests that need a DIFFERENT tenant use `tenancy.context.tenant_context`,
    which rebinds and restores on its own.
    """
    try:
        from tenancy import resolver, rls

        if not rls.is_enabled():
            return
        # THE CACHED ID FIRST, AND THAT ORDER IS LOAD-BEARING.
        #
        # This is also called from `pytest_runtest_setup` to restore the
        # migration seed after a transactional test has truncated every table
        # -- including `tenancy_organization`. Resolving the tenant from the
        # database at that moment asks "which organization is the default?" of
        # a table that has just been emptied, gets None, binds nothing, and
        # the restore's own INSERTs are then refused by the policy's WITH
        # CHECK. The run never recovers: the seed is gone and every later test
        # fails on configuration it was right to assume.
        #
        # The id is resolved once per session, before anything can truncate,
        # which is why it is still correct here.
        org_id = _DEFAULT_ORG_ID
        if org_id is None:
            org_id = resolver.default_organization_id(_default_slug())
        rls.set_current_org(org_id, local=False)
    except Exception:  # noqa: BLE001 - no DB, or tenancy not migrated yet
        pass


def _default_slug():
    """The slug the harness treats as "this deployment's tenant".

    NAMED, NOT GUESSED, and that is the whole of a bug that cost three
    attempts at R18.

    `resolver.default_organization_id("")` means "the only organization that
    exists", and it deliberately returns None once there are two -- that
    refusal is what stops the single-tenant compatibility shim leaking when
    the platform grows. The harness used to pass `""`, which worked on a
    freshly migrated database and then silently stopped the moment any test
    provisioned a second tenant. With nothing bound, every INSERT into a
    tenant table is refused by the policy's WITH CHECK, and the suite fails in
    a way that looks like an application bug rather than a harness one.

    So it reads TENANCY_DEFAULT_SLUG, exactly as TenantResolutionMiddleware
    does.
    """
    from django.conf import settings

    return getattr(settings, "TENANCY_DEFAULT_SLUG", "") or ""


def _warm_tenant_resolution():
    """Populate the host -> organization cache, if a database is available."""
    try:
        from tenancy import resolver

        resolver.default_organization_id(_default_slug())
        resolver.resolve_id("testserver")
    except Exception:  # noqa: BLE001 - no DB, no migrations, or no tenancy yet
        pass


# ---------------------------------------------------------------------------
# Migration-seeded master data survives a transactional test's flush
# ---------------------------------------------------------------------------
#
# THE PROBLEM
#
# A test marked `django_db(transaction=True)` is a TransactionTestCase, and
# Django TRUNCATES every table on its teardown. That takes the rows created by
# data migrations with it — MinuteType (minutes/0002, 0009), LeaveType/Holiday/
# EntitlementRule (leaves/0005, 0010, 0013), AttendancePolicy and Shift
# (attendance), TaskTemplate (tasks) — and nothing puts them back. Measured:
# 15 MinuteType rows before a transactional test, 0 after.
#
# Whatever runs next then fails on a lookup it was right to assume would work:
#
#     MinuteType.objects.get(code="department")   -> MinuteType.DoesNotExist
#
# Which tests fail depends on collection order and, under `-n auto --dist
# loadscope`, on which worker a module lands. That is why the suite passes
# locally and fails in CI, and why it fails on a different module each time.
#
# WHY RESTORING AT SETUP, NOT TEARDOWN
#
# Three modules (leaves, biometric, inventory) each grew their own module-scoped
# teardown fixture for this, and biometric's docstring records why function
# scope was rejected: "a function-scoped teardown is not guaranteed to run after
# pytest-django's flush". That is true, and it is the whole difficulty — at
# teardown you are racing the flush.
#
# Restoring at SETUP has no such race. By the time a test sets up, the previous
# test's flush has already happened; there is nothing left to race. So the
# restore is armed by a transactional test finishing and performed by the next
# database test starting.
#
# WHY THE WHOLE SNAPSHOT, NOT A LIST OF MODELS
#
# The three existing fixtures each restore `leaves.` only, which is why the
# minutes, attendance and tasks suites were left exposed. Django's own
# serialisation is used here instead of a hand-kept model list, so a data
# migration added tomorrow is covered without anyone remembering to come back.

_SEED_LOST_TO_FLUSH = False
_SEED_SNAPSHOT = None

# Rebuilt by the post_migrate signal that follows a flush. Re-inserting them
# collides with the fresh rows on their natural-key unique constraints.
_REBUILT_BY_POST_MIGRATE = ("contenttypes.", "auth.permission")

_DB_FIXTURES = frozenset({"db", "transactional_db", "django_db_setup",
                          "django_db_reset_sequences", "live_server"})


def _uses_database(request):
    if _DB_FIXTURES.intersection(request.fixturenames):
        return True
    return request.node.get_closest_marker("django_db") is not None


def _is_transactional(request):
    if {"transactional_db", "live_server"}.intersection(request.fixturenames):
        return True
    marker = request.node.get_closest_marker("django_db")
    if marker is None:
        return False
    if marker.kwargs.get("transaction"):
        return True
    # Positional form: django_db(True)
    return bool(marker.args and marker.args[0])


@pytest.fixture(scope="session")
def _migration_seed_snapshot(django_db_setup, django_db_blocker):
    """Every migration-seeded row, serialised once on the freshly migrated database.

    Taken before any test has run, so it holds exactly what the migrations
    produced and nothing a test created.
    """
    import json

    from django.db import connection

    global _SEED_SNAPSHOT

    with django_db_blocker.unblock():
        # BIND THE TENANT BEFORE SERIALISING (Phase S5).
        #
        # Under row-level security every tenant table is invisible to a
        # connection with no `app.current_org` set -- so an unbound snapshot
        # would be EMPTY, and the restore after the first transactional test
        # would put nothing back. The symptom was
        # `Organization.DoesNotExist` in every test that ran after one, because
        # the flush had truncated the seeded rows and there was no snapshot to
        # restore them from.
        _bind_default_tenant_for_rls()
        rows = json.loads(connection.creation.serialize_db_to_string())

    kept = [row for row in rows
            if not row["model"].startswith(_REBUILT_BY_POST_MIGRATE)]

    # TENANCY ROWS FIRST (Phase S2). `deserialize_db_from_string` saves objects
    # in the order they appear, and the serialiser emits them in INSTALLED_APPS
    # order -- which puts `tenancy` LAST, because it is the newest app. Now that
    # 27 Phase A tables carry a non-null FK to tenancy.Organization, restoring
    # in that order inserts a Department before the Organization it points at
    # and the database rejects it:
    #
    #     IntegrityError: The row in table 'leaves_department' ... has an
    #     invalid foreign key
    #
    # Sorting the parents to the front is the whole fix. It is stable, so the
    # relative order of everything else is untouched.
    _SEED_SNAPSHOT = json.dumps(
        sorted(kept, key=lambda row: not row["model"].startswith("tenancy.")))
    return _SEED_SNAPSHOT


@pytest.fixture(autouse=True)
def _arm_migration_seed_restore(request):
    """Take the snapshot on the first database test, and note when a flush is due.

    This fixture does NOT restore. Anything written from inside a fixture lands
    in the transaction pytest-django has already opened around the test, and is
    rolled back the moment that test ends — which made an in-fixture restore
    appear to work for exactly one test and no others. The restore therefore
    happens in `pytest_runtest_setup` below, before any transaction exists.
    """
    global _SEED_LOST_TO_FLUSH

    if _uses_database(request):
        # Requested eagerly, so the snapshot is taken on the first database test
        # of the session — i.e. before anything has had a chance to flush it.
        request.getfixturevalue("_migration_seed_snapshot")

    yield

    if _is_transactional(request):
        _SEED_LOST_TO_FLUSH = True


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Restore the migration seed before the next test's fixtures run.

    `tryfirst` puts this ahead of the builtin hook that sets fixtures up, so the
    write happens outside pytest-django's per-test transaction and commits.
    """
    global _SEED_LOST_TO_FLUSH

    if not (_SEED_LOST_TO_FLUSH and _SEED_SNAPSHOT):
        return

    from django.db import connection
    from pytest_django.plugin import blocking_manager_key

    blocker = item.config.stash[blocking_manager_key]
    with blocker.unblock():
        # Same reason as the snapshot: a restore needs the tenant bound, or
        # the policy's WITH CHECK refuses every row it tries to write back.
        _bind_default_tenant_for_rls()
        # deserialize saves by primary key, so this is an upsert: rows a flush
        # left behind are rewritten rather than duplicated.
        connection.creation.deserialize_db_from_string(_SEED_SNAPSHOT)
    _SEED_LOST_TO_FLUSH = False
