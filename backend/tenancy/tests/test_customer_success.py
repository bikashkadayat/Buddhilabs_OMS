"""Customer success: adoption counts, customer health, support, tours.

The adoption tests matter for a privacy reason as much as a product one: the
console learns "this customer is using leave" from a COUNT, and nothing about
any leave request.
"""
import datetime

import pytest
from django.core import mail
from django.core.cache import cache
from rest_framework.test import APIClient

from tenancy import adoption
from tenancy.context import no_tenant, tenant_context
from tenancy.models import Organization, PlatformMetric, SupportRequest

pytestmark = pytest.mark.django_db
K = PlatformMetric.Key


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


@pytest.fixture
def ops(platform_user):
    client = APIClient()
    client.force_authenticate(user=platform_user)
    return client


@pytest.fixture
def member(org, django_user_model):
    with tenant_context(org):
        return django_user_model.objects.create_user(
            username="cs-member", email="member@abc.test", password="x-Pass-12345",
            first_name="Asha", role="maker", organization=org)


def _count(org, key):
    with no_tenant():
        return sum(PlatformMetric.objects.filter(organization=org, key=key)
                   .values_list("count", flat=True))


def test_a_person_counts_as_active_once_a_day(org, member, platform_user):
    adoption.record_active(member)
    adoption.record_active(member)
    adoption.record_active(platform_user)          # operators are not customers
    assert _count(org, K.ACTIVE_USER) == 1


def test_using_a_module_is_counted_without_recording_what(org, member):
    from leaves.models import Leave

    with tenant_context(org):
        Leave.objects.create(user=member, leave_type="annual",
                             start_date=datetime.date(2026, 9, 1),
                             end_date=datetime.date(2026, 9, 1), reason="Private")
    assert _count(org, K.LEAVE_USED) == 1
    with no_tenant():
        row = PlatformMetric.objects.get(organization=org, key=K.LEAVE_USED)
    # The counter row holds a day, a key, an organization and a number.
    assert {f.name for f in row._meta.fields} == {"id", "day", "key", "organization", "count"}


def test_customer_health_names_what_is_wrong(ops, org):
    body = ops.get("/api/v1/platform/customer-health/").json()
    row = next(r for r in body["organizations"] if r["slug"] == org.slug)
    codes = {r["code"] for r in row["reasons"]}
    assert {"no_employees", "inactive", "no_attendance"} <= codes
    assert row["health"] < 100
    assert "series" in body["platform"] and body["platform"]["series"]["active_users"]


def test_a_customer_in_use_looks_healthier(ops, org, member, nif):
    adoption.record_active(member)
    with no_tenant():
        Organization.objects.filter(pk=org.pk).update(seat_count=5)
    from attendance.models import Attendance

    with tenant_context(org):
        Attendance.objects.create(employee=member, date=datetime.date.today(), status="present")
    body = ops.get("/api/v1/platform/customer-health/").json()
    row = next(r for r in body["organizations"] if r["slug"] == org.slug)
    assert row["active_today"] == 1 and row["usage_30d"]["attendance"] == 1
    assert not {r["code"] for r in row["reasons"]} & {"no_employees", "inactive", "no_attendance"}


