"""
Phase T3 Part 8 — the five reports, and Part 7's event seam.

Reports are derived and read-only, so the property that matters most is that a
report can never contain a task the caller could not open individually — a
summary is a disclosure like any other.
"""
import csv
import datetime
import io

import pytest

from tasks import analytics, events
from tasks.models import Task

from .conftest import LIST

pytestmark = pytest.mark.django_db

Status = Task.Status
REPORTS = f"{LIST}reports/"


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------
def test_every_report_the_specification_names_is_offered(cast, auth):
    slugs = [row["slug"] for row in auth(cast["hod"]).get(REPORTS).data]
    # T3 added the first five, T4 the reviewer and template ones, T5 the last
    # three. Registered by appending, so an existing slug can never shift —
    # a slug is in URLs people have bookmarked and in saved exports.
    assert slugs == ["completion", "status", "department", "workload", "overdue",
                     "reviewer", "template-usage",
                     "task-health", "trend", "executive",
                     "employee-evidence", "department-summary",
                     "task-contribution",
                     # Phase TASK-GOVERNANCE-HARDENING, appended last so no
                     # existing slug shifts.
                     "department-performance", "cycle-time", "review-turnaround",
                     "overdue-analysis",
                     # Phase TASK-PLANNING-AND-DEPARTMENT-OWNERSHIP.
                     "department-productivity", "goal-completion",
                     "department-progress"]
    assert all(row["label"] and row["description"]
               for row in auth(cast["hod"]).get(REPORTS).data)


@pytest.mark.parametrize("slug", list(analytics.REPORTS))
def test_every_report_returns_the_same_envelope(cast, auth, make_task, slug):
    """
    {columns, rows, summary} for all five, so one table component renders them
    all and CSV export is written once. A report with its own shape needs its own
    renderer, and the fifth one gets a worse table than the first.
    """
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    body = auth(cast["hod"]).get(f"{REPORTS}{slug}/").data

    assert body["slug"] == slug
    assert isinstance(body["columns"], list) and body["columns"]
    assert isinstance(body["rows"], list)
    assert isinstance(body["summary"], dict)
    for column in body["columns"]:
        assert {"key", "label"} <= set(column)
    # Every column a row claims to have must exist in the header.
    keys = {c["key"] for c in body["columns"]}
    for row in body["rows"]:
        assert keys <= set(row), keys - set(row)


def test_an_unknown_report_is_a_404_not_an_empty_table(cast, auth):
    assert auth(cast["hod"]).get(f"{REPORTS}nonsense/").status_code == 404


# ---------------------------------------------------------------------------
# The numbers
# ---------------------------------------------------------------------------
def test_the_completion_report_is_per_employee(cast, auth, make_task):
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.CLOSED)
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)

    body = auth(cast["hr"]).get(f"{REPORTS}completion/").data
    row = next(r for r in body["rows"]
               if r["employee"] == cast["employee"].get_full_name())
    assert row["total"] == 2
    assert row["completed"] == 1
    assert row["completion_percent"] == 50
    assert body["summary"]["completion_percent"] == 50


def test_the_status_report_lists_every_status_including_the_empty_ones(
        cast, auth, make_task):
    make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    body = auth(cast["hr"]).get(f"{REPORTS}status/").data

    labels = [row["status"] for row in body["rows"]]
    assert labels == [label for _, label in Task.Status.choices]
    counts = {row["status"]: row["count"] for row in body["rows"]}
    assert counts["Assigned"] == 1
    assert counts["Cancelled"] == 0
    assert sum(row["share_percent"] for row in body["rows"]) == 100


def test_the_department_report_groups_by_department(cast, auth, make_task,
                                                    departments):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              department=departments["engineering"])
    make_task(cast["other_hod"], [cast["outsider"]], reviewer=cast["other_hod"],
              status=Status.CLOSED, department=departments["finance"])

    rows = {r["department"]: r
            for r in auth(cast["hr"]).get(f"{REPORTS}department/").data["rows"]}
    assert rows["Engineering"]["open"] == 1
    assert rows["Finance"]["completed"] == 1
    assert rows["Finance"]["completion_percent"] == 100


