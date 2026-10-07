"""
The task state machine.

    Draft ──assign──▶ Assigned ──accept──▶ Accepted ──start──▶ In Progress
                          │                                        │
                          └──request_clarification (stays here)    │
                                                                   ▼
                                              Under Review ◀──submit_for_review
                                                    │
                            ┌───────────────────────┴───────────────────┐
                    approve_review                              request_rework
                            │                                           │
                            ▼                                           ▼
                       Completed ──close (HR/HOD verification)──▶  In Progress
                            │
                            ▼
                          Closed

    Exceptions, reachable from the middle of the ladder: Blocked, Cancelled.

WHERE NOTIFICATIONS ARE SENT FROM (since Phase T4.3)
----------------------------------------------------
For the SIX EVENTS the specification names — assigned, review required,
completed, returned, blocked, overdue — this module emits the event and stops.
Who gets told, in what words, is tasks/receivers.py, connected in
AppConfig.ready(). Phases T1 and T2 sent those inline from here; T4 moved them
out so each has one delivery path and a notification failure cannot touch a
transition.

Three notifications are still sent DIRECTLY from here: acceptance, a
clarification request, and cancellation. None of them is one of the six, and
inventing three more events so that every notification could travel the same
road would be adding surface the specification did not ask for, to a seam whose
whole value is that it is small. They are marked at their call sites. If a later
phase names them as events, this is the shape the move takes.

TRANSITIONS ARE THE ONLY WAY STATUS CHANGES
-------------------------------------------
Every function here is atomic and writes its timeline row in the same
transaction as the state change, so a rolled-back transition cannot leave a
trail claiming it happened. Nothing outside this module assigns to `task.status`
— not the serializers, not the views, not the admin.

WHY "REQUEST CLARIFICATION" IS NOT A STATUS
-------------------------------------------
The workflow diagram routes "Needs Clarification" back to Assign Task rather
than into a state of its own, and that is right: the task is still assigned, the
employee simply has a question outstanding. Making it a status would mean every
list, filter, tile and permission check in the module had to learn a tenth state
that behaves identically to ASSIGNED. Instead the question is recorded on the
task, the creator is notified, and the task stays where the diagram leaves it.

WHY ACCEPTANCE IS PER-ASSIGNEE AND THE STATUS WAITS FOR ALL OF IT
-----------------------------------------------------------------
Each assignee accepts for themselves (TaskAssignee.accepted_at). A task given to
SEVERAL people stays Assigned — read as "Pending Acceptance" — until every one
of them has accepted, and only then becomes Accepted / "Ready to Start"
(Phase TASK-MULTI-ASSIGNEE-COLLABORATION).

It used to move on the FIRST acceptance, so two of three people could be
recorded as "Not Accepted" while the work was already being reported as done
around them. Shared work that one person can commit the others to is not shared
work, and the per-row stamps recorded the disagreement without preventing it.

A task with ONE assignee is unchanged. Phase TASK-SIMPLIFICATION took the
acknowledgement step out of the ladder, and `start` still fills the acceptance
stamp for a solo assignee who goes straight to work — see
Task.needs_unanimous_acceptance for why the gate is scoped to shared tasks.
"""
import datetime
import logging

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from notifications.models import Category

from . import events
from .models import Task, TaskAssignee, TaskAuditLog, TaskDependency
from .services import (
    blocking_dependencies, notify_user, record_assignee_progress, record_audit,
    resolve_department, subtask_tally, user_snapshot, would_cycle,
)
from tenancy.stamping import stamp_all

logger = logging.getLogger("tasks")

Action = TaskAuditLog.Action
Status = Task.Status


def _refuse(message):
    raise ValidationError({"workflow": message})


def _label(task):
    return f"{task.task_number} — {task.title}"


def _assignee_users(task):
    return [row.user for row in task.assignees.select_related("user") if row.user]


def _notify_assignees(task, category, title, body, *, exclude=None, key_suffix=""):
    """Tell everyone the task is assigned to, optionally skipping the actor."""
    for user in _assignee_users(task):
        if exclude is not None and user.pk == getattr(exclude, "pk", None):
            continue
        notify_user(
            user, category, title, body, task,
            idempotency_key=f"task-{task.pk}-{key_suffix}-{user.pk}" if key_suffix else None,
        )


def _owner(task):
    """Who is answerable for the task's definition: its reviewer, else its creator."""
    return task.reviewer or task.created_by


def _refuse_if_not_accepted(task, verb):
    """
    Stop shared work moving until everybody on it has accepted
    (Phase TASK-MULTI-ASSIGNEE-COLLABORATION).

    In the ENGINE, beside every other transition rule, for the same reason the
    dependency gate is: a rule only the button respected would be advice.

    The refusal NAMES who is still outstanding. "This task has not been
    accepted" leaves somebody waiting on nothing in particular; "waiting for
    Sanjaya Shrestha" tells them who to ask.
    """
    if task.work_may_start:
        return
    names = ", ".join(task.pending_assignee_names)
    _refuse(f"This task cannot be {verb} until every assignee has accepted it. "
            f"Still waiting for: {names}.")


