"""
The task state machine.

Every transition is exercised through the ENGINE, and every refusal is asserted
as a refusal rather than as an absence of change — a transition that silently
does nothing looks identical to one that is correctly blocked, right up until it
does something.
"""
import pytest
from rest_framework.exceptions import ValidationError

from tasks import workflow
from tasks.models import Task, TaskAuditLog

pytestmark = pytest.mark.django_db

Status = Task.Status


# ---------------------------------------------------------------------------
# The happy path, end to end
# ---------------------------------------------------------------------------
def test_the_full_ladder_from_draft_to_closed(cast, make_task):
    hod, employee, hr = cast["hod"], cast["employee"], cast["hr"]
    task = make_task(hod, [employee], reviewer=hod)
    assert task.status == Status.DRAFT

    workflow.assign(task, hod)
    assert task.status == Status.ASSIGNED and task.assigned_at is not None

    workflow.accept(task, employee)
    assert task.status == Status.ACCEPTED
    assert task.assignees.get(user=employee).accepted_at is not None

    workflow.start(task, employee)
    assert task.status == Status.IN_PROGRESS and task.started_at is not None

    workflow.update_progress(task, employee, 60, note="Half of it drafted.")
    task.refresh_from_db()
    assert task.progress_percent == 60

    workflow.submit_for_review(task, employee)
    assert task.status == Status.UNDER_REVIEW
    # Submitting is a claim that the work is done; the number must agree.
    assert task.progress_percent == 100

    workflow.approve_review(task, hod, remarks="Looks right.")
    assert task.status == Status.COMPLETED and task.completed_at is not None

    workflow.close(task, hr, remarks="Verified against the ledger.")
    assert task.status == Status.CLOSED and task.closed_at is not None


def test_every_step_leaves_a_timeline_row(cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.CLOSED)
    actions = list(task.audit_entries.values_list("action", flat=True))
    assert actions[0] == TaskAuditLog.Action.CREATED
    for expected in (TaskAuditLog.Action.ASSIGNED, TaskAuditLog.Action.ACCEPTED,
                     TaskAuditLog.Action.STARTED,
                     TaskAuditLog.Action.SUBMITTED_FOR_REVIEW,
                     TaskAuditLog.Action.REVIEW_APPROVED,
                     TaskAuditLog.Action.CLOSED):
        assert expected in actions, expected


def test_the_timeline_records_both_ends_of_each_transition(cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ACCEPTED)
    row = task.audit_entries.get(action=TaskAuditLog.Action.ACCEPTED)
    assert row.from_status == Status.ASSIGNED
    assert row.to_status == Status.ACCEPTED


def test_creation_is_recorded_once_however_often_it_is_called(cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]])
    workflow.record_creation(task, cast["hod"])
    workflow.record_creation(task, cast["hod"])
    assert task.audit_entries.filter(
        action=TaskAuditLog.Action.CREATED).count() == 1


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------
def test_a_task_with_no_assignees_cannot_be_assigned(cast, make_task):
    task = make_task(cast["hod"], [])
    with pytest.raises(ValidationError):
        workflow.assign(task, cast["hod"])
    task.refresh_from_db()
    assert task.status == Status.DRAFT