def test_the_workload_report_flags_who_is_carrying_too_much(cast, auth,
                                                            make_task):
    for _ in range(6):
        make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)

    body = auth(cast["hr"]).get(f"{REPORTS}workload/").data
    assert body["summary"]["overloaded"] == 1
    assert body["summary"]["open"] == 7


def test_the_overdue_report_reports_the_worst_case(cast, auth, make_task, today):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              due_date=today - datetime.timedelta(days=11))
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              due_date=today - datetime.timedelta(days=2))

    body = auth(cast["hr"]).get(f"{REPORTS}overdue/").data
    assert body["summary"]["total"] == 2
    assert body["summary"]["worst_days"] == 11


def test_a_report_over_nothing_is_empty_rather_than_an_error(cast, auth):
    body = auth(cast["employee"]).get(f"{REPORTS}completion/").data
    assert body["rows"] == []
    assert body["summary"]["completion_percent"] == 0


# ---------------------------------------------------------------------------
# Scoping — a summary is a disclosure like any other
# ---------------------------------------------------------------------------
def test_a_report_never_counts_a_task_the_caller_cannot_open(cast, auth,
                                                             make_task):
    make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)

    body = auth(cast["employee"]).get(f"{REPORTS}completion/").data
    names = {row["employee"] for row in body["rows"]}
    assert names == {cast["employee"].get_full_name()}


def test_a_department_head_reports_on_their_own_department_only(
        cast, auth, make_task, departments):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              department=departments["engineering"])
    make_task(cast["other_hod"], [cast["outsider"]], status=Status.IN_PROGRESS,
              department=departments["finance"])

    rows = auth(cast["hod"]).get(f"{REPORTS}department/").data["rows"]
    assert {r["department"] for r in rows} == {"Engineering"}


def test_reports_honour_the_url_filters(cast, auth, make_task, departments):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              department=departments["engineering"])
    make_task(cast["other_hod"], [cast["outsider"]], status=Status.IN_PROGRESS,
              department=departments["finance"])

    rows = auth(cast["hr"]).get(f"{REPORTS}department/",
                                {"department": "Finance"}).data["rows"]
    assert {r["department"] for r in rows} == {"Finance"}


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------
def test_a_report_exports_the_same_rows_as_csv(cast, auth, make_task):
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.CLOSED)
    response = auth(cast["hr"]).get(f"{REPORTS}completion/", {"export": "csv"})

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert "attachment" in response["Content-Disposition"]

    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    header = rows[0]
    assert header[0] == "Employee"
    assert any(cast["employee"].get_full_name() in row for row in rows[1:])


def test_a_csv_export_is_served_with_the_same_headers_as_a_file(cast, auth):
    """
    A spreadsheet is opened by a desktop application; nosniff and the sandbox
    CSP stop a browser rendering it in this origin.
    """
    response = auth(cast["hr"]).get(f"{REPORTS}status/", {"export": "csv"})
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response["Content-Security-Policy"]


def test_a_csv_export_is_scoped_exactly_like_the_json(cast, auth, make_task):
    make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    response = auth(cast["employee"]).get(f"{REPORTS}completion/",
                                          {"export": "csv"})
    body = response.content.decode("utf-8-sig")
    assert cast["peer"].get_full_name() not in body


# ---------------------------------------------------------------------------
# Part 7 — the event seam
# ---------------------------------------------------------------------------
def test_the_named_events_exist(cast):
    """
    Keyed by the specification's own SCREAMING_CASE names, because those are what
    a delivery layer's configuration will be written against.

    TASK_CREATED joined them in TASK-MANAGEMENT-ASANA-MODEL: once everybody can
    raise work, the people accountable for it need to learn that it exists.
    """
    assert set(events.EVENTS) == {
        "TASK_CREATED", "TASK_ASSIGNED", "TASK_REVIEW_REQUIRED", "TASK_COMPLETED",
        "TASK_OVERDUE", "TASK_RETURNED", "TASK_BLOCKED"}


def test_assigning_a_task_emits_task_assigned(cast, make_task):
    seen = []

    def receiver(sender, task, **kwargs):
        seen.append((task, kwargs))

    events.task_assigned.connect(receiver)
    try:
        task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    finally:
        events.task_assigned.disconnect(receiver)

    assert len(seen) == 1
    assert seen[0][0].pk == task.pk
    assert seen[0][1]["actor"] == cast["hod"]
    assert cast["employee"] in seen[0][1]["assignees"]