def _refuse_if_open_subtasks(task, verb):
    """
    Phase TASK-AUTOSAVE-AND-SUBTASKS. An open subtask is unfinished work; the
    permission layer hides the button and this refuses the direct call, so the
    two cannot disagree.
    """
    done, total = subtask_tally(task)
    open_count = total - done
    if open_count:
        _refuse(f"This task cannot be {verb} while {open_count} subtask"
                f"{'' if open_count == 1 else 's'} {'is' if open_count == 1 else 'are'} "
                "still open.")


class ProgressDerived(Exception):
    """Raised by update_progress while the figure is derived from subtasks."""


def _refuse_if_blocked(task, verb):
    """
    Stop a task moving forward while a prerequisite is unfinished
    (Phase TASK-GOVERNANCE-HARDENING).

    Applied in the ENGINE, beside every other transition rule, rather than in
    the view: a dependency that only the button respected would be advice, and
    the whole point of recording one is that it is not.

    The refusal NAMES the prerequisites, with their numbers and their current
    status. "This task is blocked" leaves somebody clicking the same button
    again; "waiting on NIFN-TSK-2083-0007 (In Progress)" tells them what to
    chase, and where to go and read about it.
    """
    blockers = blocking_dependencies(task)
    if not blockers:
        return
    named = ", ".join(
        f"{row.depends_on.task_number} ({row.depends_on.get_status_display()})"
        for row in blockers)
    _refuse(f"This task cannot be {verb} until the work it waits on is finished: "
            f"{named}.")


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------
def record_creation(task, actor, request=None):
    """
    Write the "Created" timeline row. Idempotent, so calling it twice cannot
    double the row — the timeline's first event is the one every later event is
    read against.
    """
    if task.audit_entries.filter(action=Action.CREATED).exists():
        return None
    return record_audit(task, actor, Action.CREATED,
                        remarks=f"Task created as {task.get_status_display()}.",
                        to_status=task.status, request=request)


def announce_creation(task, actor):
    """
    Tell whoever is accountable that this work now exists
    (Phase TASK-MANAGEMENT-ASANA-MODEL).

    Sent to whoever will review it. Not to the creator, who knows - see
    receivers._tell, which never notifies the actor who caused the event.
    """
    return events.emit(events.task_created, task, actor=actor)


# ---------------------------------------------------------------------------
# Assignees
# ---------------------------------------------------------------------------
@transaction.atomic
def set_assignees(task, users, actor, request=None):
    """
    Replace the assignee list.

    Refused once the task is terminal. Refused for a task already under review,
    too: swapping in a new assignee at that point would credit somebody with work
    they did not do, and the reviewer is looking at output the new person never
    produced.

    Acceptance stamps SURVIVE for anyone who is still on the list. Rebuilding the
    rows from scratch would silently un-accept people who were not the point of
    the change.
    """
    if task.status in Task.TERMINAL_STATUSES:
        _refuse("A closed or cancelled task cannot be reassigned.")
    if task.status in (Status.UNDER_REVIEW, Status.COMPLETED):
        _refuse("Reassign the task before it is submitted for review.")

    seen = []
    for index, user in enumerate(users, start=1):
        if user is None:
            _refuse(f"Row {index}: the selected employee does not exist.")
        if user.pk in [u.pk for u in seen]:
            _refuse(f"{user.get_full_name() or user.username} is listed more than once.")
        seen.append(user)

    existing = {row.user_id: row for row in task.assignees.all()}
    keep_ids = {u.pk for u in seen}
    task.assignees.exclude(user_id__in=keep_ids).delete()
    # Phase TASK-AUTOSAVE-AND-SUBTASKS: a subtask may only be held by somebody
    # on the task, so a person taken off the task is taken off their subtasks.
    task.subtasks.exclude(assignee_id__in=keep_ids).exclude(
        assignee__isnull=True).update(assignee=None, assignee_name="")

    for position, user in enumerate(seen):
        snapshot = user_snapshot(user)
        row = existing.get(user.pk)
        if row is None:
            TaskAssignee.objects.create(
                task=task, user=user, user_name=snapshot["name"],
                designation=snapshot["designation"],
                department_label=snapshot["department"],
                is_primary=(position == 0))
        else:
            row.is_primary = (position == 0)
            row.user_name = snapshot["name"]
            row.designation = snapshot["designation"]
            row.department_label = snapshot["department"]
            row.save(update_fields=["is_primary", "user_name", "designation",
                                    "department_label"])

    record_audit(task, actor, Action.ASSIGNEES_CHANGED,
                 remarks=", ".join(u.get_full_name() or u.username for u in seen)
                         or "All assignees removed.",
                 metadata={"count": len(seen)}, request=request)

    # A person added to a task that was READY TO START has not accepted it, so
    # it is not ready any more: it goes back to Pending Acceptance and waits for
    # them (Phase TASK-MULTI-ASSIGNEE-COLLABORATION).
    #
    # Only from ACCEPTED. A task already IN PROGRESS is left where it is —
    # work that is genuinely under way must not be suspended because somebody
    # was added to help with it, and un-starting it would contradict a
    # `started_at` that is already true.
    if (task.status == Status.ACCEPTED
            and task.assignees.filter(accepted_at__isnull=True).exists()):
        task.status = Status.ASSIGNED
        task.accepted_at = None
        task.save(update_fields=["status", "accepted_at", "updated_at"])
        record_audit(task, actor, Action.ASSIGNED, from_status=Status.ACCEPTED,
                     to_status=task.status,
                     remarks="Back to pending acceptance: a new assignee has "
                             "not accepted yet.", request=request)
    return task.assignees.all()


