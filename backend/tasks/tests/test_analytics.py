"""
Phase T5 — analytics, KPIs, trends and visibility.

THE GUARDRAIL TESTS ARE THE POINT OF THIS FILE
----------------------------------------------
Phase T5's instruction is explicit: display metrics, do not generate performance
scores, do not rank employees, do not evaluate employees. That is easy to honour
on the day and easy to erode later — somebody adds a "productivity index"
because it would be convenient on one screen, and nothing fails.

So the absence is asserted, not assumed: no score-shaped key anywhere in the
employee payloads, no ordering by any metric, and no composite in the KPI
registry. Departments MAY be ranked, and that is asserted too — the distinction
between ranking a unit of work and ranking a person is the whole design.
"""
import datetime

import pytest
from django.utils import timezone

from tasks import analytics, kpi
from tasks.models import Task

from .conftest import LIST

pytestmark = pytest.mark.django_db

Status = Task.Status
ANALYTICS = "/api/v1/task-analytics/"

# Any of these appearing beside a person is the thing this phase forbids.
SCORE_WORDS = ("score", "rating", "rank", "grade", "index", "percentile",
               "band", "performance")


def visible(user):
    from tasks import permissions as perms

    queryset = Task.objects.all()
    where = perms.visible_task_filter(user)
    return queryset.filter(where).distinct() if where is not None else queryset


# ===========================================================================
# The guardrails (Part 3, Part 12)
# ===========================================================================
def test_no_kpi_is_a_composite_score():
    """
    A composite is the exact thing that turns a work tool into a scoring tool:
    the moment five honest numbers become one, it gets compared, and nobody can
    explain what it means.
    """
    for key, metric in kpi.REGISTRY.items():
        assert not any(word in key for word in SCORE_WORDS), key
        assert not any(word in metric.label.lower() for word in SCORE_WORDS), key


def test_every_kpi_ships_the_sentence_it_is_read_by():
    """A percentage with no stated denominator is the most reliable way to have
    a metric misread in a meeting."""
    for metric in kpi.REGISTRY.values():
        assert len(metric.definition) > 40, metric.key
        assert metric.unit
        assert isinstance(metric.higher_is_better, bool)


def test_the_employee_view_carries_no_score_shaped_field(cast, auth, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    rows = auth(cast["hr"]).get(f"{ANALYTICS}employees/").data["employees"]
    assert rows
    for row in rows:
        for key in row:
            assert not any(word in key.lower() for word in SCORE_WORDS), key


def test_employees_are_ordered_by_name_not_by_any_metric(cast, auth, make_task):
    """
    A list sorted by completion rate IS a ranking whatever the header says, and
    the person at the bottom of it will be asked about it.
    """
    # Give one person a perfect record and another a poor one, so a
    # metric-ordered list would differ from an alphabetical one.
    for _ in range(3):
        make_task(cast["hod"], [cast["peer"]], reviewer=cast["hod"],
                  status=Status.CLOSED)
    for _ in range(3):
        make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)

    rows = auth(cast["hr"]).get(f"{ANALYTICS}employees/").data["employees"]
    names = [r["name"] for r in rows]
    assert names == sorted(names, key=str.lower)


def test_the_employee_payload_says_out_loud_that_it_is_not_a_ranking(cast, auth,
                                                                     make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    body = auth(cast["hr"]).get(f"{ANALYTICS}employees/").data
    assert "not scored" in body["note"] or "Metrics only" in body["note"]


def test_reviewers_are_ordered_by_name_too(cast, auth, make_task):
    """A reviewer is a person; the same rule applies."""
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.UNDER_REVIEW)
    make_task(cast["hr"], [cast["employee"]], reviewer=cast["hr"],
              status=Status.UNDER_REVIEW)
    rows = auth(cast["admin"]).get(f"{ANALYTICS}reviewers/").data["reviewers"]
    names = [r["reviewer"] for r in rows]
    assert names == sorted(names, key=str.lower)


