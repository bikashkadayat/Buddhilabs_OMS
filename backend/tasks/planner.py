"""
The Department Planner (Phase TASK-PLANNING-AND-DEPARTMENT-OWNERSHIP).

A week, a month or a quarter of a department's work, laid out in the lanes a
person plans in, with the department's own figures beside it.

WHY LANES RATHER THAN A LIST
----------------------------
A list answers "what is there"; the lists in this module already do that, five
different ways. A plan answers "when is it landing", and that question is only
legible when the work is cut into the periods people actually commit to: days
across a week, weeks across a month, months across a quarter. The cut is made
HERE, on the server, for the same reason the board's columns are: two copies of
a date rule is two copies that disagree about which week the 1st belongs to.

WHAT COUNTS AS BEING IN THE WINDOW
----------------------------------
Its DUE DATE. A plan is about commitments, and a task with no due date has made
none - so those are reported separately, as "undated", rather than silently
dropped into the current lane. Dropping them would make a plan look complete
while the work nobody scheduled sits outside it.

WEEKS START ON SUNDAY, as everywhere else in this product (tasks.analytics uses
the same convention for the calendar), because that is the week Nepal works to.
"""
from datetime import date, timedelta

from django.db.models import Q

from .models import Task

PERIODS = ("weekly", "monthly", "quarterly")

# The statuses the department stats report, in the board's own language so the
# planner and the board cannot disagree about what "In Progress" means.
STAT_BUCKETS = (
    ("to_do", "To Do", (Task.Status.ASSIGNED, Task.Status.ACCEPTED)),
    ("in_progress", "In Progress", (Task.Status.IN_PROGRESS, Task.Status.BLOCKED)),
    ("review", "Review", (Task.Status.UNDER_REVIEW,)),
    ("done", "Done", (Task.Status.COMPLETED, Task.Status.CLOSED)),
)


def _week_start(day):
    """The Sunday on or before `day`."""
    return day - timedelta(days=(day.weekday() + 1) % 7)


def _month_start(day):
    return day.replace(day=1)


def _add_month(day):
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def _quarter_start(day):
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def window(period, anchor=None):
    """
    The dates a plan covers, and the lanes inside it.

    Returns (start, end, lanes) where each lane is {key, label, start, end}.
    Both ends are INCLUSIVE, because a person reading "1 Sep to 30 Sep" means
    the 30th, and an exclusive end would quietly drop the last day's work.
    """
    anchor = anchor or date.today()
    if period == "weekly":
        start = _week_start(anchor)
        end = start + timedelta(days=6)
        lanes = [{"key": (start + timedelta(days=i)).isoformat(),
                  "label": (start + timedelta(days=i)).strftime("%a %d %b"),
                  "start": start + timedelta(days=i),
                  "end": start + timedelta(days=i)}
                 for i in range(7)]
        return start, end, lanes

    if period == "monthly":
        start = _month_start(anchor)
        end = _add_month(start) - timedelta(days=1)
        lanes, cursor = [], start
        while cursor <= end:
            lane_start = cursor
            lane_end = min(_week_start(cursor) + timedelta(days=6), end)
            lanes.append({
                "key": f"week-of-{lane_start.isoformat()}",
                "label": f"{lane_start.strftime('%d %b')} – {lane_end.strftime('%d %b')}",
                "start": lane_start, "end": lane_end,
            })
            cursor = lane_end + timedelta(days=1)
        return start, end, lanes

    # quarterly
    start = _quarter_start(anchor)
    lanes, cursor = [], start
    for _ in range(3):
        lane_end = _add_month(cursor) - timedelta(days=1)
        lanes.append({"key": cursor.strftime("%Y-%m"),
                      "label": cursor.strftime("%B %Y"),
                      "start": cursor, "end": lane_end})
        cursor = _add_month(cursor)
    return start, lanes[-1]["end"], lanes


