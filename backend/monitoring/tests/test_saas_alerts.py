"""Phase S11 Part 3: the nine SaaS alerts, and the boundary around them.

TWO THINGS ARE BEING TESTED, and the second is a security property rather
than a monitoring one.

1. Each alert fires when the condition it describes is true, and does not
   fire when it is not. An alert that is always on is noise people mute; an
   alert that is never on is a false sense of coverage.

2. **The SaaS metrics never reach a tenant.** `monitoring/views.py` serves
   `metrics.collect()` to `IsOperator` -- a role held by HR and Admin users
   INSIDE a customer's workspace. Every SaaS metric is platform-wide: which
   tenants are stuck, which hostnames are failing, the payment backlog
   across all customers. If the default board ever starts carrying them,
   every customer's administrator gets a running commentary on every other
   customer, and no test of the alerts themselves would notice.
"""
import datetime

import pytest
from django.utils import timezone

from monitoring import alerts, metrics
from monitoring.saas import saas_platform
from tenancy.context import no_tenant, tenant_context

pytestmark = pytest.mark.django_db


# The tenancy fixtures live in `tenancy/tests/conftest.py`, which pytest does
# not share across packages. Declared here rather than hoisted into a project
# conftest: these three are all this module needs, and widening the scope of a
# shared fixture to satisfy one test file is how conftests become unreadable.
@pytest.fixture
def monthly_plan(db):
    from tenancy.models import Plan

    return Plan.objects.get(code="monthly")


@pytest.fixture
def nif(db):
    from tenancy.models import Organization

    return Organization.objects.get(slug="nif")


@pytest.fixture
def org(db, monthly_plan):
    from tenancy import services

    return services.provision_organization(
        name="Metric Co", slug="metric-co", document_prefix="MTCO",
        email="admin@metric-co.test", plan=monthly_plan,
        today=datetime.date(2026, 10, 6))


def board():
    with no_tenant():
        return {m["key"]: m for m in saas_platform()}


def firing(keys=None):
    with no_tenant():
        collected = metrics.collect(platform=True)
    return {a["key"]: a for a in alerts.evaluate(collected)}


# ---------------------------------------------------------------------------
# the boundary
# ---------------------------------------------------------------------------
class TestTheSaaSSectionIsNotTenantVisible:
    def test_the_default_board_has_no_saas_section(self):
        keys = {s["key"] for s in metrics.collect()["sections"]}
        assert "saas" not in keys

    def test_and_carries_no_saas_metric_under_any_section(self):
        """Checked by prefix rather than by section name, so moving a metric
        into an existing section does not slip past this."""
        leaked = [m["key"] for s in metrics.collect()["sections"]
                  for m in s["metrics"] if m["key"].startswith("saas_")]
        assert leaked == []

    def test_the_platform_board_does_have_it(self):
        with no_tenant():
            keys = {s["key"] for s in metrics.collect(platform=True)["sections"]}
        assert "saas" in keys

    def test_the_tenant_facing_view_asks_for_the_default_board(self):
        """The call site, not just the default. A view that passed
        `platform=True` would defeat the default entirely."""
        import inspect

        from monitoring import views

        source = inspect.getsource(views)
        assert "collect(platform=True)" not in source
        assert "metrics.collect()" in source

    def test_the_alerter_asks_for_the_platform_board(self):
        import inspect

        source = inspect.getsource(alerts.evaluate)
        assert "platform=True" in source


