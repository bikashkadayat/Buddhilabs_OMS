"""
Notification delivery for task events (Phase T4.3).

THIS IS THE PHASE THE T3 SEAM WAS BUILT FOR
-------------------------------------------
Phase T3 declared six signals and connected nothing, and its own docstring said:
"When a delivery layer is built, the choice of which of the two paths to retire
is a decision for that phase, taken deliberately." This is that decision.

There is now ONE delivery path. The direct `notify_user` calls that Phases T1 and
T2 made from inside `tasks.workflow` have been moved here, unchanged in what they
send — same categories, same recipients, same wording. The alternative was to
keep both, and two paths that each send half the notifications is how a category
ends up delivered twice for one event and not at all for another.

The workflow module now does one thing per transition: change the state and
record it. What anybody is TOLD about that is this file's problem, which is why
a notification failure can no longer affect a transition at all.

WHY RECEIVERS AND NOT A SERVICE CALL
------------------------------------
A direct call would have been simpler and would have kept the seam decorative.
The point of the seam is that a second listener — a digest, a Slack bridge, an
export — can be added without touching the workflow engine. Signals make that
free; a hard-coded call does not.

CONNECTED IN AppConfig.ready(), ONCE
------------------------------------
Django imports `ready()` exactly once per process, so the receivers cannot be
double-connected — which would double every notification. `dispatch_uid` on each
connection makes that guarantee explicit rather than relying on it.
"""
import logging

from notifications.models import Category

from . import events
from .services import notify_user, user_snapshot

logger = logging.getLogger("tasks")


def _label(task):
    return f"{task.task_number} — {task.title}"


def _assignees(task):
    return [row.user for row in task.assignees.all() if row.user]


def _owner(task):
    """Who is answerable for the task: its reviewer, else whoever raised it."""
    return task.reviewer or task.created_by


def _recipients_with_reviewer(task):
    """
    Everybody doing the work, plus whoever was chosen to review it.

    De-duplicated by id: the same person appearing twice would send the same
    notification twice, and the idempotency key would only hide half of it.
    """
    people = {user.pk: user for user in _assignees(task) if user is not None}
    if task.reviewer_id and task.reviewer is not None:
        people.setdefault(task.reviewer.pk, task.reviewer)
    return list(people.values())


def _tell(user, category, title, body, task, *, exclude=None, key=None):
    """Send to one person, never to the actor who caused the event."""
    if user is None:
        return None
    if exclude is not None and user.pk == getattr(exclude, "pk", None):
        return None
    return notify_user(user, category, title, body, task, idempotency_key=key)


# ---------------------------------------------------------------------------
# The events
# ---------------------------------------------------------------------------
def on_task_created(sender, task, actor=None, **kwargs):
    """
    TASK_CREATED - the person accountable for the work now knows it exists.

    Sent to the reviewer (the Department Head for a Department task), and never
    to the person who just raised it: they know. Personal tasks never reach here
    at all - see workflow.announce_creation.
    """
    raiser = getattr(actor, "get_full_name", lambda: "")() or "Somebody"
    _tell(_owner(task), Category.TASK_CREATED, "New task raised",
          f"{raiser} raised {_label(task)}"
          + (f", due {task.due_date}." if task.due_date else "."), task,
          exclude=actor, key=f"task-{task.pk}-created")


def on_task_assigned(sender, task, actor=None, assignees=None, **kwargs):
    """TASK_ASSIGNED — everybody the task was handed to."""
    due = f", due {task.due_date}." if task.due_date else "."
    for user in (assignees if assignees is not None else _assignees(task)):
        _tell(user, Category.TASK_ASSIGNED, "Task assigned to you",
              f"{_label(task)} has been assigned to you{due}", task,
              exclude=actor, key=f"task-{task.pk}-assigned-{user.pk}")


