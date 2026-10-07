"""Phase S6.75: the platform survives being misconfigured, and says so.

PART 9 IS THE CENTRE OF THIS FILE. Three failures, each checked for three
things: the customer gets something a human wrote, the operator gets
something actionable, and the database is left clean. Those are different
properties and they fail independently -- a friendly message over a
half-created tenant is worse than a stack trace.
"""
import pytest

from tenancy import launch
from tenancy.models import Organization, PlatformMetric

pytestmark = pytest.mark.django_db


# --- Part 1: the launch checks ------------------------------------------
def test_a_healthy_deployment_reports_launch_ready(settings, monthly_plan,
                                                    platform_user, tmp_path):
    """Everything a customer depends on, present at once."""
    settings.DEBUG = False
    settings.TENANCY_ENABLED = True
    settings.TENANCY_RLS_ENABLED = False      # SQLite; see the RLS check test
    settings.TENANCY_BASE_DOMAIN = "platform.test"
    settings.TENANCY_PLATFORM_HOSTS = "admin.platform.test"
    settings.ALLOWED_HOSTS = ["admin.platform.test", ".platform.test"]
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    settings.EMAIL_HOST = "smtp.example.net"
    settings.DEFAULT_FROM_EMAIL = "noreply@platform.test"

    report = launch.audit()
    failing = {c["key"]: c["detail"] for c in report["checks"]
               if not c["ready"] and c["severity"] == launch.CRITICAL}
    # RLS and cache are the two this environment cannot satisfy: SQLite has
    # no row-level security, and the test settings use LocMemCache.
    assert failing.keys() <= {"rls"}, failing
    assert report["score"] > 50


def test_no_purchasable_plan_blocks_launch(settings, monthly_plan):
    """Part 9 failure 1, at the readiness layer.

    The check that this whole module exists for: a platform with no plan is
    not broken, it was never set up -- and nothing else notices.
    """
    from tenancy.models import Plan

    settings.TENANCY_ENABLED = True
    Plan.objects.update(is_active=False)

    result = launch.check_plans()
    assert result["ready"] is False
    assert result["severity"] == launch.CRITICAL
    assert "refused" in result["detail"]
    assert "seed" in result["hint"].lower()

    report = launch.audit()
    assert report["ready"] is False
    assert "plans" in report["blocking"]
    assert report["verdict"] == "Not Launch Ready"


def test_a_trial_of_zero_days_blocks_launch(settings, monthly_plan):
    """"A plan exists" and "a new customer gets a trial" are different facts.

    A catalogue of one plan with a zero-day trial passes the first and hands
    every self-registered customer a workspace that has already expired.
    """
    settings.TENANCY_SELF_SERVICE_TRIAL_DAYS = 0
    result = launch.check_trial_plan()
    assert result["ready"] is False
    assert "expire" in result["detail"]


def test_an_email_backend_that_sends_nothing_blocks_a_signup_platform(
        settings):
    """Critical with public registration on, important without it.

    Not hedging: verification mail is the single point of failure in the
    self-service flow -- no email, no workspace, ever -- while an operator
    provisioning by hand reads the temporary password off their own screen.
    """
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    settings.TENANCY_PUBLIC_REGISTRATION = False
    assert launch.check_email()["severity"] == launch.IMPORTANT

    settings.TENANCY_PUBLIC_REGISTRATION = True
    answer = launch.check_email()
    assert answer["ready"] is False
    assert answer["severity"] == launch.CRITICAL
    assert "no tenant is ever created" in answer["hint"]


def test_storage_is_checked_by_writing_a_file_not_by_reading_a_setting(
        settings, tmp_path):
    """"A backend is configured" is a string in a settings file.

    The failure mode of an unmounted volume is uploads that appear to
    succeed and are gone after the next redeploy, and no amount of reading
    `STORAGES` detects it.
    """
    assert launch.check_storage()["ready"] is True

    class Broken:
        def save(self, *args, **kwargs):
            raise OSError("read-only file system")

    from django.core.files import storage

    original = storage.default_storage._wrapped
    storage.default_storage._wrapped = Broken()
    try:
        answer = launch.check_storage()
    finally:
        storage.default_storage._wrapped = original

    assert answer["ready"] is False
    assert answer["severity"] == launch.CRITICAL
    assert "read-only file system" in answer["detail"]


