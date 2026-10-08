"""Support Desk 3.0: teams, ownership, transfer, escalation rules, mentions,
links, ticket tasks, known issues, the SLA board, campaigns, the founder view."""
from datetime import timedelta

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from tenancy import desk
from tenancy.context import no_tenant, tenant_context
from tenancy.models import (KnownIssue, PlatformMetric, SuccessCampaign, SuccessTask,
                            SupportMention, SupportMessage, SupportRequest, SupportTeam,
                            SupportTeamMember)

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


def _staff(django_user_model, username, first=""):
    with no_tenant():
        return django_user_model.objects.create_user(
            username=username, email=f"{username}@platform.test", password="x-Platform-1",
            first_name=first or username.title(), is_platform_staff=True, organization=None)


def _user(org, django_user_model, username, role="maker"):
    with tenant_context(org):
        return django_user_model.objects.create_user(
            username=username, email=f"{username}@abc.test", password="x-Pass-12345",
            first_name=username.title(), role=role, organization=org)


@pytest.fixture
def member(org, django_user_model):
    return _user(org, django_user_model, "bikash")


def _team(key):
    with no_tenant():
        return SupportTeam.objects.get(key=key)


def _join(team, user, role="agent", available=True):
    with no_tenant():
        return SupportTeamMember.objects.create(team=team, user=user, role=role,
                                                is_available=available)


def _ticket(user, **extra):
    body = {"category": "billing", "subject": "Invoice shows the wrong amount",
            "message": "We were charged twice this month.", "page": "/billing"}
    body.update(extra)
    response = _as(user).post(TICKETS, body, format="json")
    assert response.status_code == 201, response.content
    return response.json()


def _get(ticket_id):
    with no_tenant():
        return SupportRequest.objects.select_related("team", "assigned_to").get(pk=ticket_id)


def _notes(ticket_id):
    with no_tenant():
        return list(SupportMessage.objects.filter(ticket_id=ticket_id, is_internal=True)
                    .values_list("body", flat=True))


# ---------------------------------------------------------------------------
# Part 1: teams and routing -- every ticket has an owner
# ---------------------------------------------------------------------------
def test_the_four_teams_exist_and_route_by_category():
    with no_tenant():
        teams = {t.key: t for t in SupportTeam.objects.all()}
    assert set(teams) == {"technical", "billing", "customer-success", "platform"}
    assert desk.route("billing").key == "billing"
    assert desk.route("domain").key == "platform"
    assert desk.route("attendance").key == "technical"
    assert desk.route("other").key == "customer-success"
    assert desk.route("").is_default


def test_a_new_ticket_is_owned_by_its_team_and_the_least_loaded_available_agent(
        member, ops, django_user_model):
    billing = _team("billing")
    busy, free, away = (_staff(django_user_model, n) for n in ("busy", "free", "away"))
    _join(billing, busy)
    _join(billing, free)
    _join(billing, away, available=False)
    first = _ticket(member)
    assert _get(first["id"]).assigned_to == busy            # all at zero: first member
    second = _ticket(member)
    assert _get(second["id"]).assigned_to == free           # busy now has one
    t = _get(second["id"])
    assert t.team == billing and t.status == "in_progress"
    assert any("Routed to Billing Team, assigned to Free" in n for n in _notes(t.pk))
    # The customer never sees the desk's internals.
    seen = _as(member).get(f"{TICKETS}{t.pk}/").json()
    assert "team" not in seen and "assigned_to" not in seen
    assert all(not m["is_internal"] for m in seen["messages"])


def test_an_unstaffed_team_still_owns_the_ticket(member):
    t = _get(_ticket(member, category="domain", subject="Domain not verifying")["id"])
    assert t.team.key == "platform" and t.assigned_to is None and t.status == "open"