def test_departments_may_be_ranked_because_they_are_not_people(cast, auth,
                                                               make_task,
                                                               departments):
    """
    Part 1 asks for a department ranking, and it is a different act: a
    department is a unit of work with a head accountable for it.
    """
    for _ in range(4):
        make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                  status=Status.CLOSED, department=departments["engineering"])
    for _ in range(4):
        make_task(cast["other_hod"], [cast["outsider"]], status=Status.IN_PROGRESS,
                  department=departments["finance"])

    ranking = auth(cast["hr"]).get(f"{ANALYTICS}departments/").data["ranking"]
    assert ranking[0]["department"] == "Engineering"
    assert ranking[0]["position"] == 1
    assert [r["position"] for r in ranking] == list(range(1, len(ranking) + 1))


def test_a_one_task_department_cannot_top_the_ranking_unmarked(cast, auth,
                                                               make_task,
                                                               departments):
    """
    A department that finished its one task is not "100% complete" in any sense
    worth putting at the top of a list, and a ranking that lets a single task
    outrank a hundred is one nobody believes twice.
    """
    make_task(cast["other_hod"], [cast["outsider"]], reviewer=cast["other_hod"],
              status=Status.CLOSED, department=departments["finance"])
    for _ in range(5):
        make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
                  department=departments["engineering"])

    ranking = auth(cast["hr"]).get(f"{ANALYTICS}departments/").data["ranking"]
    finance = next(r for r in ranking if r["department"] == "Finance")
    assert finance["low_volume"] is True


# ===========================================================================
# Visibility (Part 12)
# ===========================================================================
@pytest.mark.parametrize("endpoint", ["employees", "reviewers"])
@pytest.mark.parametrize("who,expected", [
    ("employee", 403), ("hod", 200), ("hr", 200), ("admin", 200),
])
def test_per_person_views_are_manager_only(cast, auth, endpoint, who, expected):
    """
    Refused rather than narrowed: a per-person table of one person is not a
    per-person table, and returning one would be misleading rather than smaller.
    """
    assert auth(cast[who]).get(f"{ANALYTICS}{endpoint}/").status_code == expected


def test_an_employee_gets_the_executive_view_over_their_own_work(cast, auth,
                                                                 make_task):
    """
    Scoped, not refused: these figures over their own tasks are a legitimate
    view of their own work.
    """
    mine = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)

    body = auth(cast["employee"]).get(f"{ANALYTICS}executive/").data
    assert body["totals"]["total"] == 1
    assert body["totals"]["open"] == 1


def test_a_department_head_sees_their_own_department_only(cast, auth, make_task,
                                                          departments):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              department=departments["engineering"])
    make_task(cast["other_hod"], [cast["outsider"]], status=Status.IN_PROGRESS,
              department=departments["finance"])

    rows = auth(cast["hod"]).get(f"{ANALYTICS}departments/").data["departments"]
    assert {r["department"] for r in rows} == {"Engineering"}


def test_hr_sees_the_organisation(cast, auth, make_task, departments):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              department=departments["engineering"])
    make_task(cast["other_hod"], [cast["outsider"]], status=Status.IN_PROGRESS,
              department=departments["finance"])
    rows = auth(cast["hr"]).get(f"{ANALYTICS}departments/").data["departments"]
    assert {"Engineering", "Finance"} <= {r["department"] for r in rows}


def test_analytics_requires_authentication(api):
    assert api.get(f"{ANALYTICS}executive/").status_code in (401, 403)


# ===========================================================================
# The numbers (Parts 1, 2, 5)
# ===========================================================================
def test_drafts_are_excluded_from_every_total(cast, auth, make_task):
    """
    A task nobody has been given is not work in progress, and counting it drags
    every completion figure down with something that has not started.
    """
    make_task(cast["hod"], [cast["employee"]])              # draft
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)

    body = auth(cast["hr"]).get(f"{ANALYTICS}executive/").data
    assert body["totals"]["total"] == 1


def test_on_time_excludes_tasks_that_had_no_due_date(cast, make_task, today):
    """
    Counting them as on time inflates the figure with work nobody committed to
    a date for.
    """
    dated = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                      status=Status.COMPLETED, due_date=today)
    undated = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                        status=Status.COMPLETED, due_date=None)
    now = timezone.now()
    Task.objects.filter(pk__in=[dated.pk, undated.pk]).update(completed_at=now)

    stats = analytics.metric_inputs(visible(cast["hr"]))
    assert stats["completed_with_due_date"] == 1
    assert stats["on_time"] == 1
    assert kpi.REGISTRY["on_time_percent"].evaluate(stats) == 100