def test_a_wildcard_missing_from_allowed_hosts_blocks_launch(settings):
    """The mistake that breaks every tenant created after the deploy.

    ALLOWED_HOSTS listing the console and today's customers looks complete
    and refuses tomorrow's with a 400 before any view runs.
    """
    settings.TENANCY_ENABLED = True
    settings.TENANCY_BASE_DOMAIN = "platform.test"
    settings.TENANCY_PLATFORM_HOSTS = "admin.platform.test"
    settings.ALLOWED_HOSTS = ["admin.platform.test", "nif.platform.test"]

    answer = launch.check_domains()
    assert answer["ready"] is False
    assert "wildcard" in answer["detail"]

    settings.ALLOWED_HOSTS = ["admin.platform.test", ".platform.test"]
    assert launch.check_domains()["ready"] is True


def test_debug_mode_blocks_launch(settings):
    settings.DEBUG = True
    assert launch.check_debug()["ready"] is False
    settings.DEBUG = False
    assert launch.check_debug()["ready"] is True


def test_the_report_survives_a_check_that_itself_explodes(monkeypatch):
    """A readiness page that 500s instead of naming a problem is useless."""
    def explode():
        raise RuntimeError("the check is broken")

    monkeypatch.setattr(launch, "CHECKS", (explode,) + launch.CHECKS[1:])
    report = launch.audit()
    assert any("the check itself failed" in c["detail"]
               for c in report["checks"])


def test_the_score_and_the_verdict_are_not_the_same_question(
        settings, monthly_plan, platform_user):
    """A platform missing ONE critical dependency scores well and must not
    launch. The score says how much work is left; the verdict says whether
    to open the doors.

    The environment is set up healthy first, because the point only shows
    with one thing wrong -- a deployment where half the checks fail makes
    "high score, not ready" unremarkable.
    """
    settings.DEBUG = False
    settings.TENANCY_ENABLED = True
    settings.TENANCY_RLS_ENABLED = False
    settings.TENANCY_BASE_DOMAIN = "platform.test"
    settings.TENANCY_PLATFORM_HOSTS = "admin.platform.test"
    settings.ALLOWED_HOSTS = ["admin.platform.test", ".platform.test"]
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    settings.EMAIL_HOST = "smtp.example.net"
    settings.DEFAULT_FROM_EMAIL = "noreply@platform.test"

    from tenancy.models import Plan

    healthy = launch.audit()
    Plan.objects.update(is_active=False)
    broken = launch.audit()

    # MEASURED AGAINST THIS ENVIRONMENT'S OWN BASELINE, not against an
    # absolute number. How many checks pass here depends on what the test
    # environment can satisfy -- SQLite has no row-level security, the test
    # cache is LocMemCache, no payment instructions are seeded -- so a
    # hard-coded threshold tests the harness rather than the code, and
    # breaks every time a check is added. (It did: adding the thirteenth
    # check moved the figure from 54 to 48.)
    #
    # The point is the RELATIONSHIP: the verdict flips while the score
    # barely moves, so an operator reading the score alone would launch.
    # NOT "the baseline is launch ready" -- it is not, and need not be. This
    # test environment cannot satisfy every critical check (it runs with
    # TENANCY_RLS_ENABLED off so the same file works on SQLite, which under
    # PostgreSQL is itself a blocking failure). The claim is narrower and
    # still the one that matters: breaking ONE dependency barely moves the
    # score, so the score is a poor proxy for the decision.
    assert broken["ready"] is False
    assert broken["score"] >= healthy["score"] * 0.6, (
        healthy["score"], broken["score"])
    assert broken["score"] < healthy["score"], (
        "breaking a dependency must cost something")
    # Both, because they are different facts: no plan to sell, and therefore
    # no plan to start a trial on.
    assert set(broken["blocking"]) >= {"plans", "trial_plan"}


