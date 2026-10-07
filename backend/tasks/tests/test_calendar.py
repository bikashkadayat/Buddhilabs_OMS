"""
Phase T3 Part 3 — the calendar.

A calendar is a view of dates, so the rules under test are about dates: which
window a view covers, which single kind a task is classified as, and what
happens to a task that has no date at all.
"""
import datetime

import pytest

from tasks import analytics
from tasks.models import Task

from .conftest import LIST

pytestmark = pytest.mark.django_db

Status = Task.Status
CALENDAR = f"{LIST}calendar/"


def kinds(response):
    return {e["task"]["id"]: e["kind"] for e in response.data["events"]}


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------
def test_a_day_view_covers_exactly_that_day():
    day = datetime.date(2026, 9, 15)
    assert analytics.calendar_range(day, "day") == (day, day)


def test_a_week_starts_on_sunday():
    """
    The Nepali working week. A calendar that breaks the week where the local one
    does not is subtly wrong every single time it is read.
    """
    # 2026-09-15 is a Tuesday.
    start, end = analytics.calendar_range(datetime.date(2026, 9, 15), "week")
    assert start == datetime.date(2026, 9, 13)      # the Sunday before
    assert end == datetime.date(2026, 9, 19)        # the Saturday after
    assert start.weekday() == 6                     # Monday=0, so Sunday=6


def test_a_sunday_anchors_its_own_week():
    start, end = analytics.calendar_range(datetime.date(2026, 9, 13), "week")
    assert start == datetime.date(2026, 9, 13)
    assert end == datetime.date(2026, 9, 19)


@pytest.mark.parametrize("anchor,first,last", [
    (datetime.date(2026, 9, 15), datetime.date(2026, 9, 1), datetime.date(2026, 9, 30)),
    (datetime.date(2026, 2, 10), datetime.date(2026, 2, 1), datetime.date(2026, 2, 28)),
    # A leap year: found by stepping into the next month and back, not from a
    # table of month lengths that gets February wrong every four years.
    (datetime.date(2028, 2, 10), datetime.date(2028, 2, 1), datetime.date(2028, 2, 29)),
    (datetime.date(2026, 12, 31), datetime.date(2026, 12, 1), datetime.date(2026, 12, 31)),
])
def test_a_month_view_covers_the_whole_month(anchor, first, last):
    assert analytics.calendar_range(anchor, "month") == (first, last)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def test_each_task_gets_exactly_one_kind(cast, auth, make_task, today):
    """
    A cell showing a task as both completed and upcoming is a cell nobody can
    read.
    """
    overdue = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
                        due_date=today - datetime.timedelta(days=3))
    due = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                    due_date=today)
    upcoming = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                         due_date=today + datetime.timedelta(days=5))
    done = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.COMPLETED,
                     due_date=today - datetime.timedelta(days=1))

    # Asked for one day at a time, anchored on each task's own due date. A
    # single month view would depend on which day of the month the suite runs:
    # on the 31st, "+5 days" is next month and the assertion fails for a reason
    # that has nothing to do with classification.
    def kind_of(task):
        body = auth(cast["hod"]).get(
            CALENDAR, {"view": "day", "date": task.due_date.isoformat()})
        return kinds(body)[str(task.id)]

    assert kind_of(overdue) == "overdue"
    assert kind_of(due) == "due"
    assert kind_of(upcoming) == "upcoming"
    # Finished late is COMPLETED, not overdue: the work is done, and chasing it
    # is how a metric stops being believed.
    assert kind_of(done) == "completed"


def test_a_task_with_no_due_date_is_absent(cast, auth, make_task):
    """
    Inventing a date to place it on would be a lie about when it is wanted.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=None)
    body = auth(cast["hod"]).get(CALENDAR, {"view": "month"})
    assert str(task.id) not in kinds(body)


def test_the_counts_agree_with_the_events(cast, auth, make_task, today):
    make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
              due_date=today)
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              due_date=today - datetime.timedelta(days=2))

    body = auth(cast["hod"]).get(CALENDAR, {"view": "month"}).data
    counted = body["counts"]
    for kind in ("due", "overdue", "completed", "upcoming"):
        actual = sum(1 for e in body["events"] if e["kind"] == kind)
        assert counted[kind] == actual, kind


# ---------------------------------------------------------------------------
# The window is the server's decision
# ---------------------------------------------------------------------------
def test_a_task_outside_the_window_is_not_returned(cast, auth, make_task, today):
    inside = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                       due_date=today)
    outside = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                        due_date=today + datetime.timedelta(days=120))

    mapping = kinds(auth(cast["hod"]).get(CALENDAR, {"view": "day"}))
    assert str(inside.id) in mapping
    assert str(outside.id) not in mapping


def test_the_anchor_date_can_be_chosen(cast, auth, make_task, today):
    future = today + datetime.timedelta(days=40)
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=future)
    body = auth(cast["hod"]).get(CALENDAR,
                                 {"view": "day", "date": future.isoformat()})
    assert str(task.id) in kinds(body)
    assert body.data["anchor"] == future


@pytest.mark.parametrize("params", [
    {"view": "decade"},
    {"view": "month", "date": "not-a-date"},
    {"view": "month", "date": "2026-13-45"},
])
def test_a_nonsense_window_is_refused_rather_than_guessed(cast, auth, params):
    assert auth(cast["hod"]).get(CALENDAR, params).status_code == 400


def test_the_calendar_defaults_to_this_month(cast, auth, today):
    body = auth(cast["hod"]).get(CALENDAR).data
    assert body["view"] == "month"
    assert body["start"] == today.replace(day=1)


# ---------------------------------------------------------------------------
# Scoping
# ---------------------------------------------------------------------------
def test_the_calendar_never_shows_a_task_the_caller_cannot_open(cast, auth,
                                                                make_task, today):
    mine = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=today)
    theirs = make_task(cast["hod"], [cast["peer"]], status=Status.ASSIGNED,
                       due_date=today)

    mapping = kinds(auth(cast["employee"]).get(CALENDAR, {"view": "day"}))
    assert str(mine.id) in mapping
    assert str(theirs.id) not in mapping