def test_work_finished_late_is_completed_not_overdue(cast, make_task, today):
    """The person did their part; a completion metric that punishes them for the
    reviewer's backlog stops being believed."""
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.COMPLETED,
                     due_date=today - datetime.timedelta(days=5))
    Task.objects.filter(pk=task.pk).update(completed_at=timezone.now())

    stats = analytics.metric_inputs(visible(cast["hr"]))
    assert stats["overdue"] == 0
    assert kpi.REGISTRY["on_time_percent"].evaluate(stats) == 0


def test_rework_is_counted_from_the_timeline(cast, make_task):
    """A task returned twice and then approved still counts as rework — the
    current status has forgotten it."""
    from tasks import workflow

    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW)
    workflow.request_rework(task, cast["hod"], "The figures are stale here.")
    workflow.submit_for_review(task, cast["employee"])
    workflow.approve_review(task, cast["hod"])

    stats = analytics.metric_inputs(visible(cast["hr"]))
    assert stats["reworked"] == 1
    assert kpi.REGISTRY["rework_percent"].evaluate(stats) == 100


def test_every_ratio_is_zero_rather_than_a_crash_on_an_empty_set(cast, auth):
    body = auth(cast["hr"]).get(f"{ANALYTICS}health/").data
    for item in body["kpis"]:
        assert item["value"] == 0
    assert body["inputs"]["total"] == 0


def test_the_health_dashboard_names_the_five_ratios(cast, auth, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    keys = {item["key"] for item in
            auth(cast["hr"]).get(f"{ANALYTICS}health/").data["kpis"]}
    assert keys == {"on_time_percent", "overdue_percent", "blocked_percent",
                    "rework_percent", "review_delay_percent"}


def test_the_executive_dashboard_carries_everything_part_one_names(cast, auth,
                                                                   make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    body = auth(cast["hr"]).get(f"{ANALYTICS}executive/").data
    for key in ("total", "completed", "open", "overdue", "blocked",
                "review_backlog", "average_completion_days"):
        assert key in body["totals"], key
    assert body["department_ranking"] is not None
    assert any(k["key"] == "completion_percent" for k in body["kpis"])


def test_department_analytics_adds_resolution_time_and_backlog(cast, auth,
                                                               make_task,
                                                               departments):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.COMPLETED, department=departments["engineering"])
    now = timezone.now()
    Task.objects.filter(pk=task.pk).update(
        assigned_at=now - datetime.timedelta(days=6), completed_at=now)
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.UNDER_REVIEW, department=departments["engineering"])

    row = next(r for r in auth(cast["hr"]).get(
        f"{ANALYTICS}departments/").data["departments"]
        if r["department"] == "Engineering")
    assert row["average_resolution_days"] == 6.0
    assert row["reviewer_backlog"] == 1


def test_reviewer_backlog_counts_every_waiting_task_not_just_the_last_one(
        cast, auth, make_task, departments):
    """
    Regression: `reviewer_backlog` was not a count.

    `Task.Meta.ordering` is `["-created_at"]`, and a `.values().annotate()` that
    does not clear it groups by `department_name, created_at` — one group per
    TASK. The dict that collects the rows then kept whichever came last, so the
    figure was "was the last-ordered task under review": 0 or 1 by luck, and
    different on SQLite and PostgreSQL because they order equal timestamps
    differently.

    THREE tasks under review is what makes this a real test. With one, a broken
    implementation returns 1 half the time and looks fine.
    """
    for _ in range(3):
        make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                  status=Status.UNDER_REVIEW,
                  department=departments["engineering"])
    # Non-draft, not under review, same department: must not be counted.
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.COMPLETED, department=departments["engineering"])
    # A different department must not leak into Engineering's figure.
    make_task(cast["other_hod"], [cast["outsider"]], reviewer=cast["other_hod"],
              status=Status.UNDER_REVIEW, department=departments["finance"])

    rows = auth(cast["hr"]).get(f"{ANALYTICS}departments/").data["departments"]
    engineering = next(r for r in rows if r["department"] == "Engineering")
    finance = next(r for r in rows if r["department"] == "Finance")

    assert engineering["reviewer_backlog"] == 3
    assert finance["reviewer_backlog"] == 1