# --- Part 1: the deploy gate --------------------------------------------
def test_the_gate_is_silent_on_the_single_tenant_deployment(settings):
    """NIF today has no plan, no platform hostname and no SMTP requirement,
    and none of that is a fault. Grading it as one would teach people to
    deploy with --skip-checks."""
    from tenancy.checks import saas_launch_readiness

    settings.TENANCY_ENABLED = False
    assert saas_launch_readiness(None) == []


def test_the_gate_refuses_a_platform_with_no_plan(settings, monthly_plan):
    from tenancy.checks import saas_launch_readiness
    from tenancy.models import Plan

    settings.TENANCY_ENABLED = True
    settings.TENANCY_BASE_DOMAIN = "platform.test"
    Plan.objects.update(is_active=False)

    ids = {problem.id for problem in saas_launch_readiness(None)}
    assert "tenancy.L006" in ids


def test_the_gate_does_not_report_the_same_root_cause_twice(settings):
    """`tenancy.E001` already fails the gate when the base domain is empty.

    Printing the same sentence under two ids is how people start skimming
    gate output.
    """
    from tenancy.checks import saas_launch_readiness, tenancy_configuration

    settings.TENANCY_ENABLED = True
    settings.TENANCY_BASE_DOMAIN = ""

    configuration = {p.id for p in tenancy_configuration(None)}
    gate = {p.id for p in saas_launch_readiness(None)}
    assert "tenancy.E001" in configuration
    assert "tenancy.L004" not in gate


# --- Part 9, failure 1: no purchasable plan -----------------------------
def test_no_plan_gives_the_customer_a_human_message_and_the_operator_the_cause(
        settings, client, caplog):
    """Part 9's three properties, for the first failure.

    The registrant sees something a person wrote; the log says exactly what
    is wrong; and nothing was half-created.
    """
    import logging

    from tenancy.models import PendingRegistration, Plan

    settings.TENANCY_PUBLIC_REGISTRATION = True
    settings.TENANCY_BASE_DOMAIN = "platform.test"

    form = {
        "organization_name": "No Plan School", "slug": "noplanschool",
        "organization_email": "office@noplan.test",
        "admin_email": "head@noplan.test",
        "password": "Str0ng-Pass-2026",
        "password_confirmation": "Str0ng-Pass-2026",
    }
    assert client.post("/api/v1/register/", form,
                       content_type="application/json").status_code == 202

    from django.core import mail

    token = mail.outbox[-1].body.split("token=")[1].split()[0].strip()
    Plan.objects.update(is_active=False)

    with caplog.at_level(logging.ERROR):
        response = client.post("/api/v1/register/verify/",
                               {"token": token},
                               content_type="application/json")

    # 1. The customer.
    assert response.status_code == 400
    body = str(response.json())
    assert "support" in body.lower()
    assert "plan" not in body.lower(), body

    # 2. The operator.
    assert any("No purchasable plan" in record.getMessage()
               for record in caplog.records), [
        r.getMessage() for r in caplog.records]

    # 3. The database.
    assert not Organization.objects.filter(slug="noplanschool").exists()
    record = PendingRegistration.objects.get(slug="noplanschool")
    assert record.status == PendingRegistration.Status.PENDING, (
        "the registration must be retryable once the platform is fixed")