def test_teams_are_managed_from_the_console_only(ops, member, django_user_model, platform_user):
    created = ops.post(f"{DESK}teams/", {"name": "Enterprise Desk",
                                         "categories": ["billing", "nonsense"]}, format="json")
    assert created.status_code == 201, created.content
    team = created.json()
    assert team["key"] == "enterprise-desk" and team["categories"] == ["billing"]
    # One owner per category: Billing gave it up.
    assert "billing" not in _team("billing").categories
    agent = _staff(django_user_model, "lead1")
    r = ops.post(f"{DESK}teams/{team['id']}/members/", {"user": str(agent.pk), "role": "lead"},
                 format="json")
    assert r.json()["members"][0]["role"] == "lead"
    # A tenant user can neither be a member nor reach the endpoints.
    assert ops.post(f"{DESK}teams/{team['id']}/members/", {"user": str(member.pk)},
                    format="json").status_code == 400
    for url in ("teams/", "sla/", "mentions/", "known-issues/"):
        assert _as(member).get(f"{DESK}{url}").status_code == 403
    assert _as(member).get("/api/v1/platform/success/campaigns/").status_code == 403
    assert _as(member).get("/api/v1/platform/success/operations/").status_code == 403
    listed = {t["key"]: t for t in ops.get(f"{DESK}teams/").json()}
    assert listed["enterprise-desk"]["members"][0]["name"] == "Lead1"


# ---------------------------------------------------------------------------
# Part 2: assign, reassign, transfer, escalate
# ---------------------------------------------------------------------------
def test_reassign_and_unassign_leave_a_trail(member, ops, django_user_model):
    a, b = _staff(django_user_model, "anil"), _staff(django_user_model, "bina")
    t = _ticket(member)
    ops.patch(f"{DESK}{t['id']}/", {"assigned_to": str(a.pk)}, format="json")
    ops.patch(f"{DESK}{t['id']}/", {"assigned_to": str(b.pk)}, format="json")
    ops.patch(f"{DESK}{t['id']}/", {"assigned_to": None}, format="json")
    notes = _notes(t["id"])
    assert "Assigned to Anil." in notes and "Reassigned from Anil to Bina." in notes
    assert "Unassigned — stays in the Billing Team queue." in notes
    assert _get(t["id"]).team.key == "billing"               # still owned


def test_transfer_moves_the_ticket_and_reroutes_inside_the_new_team(member, ops, django_user_model):
    tech = _team("technical")
    engineer = _staff(django_user_model, "engineer")
    _join(tech, engineer)
    t = _ticket(member)
    row = ops.post(f"{DESK}{t['id']}/transfer/", {"team": str(tech.pk),
                                                   "note": "It is a calculation bug"},
                   format="json").json()
    assert row["team_name"] == "Technical Team" and row["assigned_to"] == str(engineer.pk)
    assert any(n.startswith("Transferred from Billing Team to Technical Team, assigned to Engineer")
               for n in _notes(t["id"]))
    assert any("Transferred to you" in m.subject for m in mail.outbox)
    again = ops.post(f"{DESK}{t['id']}/transfer/", {"team": str(tech.pk)}, format="json")
    assert again.status_code == 400


def test_manual_escalation_raises_the_level_hands_to_a_lead_and_tells_leadership(
        member, ops, django_user_model, settings):
    settings.SUPPORT_LEADERSHIP_EMAILS = ["head-of-support@platform.test"]
    lead = _staff(django_user_model, "sita")
    _join(_team("platform"), lead, role="lead")
    t = _ticket(member, category="domain", subject="Domain down")
    with no_tenant():
        SupportTeamMember.objects.filter(user=lead).update(is_available=False)   # not auto-picked
        SupportRequest.objects.filter(pk=t["id"]).update(assigned_to=None, status="open")
    mail.outbox.clear()
    row = ops.patch(f"{DESK}{t['id']}/", {"escalate": True, "escalate_reason": "Whole school locked out"},
                    format="json").json()
    assert row["escalation_level"] == 1 and row["priority"] == "critical" and row["escalated"]
    assert row["assigned_to"] == str(lead.pk)
    sent = [m for m in mail.outbox if "[Escalation L1]" in m.subject]
    assert sent and set(sent[0].to) == {"head-of-support@platform.test", lead.email}
    # Critical tickets can still be escalated further.
    assert ops.patch(f"{DESK}{t['id']}/", {"escalate": True}, format="json").json()["escalation_level"] == 2


