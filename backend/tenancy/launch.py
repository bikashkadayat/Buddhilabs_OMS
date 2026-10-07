"""Phase S6.75 Parts 1 and 5: is this platform fit to be sold to strangers?

WHAT THIS MODULE IS FOR, AND WHY IT IS NOT JUST ANOTHER HEALTH CHECK
--------------------------------------------------------------------
``console.system_health`` answers "is the platform working right now" --
database up, cache up, queues drained. This answers a different question:
"would a customer who arrived in the next five minutes get a working
workspace, or a broken promise?"

They come apart in exactly the way that matters. A platform with a healthy
database, a warm cache and no pending payments is perfectly *working* and
completely unfit to sell if it has no purchasable plan: every registration
that reaches verification will be refused, after the customer has filled in
a form, received an email and clicked a link. Nothing in `system_health`
notices, because nothing is broken. It was never set up.

So each check here asks about a DEPENDENCY OF SELLING, and each one is
graded:

  critical   a customer would hit this. Launch is blocked.
  important  an operator would hit this, or it degrades under load.
  advisory   worth fixing, nobody is harmed today.

HOW "REFUSE STARTUP" ACTUALLY WORKS, STATED HONESTLY
----------------------------------------------------
``tenancy.checks`` turns the critical failures here into Django
``Error``s, which makes ``manage.py check``, ``migrate``, ``runserver`` and
any deploy step that runs them fail outright. That is the enforcement, and it
is real.

What it is NOT: a WSGI or ASGI server does not run Django's system checks on
boot. Gunicorn and Daphne will happily serve a misconfigured platform. So the
deployment has to run ``manage.py check --deploy`` as a gate -- which is why
this phase also produces a deployment checklist with that step on it rather
than claiming the code can prevent every deployment mistake by itself.
"""
import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)

CRITICAL = "critical"
IMPORTANT = "important"
ADVISORY = "advisory"

_WEIGHT = {CRITICAL: 3, IMPORTANT: 2, ADVISORY: 1}


def saas_mode():
    """Is this deployment selling workspaces to strangers?

    Two settings, and the distinction matters for grading: a multi-tenant
    deployment that provisions through the console only (S6) has different
    critical dependencies from one with a public signup form (S7). SMTP is
    the clearest case -- an operator who provisions by hand can read a
    temporary password off the screen, where a self-service customer who
    never receives the verification email simply has no workspace.
    """
    return {
        "tenancy": bool(getattr(settings, "TENANCY_ENABLED", False)),
        "public_registration": bool(
            getattr(settings, "TENANCY_PUBLIC_REGISTRATION", False)),
    }


def _result(key, label, ready, severity, detail, hint=""):
    return {"key": key, "label": label, "ready": bool(ready),
            "severity": severity, "detail": detail, "hint": hint}


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------
def check_database():
    from django.db import connection

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return _result("database", "Database", True, CRITICAL,
                       f"{connection.vendor} reachable")
    except Exception as exc:                       # noqa: BLE001
        return _result("database", "Database", False, CRITICAL,
                       f"unreachable: {type(exc).__name__}",
                       "Nothing works without this. Check DATABASE_URL and "
                       "that the server is accepting connections.")