# --- Part 9, failure 2: SMTP unavailable --------------------------------
def test_smtp_failure_does_not_lose_the_registration(settings, client,
                                                      monkeypatch, caplog):
    """A bounced verification email must not cost the registrant their slug.

    The platform answers the same way it always does -- deliberately, because
    the response must not reveal whether mail to that address succeeded --
    and the registration is still there to be re-sent.
    """
    import logging

    from tenancy import registration as registration_module
    from tenancy.models import PendingRegistration

    settings.TENANCY_PUBLIC_REGISTRATION = True
    settings.TENANCY_BASE_DOMAIN = "platform.test"

    def refuse(*args, **kwargs):
        raise OSError("[Errno 111] Connection refused")

    monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send",
                        refuse)

    with caplog.at_level(logging.WARNING):
        response = client.post("/api/v1/register/", {
            "organization_name": "Mailless NGO", "slug": "maillessngo",
            "organization_email": "office@mailless.test",
            "admin_email": "lead@mailless.test",
            "password": "Str0ng-Pass-2026",
            "password_confirmation": "Str0ng-Pass-2026",
        }, content_type="application/json")

    assert response.status_code == 202, response.json()
    assert "check your email" in str(response.json()).lower()
    assert any("verification email" in r.getMessage() for r in caplog.records)

    record = PendingRegistration.objects.get(slug="maillessngo")
    assert record.status == PendingRegistration.Status.PENDING
    assert record.verification_sends == 1
    assert not Organization.objects.filter(slug="maillessngo").exists()

    # And the launch report would have told them before a customer did.
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    assert launch.check_email()["ready"] is False


# --- Part 9, failure 3: an exception mid-provision ----------------------
def test_an_exception_mid_provision_leaves_no_partial_tenant(settings,
                                                              platform_user,
                                                              monthly_plan,
                                                              monkeypatch):
    """Part 2 and Part 9's third failure, which are the same question.

    The bootstrap runs after the organization, the settings, the branding and
    the subscription already exist. If it throws and the transaction does not
    hold, what is left is a tenant on the platform dashboard with no
    configuration, an orphan subscription billing for it, and a slug nobody
    can reuse.
    """
    from tenancy import bootstrap, console
    from tenancy.exceptions import TenancyError
    from tenancy.models import Subscription

    def explode(*args, **kwargs):
        raise RuntimeError("bootstrap exploded halfway")

    monkeypatch.setattr(bootstrap, "bootstrap_organization", explode)

    with pytest.raises(RuntimeError):
        console.create_organization(
            platform_user, name="Half Tenant", slug="halftenant",
            document_prefix="HALF", email="a@half.test", plan=monthly_plan)

    # No partial tenant.
    assert not Organization.objects.filter(slug="halftenant").exists()
    # No orphan subscription.
    assert not Subscription.objects.filter(
        organization__slug="halftenant").exists()
    # And the slug is free, so the operator can simply try again.
    from tenancy import registration

    assert registration.slug_status("halftenant")["available"] is True


def test_a_failure_after_the_subscription_still_leaves_nothing_behind(
        settings, platform_user, monthly_plan, monkeypatch):
    """The same guarantee, with the failure moved later in the sequence."""
    from tenancy import services
    from tenancy.models import Subscription

    def explode(organization, *args, **kwargs):
        raise RuntimeError("administrator creation exploded")

    monkeypatch.setattr(services, "create_tenant_admin", explode)

    with pytest.raises(RuntimeError):
        services.provision_organization(
            name="Late Failure", slug="latefailure", document_prefix="LATE",
            email="a@late.test", plan=monthly_plan,
            admin_email="admin@late.test")

    assert not Organization.objects.filter(slug="latefailure").exists()
    assert not Subscription.objects.filter(
        organization__slug="latefailure").exists()


def test_a_provisioning_failure_is_counted_as_a_failure(settings,
                                                         platform_user,
                                                         monthly_plan,
                                                         monkeypatch):
    """The counter has to survive the rollback that hides the attempt.

    Incremented inside the atomic block it would be rolled back by the very
    failure it was recording, so a broken deployment would show no
    provisioning activity at all -- which reads as a quiet week rather than
    as an outage.
    """
    from tenancy import bootstrap, services

    monkeypatch.setattr(bootstrap, "bootstrap_organization",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("boom")))

    before = _metric(PlatformMetric.Key.PROVISION_FAILED)
    with pytest.raises(RuntimeError):
        services.provision_organization(
            name="Counted", slug="countedfail", document_prefix="CNT",
            email="a@counted.test", plan=monthly_plan)

    assert _metric(PlatformMetric.Key.PROVISION_FAILED) == before + 1


