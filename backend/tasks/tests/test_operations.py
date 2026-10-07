"""
Phase T4 — bulk operations, task groups, review management, calendar scopes,
search and archiving.

The rule running through all of it: an integration point may never widen what
somebody can see. A bulk endpoint, a group, a calendar lens and a search hit are
all just different doors onto the same scoped queryset.
"""
import datetime

import pytest

from tasks.models import Task, TaskGroup, TaskTemplate

from .conftest import LIST

pytestmark = pytest.mark.django_db

Status = Task.Status
BULK = f"{LIST}bulk/"
GROUPS = f"{LIST}groups/"
REVIEW_QUEUE = f"{LIST}review-queue/"
TEMPLATES = "/api/v1/task-templates/"


def seed_template(auth, user, name="Website Launch"):
    return auth(user).post(TEMPLATES, {
        "name": name,
        "description": "The standing launch runbook.",
        "priority": "high",
        "default_due_in_days": 21,
        "groups": [
            {"title": "Content", "items": ["Draft copy", "Proofread"]},
            {"title": "Infrastructure", "items": ["Provision", "Deploy"]},
        ],
    }, format="json").data


# ---------------------------------------------------------------------------
# Bulk operations (Part 8)
# ---------------------------------------------------------------------------
def test_a_bulk_priority_update_applies_to_every_task(cast, auth, make_task):
    tasks = [make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
             for _ in range(3)]
    response = auth(cast["hod"]).post(BULK, {
        "action": "priority", "priority": "urgent",
        "task_ids": [str(t.id) for t in tasks],
    }, format="json")

    assert response.status_code == 200, response.data
    assert len(response.data["applied"]) == 3
    assert response.data["refused"] == []
    for task in tasks:
        task.refresh_from_db()
        assert task.priority == Task.Priority.URGENT


def test_a_bulk_due_date_update_can_clear_the_date(cast, auth, make_task,
                                                   tomorrow):
    """
    `due_date: null` is how a date is removed, so presence is checked rather
    than truthiness — otherwise clearing a date would be silently ignored.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=tomorrow)
    response = auth(cast["hod"]).post(BULK, {
        "action": "due_date", "due_date": None, "task_ids": [str(task.id)],
    }, format="json")
    assert response.status_code == 200, response.data
    task.refresh_from_db()
    assert task.due_date is None


def test_partial_success_is_reported_rather_than_hidden(cast, auth, make_task):
    """
    Refusing the whole batch because one row was ineligible turns a fifty-task
    update into a guessing game; applying silently to whatever was allowed is
    worse still.
    """
    ok = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    closed = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                       status=Status.CLOSED)

    response = auth(cast["hod"]).post(BULK, {
        "action": "priority", "priority": "low",
        "task_ids": [str(ok.id), str(closed.id)],
    }, format="json")

    assert response.data["applied"] == [str(ok.id)]
    assert len(response.data["refused"]) == 1
    assert response.data["refused"][0]["task_number"] == closed.task_number
    assert response.data["refused"][0]["reason"]        # a readable sentence
    closed.refresh_from_db()
    assert closed.priority != Task.Priority.LOW


def test_a_bulk_request_cannot_reach_a_task_the_caller_cannot_see(cast, auth,
                                                                  make_task):
    hidden = make_task(cast["hod"], [cast["peer"]], status=Status.ASSIGNED)
    response = auth(cast["employee"]).post(BULK, {
        "action": "priority", "priority": "low", "task_ids": [str(hidden.id)],
    }, format="json")

    assert response.data["applied"] == []
    assert response.data["refused"][0]["reason"] == "Not found."
    hidden.refresh_from_db()
    assert hidden.priority != Task.Priority.LOW


def test_bulk_close_respects_the_rule_that_the_doer_cannot_close(cast, auth,
                                                                 make_task):
    """The single-task guard is the bulk guard; there is not a second one."""
    mine = make_task(cast["hr"], [cast["hr"]], reviewer=cast["hr"],
                     status=Status.COMPLETED)
    response = auth(cast["hr"]).post(BULK, {
        "action": "close", "task_ids": [str(mine.id)],
    }, format="json")
    assert response.data["applied"] == []
    mine.refresh_from_db()
    assert mine.status == Status.COMPLETED


def test_an_action_missing_its_payload_is_refused(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    response = auth(cast["hod"]).post(BULK, {
        "action": "priority", "task_ids": [str(task.id)],
    }, format="json")
    assert response.status_code == 400
    assert "priority" in response.data


def test_a_bulk_request_is_capped(cast, auth):
    """
    Uncapped bulk endpoints are how a mis-typed filter becomes a thousand-row
    write under one request timeout.
    """
    import uuid

    response = auth(cast["hod"]).post(BULK, {
        "action": "archive",
        "task_ids": [str(uuid.uuid4()) for _ in range(201)],
    }, format="json")
    assert response.status_code == 400


def test_an_employee_who_is_not_on_the_task_cannot_bulk_edit(cast, auth, make_task):
    """
    Bulk priority is `can_edit`, and `can_edit` is the owner or somebody
    assigned. A bystander is neither — and cannot even see the task, so the
    batch reports it as not found rather than as refused.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    response = auth(cast["peer"]).post(BULK, {
        "action": "priority", "priority": "low", "task_ids": [str(task.id)],
    }, format="json")
    assert response.data["applied"] == []