# ---------------------------------------------------------------------------
# every alert has a rule, and every rule an alert
# ---------------------------------------------------------------------------
class TestRuleCoverage:
    REQUIRED = {
        "saas_provisioning_stuck", "saas_provisioning_failed",
        "saas_registration_stalled", "saas_verification_failed",
        "saas_domain_failed", "saas_export_failed", "saas_restore_needed",
        "saas_subscription_expiry", "saas_payment_backlog", "saas_5xx_rate",
    }

    def test_every_saas_metric_has_a_rule(self):
        """A metric with no rule is a number on a dashboard nobody reads."""
        produced = set(board())
        assert self.REQUIRED <= produced, self.REQUIRED - produced
        without = [key for key in produced if key not in alerts.RULES]
        assert without == [], f"metrics with no alert rule: {without}"

    def test_every_saas_rule_has_a_metric(self):
        """And a rule with no metric can never fire, which is worse: it
        looks like coverage."""
        produced = set(board()) | {"saas_platform"}
        orphans = [key for key in alerts.RULES
                   if key.startswith("saas_") and key not in produced]
        assert orphans == [], f"rules that can never fire: {orphans}"

    def test_the_probes_own_failure_fires(self):
        """`metrics.safe` turns an exception into one red metric named after
        the function. Without a rule for it, a broken probe takes all nine
        alerts offline and fires nothing -- monitoring that fails quiet."""
        assert "saas_platform" in alerts.RULES

    def test_every_rule_says_what_to_do(self):
        for key, (severity, title, action) in alerts.RULES.items():
            if not key.startswith("saas"):
                continue
            assert severity in (alerts.CRITICAL, alerts.WARNING, alerts.INFO)
            assert title and len(title) > 8, key
            # The action is what somebody reads at 03:00. A rule that only
            # restates the title is not an action.
            assert len(action) > 60, f"{key}: action too thin"
            assert action.lower() != title.lower()


# ---------------------------------------------------------------------------
# quiet when nothing is wrong
# ---------------------------------------------------------------------------
def test_a_healthy_platform_fires_no_saas_alert(nif):
    assert [k for k in firing() if k.startswith("saas")] == []


# ---------------------------------------------------------------------------
# the nine conditions
# ---------------------------------------------------------------------------
def test_provisioning_stuck(org):
    from tenancy.models import Organization

    with no_tenant():
        Organization.objects.filter(pk=org.pk).update(
            status=Organization.Status.PROVISIONING)
    metric = board()["saas_provisioning_stuck"]
    assert metric["state"] == metrics.RED
    assert org.slug in metric["detail"]
    assert firing()["saas_provisioning_stuck"]["severity"] == alerts.CRITICAL


def test_provisioning_failures_are_counted_from_the_platform_counters(db):
    from tenancy import metrics as tenancy_metrics
    from tenancy.models import PlatformMetric

    assert board()["saas_provisioning_failed"]["state"] == metrics.GREEN
    with no_tenant():
        for _ in range(3):
            tenancy_metrics.bump(PlatformMetric.Key.PROVISION_FAILED)
    metric = board()["saas_provisioning_failed"]
    assert metric["value"] == 3
    assert metric["state"] == metrics.RED


def test_a_verified_signup_with_no_workspace_is_critical(db):
    """The worst state in the product: the customer has done everything
    asked of them and been told so."""
    from tenancy.models import PendingRegistration

    with no_tenant():
        PendingRegistration.objects.create(
            organization_name="Stalled Co", slug="stalled-co",
            organization_email="a@stalled-co.test",
            admin_name="A", admin_email="a@stalled-co.test",
            admin_password="x", token_hash="h" * 64,
            token_expires_at=timezone.now() + datetime.timedelta(hours=1),
            status=PendingRegistration.Status.VERIFIED,
            verified_at=timezone.now() - datetime.timedelta(hours=2))
    metric = board()["saas_registration_stalled"]
    assert metric["state"] == metrics.RED
    assert "stalled-co" in metric["detail"]
    assert firing()["saas_registration_stalled"]["severity"] == alerts.CRITICAL


def test_a_signup_verified_moments_ago_is_not_yet_an_alert(db):
    """Provisioning follows verification in the same request, but a retry
    window keeps a healthy signup from paging anybody."""
    from tenancy.models import PendingRegistration

    with no_tenant():
        PendingRegistration.objects.create(
            organization_name="Fresh Co", slug="fresh-co",
            organization_email="a@fresh-co.test", admin_name="A",
            admin_email="a@fresh-co.test", admin_password="x",
            token_hash="i" * 64,
            token_expires_at=timezone.now() + datetime.timedelta(hours=1),
            status=PendingRegistration.Status.VERIFIED,
            verified_at=timezone.now())
    assert board()["saas_registration_stalled"]["state"] == metrics.GREEN


