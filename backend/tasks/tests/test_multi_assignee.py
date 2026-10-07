"""
Shared work waits for everybody (Phase TASK-MULTI-ASSIGNEE-COLLABORATION).

A task given to three people used to move the moment ONE of them accepted, so
two could be recorded as "Not Accepted" while progress was being reported around
them. These tests hold the rule that replaced it, and — just as importantly —
hold the SINGLE-assignee path unchanged beside it: a solo task still starts
without an acknowledgement step, which is what Phase TASK-SIMPLIFICATION asked
for and what every task raised before this phase relies on.
"""
import pytest
from rest_framework.exceptions import ValidationError

from notifications.models import Category, Notification
from tasks import workflow
from tasks.models import Task
from .conftest import LIST

pytestmark = pytest.mark.django_db

Status = Task.Status


@pytest.fixture
def trio(cast, make_task):
    """One task, three people, nobody has accepted yet."""
    return make_task(cast["hod"], [cast["employee"], cast["peer"], cast["hr"]],
                     reviewer=cast["hod"], status=Status.ASSIGNED)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
def test_the_task_stays_pending_until_the_last_person_accepts(trio, cast):
    workflow.accept(trio, cast["employee"])
    workflow.accept(trio, cast["peer"])
    trio.refresh_from_db()

    assert trio.status == Status.ASSIGNED
    assert trio.workflow_label == "Pending Acceptance"
    assert trio.accepted_count == 2
    assert trio.pending_assignee_names == [cast["hr"].get_full_name()]

    workflow.accept(trio, cast["hr"])
    trio.refresh_from_db()
    assert trio.status == Status.ACCEPTED
    assert trio.workflow_label == "Ready to Start"
    assert trio.all_assignees_accepted is True


@pytest.mark.parametrize("call", ["start", "progress", "submit"])
def test_nothing_moves_while_one_person_has_not_accepted(trio, cast, call):
    workflow.accept(trio, cast["employee"])
    workflow.accept(trio, cast["peer"])
    actions = {
        "start": lambda: workflow.start(trio, cast["employee"]),
        "progress": lambda: workflow.update_progress(trio, cast["employee"], 50),
        "submit": lambda: workflow.submit_for_review(trio, cast["employee"]),
    }
    with pytest.raises(ValidationError) as exc:
        actions[call]()
    # The refusal NAMES who is still outstanding — "not accepted" on its own
    # leaves somebody waiting on nothing in particular.
    assert cast["hr"].get_full_name() in str(exc.value)
    trio.refresh_from_db()
    assert trio.status == Status.ASSIGNED


def test_work_runs_normally_once_everybody_has_accepted(trio, cast):
    for person in (cast["employee"], cast["peer"], cast["hr"]):
        workflow.accept(trio, person)
    workflow.start(trio, cast["employee"])
    trio.refresh_from_db()
    assert trio.status == Status.IN_PROGRESS


# ---------------------------------------------------------------------------
# The single-assignee path is untouched
# ---------------------------------------------------------------------------
def test_a_solo_task_still_starts_without_accepting_first(cast, make_task):
    """Phase TASK-SIMPLIFICATION removed the acknowledgement step, and this
    phase re-imposes it only on shared work."""
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    workflow.start(task, cast["employee"])
    task.refresh_from_db()
    assert task.status == Status.IN_PROGRESS
    # Starting IS the acknowledgement, and the person's own row says so — an
    # assignee left "not accepted" for the life of the task would be counted as
    # outstanding by every tally on the page.
    assert task.assignees.get(user=cast["employee"]).accepted_at is not None


def test_a_solo_task_is_not_labelled_pending_once_it_is_accepted(cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ACCEPTED)
    assert task.needs_unanimous_acceptance is False
    assert task.work_may_start is True


# ---------------------------------------------------------------------------
# Progress: per person, and the average of it
# ---------------------------------------------------------------------------
def test_overall_progress_is_the_mean_of_what_each_person_reports(trio, cast):
    for person in (cast["employee"], cast["peer"], cast["hr"]):
        workflow.accept(trio, person)
    workflow.update_progress(trio, cast["employee"], 100)
    workflow.update_progress(trio, cast["peer"], 50)
    workflow.update_progress(trio, cast["hr"], 20)

    trio.refresh_from_db()
    assert trio.progress_percent == 57            # (100 + 50 + 20) / 3
    assert trio.assignees.get(user=cast["peer"]).progress_percent == 50


