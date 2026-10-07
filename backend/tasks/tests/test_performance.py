"""
Phase T2.10 — query-count stability.

Board cards and a task detail page both render a lot of related data. The
failure mode this guards is not slowness in the abstract: it is the N+1, where
a page costs a fixed number of queries with three rows on it and a hundred with
fifty. That kind of regression is invisible in development and fatal in
production, and it is introduced by a one-line change — a `.filter()` on a
related manager where a list comprehension over the prefetch was intended.

The numbers below are ceilings, not targets. They are deliberately loose enough
not to fail on an unrelated change to authentication or throttling, and tight
enough that an N+1 blows straight through them.
"""
import pytest
from django.test.utils import CaptureQueriesContext
from django.db import connection

from tasks.models import Task, TaskAttachment, TaskChecklistItem, TaskComment
from tasks.services import user_snapshot

from .conftest import LIST
from tenancy.stamping import stamp_all

pytestmark = pytest.mark.django_db

Status = Task.Status


def furnish(task, author, *, comments=3, items=4, files=2):
    """Give a task a realistic amount of content, without going near the API."""
    TaskChecklistItem.objects.bulk_create(stamp_all([
        TaskChecklistItem(task=task, text=f"Item {i}", position=i,
                          is_done=(i % 2 == 0))
        for i in range(items)
    ]))
    for i in range(comments):
        parent = TaskComment.objects.create(
            task=task, author=author, author_name=user_snapshot(author)["name"],
            body=f"Comment {i}")
        TaskComment.objects.create(
            task=task, parent=parent, author=author,
            author_name=user_snapshot(author)["name"], body=f"Reply to {i}")
    TaskAttachment.objects.bulk_create(stamp_all([
        TaskAttachment(task=task, link_url=f"https://example.org/{i}",
                       original_name=f"link-{i}", is_evidence=(i % 2 == 0),
                       uploaded_by=author,
                       uploaded_by_name=user_snapshot(author)["name"])
        for i in range(files)
    ]))


def build(cast, make_task, count, **kwargs):
    tasks = []
    for _ in range(count):
        task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                         **kwargs)
        furnish(task, cast["employee"])
        tasks.append(task)
    return tasks


def count_queries(client, url, params=None):
    with CaptureQueriesContext(connection) as captured:
        response = client.get(url, params or {})
        assert response.status_code == 200, response.data
    return len(captured.captured_queries), response


# ---------------------------------------------------------------------------
# The list / board
# ---------------------------------------------------------------------------
def test_the_list_costs_the_same_for_three_rows_as_for_twenty(
        cast, auth, make_task):
    """
    THE test in this file. Everything a card shows — assignees, checklist tally,
    comment and attachment counts — is either annotated on the queryset or read
    from a prefetch, so the page is a fixed number of queries regardless of how
    many cards are on it.
    """
    build(cast, make_task, 3)
    small, _ = count_queries(auth(cast["hod"]), LIST)

    build(cast, make_task, 17)
    large, response = count_queries(auth(cast["hod"]), LIST)

    assert len(response.data["results"]) == 20
    assert large == small, (
        f"{small} queries for 3 rows but {large} for 20 — something in the list "
        f"serializer is querying per row.")


def test_a_card_carries_its_counts_without_a_query_each(cast, auth, make_task):
    task = build(cast, make_task, 1)[0]
    _, response = count_queries(auth(cast["hod"]), LIST)
    card = next(r for r in response.data["results"] if r["id"] == str(task.id))

    assert card["comment_count"] == 6        # 3 top-level + 3 replies
    assert card["attachment_count"] == 1     # one general
    assert card["evidence_count"] == 1       # one evidence
    assert card["checklist_total"] == 4
    assert card["checklist_done"] == 2


def test_the_counts_are_not_multiplied_by_the_assignees(cast, auth, make_task):
    """
    The visibility filter LEFT JOINs the assignee table. Without distinct=True a
    task with three assignees would report three times its real comment count —
    the same join-multiplication bug the dashboard aggregates had.
    """
    task = make_task(cast["hod"], [cast["employee"], cast["peer"], cast["hr"]],
                     status=Status.ASSIGNED)
    furnish(task, cast["employee"], comments=2, files=2)

    rows = auth(cast["hod"]).get(LIST).data["results"]
    card = next(r for r in rows if r["id"] == str(task.id))
    assert card["comment_count"] == 4        # 2 top-level + 2 replies
    assert card["attachment_count"] == 1
    assert card["evidence_count"] == 1