def check_connection_budget():
    """Will PostgreSQL run out of connection slots before the platform does?

    PHASE S10 PART 3 FOUND THIS BY MEASURING IT. The load test provisioned
    100 tenants and drove 32 concurrent sessions against ONE Daphne process,
    and 14% of requests answered HTTP 500:

        FATAL: remaining connection slots are reserved for roles with the
        SUPERUSER attribute

    The arithmetic, which nothing was doing:

      * ``CONN_MAX_AGE`` defaults to 60 here, so every worker THREAD keeps
        its own PostgreSQL connection alive between requests.
      * An ASGI deployment runs sync views in a thread pool. asgiref's
        default is ``min(32, cpu_count + 4)`` threads PER PROCESS.
      * ``max_connections`` defaults to 100, of which
        ``superuser_reserved_connections`` are not available to the
        application role at all.

    So one process can hold ~36 connections and four gunicorn workers can
    want ~144 -- against 97 usable. The platform does not degrade when it
    runs out; it serves 500s, which is the worst failure mode available
    because it looks like an application bug.

    THE DESIGN IS ALREADY POOLER-FRIENDLY, which is the good news and worth
    writing down: tenant binding uses ``SET LOCAL`` inside
    ``ATOMIC_REQUESTS``, so it is scoped to the transaction rather than the
    session -- which means pgbouncer in TRANSACTION mode is safe here, where
    a session-scoped ``SET`` would have made it unusable.

    Graded `important` rather than `critical`: the numbers are a deployment
    decision, this check cannot see the worker count from inside the
    process, and refusing a launch over an estimate would be a gate people
    learn to skip. It states the budget and names what to do.
    """
    from django.db import connection

    if connection.vendor != "postgresql":
        return _result("connections", "Connection budget", True, IMPORTANT,
                       f"{connection.vendor}: not applicable")

    conn_max_age = settings.DATABASES.get("default", {}).get("CONN_MAX_AGE", 0)
    # What the deployment says it runs. Named so a deployment can tell the
    # check the truth instead of being graded against a guess.
    processes = int(os.getenv("WEB_CONCURRENCY", "4"))
    threads = int(os.getenv("WEB_THREADS", "0")) or _default_thread_pool()

    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW max_connections")
            maximum = int(cursor.fetchone()[0])
            cursor.execute("SHOW superuser_reserved_connections")
            reserved = int(cursor.fetchone()[0])
    except Exception as exc:                           # noqa: BLE001
        return _result("connections", "Connection budget", False, IMPORTANT,
                       f"could not be read: {type(exc).__name__}",
                       "Grant the application role access to SHOW "
                       "max_connections, or set it explicitly.")

    usable = maximum - reserved
    if not conn_max_age:
        return _result(
            "connections", "Connection budget", True, IMPORTANT,
            f"CONN_MAX_AGE is 0, so connections are not held between "
            f"requests; {usable} slots available",
            "A new connection per request costs a few milliseconds. That is "
            "the safe setting without a pooler.")

    # A DEPLOYMENT THAT HAS A POOLER SAYS SO, and is then graded on the
    # pooler's budget rather than Django's.
    if os.getenv("DATABASE_POOLER", "").strip().lower() in ("1", "true",
                                                            "yes"):
        return _result(
            "connections", "Connection budget", True, IMPORTANT,
            f"a pooler is declared in front of {usable} usable slots; "
            f"CONN_MAX_AGE={conn_max_age}",
            "Transaction mode is the right one here: tenant binding is SET "
            "LOCAL inside ATOMIC_REQUESTS, so nothing depends on the session "
            "outliving the transaction.")

    wanted = processes * threads

    # WHY THIS IS A FAILURE EVEN WHEN THE ARITHMETIC FITS.
    #
    # The first version of this check compared `processes x threads` against
    # the usable slots and passed. The load test it was written for then
    # failed anyway: ONE Daphne process reached 95 IDLE connections and 14.5%
    # of requests answered HTTP 500, against an estimate of 18 for that
    # process.
    #
    # The estimate is not merely low, it is the wrong model. Django closes
    # connections on the `request_finished` signal, and that closes the
    # connection belonging to THE THREAD THE SIGNAL FIRES ON -- while under
    # ASGI the view ran on a pool thread. So a connection held by a pool
    # thread is reclaimed by nothing except CONN_MAX_AGE expiring, and the
    # pool is free to be larger than asgiref's documented default.
    #
    # Setting CONN_MAX_AGE=0 took the same load to 0 errors and a peak of 33
    # connections -- exactly the 32 concurrent requests plus one, which is
    # what ATOMIC_REQUESTS should cost. So the honest grade for "ASGI, held
    # connections, no pooler" is "not ready", whatever the multiplication
    # says.
    return _result(
        "connections", "Connection budget", False, IMPORTANT,
        f"CONN_MAX_AGE={conn_max_age} holds connections on ASGI worker "
        f"threads that nothing reclaims until it expires. Estimated "
        f"{wanted} ({processes}x{threads}) against {usable} usable of "
        f"{maximum} -- but Phase S10 measured ONE process reaching 95 idle "
        f"connections and 14.5% of requests failing with 'remaining "
        f"connection slots are reserved'",
        "Set CONN_MAX_AGE=0 (measured: 0 errors, peak 33 connections for 32 "
        "concurrent requests), or put pgbouncer in TRANSACTION mode in front "
        "and set DATABASE_POOLER=1 -- safe here because tenant binding is "
        "SET LOCAL inside a transaction, not a session SET.")