# --- Support Center ---------------------------------------------------------
def _as(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_support_requests_need_words_and_feedback_needs_a_rating(member):
    client = _as(member)
    assert client.post("/api/v1/support/requests/", {"kind": "problem", "message": " "},
                       format="json").status_code == 400
    assert client.post("/api/v1/support/requests/", {"kind": "feedback"},
                       format="json").status_code == 400
    ok = client.post("/api/v1/support/requests/", {"kind": "feedback", "rating": 4,
                                                   "feature": "leave-apply"}, format="json")
    assert ok.status_code == 201


def test_a_problem_reaches_the_platform_inbox_and_the_reply_reaches_the_customer(
        member, ops, settings):
    settings.PLATFORM_SUPPORT_EMAIL = "help@platform.test"
    sent = _as(member).post("/api/v1/support/requests/", {
        "kind": "problem", "subject": "Can't apply for leave",
        "message": "The button stays grey.", "page": "/leave/apply"}, format="json")
    assert sent.status_code == 201
    assert any("help@platform.test" in m.to for m in mail.outbox)
    inbox = ops.get("/api/v1/platform/support/").json()
    row = next(r for r in inbox["requests"] if r["id"] == sent.json()["id"])
    assert row["organization_name"] == "ABC School" and row["page"] == "/leave/apply"
    assert row["submitted_by_email"] == "member@abc.test"
    mail.outbox.clear()
    reply = ops.patch(f"/api/v1/platform/support/{row['id']}/",
                      {"status": "resolved", "response": "Fixed — please try again."},
                      format="json")
    assert reply.status_code == 200 and reply.json()["status"] == "resolved"
    assert mail.outbox and mail.outbox[0].to == ["member@abc.test"]
    mine = _as(member).get("/api/v1/support/requests/").json()
    assert mine[0]["response"] == "Fixed — please try again."


def test_a_person_sees_only_their_own_requests(member, org, django_user_model):
    with tenant_context(org):
        other = django_user_model.objects.create_user(
            username="cs-other", email="other@abc.test", password="x-Pass-12345",
            role="maker", organization=org)
    _as(member).post("/api/v1/support/requests/", {"kind": "contact", "message": "Hi"}, format="json")
    assert _as(other).get("/api/v1/support/requests/").json() == []


# --- Guided tours -------------------------------------------------------------
# Requests that write or read the member's OWN tenant rows are sent on their
# organization's hostname -- what binds that organization under row-level
# security, as in production. On `testserver` the request is bound to the
# default organization and RLS (rightly) hides this member's rows.
def _host(settings, org):
    settings.TENANCY_BASE_DOMAIN = "buddhilabs.com"
    settings.ALLOWED_HOSTS = ["*"]
    return f"{org.slug}.buddhilabs.com"


def test_a_finished_tour_is_remembered_on_every_device(member, org, settings):
    host = _host(settings, org)
    client = _as(member)
    assert client.post("/api/v1/profile/me/tour/", {"tour": "bogus"}, format="json",
                       HTTP_HOST=host).status_code == 400
    assert client.post("/api/v1/profile/me/tour/", {"tour": "employee"},
                       format="json", HTTP_HOST=host).json() == {"tours_done": ["employee"]}
    with tenant_context(org):
        member.refresh_from_db()
    assert member.ui_state["tours_done"] == ["employee"]


# --- Notification centre ------------------------------------------------------
def test_the_notification_centre_groups_by_what_people_think_in(member, org, settings):
    host = _host(settings, org)
    from notifications.models import Notification

    with tenant_context(org):
        for category in ("LEAVE_SUBMITTED", "TASK_ASSIGNED", "MEMO_APPROVED", "PAYMENT_APPROVED"):
            Notification.objects.create(recipient=member, category=category, title=category)
    client = _as(member)

    def titles(group):
        body = client.get("/api/v1/notifications/", {"group": group}, HTTP_HOST=host).json()
        rows = body.get("results", body) if isinstance(body, dict) else body
        return {r["title"] for r in rows}

    assert titles("approvals") == {"LEAVE_SUBMITTED"}
    assert titles("documents") == {"MEMO_APPROVED"}
    assert titles("payments") == {"PAYMENT_APPROVED"}


# --- Getting Started ------------------------------------------------------------
def test_the_new_getting_started_steps_are_measured(org, django_user_model):
    from django.utils import timezone

    from leaves.models import Department, Leave
    from tenancy import onboarding

    with tenant_context(org):
        admin = django_user_model.objects.create_user(
            username="cs-admin", email="adm@abc.test", password="x-Pass-12345",
            role="admin", organization=org)
        steps = lambda: {s["key"]: s["done"] for s in onboarding.state(admin)["checklist"]}  # noqa: E731
        assert not any(steps()[k] for k in ("department", "invite", "approve_leave"))
        teacher = django_user_model.objects.create_user(
            username="cs-teacher", email="t@abc.test", password="x-Pass-12345",
            role="maker", organization=org)
        Department.objects.update_or_create(code="ACAD", defaults={"name": "Academics", "head": admin})
        django_user_model.objects.filter(pk__in=[admin.pk, teacher.pk]).update(last_login=timezone.now())
        Leave.objects.create(user=teacher, leave_type="annual", status=Leave.Status.APPROVED,
                             start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 9, 1),
                             reason="Rest")
        assert all(steps()[k] for k in ("department", "invite", "approve_leave"))