def test_signups_that_never_verify_are_graded_on_volume(db):
    from tenancy.models import PendingRegistration

    with no_tenant():
        for n in range(10):
            PendingRegistration.objects.create(
                organization_name=f"Gone {n}", slug=f"gone-{n}",
                organization_email=f"a{n}@gone.test", admin_name="A",
                admin_email=f"a{n}@gone.test", admin_password="x",
                token_hash=f"{n:064d}",
                token_expires_at=timezone.now() - datetime.timedelta(hours=1),
                status=PendingRegistration.Status.EXPIRED)
    metric = board()["saas_verification_failed"]
    assert metric["value"] == 10
    assert metric["state"] == metrics.RED
    # A warning, not a page: some of this is people abandoning a form.
    assert firing()["saas_verification_failed"]["severity"] == alerts.WARNING


def test_a_domain_that_will_not_verify(org, settings):
    settings.TENANCY_ENABLED = True
    settings.TENANCY_BASE_DOMAIN = "platform.test"
    settings.TENANCY_PLATFORM_HOSTS = "admin.platform.test"
    from tenancy import domains
    from tenancy.models import TenantDomain

    domain = domains.claim(org, "hr.example-co.com")
    assert board()["saas_domain_failed"]["state"] == metrics.GREEN

    with no_tenant():
        TenantDomain.objects.filter(pk=domain.pk).update(
            status=TenantDomain.Status.FAILED)
    metric = board()["saas_domain_failed"]
    assert metric["state"] == metrics.AMBER
    assert "hr.example-co.com" in metric["detail"]


def test_a_claim_checked_repeatedly_without_succeeding_also_counts(org,
                                                                    settings):
    """Phase S9's console surfaces the check count as the column that
    matters: somebody pressing "Check now" eleven times has misread their
    DNS panel and will not work it out alone."""
    settings.TENANCY_ENABLED = True
    settings.TENANCY_BASE_DOMAIN = "platform.test"
    from tenancy import domains
    from tenancy.models import TenantDomain

    domain = domains.claim(org, "hr.example-co.com")
    with no_tenant():
        TenantDomain.objects.filter(pk=domain.pk).update(check_count=5)
    assert board()["saas_domain_failed"]["value"] == 1


def test_a_failed_export(org):
    from tenancy.models import TenantExport

    with no_tenant():
        TenantExport.objects.create(
            organization=org, organization_slug=org.slug,
            status=TenantExport.Status.FAILED, error="disk full")
    metric = board()["saas_export_failed"]
    assert metric["state"] == metrics.AMBER
    assert org.slug in metric["detail"]


def test_archived_while_still_subscribed_is_critical(org):
    """There is no "restore failed" record to count. This is the state a
    failed restore leaves behind -- and what a wrongly archived customer
    looks like. Both need the same phone call."""
    from tenancy.models import Organization

    assert board()["saas_restore_needed"]["state"] == metrics.GREEN
    with no_tenant():
        Organization.objects.filter(pk=org.pk).update(
            status=Organization.Status.ARCHIVED)
    metric = board()["saas_restore_needed"]
    assert metric["state"] == metrics.RED
    assert org.slug in metric["detail"]
    assert firing()["saas_restore_needed"]["severity"] == alerts.CRITICAL


def test_expiring_is_amber_and_lapsed_is_red(org):
    from tenancy.models import Subscription

    with no_tenant():
        Subscription.objects.filter(organization=org).update(
            status=Subscription.Status.ACTIVE,
            current_period_end=timezone.localdate() + datetime.timedelta(days=3))
    metric = board()["saas_subscription_expiry"]
    assert metric["state"] == metrics.AMBER, metric
    assert "expiring within 7 days" in metric["detail"]

    with no_tenant():
        Subscription.objects.filter(organization=org).update(
            status=Subscription.Status.GRACE)
    metric = board()["saas_subscription_expiry"]
    assert metric["state"] == metrics.RED
    assert "grace" in metric["detail"]


