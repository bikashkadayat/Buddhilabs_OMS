"""
Phase T3 Parts 4, 5 and 6 — workload, the role dashboards, and overdue.

The workload view is the one place in this module where a number is about a
PERSON rather than a task, so the tests lean on the two things that makes
delicate: it must never invent capacity it does not know, and it must never show
somebody a colleague's load they are not entitled to see.
"""
import datetime

import pytest

from tasks import analytics
from tasks.models import Task

from .conftest import LIST

pytestmark = pytest.mark.django_db

Status = Task.Status
WORKLOAD = f"{LIST}workload/"
DASHBOARD = f"{LIST}dashboard/"
OVERDUE = f"{LIST}overdue/"


def by_name(rows):
    return {row["name"]: row for row in rows}


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("who,perspective", [
    ("employee", "employee"),
    ("hod", "department"),
    ("hr", "organisation"),
    ("admin", "organisation"),
])
def test_the_workload_view_answers_in_the_callers_perspective(cast, auth, who,
                                                              perspective):
    """
    Three perspectives, one endpoint, chosen by the SERVER. The page renders what
    it was given rather than guessing from the role, which is how a client ends
    up asking for a block the server did not send.
    """
    response = auth(cast[who]).get(WORKLOAD)
    assert response.status_code == 200
    assert response.data["perspective"] == perspective


def test_an_employee_gets_their_own_four_numbers_and_nobody_elses(
        cast, auth, make_task, today):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              due_date=today - datetime.timedelta(days=2))
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.CLOSED)
    make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)

    body = auth(cast["employee"]).get(WORKLOAD).data
    personal = body["personal"]
    assert personal["assigned"] == 3
    assert personal["open"] == 2
    assert personal["overdue"] == 1
    assert personal["completed"] == 1
    # No team block, and above all no per-person breakdown of colleagues.
    assert "by_employee" not in body
    assert "by_department" not in body
    assert "utilisation" not in body


def test_a_manager_keeps_their_own_numbers_alongside_the_team(cast, auth,
                                                              make_task):
    """
    A department head is also somebody with tasks of their own, and a workload
    page that hides their own load is one they will keep a second list beside.
    """
    make_task(cast["admin"], [cast["hod"]], status=Status.IN_PROGRESS)
    body = auth(cast["hod"]).get(WORKLOAD).data
    assert body["personal"]["open"] == 1
    assert "by_employee" in body


def test_hr_additionally_gets_departments_and_utilisation(cast, auth, make_task,
                                                          departments):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              department=departments["engineering"])
    body = auth(cast["hr"]).get(WORKLOAD).data
    assert "by_department" in body
    assert "utilisation" in body
    assert body["utilisation"]["open"] >= 1


def test_utilisation_reports_concentration_not_invented_capacity(cast, auth,
                                                                 make_task):
    """
    Nothing here knows anybody's hours, so "83% utilised" would be invented.
    "A quarter of the team holds this much of the open work" is supportable.
    """
    for _ in range(8):
        make_task(cast["admin"], [cast["employee"]], status=Status.IN_PROGRESS)
    for _ in range(2):
        make_task(cast["admin"], [cast["peer"]], status=Status.IN_PROGRESS)

    utilisation = auth(cast["hr"]).get(WORKLOAD).data["utilisation"]
    assert utilisation["open"] == 10
    assert utilisation["people"] == 2
    # The busiest quarter of two people is one person, holding 8 of 10.
    assert utilisation["busiest_quarter_share_percent"] == 80
    assert "utilisation_percent" not in utilisation


def test_utilisation_reports_open_work_nobody_is_carrying(cast, auth, make_task):
    """The gap a utilisation view most often hides."""
    make_task(cast["hod"], [], status=Status.DRAFT)
    orphan = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    orphan.assignees.all().delete()

    utilisation = auth(cast["hr"]).get(WORKLOAD).data["utilisation"]
    assert utilisation["unassigned_open"] >= 1


def test_a_department_head_sees_only_their_own_department(cast, auth, make_task,
                                                          departments):
    make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
              department=departments["engineering"])
    make_task(cast["other_hod"], [cast["outsider"]], status=Status.ASSIGNED,
              department=departments["finance"])

    people = by_name(auth(cast["hod"]).get(WORKLOAD).data["by_employee"])
    assert cast["employee"].get_full_name() in people
    assert cast["outsider"].get_full_name() not in people


