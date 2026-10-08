"""Customer Success Center: smart tickets, the desk, SLA, CSAT, roadmap,
What's New and System Status."""
import io
from datetime import timedelta

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from tenancy import support
from tenancy.context import no_tenant, tenant_context
from tenancy.models import ProductUpdate, StatusNotice, SupportMessage, SupportRequest

pytestmark = pytest.mark.django_db
TICKETS = "/api/v1/support/requests/"
DESK = "/api/v1/platform/support/"


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


@pytest.fixture
def member(org, django_user_model):
    return _user(org, django_user_model, "bikash")


def _png():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(buf, "PNG")
    return SimpleUploadedFile("screen.png", buf.getvalue(), content_type="image/png")


def _ticket(user, **extra):
    body = {"category": "attendance", "subject": "Check-in button missing",
            "message": "Since this morning the button is gone.", "page": "/my-attendance",
            "url": "https://abcschool.buddhilabs.com/my-attendance",
            "context": {"browser": "Chrome 141", "device": "Desktop", "viewport": "1440x900"}}
    body.update(extra)
    response = _as(user).post(TICKETS, body, format="json")
    assert response.status_code == 201, response.content
    return response.json()


# ---------------------------------------------------------------------------
# Smart ticket creation
# ---------------------------------------------------------------------------
def test_a_ticket_captures_its_context_and_gets_a_reference(member, ops):
    sent = _as(member).post(TICKETS, {
        "category": "login", "subject": "Can't sign in", "message": "Wrong password loop.",
        "page": "/login", "url": "https://abcschool.buddhilabs.com/login",
        "context": '{"browser": "Chrome 141", "device": "Desktop"}',
        "screenshot": _png()}, format="multipart", HTTP_USER_AGENT="Mozilla/5.0 Chrome/141")
    assert sent.status_code == 201, sent.content
    body = sent.json()
    assert body["reference"].startswith("SUP-")
    assert body["priority"] == "high"            # login -> high, not the customer's choice
    assert body["screenshot"]["name"] == "screen.png"
    detail = ops.get(f"{DESK}{body['id']}/").json()
    ctx = detail["context"]
    assert ctx["organization"] == "ABC School" and ctx["tenant"] == "abcschool"
    assert ctx["browser"] == "Chrome 141" and ctx["device"] == "Desktop"
    assert "plan" in ctx and ctx["received_at"]
    assert detail["url"].endswith("/login") and "Chrome/141" in detail["user_agent"]
    hours = (timezone.datetime.fromisoformat(detail["sla_due_at"].replace("Z", "+00:00"))
             - timezone.datetime.fromisoformat(detail["created_at"].replace("Z", "+00:00")))
    assert round(hours.total_seconds() / 3600) == 8


def test_an_executable_attachment_is_refused(member):
    bad = SimpleUploadedFile("run.exe", b"MZ\x90\x00", content_type="application/octet-stream")
    response = _as(member).post(TICKETS, {"category": "bug", "message": "x", "attachment": bad},
                                format="multipart")
    assert response.status_code == 400 and "attachment" in response.json()


def test_a_feature_request_goes_on_the_roadmap_not_the_sla(member):
    body = _ticket(member, category="feature_request", subject="Export to Excel")
    assert body["kind"] == "feature" and body["roadmap_status"] == "submitted"
    listing = _as(member).get("/api/v1/support/feature-requests/").json()
    assert [r["subject"] for r in listing["requests"]] == ["Export to Excel"]
    assert [s["value"] for s in listing["roadmap"]] == [
        "submitted", "under_review", "planned", "in_development", "completed"]
    with no_tenant():
        assert SupportRequest.objects.get(pk=body["id"]).sla_due_at is None