def test_an_assignee_can_bulk_set_priority_on_their_own_task(cast, auth, make_task):
    """
    Phase TASK-MULTI-ASSIGNEE-COLLABORATION: priority is one of the four fields
    the people doing the work may change, and the bulk route reads the same
    `can_edit` the single-task route does.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    response = auth(cast["employee"]).post(BULK, {
        "action": "priority", "priority": "low", "task_ids": [str(task.id)],
    }, format="json")
    assert response.data["applied"] == [str(task.id)]
    task.refresh_from_db()
    assert task.priority == "low"


# ---------------------------------------------------------------------------
# Archiving (Part 8)
# ---------------------------------------------------------------------------
def test_archiving_files_a_task_out_of_the_default_list(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.CLOSED)
    auth(cast["hod"]).post(BULK, {"action": "archive",
                                  "task_ids": [str(task.id)]}, format="json")

    listed = [r["id"] for r in auth(cast["hod"]).get(LIST).data["results"]]
    assert str(task.id) not in listed
    # ...but nothing becomes unfindable by being tidied away.
    archived = [r["id"] for r in
                auth(cast["hod"]).get(LIST, {"archived": "1"}).data["results"]]
    assert str(task.id) in archived
    assert auth(cast["hod"]).get(f"{LIST}{task.id}/").status_code == 200


def test_unfinished_work_cannot_be_archived(cast, auth, make_task):
    """Filing something still in flight would hide work somebody is waiting on."""
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    response = auth(cast["hod"]).post(
        BULK, {"action": "archive", "task_ids": [str(task.id)]}, format="json")
    assert response.data["applied"] == []
    task.refresh_from_db()
    assert task.archived_at is None


def test_archiving_is_not_cancelling(cast, auth, make_task):
    """
    Every completion metric counts closed work, so folding archiving into
    cancellation would quietly delete finished work from the numbers.
    """
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.CLOSED)
    auth(cast["hod"]).post(BULK, {"action": "archive",
                                  "task_ids": [str(task.id)]}, format="json")
    task.refresh_from_db()
    assert task.status == Status.CLOSED
    assert task.is_archived is True


def test_an_archived_task_can_be_restored(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.CLOSED)
    auth(cast["hod"]).post(BULK, {"action": "archive",
                                  "task_ids": [str(task.id)]}, format="json")
    auth(cast["hod"]).post(BULK, {"action": "unarchive",
                                  "task_ids": [str(task.id)]}, format="json")
    task.refresh_from_db()
    assert task.archived_at is None


# ---------------------------------------------------------------------------
# Task groups (Part 7)
# ---------------------------------------------------------------------------
def test_a_group_raises_one_task_per_template_section(cast, auth):
    """
    The sections are the natural seams of a multi-step job, and they are
    different people's work. One task with eight sections leaves one assignee
    holding all of it.
    """
    template = seed_template(auth, cast["hod"])
    response = auth(cast["hod"]).post(GROUPS, {
        "template": template["id"], "name": "Website Launch — Q3",
    }, format="json")

    assert response.status_code == 201, response.data
    assert len(response.data["tasks"]) == 2
    titles = {t["title"] for t in response.data["tasks"]}
    assert titles == {"Content", "Infrastructure"}
    assert response.data["group"]["task_count"] == 2


def test_each_task_in_a_group_carries_its_own_section_checklist(cast, auth):
    template = seed_template(auth, cast["hod"])
    body = auth(cast["hod"]).post(GROUPS, {"template": template["id"]},
                                  format="json").data
    content = next(t for t in body["tasks"] if t["title"] == "Content")
    rows = auth(cast["hod"]).get(f"{LIST}{content['id']}/checklist/").data
    assert [r["text"] for r in rows] == ["Draft copy", "Proofread"]


def test_every_task_in_a_group_gets_its_own_number(cast, auth):
    template = seed_template(auth, cast["hod"])
    body = auth(cast["hod"]).post(GROUPS, {"template": template["id"]},
                                  format="json").data
    numbers = {t["task_number"] for t in body["tasks"]}
    assert len(numbers) == 2
    assert all(n.startswith("NIFN-TSK-") for n in numbers)


def test_a_group_applies_the_templates_due_offset(cast, auth, today):
    """A template carries days rather than a date precisely so this works
    whenever it is used."""
    template = seed_template(auth, cast["hod"])
    body = auth(cast["hod"]).post(GROUPS, {"template": template["id"]},
                                  format="json").data
    expected = (today + datetime.timedelta(days=21)).isoformat()
    assert all(t["due_date"] == expected for t in body["tasks"])


def test_a_group_can_be_assigned_on_creation(cast, auth):
    template = seed_template(auth, cast["hod"])
    body = auth(cast["hod"]).post(GROUPS, {
        "template": template["id"],
        "assignee_ids": [str(cast["employee"].id)],
    }, format="json").data
    assert all(t["status"] == Status.ASSIGNED for t in body["tasks"])
    assert all(cast["employee"].get_full_name() in t["assignee_names"]
               for t in body["tasks"])


def test_a_template_with_no_checklist_cannot_raise_a_group(cast, auth):
    """A batch of empty tasks is nobody's idea of a useful button."""
    empty = auth(cast["hod"]).post(TEMPLATES, {
        "name": "Empty Shell", "priority": "low", "items": [],
    }, format="json").data
    response = auth(cast["hod"]).post(GROUPS, {"template": empty["id"]},
                                      format="json")
    assert response.status_code == 400


