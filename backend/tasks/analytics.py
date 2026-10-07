"""
Read-side aggregation for the task workspace (Phase T3, Parts 3-6 and 8).

EVERY FUNCTION HERE TAKES AN ALREADY-SCOPED QUERYSET
----------------------------------------------------
Not a user, not a request — a queryset the caller has already filtered through
`tasks.permissions.visible_task_filter`. That is deliberate: it means nothing in
this module can widen what somebody may see, because it never had the chance to
narrow it. Authorisation stays in exactly one place (tasks/permissions.py), and
this file is arithmetic.

NOTHING HERE WRITES
-------------------
No model in this module, no migration, no field. Phase T3 is a workspace built
over data Phases T1 and T2 already record; a metric that needed a new column
would be a sign it was being computed at the wrong time.
"""
import datetime
from collections import OrderedDict

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q
from django.utils import timezone

from .models import Task

Status = Task.Status

DONE_STATUSES = [Status.COMPLETED, Status.CLOSED]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def overdue_days(task, today=None):
    """How late a task is, in whole days. 0 when it is not late."""
    today = today or timezone.localdate()
    if not task.due_date or task.status not in Task.OPEN_STATUSES:
        return 0
    return max((today - task.due_date).days, 0)


def average_completion_days(queryset):
    """
    Mean days from assignment to completion, over tasks that actually finished.

    Measured from `assigned_at`, not `created_at`: the clock a person is
    answerable for starts when the work reaches them, and a task that sat in
    somebody's drafts for a fortnight would otherwise be counted against the
    assignee. Tasks missing either stamp are excluded rather than treated as
    zero — a task with no assignment stamp predates the workflow that sets one,
    and folding it in as "completed instantly" would drag every average down.

    Returns a float rounded to one decimal, or None when nothing has completed
    yet. None rather than 0.0, because "no data" and "same day" are different
    answers and a dashboard showing 0.0 days would be read as the second.
    """
    rows = queryset.filter(
        status__in=DONE_STATUSES,
        assigned_at__isnull=False,
        completed_at__isnull=False,
    ).annotate(
        elapsed=ExpressionWrapper(F("completed_at") - F("assigned_at"),
                                  output_field=DurationField()),
    ).aggregate(mean=Avg("elapsed"))

    mean = rows["mean"]
    if mean is None:
        return None
    return round(mean.total_seconds() / 86400, 1)


def completion_percent(done, total):
    """Integer percent, and 0 rather than a division by zero on an empty set."""
    return round(100 * done / total) if total else 0


# ---------------------------------------------------------------------------
# Calendar (Part 3)
# ---------------------------------------------------------------------------
def calendar_events(queryset, start, end, today=None):
    """
    Tasks as dated events between `start` and `end` inclusive.

    A task appears on its DUE date, and is classified into exactly one of four
    kinds so the client colours it without re-deriving the rule:

      overdue    past its due date and still open
      completed  finished, whenever it finished
      due        open and due today
      upcoming   open and due later

    One kind per task, not a set: a calendar cell showing a task as both
    "completed" and "upcoming" is a cell nobody can read. Tasks with no due date
    are absent entirely — a calendar is a view of dates, and inventing one to
    place them on would be a lie about when they are wanted.
    """
    today = today or timezone.localdate()
    rows = (queryset
            .filter(due_date__gte=start, due_date__lte=end)
            .select_related("department")
            .prefetch_related("assignees")
            .order_by("due_date", "-priority"))

    events = []
    for task in rows:
        if task.status in DONE_STATUSES:
            kind = "completed"
        elif task.due_date < today:
            kind = "overdue"
        elif task.due_date == today:
            kind = "due"
        else:
            kind = "upcoming"
        events.append({"task": task, "date": task.due_date, "kind": kind})
    return events


def calendar_range(anchor, view):
    """
    The (start, end) window for a Day / Week / Month view around `anchor`.

    The week starts on SUNDAY, matching the Nepali working week — a calendar
    that breaks the week where the local one does not is subtly wrong every
    single time it is read. `weekday()` is Monday=0, so Sunday is 6 and the
    offset is `(weekday + 1) % 7`.
    """
    if view == "day":
        return anchor, anchor
    if view == "week":
        start = anchor - datetime.timedelta(days=(anchor.weekday() + 1) % 7)
        return start, start + datetime.timedelta(days=6)
    # Month: the 1st to the last day, found by stepping into the next month and
    # back a day rather than by a table of month lengths (which gets February
    # wrong every four years).
    start = anchor.replace(day=1)
    next_month = (start + datetime.timedelta(days=32)).replace(day=1)
    return start, next_month - datetime.timedelta(days=1)


