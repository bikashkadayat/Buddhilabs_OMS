"""
Phase T4.4 / T4.5 — the reminder and escalation engines.

The property everything here rests on: a reminder is sent AT MOST ONCE PER DAY,
however many times the scheduler runs. Everything else is cadence.
"""
import datetime

import pytest
from django.utils import timezone

from notifications.models import Category, Notification
from tasks import reminders
from tasks.models import Task, TaskReminderLog

pytestmark = pytest.mark.django_db

Status = Task.Status
Kind = TaskReminderLog.Kind


def run(today=None, **kwargs):
    return reminders.run_all(today=today, **kwargs)


def sent_kinds(task):
    return set(task.reminders.values_list("kind", flat=True))


def notices(user, category):
    return Notification.objects.filter(recipient=user, category=category).count()


# ---------------------------------------------------------------------------
# The cadence (Part 4)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("days_out,kind", [
    (7, Kind.DUE_7),
    (3, Kind.DUE_3),
    (1, Kind.DUE_1),
    (0, Kind.DUE_TODAY),
])
def test_each_rung_of_the_schedule_fires_on_its_day(cast, make_task, today,
                                                    days_out, kind):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=today + datetime.timedelta(days=days_out))
    reminders.run_due_reminders(today=today)
    assert sent_kinds(task) == {kind}


@pytest.mark.parametrize("days_out", [2, 4, 5, 6, 8, 30])
def test_nothing_fires_on_a_day_that_is_not_a_rung(cast, make_task, today,
                                                   days_out):
    """
    Otherwise the schedule is "every day", and a reminder that arrives every day
    is one nobody reads.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=today + datetime.timedelta(days=days_out))
    reminders.run_due_reminders(today=today)
    assert sent_kinds(task) == set()


def test_an_overdue_task_is_chased_every_day(cast, make_task, today):
    """
    Unlike the approach rungs. "Three days overdue" is a different fact from
    "one day overdue", and the chase should not stop because the date passed.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
                     due_date=today - datetime.timedelta(days=5))
    reminders.run_due_reminders(today=today)
    reminders.run_due_reminders(today=today + datetime.timedelta(days=1))

    rows = task.reminders.filter(kind=Kind.OVERDUE)
    assert rows.count() == 2
    assert rows.values_list("sent_on", flat=True).distinct().count() == 2


# ---------------------------------------------------------------------------
# Idempotency — the property that matters most
# ---------------------------------------------------------------------------
def test_running_twice_in_a_day_sends_nothing_twice(cast, make_task, today):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=today)
    first = reminders.run_due_reminders(today=today)
    second = reminders.run_due_reminders(today=today)

    assert (first, second) == (1, 0)
    assert task.reminders.count() == 1
    assert notices(cast["employee"], Category.TASK_DUE_REMINDER) == 1