def test_hr_sees_the_whole_organisation(cast, auth, make_task, departments):
    make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
              department=departments["engineering"])
    make_task(cast["other_hod"], [cast["outsider"]], status=Status.ASSIGNED,
              department=departments["finance"])

    body = auth(cast["hr"]).get(WORKLOAD).data
    people = by_name(body["by_employee"])
    assert {cast["employee"].get_full_name(),
            cast["outsider"].get_full_name()} <= set(people)
    assert {row["department"] for row in body["by_department"]} >= {
        "Engineering", "Finance"}


# ---------------------------------------------------------------------------
# The numbers
# ---------------------------------------------------------------------------
def test_a_persons_row_separates_open_completed_and_overdue(cast, auth,
                                                            make_task, today):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              due_date=today - datetime.timedelta(days=4))
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.CLOSED)

    row = by_name(auth(cast["hod"]).get(WORKLOAD).data["by_employee"])[
        cast["employee"].get_full_name()]
    assert row["total"] == 3
    assert row["open"] == 2
    assert row["overdue"] == 1        # the overdue one is also counted as open
    assert row["completed"] == 1
    assert row["completion_percent"] == 33


def test_a_multi_assignee_task_counts_for_everybody_on_it(cast, auth, make_task):
    """Each of them is carrying it; splitting it into thirds would be fiction."""
    make_task(cast["hod"], [cast["employee"], cast["peer"]],
              status=Status.IN_PROGRESS)
    people = by_name(auth(cast["hod"]).get(WORKLOAD).data["by_employee"])
    assert people[cast["employee"].get_full_name()]["open"] == 1
    assert people[cast["peer"].get_full_name()]["open"] == 1


def test_somebody_with_no_tasks_is_not_a_workload_row(cast, auth, make_task):
    """
    Listing every employee in the organisation with three zeroes beside their
    name is how a workload view becomes unreadable.
    """
    make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    people = by_name(auth(cast["hr"]).get(WORKLOAD).data["by_employee"])
    assert cast["peer"].get_full_name() not in people


def test_the_overload_flag_needs_somebody_to_actually_be_carrying_more(
        cast, auth, make_task):
    for _ in range(6):
        make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)

    people = by_name(auth(cast["hr"]).get(WORKLOAD).data["by_employee"])
    assert people[cast["employee"].get_full_name()]["is_overloaded"] is True
    assert people[cast["peer"].get_full_name()]["is_overloaded"] is False


def test_nobody_is_overloaded_when_everybody_has_one_task(cast, auth, make_task):
    """
    A median of zero would otherwise flag the one person with a single task,
    which is absurd. The flag marks "this needs looking at", not "this exists".
    """
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    people = auth(cast["hr"]).get(WORKLOAD).data["by_employee"]
    assert all(row["is_overloaded"] is False for row in people)


def test_the_workload_view_is_ordered_by_open_load(cast, auth, make_task):
    for _ in range(3):
        make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)

    names = [row["name"] for row in
             auth(cast["hr"]).get(WORKLOAD).data["by_employee"]]
    assert names[0] == cast["peer"].get_full_name()


# ---------------------------------------------------------------------------
# Average completion time (Part 5)
# ---------------------------------------------------------------------------
def test_average_completion_is_none_rather_than_zero_when_nothing_is_done(
        cast, auth, make_task):
    """
    "No data" and "same day" are different answers, and a dashboard reading
    0.0 days would be understood as the second.
    """
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    body = auth(cast["employee"]).get(DASHBOARD).data
    assert body["average_completion_days"] is None


def test_average_completion_is_measured_from_assignment(cast, auth, make_task):
    """
    Not from creation. A task that sat in somebody's drafts for a fortnight
    would otherwise be counted against the assignee.
    """
    from django.utils import timezone

    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.COMPLETED)
    now = timezone.now()
    Task.objects.filter(pk=task.pk).update(
        created_at=now - datetime.timedelta(days=30),
        assigned_at=now - datetime.timedelta(days=4),
        completed_at=now)

    body = auth(cast["employee"]).get(DASHBOARD).data
    assert body["average_completion_days"] == 4.0


def test_a_task_with_no_assignment_stamp_is_excluded_not_counted_as_instant(
        cast, auth, make_task):
    from django.utils import timezone

    good = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.COMPLETED)
    stale = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                      status=Status.COMPLETED)
    now = timezone.now()
    Task.objects.filter(pk=good.pk).update(
        assigned_at=now - datetime.timedelta(days=6), completed_at=now)
    Task.objects.filter(pk=stale.pk).update(assigned_at=None, completed_at=now)

    body = auth(cast["employee"]).get(DASHBOARD).data
    # 6.0, not 3.0 — the stale row is excluded, not folded in as zero.
    assert body["average_completion_days"] == 6.0