def _default_thread_pool():
    """What asgiref will use if nothing says otherwise."""
    configured = os.getenv("ASGI_THREADS")
    if configured:
        try:
            return int(configured)
        except ValueError:
            pass
    return min(32, (os.cpu_count() or 1) + 4)


def check_cache():
    """A cache round trip, and an opinion about WHICH cache.

    LocMemCache is per-process, and DRF's throttles live in the cache -- so
    with four gunicorn workers every rate limit is silently four times what
    it says, including the ones protecting registration and login. The
    project's own settings comment records this; it is graded `important`
    because the platform works, it just does not enforce what it claims.
    """
    from django.core.cache import cache

    backend = settings.CACHES.get("default", {}).get("BACKEND", "")
    try:
        cache.set("launch:probe", "1", 5)
        hit = cache.get("launch:probe") == "1"
    except Exception as exc:                       # noqa: BLE001
        return _result("cache", "Cache / Redis", False, CRITICAL,
                       f"unusable: {type(exc).__name__}",
                       "The tenant resolver and every throttle run on the "
                       "cache.")
    if not hit:
        return _result("cache", "Cache / Redis", False, CRITICAL,
                       "a value written to the cache could not be read back",
                       "Check the Redis connection.")
    if "locmem" in backend.lower():
        return _result(
            "cache", "Cache / Redis", False, IMPORTANT,
            "LocMemCache: per-process, so every rate limit is multiplied by "
            "the worker count, the tenant resolver cache is not shared, and "
            "a verified custom domain takes up to a minute to start working "
            "on each worker (a withdrawn one, up to a minute to stop)",
            "Point CACHES at Redis. See config/settings.py, which records "
            "this as a real pre-existing bug. Phase S9 added the third "
            "consequence: `tenancy.allowed_hosts` and `tenancy.resolver` "
            "evict on change, and a per-process cache cannot carry an "
            "eviction to the other workers -- only the TTL does.")
    return _result("cache", "Cache / Redis", True, CRITICAL,
                   backend.rsplit(".", 1)[-1])


def check_plans():
    """At least one plan a customer can actually be put on.

    THE CHECK THIS MODULE EXISTS FOR. `provision_organization` refuses
    without one, and in the self-service flow that refusal lands after the
    customer has registered, received the email and clicked the link.
    """
    try:
        from . import plans

        purchasable = list(plans.purchasable_plans()[:5])
    except Exception as exc:                       # noqa: BLE001
        return _result("plans", "Purchasable plans", False, CRITICAL,
                       f"could not be read: {type(exc).__name__}")
    if not purchasable:
        return _result(
            "plans", "Purchasable plans", False, CRITICAL,
            "none: every provisioning attempt will be refused",
            "Seed at least one active, public plan with a current PlanPrice. "
            "tenancy.plans.purchasable_plans() is what provisioning asks.")
    return _result("plans", "Purchasable plans", True, CRITICAL,
                   ", ".join(plan.code for plan in purchasable))


def check_trial_plan():
    """The plan a self-service registration would land on, with a trial on it.

    Separate from `check_plans` because "a plan exists" and "a new customer
    gets a trial" are different facts: a catalogue of one annual plan with
    `trial_days = 0` passes the first and gives every self-registered
    customer a workspace that expires immediately.
    """
    from . import registration

    plan = registration._trial_plan()
    if plan is None:
        return _result("trial_plan", "Default trial plan", False, CRITICAL,
                       "no purchasable plan to put a new customer on")

    configured = int(getattr(settings, "TENANCY_SELF_SERVICE_TRIAL_DAYS", 14))
    if configured <= 0:
        return _result(
            "trial_plan", "Default trial plan", False, CRITICAL,
            f"TENANCY_SELF_SERVICE_TRIAL_DAYS is {configured}, so a new "
            f"workspace would expire the moment it is created",
            "Set it to the trial length you intend to offer.")
    return _result("trial_plan", "Default trial plan", True, CRITICAL,
                   f"{plan.code}, {configured}-day trial")


