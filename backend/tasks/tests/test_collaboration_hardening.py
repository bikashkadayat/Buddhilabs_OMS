"""
Phase TASK-COLLABORATION-HARDENING.

Four corrections to the multi-assignee phase, each of which it got wrong in the
same direction: it read "no task activity until everybody accepts" as covering
things that are not activity at all.

  1. Talking is not working. Comments and clarification requests are open
     while a shared task waits to be accepted.
  2. An unchanged field is not an edit. Sending the task back as it stands must
     not be refused as an attempt to change it.
  3. A notification nobody can act on is noise.
  4. A question already answered in memory must not be asked of the database
     eleven more times.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from notifications.models import Category, Notification
from tasks import workflow
from tasks.models import Task
from .conftest import LIST

pytestmark = pytest.mark.django_db

Status = Task.Status


@pytest.fixture
def trio(cast, make_task):
    return make_task(cast["hod"], [cast["employee"], cast["peer"], cast["hr"]],
                     reviewer=cast["hod"], status=Status.ASSIGNED)


# ---------------------------------------------------------------------------
# 1. Talking is not working
# ---------------------------------------------------------------------------
def test_an_assignee_can_comment_before_anybody_has_accepted(trio, cast, auth):
    res = auth(cast["employee"]).post(
        f"{LIST}{trio.id}/comments/",
        {"body": "Can we do this by Friday if I take the cabling?"},
        format="json")
    assert res.status_code == 201, res.data


def test_an_assignee_can_comment_after_accepting_while_others_have_not(
        trio, cast, auth):
    workflow.accept(trio, cast["employee"])
    res = auth(cast["employee"]).post(
        f"{LIST}{trio.id}/comments/", {"body": "I am in. Prashanta?"},
        format="json")
    assert res.status_code == 201, res.data


def test_a_reply_and_a_mention_work_while_acceptance_is_pending(
        trio, cast, auth):
    parent = auth(cast["employee"]).post(
        f"{LIST}{trio.id}/comments/", {"body": "Who is taking the config?"},
        format="json").data
    res = auth(cast["peer"]).post(f"{LIST}{trio.id}/comments/", {
        "body": "I will.", "parent": parent["id"],
        "mention_ids": [str(cast["employee"].id)]}, format="json")
    assert res.status_code == 201, res.data
    assert Notification.objects.filter(
        category=Category.TASK_MENTIONED,
        recipient=cast["employee"]).exists()


def test_clarification_stays_open_to_everybody_while_pending(trio, cast, auth):
    """
    Including somebody who has already accepted: on shared work an assignee may
    have said yes and still be waiting on an answer the others need.
    """
    workflow.accept(trio, cast["employee"])
    for person in (cast["employee"], cast["peer"]):
        res = auth(person).post(
            f"{LIST}{trio.id}/request-clarification/",
            {"reason": "Which quarter is this report for, exactly?"},
            format="json")
        assert res.status_code == 200, res.data
        assert res.data["capabilities"]["can_request_clarification"] is True


def test_the_work_gate_is_still_shut_while_talking_is_open(trio, cast, auth):
    """The correction must not have opened the thing the gate exists for."""
    workflow.accept(trio, cast["employee"])
    client = auth(cast["employee"])
    assert client.post(f"{LIST}{trio.id}/comments/", {"body": "Noted, thanks."},
                       format="json").status_code == 201
    assert client.post(f"{LIST}{trio.id}/progress/", {"progress_percent": 50},
                       format="json").status_code == 403
    assert client.post(f"{LIST}{trio.id}/start/").status_code == 403


# ---------------------------------------------------------------------------
# 2. An unchanged field is not an edit
# ---------------------------------------------------------------------------
def test_an_assignee_may_send_the_task_back_exactly_as_it_stands(
        cast, auth, make_task):
    """
    A client that PATCHes the whole object — which is what an edit form does —
    used to be refused for including the assignee list it had just been given.
    """
    task = make_task(cast["hod"], [cast["employee"], cast["peer"]],
                     reviewer=cast["hod"], status=Status.ASSIGNED)
    res = auth(cast["employee"]).patch(f"{LIST}{task.id}/", {
        "title": "Rack the new switch and label every port",
        "description": "Unchanged everything else.",
        "priority": "high",
        "reviewer": str(cast["hod"].id),
        "assignee_ids": [str(cast["employee"].id), str(cast["peer"].id)],
        "checklist": [],
    }, format="json")
    assert res.status_code == 200, res.data
    task.refresh_from_db()
    assert task.title.endswith("label every port")
    # Nothing about the people moved, and no timeline row claims it did.
    assert task.assignees.count() == 2
    assert not task.audit_entries.filter(action="assignees_changed",
                                         actor=cast["employee"]).exists()


def test_the_order_of_an_unchanged_assignee_list_does_not_matter(
        cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"], cast["peer"]],
                     reviewer=cast["hod"], status=Status.ASSIGNED)
    res = auth(cast["employee"]).patch(f"{LIST}{task.id}/", {
        "title": "Same people, listed the other way round",
        "assignee_ids": [str(cast["peer"].id), str(cast["employee"].id)],
    }, format="json")
    assert res.status_code == 200, res.data
    # ...and the primary assignee is untouched, which is what the order decides.
    assert task.assignees.get(user=cast["employee"]).is_primary is True


def test_a_real_change_to_the_terms_is_still_refused(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.ASSIGNED)
    res = auth(cast["employee"]).patch(f"{LIST}{task.id}/", {
        "assignee_ids": [str(cast["employee"].id), str(cast["peer"].id)],
    }, format="json")
    assert res.status_code == 403
    assert "assignee_ids" in res.data["detail"]
    assert task.assignees.count() == 1


def test_an_assignee_may_not_rewrite_the_checklist(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.ASSIGNED)
    res = auth(cast["hod"]).post(f"{LIST}{task.id}/checklist/",
                                 {"items": ["Order the SFPs"]}, format="json")
    assert res.status_code == 200, res.data
    res = auth(cast["employee"]).patch(f"{LIST}{task.id}/", {
        "checklist": ["Order the SFPs", "And a step I added myself"],
    }, format="json")
    assert res.status_code == 403
    assert "checklist" in res.data["detail"]


def test_a_moved_due_date_is_recorded_with_both_values(cast, auth, make_task):
    """
    An assignee can now move the date they are measured against. That is the
    phase's intent — but a timeline row saying only "details edited" would make
    it unauditable, so the row carries what it was and what it became.
    """
    import datetime
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.ASSIGNED,
                     due_date=datetime.date.today() + datetime.timedelta(days=2))
    later = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    res = auth(cast["employee"]).patch(f"{LIST}{task.id}/",
                                       {"due_date": later}, format="json")
    assert res.status_code == 200, res.data
    row = task.audit_entries.filter(action="updated").last()
    assert later in row.remarks and "→" in row.remarks
    assert row.metadata["moved"]["due_date"][1] == later
    assert row.actor_id == cast["employee"].id


# ---------------------------------------------------------------------------
# 3. Notification noise
# ---------------------------------------------------------------------------
def test_one_shared_task_cycle_stays_in_single_figures(trio, cast):
    """
    The measured regression this phase fixes: acceptance plus a full round of
    progress on a three-person task sent 35 notifications, 28 of which asked
    nobody for anything.
    """
    Notification.objects.all().delete()
    people = [cast["employee"], cast["peer"], cast["hr"]]
    for person in people:
        workflow.accept(trio, person)
    workflow.start(trio, people[0])
    for percent in (25, 50, 75, 100):
        for person in people:
            workflow.update_progress(trio, person, percent)

    total = Notification.objects.count()
    assert total <= 12, {
        row["title"]: row["n"] for row in
        Notification.objects.values("title").annotate(
            n=__import__("django.db.models", fromlist=["Count"]).Count("id"))}


def test_the_owner_hears_about_the_last_acceptance_once(trio, cast):
    Notification.objects.all().delete()
    for person in (cast["employee"], cast["peer"], cast["hr"]):
        workflow.accept(trio, person)
    owner_told = Notification.objects.filter(recipient=cast["hod"])
    # Two partial acceptances, then ONE "ready to start" — not a third
    # "X accepted" a second before it saying the same thing.
    assert owner_told.count() == 3
    assert owner_told.filter(title="Task ready to start").count() == 1


def test_a_solo_task_notifies_exactly_as_it_did_before(cast, make_task):
    """Backward compatibility: none of the noise work touched the one-person
    path, which never had any of it."""
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.ASSIGNED)
    Notification.objects.all().delete()   # the assignment itself is not the subject
    workflow.accept(task, cast["employee"])
    told = list(Notification.objects.values_list("recipient__username", "title"))
    assert told == [(cast["hod"].username, "Task accepted")]


# ---------------------------------------------------------------------------
# 4. Shared-task performance
# ---------------------------------------------------------------------------
def _queries(client, url):
    with CaptureQueriesContext(connection) as captured:
        response = client.get(url)
    assert response.status_code == 200, response.data
    return captured.captured_queries


def test_the_detail_page_does_not_ask_who_the_assignees_are_twelve_times(
        trio, cast, auth):
    """
    `capabilities()` calls a dozen predicates and nearly all of them ask "is
    this person an assignee?". As a query that was eleven round trips per page,
    against rows the same response had already prefetched to draw the chips.
    """
    rows = [q for q in _queries(auth(cast["employee"]), f"{LIST}{trio.id}/")
            if "taskassignee" in q["sql"].lower()]
    assert len(rows) <= 2, [q["sql"][:90] for q in rows]


def test_a_shared_task_costs_no_more_to_render_than_a_solo_one(
        trio, cast, auth, make_task):
    solo = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.ASSIGNED)
    one = len(_queries(auth(cast["employee"]), f"{LIST}{solo.id}/"))
    three = len(_queries(auth(cast["employee"]), f"{LIST}{trio.id}/"))
    assert three <= one, (
        f"{one} queries for one assignee but {three} for three — something in "
        "the detail payload is per-assignee")


def test_the_list_cost_does_not_move_with_the_number_of_shared_rows(
        cast, auth, make_task):
    """
    The acceptance tally on every card reads the prefetch. A tally that queried
    would turn a fifty-row board into fifty extra round trips.
    """
    people = [cast["employee"], cast["peer"], cast["hr"]]
    for _ in range(3):
        make_task(cast["hod"], people, reviewer=cast["hod"], status=Status.ASSIGNED)
    small = len(_queries(auth(cast["hod"]), LIST))
    for _ in range(12):
        make_task(cast["hod"], people, reviewer=cast["hod"], status=Status.ASSIGNED)
    large = len(_queries(auth(cast["hod"]), LIST))
    assert small == large, f"{small} queries for 3 rows but {large} for 15"