# ---------------------------------------------------------------------------
# Who sees what
# ---------------------------------------------------------------------------
def test_tickets_are_private_to_the_sender_and_their_admin(org, member, django_user_model,
                                                           monthly_plan):
    from tenancy import services

    ticket = _ticket(member)
    colleague = _user(org, django_user_model, "colleague")
    admin = _user(org, django_user_model, "principal", role="admin")
    assert _as(colleague).get(f"{TICKETS}{ticket['id']}/").status_code == 404
    assert _as(admin).get(f"{TICKETS}{ticket['id']}/").status_code == 200
    assert _as(colleague).get(TICKETS).json() == []
    assert len(_as(admin).get(TICKETS, {"scope": "organization"}).json()) == 1
    other_org = services.provision_organization(
        name="XYZ Hospital", slug="xyzhospital", document_prefix="XYZ",
        email="a@xyz.test", plan=monthly_plan)
    outsider = _user(other_org, django_user_model, "outsider", role="admin")
    assert _as(outsider).get(f"{TICKETS}{ticket['id']}/").status_code == 404
    assert _as(outsider).get(f"{TICKETS}{ticket['id']}/files/screenshot/").status_code == 404
    assert _as(member).get(DESK).status_code == 403


# ---------------------------------------------------------------------------
# The conversation, internal notes, unread
# ---------------------------------------------------------------------------
def test_reply_note_and_unread(member, ops):
    ticket = _ticket(member)
    assert ops.get(DESK, {"view": "unread"}).json()["summary"]["unread"] == 1
    ops.get(f"{DESK}{ticket['id']}/")                     # opening it reads it
    assert ops.get(DESK).json()["summary"]["unread"] == 0

    mail.outbox.clear()
    note = ops.post(f"{DESK}{ticket['id']}/messages/",
                    {"body": "Looks like a geofence misconfig.", "internal": "true"})
    assert note.status_code == 201 and not mail.outbox    # notes are never emailed
    reply = ops.post(f"{DESK}{ticket['id']}/messages/", {"body": "Please refresh and retry."})
    assert reply.status_code == 201
    assert mail.outbox and mail.outbox[0].to == ["bikash@abc.test"]
    assert f"/help/tickets/{ticket['id']}" in mail.outbox[0].body

    seen = _as(member).get(f"{TICKETS}{ticket['id']}/").json()
    bodies = [m["body"] for m in seen["messages"]]
    assert "Please refresh and retry." in bodies
    assert "Looks like a geofence misconfig." not in bodies
    assert seen["status"] == "in_progress"

    answer = _as(member).post(f"{TICKETS}{ticket['id']}/messages/", {"body": "Still gone."})
    assert answer.status_code == 201
    assert ops.get(DESK).json()["summary"]["unread"] == 1


def test_internal_notes_are_not_in_the_customers_export(org, member, ops):
    from tenancy import export

    ticket = _ticket(member)
    ops.post(f"{DESK}{ticket['id']}/messages/", {"body": "secret note", "internal": "true"})
    ops.post(f"{DESK}{ticket['id']}/messages/", {"body": "public reply"})
    with no_tenant():
        rows = list(SupportMessage.objects.filter(ticket__organization=org)
                    .filter(**export.PLATFORM_SIDE_FILTERS["tenancy.SupportMessage"]))
    assert [m.body for m in rows] == ["public reply"]


# ---------------------------------------------------------------------------
# SLA: pause while waiting, escalation, assignment
# ---------------------------------------------------------------------------
def test_waiting_for_the_customer_pauses_the_sla(member, ops, monkeypatch):
    ticket = _ticket(member)
    with no_tenant():
        due = SupportRequest.objects.get(pk=ticket["id"]).sla_due_at
    start = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: start)
    ops.patch(f"{DESK}{ticket['id']}/", {"status": "waiting_customer"}, format="json")
    monkeypatch.setattr(timezone, "now", lambda: start + timedelta(hours=30))
    detail = ops.get(f"{DESK}{ticket['id']}/").json()
    assert detail["sla_paused"] is True and detail["overdue"] is False
    _as(member).post(f"{TICKETS}{ticket['id']}/messages/", {"body": "Here is more detail."})
    with no_tenant():
        record = SupportRequest.objects.get(pk=ticket["id"])
    assert record.status == "in_progress" and record.sla_paused_at is None
    assert record.sla_due_at == due + timedelta(hours=30)