def check_email():
    """Can this platform send mail, and does it know who it is sending as?

    Graded critical ONLY with public registration on, and that is a real
    distinction rather than hedging: verification mail is the single point of
    failure in the self-service flow -- no email, no workspace, ever -- while
    an operator provisioning by hand reads the temporary password off their
    own screen and never needs SMTP at all.
    """
    modes = saas_mode()
    severity = CRITICAL if modes["public_registration"] else IMPORTANT
    backend = getattr(settings, "EMAIL_BACKEND", "")
    sender = getattr(settings, "DEFAULT_FROM_EMAIL", "")

    if "smtp" not in backend.lower():
        consequence = (
            "every signup stops at 'check your email' and no tenant is ever "
            "created" if modes["public_registration"] else
            "nothing that depends on email works: leave decisions, memo "
            "approvals, subscription notices")
        return _result(
            "email", "Email provider", False, severity,
            f"{backend.rsplit('.', 1)[-1] or 'unset'} does not send mail",
            f"Configure SMTP. Without it, {consequence}.")
    if not getattr(settings, "EMAIL_HOST", ""):
        return _result("email", "Email provider", False, severity,
                       "SMTP backend with no EMAIL_HOST")
    if not sender or "example.com" in sender:
        return _result(
            "email", "Email provider", False, IMPORTANT,
            f"DEFAULT_FROM_EMAIL is {sender!r}",
            "Set it to an address on a domain you control, with SPF and "
            "DKIM, or verification mail lands in spam.")
    return _result("email", "Email provider", True, severity,
                   f"SMTP via {settings.EMAIL_HOST} as {sender}")


def check_storage():
    """Writes a file, reads it back, deletes it.

    Not "is a backend configured" -- that is a string in a settings file and
    says nothing about whether the bucket exists or the volume is mounted.
    Every tenant's documents, logos and export bundles live here, and the
    failure mode of an unmounted volume is uploads that appear to succeed and
    are gone after a redeploy.
    """
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage

    backend = settings.STORAGES.get("default", {}).get("BACKEND", "")
    probe = "launch-probe/.readiness"
    try:
        name = default_storage.save(probe, ContentFile(b"ok"))
        with default_storage.open(name, "rb") as handle:
            round_tripped = handle.read() == b"ok"
        default_storage.delete(name)
    except Exception as exc:                       # noqa: BLE001
        return _result("storage", "File storage", False, CRITICAL,
                       f"not writable: {type(exc).__name__}: {exc}"[:200],
                       "Mount the media volume, or fix the bucket "
                       "credentials. Tenant documents and export bundles go "
                       "here.")
    if not round_tripped:
        return _result("storage", "File storage", False, CRITICAL,
                       "a file written to storage read back differently")

    if ("FileSystemStorage" in backend
            and not getattr(settings, "DEBUG", False)):
        return _result(
            "storage", "File storage", True, ADVISORY,
            "local filesystem: works, but is per-instance",
            "With more than one application instance, uploads land on "
            "whichever one served the request. Set USE_S3 for shared "
            "storage.")
    return _result("storage", "File storage", True, CRITICAL,
                   backend.rsplit(".", 1)[-1])