# ---------------------------------------------------------------------------
# Assign  (Draft -> Assigned)
# ---------------------------------------------------------------------------
@transaction.atomic
def assign(task, actor, request=None):
    """
    Hand the task to its assignees: the "Assign Task" step, and the point at
    which "Notification Sent" happens.

    Reachable from Draft, and also from Assigned — the diagram loops back to
    Assign Task after a clarification request, and re-assigning has to re-notify.
    """
    if task.status not in (Status.DRAFT, Status.ASSIGNED):
        _refuse("Only a draft or an already-assigned task can be assigned.")
    if not task.assignees.exists():
        _refuse("Choose at least one employee before assigning this task.")

    previous = task.status
    task.status = Status.ASSIGNED
    task.assigned_at = task.assigned_at or timezone.now()
    resolve_department(task)
    task.save(update_fields=["status", "assigned_at", "department", "department_name",
                             "updated_at"])

    record_audit(task, actor, Action.ASSIGNED, from_status=previous,
                 to_status=task.status,
                 remarks=f"Assigned to {task.assignees.count()} employee(s).",
                 request=request)
    # Phase T4.3: notification delivery moved to tasks/receivers.py, which is
    # connected to this signal. There is now ONE delivery path — see that
    # module. A receiver that fails cannot roll this transition back.
    events.emit(events.task_assigned, task, actor=actor,
                assignees=_assignee_users(task))
    return task


# ---------------------------------------------------------------------------
# Accept  (Assigned -> Accepted)   /   Request clarification (stays Assigned)
# ---------------------------------------------------------------------------
@transaction.atomic
def accept(task, actor, request=None):
    """
    One assignee accepts.

    The TASK moves to Accepted only once EVERY assignee has — see the module
    docstring. Until then it stays Assigned, which the API and the page read as
    "Pending Acceptance", and this person's stamp records that they are not the
    one being waited for.
    """
    if task.status != Status.ASSIGNED:
        _refuse("Only an assigned task can be accepted.")
    row = task.assignees.filter(user=actor).first()
    if row is None:
        _refuse("Only an assignee can accept this task.")
    if row.accepted_at:
        _refuse("You have already accepted this task.")

    row.accepted_at = timezone.now()
    row.save(update_fields=["accepted_at"])

    # A fresh count, not the prefetched rows: the stamp above was written a line
    # ago and a cached list would still show this person as outstanding.
    outstanding = list(task.assignees.filter(accepted_at__isnull=True)
                       .values_list("user_name", flat=True))
    previous = task.status
    actor_name = user_snapshot(actor)["name"]

    if outstanding:
        # Recorded, and nothing else moves. The remarks name who is still
        # missing so the timeline answers "why is this still pending?" on its
        # own, without the reader counting chips on the page above it.
        record_audit(task, actor, Action.ACCEPTED, from_status=previous,
                     to_status=previous,
                     remarks=f"Accepted by {actor_name}. Still waiting for: "
                             f"{', '.join(name or 'Unknown' for name in outstanding)}.",
                     metadata={"accepted_by": str(getattr(actor, "pk", "")),
                               "pending": len(outstanding)},
                     request=request)
        # THE OWNER, AND NOBODY ELSE (Phase TASK-COLLABORATION-HARDENING).
        # Telling the other assignees that a colleague has accepted asks
        # nothing of them, changes nothing they can do, and is already on the
        # page as a green chip. On a task of four it sent nine notifications
        # nobody could act on; the one that matters is "everybody has now
        # accepted", below.
        notify_user(_owner(task), Category.TASK_ACCEPTED, "Task accepted",
                    f"{actor_name} accepted {_label(task)}. Waiting for "
                    f"{len(outstanding)} more.", task)
        return task

    task.status = Status.ACCEPTED
    task.accepted_at = task.accepted_at or row.accepted_at
    # The question, if there was one, has been overtaken by the acceptance.
    # Cleared HERE and not on a partial acceptance: the question may have been
    # asked by somebody who has still not accepted, and clearing it on another
    # person's yes would erase a request nobody has answered.
    task.clarification_note = ""
    task.clarification_requested_at = None
    task.save(update_fields=["status", "accepted_at", "clarification_note",
                             "clarification_requested_at", "updated_at"])

    record_audit(task, actor, Action.ACCEPTED, from_status=previous,
                 to_status=task.status,
                 remarks="Task accepted." if task.assignee_count == 1
                         else f"Accepted by {actor_name}. All assignees have now "
                              "accepted - the task is ready to start.",
                 request=request)
    # Sent directly: not one of the six named events. See the module
    # docstring on why this is not routed through the seam.
    if task.assignee_count > 1:
        # Everybody is now waiting on nobody, and the people doing the work are
        # the ones who need to hear it. Keyed so a retry cannot say it twice.
        #
        # ONE notification to the owner, not two
        # (Phase TASK-COLLABORATION-HARDENING): "Sanjaya accepted" followed a
        # second later by "everybody has accepted" is the same news twice, and
        # the second telling is the one worth keeping.
        _notify_assignees(
            task, Category.TASK_ACCEPTED, "Task ready to start",
            f"Everybody has accepted {_label(task)}. Work can begin.",
            key_suffix="all-accepted")
        notify_user(_owner(task), Category.TASK_ACCEPTED, "Task ready to start",
                    f"Every assignee has accepted {_label(task)}.", task,
                    idempotency_key=f"task-{task.pk}-all-accepted-owner")
    else:
        notify_user(_owner(task), Category.TASK_ACCEPTED, "Task accepted",
                    f"{actor_name} accepted {_label(task)}.", task)
    return task