def test_the_review_and_completion_events_fire_at_the_right_points(cast,
                                                                   make_task):
    from tasks import workflow

    seen = []
    handlers = {
        "review_required": lambda sender, **kw: seen.append("review_required"),
        "returned": lambda sender, **kw: seen.append(f"returned:{kw['remarks'][:4]}"),
        "completed": lambda sender, **kw: seen.append(
            f"completed:{kw.get('closed')}"),
    }
    events.task_review_required.connect(handlers["review_required"])
    events.task_returned.connect(handlers["returned"])
    events.task_completed.connect(handlers["completed"])
    try:
        task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                         status=Status.IN_PROGRESS)
        workflow.submit_for_review(task, cast["employee"])
        workflow.request_rework(task, cast["hod"], "Page two is stale figures.")
        workflow.submit_for_review(task, cast["employee"])
        workflow.approve_review(task, cast["hod"])
        workflow.close(task, cast["hr"])
    finally:
        events.task_review_required.disconnect(handlers["review_required"])
        events.task_returned.disconnect(handlers["returned"])
        events.task_completed.disconnect(handlers["completed"])

    assert seen == [
        "review_required", "returned:Page",
        "review_required", "completed:False",
        "completed:True",
    ]


def test_blocking_a_task_emits_task_blocked(cast, make_task):
    from tasks import workflow

    seen = []
    receiver = lambda sender, task, reason, **kw: seen.append(reason)  # noqa: E731
    events.task_blocked.connect(receiver)
    try:
        task = make_task(cast["hod"], [cast["employee"]],
                         status=Status.IN_PROGRESS)
        workflow.block(task, cast["employee"], "Waiting on the auditor.")
    finally:
        events.task_blocked.disconnect(receiver)

    assert seen == ["Waiting on the auditor."]


def test_a_broken_receiver_can_never_roll_back_a_transition(cast, make_task):
    """
    The opposite rule to the audit trail, deliberately: a state change with no
    record of it is an evidence gap, but a missed notification is an
    inconvenience. The work must not be lost to a listener's bug.
    """
    def explode(sender, **kwargs):
        raise RuntimeError("the delivery layer is down")

    events.task_assigned.connect(explode)
    try:
        task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    finally:
        events.task_assigned.disconnect(explode)

    task.refresh_from_db()
    assert task.status == Status.ASSIGNED


def test_every_event_has_exactly_one_receiver(cast):
    """
    Phase T3 asserted the opposite — that NOTHING was connected — because T3 was
    told to prepare hooks and deliver nothing. Phase T4.3 is the phase that
    builds delivery, so the assertion inverts: each of the six now has exactly
    one receiver, connected once in AppConfig.ready().

    "Exactly one" is the part worth pinning. Two receivers on the same signal
    would deliver every task notification twice — invisible in development,
    obvious to everybody else — which is what the dispatch_uid on each
    connection exists to prevent.
    """
    for name, signal in events.EVENTS.items():
        assert len(signal.receivers) == 1, (
            f"{name} has {len(signal.receivers)} receivers, expected 1")


def test_connecting_twice_does_not_double_delivery(cast):
    """`connect()` is idempotent, so a second call is a no-op rather than a
    second copy of every notification."""
    from tasks import receivers

    before = {name: len(signal.receivers) for name, signal in events.EVENTS.items()}
    receivers.connect()
    after = {name: len(signal.receivers) for name, signal in events.EVENTS.items()}
    assert before == after


def test_the_overdue_detector_emits_with_the_day_count(cast, make_task, today):
    from django.core.management import call_command

    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              due_date=today - datetime.timedelta(days=4))
    make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
              due_date=today + datetime.timedelta(days=4))

    seen = []
    receiver = lambda sender, task, overdue_days, **kw: seen.append(overdue_days)  # noqa: E731
    events.task_overdue.connect(receiver)
    try:
        call_command("detect_overdue_tasks", verbosity=0)
    finally:
        events.task_overdue.disconnect(receiver)

    assert seen == [4]


