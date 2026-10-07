"""
The appraisal evidence service (Phase T5.7, T5.8).

WHAT THIS IS FOR, AND WHAT IT MUST NOT BECOME
---------------------------------------------
A future appraisal module will need to know what somebody actually did. Gathering
that at appraisal time would mean either a second round of data collection or a
retrospective query against tasks that have since moved — so this layer produces
it as work happens, in a shape an appraisal process can consume without asking
anybody to fill in a form.

That is the whole purpose. It is NOT an appraisal, and the difference is
structural rather than a matter of tone:

  * There is no score, rating, band, grade or index. Every field is a count, a
    duration, or a percentage reported beside the two numbers it came from.
  * Nothing is weighted against anything else. Deciding that on-time delivery
    matters twice as much as volume is a judgement an organisation must make
    openly, with people who can be held to it — not a constant in a service
    module.
  * Nobody is compared to anybody. There is no cohort average here, no
    percentile, no "above/below expected". A comparison is an assessment wearing
    arithmetic.
  * It is READ-ONLY. Nothing here writes to a task, and nothing about a person's
    record changes because this ran.

If a later phase wants a score, it should build it somewhere it can be argued
with — and it must find these numbers unweighted, so the argument is about the
weighting rather than about what the numbers were.

WHY EVIDENCE IS SCOPED LIKE EVERYTHING ELSE
-------------------------------------------
An employee may read their OWN evidence and nobody else's; a department head may
read their department's; HR and Admin the organisation's. That is the same rule
the rest of the module follows, applied here because "how did they do" is the
most sensitive question this system can answer about a person.
"""
import datetime

from django.db.models import Count, Q
from django.utils import timezone

from evidence.schema import EvidenceSet, Metric, PeriodType, Source, Unit

from .models import EmployeeTaskEvidenceSnapshot, Task

DONE_STATUSES = [Task.Status.COMPLETED, Task.Status.CLOSED]

# The default window: a rolling year. Long enough that a quarter's appraisal has
# context, short enough that somebody's first month three years ago is not still
# being counted.
DEFAULT_WINDOW_DAYS = 365


def default_period(today=None):
    today = today or timezone.localdate()
    return today - datetime.timedelta(days=DEFAULT_WINDOW_DAYS), today