@transaction.atomic
def request_clarification(task, actor, question, request=None):
    """
    An assignee asks for more information before accepting.

    The status does NOT change — see the module docstring. The question is
    recorded on the task, added to the discussion, and sent to whoever raised it.
    """
    if task.status != Status.ASSIGNED:
        _refuse("Clarification can only be requested on a task you have not yet accepted.")
    if not task.assignees.filter(user=actor).exists():
        _refuse("Only an assignee can request clarification on this task.")

    task.clarification_note = question
    task.clarification_requested_at = timezone.now()
    task.save(update_fields=["clarification_note", "clarification_requested_at",
                             "updated_at"])

    record_audit(task, actor, Action.CLARIFICATION_REQUESTED, remarks=question,
                 from_status=task.status, to_status=task.status, request=request)
    # Sent directly: not one of the six named events. See the module
    # docstring on why this is not routed through the seam.
    notify_user(_owner(task), Category.TASK_CLARIFICATION_REQUESTED,
                "Task clarification requested",
                f"{user_snapshot(actor)['name']} needs more information on "
                f"{_label(task)}: {question}", task)
    return task


# ---------------------------------------------------------------------------
# Dependencies  (Phase TASK-GOVERNANCE-HARDENING)
# ---------------------------------------------------------------------------
@transaction.atomic
def add_dependency(task, prerequisite, actor, *, kind=None, note="", request=None):
    """
    Record that `task` waits for `prerequisite`.

    Every refusal here is about a relationship that could not be true, and each
    one says which: itself, a duplicate, a ring, or a prerequisite that is
    already finished (which would be an edge that never does anything - better
    to say so than to accept it and leave somebody believing the task is guarded).
    """
    if task.pk == prerequisite.pk:
        _refuse("A task cannot wait for itself.")
    if TaskDependency.objects.filter(task=task, depends_on=prerequisite).exists():
        _refuse(f"This task already waits for {prerequisite.task_number}.")
    if prerequisite.status in (Status.COMPLETED, Status.CLOSED, Status.CANCELLED):
        _refuse(f"{prerequisite.task_number} is already "
                f"{prerequisite.get_status_display().lower()}, so waiting for it "
                "would hold nothing up.")
    if would_cycle(task, prerequisite):
        _refuse(f"{prerequisite.task_number} already waits for this task, directly "
                "or through another one. Two tasks cannot wait for each other.")

    row = TaskDependency.objects.create(
        task=task, depends_on=prerequisite,
        kind=kind or TaskDependency.Kind.BLOCKED_BY, note=note[:255],
        created_by=actor, created_by_name=user_snapshot(actor)["name"])
    record_audit(task, actor, Action.DEPENDENCY_ADDED,
                 remarks=f"{row.get_kind_display()} {prerequisite.task_number} "
                         f"- {prerequisite.title}",
                 metadata={"depends_on": str(prerequisite.pk),
                           "task_number": prerequisite.task_number,
                           "kind": row.kind},
                 request=request)
    return row


@transaction.atomic
def remove_dependency(row, actor, request=None):
    task, prerequisite = row.task, row.depends_on
    record_audit(task, actor, Action.DEPENDENCY_REMOVED,
                 remarks=f"No longer waiting for {prerequisite.task_number}.",
                 metadata={"depends_on": str(prerequisite.pk),
                           "task_number": prerequisite.task_number},
                 request=request)
    row.delete()
    return task


