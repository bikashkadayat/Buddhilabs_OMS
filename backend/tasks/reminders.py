"""
The reminder and escalation engines (Phase T4.4, T4.5).

WHAT THEY ARE
-------------
Two schedules over the same data, run by one management command:

  REMINDERS  chase whoever has to do the work — 7 days before the due date,
             3 days, 1 day, on the day, and every day it stays overdue. Plus a
             standing nudge to a reviewer sitting on submitted work.

  ESCALATION climbs when a reminder has not worked. Overdue -> reminder ->
             supervisor -> HR -> management visibility, each rung reached by
             passing a day threshold and each addressed to somebody NEW.

EVERY SEND IS RECORDED, AND THE RECORD IS THE LOCK
--------------------------------------------------
`TaskReminderLog` holds one row per (task, kind, day) under a unique constraint,
and every send goes through `_already_sent`/`_record`. So the scheduler can run
twice, be re-run by hand after a failure, or overlap itself, and nobody gets the
same reminder twice on the same day. This is the state the T3 seam deliberately
left to its receiver.

ESCALATION IS CONFIGURABLE, AND CONFIGURED IN ONE PLACE
-------------------------------------------------------
The rungs and their day thresholds come from settings (`TASK_ESCALATION_*`), not
from constants buried in a loop. An organisation that wants HR told after three
days rather than seven changes a number in the environment; nobody edits this
file, and nobody has to find every place a 7 was written down.

WHY IT NEVER ESCALATES PAST THE PEOPLE WHO CAN ACT
---------------------------------------------------
Each rung resolves its audience from the task itself — the reviewer or creator,
then the head of the task's department, then HR, then Admin. A rung with nobody
to tell is SKIPPED rather than falling through to everybody, because an
escalation that reaches an audience of the whole organisation is one that
teaches people to ignore escalations.
"""
import datetime
import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from notifications.models import Category

from .models import Task, TaskReminderLog
from .services import notify_user

logger = logging.getLogger("tasks")

Kind = TaskReminderLog.Kind

# Approach reminders: days-before -> kind. Read from settings so the cadence is
# configurable, with the specification's schedule as the default.
DEFAULT_APPROACH_DAYS = {7: Kind.DUE_7, 3: Kind.DUE_3, 1: Kind.DUE_1}

# Escalation rungs, in order. Each is (kind, setting name, default days overdue).
ESCALATION_LADDER = [
    (Kind.ESCALATED_SUPERVISOR, "TASK_ESCALATION_SUPERVISOR_DAYS", 3),
    (Kind.ESCALATED_HR, "TASK_ESCALATION_HR_DAYS", 7),
    (Kind.ESCALATED_MANAGEMENT, "TASK_ESCALATION_MANAGEMENT_DAYS", 14),
]

# How long submitted work may sit with a reviewer before they are nudged.
DEFAULT_REVIEW_PENDING_DAYS = 2


