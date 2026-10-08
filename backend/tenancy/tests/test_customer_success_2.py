"""Customer Success 2.0: health score, command center, adoption, onboarding,
executive view, timeline, alerts, success tasks and the in-app assistant."""
from datetime import timedelta

import pytest
from django.core import mail
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from tenancy import success
from tenancy.context import no_tenant, tenant_context
from tenancy.models import (PlatformMetric, StatusNotice, SuccessTask, SupportMessage,
                            SupportRequest)

pytestmark = pytest.mark.django_db
K = PlatformMetric.Key


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


def _as(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def ops(platform_user):
    return _as(platform_user)


def _user(org, django_user_model, username, role="maker"):
    with tenant_context(org):
        return django_user_model.objects.create_user(
            username=username, email=f"{username}@abc.test", password="x-Pass-12345",
            first_name=username.title(), role=role, organization=org)


def _metric(org, key, days_ago, count=1):
    with no_tenant():
        PlatformMetric.objects.update_or_create(
            day=timezone.localdate() - timedelta(days=days_ago), key=key, organization=org,
            defaults={"count": count})


def _busy(org, days=20, people=5):
    for d in range(days):
        _metric(org, K.ACTIVE_USER, d, people)
        _metric(org, K.ATTENDANCE_USED, d, people)
        if d % 3 == 0:
            _metric(org, K.TASK_USED, d, 2)
            _metric(org, K.DOCUMENT_USED, d, 1)


def _row(slug):
    return next(r for r in success.health() if r["slug"] == slug)


# ---------------------------------------------------------------------------
# Part 2: the score
# ---------------------------------------------------------------------------
def test_the_score_is_a_weighted_blend_of_six_components():
    days = lambda n: {timezone.localdate() - timedelta(days=i) for i in range(n)}  # noqa: E731
    busy = success.score_components(
        seats=6, usage={K.ACTIVE_USER: 100},
        days_by_key={K.ACTIVE_USER: days(20), K.ATTENDANCE_USED: days(20),
                     K.TASK_USED: days(7), K.DOCUMENT_USED: days(4)},
        support={"open": 1, "critical": 0, "overdue": 0})
    assert success.total(busy) >= 90                       # "ABC School: 92"
    quiet = success.score_components(
        seats=10, usage={K.ACTIVE_USER: 12},
        days_by_key={K.ACTIVE_USER: days(6), K.ATTENDANCE_USED: days(5)},
        support={"open": 3, "critical": 1, "overdue": 1})
    assert success.total(quiet) < 50                        # "XYZ Hospital: 41 — needs attention"
    assert set(busy) == set(success.WEIGHTS) and sum(success.WEIGHTS.values()) == 100


def test_health_ranks_an_active_customer_above_an_idle_one(org, nif):
    _busy(org)
    with tenant_context(org):
        org.seat_count = 6
    from tenancy.models import Organization
    Organization.objects.filter(pk=org.pk).update(seat_count=6)
    active, idle = _row(org.slug), _row(nif.slug)
    assert active["health"] > idle["health"]
    assert {c["key"] for c in active["components"]} == set(success.WEIGHTS)
    assert "no_attendance" in {r["code"] for r in idle["reasons"]}


def test_overdue_tickets_pull_the_support_component_down(org):
    _busy(org)
    with no_tenant():
        SupportRequest.objects.create(organization=org, kind="problem", category="bug",
                                      message="x", priority="critical",
                                      sla_due_at=timezone.now() - timedelta(hours=1))
    support = next(c for c in _row(org.slug)["components"] if c["key"] == "support")
    assert support["score"] == 60                            # -25 overdue, -15 critical


# ---------------------------------------------------------------------------
# Part 1 / 8 / 9 / 10: command center, adoption, onboarding, executive
# ---------------------------------------------------------------------------
def test_command_center_segments(ops, org):
    data = ops.get("/api/v1/platform/success/command-center/").json()
    seg = {s["key"]: s for s in data["segments"]}
    assert set(seg) == {"at_risk", "inactive", "low_adoption", "no_attendance", "no_tasks",
                        "no_employees", "trial_ending", "new"}
    assert org.slug in seg["new"]["organizations"]
    assert org.slug in seg["no_employees"]["organizations"]
    assert data["average_health"] is not None and len(data["weights"]) == 6


def test_adoption_counts_organizations_exactly(ops, org, nif):
    for d in (0, 1, 2):
        _metric(org, K.ACTIVE_USER, d, 4)
    _metric(org, K.ATTENDANCE_USED, 0)
    _metric(nif, K.ACTIVE_USER, 10, 2)
    data = ops.get("/api/v1/platform/success/adoption/").json()
    assert data["active_orgs"] == {"daily": 1, "weekly": 1, "monthly": 2}
    assert data["adoption_30d"]["attendance"]["percent"] == 50
    assert len(data["weekly_trend"]) == 12 and len(data["daily_active_orgs"]) == 30


def test_onboarding_milestones(ops, org, django_user_model):
    _user(org, django_user_model, "founder", role="admin")     # the founding user
    _user(org, django_user_model, "teacher")                   # the first employee
    _metric(org, K.ACTIVE_USER, 2)
    _metric(org, K.TASK_USED, 1)
    data = ops.get("/api/v1/platform/success/onboarding/").json()
    row = next(r for r in data["organizations"] if r["slug"] == org.slug)
    marks = {m["key"]: m["reached_on"] for m in row["milestones"]}
    assert marks["first_login"] and marks["first_task"] and marks["first_employee"]
    assert marks["first_attendance"] is None and marks["first_payment"] is None
    assert row["completed"] == 3 and row["total"] == 6


def test_the_founding_user_is_not_counted_as_an_employee_added(org, django_user_model):
    def added():
        with no_tenant():
            return sum(PlatformMetric.objects.filter(organization=org, key=K.EMPLOYEE_ADDED)
                       .values_list("count", flat=True))
    with no_tenant():
        from django.contrib.auth import get_user_model
        existing = get_user_model().all_tenants.filter(organization=org).count()
    before = added()
    _user(org, django_user_model, "second")
    assert added() == before + 1 if existing else added() == before


def test_executive_view(ops, org):
    from tenancy.models import Subscription
    with no_tenant():
        Subscription.objects.filter(organization=org).update(
            status="trial", trial_end=timezone.localdate() + timedelta(days=10),
            current_period_end=None)
    data = ops.get("/api/v1/platform/success/executive/").json()
    for key in ("top_active", "at_risk", "renewals_due", "support_load", "satisfaction",
                "mrr_minor", "arr_minor", "tickets_deflected_30d"):
        assert key in data
    assert any(r["slug"] == org.slug for r in data["renewals_due"])   # trial ends < 30d


# ---------------------------------------------------------------------------
# Part 5: timeline
# ---------------------------------------------------------------------------
def test_timeline_tells_the_customer_story(ops, org):
    _metric(org, K.ATTENDANCE_USED, 3)
    with no_tenant():
        SupportRequest.objects.create(organization=org, kind="problem", category="leave",
                                      subject="Can't apply", message="x", number=900)
        SupportRequest.objects.create(organization=org, kind="feature", category="feature_request",
                                      subject="Excel export", message="x", number=901)
    events = ops.get(f"/api/v1/platform/organizations/{org.slug}/timeline/").json()
    titles = [e["title"] for e in events]
    assert "Organization created" in titles
    assert "Support ticket opened" in titles and "Feature request created" in titles
    assert "Attendance enabled (first record)" in titles
    assert events == sorted(events, key=lambda e: e["at"], reverse=True)


# ---------------------------------------------------------------------------
# Part 3 / 11: alerts
# ---------------------------------------------------------------------------
def test_ticket_alerts_email_once_then_repeat_overdue_daily(org, settings):
    settings.PLATFORM_SUPPORT_EMAIL = "team@platform.test"
    now = timezone.now()
    with no_tenant():
        t = SupportRequest.objects.create(
            organization=org, kind="problem", category="login", priority="critical",
            subject="Nobody can sign in", message="x", number=950,
            sla_due_at=now - timedelta(minutes=10))
        SupportRequest.objects.filter(pk=t.pk).update(created_at=now - timedelta(hours=5))
    alerts = success.ticket_alerts()
    assert {k for k, v in alerts.items() if v} == {"critical", "overdue", "no_response"}
    mail.outbox.clear()
    first = success.run_ticket_alerts(now)
    assert first["alerted"] == 3 and len(mail.outbox) == 1
    assert "Nobody can sign in" in mail.outbox[0].body
    assert success.run_ticket_alerts(now + timedelta(minutes=15))["alerted"] == 0
    assert success.run_ticket_alerts(now + timedelta(hours=25))["by_kind"]["overdue"] == 1


def test_customer_signals_open_one_task_each_and_a_digest(org, settings):
    settings.PLATFORM_SUPPORT_EMAIL = "team@platform.test"
    from tenancy.models import Organization
    Organization.objects.filter(pk=org.pk).update(created_at=timezone.now() - timedelta(days=20))
    mail.outbox.clear()
    result = success.run_customer_alerts()
    assert result["tasks_opened"] >= 2
    with no_tenant():
        reasons = set(SuccessTask.objects.filter(organization=org).values_list("auto_reason", flat=True))
    assert {"no_employees", "no_attendance"} <= reasons
    assert mail.outbox and "reach out" in mail.outbox[0].subject
    assert success.run_customer_alerts()["tasks_opened"] == 0      # no duplicates


def test_the_alert_command_runs(org):
    from io import StringIO
    from django.core.management import call_command
    out = StringIO()
    call_command("customer_success_alerts", stdout=out)
    call_command("customer_success_alerts", "--customers", stdout=out)
    assert "support_sla_alerts" in out.getvalue() and "success_customers" in out.getvalue()


# ---------------------------------------------------------------------------
# Part 4: tasks
# ---------------------------------------------------------------------------
def test_success_tasks_are_platform_only(ops, org, platform_user, django_user_model):
    created = ops.post("/api/v1/platform/success/tasks/", {
        "organization": org.slug, "kind": "training", "title": "Attendance training",
        "assigned_to": str(platform_user.pk), "due_date": str(timezone.localdate())}, format="json")
    assert created.status_code == 201, created.content
    task = created.json()
    assert task["kind_display"] == "Product training" and task["assigned_to"] == str(platform_user.pk)
    done = ops.patch(f"/api/v1/platform/success/tasks/{task['id']}/", {"status": "done"}, format="json")
    assert done.json()["status"] == "done" and done.json()["completed_at"]
    member = _user(org, django_user_model, "member", role="admin")
    assert ops.post("/api/v1/platform/success/tasks/", {"organization": org.slug,
                    "assigned_to": str(member.pk)}, format="json").status_code == 400
    assert _as(member).get("/api/v1/platform/success/tasks/").status_code == 403
    listed = ops.get("/api/v1/platform/success/tasks/", {"organization": org.slug}).json()
    assert [t["title"] for t in listed] == ["Attendance training"]


# ---------------------------------------------------------------------------
# Part 6: the assistant; Part 7: badges
# ---------------------------------------------------------------------------
def test_the_assistant_suggests_answers_known_issues_and_counts_deflection(org, django_user_model):
    member = _user(org, django_user_model, "asha")
    colleague = _user(org, django_user_model, "ram")
    with no_tenant():
        mine = SupportRequest.objects.create(
            organization=org, kind="problem", category="attendance", number=970, status="resolved",
            subject="Check in button missing on home", message="The check in button disappeared",
            submitted_by_email=member.email)
        SupportMessage.objects.create(ticket=mine, author_kind="staff", author_name="Gita",
                                      body="Location permission was off — enable it in the browser.")
        SupportRequest.objects.create(
            organization=org, kind="problem", category="attendance", number=971,
            subject="Check in button missing", message="gone", submitted_by_email=colleague.email)
        StatusNotice.objects.create(component="attendance", level="degraded",
                                    message="Device collection delayed.")
    data = _as(member).post("/api/v1/support/assist/", {
        "query": "check in button missing", "category": "attendance"}, format="json").json()
    assert [t["reference"] for t in data["similar_tickets"]] == ["SUP-000970"]   # not the colleague's
    assert "Location permission" in data["similar_tickets"][0]["answer"]
    assert data["known_issues"][0]["message"] == "Device collection delayed."
    _as(member).post("/api/v1/support/assist/", {"deflected": True}, format="json")
    with no_tenant():
        assert PlatformMetric.objects.filter(organization=org, key=K.TICKET_DEFLECTED).exists()


def test_badges_count_unread_replies_and_unseen_updates(org, django_user_model, ops):
    member = _user(org, django_user_model, "badge")
    with no_tenant():
        SupportRequest.objects.create(organization=org, kind="problem", number=980, message="x",
                                      submitted_by_email=member.email, unread_by_customer=True)
    ops.post("/api/v1/platform/product-updates/", {"title": "New", "publish": True}, format="json")
    badges = _as(member).get("/api/v1/support/badges/").json()
    assert badges == {"tickets_unread": 1, "updates_unseen": 1}