# ---------------------------------------------------------------------------
# Workload (Part 4)
# ---------------------------------------------------------------------------
def workload_by_employee(queryset, today=None, limit=100):
    """
    One row per person with work assigned, ordered by open load.

    Counts are computed in PYTHON over one prefetched pass rather than with a
    per-person query, and rows come from the assignee table rather than from the
    user directory — somebody with no tasks is not a workload row, and listing
    every employee in the organisation with three zeroes beside their name is how
    a workload view becomes unreadable.

    `is_overloaded` is a flag, not a judgement: it marks whoever is carrying more
    than 1.5x the median open load, which is the shape of "this needs looking at"
    without pretending to know anybody's capacity.
    """
    today = today or timezone.localdate()
    rows = (queryset
            .prefetch_related("assignees")
            .select_related("department"))

    people = OrderedDict()
    for task in rows:
        for row in task.assignees.all():
            if row.user_id is None:
                continue
            entry = people.setdefault(row.user_id, {
                "user_id": str(row.user_id),
                "name": row.user_name or "Unknown",
                "designation": row.designation or "",
                "department": row.department_label or task.department_name or "",
                "open": 0, "completed": 0, "overdue": 0, "total": 0,
            })
            entry["total"] += 1
            if task.status in DONE_STATUSES:
                entry["completed"] += 1
            elif task.status in Task.OPEN_STATUSES:
                entry["open"] += 1
                if task.due_date and task.due_date < today:
                    entry["overdue"] += 1

    result = sorted(people.values(), key=lambda r: (-r["open"], r["name"]))[:limit]

    # A TRUE median — the mean of the two middle values on an even-sized list,
    # not the upper one. Taking the upper element makes the busiest person the
    # median whenever there are two of them, so nobody could ever exceed 1.5x
    # it and the flag would never fire.
    open_loads = sorted(r["open"] for r in result)
    if not open_loads:
        median = 0
    elif len(open_loads) % 2:
        median = open_loads[len(open_loads) // 2]
    else:
        middle = len(open_loads) // 2
        median = (open_loads[middle - 1] + open_loads[middle]) / 2

    threshold = median * 1.5
    for entry in result:
        entry["completion_percent"] = completion_percent(
            entry["completed"], entry["total"])
        # A median of 0 means nobody is carrying anything; flagging the one
        # person with a single task as "overloaded" would be absurd.
        entry["is_overloaded"] = bool(median and entry["open"] > threshold)
    return result


def workload_by_department(queryset, today=None):
    """
    One row per department. Straight SQL aggregation — a department is a column
    on the task, so unlike the per-person view this needs no join to fan out.
    """
    today = today or timezone.localdate()
    rows = (queryset
            .values("department_name")
            .annotate(
                total=Count("id", distinct=True),
                open=Count("id", distinct=True,
                           filter=Q(status__in=Task.OPEN_STATUSES)),
                completed=Count("id", distinct=True,
                                filter=Q(status__in=DONE_STATUSES)),
                overdue=Count("id", distinct=True,
                              filter=Q(status__in=Task.OPEN_STATUSES,
                                       due_date__lt=today)),
            )
            .order_by("-open", "department_name"))

    return [
        {
            "department": row["department_name"] or "Unassigned",
            "total": row["total"],
            "open": row["open"],
            "completed": row["completed"],
            "overdue": row["overdue"],
            "completion_percent": completion_percent(row["completed"], row["total"]),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Reports (Part 8)
# ---------------------------------------------------------------------------
# Each report returns the same envelope — {columns, rows, summary} — so the
# frontend renders all five with one table component and CSV export is written
# once. A report that invented its own shape would need its own renderer, and
# the fifth one would get a worse table than the first.
def _employee_names(task):
    return ", ".join(row.user_name for row in task.assignees.all()) or "Unassigned"


def report_completion(queryset, today=None):
    """
    Per-employee: how much was finished, how late, and how long it took.

    "Average days" is the mean over that person's COMPLETED tasks only, so it is
    not diluted by work still in flight — an average that moved every time
    somebody was given a new task would measure assignment, not completion.
    """
    today = today or timezone.localdate()
    people = workload_by_employee(queryset, today=today, limit=500)
    means = _average_days_by_assignee(queryset)

    rows = [
        {
            "employee": entry["name"],
            "department": entry["department"] or "—",
            "total": entry["total"],
            "completed": entry["completed"],
            "open": entry["open"],
            "overdue": entry["overdue"],
            "completion_percent": entry["completion_percent"],
            "average_days": means.get(entry["user_id"]),
        }
        for entry in people
    ]
    total = sum(r["total"] for r in rows)
    completed = sum(r["completed"] for r in rows)
    return {
        "columns": [
            {"key": "employee", "label": "Employee"},
            {"key": "department", "label": "Department"},
            {"key": "total", "label": "Total", "numeric": True},
            {"key": "completed", "label": "Completed", "numeric": True},
            {"key": "open", "label": "Open", "numeric": True},
            {"key": "overdue", "label": "Overdue", "numeric": True},
            {"key": "completion_percent", "label": "Completion %", "numeric": True},
            {"key": "average_days", "label": "Avg Days", "numeric": True},
        ],
        "rows": rows,
        "summary": {
            "employees": len(rows),
            "total": total,
            "completed": completed,
            "completion_percent": completion_percent(completed, total),
            "average_days": average_completion_days(queryset),
        },
    }


def _average_days_by_assignee(queryset):
    """
    Mean completion time per assignee.

    One grouped query rather than one per person. The join to the assignee table
    fans a multi-assignee task out across everybody on it, which is the right
    answer here: each of them took that long.
    """
    rows = (queryset
            .filter(status__in=DONE_STATUSES,
                    assigned_at__isnull=False,
                    completed_at__isnull=False,
                    assignees__user__isnull=False)
            .annotate(elapsed=ExpressionWrapper(
                F("completed_at") - F("assigned_at"), output_field=DurationField()))
            .values("assignees__user_id")
            .annotate(mean=Avg("elapsed")))
    return {
        str(row["assignees__user_id"]): round(row["mean"].total_seconds() / 86400, 1)
        for row in rows if row["mean"] is not None
    }


def report_status(queryset):
    """Every status, with its share. The distribution behind the HR dashboard."""
    counts = dict(queryset.values_list("status").annotate(
        n=Count("id", distinct=True)))
    total = sum(counts.values())
    rows = [
        {
            "status": label,
            "count": counts.get(value, 0),
            "share_percent": completion_percent(counts.get(value, 0), total),
        }
        # Driven by the CHOICES, not by what happens to be in the table, so a
        # status with nothing in it shows as 0 rather than vanishing — "no tasks
        # are blocked" is the answer somebody is looking for.
        for value, label in Task.Status.choices
    ]
    return {
        "columns": [
            {"key": "status", "label": "Status"},
            {"key": "count", "label": "Tasks", "numeric": True},
            {"key": "share_percent", "label": "Share %", "numeric": True},
        ],
        "rows": rows,
        "summary": {"total": total},
    }


def report_department(queryset, today=None):
    rows = workload_by_department(queryset, today=today)
    total = sum(r["total"] for r in rows)
    completed = sum(r["completed"] for r in rows)
    return {
        "columns": [
            {"key": "department", "label": "Department"},
            {"key": "total", "label": "Total", "numeric": True},
            {"key": "open", "label": "Open", "numeric": True},
            {"key": "completed", "label": "Completed", "numeric": True},
            {"key": "overdue", "label": "Overdue", "numeric": True},
            {"key": "completion_percent", "label": "Completion %", "numeric": True},
        ],
        "rows": rows,
        "summary": {
            "departments": len(rows),
            "total": total,
            "completed": completed,
            "completion_percent": completion_percent(completed, total),
        },
    }


def report_workload(queryset, today=None):
    rows = workload_by_employee(queryset, today=today, limit=500)
    return {
        "columns": [
            {"key": "name", "label": "Employee"},
            {"key": "department", "label": "Department"},
            {"key": "open", "label": "Open", "numeric": True},
            {"key": "overdue", "label": "Overdue", "numeric": True},
            {"key": "completed", "label": "Completed", "numeric": True},
            {"key": "total", "label": "Total", "numeric": True},
            {"key": "is_overloaded", "label": "Overloaded"},
        ],
        "rows": rows,
        "summary": {
            "employees": len(rows),
            "open": sum(r["open"] for r in rows),
            "overloaded": sum(1 for r in rows if r["is_overloaded"]),
        },
    }


def report_overdue(queryset, today=None):
    """
    Every open task past its date, worst first (Part 6's screen and Part 8's
    report are the same data, so they are the same query).
    """
    today = today or timezone.localdate()
    rows = (queryset
            .filter(due_date__lt=today, status__in=Task.OPEN_STATUSES)
            .select_related("department", "reviewer")
            .prefetch_related("assignees")
            .order_by("due_date"))

    out = [
        {
            "task_id": str(task.id),
            "task_number": task.task_number,
            "title": task.title,
            "assignee": _employee_names(task),
            "department": task.department_name or "—",
            "priority": task.get_priority_display(),
            "status": task.get_status_display(),
            "reviewer": task.reviewer_name or task.created_by_name or "—",
            "due_date": task.due_date,
            "overdue_days": (today - task.due_date).days,
        }
        for task in rows
    ]
    return {
        "columns": [
            {"key": "task_number", "label": "Task No"},
            {"key": "title", "label": "Task"},
            {"key": "assignee", "label": "Assignee"},
            {"key": "department", "label": "Department"},
            {"key": "priority", "label": "Priority"},
            {"key": "reviewer", "label": "Reviewer"},
            {"key": "due_date", "label": "Due"},
            {"key": "overdue_days", "label": "Days Overdue", "numeric": True},
        ],
        "rows": out,
        "summary": {
            "total": len(out),
            "worst_days": max((r["overdue_days"] for r in out), default=0),
        },
    }


def report_reviewer(queryset, today=None):
    """
    Per reviewer: how much came to them, how much they sent back, and how long
    work sat with them.

    "Turnaround" is submitted_at -> completed_at, which is the reviewer's OWN
    time — not the task's age. A reviewer who answers in a day on work that took
    a month to produce should read as fast, and measuring from creation would
    show the opposite.

    The rework rate is the point of the report. A reviewer who returns nothing
    may be waving work through; one who returns most of it may be reviewing
    against a standard nobody was told about. Neither is a verdict, and the
    column is a count beside a total rather than a score.
    """
    from django.db.models import Count as _Count

    today = today or timezone.localdate()
    # Only tasks that actually reached a reviewer. A task nobody submitted says
    # nothing about the person named on it.
    reviewed = queryset.filter(submitted_at__isnull=False) | queryset.filter(
        status__in=DONE_STATUSES)

    rows = {}
    for task in reviewed.distinct().select_related("reviewer"):
        name = task.reviewer_name or task.created_by_name or "Unassigned"
        entry = rows.setdefault(name, {
            "reviewer": name, "received": 0, "approved": 0, "returned": 0,
            "awaiting": 0, "_turnarounds": [],
        })
        entry["received"] += 1
        if task.status == Status.UNDER_REVIEW:
            entry["awaiting"] += 1
        if task.status in DONE_STATUSES:
            entry["approved"] += 1
            if task.submitted_at and task.completed_at:
                entry["_turnarounds"].append(
                    (task.completed_at - task.submitted_at).total_seconds() / 86400)

    # Returns are counted from the timeline, not from the task's current state:
    # a task sent back twice and then approved shows as approved, and the two
    # returns would otherwise be invisible.
    returns = (queryset
               .filter(audit_entries__action="rework_requested")
               .values("audit_entries__actor_name")
               .annotate(n=_Count("audit_entries__id")))
    for row in returns:
        name = row["audit_entries__actor_name"] or "Unassigned"
        if name in rows:
            rows[name]["returned"] = row["n"]

    out = []
    for entry in sorted(rows.values(), key=lambda r: (-r["received"], r["reviewer"])):
        turnarounds = entry.pop("_turnarounds")
        entry["average_turnaround_days"] = (
            round(sum(turnarounds) / len(turnarounds), 1) if turnarounds else None)
        entry["return_rate_percent"] = completion_percent(
            entry["returned"], entry["received"])
        out.append(entry)

    return {
        "columns": [
            {"key": "reviewer", "label": "Reviewer"},
            {"key": "received", "label": "Received", "numeric": True},
            {"key": "approved", "label": "Approved", "numeric": True},
            {"key": "returned", "label": "Returned", "numeric": True},
            {"key": "awaiting", "label": "Awaiting", "numeric": True},
            {"key": "return_rate_percent", "label": "Return %", "numeric": True},
            {"key": "average_turnaround_days", "label": "Avg Turnaround",
             "numeric": True},
        ],
        "rows": out,
        "summary": {
            "reviewers": len(out),
            "received": sum(r["received"] for r in out),
            "awaiting": sum(r["awaiting"] for r in out),
        },
    }


def resource_utilisation(queryset, today=None):
    """
    Organisation-level load, for the HR workload view.

    `utilisation_percent` is the share of all OPEN work sitting with the busiest
    quarter of people — a concentration measure, not a capacity one. Nothing here
    knows anybody's hours, so a figure presented as "83% utilised" would be
    invented; "a quarter of the team is holding 60% of the open work" is a fact
    the data supports and a manager can act on.
    """
    people = workload_by_employee(queryset, today=today, limit=1000)
    total_open = sum(p["open"] for p in people)
    if not people or not total_open:
        return {
            "people": len(people), "open": 0, "median_open": 0,
            "busiest_quarter_share_percent": 0, "overloaded": 0,
            "unassigned_open": queryset.filter(
                status__in=Task.OPEN_STATUSES, assignees__isnull=True).count(),
        }

    ordered = sorted((p["open"] for p in people), reverse=True)
    quarter = max(1, round(len(ordered) / 4))
    return {
        "people": len(people),
        "open": total_open,
        "median_open": ordered[len(ordered) // 2],
        "busiest_quarter_share_percent": completion_percent(
            sum(ordered[:quarter]), total_open),
        "overloaded": sum(1 for p in people if p["is_overloaded"]),
        # Open work nobody is carrying is the gap a utilisation view most often
        # hides, so it is reported beside the load rather than left out.
        "unassigned_open": queryset.filter(
            status__in=Task.OPEN_STATUSES, assignees__isnull=True).count(),
    }


def personal_workload(queryset, user, today=None):
    """
    The employee perspective on the workload view: their own four numbers.

    `queryset` is already scoped, so this cannot see anybody else's work — it
    narrows to the caller's own assignments and counts them.
    """
    today = today or timezone.localdate()
    mine = queryset.filter(assignees__user=user).distinct()
    total = mine.count()
    completed = mine.filter(status__in=DONE_STATUSES).count()
    return {
        "assigned": total,
        "open": mine.filter(status__in=Task.OPEN_STATUSES).count(),
        "completed": completed,
        "overdue": mine.filter(status__in=Task.OPEN_STATUSES,
                               due_date__lt=today).count(),
        "completion_percent": completion_percent(completed, total),
        "average_completion_days": average_completion_days(mine),
    }


def completion_trend(queryset, weeks=12, today=None):
    """
    Tasks completed per week, most recent last (Phase T4.11).

    Bucketed by the ISO week of `completed_at`, and every week in the window is
    present even when nothing finished in it — a trend line that silently skips
    its empty weeks is a lie about the shape of the curve, because the gaps are
    where the story usually is.
    """
    today = today or timezone.localdate()
    # Start on the Sunday of the window's first week, matching the working week
    # the calendar splits on, so a "week" means the same thing in both places.
    this_week_start = today - datetime.timedelta(days=(today.weekday() + 1) % 7)
    start = this_week_start - datetime.timedelta(weeks=weeks - 1)

    rows = (queryset
            .filter(status__in=DONE_STATUSES, completed_at__date__gte=start)
            .values_list("completed_at", flat=True))

    buckets = OrderedDict()
    for index in range(weeks):
        week_start = start + datetime.timedelta(weeks=index)
        buckets[week_start.isoformat()] = 0
    for stamp in rows:
        completed = timezone.localtime(stamp).date()
        week_start = completed - datetime.timedelta(
            days=(completed.weekday() + 1) % 7)
        key = week_start.isoformat()
        if key in buckets:
            buckets[key] += 1

    return [{"week_starting": key, "completed": value}
            for key, value in buckets.items()]


def report_template_usage(queryset, today=None):
    """
    Which templates are actually used, and how the work raised from them fares
    (Phase T4.12).

    Counted over TASKS rather than read off `TaskTemplate.usage_count`, because
    that counter is scoped to nothing — it counts every use organisation-wide,
    and this report is scoped to what the caller can see. Two numbers that
    disagree on the same screen is worse than one.
    """
    today = today or timezone.localdate()
    rows = (queryset
            .filter(template__isnull=False)
            .values("template__name")
            .annotate(
                raised=Count("id", distinct=True),
                completed=Count("id", distinct=True,
                                filter=Q(status__in=DONE_STATUSES)),
                open=Count("id", distinct=True,
                           filter=Q(status__in=Task.OPEN_STATUSES)),
                overdue=Count("id", distinct=True,
                              filter=Q(status__in=Task.OPEN_STATUSES,
                                       due_date__lt=today)),
            )
            .order_by("-raised"))

    out = [
        {
            "template": row["template__name"],
            "raised": row["raised"],
            "completed": row["completed"],
            "open": row["open"],
            "overdue": row["overdue"],
            "completion_percent": completion_percent(row["completed"],
                                                     row["raised"]),
        }
        for row in rows
    ]
    unused = queryset.filter(template__isnull=True).count()
    return {
        "columns": [
            {"key": "template", "label": "Template"},
            {"key": "raised", "label": "Tasks Raised", "numeric": True},
            {"key": "completed", "label": "Completed", "numeric": True},
            {"key": "open", "label": "Open", "numeric": True},
            {"key": "overdue", "label": "Overdue", "numeric": True},
            {"key": "completion_percent", "label": "Completion %",
             "numeric": True},
        ],
        "rows": out,
        "summary": {
            "templates_used": len(out),
            "from_templates": sum(r["raised"] for r in out),
            # Reported beside it, because "how much of our work is templated"
            # is the question this report is usually opened to answer.
            "raised_by_hand": unused,
        },
    }


def report_task_health(queryset, today=None):
    """The five ratios as a table, each row carrying the definition it is read by."""
    health = task_health(queryset, today=today)
    rows = [
        {
            "metric": item["label"],
            "value": item["value"],
            "unit": item["unit"],
            "better": "higher" if item["higher_is_better"] else "lower",
            "definition": item["definition"],
        }
        for item in health["kpis"]
    ]
    return {
        "columns": [
            {"key": "metric", "label": "Metric"},
            {"key": "value", "label": "Value", "numeric": True},
            {"key": "unit", "label": "Unit"},
            {"key": "better", "label": "Better When"},
            {"key": "definition", "label": "Definition"},
        ],
        "rows": rows,
        "summary": {
            "tasks": health["inputs"]["total"],
            "open": health["inputs"]["open"],
            "completed": health["inputs"]["completed"],
        },
    }


def report_trend(queryset, today=None, period="weekly"):
    """Created / completed / closed / overdue, per period."""
    data = trend(queryset, period=period, today=today)
    return {
        "columns": [
            {"key": "label", "label": "Period"},
            {"key": "created", "label": "Created", "numeric": True},
            {"key": "completed", "label": "Completed", "numeric": True},
            {"key": "closed", "label": "Closed", "numeric": True},
            {"key": "overdue", "label": "Overdue", "numeric": True},
        ],
        "rows": data["buckets"],
        "summary": {
            "period": data["period"],
            "created": sum(b["created"] for b in data["buckets"]),
            "completed": sum(b["completed"] for b in data["buckets"]),
        },
    }


def report_executive(queryset, today=None):
    """
    The one-page summary: every KPI with its definition, over the caller's scope.

    Deliberately the KPI registry rather than a hand-picked selection — an
    executive summary that quietly omits the unflattering metric is not a summary.
    """
    summary = executive_summary(queryset, today=today)
    rows = [
        {
            "metric": item["label"],
            "value": item["value"],
            "unit": item["unit"],
            "definition": item["definition"],
        }
        for item in summary["kpis"]
    ]
    totals = summary["totals"]
    rows.extend([
        {"metric": "Total Tasks", "value": totals["total"], "unit": "",
         "definition": "Every task in scope, excluding drafts."},
        {"metric": "Open Tasks", "value": totals["open"], "unit": "",
         "definition": "Assigned, accepted, in progress, under review or blocked."},
        {"metric": "Review Backlog", "value": totals["review_backlog"], "unit": "",
         "definition": "Submitted work awaiting a decision."},
        {"metric": "Average Completion Time",
         "value": totals["average_completion_days"], "unit": "days",
         "definition": "From assignment to completion. Blank when nothing has "
                       "completed — not zero."},
    ])
    return {
        "columns": [
            {"key": "metric", "label": "Metric"},
            {"key": "value", "label": "Value", "numeric": True},
            {"key": "unit", "label": "Unit"},
            {"key": "definition", "label": "Definition"},
        ],
        "rows": rows,
        "summary": {
            "total": totals["total"],
            "completed": totals["completed"],
            "overdue": totals["overdue"],
            "blocked": totals["blocked"],
        },
    }


# ---------------------------------------------------------------------------
# Evidence exports (Phase T6.7)
# ---------------------------------------------------------------------------
# All three are ACTIVITY reports. None ranks, scores or orders by a metric —
# every one is sorted by name or by department, and every percentage column is
# accompanied by the count it was taken over. A low-volume row is marked so an
# exported spreadsheet carries the caveat with it; a caveat that only exists on
# screen is one that does not survive being emailed.
def report_employee_evidence(queryset, today=None):
    """Per-person task evidence over the standard window."""
    from django.contrib.auth import get_user_model

    from . import evidence as evidence_service

    User = get_user_model()
    start, end = evidence_service.default_period(today)
    user_ids = (queryset.filter(assignees__user__isnull=False)
                .values_list("assignees__user_id", flat=True).distinct())

    rows = []
    for person in User.objects.filter(pk__in=list(user_ids)):
        raw = evidence_service.build_evidence(queryset, person, start, end)
        rows.append({
            "employee": raw["employee_name"],
            "department": raw["department"] or "—",
            "assigned": raw["tasks_assigned"],
            "completed": raw["tasks_completed"],
            "overdue": raw["tasks_overdue"],
            "completion_percent": raw["completion_percent"],
            "on_time_percent": raw["on_time_percent"],
            "on_time_of": raw["completed_with_due_date"],
            "average_days": raw["average_completion_days"],
            "reviews": raw["reviews_performed"],
            "evidence_files": raw["evidence_files_submitted"],
            "low_volume": ("yes" if raw["tasks_assigned"]
                           < evidence_service.LOW_VOLUME_THRESHOLD else ""),
        })
    rows.sort(key=lambda r: r["employee"].lower())      # by NAME, never a metric

    return {
        "columns": [
            {"key": "employee", "label": "Employee"},
            {"key": "department", "label": "Department"},
            {"key": "assigned", "label": "Assigned", "numeric": True},
            {"key": "completed", "label": "Completed", "numeric": True},
            {"key": "overdue", "label": "Overdue", "numeric": True},
            {"key": "completion_percent", "label": "Completion %", "numeric": True},
            {"key": "on_time_percent", "label": "On-Time %", "numeric": True},
            {"key": "on_time_of", "label": "On-Time Of", "numeric": True},
            {"key": "average_days", "label": "Avg Days", "numeric": True},
            {"key": "reviews", "label": "Reviews", "numeric": True},
            {"key": "evidence_files", "label": "Evidence Files", "numeric": True},
            {"key": "low_volume", "label": "Low Volume"},
        ],
        "rows": rows,
        "summary": {
            "employees": len(rows),
            "period_start": str(start),
            "period_end": str(end),
            "low_volume_rows": sum(1 for r in rows if r["low_volume"]),
            "note": "Activity evidence, ordered by name. Not scored or ranked.",
        },
    }


def report_department_summary(queryset, today=None):
    """Department-level activity, for the same window."""
    rows = department_analytics(queryset, today=today)
    rows.sort(key=lambda r: r["department"].lower())
    return {
        "columns": [
            {"key": "department", "label": "Department"},
            {"key": "total", "label": "Tasks", "numeric": True},
            {"key": "completed", "label": "Completed", "numeric": True},
            {"key": "open", "label": "Open", "numeric": True},
            {"key": "overdue", "label": "Overdue", "numeric": True},
            {"key": "completion_percent", "label": "Completion %", "numeric": True},
            {"key": "average_resolution_days", "label": "Avg Resolution",
             "numeric": True},
            {"key": "reviewer_backlog", "label": "Awaiting Review",
             "numeric": True},
        ],
        "rows": rows,
        "summary": {
            "departments": len(rows),
            "tasks": sum(r["total"] for r in rows),
            "note": "Departments, ordered by name.",
        },
    }


def report_task_contribution(queryset, today=None):
    """
    Who contributed what, per task — the row-level record an evidence figure is
    aggregated from.

    Included because an aggregate nobody can drill into is an aggregate nobody
    can check. If somebody disputes "9 of 12 completed", this is the list of
    twelve.
    """
    today = today or timezone.localdate()
    rows = []
    for task in (queryset.exclude(status=Status.DRAFT)
                 .select_related("department", "reviewer")
                 .prefetch_related("assignees", "checklist")
                 .order_by("task_number")):
        items = list(task.checklist.all())
        rows.append({
            "task_number": task.task_number,
            "title": task.title,
            "assignee": ", ".join(r.user_name for r in task.assignees.all())
                        or "Unassigned",
            "reviewer": task.reviewer_name or task.created_by_name or "—",
            "department": task.department_name or "—",
            "status": task.get_status_display(),
            "due_date": task.due_date,
            "completed_at": (timezone.localtime(task.completed_at).date()
                             if task.completed_at else None),
            "checklist_done": sum(1 for i in items if i.is_done),
            "checklist_total": len(items),
        })
    return {
        "columns": [
            {"key": "task_number", "label": "Task No"},
            {"key": "title", "label": "Task"},
            {"key": "assignee", "label": "Assignee"},
            {"key": "reviewer", "label": "Reviewer"},
            {"key": "department", "label": "Department"},
            {"key": "status", "label": "Status"},
            {"key": "due_date", "label": "Due"},
            {"key": "completed_at", "label": "Completed"},
            {"key": "checklist_done", "label": "Checklist Done", "numeric": True},
            {"key": "checklist_total", "label": "Checklist Total", "numeric": True},
        ],
        "rows": rows,
        "summary": {"tasks": len(rows)},
    }


REPORTS_T6 = [
    ("employee-evidence", {
        "label": "Employee Task Evidence Report",
        "description": "Per-person activity evidence, ordered by name. Not "
                       "scored or ranked.",
        "build": report_employee_evidence,
    }),
    ("department-summary", {
        "label": "Department Summary",
        "description": "Department-level activity and resolution times.",
        "build": report_department_summary,
    }),
    ("task-contribution", {
        "label": "Task Contribution Summary",
        "description": "Row-level record behind the aggregates — who did what, "
                       "on which task.",
        "build": report_task_contribution,
    }),
]


REPORTS_T5 = [
    ("task-health", {
        "label": "Task Health Report",
        "description": "On-time, overdue, blocked, rework and review delay.",
        "build": report_task_health,
    }),
    ("trend", {
        "label": "Task Trend Report",
        "description": "Created, completed, closed and overdue over time.",
        "build": report_trend,
    }),
    ("executive", {
        "label": "Executive Summary",
        "description": "Every organisational KPI on one page, with definitions.",
        "build": report_executive,
    }),
]


# ===========================================================================
# Phase TASK-GOVERNANCE-HARDENING - the four governance reports
#
# Each one measures a DURATION or a SHORTFALL rather than a count, because the
# counts already have reports and the question governance asks is "how long" and
# "how badly". Every one of them is empty-safe: the fixtures deliberately
# include a task with no due date, no assignee and no stamps, and a report that
# divides by the number of finished tasks has to survive there being none.
# ===========================================================================
def _days_between(start, end):
    """Whole-ish days between two stamps, to one decimal. None if either is missing."""
    if not start or not end:
        return None
    return round((end - start).total_seconds() / 86400, 1)


def _mean(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def report_department_performance(queryset, today=None):
    """
    Each department's output and the two things that say whether it is healthy:
    how much is late, and how long its work takes end to end.

    Departments are listed in the server's order, not ranked - a league table of
    departments invites the comparison between them, and the sizes are not
    comparable (project rule: no scoring, no ranking).
    """
    today = today or timezone.localdate()
    # Who answers for each department, read through the app registry rather than
    # by importing the leave module (see tasks/models.py on module
    # independence). A department performance report that does not say who is
    # accountable for the numbers is a report nobody can act on - and a blank
    # here is itself the finding (Phase DEPARTMENT-GOVERNANCE-HARDENING).
    from django.apps import apps

    Department = apps.get_model("leaves", "Department")
    heads = {}
    for dept in Department.objects.select_related("head"):
        head = dept.head if (dept.head and dept.head.is_active) else None
        heads[dept.name] = head.get_full_name() if head else ""

    rows = []
    for row in workload_by_department(queryset, today=today):
        name = row["department"]
        in_dept = queryset.filter(department_name=name) if name else queryset.none()
        cycle = _mean([_days_between(t.assigned_at, t.completed_at)
                       for t in in_dept.filter(status__in=DONE_STATUSES)])
        rows.append({
            "department": name,
            "head": heads.get(name) or "NO HEAD",
            "total": row["total"],
            "open": row["open"],
            "completed": row["completed"],
            "overdue": row["overdue"],
            "completion_percent": row["completion_percent"],
            "average_cycle_days": cycle,
            "awaiting_review": in_dept.filter(status=Status.UNDER_REVIEW).count(),
        })
    total = sum(r["total"] for r in rows)
    completed = sum(r["completed"] for r in rows)
    return {
        "columns": [
            {"key": "department", "label": "Department"},
            {"key": "head", "label": "Department Head"},
            {"key": "total", "label": "Total", "numeric": True},
            {"key": "open", "label": "Open", "numeric": True},
            {"key": "completed", "label": "Completed", "numeric": True},
            {"key": "overdue", "label": "Overdue", "numeric": True},
            {"key": "completion_percent", "label": "Completion %", "numeric": True},
            {"key": "average_cycle_days", "label": "Avg Cycle Days", "numeric": True},
            {"key": "awaiting_review", "label": "Awaiting Review", "numeric": True},
        ],
        "rows": rows,
        "summary": {
            "departments": len(rows),
            "total": total,
            "completed": completed,
            "completion_percent": completion_percent(completed, total),
            "average_cycle_days": _mean([r["average_cycle_days"] for r in rows]),
            # Named in the summary as well as the rows: somebody reading only
            # the headline still learns that part of this report has no owner.
            "departments_without_a_head": sum(
                1 for r in rows if r["head"] == "NO HEAD"),
        },
    }


def report_cycle_time(queryset, today=None):
    """
    How long finished work took, broken into the three waits it is made of:
    sitting unstarted, being worked on, and waiting for a reviewer.

    One row per finished task rather than an average, because an average hides
    the two-day task beside the forty-day one, and it is the forty-day one
    somebody needs to go and look at. The summary carries the averages.
    """
    rows = []
    for task in (queryset.filter(status__in=DONE_STATUSES)
                 .order_by("-completed_at")[:500]):
        rows.append({
            "task_number": task.task_number,
            "title": task.title,
            "department": task.department_name or "—",
            "owner": (task.assignees.all()[0].user_name
                      if task.assignees.all() else task.created_by_name),
            "to_start_days": _days_between(task.assigned_at, task.started_at),
            "work_days": _days_between(task.started_at, task.submitted_at),
            "review_days": _days_between(task.submitted_at, task.completed_at),
            "total_days": _days_between(task.assigned_at, task.completed_at),
        })
    return {
        "columns": [
            {"key": "task_number", "label": "Task"},
            {"key": "title", "label": "Title"},
            {"key": "department", "label": "Department"},
            {"key": "owner", "label": "Owner"},
            {"key": "to_start_days", "label": "Waiting To Start", "numeric": True},
            {"key": "work_days", "label": "Being Worked", "numeric": True},
            {"key": "review_days", "label": "With Reviewer", "numeric": True},
            {"key": "total_days", "label": "Total Days", "numeric": True},
        ],
        "rows": rows,
        "summary": {
            "tasks": len(rows),
            "average_to_start_days": _mean([r["to_start_days"] for r in rows]),
            "average_work_days": _mean([r["work_days"] for r in rows]),
            "average_review_days": _mean([r["review_days"] for r in rows]),
            "average_total_days": _mean([r["total_days"] for r in rows]),
        },
    }


def report_review_turnaround(queryset, today=None):
    """
    How long each reviewer keeps work, and what is sitting with them right now.

    The waiting column counts from `submitted_at` to NOW for anything still
    under review, so a reviewer who has not looked at something for a fortnight
    shows a fortnight rather than a blank.
    """
    now = timezone.now()
    reviewers = {}
    for task in queryset.filter(submitted_at__isnull=False).select_related("reviewer"):
        name = task.reviewer_name or "Unassigned"
        row = reviewers.setdefault(name, {
            "reviewer": name, "decided": 0, "waiting": 0,
            "_turnarounds": [], "longest_wait_days": None,
        })
        if task.status == Status.UNDER_REVIEW:
            row["waiting"] += 1
            waited = _days_between(task.submitted_at, now)
            if waited is not None:
                row["longest_wait_days"] = max(row["longest_wait_days"] or 0, waited)
        elif task.completed_at:
            row["decided"] += 1
            row["_turnarounds"].append(
                _days_between(task.submitted_at, task.completed_at))

    rows = []
    for row in sorted(reviewers.values(), key=lambda r: r["reviewer"].lower()):
        turnarounds = row.pop("_turnarounds")
        row["average_turnaround_days"] = _mean(turnarounds)
        rows.append(row)
    return {
        "columns": [
            {"key": "reviewer", "label": "Reviewer"},
            {"key": "decided", "label": "Decided", "numeric": True},
            {"key": "waiting", "label": "Waiting Now", "numeric": True},
            {"key": "average_turnaround_days", "label": "Avg Turnaround Days",
             "numeric": True},
            {"key": "longest_wait_days", "label": "Longest Wait Now", "numeric": True},
        ],
        "rows": rows,
        "summary": {
            "reviewers": len(rows),
            "decided": sum(r["decided"] for r in rows),
            "waiting": sum(r["waiting"] for r in rows),
            "average_turnaround_days": _mean(
                [r["average_turnaround_days"] for r in rows]),
        },
    }


def report_overdue_analysis(queryset, today=None):
    """
    Overdue work grouped by HOW LATE it is, not listed task by task - the
    overdue report already lists them.

    The bands answer a different question: is this a few things that slipped
    this week, or a backlog nobody has looked at in a month? Those need
    different responses, and one list of forty rows shows neither.
    """
    today = today or timezone.localdate()
    bands = [("1-3 days", 1, 3), ("4-7 days", 4, 7), ("8-14 days", 8, 14),
             ("15-30 days", 15, 30), ("Over 30 days", 31, None)]
    counts = {label: {"band": label, "tasks": 0, "departments": set(),
                      "oldest_days": 0} for label, _, _ in bands}
    late = 0
    for task in queryset.filter(status__in=Task.OPEN_STATUSES,
                                due_date__isnull=False):
        days = overdue_days(task, today=today)
        if days <= 0:
            continue
        late += 1
        for label, low, high in bands:
            if days >= low and (high is None or days <= high):
                row = counts[label]
                row["tasks"] += 1
                row["oldest_days"] = max(row["oldest_days"], days)
                if task.department_name:
                    row["departments"].add(task.department_name)
                break
    rows = []
    for label, _, _ in bands:
        row = counts[label]
        rows.append({
            "band": label,
            "tasks": row["tasks"],
            "share_percent": completion_percent(row["tasks"], late),
            "departments": ", ".join(sorted(row["departments"])) or "—",
            "oldest_days": row["oldest_days"],
        })
    return {
        "columns": [
            {"key": "band", "label": "How Late"},
            {"key": "tasks", "label": "Tasks", "numeric": True},
            {"key": "share_percent", "label": "Share %", "numeric": True},
            {"key": "departments", "label": "Departments"},
            {"key": "oldest_days", "label": "Oldest", "numeric": True},
        ],
        "rows": rows,
        "summary": {
            "overdue": late,
            "worst_days": max((r["oldest_days"] for r in rows), default=0),
            "open": queryset.filter(status__in=Task.OPEN_STATUSES).count(),
        },
    }


# ===========================================================================
# Phase TASK-PLANNING-AND-DEPARTMENT-OWNERSHIP - the planning reports
# ===========================================================================
def report_department_productivity(queryset, today=None):
    """
    What each department RAISED and FINISHED, and how much of it is late.

    Deliberately not a ranking (project rule): departments differ in size and in
    the kind of work they carry, and a league table invites a comparison the
    numbers do not support. Rows come back in the server's order.
    """
    today = today or timezone.localdate()
    rows = []
    for row in workload_by_department(queryset, today=today):
        name = row["department"]
        in_dept = queryset.filter(department_name=name) if name else queryset.none()
        rows.append({
            "department": name,
            "raised": row["total"],
            "completed": row["completed"],
            "in_review": in_dept.filter(status=Status.UNDER_REVIEW).count(),
            "overdue": row["overdue"],
            "completion_percent": row["completion_percent"],
            "average_cycle_days": _mean(
                [_days_between(t.assigned_at, t.completed_at)
                 for t in in_dept.filter(status__in=DONE_STATUSES)]),
        })
    total = sum(r["raised"] for r in rows)
    completed = sum(r["completed"] for r in rows)
    return {
        "columns": [
            {"key": "department", "label": "Department"},
            {"key": "raised", "label": "Raised", "numeric": True},
            {"key": "completed", "label": "Completed", "numeric": True},
            {"key": "in_review", "label": "In Review", "numeric": True},
            {"key": "overdue", "label": "Overdue", "numeric": True},
            {"key": "completion_percent", "label": "Completion %", "numeric": True},
            {"key": "average_cycle_days", "label": "Avg Cycle Days", "numeric": True},
        ],
        "rows": rows,
        "summary": {"departments": len(rows), "raised": total,
                    "completed": completed,
                    "completion_percent": completion_percent(completed, total)},
    }


def report_goal_completion(queryset, today=None):
    """
    Every goal in scope and how much of its work is done.

    THE SCOPE IS THE TASKS, NOT THE GOALS. This report is built from the same
    permission-scoped queryset every other report here is, so a goal appears
    only where its department's work does - a goal is a heading over tasks and
    must not be readable where the tasks are not.

    `percent` is blank for a goal nothing has been linked to. Reporting 0% would
    say the department is failing at something nobody has started.
    """
    from .models import DepartmentGoal

    departments = set(queryset.values_list("department_id", flat=True))
    departments.discard(None)
    goals = (DepartmentGoal.objects.filter(department_id__in=departments)
             .select_related("department").prefetch_related("tasks")
             .order_by("department_name", "-starts_on", "title"))
    rows = []
    for goal in goals:
        progress = goal.progress()
        rows.append({
            "department": goal.department_name,
            "goal": goal.title,
            "period": goal.get_period_display(),
            "window": f"{goal.starts_on} to {goal.ends_on}",
            "status": goal.get_status_display(),
            "linked_tasks": progress["linked_tasks"],
            "completed_tasks": progress["completed_tasks"],
            "percent": progress["percent"] if progress["percent"] is not None else "—",
        })
    achieved = sum(1 for g in goals if g.status == DepartmentGoal.Status.ACHIEVED)
    return {
        "columns": [
            {"key": "department", "label": "Department"},
            {"key": "goal", "label": "Goal"},
            {"key": "period", "label": "Period"},
            {"key": "window", "label": "Window"},
            {"key": "status", "label": "Status"},
            {"key": "linked_tasks", "label": "Linked", "numeric": True},
            {"key": "completed_tasks", "label": "Completed", "numeric": True},
            {"key": "percent", "label": "Progress %", "numeric": True},
        ],
        "rows": rows,
        "summary": {
            "goals": len(rows),
            "achieved": achieved,
            "without_linked_work": sum(1 for r in rows if r["linked_tasks"] == 0),
        },
    }


def report_department_progress(queryset, today=None):
    """
    Where each department's open work actually IS - the board's columns as a
    table, plus what is late.

    The difference from Department Productivity is the question: productivity
    asks what a department has produced, progress asks what state its live work
    is in right now. A department can look productive on one and blocked on the
    other, and that pair is the useful reading.
    """
    today = today or timezone.localdate()
    from .planner import STAT_BUCKETS

    rows = []
    names = sorted({name for name in queryset.values_list("department_name", flat=True)
                    if name})
    for name in names:
        in_dept = queryset.filter(department_name=name)
        row = {"department": name}
        for key, _label, statuses in STAT_BUCKETS:
            row[key] = in_dept.filter(status__in=statuses).count()
        row["overdue"] = in_dept.filter(status__in=Task.OPEN_STATUSES,
                                        due_date__lt=today).count()
        row["unscheduled"] = in_dept.filter(due_date__isnull=True,
                                            status__in=Task.OPEN_STATUSES).count()
        rows.append(row)
    return {
        "columns": [
            {"key": "department", "label": "Department"},
            {"key": "to_do", "label": "To Do", "numeric": True},
            {"key": "in_progress", "label": "In Progress", "numeric": True},
            {"key": "review", "label": "Review", "numeric": True},
            {"key": "done", "label": "Done", "numeric": True},
            {"key": "overdue", "label": "Overdue", "numeric": True},
            {"key": "unscheduled", "label": "No Due Date", "numeric": True},
        ],
        "rows": rows,
        "summary": {
            "departments": len(rows),
            # Overdue OVERLAPS the columns beside it - an overdue task is also
            # To Do or In Progress - so it is named rather than added in.
            "overdue": sum(r["overdue"] for r in rows),
            "unscheduled": sum(r["unscheduled"] for r in rows),
        },
    }


REPORTS_PLANNING = [
    ("department-productivity", {
        "label": "Department Productivity",
        "description": "What each department raised, finished and still owes.",
        "build": report_department_productivity,
    }),
    ("goal-completion", {
        "label": "Goal Completion",
        "description": "Every department goal and how much of its work is done.",
        "build": report_goal_completion,
    }),
    ("department-progress", {
        "label": "Department Progress",
        "description": "Where each department's live work sits, and what is late.",
        "build": report_department_progress,
    }),
]


REPORTS_GOVERNANCE = [
    ("department-performance", {
        "label": "Department Performance",
        "description": "Output, lateness and cycle time for each department.",
        "build": report_department_performance,
    }),
    ("cycle-time", {
        "label": "Task Cycle Time",
        "description": "How long finished work took, split into waiting, working "
                       "and reviewing.",
        "build": report_cycle_time,
    }),
    ("review-turnaround", {
        "label": "Review Turnaround",
        "description": "How long each reviewer keeps work, and what is with them now.",
        "build": report_review_turnaround,
    }),
    ("overdue-analysis", {
        "label": "Overdue Analysis",
        "description": "Overdue work banded by how late it is.",
        "build": report_overdue_analysis,
    }),
]


REPORTS = OrderedDict([
    ("completion", {
        "label": "Task Completion Report",
        "description": "Per employee: finished, still open, and how long it took.",
        "build": report_completion,
    }),
    ("status", {
        "label": "Task Status Report",
        "description": "Every status and its share of the total.",
        "build": lambda queryset, today=None: report_status(queryset),
    }),
    ("department", {
        "label": "Department Report",
        "description": "Open, completed and overdue work by department.",
        "build": report_department,
    }),
    ("workload", {
        "label": "Workload Report",
        "description": "Who is carrying what, and who is carrying too much.",
        "build": report_workload,
    }),
    ("overdue", {
        "label": "Overdue Report",
        "description": "Open tasks past their due date, worst first.",
        "build": report_overdue,
    }),
    ("reviewer", {
        "label": "Reviewer Performance Report",
        "description": "What reached each reviewer, what they returned, and how "
                       "long it sat with them.",
        "build": report_reviewer,
    }),
    ("template-usage", {
        "label": "Template Usage Report",
        "description": "Which templates are used, and how the work raised from "
                       "them fares.",
        "build": report_template_usage,
    }),
])

# Phase T5.10 — three more, registered after the fact so the T3/T4 entries above
# stay exactly where they were and their slugs cannot shift.
REPORTS.update(REPORTS_T5)
# Phase T6.7 — the evidence exports, appended for the same reason: an existing
# slug is in bookmarks and saved exports and must not shift.
REPORTS.update(REPORTS_T6)
# Phase TASK-GOVERNANCE-HARDENING - the four governance reports, appended last
# for the same reason every earlier batch was: an existing slug is in somebody's
# bookmarks and saved exports and must not shift.
REPORTS.update(OrderedDict(REPORTS_GOVERNANCE))
# Phase TASK-PLANNING-AND-DEPARTMENT-OWNERSHIP, appended last for the same
# reason as every earlier batch.
REPORTS.update(OrderedDict(REPORTS_PLANNING))


# ===========================================================================
# Phase T5 — performance intelligence
#
# EVERY FUNCTION BELOW TAKES AN ALREADY-SCOPED QUERYSET, like the rest of this
# module. Nothing here can widen what somebody may see, because it never had the
# chance to narrow it.
#
# AND NOTHING BELOW SCORES A PERSON. Employee metrics are reported and ordered
# BY NAME; there is no composite, no index, no rank. Departments are ranked,
# because Part 1 asks for it and a department is a unit of work with a head
# accountable for it — not a person being appraised. The distinction is enforced
# by tests, not left to good intentions.
# ===========================================================================
REVIEW_DELAY_DAYS = 2


def _review_threshold():
    from django.conf import settings

    return getattr(settings, "TASK_REVIEW_PENDING_DAYS", REVIEW_DELAY_DAYS)


def metric_inputs(queryset, today=None):
    """
    The one stats dict every KPI reads (see tasks/kpi.py).

    Assembled in a handful of aggregate queries rather than one pass per metric,
    and DELIBERATELY raw: counts and totals, no percentages. The percentages live
    in the registry, so "completion %" has one definition rather than one per
    caller.

    Drafts are excluded from `total` throughout. A task nobody has been given is
    not work in progress, and counting it drags every completion figure down
    with something that has not started.
    """
    today = today or timezone.localdate()
    threshold = _review_threshold()
    cutoff = timezone.now() - datetime.timedelta(days=threshold)

    live = queryset.exclude(status=Status.DRAFT)
    completed = live.filter(status__in=DONE_STATUSES)

    # On time is measured against the DUE DATE, over completed tasks that had
    # one. A task with no due date is excluded rather than counted as on time:
    # there was nothing to be on time for, and counting it inflates the figure
    # with work nobody committed to a date for.
    with_due = completed.filter(due_date__isnull=False, completed_at__isnull=False)
    on_time = with_due.filter(completed_at__date__lte=F("due_date"))

    ever_submitted = live.filter(
        Q(submitted_at__isnull=False) | Q(status__in=DONE_STATUSES))
    reworked = ever_submitted.filter(
        audit_entries__action="rework_requested").distinct()

    awaiting = live.filter(status=Status.UNDER_REVIEW)
    decided = completed.filter(submitted_at__isnull=False,
                               completed_at__isnull=False)

    return {
        "total": live.count(),
        "open": live.filter(status__in=Task.OPEN_STATUSES).count(),
        "completed": completed.count(),
        "overdue": live.filter(status__in=Task.OPEN_STATUSES,
                               due_date__lt=today).count(),
        "blocked": live.filter(status=Status.BLOCKED).count(),
        "completed_with_due_date": with_due.count(),
        "on_time": on_time.count(),
        "ever_submitted": ever_submitted.count(),
        "reworked": reworked.count(),
        "awaiting_review": awaiting.count(),
        "review_delayed": awaiting.filter(submitted_at__lte=cutoff).count(),
        "reviews_decided": decided.count(),
        "reviews_on_time": decided.filter(
            completed_at__lte=F("submitted_at") + datetime.timedelta(days=threshold)
        ).count(),
    }


def task_health(queryset, today=None):
    """
    The health dashboard (Part 5): the five ratios, with their definitions.

    Returned as KPI objects rather than bare numbers so the client renders the
    explanation beside the figure. A percentage with no stated denominator is
    the most reliable way to have a metric misread in a meeting.
    """
    from . import kpi

    stats = metric_inputs(queryset, today=today)
    return {
        "kpis": kpi.evaluate_all(stats, keys=[
            "on_time_percent", "overdue_percent", "blocked_percent",
            "rework_percent", "review_delay_percent",
        ]),
        "inputs": stats,
    }


# ---------------------------------------------------------------------------
# Trends (Part 6)
# ---------------------------------------------------------------------------
PERIODS = {"weekly": 7, "monthly": 30, "quarterly": 91}


def _bucket_start(day, period):
    """The first day of the bucket `day` falls in."""
    if period == "weekly":
        # Sunday, matching the working week the calendar splits on.
        return day - datetime.timedelta(days=(day.weekday() + 1) % 7)
    if period == "monthly":
        return day.replace(day=1)
    quarter = (day.month - 1) // 3
    return day.replace(month=quarter * 3 + 1, day=1)


def _advance(day, period):
    if period == "weekly":
        return day + datetime.timedelta(days=7)
    step = 32 if period == "monthly" else 95
    nxt = (day + datetime.timedelta(days=step)).replace(day=1)
    return _bucket_start(nxt, period)


def trend(queryset, period="weekly", buckets=12, today=None):
    """
    Created / completed / closed / overdue / blocked, per period (Part 6).

    EVERY BUCKET IS PRESENT, including the empty ones. A trend line that skips
    its quiet weeks is a lie about the shape of the curve, and the gaps are
    usually where the story is.

    `created` and `completed` are counted by the date they HAPPENED, so they are
    historical and stable. `overdue` and `blocked` are states, not events —
    there is no date on which a task "became blocked" that survives it being
    unblocked — so they are counted as at the END of each bucket for closed
    buckets, and as at today for the current one. That is stated in the payload
    (`as_at`) rather than left for a reader to assume it means the same thing as
    the other two.
    """
    if period not in PERIODS:
        raise ValueError(f"Unknown period {period!r}")
    today = today or timezone.localdate()

    current = _bucket_start(today, period)
    starts = [current]
    for _ in range(buckets - 1):
        previous = starts[0]
        if period == "weekly":
            starts.insert(0, previous - datetime.timedelta(days=7))
        elif period == "monthly":
            starts.insert(0, _bucket_start(
                previous - datetime.timedelta(days=1), period))
        else:
            starts.insert(0, _bucket_start(
                previous - datetime.timedelta(days=1), period))

    live = queryset.exclude(status=Status.DRAFT)
    rows = []
    for index, start in enumerate(starts):
        end = (_advance(start, period) - datetime.timedelta(days=1))
        is_current = index == len(starts) - 1
        as_at = today if is_current else end

        rows.append({
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "label": _bucket_label(start, period),
            "created": live.filter(created_at__date__gte=start,
                                   created_at__date__lte=end).count(),
            "completed": live.filter(completed_at__date__gte=start,
                                     completed_at__date__lte=end).count(),
            "closed": live.filter(closed_at__date__gte=start,
                                  closed_at__date__lte=end).count(),
            # States, as at the end of the window — see the docstring.
            "overdue": live.filter(status__in=Task.OPEN_STATUSES,
                                   due_date__lt=as_at).count() if is_current else
                       live.filter(due_date__lt=as_at,
                                   created_at__date__lte=end).exclude(
                           completed_at__date__lte=as_at).count(),
            "blocked": live.filter(status=Status.BLOCKED).count() if is_current else 0,
            "as_at": as_at.isoformat(),
            "is_current": is_current,
        })
    return {"period": period, "buckets": rows}


def _bucket_label(start, period):
    if period == "weekly":
        return start.strftime("%d %b")
    if period == "monthly":
        return start.strftime("%b %Y")
    return f"Q{(start.month - 1) // 3 + 1} {start.year}"


# ---------------------------------------------------------------------------
# Department, employee and reviewer views (Parts 1-4)
# ---------------------------------------------------------------------------
def department_analytics(queryset, today=None):
    """
    Per department, with resolution time and reviewer backlog added to the
    workload figures Phase T3 already produced (Part 2).
    """
    today = today or timezone.localdate()
    base = workload_by_department(queryset, today=today)

    means, backlog = {}, {}
    # `.order_by()` WITH NO ARGUMENTS, and it is load-bearing.
    #
    # Task.Meta.ordering is ["-created_at"]. A `.values(...).annotate(...)` that
    # does not clear it groups by `department_name, created_at` — one group per
    # TASK — and the dict assignments below then keep whichever row the database
    # happened to return last. `reviewer_backlog` was therefore not a count at
    # all: it was "was the last-ordered task in this department under review",
    # which is 0 or 1 by luck, and differed between SQLite and PostgreSQL
    # because the two order equal timestamps differently.
    #
    # `workload_by_department` above escapes this only because it happens to end
    # in `.order_by("-open", "department_name")`, which replaces the inherited
    # ordering. Relying on that by accident is what made this hard to see.
    rows = (queryset.exclude(status=Status.DRAFT)
            .order_by()
            .values("department_name")
            .annotate(
                awaiting=Count("id", distinct=True,
                               filter=Q(status=Status.UNDER_REVIEW)),
            ))
    for row in rows:
        backlog[row["department_name"] or "Unassigned"] = row["awaiting"]

    resolution = (queryset
                  .filter(status__in=DONE_STATUSES,
                          assigned_at__isnull=False, completed_at__isnull=False)
                  .annotate(elapsed=ExpressionWrapper(
                      F("completed_at") - F("assigned_at"),
                      output_field=DurationField()))
                  .order_by()
                  .values("department_name")
                  .annotate(mean=Avg("elapsed")))
    for row in resolution:
        if row["mean"] is not None:
            means[row["department_name"] or "Unassigned"] = round(
                row["mean"].total_seconds() / 86400, 1)

    for entry in base:
        name = entry["department"]
        entry["average_resolution_days"] = means.get(name)
        entry["reviewer_backlog"] = backlog.get(name, 0)
    return base


def department_ranking(queryset, today=None, limit=20):
    """
    Departments ordered by completion rate (Part 1).

    RANKING DEPARTMENTS IS NOT RANKING PEOPLE. A department is a unit of work
    with a head accountable for it; the ranking is a management view of where
    work is and is not getting done. Nothing in this module ranks individuals —
    see `employee_analytics`, which orders by name and is tested for it.

    Departments with fewer than three tasks are reported but marked
    `low_volume`: a department that finished its one task is not "100%
    complete" in any sense worth putting at the top of a list, and a ranking
    that lets a single task outrank a hundred is one nobody believes twice.
    """
    rows = department_analytics(queryset, today=today)
    ranked = sorted(
        rows,
        key=lambda r: (-r["completion_percent"], -r["total"], r["department"]))
    for position, entry in enumerate(ranked[:limit], start=1):
        entry["position"] = position
        entry["low_volume"] = entry["total"] < 3
    return ranked[:limit]


def employee_analytics(queryset, today=None, limit=500):
    """
    Per-person metrics (Part 3). METRICS ONLY.

    THIS FUNCTION DELIBERATELY DOES NOT:
      * produce a score, index or composite of any kind;
      * order people by any metric;
      * compare one person to another, or to an average.

    Rows come back ordered BY NAME. That is not a cosmetic choice — a list
    sorted by completion rate IS a ranking, whatever the column header says, and
    the person at the bottom of it will be asked about it. Phase T5's
    instruction is explicit on this, and `test_analytics.py` asserts both the
    ordering and the absence of any score-shaped key.

    What it does report is what somebody needs to see about their OWN work, and
    what a manager needs to see about who is carrying what: assigned, completed,
    overdue, completion rate, average close time, review delay and current load.
    """
    today = today or timezone.localdate()
    people = workload_by_employee(queryset, today=today, limit=limit)
    means = _average_days_by_assignee(queryset)

    # How long this person's submitted work waits on a reviewer. Reported
    # because a low completion rate caused by somebody else's queue is the most
    # common way these numbers get misread.
    delays = {}
    rows = (queryset
            .filter(status=Status.UNDER_REVIEW, submitted_at__isnull=False,
                    assignees__user__isnull=False)
            .values("assignees__user_id", "submitted_at"))
    now = timezone.now()
    for row in rows:
        key = str(row["assignees__user_id"])
        delays.setdefault(key, []).append((now - row["submitted_at"]).days)

    out = []
    for entry in people:
        waits = delays.get(entry["user_id"], [])
        out.append({
            "user_id": entry["user_id"],
            "name": entry["name"],
            "designation": entry["designation"],
            "department": entry["department"],
            "assigned": entry["total"],
            "completed": entry["completed"],
            "open": entry["open"],
            "overdue": entry["overdue"],
            "completion_percent": entry["completion_percent"],
            "average_close_days": means.get(entry["user_id"]),
            # None, not 0, when nothing of theirs is with a reviewer.
            "review_delay_days": max(waits) if waits else None,
            "task_load": entry["open"],
        })
    # BY NAME. See the docstring — this is the guardrail, not a default.
    return sorted(out, key=lambda r: r["name"].lower())


def reviewer_analytics(queryset, today=None):
    """
    Per reviewer (Part 4): what is waiting, how long decisions take, how much of
    the queue is late.

    A reviewer's own backlog is a QUEUE measure. It is reported so a bottleneck
    can be found and unblocked, not so a person can be assessed — which is why
    there is no score here either, and why the rows carry the raw counts the
    number was derived from.
    """
    today = today or timezone.localdate()
    threshold = _review_threshold()
    cutoff = timezone.now() - datetime.timedelta(days=threshold)
    now = timezone.now()

    rows = {}
    awaiting = (queryset.filter(status=Status.UNDER_REVIEW)
                .select_related("reviewer", "created_by"))
    for task in awaiting:
        name = task.reviewer_name or task.created_by_name or "Unassigned"
        entry = rows.setdefault(name, _blank_reviewer(name))
        entry["awaiting"] += 1
        waited = (now - task.submitted_at).days if task.submitted_at else 0
        entry["oldest_days"] = max(entry["oldest_days"], waited)
        if task.submitted_at and task.submitted_at <= cutoff:
            entry["delayed"] += 1

    decided = (queryset
               .filter(status__in=DONE_STATUSES, submitted_at__isnull=False,
                       completed_at__isnull=False)
               .select_related("reviewer", "created_by"))
    turnarounds = {}
    for task in decided:
        name = task.reviewer_name or task.created_by_name or "Unassigned"
        entry = rows.setdefault(name, _blank_reviewer(name))
        entry["decided"] += 1
        turnarounds.setdefault(name, []).append(
            (task.completed_at - task.submitted_at).total_seconds() / 86400)

    for name, entry in rows.items():
        samples = turnarounds.get(name, [])
        entry["average_review_days"] = (
            round(sum(samples) / len(samples), 1) if samples else None)
        entry["workload"] = entry["awaiting"]
    # By name, for the same reason employees are — a reviewer is a person.
    return sorted(rows.values(), key=lambda r: r["reviewer"].lower())


def _blank_reviewer(name):
    return {"reviewer": name, "awaiting": 0, "delayed": 0, "decided": 0,
            "oldest_days": 0, "average_review_days": None, "workload": 0}


def executive_summary(queryset, today=None):
    """
    The organisation dashboard (Part 1): headline counts, the KPI set, and the
    department ranking.
    """
    from . import kpi

    today = today or timezone.localdate()
    stats = metric_inputs(queryset, today=today)
    awaiting = queryset.filter(status=Status.UNDER_REVIEW)

    return {
        "totals": {
            "total": stats["total"],
            "completed": stats["completed"],
            "open": stats["open"],
            "overdue": stats["overdue"],
            "blocked": stats["blocked"],
            "review_backlog": stats["awaiting_review"],
            "average_completion_days": average_completion_days(queryset),
            "oldest_review_days": max(
                ((timezone.now() - task.submitted_at).days
                 for task in awaiting if task.submitted_at), default=0),
        },
        "kpis": kpi.evaluate_all(stats),
        "department_ranking": department_ranking(queryset, today=today),
        "generated_at": timezone.now(),
    }