def test_an_employee_cannot_raise_a_task_group(cast, auth):
    template = seed_template(auth, cast["hod"])
    assert auth(cast["employee"]).post(
        GROUPS, {"template": template["id"]}, format="json").status_code == 403


def test_raising_a_group_counts_every_task_against_the_template(cast, auth):
    template = seed_template(auth, cast["hod"])
    auth(cast["hod"]).post(GROUPS, {"template": template["id"]}, format="json")
    assert TaskTemplate.objects.get(pk=template["id"]).usage_count == 2


# ---------------------------------------------------------------------------
# Review management (Part 6)
# ---------------------------------------------------------------------------
def test_the_review_queue_is_oldest_first_with_its_age(cast, auth, make_task):
    from django.utils import timezone

    old = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                    status=Status.UNDER_REVIEW)
    recent = make_task(cast["hod"], [cast["peer"]], reviewer=cast["hod"],
                       status=Status.UNDER_REVIEW)
    Task.objects.filter(pk=old.pk).update(
        submitted_at=timezone.now() - datetime.timedelta(days=12))

    body = auth(cast["hod"]).get(REVIEW_QUEUE).data
    assert [r["task_id"] for r in body["rows"]][0] == str(old.id)
    assert body["rows"][0]["waiting_days"] == 12
    assert body["summary"]["oldest_days"] == 12
    assert body["summary"]["waiting"] == 2


def test_the_review_queue_names_the_bottleneck(cast, auth, make_task):
    """A backlog with a name is actionable; a number is not."""
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.UNDER_REVIEW)
    make_task(cast["hod"], [cast["peer"]], reviewer=cast["hod"],
              status=Status.UNDER_REVIEW)

    body = auth(cast["hr"]).get(REVIEW_QUEUE).data
    entry = next(r for r in body["by_reviewer"]
                 if r["reviewer"] == cast["hod"].get_full_name())
    assert entry["waiting"] == 2


def test_review_age_ignores_the_due_date(cast, auth, make_task, today):
    """
    A task submitted three weeks ago against a deadline three months out IS a
    bottleneck — and a due-date report would never show it.
    """
    from django.utils import timezone

    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW,
                     due_date=today + datetime.timedelta(days=90))
    Task.objects.filter(pk=task.pk).update(
        submitted_at=timezone.now() - datetime.timedelta(days=21))

    row = auth(cast["hod"]).get(REVIEW_QUEUE).data["rows"][0]
    assert row["waiting_days"] == 21


def test_the_review_queue_is_scoped(cast, auth, make_task):
    mine = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW)
    make_task(cast["hod"], [cast["peer"]], reviewer=cast["hod"],
              status=Status.UNDER_REVIEW)

    rows = auth(cast["employee"]).get(REVIEW_QUEUE).data["rows"]
    assert [r["task_id"] for r in rows] == [str(mine.id)]