# ---------------------------------------------------------------------------
# Work  (Accepted -> In Progress -> Under Review)
# ---------------------------------------------------------------------------
@transaction.atomic
def start(task, actor, request=None):
    """
    Begin work. Also the way back out of Blocked.

    ASSIGNED counts as a starting point (Phase TASK-SIMPLIFICATION). The
    workflow the phase specifies is Created -> In Progress -> Submitted ->
    Approved -> Completed, with no acknowledgement step in the middle:
    `accept` still exists for anybody who wants to acknowledge first, but
    nobody has to pass through it to start work they have already begun.
    """
    if task.status not in (Status.ASSIGNED, Status.ACCEPTED, Status.BLOCKED):
        _refuse("Only an assigned, accepted or blocked task can be started.")
    # Shared work waits for everybody (Phase TASK-MULTI-ASSIGNEE-COLLABORATION).
    # A solo assignee still starts straight from Assigned, and the stamp below
    # records that starting was their acknowledgement.
    _refuse_if_not_accepted(task, "started")
    _refuse_if_blocked(task, "started")

    previous = task.status
    unblocking = previous == Status.BLOCKED
    task.status = Status.IN_PROGRESS
    # Starting straight from Assigned is also an acknowledgement, so the stamp
    # is filled rather than left blank - an empty accepted_at beside a filled
    # started_at would read as data loss on the timeline.
    task.accepted_at = task.accepted_at or timezone.now()
    task.started_at = task.started_at or timezone.now()
    task.blocked_reason = ""
    # The same acknowledgement, on the person's own row. Without it a solo task
    # started without accepting would show its one assignee as "not accepted"
    # for the rest of its life, and every acceptance tally would count it as
    # outstanding work that nobody is actually waiting for.
    task.assignees.filter(user=actor, accepted_at__isnull=True).update(
        accepted_at=task.accepted_at)
    task.save(update_fields=["status", "accepted_at", "started_at",
                             "blocked_reason", "updated_at"])

    record_audit(task, actor,
                 Action.UNBLOCKED if unblocking else Action.STARTED,
                 from_status=previous, to_status=task.status,
                 remarks="Work resumed." if unblocking else "Work started.",
                 request=request)
    return task


@transaction.atomic
def update_progress(task, actor, percent, note="", request=None):
    """
    Record progress. Allowed while the task is being worked on, and while it is
    blocked — an assignee may well have finished part of it before hitting the
    blocker, and the number should say so.
    """
    # The acceptance gate is checked FIRST, because its refusal is the more
    # useful one: "waiting for Sanjaya" says what to do about it, where "not
    # being worked on" leaves the reader to work out why not.
    _refuse_if_not_accepted(task, "worked on")
    if task.status not in (Status.ACCEPTED, Status.IN_PROGRESS, Status.BLOCKED):
        _refuse("Progress can only be updated on a task that is being worked on.")
    # Phase TASK-AUTOSAVE-AND-SUBTASKS: with subtasks the number is derived,
    # and a hand-typed figure would be overwritten by the next tick. The view
    # maps this to 409 so the client can tell "derived" from "not allowed".
    if subtask_tally(task)[1]:
        raise ProgressDerived(
            "Progress is derived from the subtasks. Complete a subtask to move it.")
    if percent is None or not 0 <= int(percent) <= 100:
        raise ValidationError({"progress_percent": "Give a percentage between 0 and 100."})

    previous_percent = task.progress_percent
    # Per-person first, then the task's own figure from it
    # (Phase TASK-MULTI-ASSIGNEE-COLLABORATION). On a solo task the mean of one
    # number is that number, so nothing about the single-assignee case changes.
    overall = record_assignee_progress(task, actor, percent)
    task.progress_percent = int(percent) if overall is None else overall
    # Phase T2.2: a hand-reported figure takes the task off automatic. The
    # checklist stops overwriting it from here on — see
    # tasks.services.recalculate_progress. Reporting progress by hand and then
    # having the next tick silently discard it is the failure this prevents.
    task.progress_is_auto = False
    # Recording progress on an accepted task is starting it; making the employee
    # press a separate button first is bookkeeping for its own sake.
    fields = ["progress_percent", "progress_is_auto", "updated_at"]
    if task.status == Status.ACCEPTED:
        task.status = Status.IN_PROGRESS
        task.started_at = task.started_at or timezone.now()
        fields += ["status", "started_at"]
    task.save(update_fields=fields)

    record_audit(task, actor, Action.PROGRESS_UPDATED, remarks=note,
                 metadata={"from": previous_percent, "to": task.progress_percent,
                           "reported": int(percent)},
                 request=request)
    if task.assignee_count > 1 and int(percent) == 100:
        # ONLY WHEN SOMEBODY'S PART IS DONE (Phase TASK-COLLABORATION-HARDENING).
        #
        # Three people each stepping 25 / 50 / 75 / 100 sent twenty-four
        # notifications, not one of which asked anybody for anything — the
        # catalogue's own rule is one category per point at which somebody has
        # to ACT, and "Prashanta is halfway" is not one. Finishing IS: it tells
        # the others the submission is in reach and makes whoever is left the
        # visible one. The figures themselves are on the task, live, for anyone
        # who wants them.
        #
        # Keyed on the person, so a correction back to 100 after a fix does not
        # announce the same finish twice.
        # A fresh count: the row above was written a moment ago, and the
        # prefetched list the view handed us predates it.
        done = task.assignees.filter(progress_percent=100).count()
        _notify_assignees(
            task, Category.TASK_PROGRESS_UPDATED, "A teammate has finished their part",
            f"{user_snapshot(actor)['name']} has finished their part of "
            f"{_label(task)} ({done} of {task.assignee_count} done). "
            f"Overall: {task.progress_percent}%.", exclude=actor,
            key_suffix=f"part-done-{getattr(actor, 'pk', '')}")
    return task