def test_a_second_writer_losing_the_race_is_not_an_error(cast, make_task, today):
    """
    The unique constraint is the real lock, not the "already sent?" read: two
    schedulers would both pass that check and only one can win the insert.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=today)
    TaskReminderLog.objects.create(task=task, kind=Kind.DUE_TODAY, sent_on=today)
    # The row already exists; the engine must treat that as "done", not blow up.
    assert reminders.run_due_reminders(today=today) == 0


def test_the_log_records_who_was_told(cast, make_task, today):
    """The recipients of an escalation are the point of it, and resolving them
    again later would answer with today's org chart rather than the one that
    applied."""
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=today)
    reminders.run_due_reminders(today=today)
    row = task.reminders.get(kind=Kind.DUE_TODAY)
    assert row.recipients == [str(cast["employee"].id)]


# ---------------------------------------------------------------------------
# What is excluded
# ---------------------------------------------------------------------------
def test_a_finished_task_is_never_chased(cast, make_task, today):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.CLOSED,
                     due_date=today - datetime.timedelta(days=5))
    reminders.run_due_reminders(today=today)
    assert task.reminders.count() == 0


def test_an_archived_task_is_never_chased(cast, make_task, today):
    from tasks import workflow

    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=today)
    task.status = Status.CANCELLED
    task.save(update_fields=["status"])
    workflow.archive(task, cast["hod"])
    reminders.run_due_reminders(today=today)
    assert task.reminders.count() == 0


def test_a_task_with_no_due_date_is_never_chased(cast, make_task, today):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=None)
    reminders.run_due_reminders(today=today)
    assert task.reminders.count() == 0


def test_a_task_with_nobody_on_it_sends_nothing(cast, make_task, today):
    """A reminder with no recipient is not a reminder."""
    task = make_task(cast["hod"], [], status=Status.DRAFT, due_date=today)
    assert reminders.run_due_reminders(today=today) == 0
    assert task.reminders.count() == 0


# ---------------------------------------------------------------------------
# Review reminders
# ---------------------------------------------------------------------------
def test_a_reviewer_sitting_on_work_is_nudged(cast, make_task, today):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW)
    Task.objects.filter(pk=task.pk).update(
        submitted_at=timezone.now() - datetime.timedelta(days=4))

    assert reminders.run_review_reminders(today=today) == 1
    assert notices(cast["hod"], Category.TASK_REVIEW_REMINDER) == 1


def test_a_review_that_has_just_arrived_is_not_nudged(cast, make_task, today):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW)
    Task.objects.filter(pk=task.pk).update(submitted_at=timezone.now())
    assert reminders.run_review_reminders(today=today) == 0


def test_a_review_nudge_ignores_the_due_date(cast, make_task, today):
    """
    A task submitted three weeks ago against a deadline three months out IS a
    bottleneck. A due-date report would never show it, which is why review has
    its own reminder.
    """
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW,
                     due_date=today + datetime.timedelta(days=90))
    Task.objects.filter(pk=task.pk).update(
        submitted_at=timezone.now() - datetime.timedelta(days=21))
    assert reminders.run_review_reminders(today=today) == 1


# ---------------------------------------------------------------------------
# Escalation (Part 5)
# ---------------------------------------------------------------------------
def test_nothing_escalates_before_its_threshold(cast, make_task, today,
                                                departments):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
                     due_date=today - datetime.timedelta(days=1),
                     department=departments["engineering"])
    reminders.run_escalations(today=today)
    assert sent_kinds(task) == set()


def test_the_ladder_climbs_and_is_cumulative(cast, make_task, today,
                                             departments):
    """
    A task past the top threshold has had the lower rungs on earlier days and
    now adds the top one, so visibility ACCUMULATES rather than moving up and
    going quiet.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
                     due_date=today - datetime.timedelta(days=20),
                     department=departments["engineering"])
    reminders.run_escalations(today=today)
    assert sent_kinds(task) == {
        Kind.ESCALATED_SUPERVISOR, Kind.ESCALATED_HR, Kind.ESCALATED_MANAGEMENT}


@pytest.mark.parametrize("days,expected", [
    (3, {Kind.ESCALATED_SUPERVISOR}),
    (7, {Kind.ESCALATED_SUPERVISOR, Kind.ESCALATED_HR}),
])
def test_each_rung_needs_its_own_threshold(cast, make_task, today, departments,
                                           days, expected):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
                     due_date=today - datetime.timedelta(days=days),
                     department=departments["engineering"])
    reminders.run_escalations(today=today)
    assert sent_kinds(task) == expected


def test_the_supervisor_rung_reaches_the_department_head(cast, make_task, today,
                                                         departments):
    make_task(cast["hr"], [cast["employee"]], status=Status.IN_PROGRESS,
              due_date=today - datetime.timedelta(days=3),
              department=departments["engineering"])
    reminders.run_escalations(today=today)
    assert notices(cast["hod"], Category.TASK_ESCALATED) == 1


def test_the_hr_and_management_rungs_reach_their_roles(cast, make_task, today,
                                                       departments):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              due_date=today - datetime.timedelta(days=20),
              department=departments["engineering"])
    reminders.run_escalations(today=today)
    assert notices(cast["hr"], Category.TASK_ESCALATED) >= 1
    assert notices(cast["admin"], Category.TASK_ESCALATED) >= 1