def department_stats(queryset, today=None):
    """
    To Do / In Progress / Review / Done / Overdue, over whatever the caller can
    see.

    OVERDUE IS NOT A FIFTH BUCKET, it is a cross-cut: an overdue task is also In
    Progress or To Do, and counting it only as overdue would make the buckets
    sum to less than the work. The payload says so rather than leaving a reader
    to discover that the numbers do not add up.
    """
    today = today or date.today()
    stats = []
    for key, label, statuses in STAT_BUCKETS:
        stats.append({"key": key, "label": label,
                      "count": queryset.filter(status__in=statuses).count()})
    overdue = queryset.filter(status__in=Task.OPEN_STATUSES,
                              due_date__lt=today).count()
    stats.append({"key": "overdue", "label": "Overdue", "count": overdue,
                  "overlaps": True})
    return stats


def plan(queryset, period, anchor=None, today=None):
    """
    The plan payload: the window, its lanes with the work due in each, the
    department statistics, and the work with no date at all.
    """
    today = today or date.today()
    start, end, lanes = window(period, anchor)
    in_window = (queryset.filter(due_date__gte=start, due_date__lte=end)
                 .order_by("due_date", "-priority", "title"))

    by_lane = {lane["key"]: [] for lane in lanes}
    for task in in_window:
        for lane in lanes:
            if lane["start"] <= task.due_date <= lane["end"]:
                by_lane[lane["key"]].append(task)
                break

    return {
        "period": period,
        "start": start,
        "end": end,
        "lanes": [{
            "key": lane["key"], "label": lane["label"],
            "start": lane["start"], "end": lane["end"],
            "count": len(by_lane[lane["key"]]),
            "tasks": by_lane[lane["key"]],
        } for lane in lanes],
        "stats": department_stats(queryset, today=today),
        # Open work nobody has committed to a date. Counted, not hidden: a plan
        # that ignores it looks finished while the unscheduled work is what
        # sinks the quarter.
        "undated": queryset.filter(due_date__isnull=True,
                                   status__in=Task.OPEN_STATUSES).count(),
        "overdue_before_window": queryset.filter(
            status__in=Task.OPEN_STATUSES, due_date__lt=start).count(),
    }


def kpis(queryset, start, end, today=None):
    """
    The Department Head's five figures: raised, completed, in review, overdue,
    and the completion rate.

    RAISED AND COMPLETED ARE WINDOWED; IN REVIEW AND OVERDUE ARE NOT. "Created
    this month" is a fact about the month, but "waiting for review" and
    "overdue" are facts about right now - a review that has been sitting since
    August is exactly what a September dashboard needs to show, and windowing it
    away would hide the oldest problems.
    """
    today = today or date.today()
    created = queryset.filter(created_at__date__gte=start, created_at__date__lte=end)
    completed = queryset.filter(completed_at__date__gte=start,
                                completed_at__date__lte=end)
    created_count = created.count()
    completed_count = completed.count()
    return {
        "window": {"start": start, "end": end},
        "tasks_created": created_count,
        "tasks_completed": completed_count,
        "in_review": queryset.filter(status=Task.Status.UNDER_REVIEW).count(),
        "overdue": queryset.filter(status__in=Task.OPEN_STATUSES,
                                   due_date__lt=today).count(),
        # Of the work RAISED in the window, how much of it finished. Not
        # completed/created across different sets, which can exceed 100% when a
        # backlog clears and reads as a department outperforming reality.
        "completion_percent": (round(100 * created.filter(
            status__in=(Task.Status.COMPLETED, Task.Status.CLOSED)).count()
            / created_count) if created_count else None),
    }


def goal_rows(goals):
    """Goals with their derived progress, for the planner and the reports."""
    rows = []
    for goal in goals:
        progress = goal.progress()
        rows.append({
            "id": goal.id, "title": goal.title, "period": goal.period,
            "period_label": goal.get_period_display(),
            "status": goal.status, "status_label": goal.get_status_display(),
            "department": goal.department_name,
            "starts_on": goal.starts_on, "ends_on": goal.ends_on,
            **progress,
        })
    return rows


def visible_goals(user, queryset):
    """
    Goals a person may read: their own department's, or every department's for
    the roles that read the whole organisation.

    Mirrors tasks.permissions.visible_task_filter rather than inventing a second
    scoping rule - a goal is a heading over tasks, and it must not be visible
    where its tasks are not.
    """
    from . import permissions as perms

    if perms.has_org_wide_read(user):
        return queryset
    dept_ids = perms.department_ids_in_scope(user)
    if not dept_ids:
        return queryset.none()
    return queryset.filter(Q(department_id__in=dept_ids))