def check_domains():
    """The hostnames a multi-tenant platform cannot work without."""
    modes = saas_mode()
    if not modes["tenancy"]:
        return _result("domains", "Platform domains", True, ADVISORY,
                       "single-tenant deployment; host resolution is not in "
                       "use")

    base = getattr(settings, "TENANCY_BASE_DOMAIN", "")
    hosts = [h.strip() for h in
             (getattr(settings, "TENANCY_PLATFORM_HOSTS", "") or "").split(",")
             if h.strip()]
    if not base:
        return _result("domains", "Platform domains", False, CRITICAL,
                       "TENANCY_BASE_DOMAIN is empty, so no host resolves to "
                       "a tenant",
                       "Set it, and point wildcard DNS and a wildcard "
                       "certificate at it.")
    if not hosts:
        severity = CRITICAL if modes["public_registration"] else IMPORTANT
        return _result(
            "domains", "Platform domains", False, severity,
            "TENANCY_PLATFORM_HOSTS is empty: the console -- and the signup "
            "pages -- share hostnames with customer workspaces",
            "Give the platform its own hostname (admin.platform.com).")

    allowed = list(getattr(settings, "ALLOWED_HOSTS", []) or [])

    # ASK DJANGO, DO NOT RE-IMPLEMENT IT. This used to test `host not in
    # allowed`, which is literal membership -- and `ALLOWED_HOSTS` is not a
    # list of literals. A leading dot is a wildcard: `.platform.com` matches
    # `admin.platform.com` and every other subdomain.
    #
    # So the check reported `admin.platform.com` as missing from an
    # ALLOWED_HOSTS that already covered it, as a CRITICAL launch blocker --
    # for exactly the wildcard configuration the next clause goes on to
    # DEMAND. A gate that blocks the setup it recommends is a gate people
    # learn to skip, which is worse than not having one. Found by running
    # the platform rather than by a test.
    from django.http.request import validate_host

    if "*" not in allowed:
        missing = [host for host in hosts
                   if not validate_host(host, allowed)]
        if missing:
            return _result(
                "domains", "Platform domains", False, CRITICAL,
                f"platform host(s) not in ALLOWED_HOSTS: {missing}",
                "Django will refuse those requests with a 400 before any "
                "view runs.")
        wildcard = f".{base}"
        if not any(entry in (wildcard, f"*.{base}") for entry in allowed):
            return _result(
                "domains", "Platform domains", False, CRITICAL,
                f"no wildcard for {wildcard} in ALLOWED_HOSTS, so a new "
                f"tenant's own hostname is refused the moment it is created",
                f"Add '{wildcard}' to ALLOWED_HOSTS.")
    return _result("domains", "Platform domains", True, CRITICAL,
                   f"{base}, console on {', '.join(hosts)}")


def check_rls():
    """Row-level security on, and the connection unable to bypass it.

    Asked of the DATABASE, not of the setting. `TENANCY_RLS_ENABLED = True`
    while connected as the table owner or a BYPASSRLS role is the most
    dangerous configuration available: every policy is installed, every test
    passes, and the database enforces none of it.
    """
    from django.db import connection

    modes = saas_mode()
    if not modes["tenancy"]:
        return _result("rls", "Row-level security", True, ADVISORY,
                       "single-tenant deployment")
    severity = CRITICAL
    if not getattr(settings, "TENANCY_RLS_ENABLED", False):
        return _result("rls", "Row-level security", False, severity,
                       "off: isolation rests on the application alone",
                       "Switch TENANCY_RLS_ENABLED on and connect as the "
                       "NOBYPASSRLS application role.")
    if connection.vendor != "postgresql":
        return _result("rls", "Row-level security", False, severity,
                       f"{connection.vendor} has no row-level security",
                       "RLS is a PostgreSQL feature. On any other backend "
                       "the flag is a no-op.")
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT rolbypassrls, rolsuper FROM pg_roles "
                "WHERE rolname = current_user")
            row = cursor.fetchone() or (None, None)
    except Exception as exc:                       # noqa: BLE001
        return _result("rls", "Row-level security", False, IMPORTANT,
                       f"could not be verified: {type(exc).__name__}")
    bypass, superuser = row
    if bypass or superuser:
        return _result(
            "rls", "Row-level security", False, severity,
            "the application connects as a role that BYPASSES every policy",
            "Connect as the nifn_app role (NOSUPERUSER, NOBYPASSRLS, and not "
            "the table owner). See tenancy/rls.py.")
    return _result("rls", "Row-level security", True, severity,
                   "enforced, and this connection cannot bypass it")