def test_escalation_and_assignment(member, ops, platform_user, django_user_model):
    ticket = _ticket(member)                               # attendance -> medium, 24h
    response = ops.patch(f"{DESK}{ticket['id']}/", {"escalate": True}, format="json").json()
    assert response["priority"] == "high" and response["escalated"] is True
    assert ops.patch(f"{DESK}{ticket['id']}/", {"assigned_to": str(member.pk)},
                     format="json").status_code == 400  # only platform staff
    assigned = ops.patch(f"{DESK}{ticket['id']}/", {"assigned_to": str(platform_user.pk)},
                         format="json").json()
    assert assigned["assigned_to"] == str(platform_user.pk)
    assert assigned["status"] == "in_progress"
    assert ops.get(DESK, {"view": "mine"}).json()["requests"][0]["id"] == ticket["id"]
    thread = ops.get(f"{DESK}{ticket['id']}/").json()["messages"]
    assert any(m["is_internal"] and "Escalated" in m["body"] for m in thread)


def test_overview_counts_overdue_and_resolution_time(member, ops):
    late = _ticket(member, category="login")
    with no_tenant():
        SupportRequest.objects.filter(pk=late["id"]).update(
            sla_due_at=timezone.now() - timedelta(minutes=5), priority="critical")
    done = _ticket(member, category="bug")
    ops.patch(f"{DESK}{done['id']}/", {"status": "resolved"}, format="json")
    overview = ops.get(f"{DESK}overview/").json()
    assert overview["open"] == 1 and overview["critical"] == 1 and overview["overdue"] == 1
    assert overview["resolved"] == 1
    assert overview["avg_resolution_hours_30d"] is not None
    filtered = ops.get(DESK, {"view": "overdue"}).json()["requests"]
    assert [r["id"] for r in filtered] == [late["id"]]
    assert ops.get(DESK, {"category": "bug"}).json()["requests"][0]["id"] == done["id"]
    assert ops.get(DESK, {"q": late["reference"]}).json()["requests"][0]["id"] == late["id"]


# ---------------------------------------------------------------------------
# Resolution, satisfaction, reopen
# ---------------------------------------------------------------------------
def test_resolve_rate_once_and_reopen_by_reply(member, ops):
    ticket = _ticket(member)
    url = f"{TICKETS}{ticket['id']}/"
    assert _as(member).post(url, {"action": "rate", "rating": 5}).status_code == 400
    mail.outbox.clear()
    ops.patch(f"{DESK}{ticket['id']}/", {"status": "resolved"}, format="json")
    assert mail.outbox and "rate" in mail.outbox[0].body.lower()
    assert _as(member).get(url).json()["can_rate"] is True
    rated = _as(member).post(url, {"action": "rate", "rating": 4, "comment": "Quick, thanks"})
    assert rated.status_code == 200 and rated.json()["satisfaction_rating"] == 4
    assert _as(member).post(url, {"action": "rate", "rating": 1}).status_code == 400
    assert ops.get(f"{DESK}overview/").json()["csat_30d"]["average"] == 4.0

    _as(member).post(f"{url}messages/", {"body": "It broke again."})
    assert _as(member).get(url).json()["status"] == "open"
    _as(member).post(url, {"action": "close"})
    assert _as(member).post(f"{url}messages/", {"body": "hello?"}).status_code == 400