@transaction.atomic
def submit_for_review(task, actor, note="", request=None):
    """
    "Submit For Review" — the task goes to its reviewer.

    Progress is forced to 100: submitting work as finished while reporting it 60%
    done is a contradiction the reviewer would have to resolve by asking.
    """
    _refuse_if_blocked(task, "submitted for review")
    # Before the status check, for the same reason as in `update_progress`: on a
    # task still waiting to be accepted, "not in progress" is true but useless,
    # and "waiting for Sanjaya" is what the person can act on.
    _refuse_if_not_accepted(task, "submitted for review")
    if task.status not in (Status.IN_PROGRESS, Status.ACCEPTED):
        _refuse("Only a task in progress can be submitted for review.")
    _refuse_if_open_subtasks(task, "submitted for review")

    previous = task.status
    task.status = Status.UNDER_REVIEW
    task.submitted_at = timezone.now()
    task.progress_percent = 100
    task.save(update_fields=["status", "submitted_at", "progress_percent", "updated_at"])
    # Per-person figures follow the task's, for the same reason the task's is
    # forced: a breakdown reading 100 / 50 / 20 under an overall 100% is a
    # contradiction the reviewer would have to resolve by asking.
    task.assignees.update(progress_percent=100, progress_updated_at=timezone.now())

    record_audit(task, actor, Action.SUBMITTED_FOR_REVIEW, from_status=previous,
                 to_status=task.status, remarks=note or "Submitted for review.",
                 request=request)
    events.emit(events.task_review_required, task, actor=actor,
                reviewer=_owner(task))
    return task


# ---------------------------------------------------------------------------
# Review decision  (Under Review -> Completed | back to In Progress)
# ---------------------------------------------------------------------------
@transaction.atomic
def approve_review(task, actor, remarks="", request=None):
    """The reviewer accepts the work: the task is Completed, awaiting closure."""
    if task.status != Status.UNDER_REVIEW:
        _refuse("Only a task under review can be approved.")

    previous = task.status
    task.status = Status.COMPLETED
    task.completed_at = timezone.now()
    task.progress_percent = 100
    task.save(update_fields=["status", "completed_at", "progress_percent", "updated_at"])

    record_audit(task, actor, Action.REVIEW_APPROVED, from_status=previous,
                 to_status=task.status, remarks=remarks or "Approved.",
                 request=request)
    events.emit(events.task_completed, task, actor=actor, closed=False)
    return task


@transaction.atomic
def request_rework(task, actor, remarks, request=None):
    """
    "Need Rework" — the task goes back to the assignee, In Progress.

    A remark is mandatory: rework with no stated reason is a task the assignee
    has to guess at, and the guess is usually wrong.
    """
    if task.status != Status.UNDER_REVIEW:
        _refuse("Only a task under review can be sent back for rework.")

    previous = task.status
    task.status = Status.IN_PROGRESS
    task.submitted_at = None
    # Not reset to 0: the work already done still exists. The assignee moves it
    # back down themselves if the rework is larger than the number implies.
    task.progress_percent = min(task.progress_percent, 90)
    task.save(update_fields=["status", "submitted_at", "progress_percent", "updated_at"])

    record_audit(task, actor, Action.REWORK_REQUESTED, from_status=previous,
                 to_status=task.status, remarks=remarks, request=request)
    events.emit(events.task_returned, task, actor=actor, remarks=remarks)
    return task


# ---------------------------------------------------------------------------
# Closure  (Completed -> Closed)
# ---------------------------------------------------------------------------
@transaction.atomic
def close(task, actor, remarks="", request=None):
    """
    "HR / HOD Verification" — the terminal event. A closed task is permanent:
    every mutating permission returns False for it, and every transition here
    refuses it.
    """
    if task.status != Status.COMPLETED:
        _refuse("Only a completed task can be closed.")
    _refuse_if_open_subtasks(task, "closed")

    previous = task.status
    task.status = Status.CLOSED
    task.closed_at = timezone.now()
    task.save(update_fields=["status", "closed_at", "updated_at"])

    record_audit(task, actor, Action.CLOSED, from_status=previous,
                 to_status=task.status, remarks=remarks or "Verified and closed.",
                 request=request)
    events.emit(events.task_completed, task, actor=actor, closed=True)
    return task