def check_provisioning():
    """Would a bootstrap produce a usable workspace?

    Checks the catalogues provisioning draws on, and -- if any tenant
    already exists -- that the most recently created one has no
    configuration gaps. That second half is the only evidence that
    provisioning actually worked rather than merely being importable.
    """
    from . import bootstrap

    catalogues = {
        "departments": bootstrap.DEPARTMENTS,
        "leave types": bootstrap.LEAVE_TYPES,
        "shifts": bootstrap.SHIFTS,
        "minute types": bootstrap.MINUTE_TYPES,
        "task templates": bootstrap.TASK_TEMPLATES,
        "competencies": bootstrap.COMPETENCIES,
        "inventory categories": bootstrap.INVENTORY_CATEGORIES,
    }
    empty = [name for name, rows in catalogues.items() if not rows]
    if empty:
        return _result("provisioning", "Tenant provisioning", False, CRITICAL,
                       f"empty bootstrap catalogue(s): {', '.join(empty)}",
                       "A tenant provisioned now would open with that module "
                       "unconfigured.")

    detail = (f"{sum(len(rows) for rows in catalogues.values())} catalogue "
              f"rows across {len(catalogues)} modules")
    try:
        from .models import Organization

        newest = (Organization.objects
                  .exclude(status=Organization.Status.PROVISIONING)
                  .order_by("-created_at").first())
        if newest is not None:
            gaps = bootstrap.verify_organization(newest)
            if gaps:
                return _result(
                    "provisioning", "Tenant provisioning", False, IMPORTANT,
                    f"the newest tenant ({newest.slug}) has configuration "
                    f"gaps: {sorted(gaps)}",
                    "Repair it from the console. A gap here means the last "
                    "provisioning run did not finish.")
            detail += f"; newest tenant ({newest.slug}) verifies clean"
    except Exception as exc:                       # noqa: BLE001
        logger.warning("provisioning readiness probe failed", exc_info=True)
        detail += f"; tenant probe skipped ({type(exc).__name__})"
    return _result("provisioning", "Tenant provisioning", True, CRITICAL,
                   detail)


def check_payment_methods():
    """Can a customer who wants to pay actually find out how?

    THE GAP THIS CLOSES was visible the moment the subscription portal was
    first exercised: it told a customer exactly what they owed, with a
    payment reference, and showed them an empty list of ways to pay. Every
    figure on the page was correct and the page was useless.

    Graded `important` rather than `critical`: the platform works, trials
    run, and nothing is broken -- but no money can arrive, which for a
    deployment selling subscriptions is a fault that only shows up as a
    customer emailing to ask where to send the bank transfer.
    """
    from .models import PaymentInstruction, Plan

    try:
        methods = list(PaymentInstruction.objects.filter(is_active=True)[:5])
        sells = Plan.objects.filter(is_active=True).exists()
    except Exception as exc:                       # noqa: BLE001
        return _result("payment_methods", "Payment instructions", False,
                       IMPORTANT,
                       f"could not be read: {type(exc).__name__}")
    if not sells:
        return _result("payment_methods", "Payment instructions", True,
                       ADVISORY, "no active plans, so nothing to pay for")
    if not methods:
        return _result(
            "payment_methods", "Payment instructions", False, IMPORTANT,
            "none configured: the subscription page tells customers what "
            "they owe and shows them no way to pay it",
            "Add a PaymentInstruction (bank transfer, eSewa) from the "
            "platform console. Part 3 of the S8 brief is explicit that these "
            "are console-managed and never hardcoded.")
    incomplete = [m.label for m in methods
                  if not (m.account_number or m.esewa_id
                          or m.instructions_html)]
    if incomplete:
        return _result(
            "payment_methods", "Payment instructions", False, IMPORTANT,
            f"configured but empty: {', '.join(incomplete)}",
            "A method with no account number, eSewa id or instructions is a "
            "heading with nothing under it.")
    return _result("payment_methods", "Payment instructions", True, IMPORTANT,
                   ", ".join(m.label for m in methods))


def check_operators():
    """Somebody has to be able to answer a support request."""
    try:
        from django.contrib.auth import get_user_model

        from .context import no_tenant

        User = get_user_model()
        with no_tenant():
            count = User.all_tenants.filter(is_platform_staff=True,
                                            is_active=True).count()
    except Exception as exc:                       # noqa: BLE001
        return _result("operators", "Platform operators", False, IMPORTANT,
                       f"could not be counted: {type(exc).__name__}")
    if not count:
        return _result(
            "operators", "Platform operators", False, IMPORTANT,
            "none: nobody can reach the console to help a customer",
            "manage.py create_platform_admin --email ...")
    return _result("operators", "Platform operators", True, IMPORTANT,
                   f"{count} active")