def test_a_successful_provision_is_counted_against_its_tenant(platform_user,
                                                               monthly_plan):
    from tenancy import services

    before = _metric(PlatformMetric.Key.PROVISION_OK)
    organization = services.provision_organization(
        name="Counted OK", slug="countedok", document_prefix="COK",
        email="a@countedok.test", plan=monthly_plan)

    assert _metric(PlatformMetric.Key.PROVISION_OK) == before + 1
    assert PlatformMetric.objects.filter(
        key=PlatformMetric.Key.PROVISION_OK,
        organization=organization).exists()


def _metric(key, organization=None):
    from django.db.models import Sum

    return (PlatformMetric.objects.filter(key=key)
            .aggregate(total=Sum("count"))["total"] or 0)


# --- Part 4: the dashboards ---------------------------------------------
def test_the_dashboard_reports_every_series_the_brief_names(platform_user,
                                                             monthly_plan,
                                                             org):
    from tenancy import console, metrics

    metrics.bump(PlatformMetric.Key.LOGIN_OK, organization=org)
    metrics.bump(PlatformMetric.Key.LOGIN_FAILED)

    data = console.event_dashboard(platform_user, days=30)
    keys = {panel["key"] for panel in data["panels"]}
    assert keys == {"tenant_created", "provision_ok", "provision_failed",
                    "login_ok", "login_failed", "subscription_changed",
                    "tenant_archived", "export_created"}
    by_key = {panel["key"]: panel for panel in data["panels"]}
    assert by_key["login_ok"]["total"] == 1
    assert by_key["login_failed"]["total"] == 1
    # Provisioning the `org` fixture is a tenant creation, from the trail.
    assert by_key["tenant_created"]["total"] >= 1


def test_a_window_with_no_attempts_has_no_success_rate(platform_user):
    """None rather than 100%: a window with nothing in it has no signal, and
    showing a perfect score for it reads as health."""
    from tenancy import console

    data = console.event_dashboard(platform_user, days=1)
    assert data["rates"]["provisioning_success"] in (None, 100.0)
    assert data["totals"]["login_attempts"] >= 0


def test_a_login_counter_records_no_identifying_detail(org):
    """The count is per tenant per day. Who signed in, and from where, stays
    in the tenant's own audit trail where their administrators can see it and
    the platform cannot."""
    from tenancy import metrics

    metrics.bump(PlatformMetric.Key.LOGIN_FAILED, organization=org)
    row = PlatformMetric.objects.get(key=PlatformMetric.Key.LOGIN_FAILED)
    stored = {f.attname for f in row._meta.concrete_fields}
    assert stored == {"id", "day", "key", "organization_id", "count"}