def test_the_payment_backlog_is_measured_in_hours_not_count(org, monthly_plan):
    """Twenty receipts this morning is a busy day. One from Friday still
    waiting on Monday is a customer who paid and is locked out, and a count
    cannot tell those apart."""
    from tenancy import payments
    from tenancy.models import Payment

    assert board()["saas_payment_backlog"]["value"] == 0

    with no_tenant():
        payment = payments.create_payment(org, monthly_plan)
        Payment.objects.filter(pk=payment.pk).update(
            status=Payment.Status.SUBMITTED,
            submitted_at=timezone.now() - datetime.timedelta(hours=30))
    metric = board()["saas_payment_backlog"]
    assert metric["unit"] == "hours"
    assert 29 <= metric["value"] <= 31
    assert metric["state"] == metrics.AMBER

    with no_tenant():
        Payment.objects.filter(pk=payment.pk).update(
            submitted_at=timezone.now() - datetime.timedelta(hours=100))
    assert board()["saas_payment_backlog"]["state"] == metrics.RED
    assert firing()["saas_payment_backlog"]["severity"] == alerts.CRITICAL


def test_twenty_fresh_receipts_do_not_fire(org, monthly_plan):
    from tenancy.models import Payment

    from tenancy import payments

    with no_tenant():
        for _ in range(20):
            payment = payments.create_payment(org, monthly_plan)
            Payment.objects.filter(pk=payment.pk).update(
                status=Payment.Status.SUBMITTED, submitted_at=timezone.now())
    assert board()["saas_payment_backlog"]["state"] == metrics.GREEN


class TestServerErrorRate:
    @pytest.fixture(autouse=True)
    def _clean(self):
        from monitoring import request_stats

        request_stats.reset()
        yield
        request_stats.reset()

    def test_a_handful_of_requests_is_never_a_rate(self):
        """1 error in 3 requests is 100% and means nothing. A rate on a tiny
        sample is how a monitoring system teaches people to ignore it."""
        from monitoring import request_stats

        request_stats.record(500)
        request_stats.record(200)
        request_stats.record(200)
        metric = board()["saas_5xx_rate"]
        assert metric["state"] == metrics.GREEN
        assert "too few requests" in metric["detail"]

    def test_a_real_spike_fires_critical(self):
        from monitoring import request_stats

        for _ in range(90):
            request_stats.record(200)
        for _ in range(10):
            request_stats.record(500)
        metric = board()["saas_5xx_rate"]
        assert metric["value"] == pytest.approx(10.0, abs=0.5)
        assert metric["state"] == metrics.RED
        assert firing()["saas_5xx_rate"]["severity"] == alerts.CRITICAL

    def test_a_clean_platform_under_real_traffic_is_green(self):
        from monitoring import request_stats

        for _ in range(200):
            request_stats.record(200)
        assert board()["saas_5xx_rate"]["state"] == metrics.GREEN

    def test_the_window_slides(self):
        """A spike an hour ago must not still be inflating the rate now."""
        import time

        from monitoring import request_stats

        old = time.time() - (request_stats.WINDOW_MINUTES + 3) * 60
        for _ in range(50):
            request_stats.record(500, when=old)
        for _ in range(50):
            request_stats.record(200)
        total, errors, rate = request_stats.window()
        assert errors == 0, "a stale bucket is still being counted"
        assert total == 50

    def test_counting_never_raises_on_the_error_path(self, monkeypatch):
        """It runs on every request, including the ones already failing."""
        from django.core.cache import cache

        from monitoring import request_stats

        def boom(*args, **kwargs):
            raise RuntimeError("cache gone")

        monkeypatch.setattr(cache, "incr", boom)
        monkeypatch.setattr(cache, "add", boom)
        request_stats.record(500)      # must not raise

    def test_the_middleware_counts_a_real_response(self, client):
        from monitoring import request_stats

        before = request_stats.window()[0]
        client.get("/api/v1/health/")
        assert request_stats.window()[0] == before + 1