def check_error_tracking():
    """Is there anywhere for an unhandled exception to GO?

    PHASE S10 PART 6. Sentry support has existed since Phase 11 and is
    optional -- with `SENTRY_DSN` unset the block in `config.settings` does
    nothing. For a single-tenant deployment that is a reasonable default:
    one customer, one operator, and the operator reads the container log.

    For a platform it is not. A 500 in one tenant's workspace is invisible
    to everybody who could fix it: the customer sees a generic failure (by
    design -- Phase S6.75 Part 2), the log line is one of thousands, and
    nobody is paged. Phase S10's load test produced 339 identical 500s and
    the only reason they were noticed is that something was watching the
    status codes on purpose.

    Graded `important` rather than `critical`: the platform runs without it,
    and a deployment may aggregate stdout somewhere else entirely. What must
    not happen is launching while believing errors are being tracked when
    nothing is.
    """
    dsn = (getattr(settings, "SENTRY_DSN", "") or "").strip()
    if dsn:
        rate = getattr(settings, "SENTRY_TRACES_SAMPLE_RATE", 0.0)
        return _result("error_tracking", "Error tracking", True, IMPORTANT,
                       f"Sentry configured ({settings.SENTRY_ENVIRONMENT}, "
                       f"traces {rate})")
    return _result(
        "error_tracking", "Error tracking", False, IMPORTANT,
        "no SENTRY_DSN: an unhandled exception in a tenant's workspace is "
        "written to stdout and reported to nobody",
        "Set SENTRY_DSN, or point the deployment's log aggregator at the "
        "JSON stream and alert on `levelname=ERROR`. Customers do not see "
        "internal errors by design, so nothing else will tell you.")


def check_logging():
    """Are the application's own loggers actually configured?

    PHASE S10 PART 6, and it found a live gap: `tenancy` logs from thirty
    files and was not in `LOGGING['loggers']` at all, so everything it
    recorded below WARNING fell through to the root logger and was
    discarded.

    This checks the shape rather than a fixed list, so it keeps working as
    packages are added -- which is the failure mode it exists for. The same
    omission happened in Phase 11 (audit finding M9) and happened again for
    every package added afterwards.
    """
    import pathlib

    from django.apps import apps

    configured = set(settings.LOGGING.get("loggers", {}))
    base = pathlib.Path(settings.BASE_DIR).resolve()
    missing = []

    # VIA THE APP REGISTRY, not INSTALLED_APPS. The first version of this
    # read INSTALLED_APPS and skipped every entry containing a dot -- and
    # this project lists its own apps as dotted config paths
    # (`tenancy.apps.TenancyConfig`), so the check examined only the
    # third-party single-word entries and reported "every app that logs has
    # a logger" while `tenancy` had none. It passed for the wrong reason,
    # which is the one outcome a launch gate must not produce. Its own test
    # caught it.
    for config in apps.get_app_configs():
        package = pathlib.Path(config.path).resolve()
        if base not in package.parents and package != base:
            continue                    # third-party, not ours to configure
        if config.label in configured:
            continue
        if any("getLogger" in path.read_text(errors="ignore")
               for path in package.rglob("*.py")):
            missing.append(config.label)

    if not missing:
        return _result("logging", "Application logging", True, IMPORTANT,
                       f"{len(configured)} loggers configured; every app "
                       f"that logs has one")
    return _result(
        "logging", "Application logging", False, IMPORTANT,
        f"these apps call getLogger but have no logger configured, so "
        f"everything they record below WARNING is discarded: "
        f"{', '.join(sorted(missing))}",
        "Add them to LOGGING['loggers'] in config/settings.py. The root "
        "logger is at WARNING, so INFO lines are not merely unformatted -- "
        "they do not exist.")