# ---------------------------------------------------------------------------
# Part 8: escalation rules
# ---------------------------------------------------------------------------
def _critical(org, hours_open, number, **extra):
    now = timezone.now()
    with no_tenant():
        t = SupportRequest.objects.create(
            organization=org, kind="problem", category="bug", priority="critical", number=number,
            subject=f"Critical {number}", message="Nothing loads", team=SupportTeam.objects.get(key="technical"),
            sla_due_at=now - timedelta(hours=hours_open) + timedelta(hours=4), **extra)
        SupportRequest.objects.filter(pk=t.pk).update(created_at=now - timedelta(hours=hours_open))
        return t


def test_a_critical_ticket_open_over_four_hours_is_escalated_once(org, settings):
    settings.SUPPORT_LEADERSHIP_EMAILS = ["lead@platform.test"]
    late = _critical(org, 5, 501)
    fresh = _critical(org, 1, 502)
    paused = _critical(org, 6, 503, status="waiting_customer", sla_paused_at=timezone.now() - timedelta(hours=1))
    result = desk.run_escalations()
    assert result["tickets"] == [late.reference]
    late = _get(late.pk)
    assert late.escalated and late.escalation_level == 1 and late.escalated_at
    assert any("Escalated automatically to level 1" in n for n in _notes(late.pk))
    assert not _get(fresh.pk).escalated and not _get(paused.pk).escalated
    assert any(m.to == ["lead@platform.test"] and "SUP-000501" in m.subject for m in mail.outbox)
    assert desk.run_escalations()["escalated"] == 0              # once per rule


def test_the_fifteen_minute_job_runs_the_rules_and_the_dashboard_shows_them(org, ops):
    _critical(org, 5, 511)
    call_command("customer_success_alerts")
    alerts = ops.get("/api/v1/platform/success/alerts/").json()
    assert [e["reference"] for e in alerts["tickets"]["escalated"]] == ["SUP-000511"]
    board = ops.get(f"{DESK}sla/").json()
    assert board["escalated"]["count"] == 1 and board["rules"] == [{"priority": "critical", "hours": 4.0}]


def test_the_rule_hours_are_configurable(org, settings):
    settings.SUPPORT_ESCALATION_HOURS = {"critical": 8, "high": 6}
    _critical(org, 5, 521)
    assert desk.run_escalations()["escalated"] == 0


# ---------------------------------------------------------------------------
# Part 7: notes, mentions, links
# ---------------------------------------------------------------------------
def test_mentions_in_internal_notes_notify_the_agent_and_stay_internal(
        member, ops, django_user_model, platform_user):
    gita = _staff(django_user_model, "gita.sharma", first="Gita")
    t = _ticket(member)
    mail.outbox.clear()
    ops.post(f"{DESK}{t['id']}/messages/", {"body": "@gita can you check the gateway log?",
                                             "internal": True}, format="json")
    ops.post(f"{DESK}{t['id']}/messages/", {"body": "Hello @gita here is an update",
                                             "internal": False}, format="json")
    with no_tenant():
        assert SupportMention.objects.filter(user=gita).count() == 1
    assert any(m.to == [gita.email] and "mentioned you" in m.subject for m in mail.outbox)
    mine = _as(gita).get(f"{DESK}mentions/").json()
    assert mine["unread"] == 1 and mine["mentions"][0]["reference"] == _get(t["id"]).reference
    assert _as(gita).get("/api/v1/platform/success/alerts/").json()["mentions"]["unread"] == 1
    _as(gita).get(f"{DESK}{t['id']}/")                          # opening the ticket reads it
    assert _as(gita).get(f"{DESK}mentions/").json()["unread"] == 0
    seen = _as(member).get(f"{TICKETS}{t['id']}/").json()
    assert all("check the gateway" not in m["body"] for m in seen["messages"])