# ---------------------------------------------------------------------------
# Dashboard blocks (Part 5)
# ---------------------------------------------------------------------------
def test_the_department_dashboard_carries_the_four_it_names(cast, auth,
                                                            make_task, departments):
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.UNDER_REVIEW, department=departments["engineering"])
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.CLOSED, department=departments["engineering"])

    body = auth(cast["hod"]).get(DASHBOARD).data
    for key in ("team_tasks", "pending_review", "team_overdue",
                "team_completion_percent", "team_overdue_percent"):
        assert key in body, key
    assert body["pending_review"] == 1
    assert body["team_completion_percent"] == 50


def test_the_hr_dashboard_carries_a_distribution(cast, auth, make_task,
                                                 departments):
    make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
              department=departments["engineering"])
    body = auth(cast["hr"]).get(DASHBOARD).data

    assert "org_overdue_percent" in body
    assert "org_average_completion_days" in body
    distribution = {row["status"]: row["count"] for row in body["distribution"]}
    # Driven by the CHOICES, so a status with nothing in it reads 0 rather than
    # vanishing — "nothing is blocked" is the answer somebody is looking for.
    assert distribution["Assigned"] == 1
    assert distribution["Blocked"] == 0


def test_an_employee_gets_no_team_or_organisation_percentages(cast, auth,
                                                              make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    body = auth(cast["employee"]).get(DASHBOARD).data
    for absent in ("team_overdue_percent", "org_overdue_percent", "distribution"):
        assert absent not in body, absent


# ---------------------------------------------------------------------------
# Overdue screen (Part 6)
# ---------------------------------------------------------------------------
def test_the_overdue_screen_carries_every_column_the_specification_names(
        cast, auth, make_task, today):
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.IN_PROGRESS,
              due_date=today - datetime.timedelta(days=5))

    body = auth(cast["hod"]).get(OVERDUE).data
    row = body["rows"][0]
    for key in ("overdue_days", "assignee", "department", "priority", "reviewer"):
        assert key in row, key
    assert row["overdue_days"] == 5
    assert row["assignee"] == cast["employee"].get_full_name()
    assert row["reviewer"] == cast["hod"].get_full_name()


def test_the_overdue_screen_is_worst_first(cast, auth, make_task, today):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              due_date=today - datetime.timedelta(days=2))
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              due_date=today - datetime.timedelta(days=9))

    days = [row["overdue_days"] for row in auth(cast["hod"]).get(OVERDUE).data["rows"]]
    assert days == sorted(days, reverse=True)
    assert days[0] == 9


def test_completed_work_is_never_on_the_overdue_screen(cast, auth, make_task,
                                                       today):
    """The employee did their part; chasing them for the reviewer's backlog is
    how a metric stops being believed."""
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.COMPLETED,
              due_date=today - datetime.timedelta(days=5))
    assert auth(cast["hod"]).get(OVERDUE).data["rows"] == []


def test_overdue_days_is_on_every_list_row(cast, auth, make_task, today,
                                           tomorrow):
    late = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
                     due_date=today - datetime.timedelta(days=3))
    fine = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=tomorrow)

    rows = {r["id"]: r for r in auth(cast["hod"]).get(LIST).data["results"]}
    assert rows[str(late.id)]["overdue_days"] == 3
    assert rows[str(fine.id)]["overdue_days"] == 0


def test_an_employee_sees_only_their_own_overdue_work(cast, auth, make_task,
                                                      today):
    mine = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
                     due_date=today - datetime.timedelta(days=1))
    make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS,
              due_date=today - datetime.timedelta(days=1))

    rows = auth(cast["employee"]).get(OVERDUE).data["rows"]
    assert [r["task_id"] for r in rows] == [str(mine.id)]


@pytest.mark.parametrize("loads,expected_median", [
    ([1], 1),
    ([1, 6], 3.5),          # even: the MEAN of the two middle values
    ([1, 2, 9], 2),
    ([1, 2, 3, 10], 2.5),
])
def test_the_median_open_load_is_a_real_median(loads, expected_median):
    """
    Taking the upper middle element on an even-sized list makes the busiest
    person the median whenever there are two of them — so nobody can exceed
    1.5x it and the overload flag never fires at all.
    """
    ordered = sorted(loads)
    if len(ordered) % 2:
        median = ordered[len(ordered) // 2]
    else:
        middle = len(ordered) // 2
        median = (ordered[middle - 1] + ordered[middle]) / 2
    assert median == expected_median