def build_evidence(queryset, user, period_start, period_end):
    """
    One employee's evidence over a window, computed live.

    `queryset` is already scoped, so this can only ever see tasks the CALLER may
    see — which means an employee's own evidence is complete, and a manager's
    view of somebody is bounded by what they were entitled to see anyway.

    Every percentage is returned with its numerator and denominator. A stored
    percentage with no denominator is the kind of number that gets quoted for
    years by somebody who never knew it was three tasks out of four.
    """
    mine = queryset.filter(
        assignees__user=user,
        created_at__date__gte=period_start,
        created_at__date__lte=period_end,
    ).exclude(status=Task.Status.DRAFT).distinct()

    assigned = mine.count()
    completed_qs = mine.filter(status__in=DONE_STATUSES)
    completed = completed_qs.count()

    with_due = completed_qs.filter(due_date__isnull=False,
                                   completed_at__isnull=False)
    on_time = sum(1 for task in with_due
                  if timezone.localtime(task.completed_at).date() <= task.due_date)
    with_due_count = with_due.count()

    # Average close time, from ASSIGNMENT — the clock the person is answerable
    # for. None, never 0.0, when nothing completed: "no data" and "same day" are
    # different answers.
    durations = [
        (task.completed_at - task.assigned_at).total_seconds() / 86400
        for task in completed_qs.filter(assigned_at__isnull=False,
                                        completed_at__isnull=False)
    ]
    average_days = round(sum(durations) / len(durations), 1) if durations else None

    # Review participation: decisions this person MADE, not decisions about them.
    reviews = queryset.filter(
        reviewer=user, status__in=DONE_STATUSES,
        completed_at__date__gte=period_start,
        completed_at__date__lte=period_end,
    ).distinct().count()

    checklist = mine.aggregate(
        total=Count("checklist", distinct=True),
        done=Count("checklist", distinct=True, filter=Q(checklist__is_done=True)))

    # Evidence FILES the person uploaded — the artefacts of the work, which is
    # what an appraisal conversation actually looks at.
    files = queryset.filter(
        attachments__uploaded_by=user,
        attachments__is_evidence=True,
        attachments__is_removed=False,
        attachments__uploaded_at__date__gte=period_start,
        attachments__uploaded_at__date__lte=period_end,
    ).aggregate(n=Count("attachments", distinct=True))["n"]

    comments = queryset.filter(
        comments__author=user,
        comments__created_at__date__gte=period_start,
        comments__created_at__date__lte=period_end,
    ).aggregate(n=Count("comments", distinct=True))["n"]

    return {
        "employee_id": str(user.pk),
        "employee_name": user.get_full_name() or user.username,
        "department": getattr(user, "department_name", "") or "",
        "period_start": period_start,
        "period_end": period_end,
        "tasks_assigned": assigned,
        "tasks_completed": completed,
        "tasks_open": mine.filter(status__in=Task.OPEN_STATUSES).count(),
        "tasks_overdue": mine.filter(status__in=Task.OPEN_STATUSES,
                                     due_date__lt=period_end).count(),
        "completion_percent": round(100 * completed / assigned) if assigned else 0,
        # Denominators travel with their percentages. Always.
        "completion_of": assigned,
        "completed_with_due_date": with_due_count,
        "completed_on_time": on_time,
        "on_time_percent": (round(100 * on_time / with_due_count)
                            if with_due_count else 0),
        "average_completion_days": average_days,
        "reviews_performed": reviews,
        "checklist_items_total": checklist["total"] or 0,
        "checklist_items_completed": checklist["done"] or 0,
        "evidence_files_submitted": files or 0,
        "comments_posted": comments or 0,
        # Said out loud in the payload, so a consumer cannot mistake this for a
        # verdict it can render as a grade.
        "disclaimer": (
            "Counts and durations only. This is evidence of activity, not an "
            "assessment: nothing here is scored, weighted or compared between "
            "people."
        ),
    }


def write_snapshot(user, evidence, snapshot_date=None, period_type="daily"):
    """
    Freeze one employee's evidence for one day.

    `get_or_create`, never update: a snapshot is what the data SAID that day, and
    a second run must not quietly produce a different version of the same day's
    truth. If a day's figures are wrong, the fix is a new day's snapshot and an
    explanation — not a rewritten history.
    """
    snapshot_date = snapshot_date or timezone.localdate()
    period_type = getattr(period_type, "value", period_type)
    snapshot, created = EmployeeTaskEvidenceSnapshot.objects.get_or_create(
        employee=user,
        snapshot_date=snapshot_date,
        period_type=period_type,
        defaults={
            "employee_name": evidence["employee_name"],
            "department_name": evidence["department"],
            "period_start": evidence["period_start"],
            "period_end": evidence["period_end"],
            "tasks_assigned": evidence["tasks_assigned"],
            "tasks_completed": evidence["tasks_completed"],
            "tasks_open": evidence["tasks_open"],
            "tasks_overdue": evidence["tasks_overdue"],
            "completed_with_due_date": evidence["completed_with_due_date"],
            "completed_on_time": evidence["completed_on_time"],
            "completion_percent": evidence["completion_percent"],
            "on_time_percent": evidence["on_time_percent"],
            "average_completion_days": evidence["average_completion_days"],
            "reviews_performed": evidence["reviews_performed"],
            "checklist_items_total": evidence["checklist_items_total"],
            "checklist_items_completed": evidence["checklist_items_completed"],
            "evidence_files_submitted": evidence["evidence_files_submitted"],
            "comments_posted": evidence["comments_posted"],
        },
    )
    return snapshot, created