def test_only_an_assignee_can_accept(cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    with pytest.raises(ValidationError):
        workflow.accept(task, cast["peer"])
    task.refresh_from_db()
    assert task.status == Status.ASSIGNED


def test_accepting_twice_is_refused(cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ACCEPTED)
    with pytest.raises(ValidationError):
        workflow.accept(task, cast["employee"])


def test_a_task_cannot_skip_from_assigned_to_under_review(cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    with pytest.raises(ValidationError):
        workflow.submit_for_review(task, cast["employee"])


def test_only_a_task_under_review_can_be_approved(cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    with pytest.raises(ValidationError):
        workflow.approve_review(task, cast["hod"])


def test_only_a_completed_task_can_be_closed(cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.UNDER_REVIEW)
    with pytest.raises(ValidationError):
        workflow.close(task, cast["hr"])


def test_a_closed_task_is_immovable(cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.CLOSED)
    for call in (
        lambda: workflow.assign(task, cast["hod"]),
        lambda: workflow.accept(task, cast["employee"]),
        lambda: workflow.start(task, cast["employee"]),
        lambda: workflow.update_progress(task, cast["employee"], 50),
        lambda: workflow.submit_for_review(task, cast["employee"]),
        lambda: workflow.block(task, cast["employee"], "Because of something."),
        lambda: workflow.cancel(task, cast["hod"], "Changed our minds here."),
    ):
        with pytest.raises(ValidationError):
            call()
    task.refresh_from_db()
    assert task.status == Status.CLOSED


# ---------------------------------------------------------------------------
# Review decision
# ---------------------------------------------------------------------------
def test_rework_sends_the_task_back_to_in_progress(cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW)
    workflow.request_rework(task, cast["hod"], "The figures on page two are stale.")
    assert task.status == Status.IN_PROGRESS
    assert task.submitted_at is None
    # The work already done still exists; it is not reset to zero.
    assert task.progress_percent == 90


def test_a_reworked_task_can_be_resubmitted_and_approved(cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW)
    workflow.request_rework(task, cast["hod"], "The figures on page two are stale.")
    workflow.submit_for_review(task, cast["employee"])
    workflow.approve_review(task, cast["hod"])
    assert task.status == Status.COMPLETED


# ---------------------------------------------------------------------------
# Clarification — a recorded question, not a tenth status
# ---------------------------------------------------------------------------
def test_requesting_clarification_leaves_the_task_assigned(cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    workflow.request_clarification(task, cast["employee"],
                                   "Which quarter is this for?")
    task.refresh_from_db()
    assert task.status == Status.ASSIGNED
    assert task.clarification_note == "Which quarter is this for?"
    assert task.clarification_requested_at is not None


def test_accepting_clears_an_outstanding_question(cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    workflow.request_clarification(task, cast["employee"], "Which quarter?")
    workflow.accept(task, cast["employee"])
    task.refresh_from_db()
    assert task.clarification_note == ""
    assert task.clarification_requested_at is None


# ---------------------------------------------------------------------------
# Blocked and Cancelled
# ---------------------------------------------------------------------------
def test_blocking_records_the_reason_and_unblocking_clears_it(cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    workflow.block(task, cast["employee"], "Waiting on the auditor's figures.")
    assert task.status == Status.BLOCKED
    assert task.blocked_reason == "Waiting on the auditor's figures."

    workflow.start(task, cast["employee"])
    assert task.status == Status.IN_PROGRESS
    # A stale "blocked because X" on a running task is a lie the UI would render.
    assert task.blocked_reason == ""
    assert task.audit_entries.filter(
        action=TaskAuditLog.Action.UNBLOCKED).exists()


def test_progress_can_still_be_reported_while_blocked(cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.BLOCKED)
    workflow.update_progress(task, cast["employee"], 40)
    task.refresh_from_db()
    assert task.progress_percent == 40
    assert task.status == Status.BLOCKED


def test_a_task_can_be_cancelled_from_anywhere_live(cast, make_task):
    for status in (Status.DRAFT, Status.ASSIGNED, Status.ACCEPTED,
                   Status.IN_PROGRESS, Status.UNDER_REVIEW, Status.BLOCKED,
                   Status.COMPLETED):
        task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                         status=status)
        workflow.cancel(task, cast["hod"], "The programme was descoped.")
        assert task.status == Status.CANCELLED


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------
def test_reporting_progress_on_an_accepted_task_starts_it(cast, make_task):
    """Making the employee press Start first is bookkeeping for its own sake."""
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ACCEPTED)
    workflow.update_progress(task, cast["employee"], 25)
    task.refresh_from_db()
    assert task.status == Status.IN_PROGRESS
    assert task.started_at is not None


@pytest.mark.parametrize("percent", [-1, 101, None])
def test_progress_outside_zero_to_a_hundred_is_refused(cast, make_task, percent):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    with pytest.raises(ValidationError):
        workflow.update_progress(task, cast["employee"], percent)


# ---------------------------------------------------------------------------
# Multi-assignee
# ---------------------------------------------------------------------------
def test_the_task_waits_for_every_acceptance_and_stamps_are_per_person(
        cast, make_task):
    """
    Phase TASK-MULTI-ASSIGNEE-COLLABORATION. One person accepting is not the
    team agreeing: the task stays Assigned — read as Pending Acceptance — and
    only the person who said yes carries a stamp.
    """
    employee, peer = cast["employee"], cast["peer"]
    task = make_task(cast["hod"], [employee, peer], status=Status.ASSIGNED)

    workflow.accept(task, employee)
    task.refresh_from_db()
    assert task.status == Status.ASSIGNED
    assert task.workflow_label == "Pending Acceptance"
    assert task.assignees.get(user=employee).accepted_at is not None
    # The second person has not agreed to anything yet, and the row says so.
    assert task.assignees.get(user=peer).accepted_at is None

    workflow.accept(task, peer)
    task.refresh_from_db()
    assert task.status == Status.ACCEPTED
    assert task.workflow_label == "Ready to Start"
    assert task.accepted_at is not None


def test_the_first_listed_assignee_is_the_primary_one(cast, make_task):
    task = make_task(cast["hod"], [cast["employee"], cast["peer"]])
    assert task.assignees.get(user=cast["employee"]).is_primary is True
    assert task.assignees.get(user=cast["peer"]).is_primary is False


def test_reassignment_preserves_an_acceptance_that_was_not_the_point(
        cast, make_task):
    """
    Adding a second person to a task must not silently un-accept the first.
    Rebuilding the rows from scratch would have done exactly that.
    """
    employee, peer = cast["employee"], cast["peer"]
    task = make_task(cast["hod"], [employee], status=Status.ACCEPTED)
    accepted_at = task.assignees.get(user=employee).accepted_at

    workflow.set_assignees(task, [employee, peer], cast["hod"])
    assert task.assignees.get(user=employee).accepted_at == accepted_at
    assert task.assignees.count() == 2


def test_the_same_person_cannot_be_assigned_twice(cast, make_task):
    task = make_task(cast["hod"], [])
    with pytest.raises(ValidationError):
        workflow.set_assignees(task, [cast["employee"], cast["employee"]],
                               cast["hod"])


def test_assignees_cannot_be_swapped_once_the_work_is_under_review(
        cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW)
    with pytest.raises(ValidationError):
        workflow.set_assignees(task, [cast["peer"]], cast["hod"])


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
def test_assigning_notifies_every_assignee(cast, make_task):
    from notifications.models import Category, Notification

    employee, peer = cast["employee"], cast["peer"]
    make_task(cast["hod"], [employee, peer], status=Status.ASSIGNED)
    recipients = set(Notification.objects.filter(
        category=Category.TASK_ASSIGNED).values_list("recipient_id", flat=True))
    assert recipients == {employee.id, peer.id}


def test_submitting_for_review_notifies_the_reviewer_not_the_assignee(
        cast, make_task):
    from notifications.models import Category, Notification

    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW)
    rows = Notification.objects.filter(category=Category.TASK_REVIEW_REQUIRED)
    assert [r.recipient_id for r in rows] == [cast["hod"].id]
    assert str(task.pk) in rows.first().action_url


# ---------------------------------------------------------------------------
# The global audit trail
# ---------------------------------------------------------------------------
def test_the_global_audit_row_uses_the_shared_vocabulary(cast, make_task):
    """
    `audit.AuditLog.action` is an eight-verb enum in a varchar(20) shared by
    every app. A task's own eighteen actions must be MAPPED onto it, not written
    into it: "task.assignees_changed" is both too long for the column and
    outside the choice list.

    SQLite does not enforce varchar length, so this was green locally and failed
    on PostgreSQL — which is what CI and production run. Hence a test that
    asserts the VALUES rather than relying on the database to reject them.
    """
    from audit.models import AuditLog

    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.CLOSED)
    rows = AuditLog.objects.filter(object_id=str(task.pk))
    assert rows.exists()

    allowed = {value for value, _ in AuditLog.Action.choices}
    for row in rows:
        assert row.action in allowed, row.action
        assert len(row.action) <= 20, row.action


def test_the_precise_transition_survives_in_the_audit_metadata(cast, make_task):
    """
    Mapping onto eight verbs loses detail, so the exact action is kept in the
    `transition` key — the same convention the memo and circular modules use,
    and what the audit endpoint already reads.
    """
    from audit.models import AuditLog

    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.CLOSED)
    transitions = {row.changes.get("transition")
                   for row in AuditLog.objects.filter(object_id=str(task.pk))}
    assert {"created", "assigned", "accepted", "review_approved", "closed"} <= transitions


def test_every_task_action_maps_to_a_real_shared_verb():
    """A new action added to TaskAuditLog.Action must not fall through to junk."""
    from audit.models import AuditLog
    from tasks.services import _shared_action

    allowed = {value for value, _ in AuditLog.Action.choices}
    for action, _label in TaskAuditLog.Action.choices:
        assert _shared_action(action) in allowed, action