def test_the_list_stays_bounded_at_fifty_rows(cast, auth, make_task,
                                              tenant_binding_queries):
    """A full page of the board, which is the realistic worst case.

    The ceiling carries the tenant binding rather than excluding it: under
    row-level security the middleware writes `app.current_org` and
    `app.platform` for the request, which is one round trip. It is a constant
    and this file is mostly about growth, but an absolute ceiling should count
    everything the request actually costs.
    """
    build(cast, make_task, 50)
    queries, response = count_queries(auth(cast["hod"]), LIST)
    assert len(response.data["results"]) == 50
    ceiling = 20 + tenant_binding_queries
    assert queries < ceiling, f"{queries} queries for one page of 50 cards"


# ---------------------------------------------------------------------------
# The detail page
# ---------------------------------------------------------------------------
def test_the_detail_page_is_bounded(cast, auth, make_task):
    """
    Seven sections — information, progress, checklist, comments, attachments,
    evidence, timeline — off one prefetched task.
    """
    task = build(cast, make_task, 1)[0]
    queries, response = count_queries(auth(cast["hod"]), f"{LIST}{task.id}/")
    assert queries < 25, f"{queries} queries for one task detail page"
    for section in ("checklist", "checklist_groups", "attachments", "evidence",
                    "comments", "timeline"):
        assert section in response.data


def test_a_longer_discussion_does_not_cost_more_queries(cast, auth, make_task):
    """
    Comments, their replies and both sides' mentions come from one prefetch. A
    query per comment would be invisible on a task with two and crippling on one
    with forty.
    """
    small_task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    furnish(small_task, cast["employee"], comments=2)
    small, _ = count_queries(auth(cast["hod"]), f"{LIST}{small_task.id}/")

    big_task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    furnish(big_task, cast["employee"], comments=20)
    big, response = count_queries(auth(cast["hod"]), f"{LIST}{big_task.id}/")

    assert len(response.data["comments"]) == 20
    assert big == small, (
        f"{small} queries for 2 comments but {big} for 20 — the comment "
        f"serializer is querying per row.")


def test_a_grouped_checklist_does_not_cost_a_query_per_group(cast, auth,
                                                             make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    auth(cast["hod"]).post(f"{LIST}{task.id}/checklist/", {
        "groups": [{"title": f"Section {i}", "items": [f"Line {i}.{j}"
                                                        for j in range(4)]}
                   for i in range(2)],
    }, format="json")
    small, _ = count_queries(auth(cast["hod"]), f"{LIST}{task.id}/")

    auth(cast["hod"]).post(f"{LIST}{task.id}/checklist/", {
        "groups": [{"title": f"Section {i}", "items": [f"Line {i}.{j}"
                                                        for j in range(4)]}
                   for i in range(8)],
    }, format="json")
    large, response = count_queries(auth(cast["hod"]), f"{LIST}{task.id}/")

    assert len(response.data["checklist_groups"]) == 8
    assert large == small


# ---------------------------------------------------------------------------
# The dashboard
# ---------------------------------------------------------------------------
def test_the_dashboard_is_bounded_for_an_organisation_wide_reader(
        cast, auth, make_task, tenant_binding_queries):
    build(cast, make_task, 25)
    queries, _ = count_queries(auth(cast["hr"]), f"{LIST}dashboard/")
    ceiling = 30 + tenant_binding_queries
    assert queries < ceiling, f"{queries} queries for the HR dashboard"


# ---------------------------------------------------------------------------
# Phase T3 — board, calendar, workload, reports
# ---------------------------------------------------------------------------
def test_the_board_costs_the_same_for_five_cards_as_for_forty(cast, auth,
                                                              make_task):
    """
    A board is the densest page in the module: six columns of cards, each
    carrying assignees, a checklist tally and three counts. Every one of those
    is annotated or prefetched, so the cost is fixed.
    """
    build(cast, make_task, 5)
    small, _ = count_queries(auth(cast["hod"]), f"{LIST}board/")

    build(cast, make_task, 35)
    large, response = count_queries(auth(cast["hod"]), f"{LIST}board/")

    assert response.data["total"] == 40
    assert large == small, (
        f"{small} queries for 5 cards but {large} for 40 — the board serializer "
        f"is querying per card.")


def test_the_calendar_is_bounded(cast, auth, make_task, today):
    for _ in range(30):
        task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                         due_date=today)
        furnish(task, cast["employee"], comments=1, files=1)

    queries, response = count_queries(auth(cast["hod"]), f"{LIST}calendar/",
                                      {"view": "day"})
    assert len(response.data["events"]) == 30
    assert queries < 20, f"{queries} queries for a day with 30 tasks on it"