# ---------------------------------------------------------------------------
# Exceptions  (Blocked, Cancelled)
# ---------------------------------------------------------------------------
@transaction.atomic
def block(task, actor, reason, request=None):
    """Work cannot continue. The reason is mandatory and is shown on every list."""
    if task.status not in (Status.ACCEPTED, Status.IN_PROGRESS):
        _refuse("Only a task being worked on can be blocked.")

    previous = task.status
    task.status = Status.BLOCKED
    task.blocked_reason = reason
    task.save(update_fields=["status", "blocked_reason", "updated_at"])

    record_audit(task, actor, Action.BLOCKED, from_status=previous,
                 to_status=task.status, remarks=reason, request=request)
    events.emit(events.task_blocked, task, actor=actor, reason=reason)
    return task


@transaction.atomic
def cancel(task, actor, reason, request=None):
    """
    Stop the task for good. Reachable from anywhere except the terminal states —
    the diagram routes Cancelled out of the assignment loop and out of review,
    and a task can also be abandoned as a draft.
    """
    if task.status in Task.TERMINAL_STATUSES:
        _refuse("This task is already closed or cancelled.")

    previous = task.status
    task.status = Status.CANCELLED
    task.cancelled_at = timezone.now()
    task.save(update_fields=["status", "cancelled_at", "updated_at"])

    record_audit(task, actor, Action.CANCELLED, from_status=previous,
                 to_status=task.status, remarks=reason, request=request)
    # Sent directly: not one of the six named events. See the module
    # docstring on why this is not routed through the seam.
    _notify_assignees(task, Category.TASK_CANCELLED, "Task cancelled",
                      f"{_label(task)} was cancelled: {reason}")
    return task


# ---------------------------------------------------------------------------
# Templates (Phase T2.9)
# ---------------------------------------------------------------------------
@transaction.atomic
def save_as_template(task, actor, *, name, description="", due_in_days=None,
                     department=None, request=None):
    """
    Freeze this task's shape — priority, description and checklist — as a
    reusable template.

    What is deliberately NOT copied: the assignees, the due date, the evidence,
    the comments and the status. A template that carried people would assign
    next year's onboarding to whoever did last year's, and one that carried a
    date would be stale the day after it was saved. `due_in_days` replaces the
    date because "two weeks after it starts" survives the calendar.
    """
    from .models import TaskTemplate, TaskTemplateGroup, TaskTemplateItem

    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": "Give the template a name."})
    if TaskTemplate.objects.filter(name__iexact=name).exists():
        raise ValidationError(
            {"name": f'A template called "{name}" already exists.'})

    snapshot = user_snapshot(actor)
    template = TaskTemplate.objects.create(
        name=name,
        description=description or task.description,
        title_template=task.title,
        priority=task.priority,
        default_due_in_days=due_in_days,
        department=department or task.department,
        department_name=(department.name if department else task.department_name),
        created_by=actor,
        created_by_name=snapshot["name"],
    )

    # Groups first, then items, so an item can point at the group it copies from.
    #
    # `organization` is taken from the TEMPLATE rather than from the tenant in
    # context (Phase S2). It is derived from the parent, so it is right even
    # when there is no request -- and bulk_create() below does not emit
    # pre_save, so the stamping receiver in tenancy.stamping never runs for it.
    group_map = {}
    for group in task.checklist_groups.all():
        group_map[group.id] = TaskTemplateGroup.objects.create(
            template=template, organization_id=template.organization_id,
            title=group.title, position=group.position)

    TaskTemplateItem.objects.bulk_create([
        TaskTemplateItem(
            template=template,
            organization_id=template.organization_id,
            group=group_map.get(item.group_id),
            text=item.text,
            position=item.position)
        for item in task.checklist.all()
    ])

    record_audit(task, actor, Action.TEMPLATE_SAVED,
                 remarks=f'Saved as the template "{template.name}".',
                 metadata={"template_id": str(template.id),
                           "items": template.items.count()},
                 request=request)
    return template


@transaction.atomic
def apply_template(task, template, actor, request=None):
    """
    Copy a template's checklist onto a task.

    A COPY, never a link — see TaskTemplate's docstring. Refused once the task
    is past the point where its definition may change, and refused on a task
    that already has a checklist: silently merging two lists produces
    duplicates, and silently replacing one discards ticks somebody has already
    made. The caller clears the checklist first if that is what they meant.
    """
    from .models import TaskChecklistGroup, TaskChecklistItem

    if task.status not in Task.EDITABLE_STATUSES:
        _refuse("A template can only be applied while the task is still editable.")
    if task.checklist.exists():
        _refuse("This task already has a checklist. Clear it before applying a "
                "template.")

    group_map = {}
    for group in template.groups.all():
        group_map[group.id] = TaskChecklistGroup.objects.create(
            task=task, title=group.title, position=group.position)

    items = [
        TaskChecklistItem(
            task=task,
            group=group_map.get(item.group_id),
            text=item.text,
            position=item.position)
        for item in template.items.all()
    ]
    TaskChecklistItem.objects.bulk_create(stamp_all(items))

    task.template = template
    task.save(update_fields=["template", "updated_at"])
    # `usage_count` is an F() expression rather than a read-modify-write: two
    # people raising the same template at once would otherwise each read the old
    # value and store the same new one, losing a use.
    from django.db.models import F

    type(template).objects.filter(pk=template.pk).update(
        usage_count=F("usage_count") + 1)

    record_audit(task, actor, Action.TEMPLATE_APPLIED,
                 remarks=f'Checklist copied from the template "{template.name}".',
                 metadata={"template_id": str(template.id), "items": len(items)},
                 request=request)
    return task