def check_alert_delivery():
    """When an alert fires, does it reach a human?

    PHASE S11 PART 3. Phase S11 added nine SaaS alert rules and closed R7's
    detection half. This is the delivery half, and it fails independently:
    `monitoring.alerts._notify` sends to `ALERT_EMAILS` and/or
    `ALERT_WEBHOOK_URL`, and with neither set it logs

        "Alert X fired but no ALERT_EMAILS or ALERT_WEBHOOK_URL is
         configured -- nobody was told."

    ...which is itself a log line nobody is reading, because nothing is
    configured to read logs either. Alerting that fires into the void is
    worse than none: it is believed.

    Also checks that the EVALUATOR runs. The rules are evaluated by the
    `check_alerts` management command on a schedule; without that schedule
    the rules are correct and never consulted.
    """
    emails = [a.strip() for a in
              (getattr(settings, "ALERT_EMAILS", "") or "").split(",")
              if a.strip()]
    webhook = (getattr(settings, "ALERT_WEBHOOK_URL", "") or "").strip()

    if not emails and not webhook:
        return _result(
            "alert_delivery", "Alert delivery", False, IMPORTANT,
            "neither ALERT_EMAILS nor ALERT_WEBHOOK_URL is set, so every "
            "alert fires into a log line nobody is reading",
            "Set ALERT_EMAILS to the platform team, or ALERT_WEBHOOK_URL to "
            "a chat channel. Then schedule `manage.py check_alerts` every "
            "five minutes -- the rules are only consulted when it runs.")

    where = []
    if emails:
        where.append(f"{len(emails)} recipient(s)")
    if webhook:
        where.append("a webhook")
    return _result("alert_delivery", "Alert delivery", True, IMPORTANT,
                   " and ".join(where) + "; schedule `check_alerts` every "
                   "5 minutes")


def check_debug():
    if getattr(settings, "DEBUG", False):
        return _result(
            "debug", "Debug mode", False, CRITICAL,
            "DEBUG is on: tracebacks, settings and SQL are served to "
            "whoever triggers an error",
            "Set DJANGO_DEBUG=0.")
    return _result("debug", "Debug mode", True, CRITICAL, "off")


def check_export_retention():
    days = int(getattr(settings, "TENANCY_EXPORT_RETENTION_DAYS", 30))
    if days > 90:
        return _result(
            "export_retention", "Export retention", False, ADVISORY,
            f"{days} days: each bundle is a complete copy of one customer's "
            f"workspace",
            "Shorten it, and schedule tenancy_expire_exports.")
    return _result("export_retention", "Export retention", True, ADVISORY,
                   f"{days} days")


CHECKS = (
    check_database,
    check_connection_budget,
    check_cache,
    check_storage,
    check_domains,
    check_rls,
    check_plans,
    check_trial_plan,
    check_email,
    check_payment_methods,
    check_provisioning,
    check_operators,
    check_error_tracking,
    check_logging,
    check_alert_delivery,
    check_debug,
    check_export_retention,
)


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------
def audit():
    """Run every check. Returns the launch readiness report.

    NO CHECK IS ALLOWED TO BREAK THE REPORT. Each runs in its own try block,
    because the one thing worse than a platform with a broken dependency is a
    readiness page that 500s instead of naming it.
    """
    results = []
    for check in CHECKS:
        try:
            results.append(check())
        except Exception as exc:                   # noqa: BLE001
            logger.exception("launch check %s failed", check.__name__)
            results.append(_result(
                check.__name__.replace("check_", ""),
                check.__name__.replace("check_", "").replace("_", " ").title(),
                False, IMPORTANT,
                f"the check itself failed: {type(exc).__name__}: {exc}"[:200]))

    blocking = [r for r in results if not r["ready"]
                and r["severity"] == CRITICAL]
    earned = sum(_WEIGHT[r["severity"]] for r in results if r["ready"])
    total = sum(_WEIGHT[r["severity"]] for r in results) or 1

    return {
        "ready": not blocking,
        # A SCORE AND A VERDICT, because they answer different questions.
        # "87%" tells an operator how much work is left; "not ready" tells
        # them whether to open the doors. A platform missing one critical
        # dependency scores well and must not launch.
        "score": round(100 * earned / total),
        "verdict": "Launch Ready" if not blocking else "Not Launch Ready",
        "blocking": [r["key"] for r in blocking],
        "counts": {
            "total": len(results),
            "ready": sum(1 for r in results if r["ready"]),
            "critical_failing": len(blocking),
            "important_failing": sum(
                1 for r in results
                if not r["ready"] and r["severity"] == IMPORTANT),
            "advisory_failing": sum(
                1 for r in results
                if not r["ready"] and r["severity"] == ADVISORY),
        },
        "mode": saas_mode(),
        "checks": results,
    }


def blocking_failures():
    """The critical failures only, for ``tenancy.checks`` to raise as Errors."""
    return [r for r in audit()["checks"]
            if not r["ready"] and r["severity"] == CRITICAL]