def test_the_workload_view_does_not_query_per_person(cast, auth, make_task,
                                                     tenant_binding_queries):
    """
    Rows come from one prefetched pass over the tasks, not a query per employee —
    which is what an organisation-wide workload view would otherwise become.
    """
    for user_key in ("employee", "peer", "hr", "hod"):
        for _ in range(4):
            make_task(cast["admin"], [cast[user_key]], status=Status.IN_PROGRESS)

    queries, response = count_queries(auth(cast["hr"]), f"{LIST}workload/")
    assert len(response.data["by_employee"]) == 4
    ceiling = 20 + tenant_binding_queries
    assert queries < ceiling, f"{queries} queries for a workload of 4 people"


@pytest.mark.parametrize("slug", ["completion", "status", "department",
                                  "workload", "overdue"])
def test_every_report_is_bounded(cast, auth, make_task, today, slug):
    for _ in range(20):
        make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                  status=Status.IN_PROGRESS, due_date=today)
    queries, _ = count_queries(auth(cast["hr"]), f"{LIST}reports/{slug}/")
    assert queries < 25, f"{queries} queries for the {slug} report"


def test_the_filter_options_endpoint_is_bounded(cast, auth, make_task,
                                                tenant_binding_queries):
    build(cast, make_task, 25)
    queries, response = count_queries(auth(cast["hod"]),
                                      f"{LIST}filter-options/")
    assert response.data["assignees"]
    ceiling = 15 + tenant_binding_queries
    assert queries < ceiling, f"{queries} queries to build the filter lists"


# ---------------------------------------------------------------------------
# Phase T5 — analytics, trends and evidence
# ---------------------------------------------------------------------------
ANALYTICS = "/api/v1/task-analytics/"


@pytest.mark.parametrize("endpoint", ["executive", "health", "departments",
                                      "employees", "reviewers", "kpis"])
def test_every_analytics_endpoint_is_bounded(cast, auth, make_task, endpoint):
    """
    Analytics runs aggregate queries over the whole visible set, so the risk is
    not an N+1 per row — it is one more query per METRIC. These ceilings catch a
    metric added by looping.
    """
    build(cast, make_task, 25)
    queries, _ = count_queries(auth(cast["hr"]), f"{ANALYTICS}{endpoint}/")
    assert queries < 45, f"{queries} queries for {endpoint}"


def test_analytics_costs_the_same_for_ten_tasks_as_for_forty(cast, auth,
                                                             make_task):
    build(cast, make_task, 10)
    small, _ = count_queries(auth(cast["hr"]), f"{ANALYTICS}executive/")
    build(cast, make_task, 30)
    large, _ = count_queries(auth(cast["hr"]), f"{ANALYTICS}executive/")
    assert large == small, (
        f"{small} queries for 10 tasks but {large} for 40 — something in the "
        f"executive summary is querying per task.")


@pytest.mark.parametrize("period", ["weekly", "monthly", "quarterly"])
def test_a_trend_is_bounded_by_its_bucket_count(cast, auth, make_task, period):
    """
    A trend is inherently a few queries per bucket. The ceiling exists so a
    fifth metric per bucket is a decision rather than an accident.
    """
    build(cast, make_task, 20)
    queries, response = count_queries(auth(cast["hr"]), f"{ANALYTICS}trend/",
                                      {"period": period})
    assert len(response.data["buckets"]) == 12
    assert queries < 70, f"{queries} queries for a 12-bucket {period} trend"


def test_evidence_for_one_person_is_bounded(cast, auth, make_task):
    build(cast, make_task, 30)
    queries, _ = count_queries(auth(cast["employee"]), f"{ANALYTICS}evidence/")
    assert queries < 30, f"{queries} queries for one employee's evidence"


def test_the_snapshot_command_does_not_query_per_task(cast, make_task):
    """
    It runs nightly over everybody. A query per task would be invisible with
    thirty and crippling with thirty thousand.
    """
    from django.core.management import call_command

    build(cast, make_task, 5)
    with CaptureQueriesContext(connection) as small:
        call_command("snapshot_task_evidence", verbosity=0)
    Task.objects.all().delete()
    from tasks.models import EmployeeTaskEvidenceSnapshot

    EmployeeTaskEvidenceSnapshot.objects.all().delete()

    build(cast, make_task, 25)
    with CaptureQueriesContext(connection) as large:
        call_command("snapshot_task_evidence", verbosity=0)

    # Per EMPLOYEE, not per task — the cast has a fixed number of people, so the
    # two runs must cost the same despite five times the tasks.
    assert len(large.captured_queries) == len(small.captured_queries), (
        f"{len(small.captured_queries)} queries for 5 tasks but "
        f"{len(large.captured_queries)} for 25 — the snapshot is querying per task.")