# ---------------------------------------------------------------------------
# Task groups (Phase T4.7)
# ---------------------------------------------------------------------------
@transaction.atomic
def create_task_group(template, actor, *, name="", due_date=None, assignees=None,
                      request=None):
    """
    Raise a batch of tasks from a template: ONE TASK PER CHECKLIST SECTION.

    A template with no sections raises a single task carrying the whole
    checklist, which is exactly what `apply_template` already does — so rather
    than refusing, the group of one is created and the caller gets a consistent
    shape back. A template with no lines at all IS refused: a batch of empty
    tasks is nobody's idea of a useful button.

    Every task is created as a DRAFT and then assigned through the ordinary
    transition, so each one gets its number, its audit row and its notification
    the same way a hand-raised task does. Nothing here writes `status`.
    """
    from .models import (
        TaskChecklistGroup, TaskChecklistItem, TaskGroup,
    )
    from .services import generate_task_number, resolve_department

    sections = list(template.groups.all())
    loose = [item for item in template.items.all() if item.group_id is None]
    if not sections and not loose:
        _refuse("That template has no checklist, so there is nothing to raise.")

    snapshot = user_snapshot(actor)
    group = TaskGroup.objects.create(
        name=(name or "").strip() or template.name,
        description=template.description,
        template=template,
        department=template.department,
        department_name=template.department_name,
        created_by=actor,
        created_by_name=snapshot["name"],
    )

    if due_date is None and template.default_due_in_days is not None:
        # The template's offset, applied from today. A template carries days
        # rather than a date precisely so this works whenever it is used.
        due_date = timezone.localdate() + datetime.timedelta(
            days=template.default_due_in_days)

    # One task per section; a template with only loose lines yields one task.
    plan = [(section.title, [i for i in template.items.all()
                             if i.group_id == section.id])
            for section in sections]
    if loose:
        plan.append((template.title_template or template.name, loose))

    created = []
    for title, items in plan:
        task = Task.objects.create(
            task_number=generate_task_number(),
            title=title[:255],
            description=template.description,
            priority=template.priority,
            due_date=due_date,
            department=template.department,
            department_name=template.department_name,
            created_by=actor,
            created_by_name=snapshot["name"],
            template=template,
            group=group,
        )
        record_creation(task, actor, request=request)
        if assignees:
            set_assignees(task, list(assignees), actor, request=request)
        resolve_department(task)
        task.save(update_fields=["department", "department_name"])

        TaskChecklistItem.objects.bulk_create(stamp_all([
            TaskChecklistItem(task=task, text=item.text, position=item.position)
            for item in items
        ]))
        record_audit(task, actor, Action.TEMPLATE_APPLIED,
                     remarks=f'Raised from "{template.name}" as part of '
                             f'"{group.name}".',
                     metadata={"template_id": str(template.id),
                               "group_id": str(group.id)},
                     request=request)
        if assignees:
            assign(task, actor, request=request)
        created.append(task)

    from django.db.models import F

    type(template).objects.filter(pk=template.pk).update(
        usage_count=F("usage_count") + len(created))
    return group, created


# ---------------------------------------------------------------------------
# Archiving (Phase T4.8)
# ---------------------------------------------------------------------------
@transaction.atomic
def archive(task, actor, request=None):
    """
    File a task out of the way. NOT the same as cancelling it.

    A cancelled task was stopped; an archived one ran its course. The distinction
    is load-bearing: every completion metric counts closed work, and folding
    archiving into cancellation would quietly delete finished work from the
    numbers. Only settled tasks can be archived — filing something still in
    flight would hide work somebody is waiting on.
    """
    if task.archived_at is not None:
        _refuse("That task is already archived.")
    if task.status not in (Status.CLOSED, Status.CANCELLED, Status.COMPLETED):
        _refuse("Only a completed, closed or cancelled task can be archived.")

    task.archived_at = timezone.now()
    task.save(update_fields=["archived_at", "updated_at"])
    record_audit(task, actor, Action.UPDATED, remarks="Task archived.",
                 metadata={"archived": True}, request=request)
    return task


@transaction.atomic
def unarchive(task, actor, request=None):
    """Bring a filed task back into the default lists."""
    if task.archived_at is None:
        _refuse("That task is not archived.")
    task.archived_at = None
    task.save(update_fields=["archived_at", "updated_at"])
    record_audit(task, actor, Action.UPDATED, remarks="Task restored from archive.",
                 metadata={"archived": False}, request=request)
    return task