# ---------------------------------------------------------------------------
# Calendar lenses (Part 9)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("owner", ["all", "me", "team", "department", "review"])
def test_every_calendar_lens_is_accepted(cast, auth, owner):
    response = auth(cast["hod"]).get(f"{LIST}calendar/",
                                     {"view": "month", "owner": owner})
    assert response.status_code == 200
    assert response.data["owner"] == owner


def test_a_nonsense_lens_is_refused(cast, auth):
    assert auth(cast["hod"]).get(
        f"{LIST}calendar/", {"owner": "everyone"}).status_code == 400


def test_the_me_lens_narrows_to_my_own_tasks(cast, auth, make_task, today):
    mine = make_task(cast["hod"], [cast["hod"]], status=Status.ASSIGNED,
                     due_date=today)
    theirs = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                       due_date=today)

    ids = {e["task"]["id"] for e in auth(cast["hod"]).get(
        f"{LIST}calendar/", {"view": "day", "owner": "me"}).data["events"]}
    assert str(mine.id) in ids and str(theirs.id) not in ids


def test_a_lens_can_only_narrow_never_widen(cast, auth, make_task, today):
    """
    `team` and `department` filter an ALREADY-scoped set. An employee asking for
    the department lens gets their own visible tasks, not their department's.
    """
    hidden = make_task(cast["hod"], [cast["peer"]], status=Status.ASSIGNED,
                       due_date=today)
    ids = {e["task"]["id"] for e in auth(cast["employee"]).get(
        f"{LIST}calendar/", {"view": "day", "owner": "department"}).data["events"]}
    assert str(hidden.id) not in ids


def test_the_review_lens_shows_what_is_with_me_to_decide(cast, auth, make_task,
                                                         today):
    waiting = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                        status=Status.UNDER_REVIEW, due_date=today)
    working = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
                        due_date=today)

    ids = {e["task"]["id"] for e in auth(cast["hod"]).get(
        f"{LIST}calendar/", {"view": "day", "owner": "review"}).data["events"]}
    assert str(waiting.id) in ids and str(working.id) not in ids


# ---------------------------------------------------------------------------
# Search (Part 10)
# ---------------------------------------------------------------------------
def test_search_finds_a_task_by_its_reviewer(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.ASSIGNED)
    found = [r["id"] for r in auth(cast["hr"]).get(
        LIST, {"search": cast["hod"].get_full_name()}).data["results"]]
    assert str(task.id) in found


def test_search_finds_a_task_by_its_template_name(cast, auth):
    """People search by the thing out loud — "the onboarding one"."""
    template = seed_template(auth, cast["hod"], name="HR Onboarding")
    body = auth(cast["hod"]).post(GROUPS, {"template": template["id"]},
                                  format="json").data
    found = [r["id"] for r in auth(cast["hod"]).get(
        LIST, {"search": "Onboarding"}).data["results"]]
    assert set(found) == {t["id"] for t in body["tasks"]}


def test_search_still_finds_by_number_and_title(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     title="Audit the vehicle log")
    for term in (task.task_number, "vehicle"):
        found = [r["id"] for r in auth(cast["hod"]).get(
            LIST, {"search": term}).data["results"]]
        assert str(task.id) in found, term


def test_search_is_still_scoped(cast, auth, make_task):
    """A search hit can never be a task the searcher could not open."""
    make_task(cast["hod"], [cast["peer"]], status=Status.ASSIGNED,
              title="Secret vehicle audit")
    found = auth(cast["employee"]).get(LIST, {"search": "vehicle"}).data["results"]
    assert found == []


def test_an_archived_task_keeps_every_door_except_the_default_list(cast, auth,
                                                                   make_task):
    """
    A record you cannot open is deleted, whatever the field is called. The
    archive filter belongs to the LIST action alone — `get_queryset()` is also
    where `get_object()` looks.
    """
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.CLOSED, title="Filed away")
    auth(cast["hod"]).post(BULK, {"action": "archive",
                                  "task_ids": [str(task.id)]}, format="json")

    client = auth(cast["hod"])
    assert client.get(f"{LIST}{task.id}/").status_code == 200
    assert client.get(f"{LIST}{task.id}/timeline/").status_code == 200
    # Still searchable, still on the reports it belongs to.
    assert str(task.id) in [
        r["id"] for r in client.get(LIST, {"search": "Filed away",
                                           "archived": "1"}).data["results"]]
    assert client.get(f"{LIST}reports/status/").data["summary"]["total"] >= 1