def test_tickets_can_be_linked_both_ways_by_reference(member, ops):
    a, b = _ticket(member), _ticket(member, subject="Charged twice again")
    ref_b = _get(b["id"]).reference
    linked = ops.post(f"{DESK}{a['id']}/links/", {"ticket": ref_b}, format="json").json()
    assert [x["reference"] for x in linked] == [ref_b]
    back = ops.get(f"{DESK}{b['id']}/").json()
    assert [x["id"] for x in back["linked"]] == [a["id"]]
    assert ops.post(f"{DESK}{a['id']}/links/", {"ticket": _get(a["id"]).reference},
                    format="json").status_code == 400
    assert ops.post(f"{DESK}{a['id']}/links/", {"ticket": ref_b, "remove": True},
                    format="json").json() == []


# ---------------------------------------------------------------------------
# Part 3: tasks on a ticket
# ---------------------------------------------------------------------------
def test_agents_open_follow_up_tasks_linked_to_the_ticket(member, ops, platform_user):
    t = _ticket(member, category="domain", subject="Set up portal.abcschool.edu.np")
    for kind in ("domain_setup", "payment_verification", "training", "follow_up"):
        r = ops.post(f"{DESK}{t['id']}/tasks/", {"kind": kind}, format="json")
        assert r.status_code == 201, r.content
    task = r.json()
    assert task["ticket"] == t["id"] and task["ticket_reference"] == _get(t["id"]).reference
    assert task["assigned_to"] == str(platform_user.pk) and task["organization"] == "abcschool"
    detail = ops.get(f"{DESK}{t['id']}/").json()
    assert {x["kind"] for x in detail["tasks"]} == {"domain_setup", "payment_verification",
                                                    "training", "follow_up"}
    listed = ops.get(f"/api/v1/platform/success/tasks/?ticket={t['id']}").json()
    assert len(listed) == 4 and listed[0]["ticket_reference"]
    assert ops.post(f"{DESK}{t['id']}/tasks/", {"kind": "nap"}, format="json").status_code == 400


# ---------------------------------------------------------------------------
# Part 4: known issues and the assistant
# ---------------------------------------------------------------------------
def test_a_known_issue_answers_other_customers_without_showing_their_tickets(
        member, ops, nif, django_user_model):
    t = _ticket(member, category="attendance", subject="Late marks wrong after daylight change",
                message="Everyone shows late by one hour since Sunday")
    issue = ops.post(f"{DESK}known-issues/", {
        "from_ticket": t["id"], "symptoms": "attendance late one hour wrong time shift",
        "workaround": "Re-save the shift under Settings → Attendance; it recalculates.",
        "state": "workaround"}, format="json").json()
    assert issue["tickets"] == 1 and issue["title"] == "Late marks wrong after daylight change"
    other = _user(nif, django_user_model, "nif-staff")
    hint = _as(other).post("/api/v1/support/assist/", {
        "query": "everyone marked late one hour wrong", "category": "attendance"},
        format="json").json()
    assert hint["known_solutions"][0]["id"] == issue["id"]
    assert hint["known_solutions"][0]["resolved_for"] == 1
    assert hint["possible_solution"]["source"] == "known_issue"
    assert "Re-save the shift" in hint["possible_solution"]["text"]
    assert hint["similar_tickets"] == []                        # never another org's ticket
    _as(other).post("/api/v1/support/assist/", {"shown": True}, format="json")
    _as(other).post("/api/v1/support/assist/", {"deflected": True, "source": f"issue:{issue['id']}"},
                    format="json")
    with no_tenant():
        assert KnownIssue.objects.get(pk=issue["id"]).deflected_count == 1
        assert PlatformMetric.objects.filter(key="assist_shown", organization=nif).exists()
    ops_view = ops.get("/api/v1/platform/success/operations/").json()
    assert ops_view["deflection_rate_30d"] == 100 and ops_view["assist_shown_30d"] == 1