def _threshold(name, default):
    value = getattr(settings, name, default)
    try:
        return int(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        logger.warning("%s is not a number (%r); using %s.", name, value, default)
        return default


def _already_sent(task, kind, today):
    return TaskReminderLog.objects.filter(
        task=task, kind=kind, sent_on=today).exists()


def _record(task, kind, today, recipients):
    """
    Write the log row. Returns False if another run got there first.

    The unique constraint is the real lock, not the `_already_sent` check above:
    two schedulers running concurrently would both pass that check and only one
    can win the insert. Catching IntegrityError here is how the loser finds out,
    and it means "already sent" rather than an error.
    """
    try:
        with transaction.atomic():
            TaskReminderLog.objects.create(
                task=task, kind=kind, sent_on=today,
                recipients=[str(getattr(u, "pk", u)) for u in recipients])
        return True
    except IntegrityError:
        logger.debug("Reminder %s for %s already logged for %s.",
                     kind, task.pk, today)
        return False


def _assignees(task):
    return [row.user for row in task.assignees.all() if row.user]


def _owner(task):
    return task.reviewer or task.created_by


def _label(task):
    return f"{task.task_number} — {task.title}"


# ---------------------------------------------------------------------------
# Reminders (Part 4)
# ---------------------------------------------------------------------------
def _send(task, kind, today, recipients, category, title, body):
    """Record first, then notify — so a crash mid-send cannot re-send tomorrow's
    reminder today, and a duplicate run is stopped before anybody is told."""
    recipients = [user for user in recipients if user is not None]
    if not recipients:
        return False
    if _already_sent(task, kind, today):
        return False
    if not _record(task, kind, today, recipients):
        return False
    for user in recipients:
        notify_user(user, category, title, body, task,
                    idempotency_key=f"task-{task.pk}-{kind}-{today}-{user.pk}")
    return True


def run_due_reminders(today=None, queryset=None):
    """
    Chase approaching and passed due dates.

    One pass over the open, dated tasks: whichever rung today matches, at most
    one reminder per task per day. A task 7 days out gets DUE_7 and nothing
    else; the same task three days later gets DUE_3.
    """
    today = today or timezone.localdate()
    tasks = (queryset if queryset is not None else Task.objects.all()).filter(
        due_date__isnull=False,
        status__in=Task.OPEN_STATUSES,
        archived_at__isnull=True,
    ).prefetch_related("assignees__user")

    approach = getattr(settings, "TASK_REMINDER_DAYS", None) or DEFAULT_APPROACH_DAYS
    sent = 0
    for task in tasks:
        days_left = (task.due_date - today).days
        assignees = _assignees(task)

        if days_left in approach:
            kind = approach[days_left]
            body = (f"{_label(task)} is due in {days_left} day"
                    f"{'' if days_left == 1 else 's'}, on {task.due_date}.")
            sent += bool(_send(task, kind, today, assignees,
                               Category.TASK_DUE_REMINDER, "Task due soon", body))
        elif days_left == 0:
            sent += bool(_send(
                task, Kind.DUE_TODAY, today, assignees,
                Category.TASK_DUE_REMINDER, "Task due today",
                f"{_label(task)} is due today."))
        elif days_left < 0:
            overdue = -days_left
            sent += bool(_send(
                task, Kind.OVERDUE, today, assignees,
                Category.TASK_OVERDUE, "Task overdue",
                f"{_label(task)} is {overdue} day"
                f"{'' if overdue == 1 else 's'} overdue."))
    return sent


def run_subtask_reminders(today=None, queryset=None):
    """
    Chase overdue subtasks (Phase TASK-AUTOSAVE-AND-SUBTASKS).

    Addressed to the subtask's own assignee, or to everybody on the task when
    the piece is unassigned. One log row per task per day; the notification
    idempotency key is per subtask, per person, per day, so a task with three
    late pieces held by one person tells them three things once each.
    """
    today = today or timezone.localdate()
    tasks = (queryset if queryset is not None else Task.objects.all()).filter(
        status__in=Task.OPEN_STATUSES,
        archived_at__isnull=True,
        subtasks__is_done=False,
        subtasks__due_date__lt=today,
    ).distinct().prefetch_related("assignees__user", "subtasks__assignee")

    sent = 0
    for task in tasks:
        late = [row for row in task.subtasks.all()
                if not row.is_done and row.due_date and row.due_date < today]
        if not late:
            continue
        if _already_sent(task, Kind.SUBTASK_OVERDUE, today):
            continue
        recipients = set()
        plan = []
        for row in late:
            people = [row.assignee] if row.assignee else _assignees(task)
            for user in people:
                if user is not None:
                    recipients.add(user)
                    plan.append((user, row))
        if not plan:
            continue
        if not _record(task, Kind.SUBTASK_OVERDUE, today,
                       list(recipients) + [f"subtask:{row.pk}" for row in late]):
            continue
        for user, row in plan:
            overdue = (today - row.due_date).days
            notify_user(
                user, Category.TASK_SUBTASK_OVERDUE, "Subtask overdue",
                f"\"{row.title}\" on {_label(task)} is {overdue} day"
                f"{'' if overdue == 1 else 's'} overdue.",
                task,
                idempotency_key=f"subtask-overdue-{row.pk}-{today}-{user.pk}")
            sent += 1
    return sent


def run_review_reminders(today=None, queryset=None):
    """
    Nudge a reviewer sitting on submitted work.

    Measured from `submitted_at`, which is when it became THEIR problem — not
    from the task's due date, which may be months away or absent entirely. A
    review bottleneck is invisible on a due-date report, which is exactly why it
    gets its own reminder.
    """
    today = today or timezone.localdate()
    threshold = _threshold("TASK_REVIEW_PENDING_DAYS", DEFAULT_REVIEW_PENDING_DAYS)
    cutoff = timezone.now() - datetime.timedelta(days=threshold)

    tasks = (queryset if queryset is not None else Task.objects.all()).filter(
        status=Task.Status.UNDER_REVIEW,
        submitted_at__lte=cutoff,
        archived_at__isnull=True,
    ).select_related("reviewer", "created_by")

    sent = 0
    for task in tasks:
        waiting = (timezone.now() - task.submitted_at).days
        sent += bool(_send(
            task, Kind.REVIEW_PENDING, today, [_owner(task)],
            Category.TASK_REVIEW_REMINDER, "Review still pending",
            f"{_label(task)} has been waiting for your review for {waiting} day"
            f"{'' if waiting == 1 else 's'}."))
    return sent


# ---------------------------------------------------------------------------
# Escalation (Part 5)
# ---------------------------------------------------------------------------
def _department_head(task):
    """
    The head of the task's department, resolved through the app registry rather
    than by importing the leave module — the boundary this app keeps.
    """
    if not task.department_id:
        return None
    from django.apps import apps

    Department = apps.get_model("leaves", "Department")
    department = Department.objects.filter(pk=task.department_id).first()
    return department.head if department else None


def _role_holders(role):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    return list(User.objects.filter(role=role, is_active=True))


def escalation_audience(task, kind):
    """
    Who a given rung reaches. Empty means the rung is SKIPPED.

    Falling through to a wider audience when a rung has nobody would mean a task
    in a department with no head going straight to the whole management tier,
    which is how escalations stop being read.
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()
    if kind == Kind.ESCALATED_SUPERVISOR:
        # The head of the department, else whoever owns the task — a supervisor
        # alert with no supervisor is better addressed to the person who
        # assigned the work than to nobody.
        head = _department_head(task)
        return [head] if head else [user for user in [_owner(task)] if user]
    if kind == Kind.ESCALATED_HR:
        return _role_holders(User.Roles.APPROVER)
    if kind == Kind.ESCALATED_MANAGEMENT:
        return _role_holders(User.Roles.ADMIN)
    return []


def run_escalations(today=None, queryset=None):
    """
    Climb the ladder for tasks that have stayed overdue.

    A rung fires when the task is at least its threshold overdue AND that rung
    has not already fired today. Rungs are cumulative: a task 20 days overdue has
    already had supervisor and HR alerts on earlier days and now adds management,
    so the top rung does not silence the ones below it — visibility accumulates
    rather than moving.
    """
    today = today or timezone.localdate()
    tasks = (queryset if queryset is not None else Task.objects.all()).filter(
        due_date__lt=today,
        status__in=Task.OPEN_STATUSES,
        archived_at__isnull=True,
    ).select_related("department", "reviewer", "created_by").prefetch_related(
        "assignees__user")

    fired = 0
    for task in tasks:
        overdue = (today - task.due_date).days
        who = ", ".join(row.user_name for row in task.assignees.all()) or "nobody"
        for kind, setting, default in ESCALATION_LADDER:
            if overdue < _threshold(setting, default):
                continue
            audience = escalation_audience(task, kind)
            if not audience:
                logger.info("Escalation %s skipped for %s: nobody to tell.",
                            kind, task.task_number)
                continue
            body = (f"{_label(task)} is {overdue} days overdue. "
                    f"Assigned to {who}"
                    f"{f' in {task.department_name}' if task.department_name else ''}.")
            fired += bool(_send(task, kind, today, audience,
                                Category.TASK_ESCALATED,
                                f"Escalation: {task.task_number} overdue",
                                body))
    return fired


def run_all(today=None, queryset=None):
    """Everything the scheduler runs, in one call, with a per-engine tally."""
    return {
        "due_reminders": run_due_reminders(today=today, queryset=queryset),
        "review_reminders": run_review_reminders(today=today, queryset=queryset),
        "subtask_reminders": run_subtask_reminders(today=today, queryset=queryset),
        "escalations": run_escalations(today=today, queryset=queryset),
    }