def test_a_rung_with_nobody_to_tell_is_skipped_not_widened(cast, make_task,
                                                           today, departments):
    """
    Falling through to a wider audience would send a task in a headless
    department straight to the whole management tier, which is how escalations
    stop being read.
    """
    departments["engineering"].head = None
    departments["engineering"].save(update_fields=["head"])
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
                     due_date=today - datetime.timedelta(days=3),
                     department=departments["engineering"])
    reminders.run_escalations(today=today)

    # It falls back to the task's OWNER, not to everybody.
    row = task.reminders.get(kind=Kind.ESCALATED_SUPERVISOR)
    assert row.recipients == [str(cast["hod"].id)]
    assert notices(cast["admin"], Category.TASK_ESCALATED) == 0


def test_escalation_is_configurable(cast, make_task, today, departments,
                                    settings):
    """
    The ladder is settings, not constants in a loop: an organisation wanting HR
    told after one day changes a number in the environment.
    """
    settings.TASK_ESCALATION_HR_DAYS = 1
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
                     due_date=today - datetime.timedelta(days=1),
                     department=departments["engineering"])
    reminders.run_escalations(today=today)
    assert Kind.ESCALATED_HR in sent_kinds(task)


def test_escalations_do_not_repeat_within_a_day(cast, make_task, today,
                                                departments):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
                     due_date=today - datetime.timedelta(days=10),
                     department=departments["engineering"])
    first = reminders.run_escalations(today=today)
    second = reminders.run_escalations(today=today)
    assert second == 0 and first > 0


def test_a_finished_task_never_escalates(cast, make_task, today, departments):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.CLOSED,
                     due_date=today - datetime.timedelta(days=30),
                     department=departments["engineering"])
    reminders.run_escalations(today=today)
    assert task.reminders.count() == 0


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------
def test_the_command_runs_all_three_engines(cast, make_task, today):
    from django.core.management import call_command

    make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
              due_date=today)
    call_command("send_task_reminders", verbosity=0)
    assert TaskReminderLog.objects.filter(kind=Kind.DUE_TODAY).exists()


def test_the_command_can_run_one_engine(cast, make_task, today, departments):
    from django.core.management import call_command

    make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
              due_date=today)
    call_command("send_task_reminders", "--only", "escalation", verbosity=0)
    assert not TaskReminderLog.objects.filter(kind=Kind.DUE_TODAY).exists()


def test_the_command_can_be_run_for_a_past_date(cast, make_task, today):
    """For a backfill, and for reproducing what a given day would have sent."""
    from django.core.management import call_command

    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=today)
    yesterday = today - datetime.timedelta(days=1)
    call_command("send_task_reminders", "--date", yesterday.isoformat(),
                 verbosity=0)
    # As at yesterday the task was due in one day, not today.
    assert sent_kinds(task) == {Kind.DUE_1}


# ---------------------------------------------------------------------------
# Subtask overdue (Phase TASK-AUTOSAVE-AND-SUBTASKS)
# ---------------------------------------------------------------------------
def test_an_overdue_subtask_reminds_its_assignee_once_a_day(cast, make_task, today):
    import datetime as _dt

    from notifications.models import Category, Notification
    from tasks import reminders
    from tasks.models import Task, TaskSubtask

    task = make_task(cast["hod"], [cast["employee"], cast["peer"]],
                     status=Task.Status.IN_PROGRESS)
    TaskSubtask.objects.create(task=task, title="Late piece",
                               assignee=cast["peer"], assignee_name="Peer",
                               due_date=today - _dt.timedelta(days=2))
    TaskSubtask.objects.create(task=task, title="Unassigned late piece",
                               due_date=today - _dt.timedelta(days=1))

    assert reminders.run_subtask_reminders(today=today) == 3
    assert reminders.run_subtask_reminders(today=today) == 0
    rows = Notification.objects.filter(category=Category.TASK_SUBTASK_OVERDUE)
    assert rows.filter(recipient=cast["peer"]).count() == 2
    assert rows.filter(recipient=cast["employee"]).count() == 1