def test_an_investigating_or_private_issue_is_not_offered(member, ops):
    ops.post(f"{DESK}known-issues/", {"title": "Payroll export timeout",
                                      "symptoms": "payroll export timeout", "workaround": ""},
             format="json")
    ops.post(f"{DESK}known-issues/", {"title": "Internal: payroll export slow",
                                      "symptoms": "payroll export timeout", "workaround": "x",
                                      "state": "workaround", "is_public": False}, format="json")
    hint = _as(member).post("/api/v1/support/assist/", {"query": "payroll export timeout"},
                            format="json").json()
    assert hint["known_solutions"] == [] and hint["possible_solution"] is None


def test_fixing_a_known_issue_resolves_every_linked_ticket(member, ops):
    a, b = _ticket(member, category="bug", subject="PDF blank"), _ticket(member, category="bug",
                                                                      subject="PDF blank again")
    issue = ops.post(f"{DESK}known-issues/", {"title": "Blank PDF exports"}, format="json").json()
    for t in (a, b):
        ops.patch(f"{DESK}{t['id']}/", {"known_issue": issue["id"]}, format="json")
    mail.outbox.clear()
    fixed = ops.patch(f"{DESK}known-issues/{issue['id']}/", {
        "state": "fixed", "resolve_linked": True}, format="json").json()
    assert fixed["resolved_now"] == 2 and fixed["state"] == "fixed" and fixed["open_tickets"] == 0
    assert _get(a["id"]).status == "resolved"
    assert sum(1 for m in mail.outbox if m.to == [member.email]) == 2
    seen = _as(member).get(f"{TICKETS}{a['id']}/").json()
    assert "is fixed" in seen["messages"][-1]["body"]


# ---------------------------------------------------------------------------
# Part 5: the SLA board
# ---------------------------------------------------------------------------
def test_the_sla_board(org, ops):
    now = timezone.now()
    with no_tenant():
        billing = SupportTeam.objects.get(key="billing")
        mk = lambda n, **kw: SupportRequest.objects.create(  # noqa: E731
            organization=org, kind="problem", number=n, message="x", team=billing, **kw)
        mk(601, priority="critical", sla_due_at=now + timedelta(minutes=90))           # critical due
        mk(602, priority="high", sla_due_at=now - timedelta(hours=1))                  # overdue
        mk(603, status="waiting_customer", sla_due_at=now - timedelta(hours=1),
           sla_paused_at=now - timedelta(hours=2))                                      # waiting, not overdue
        mk(604, status="resolved", resolved_at=now, sla_due_at=now + timedelta(hours=1))
        mk(605, status="resolved", resolved_at=now - timedelta(days=3),
           sla_due_at=now - timedelta(days=4))
    board = ops.get(f"{DESK}sla/").json()
    assert board["critical_due"]["count"] == 1 and board["critical_due"]["tickets"][0]["reference"] == "SUP-000601"
    assert [x["reference"] for x in board["overdue"]["tickets"]] == ["SUP-000602"]
    assert board["waiting_customer"]["count"] == 1
    assert board["waiting_team"]["count"] == 2
    assert board["resolved_today"]["count"] == 1
    assert board["sla_met_30d"] == {"resolved": 2, "on_time": 1, "percent": 50}
    by_team = {t["name"]: t for t in board["by_team"]}
    assert by_team["Billing Team"]["open"] == 3 and by_team["Billing Team"]["overdue"] == 1
    assert ops.get(f"{DESK}sla/?team={billing.pk}").json()["overdue"]["count"] == 1


def test_desk_views_for_escalated_unassigned_and_my_teams(member, ops, platform_user):
    _join(_team("billing"), platform_user)
    t = _ticket(member)                                 # auto-assigned to me
    other = _ticket(member, category="domain")          # platform team, nobody
    ids = lambda view: [r["id"] for r in ops.get(f"{DESK}?view={view}").json()["requests"]]  # noqa: E731
    assert ids("my_teams") == [t["id"]] and ids("unassigned") == [other["id"]]
    ops.patch(f"{DESK}{other['id']}/", {"escalate": True}, format="json")
    assert ids("escalated") == [other["id"]]
    s = ops.get(f"{DESK}overview/").json()
    assert s["escalated"] == 1 and s["waiting_team"] == 2