def test_somebody_who_has_not_reported_is_not_counted_as_zero(trio, cast):
    """
    Silence is not a claim of 0%. Averaging it as one would drag a shared task's
    figure down for every person who simply has not got to it yet.
    """
    for person in (cast["employee"], cast["peer"], cast["hr"]):
        workflow.accept(trio, person)
    workflow.update_progress(trio, cast["employee"], 60)

    trio.refresh_from_db()
    assert trio.progress_percent == 60
    assert trio.assignees.get(user=cast["peer"]).progress_percent is None


def test_a_task_from_before_this_phase_keeps_its_figure(cast, make_task):
    """
    Backward compatibility: rows raised before per-person progress existed have
    no per-person figures at all, and their overall number must not be reset to
    zero the first time anything touches them.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    task.progress_percent = 75
    task.save(update_fields=["progress_percent"])
    task.assignees.update(progress_percent=None)

    from tasks.services import overall_progress
    assert overall_progress(task) is None


# ---------------------------------------------------------------------------
# Who may do what, over the API
# ---------------------------------------------------------------------------
def test_an_assignee_may_edit_the_details_of_a_task_they_did_not_raise(
        cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.ASSIGNED)
    res = auth(cast["employee"]).patch(f"{LIST}{task.id}/", {
        "title": "Rack the new switch and label the ports",
        "priority": "high"}, format="json")
    assert res.status_code == 200, res.data
    task.refresh_from_db()
    assert task.priority == "high"
    # The timeline names what moved, not just that something did.
    row = task.audit_entries.filter(action="updated").last()
    assert "title" in row.remarks and "priority" in row.remarks


def test_an_assignee_may_not_change_the_reviewer(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.ASSIGNED)
    res = auth(cast["employee"]).patch(f"{LIST}{task.id}/", {
        "reviewer": str(cast["hr"].id)}, format="json")
    assert res.status_code == 403, res.data
    task.refresh_from_db()
    assert task.reviewer_id == cast["hod"].id


def test_an_assignee_may_not_change_who_else_is_on_the_task(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.ASSIGNED)
    res = auth(cast["employee"]).patch(f"{LIST}{task.id}/", {
        "assignee_ids": [str(cast["employee"].id), str(cast["peer"].id)]},
        format="json")
    assert res.status_code == 403
    assert task.assignees.count() == 1


def test_an_assignee_may_not_delete_a_task(cast, auth, make_task):
    """Deletion is the creator's, and only while the task is an unassigned
    draft. Nothing in this phase changed that."""
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"])
    assert auth(cast["employee"]).delete(f"{LIST}{task.id}/").status_code == 403
    assert Task.objects.filter(pk=task.pk).exists()


def test_the_page_offers_no_WORK_to_an_assignee_while_acceptance_is_pending(
        trio, cast, auth):
    workflow.accept(trio, cast["employee"])
    cap = auth(cast["employee"]).get(f"{LIST}{trio.id}/").data["capabilities"]
    assert cap["can_update_progress"] is False
    assert cap["can_upload"] is False
    assert cap["can_submit_for_review"] is False
    # ...but the task is still editable, still acceptable by the others, and
    # still somewhere the three of them can talk
    # (Phase TASK-COLLABORATION-HARDENING).
    assert cap["can_edit"] is True
    assert cap["can_comment"] is True


def test_the_owner_can_still_answer_a_question_while_acceptance_is_pending(
        trio, cast, auth):
    """
    Somebody has to be able to ANSWER a clarification request. Silencing the
    creator until the last assignee accepts would leave the question with
    nowhere to go but email.
    """
    res = auth(cast["hod"]).post(f"{LIST}{trio.id}/comments/",
                                 {"body": "Yes, it is for Q3."}, format="json")
    assert res.status_code == 201, res.data


def test_the_acceptance_tally_is_on_the_payload(trio, cast, auth):
    workflow.accept(trio, cast["employee"])
    body = auth(cast["hod"]).get(f"{LIST}{trio.id}/").data
    assert body["acceptance"]["accepted"] == 1
    assert body["acceptance"]["total"] == 3
    assert body["acceptance"]["is_pending"] is True
    assert body["workflow_label"] == "Pending Acceptance"
    # The stored vocabulary is untouched beside it: reports and filters are
    # built on `status` / `status_label`.
    assert body["status"] == "assigned"
    assert body["status_label"] == "Assigned"


# ---------------------------------------------------------------------------
# Reassignment
# ---------------------------------------------------------------------------
def test_adding_somebody_to_a_ready_task_sends_it_back_to_pending(
        cast, make_task):
    task = make_task(cast["hod"], [cast["employee"], cast["peer"]],
                     status=Status.ACCEPTED)
    workflow.set_assignees(task, [cast["employee"], cast["peer"], cast["hr"]],
                           cast["hod"])
    task.refresh_from_db()
    assert task.status == Status.ASSIGNED
    assert task.accepted_at is None


def test_adding_somebody_to_work_already_under_way_does_not_suspend_it(
        cast, make_task):
    """Un-starting a task that has genuinely begun would contradict a
    `started_at` that is already true."""
    task = make_task(cast["hod"], [cast["employee"], cast["peer"]],
                     status=Status.IN_PROGRESS)
    workflow.set_assignees(task, [cast["employee"], cast["peer"], cast["hr"]],
                           cast["hod"])
    task.refresh_from_db()
    assert task.status == Status.IN_PROGRESS


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
def _told(category):
    return set(Notification.objects.filter(category=category)
               .values_list("recipient__username", flat=True))


def test_everybody_is_told_when_the_last_person_accepts(trio, cast):
    for person in (cast["employee"], cast["peer"], cast["hr"]):
        workflow.accept(trio, person)
    ready = set(Notification.objects.filter(
        category=Category.TASK_ACCEPTED, title="Task ready to start")
        .values_list("recipient__username", flat=True))
    assert {cast["employee"].username, cast["peer"].username,
            cast["hr"].username} <= ready


def test_a_finished_part_reaches_the_others_but_not_the_person_who_finished(
        trio, cast):
    for person in (cast["employee"], cast["peer"], cast["hr"]):
        workflow.accept(trio, person)
    workflow.update_progress(trio, cast["employee"], 100)

    told = _told(Category.TASK_PROGRESS_UPDATED)
    assert cast["peer"].username in told and cast["hr"].username in told
    assert cast["employee"].username not in told


def test_progress_short_of_done_tells_nobody(trio, cast):
    """
    Phase TASK-COLLABORATION-HARDENING. Three people stepping 25 / 50 / 75 / 100
    sent twenty-four notifications that asked nobody for anything. The figures
    are on the task, live; only FINISHING is a thing the others can act on.
    """
    for person in (cast["employee"], cast["peer"], cast["hr"]):
        workflow.accept(trio, person)
    for percent in (25, 50, 75):
        workflow.update_progress(trio, cast["employee"], percent)
    assert _told(Category.TASK_PROGRESS_UPDATED) == set()


def test_a_partial_acceptance_tells_the_owner_and_nobody_else(trio, cast):
    """The other assignees can do nothing with "a colleague accepted", and it
    is already a green chip on the page."""
    workflow.accept(trio, cast["employee"])
    told = _told(Category.TASK_ACCEPTED)
    assert told == {cast["hod"].username}, told


def test_a_solo_task_sends_no_progress_notification(cast, make_task):
    """The rule the notification catalogue states: one category per point at
    which somebody has to ACT. On a solo task nobody but the reporter is
    affected, so nobody is told."""
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    workflow.update_progress(task, cast["employee"], 100)
    assert _told(Category.TASK_PROGRESS_UPDATED) == set()


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
def test_every_acceptance_leaves_its_own_timeline_row(trio, cast):
    for person in (cast["employee"], cast["peer"], cast["hr"]):
        workflow.accept(trio, person)
    rows = list(trio.audit_entries.filter(action="accepted"))
    assert len(rows) == 3
    assert [row.actor_id for row in rows] == [cast["employee"].id,
                                              cast["peer"].id, cast["hr"].id]
    # The last one is the one that moved the task.
    assert rows[-1].to_status == Status.ACCEPTED
    assert rows[0].to_status == Status.ASSIGNED