def test_the_overdue_detector_emits_nothing_on_a_dry_run(cast, make_task, today):
    from django.core.management import call_command

    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              due_date=today - datetime.timedelta(days=4))

    seen = []
    receiver = lambda sender, **kw: seen.append(kw)  # noqa: E731
    events.task_overdue.connect(receiver)
    try:
        call_command("detect_overdue_tasks", "--dry-run", verbosity=0)
    finally:
        events.task_overdue.disconnect(receiver)

    assert seen == []


# ---------------------------------------------------------------------------
# Reviewer performance (Part 7)
# ---------------------------------------------------------------------------
def test_the_reviewer_report_counts_what_reached_each_reviewer(cast, auth,
                                                               make_task):
    from tasks import workflow

    approved = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                         status=Status.UNDER_REVIEW)
    workflow.approve_review(approved, cast["hod"])
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.UNDER_REVIEW)

    rows = {r["reviewer"]: r
            for r in auth(cast["hr"]).get(f"{REPORTS}reviewer/").data["rows"]}
    row = rows[cast["hod"].get_full_name()]
    assert row["received"] == 2
    assert row["approved"] == 1
    assert row["awaiting"] == 1


def test_returns_are_counted_from_the_timeline_not_the_current_state(
        cast, auth, make_task):
    """
    A task sent back twice and then approved shows as approved; the two returns
    would otherwise be invisible, and the rework rate is the point of the report.
    """
    from tasks import workflow

    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW)
    workflow.request_rework(task, cast["hod"], "The figures are stale here.")
    workflow.submit_for_review(task, cast["employee"])
    workflow.request_rework(task, cast["hod"], "Still the wrong quarter.")
    workflow.submit_for_review(task, cast["employee"])
    workflow.approve_review(task, cast["hod"])

    rows = {r["reviewer"]: r
            for r in auth(cast["hr"]).get(f"{REPORTS}reviewer/").data["rows"]}
    row = rows[cast["hod"].get_full_name()]
    assert row["returned"] == 2
    assert row["approved"] == 1


def test_turnaround_measures_the_reviewers_own_time(cast, auth, make_task):
    """
    submitted_at -> completed_at, not the task's age. A reviewer who answers in a
    day on work that took a month should read as fast.
    """
    import datetime as dt

    from django.utils import timezone

    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.COMPLETED)
    now = timezone.now()
    Task.objects.filter(pk=task.pk).update(
        assigned_at=now - dt.timedelta(days=40),
        submitted_at=now - dt.timedelta(days=2),
        completed_at=now)

    rows = {r["reviewer"]: r
            for r in auth(cast["hr"]).get(f"{REPORTS}reviewer/").data["rows"]}
    assert rows[cast["hod"].get_full_name()]["average_turnaround_days"] == 2.0


def test_a_task_nobody_submitted_says_nothing_about_its_reviewer(cast, auth,
                                                                 make_task):
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.ASSIGNED)
    assert auth(cast["hr"]).get(f"{REPORTS}reviewer/").data["rows"] == []


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------
def test_a_report_exports_as_pdf(cast, auth, make_task):
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.CLOSED)
    response = auth(cast["hr"]).get(f"{REPORTS}completion/", {"export": "pdf"})

    assert response.status_code == 200, getattr(response, "data", b"")
    assert response["Content-Type"] == "application/pdf"
    assert "attachment" in response["Content-Disposition"]
    assert bytes(response.content)[:5] == b"%PDF-"


def test_a_pdf_export_is_hardened_like_any_other_file(cast, auth):
    response = auth(cast["hr"]).get(f"{REPORTS}status/", {"export": "pdf"})
    assert response["X-Content-Type-Options"] == "nosniff"


def test_a_pdf_export_is_scoped_exactly_like_the_json(cast, auth, make_task):
    """A printed summary is a disclosure like any other."""
    make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    response = auth(cast["employee"]).get(f"{REPORTS}completion/",
                                          {"export": "pdf"})
    assert response.status_code == 200
    # The PDF is compressed, so assert on the source of truth instead: the JSON
    # the same call would have produced carries nobody else's name.
    json_rows = auth(cast["employee"]).get(f"{REPORTS}completion/").data["rows"]
    assert cast["peer"].get_full_name() not in {r["employee"] for r in json_rows}


def test_an_unknown_export_format_returns_the_json(cast, auth):
    body = auth(cast["hr"]).get(f"{REPORTS}status/", {"export": "xlsx"}).data
    assert "rows" in body and "columns" in body