def on_task_review_required(sender, task, actor=None, reviewer=None, **kwargs):
    """TASK_REVIEW_REQUIRED — the reviewer is now the one blocking."""
    target = reviewer or _owner(task)
    # Keyed on the submission time, so a resubmission after rework notifies
    # again — it is a new thing to review — while a retry of the same submission
    # does not.
    stamp = task.submitted_at.strftime("%Y%m%d%H%M%S") if task.submitted_at else "now"
    _tell(target, Category.TASK_REVIEW_REQUIRED, "Task ready for review",
          f"{_label(task)} has been submitted for your review.", task,
          exclude=actor, key=f"task-{task.pk}-review-{stamp}")


def on_task_completed(sender, task, actor=None, closed=False, **kwargs):
    """
    TASK_COMPLETED — fired twice in a task's life, and deliberately worded
    differently each time: approval is "somebody accepted your work", closure is
    "it is now on the record". Collapsing them into one message would leave the
    assignee unable to tell whether anything is still expected of them.
    """
    if closed:
        category, title = Category.TASK_CLOSED, "Task closed"
        body = f"{_label(task)} has been verified and closed."
    else:
        category, title = Category.TASK_APPROVED, "Task approved"
        body = f"{_label(task)} was approved and is awaiting closure."
    # The assignees, and the REVIEWER the creator chose
    # (Phase TASK-REVIEWER-SELECTION). `_tell` never notifies the actor, so the
    # reviewer who just approved is not told about their own decision - but an
    # Admin override reaches them, which is the case where not knowing matters.
    for user in _recipients_with_reviewer(task):
        _tell(user, category, title, body, task, exclude=actor)


def on_task_returned(sender, task, actor=None, remarks="", **kwargs):
    """TASK_RETURNED — the reason travels with it; it is the whole message."""
    for user in _recipients_with_reviewer(task):
        _tell(user, Category.TASK_REWORK_REQUESTED, "Task needs rework",
              f"{_label(task)} was returned for rework: {remarks}", task,
              exclude=actor)


def on_task_blocked(sender, task, actor=None, reason="", **kwargs):
    """
    TASK_BLOCKED — goes to the OWNER, not the assignees.

    The assignee is usually the person who raised the block; telling them is
    noise. The person who needs to know is whoever can unblock it.
    """
    _tell(_owner(task), Category.TASK_BLOCKED, "Task blocked",
          f"{_label(task)} is blocked: {reason}", task, exclude=actor)


def on_task_overdue(sender, task, overdue_days=0, **kwargs):
    """
    TASK_OVERDUE — the assignees, once per day.

    The idempotency key carries the DAY rather than the day count: a task chased
    on the 3rd and again on the 4th is two notifications, but running the
    scheduler twice on the 3rd is one. The reminder engine also keeps its own
    log (TaskReminderLog); this key is the second line of defence, at the
    delivery layer, so a caller that bypasses the engine still cannot spam.
    """
    from django.utils import timezone

    today = timezone.localdate().isoformat()
    body = (f"{_label(task)} is {overdue_days} day"
            f"{'' if overdue_days == 1 else 's'} overdue.")
    for user in _assignees(task):
        _tell(user, Category.TASK_OVERDUE, "Task overdue", body, task,
              key=f"task-{task.pk}-overdue-{today}-{user.pk}")


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
_CONNECTIONS = (
    (events.task_created, on_task_created, "tasks.on_task_created"),
    (events.task_assigned, on_task_assigned, "tasks.on_task_assigned"),
    (events.task_review_required, on_task_review_required,
     "tasks.on_task_review_required"),
    (events.task_completed, on_task_completed, "tasks.on_task_completed"),
    (events.task_returned, on_task_returned, "tasks.on_task_returned"),
    (events.task_blocked, on_task_blocked, "tasks.on_task_blocked"),
    (events.task_overdue, on_task_overdue, "tasks.on_task_overdue"),
)


def connect():
    """
    Connect every receiver. Idempotent: `dispatch_uid` means a second call is a
    no-op rather than a second delivery of everything.
    """
    for signal, receiver, uid in _CONNECTIONS:
        signal.connect(receiver, dispatch_uid=uid)


def disconnect():
    """Used by tests that need the seam bare. Not called in production."""
    for signal, receiver, uid in _CONNECTIONS:
        signal.disconnect(receiver, dispatch_uid=uid)