def test_completing_a_feature_request_tells_the_customer(member, ops):
    ticket = _ticket(member, category="feature_request", subject="Dark mode")
    ops.patch(f"{DESK}{ticket['id']}/", {"roadmap_status": "planned"}, format="json")
    mine = _as(member).get("/api/v1/support/feature-requests/").json()["requests"][0]
    assert mine["roadmap_status"] == "planned" and mine["unread"] is True
    ops.patch(f"{DESK}{ticket['id']}/", {"roadmap_status": "completed"}, format="json")
    detail = _as(member).get(f"{TICKETS}{ticket['id']}/").json()
    assert detail["status"] == "resolved"
    assert any("Completed" in m["body"] for m in detail["messages"])


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
def test_screenshots_are_served_only_to_people_who_can_see_the_ticket(member, ops):
    sent = _as(member).post(TICKETS, {"category": "bug", "message": "see screenshot",
                                      "screenshot": _png()}, format="multipart").json()
    own = _as(member).get(f"{TICKETS}{sent['id']}/files/screenshot/")
    assert own.status_code == 200 and own["X-Content-Type-Options"] == "nosniff"
    assert ops.get(f"{DESK}{sent['id']}/files/screenshot/").status_code == 200
    assert _as(member).get(f"{TICKETS}{sent['id']}/files/attachment/").status_code == 404


# ---------------------------------------------------------------------------
# What's New and System Status
# ---------------------------------------------------------------------------
def test_product_updates_drafts_publishing_and_unseen(member, ops, settings):
    # The "seen" mark writes the member's own row. Under RLS the test client
    # binds the request by HOSTNAME, so it must arrive on the member's own
    # workspace host -- as a real request does.
    settings.TENANCY_BASE_DOMAIN = "platform.test"
    host = {"HTTP_HOST": "abcschool.platform.test"}
    draft = ops.post("/api/v1/platform/product-updates/",
                     {"title": "New Branding Center", "category": "new"}, format="json")
    assert draft.status_code == 201
    assert _as(member).get("/api/v1/support/updates/").json()["updates"] == []
    ops.patch(f"/api/v1/platform/product-updates/{draft.json()['id']}/",
              {"publish": True}, format="json")
    feed = _as(member).get("/api/v1/support/updates/").json()
    assert feed["unseen"] == 1 and feed["updates"][0]["title"] == "New Branding Center"
    _as(member).post("/api/v1/support/updates/", **host)
    with tenant_context(member.organization_id):
        member.refresh_from_db()       # a real request loads the user afresh
    assert _as(member).get("/api/v1/support/updates/").json()["unseen"] == 0
    assert ops.post("/api/v1/platform/product-updates/",
                    {"title": "x", "link": "https://evil.test"}, format="json").status_code == 400
    assert _as(member).post("/api/v1/platform/product-updates/", {"title": "x"},
                            format="json").status_code == 403


def test_system_status_reports_components_and_notices(member, ops):
    status = _as(member).get("/api/v1/support/status/").json()
    keys = [c["key"] for c in status["components"]]
    assert keys == ["platform", "email", "storage", "payments", "domains", "attendance"]
    assert status["overall"] in ("operational", "maintenance", "degraded", "outage")
    notice = ops.post("/api/v1/platform/status-notices/", {
        "component": "email", "level": "outage",
        "message": "Our email provider is down; replies are queued."}, format="json")
    assert notice.status_code == 201
    status = _as(member).get("/api/v1/support/status/").json()
    email = next(c for c in status["components"] if c["key"] == "email")
    assert email["state"] == "outage" and "queued" in email["note"]
    assert status["overall"] == "outage" and status["notices"]
    ops.post(f"/api/v1/platform/status-notices/{notice.json()['id']}/resolve/")
    status = _as(member).get("/api/v1/support/status/").json()
    assert not status["notices"]


def test_the_open_view_matches_the_open_count(member, ops):
    _ticket(member)
    _ticket(member, category="feature_request", subject="Dark mode")
    data = ops.get(DESK, {"view": "open"}).json()
    assert len(data["requests"]) == data["summary"]["open"] == 1
    assert ops.get(DESK, {"kind": "feature"}).json()["requests"][0]["subject"] == "Dark mode"