def snapshot_everyone(snapshot_date=None, queryset=None, period_type="daily",
                      closing=False):
    """
    Write one cadence's snapshot for every employee who has had a task in the
    window.

    Scoped to people with ACTIVITY rather than to the whole directory: a row of
    zeroes for somebody who has never been given a task is not evidence of
    anything, and a year of them is a table nobody can read.

    The daily, monthly, quarterly and annual series COEXIST — see the model. A
    monthly row is not derived from thirty daily rows; it is computed over the
    month's own window, because the two answer different questions and summing
    dailies would double-count a task that stayed open across days.
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()
    snapshot_date = snapshot_date or timezone.localdate()
    start, end = period_window(period_type, snapshot_date, closing=closing)
    base = queryset if queryset is not None else Task.objects.all()

    user_ids = (base
                .filter(assignees__user__isnull=False,
                        created_at__date__gte=start)
                .values_list("assignees__user_id", flat=True)
                .distinct())

    written = 0
    for user in User.objects.filter(pk__in=list(user_ids), is_active=True):
        evidence = build_evidence(base, user, start, end)
        _, created = write_snapshot(user, evidence, snapshot_date=snapshot_date,
                                    period_type=period_type)
        written += int(created)
    return written


# ---------------------------------------------------------------------------
# The standardised contract (Phase T6.2)
# ---------------------------------------------------------------------------
# The nine metrics T6 names, expressed in the shared evidence schema so a
# consumer reads task evidence the same way it will read memo or leave evidence
# — same units, same denominators, same disclaimer. The dict `build_evidence`
# returns stays exactly as it was: it is what the module's own UI reads, and
# breaking it to reshape it for a consumer that does not exist yet would be the
# wrong trade.
#
# LOW VOLUME IS FLAGGED, NOT HIDDEN (Phase T5.5 audit finding)
# ------------------------------------------------------------
# Departments already carried a `low_volume` marker; people did not. Somebody
# with two tasks showing "50% completion" is noise presented as a metric, and it
# is the single most likely way this data gets misused in a conversation about a
# person. The threshold is declared here, the flag travels with the evidence,
# and a consumer that ignores it is at least ignoring something it was told.
LOW_VOLUME_THRESHOLD = 5


def _metric(key, label, unit, value, definition, basis_of=None):
    return Metric(key=key, label=label, unit=unit, value=value,
                  definition=definition, basis_of=basis_of)


def standardised_metrics(raw):
    """
    Turn one `build_evidence` payload into the nine contract metrics.

    Every percentage names the count it was calculated over — see
    evidence/schema.py on why that is not optional.
    """
    return [
        _metric("tasks_assigned", "Tasks Assigned", Unit.COUNT,
                raw["tasks_assigned"],
                "Tasks assigned to this person that were raised within the "
                "window. Drafts are excluded."),
        _metric("tasks_completed", "Tasks Completed", Unit.COUNT,
                raw["tasks_completed"],
                "Of those, the ones that reached Completed or Closed."),
        _metric("task_completion_percent", "Task Completion", Unit.PERCENT,
                raw["completion_percent"],
                "Completed as a share of assigned, over the window. It measures "
                "what was finished, not how hard any of it was.",
                basis_of="tasks_assigned"),
        _metric("task_overdue_percent", "Task Overdue", Unit.PERCENT,
                round(100 * raw["tasks_overdue"] / raw["tasks_assigned"])
                if raw["tasks_assigned"] else 0,
                "Still-open tasks past their due date, as a share of assigned. "
                "Work finished late is never counted here — it was finished.",
                basis_of="tasks_assigned"),
        _metric("average_completion_days", "Average Completion Time", Unit.DAYS,
                raw["average_completion_days"],
                "Mean days from ASSIGNMENT to completion. Null means nothing "
                "completed in the window — not zero days."),
        _metric("on_time_percent", "On-Time Completion", Unit.PERCENT,
                raw["on_time_percent"],
                "Of the completed tasks that HAD a due date, the share finished "
                "on or before it. Tasks with no due date are excluded, not "
                "counted as on time.",
                basis_of="completed_with_due_date"),
        _metric("review_participation", "Review Participation", Unit.COUNT,
                raw["reviews_performed"],
                "Review decisions this person MADE. Not decisions made about "
                "their work."),
        _metric("checklist_completion", "Checklist Completion", Unit.PERCENT,
                round(100 * raw["checklist_items_completed"]
                      / raw["checklist_items_total"])
                if raw["checklist_items_total"] else 0,
                "Checklist items ticked on their tasks, as a share of items "
                "present.",
                basis_of="checklist_items_total"),
        _metric("evidence_uploaded", "Evidence Uploaded", Unit.COUNT,
                raw["evidence_files_submitted"],
                "Files attached as evidence of work done. Withdrawn files are "
                "not counted."),
        _metric("comments_added", "Comments Added", Unit.COUNT,
                raw["comments_posted"],
                "Comments written on tasks. A participation signal, not a "
                "productivity one."),
    ]


def task_evidence_provider(user, period_start, period_end, period_type,
                           queryset=None, **kwargs):
    """
    The `evidence` registry's task provider (Phase T6.1 / T6.2).

    Registered from `TasksConfig.ready()`. Read-only: it is handed a person and
    a window and returns a value object.

    `queryset` lets a caller pass an ALREADY-SCOPED set so the provider can
    never see more than the caller may. Defaulting to every task is correct only
    for the scheduled snapshot job, which runs as the system rather than as a
    person — every request path passes its own scope.
    """
    base = queryset if queryset is not None else Task.objects.all()
    raw = build_evidence(base, user, period_start, period_end)
    metrics = standardised_metrics(raw)

    # The fairness flag travels WITH the evidence, not beside it, so a consumer
    # cannot pick up the numbers and leave the caveat behind.
    metrics.append(_metric(
        "low_volume", "Low Volume", Unit.COUNT,
        1 if raw["tasks_assigned"] < LOW_VOLUME_THRESHOLD else 0,
        f"1 when fewer than {LOW_VOLUME_THRESHOLD} tasks were assigned in the "
        f"window. Percentages over a handful of tasks are noise, and should not "
        f"be read as performance."))

    return EvidenceSet(
        source=Source.TASK,
        employee_id=str(user.pk),
        employee_name=user.get_full_name() or user.username,
        period_start=str(period_start),
        period_end=str(period_end),
        period_type=PeriodType(period_type) if not isinstance(
            period_type, PeriodType) else period_type,
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# Period windows (Phase T6.3)
# ---------------------------------------------------------------------------
def period_window(period_type, anchor=None, closing=False):
    """
    The (start, end) a snapshot covers.

    By default the period `anchor` SITS IN, ending on the anchor itself — so a
    monthly snapshot taken on the 12th covers the 1st to the 12th. That is a
    partial period, correctly labelled, which is why `period_start` and
    `period_end` are stored on every row rather than inferred later.

    `closing=True` gives the period immediately BEFORE the one the anchor sits
    in, complete. This is what the scheduled job uses: the monthly row is written
    on the 1st and covers the whole month that just ended. Writing it on the 31st
    instead would produce a row missing whatever happened after the job ran that
    evening — and nobody would ever know which hours were absent.
    """
    anchor = anchor or timezone.localdate()
    period_type = PeriodType(period_type)

    if period_type == PeriodType.DAILY:
        return default_period(anchor)

    if closing:
        # Step back one day to land inside the previous period, then take that
        # period whole. One line, and it gets February and year boundaries right
        # without a table of month lengths.
        anchor = _period_start(period_type, anchor) - datetime.timedelta(days=1)

    return _period_start(period_type, anchor), anchor


def _period_start(period_type, day):
    if period_type == PeriodType.MONTHLY:
        return day.replace(day=1)
    if period_type == PeriodType.QUARTERLY:
        quarter = (day.month - 1) // 3
        return day.replace(month=quarter * 3 + 1, day=1)
    return day.replace(month=1, day=1)