def test_average_resolution_is_a_mean_across_every_completed_task(
        cast, auth, make_task, departments):
    """
    The same bug in the same function: the resolution query had the identical
    shape, so its "average" was the last row's value rather than a mean. Two
    tasks with different durations is the smallest case that tells them apart.
    """
    for days in (4, 8):
        task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                         status=Status.COMPLETED,
                         department=departments["engineering"])
        now = timezone.now()
        Task.objects.filter(pk=task.pk).update(
            assigned_at=now - datetime.timedelta(days=days), completed_at=now)

    row = next(r for r in auth(cast["hr"]).get(
        f"{ANALYTICS}departments/").data["departments"]
        if r["department"] == "Engineering")
    assert row["average_resolution_days"] == 6.0     # the mean, not 4 or 8


def test_an_employees_review_delay_is_reported_beside_their_completion(
        cast, auth, make_task):
    """
    A low completion rate caused by somebody else's queue is the most common way
    these numbers get misread, so the wait is reported next to them.
    """
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW)
    Task.objects.filter(pk=task.pk).update(
        submitted_at=timezone.now() - datetime.timedelta(days=9))

    row = next(r for r in auth(cast["hr"]).get(
        f"{ANALYTICS}employees/").data["employees"]
        if r["name"] == cast["employee"].get_full_name())
    assert row["review_delay_days"] == 9


# ===========================================================================
# Trends (Part 6)
# ===========================================================================
@pytest.mark.parametrize("period", ["weekly", "monthly", "quarterly"])
def test_every_period_is_supported(cast, auth, period):
    body = auth(cast["hr"]).get(f"{ANALYTICS}trend/", {"period": period}).data
    assert body["period"] == period
    assert len(body["buckets"]) == 12


def test_a_nonsense_period_is_refused(cast, auth):
    assert auth(cast["hr"]).get(
        f"{ANALYTICS}trend/", {"period": "fortnightly"}).status_code == 400


def test_empty_buckets_are_present(cast, auth, make_task):
    """
    A trend line that skips its quiet weeks is a lie about the shape of the
    curve, and the gaps are usually where the story is.
    """
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    buckets = auth(cast["hr"]).get(f"{ANALYTICS}trend/").data["buckets"]
    assert len(buckets) == 12
    assert sum(1 for b in buckets if b["created"] == 0) >= 10


def test_the_trend_says_which_bucket_is_still_running(cast, auth):
    """
    `overdue` and `blocked` are states, not events, so they mean something
    different in the current bucket. The payload says so rather than leaving a
    reader to assume otherwise.
    """
    buckets = auth(cast["hr"]).get(f"{ANALYTICS}trend/").data["buckets"]
    assert buckets[-1]["is_current"] is True
    assert all(b["is_current"] is False for b in buckets[:-1])
    assert all("as_at" in b for b in buckets)


def test_the_bucket_count_is_capped(cast, auth):
    """An uncapped bucket count scans the whole table once per bucket."""
    body = auth(cast["hr"]).get(f"{ANALYTICS}trend/", {"buckets": "500"}).data
    assert len(body["buckets"]) == 24


def test_created_is_counted_by_when_it_happened(cast, auth, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    buckets = auth(cast["hr"]).get(f"{ANALYTICS}trend/").data["buckets"]
    assert buckets[-1]["created"] == 1


# ===========================================================================
# KPI endpoint (Part 9)
# ===========================================================================
def test_the_kpi_endpoint_ships_values_and_the_catalogue(cast, auth, make_task):
    """
    So a client never holds its own copy of what a metric means — which is how
    two screens end up explaining the same number differently.
    """
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    body = auth(cast["hr"]).get(f"{ANALYTICS}kpis/").data
    assert len(body["kpis"]) == len(kpi.REGISTRY)
    assert len(body["catalogue"]) == len(kpi.REGISTRY)
    assert all("definition" in item for item in body["kpis"])
    assert "inputs" in body        # the raw counts every figure came from