# ---------------------------------------------------------------------------
# Part 6: the customer story inside the ticket
# ---------------------------------------------------------------------------
def test_ticket_detail_carries_the_customer_story(member, ops, org):
    with no_tenant():
        PlatformMetric.objects.create(day=timezone.localdate(), key="active_user",
                                      organization=org, count=3)
    t = _ticket(member)
    ops.patch(f"{DESK}{t['id']}/", {"escalate": True}, format="json")
    story = ops.get(f"{DESK}{t['id']}/").json()["customer"]
    assert story["slug"] == "abcschool" and story["health"] is not None
    kinds = {e["kind"] for e in story["timeline"]}
    assert {"ticket_opened", "ticket_escalated", "usage"} <= kinds
    timeline = ops.get("/api/v1/platform/organizations/abcschool/timeline/").json()
    usage = next(e for e in timeline if e["kind"] == "usage")
    assert usage["detail"].startswith("3 active person-days")


# ---------------------------------------------------------------------------
# Part 9: campaigns
# ---------------------------------------------------------------------------
def test_a_campaign_opens_one_outreach_task_per_customer(org, nif, ops, platform_user):
    from tenancy.models import Subscription
    with no_tenant():
        Subscription.objects.filter(organization=org).update(
            status="active", current_period_end=timezone.localdate() + timedelta(days=10))
    data = ops.get("/api/v1/platform/success/campaigns/").json()
    near = next(s for s in data["segments"] if s["key"] == "near_renewal")
    assert near["count"] >= 1
    preview = ops.get("/api/v1/platform/success/campaigns/?segment=near_renewal").json()
    assert "abcschool" in [o["slug"] for o in preview["organizations"]]
    made = ops.post("/api/v1/platform/success/campaigns/", {
        "segment": "near_renewal", "assigned_to": str(platform_user.pk), "due_in_days": 5},
        format="json").json()
    assert made["created"] == near["count"] and made["task_title"] == "Renewal conversation"
    with no_tenant():
        task = SuccessTask.objects.get(campaign_id=made["id"], organization=org)
    assert task.kind == "outreach" and task.assigned_to_id == platform_user.pk
    assert task.due_date == timezone.localdate() + timedelta(days=5)
    again = ops.post("/api/v1/platform/success/campaigns/", {"segment": "near_renewal"},
                     format="json").json()
    assert again["created"] == 0 and again["skipped"] == made["created"]      # no double calls
    ops.patch(f"/api/v1/platform/success/tasks/{task.pk}/", {"status": "done"}, format="json")
    listed = {c["id"]: c for c in ops.get("/api/v1/platform/success/campaigns/").json()["campaigns"]}
    assert listed[made["id"]]["done"] == 1
    assert ops.post("/api/v1/platform/success/campaigns/", {"segment": "everyone"},
                    format="json").status_code == 400
    with no_tenant():
        assert SuccessCampaign.objects.count() == 2


# ---------------------------------------------------------------------------
# Part 10: the founder view
# ---------------------------------------------------------------------------
def test_the_operations_dashboard(org, ops, member):
    from tenancy.models import Subscription
    with no_tenant():
        Subscription.objects.filter(organization=org).update(
            status="active", current_period_end=timezone.localdate() + timedelta(days=20))
    _ticket(member)
    d = ops.get("/api/v1/platform/success/operations/").json()
    for key in ("mrr_minor", "arr_minor", "active_customers", "at_risk_customers", "open_tickets",
                "satisfaction", "renewal_pipeline", "renewals_upcoming", "currency",
                "deflection_rate_30d", "escalated_open", "bands"):
        assert key in d, key
    assert d["open_tickets"] == 1 and d["active_customers"] >= 1
    assert d["renewal_pipeline"]["30"]["count"] >= 1
    assert d["renewal_pipeline"]["30"]["value_minor"] > 0
    assert d["arr_minor"] == d["mrr_minor"] * 12
    assert any(r["slug"] == "abcschool" for r in d["renewals_upcoming"])
