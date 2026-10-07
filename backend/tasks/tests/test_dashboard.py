"""
The dashboard, per role, and its one hard promise:

    A TILE NEVER COUNTS A TASK THE CALLER CANNOT OPEN.

Every count is computed over the caller's own visible set. The test that matters
most here is not that a number is right in isolation — it is that the number
agrees with the list the tile links to.
"""
import datetime

import pytest

from tasks.models import Task

from .conftest import LIST

pytestmark = pytest.mark.django_db

DASHBOARD = f"{LIST}dashboard/"
Status = Task.Status


# ---------------------------------------------------------------------------
# Employee
# ---------------------------------------------------------------------------
def test_the_employee_tiles_are_the_four_the_specification_names(
        cast, auth, make_task, today, tomorrow):
    employee = cast["employee"]
    make_task(cast["hod"], [employee], status=Status.IN_PROGRESS, due_date=tomorrow)
    make_task(cast["hod"], [employee], status=Status.ASSIGNED, due_date=today)
    make_task(cast["hod"], [employee], status=Status.IN_PROGRESS,
              due_date=today - datetime.timedelta(days=3))
    make_task(cast["hod"], [employee], reviewer=cast["hod"], status=Status.CLOSED)

    body = auth(employee).get(DASHBOARD).data
    assert body["my_tasks"] == 3          # three live, the closed one excluded
    assert body["due_today"] == 1
    assert body["overdue"] == 1
    assert body["completed"] == 1


def test_an_employee_never_sees_another_persons_work_in_a_tile(
        cast, auth, make_task):
    make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    body = auth(cast["employee"]).get(DASHBOARD).data
    assert body["my_tasks"] == 0
    assert body["by_status"] == []


def test_an_employee_gets_no_team_or_organisation_tiles(cast, auth, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    body = auth(cast["employee"]).get(DASHBOARD).data
    for absent in ("team_tasks", "team_completion_percent", "org_open",
                   "by_department"):
        assert absent not in body, absent


def test_the_badge_count_matches_the_needs_me_list(cast, auth, make_task):
    """
    The sidebar badge reads `needs_my_action`. A badge that disagrees with the
    list it opens is the fastest way to make people stop trusting both.
    """
    make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.CLOSED)

    badge = auth(cast["employee"]).get(DASHBOARD).data["needs_my_action"]
    listed = auth(cast["employee"]).get(LIST, {"scope": "needs_me"}).data["count"]
    assert badge == listed == 2


# ---------------------------------------------------------------------------
# Department Head
# ---------------------------------------------------------------------------
def test_the_department_head_tiles_are_team_completion_and_pending_review(
        cast, auth, make_task, departments):
    eng = departments["engineering"]
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              department=eng)
    make_task(cast["hod"], [cast["peer"]], reviewer=cast["hod"],
              status=Status.UNDER_REVIEW, department=eng)
    make_task(cast["hod"], [cast["peer"]], reviewer=cast["hod"],
              status=Status.CLOSED, department=eng)

    body = auth(cast["hod"]).get(DASHBOARD).data
    assert body["team_tasks"] == 2            # in progress + under review
    assert body["team_completed"] == 1
    assert body["pending_review"] == 1
    assert body["team_completion_percent"] == 33   # 1 of 3


def test_a_department_head_counts_only_their_own_department(
        cast, auth, make_task, departments):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              department=departments["engineering"])
    make_task(cast["other_hod"], [cast["outsider"]], status=Status.IN_PROGRESS,
              department=departments["finance"])
    assert auth(cast["hod"]).get(DASHBOARD).data["team_tasks"] == 1


def test_completion_percent_is_zero_rather_than_a_division_by_zero(cast, auth):
    body = auth(cast["hod"]).get(DASHBOARD).data
    assert body["team_completion_percent"] == 0
    assert body["team_tasks"] == 0


def test_a_department_head_gets_no_organisation_tiles(cast, auth, make_task,
                                                      departments):
    make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
              department=departments["engineering"])
    body = auth(cast["hod"]).get(DASHBOARD).data
    assert "team_tasks" in body
    assert "org_open" not in body


# ---------------------------------------------------------------------------
# HR and Admin
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("who", ["hr", "admin"])
def test_hr_and_admin_get_the_organisation_summary(cast, auth, make_task,
                                                   departments, who):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              department=departments["engineering"])
    make_task(cast["other_hod"], [cast["outsider"]], reviewer=cast["other_hod"],
              status=Status.CLOSED, department=departments["finance"])

    body = auth(cast[who]).get(DASHBOARD).data
    assert body["org_total"] == 2
    assert body["org_open"] == 1
    assert body["org_completed"] == 1
    assert body["org_completion_percent"] == 50
    labels = {row["label"] for row in body["by_department"]}
    assert {"Engineering", "Finance"} <= labels


def test_the_organisation_summary_excludes_other_peoples_drafts(
        cast, auth, make_task, departments):
    """A draft is not organisational work yet; counting it inflates the total."""
    make_task(cast["hod"], [cast["employee"]], department=departments["engineering"])
    assert auth(cast["hr"]).get(DASHBOARD).data["org_total"] == 0


def test_hr_keeps_their_own_employee_tiles_as_well(cast, auth, make_task):
    """
    An HR officer is also an employee with tasks of their own. A dashboard that
    replaces the personal tiles with the organisational ones is why people keep
    a second list on paper.
    """
    make_task(cast["admin"], [cast["hr"]], status=Status.ASSIGNED)
    body = auth(cast["hr"]).get(DASHBOARD).data
    assert body["my_tasks"] == 1
    assert "org_open" in body


# ---------------------------------------------------------------------------
# Breakdowns
# ---------------------------------------------------------------------------
def test_the_status_and_priority_breakdowns_are_labelled_and_scoped(
        cast, auth, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)

    body = auth(cast["employee"]).get(DASHBOARD).data
    by_status = {row["value"]: row for row in body["by_status"]}
    assert by_status["assigned"]["count"] == 1
    assert by_status["assigned"]["label"] == "Assigned"
    assert sum(row["count"] for row in body["by_priority"]) == 2


def test_the_dashboard_requires_authentication(api):
    assert api.get(DASHBOARD).status_code in (401, 403)


def test_the_breakdown_does_not_multiply_a_task_by_its_assignees(cast, auth,
                                                                 make_task):
    """
    The visible-task filter LEFT JOINs the assignee table, so a task with three
    assignees produces three joined rows for anyone matched by one of the other
    OR branches — here, the creator. A plain COUNT over that counts the task
    three times, and the tile then reports more tasks than exist.

    One task, three assignees, counted once.
    """
    make_task(cast["hod"], [cast["employee"], cast["peer"], cast["hr"]],
              status=Status.ASSIGNED)
    body = auth(cast["hod"]).get(DASHBOARD).data
    assert sum(row["count"] for row in body["by_status"]) == 1
    assert sum(row["count"] for row in body["by_priority"]) == 1