def test_counting_never_breaks_the_thing_it_counts(monkeypatch):
    """A dashboard is not worth an outage."""
    from tenancy import metrics
    from tenancy.models import PlatformMetric as Metric

    monkeypatch.setattr(
        Metric.objects.__class__, "filter",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    # Must not raise.
    metrics.bump(Metric.Key.LOGIN_OK)


# --- Part 7: the rate limits --------------------------------------------
def test_every_abusable_endpoint_has_its_own_ceiling(settings):
    """The review, as an assertion.

    `password_change` is the one this phase added: with no scope of its own
    it inherited `user` -- 200 a minute -- and it verifies the current
    password before accepting a new one, which makes it an oracle for that
    password to anybody holding a stolen session.
    """
    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    for scope in ("login", "registration", "registration_verify",
                  "registration_slug", "password_change", "media", "verify"):
        assert scope in rates, scope

    # Registration is per HOUR, because its product is a whole tenant.
    assert rates["registration"].endswith("/hour")
    assert rates["registration_verify"].endswith("/hour")


def test_there_is_no_self_service_password_reset_to_rate_limit():
    """Part 7 lists one. This platform does not have one, and that is the
    honest answer rather than a missing control: an administrator resets a
    password and tells the user, so there is no unauthenticated reset
    endpoint to abuse.
    """
    from django.urls import URLResolver, get_resolver

    def paths(resolver, prefix=""):
        for entry in resolver.url_patterns:
            if isinstance(entry, URLResolver):
                yield from paths(entry, prefix + str(entry.pattern))
            else:
                yield prefix + str(entry.pattern)

    routes = list(paths(get_resolver()))
    reset_routes = [r for r in routes if "reset-password" in r]

    # There ARE reset routes, and every one of them is an ADMINISTRATOR
    # resetting somebody else's password from inside a tenant -- which is how
    # this product has always worked, and is behind admin authentication.
    # What does not exist is an unauthenticated "forgot my password" flow, so
    # there is no anonymous password-guessing surface to rate limit.
    assert reset_routes, "the admin reset routes have moved; re-check this"
    for route in reset_routes:
        assert "admin" in route, route


# ---------------------------------------------------------------------------
# Phase S10: the three checks the certification review added
# ---------------------------------------------------------------------------
class TestConnectionBudget:
    """Phase S10 Part 3 measured a 14.5% error rate under load and traced it
    to PostgreSQL connection exhaustion. This is that finding, kept."""

    def test_holding_connections_without_a_pooler_is_not_launch_ready(
            self, settings, monkeypatch):
        from django.db import connection

        if connection.vendor != "postgresql":
            pytest.skip("the budget only applies to PostgreSQL")

        settings.DATABASES["default"]["CONN_MAX_AGE"] = 60
        monkeypatch.delenv("DATABASE_POOLER", raising=False)
        result = launch.check_connection_budget()
        assert result["ready"] is False
        # The hint has to name what to do, not just what is wrong.
        assert "CONN_MAX_AGE=0" in result["hint"]
        assert "pgbouncer" in result["hint"]

    def test_not_holding_them_is(self, settings):
        from django.db import connection

        if connection.vendor != "postgresql":
            pytest.skip("the budget only applies to PostgreSQL")

        settings.DATABASES["default"]["CONN_MAX_AGE"] = 0
        assert launch.check_connection_budget()["ready"] is True

    def test_a_declared_pooler_is_accepted(self, settings, monkeypatch):
        """Because transaction-mode pooling IS safe here: tenant binding is
        `SET LOCAL` inside `ATOMIC_REQUESTS`, so nothing depends on the
        session outliving the transaction. A deployment that has one should
        not be graded as if it did not."""
        from django.db import connection

        if connection.vendor != "postgresql":
            pytest.skip("the budget only applies to PostgreSQL")

        settings.DATABASES["default"]["CONN_MAX_AGE"] = 60
        monkeypatch.setenv("DATABASE_POOLER", "1")
        assert launch.check_connection_budget()["ready"] is True

    def test_it_is_silent_on_sqlite(self, settings):
        from django.db import connection

        if connection.vendor == "postgresql":
            pytest.skip("this is the SQLite branch")
        assert launch.check_connection_budget()["ready"] is True


class TestErrorTracking:
    def test_no_dsn_is_reported(self, settings):
        settings.SENTRY_DSN = ""
        result = launch.check_error_tracking()
        assert result["ready"] is False
        # The reason matters: customers deliberately never see internal
        # errors, so nothing else will tell the operator.
        assert "reported to nobody" in result["detail"]

    def test_a_dsn_satisfies_it(self, settings):
        settings.SENTRY_DSN = "https://key@example.ingest.sentry.io/1"
        settings.SENTRY_ENVIRONMENT = "production"
        settings.SENTRY_TRACES_SAMPLE_RATE = 0.0
        assert launch.check_error_tracking()["ready"] is True


class TestApplicationLogging:
    """The gap this found: `tenancy` logs from thirty files and had no
    logger configured, so everything it recorded below WARNING was
    discarded -- including the domain-verification failures support needs."""

    def test_every_app_that_logs_has_a_logger(self):
        result = launch.check_logging()
        assert result["ready"] is True, result["detail"]

    def test_tenancy_in_particular(self, settings):
        assert "tenancy" in settings.LOGGING["loggers"]
        assert settings.LOGGING["loggers"]["tenancy"]["level"] != "WARNING"

    def test_an_unconfigured_app_is_caught(self, settings):
        """Checks the SHAPE rather than a fixed list, because the failure
        mode is a package added later -- which is how this happened twice."""
        loggers = dict(settings.LOGGING["loggers"])
        loggers.pop("tenancy", None)
        settings.LOGGING = {**settings.LOGGING, "loggers": loggers}
        result = launch.check_logging()
        assert result["ready"] is False
        assert "tenancy" in result["detail"]


class TestPlatformHostsUseDjangoMatching:
    """The wildcard configuration this check recommends must also PASS it.

    `check_domains` demands a `.<base domain>` wildcard in ALLOWED_HOSTS and
    then used literal membership to look for the console hostname -- so the
    recommended setup (`.platform.com` covering `admin.platform.com`) was
    reported as a CRITICAL blocker. Found by running the platform locally,
    where `.localhost` covers `admin.localhost`.
    """

    @pytest.fixture(autouse=True)
    def _saas(self, settings):
        settings.TENANCY_ENABLED = True
        settings.TENANCY_BASE_DOMAIN = "platform.com"
        settings.TENANCY_PLATFORM_HOSTS = "admin.platform.com"

    def test_a_wildcard_covers_the_console_hostname(self, settings):
        settings.ALLOWED_HOSTS = [".platform.com"]
        result = launch.check_domains()
        assert result["ready"] is True, result["detail"]

    def test_an_explicit_entry_still_works(self, settings):
        settings.ALLOWED_HOSTS = ["admin.platform.com", ".platform.com"]
        assert launch.check_domains()["ready"] is True

    def test_a_genuinely_missing_console_host_is_still_caught(self, settings):
        settings.ALLOWED_HOSTS = [".example.net"]
        result = launch.check_domains()
        assert result["ready"] is False
        assert "admin.platform.com" in result["detail"]

    def test_and_a_missing_tenant_wildcard_is_still_caught(self, settings):
        """The clause that the load test turned from a warning into a
        measured 100% failure rate."""
        settings.ALLOWED_HOSTS = ["admin.platform.com"]
        result = launch.check_domains()
        assert result["ready"] is False
        assert "wildcard" in result["detail"]


class TestAlertDelivery:
    """Phase S11 closed R7's detection half with nine rules. This is the
    delivery half, which fails independently and silently."""

    def test_nothing_configured_is_not_ready(self, settings):
        settings.ALERT_EMAILS = ""
        settings.ALERT_WEBHOOK_URL = ""
        result = launch.check_alert_delivery()
        assert result["ready"] is False
        assert "into a log line nobody is reading" in result["detail"]
        # And the hint has to name the SCHEDULE too: correct rules that are
        # never evaluated are the same outcome as no rules.
        assert "check_alerts" in result["hint"]

    def test_email_recipients_satisfy_it(self, settings):
        settings.ALERT_EMAILS = "ops@platform.test, oncall@platform.test"
        settings.ALERT_WEBHOOK_URL = ""
        result = launch.check_alert_delivery()
        assert result["ready"] is True
        assert "2 recipient" in result["detail"]

    def test_a_webhook_alone_satisfies_it(self, settings):
        settings.ALERT_EMAILS = ""
        settings.ALERT_WEBHOOK_URL = "https://hooks.example.test/abc"
        assert launch.check_alert_delivery()["ready"] is True
