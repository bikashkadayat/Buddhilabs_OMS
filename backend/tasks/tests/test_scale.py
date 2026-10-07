"""
Phase T5.5 Part 4 — scale audit.

DESELECTED BY DEFAULT. These seed thousands of rows and take minutes; they are
audit instrumentation, not part of the ordinary suite. Run them deliberately:

    pytest -m slow -s tasks/tests/test_scale.py

WHAT THEY MEASURE, AND WHAT THEY DO NOT
---------------------------------------
Query COUNT, not wall-clock time. A timing assertion on a developer laptop
running SQLite tells you about the laptop; a query count is a property of the
code and holds on any database. The failure mode worth catching is the one that
is invisible at 100 rows and fatal at 5000 — a query per row — and that shows up
as a count that grows with the dataset.

Each test therefore measures the SAME endpoint at two sizes and asserts the
count did not move. The absolute ceilings are secondary and deliberately loose.
"""
import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tasks.models import (
    Task, TaskAssignee, TaskAttachment, TaskChecklistItem, TaskComment,
)
from tasks.services import generate_task_number, user_snapshot
from tenancy.stamping import stamp_all

pytestmark = [pytest.mark.django_db, pytest.mark.slow]

LIST = "/api/v1/tasks/"
ANALYTICS = "/api/v1/task-analytics/"
Status = Task.Status


_SEED_BATCH = [0]


def seed(cast, count, *, today=None):
    """
    `count` realistic tasks, in bulk.

    Built with bulk_create rather than through the API: seeding 5000 tasks a
    request at a time would take an hour and would be measuring the seeder. The
    shapes still vary — statuses, due dates, assignees, checklists, comments and
    attachments — because a table of 5000 identical rows exercises one query
    plan and hides the rest.
    """
    today = today or timezone.localdate()
    now = timezone.now()
    # A fresh prefix per call — these helpers are invoked twice in the same test
    # to compare two sizes, and the second call must not collide with the first.
    _SEED_BATCH[0] += 1
    batch = _SEED_BATCH[0]
    statuses = [Status.ASSIGNED, Status.IN_PROGRESS, Status.UNDER_REVIEW,
                Status.COMPLETED, Status.CLOSED, Status.BLOCKED]
    people = [cast["employee"], cast["peer"], cast["hr"], cast["hod"]]

    tasks = []
    for index in range(count):
        status = statuses[index % len(statuses)]
        done = status in (Status.COMPLETED, Status.CLOSED)
        tasks.append(Task(
            task_number=f"NIFN-TSK-SCALE-{batch:03d}-{index:06d}",
            title=f"Scale task {index}",
            description="Seeded for the scale audit.",
            status=status,
            priority=[Task.Priority.LOW, Task.Priority.MEDIUM,
                      Task.Priority.HIGH, Task.Priority.URGENT][index % 4],
            due_date=today + datetime.timedelta(days=(index % 60) - 30),
            created_by=cast["hod"],
            created_by_name=user_snapshot(cast["hod"])["name"],
            reviewer=cast["hod"],
            reviewer_name=user_snapshot(cast["hod"])["name"],
            department_name="Engineering",
            assigned_at=now - datetime.timedelta(days=(index % 40) + 1),
            submitted_at=now - datetime.timedelta(days=index % 5) if status in
            (Status.UNDER_REVIEW, Status.COMPLETED, Status.CLOSED) else None,
            completed_at=now - datetime.timedelta(days=index % 3) if done else None,
            progress_percent=100 if done else (index % 5) * 25,
        ))
    Task.objects.bulk_create(stamp_all(tasks), batch_size=500)
    created = list(Task.objects.filter(
        task_number__startswith=f"NIFN-TSK-SCALE-{batch:03d}-"))

    TaskAssignee.objects.bulk_create(stamp_all([
        TaskAssignee(task=task, user=people[i % len(people)],
                     user_name=user_snapshot(people[i % len(people)])["name"],
                     is_primary=True)
        for i, task in enumerate(created)
    ]), batch_size=500)

    # Content on a quarter of them — enough to exercise the joins without
    # quadrupling the seed time.
    sample = created[::4]
    TaskChecklistItem.objects.bulk_create(stamp_all([
        TaskChecklistItem(task=task, text=f"Step {n}", position=n,
                          is_done=(n % 2 == 0))
        for task in sample for n in range(4)
    ]), batch_size=1000)
    TaskComment.objects.bulk_create(stamp_all([
        TaskComment(task=task, author=cast["employee"],
                    author_name=user_snapshot(cast["employee"])["name"],
                    body="Progress note.")
        for task in sample
    ]), batch_size=1000)
    TaskAttachment.objects.bulk_create(stamp_all([
        TaskAttachment(task=task, link_url="https://example.org/evidence",
                       original_name="evidence", is_evidence=True,
                       uploaded_by=cast["employee"],
                       uploaded_by_name=user_snapshot(cast["employee"])["name"])
        for task in sample
    ]), batch_size=1000)
    return created


def measure(client, url, params=None):
    with CaptureQueriesContext(connection) as captured:
        response = client.get(url, params or {})
        assert response.status_code == 200, response.status_code
    return len(captured.captured_queries), response


def report(name, small, large, small_n, large_n):
    print(f"  {name:<28} {small_n:>5} rows: {small:>3} queries   "
          f"{large_n:>5} rows: {large:>3} queries")


# ---------------------------------------------------------------------------
# The endpoints an organisation actually opens all day
# ---------------------------------------------------------------------------
SURFACES = [
    ("list", LIST, {}),
    ("board", f"{LIST}board/", {}),
    ("dashboard", f"{LIST}dashboard/", {}),
    ("calendar (month)", f"{LIST}calendar/", {"view": "month"}),
    ("overdue", f"{LIST}overdue/", {}),
    ("review queue", f"{LIST}review-queue/", {}),
    ("workload", f"{LIST}workload/", {}),
    ("analytics executive", f"{ANALYTICS}executive/", {}),
    ("analytics health", f"{ANALYTICS}health/", {}),
    ("analytics employees", f"{ANALYTICS}employees/", {}),
    ("analytics trend", f"{ANALYTICS}trend/", {"period": "weekly"}),
    ("report: completion", f"{LIST}reports/completion/", {}),
    ("report: overdue", f"{LIST}reports/overdue/", {}),
    ("report: executive", f"{LIST}reports/executive/", {}),
]


@pytest.mark.parametrize("name,url,params", SURFACES,
                         ids=[s[0] for s in SURFACES])
def test_query_count_does_not_grow_with_the_dataset(cast, auth, name, url,
                                                    params):
    """
    THE test in this file.

    100 rows then 1000: if the count moves, something queries per row, and the
    same code at 5000 will issue 5000 queries. A count that holds is a property
    that holds at any size.
    """
    client = auth(cast["hr"])

    seed(cast, 100)
    small, _ = measure(client, url, params)

    seed(cast, 900)      # 1000 in total
    large, response = measure(client, url, params)

    report(name, small, large, 100, 1000)
    assert large == small, (
        f"{name}: {small} queries at 100 rows but {large} at 1000 — "
        f"this endpoint queries per row and will not survive production.")


@pytest.mark.parametrize("name,url,params", SURFACES,
                         ids=[s[0] for s in SURFACES])
def test_every_surface_is_bounded_at_five_thousand(cast, auth, name, url,
                                                   params):
    """
    The absolute ceiling at the size this organisation might plausibly reach in
    a few years. Loose on purpose — the point is that it is a CONSTANT, which
    the test above establishes; this one catches a constant that is merely
    absurd.
    """
    seed(cast, 5000)
    queries, response = measure(auth(cast["hr"]), url, params)
    print(f"  {name:<28} 5000 rows: {queries:>3} queries")
    assert queries < 80, f"{name} costs {queries} queries at 5000 tasks"


def test_the_list_page_returns_a_bounded_page_not_the_whole_table(cast, auth):
    """
    Pagination is the other half of scale: a bounded query count is no use if
    the response carries 5000 serialized tasks.
    """
    seed(cast, 5000)
    response = auth(cast["hr"]).get(LIST)
    assert response.data["count"] == 5000
    assert len(response.data["results"]) <= 200      # max_page_size


def test_an_employee_at_scale_pays_for_their_own_rows_only(cast, auth):
    """
    The visibility filter must narrow the QUERY, not the response. If scoping
    happened after the fact, an employee's list would cost the same as HR's.
    """
    seed(cast, 2000)
    hr_queries, hr_response = measure(auth(cast["hr"]), LIST)
    emp_queries, emp_response = measure(auth(cast["employee"]), LIST)

    print(f"  visibility scoping        HR sees {hr_response.data['count']} "
          f"({hr_queries} q), employee sees {emp_response.data['count']} "
          f"({emp_queries} q)")
    assert emp_response.data["count"] < hr_response.data["count"]
    assert emp_queries <= hr_queries


def test_the_reminder_engine_does_not_query_per_task(cast):
    """
    It runs nightly over everything overdue. A query per task would be
    invisible at 100 and would take the scheduler down at 5000.
    """
    from tasks import reminders

    seed(cast, 200)
    with CaptureQueriesContext(connection) as small:
        reminders.run_due_reminders()
    Task.objects.filter(task_number__startswith="NIFN-TSK-SCALE-").delete()

    seed(cast, 1000)
    with CaptureQueriesContext(connection) as large:
        reminders.run_due_reminders()

    small_n, large_n = len(small.captured_queries), len(large.captured_queries)
    print(f"  reminder engine           200 tasks: {small_n} q, "
          f"1000 tasks: {large_n} q")
    # This one legitimately scales with the number of tasks it NOTIFIES about —
    # each send is a row plus a notification. What must not happen is a
    # super-linear blow-up.
    assert large_n < small_n * 8, (
        f"the reminder engine went from {small_n} to {large_n} queries for 5x "
        f"the tasks — worse than linear.")


def test_reminder_engine_cost_profile(cast):
    """
    AUDIT MEASUREMENT, not a pass/fail gate.

    Prints the per-notification cost so the finding in the audit report is a
    measured number rather than an estimate. The engine is inherently linear in
    the number of people it TELLS — each notification is a row — so the question
    is the constant, not the shape.
    """
    from tasks import reminders
    from notifications.models import Notification

    seed(cast, 1000)
    Notification.objects.all().delete()
    with CaptureQueriesContext(connection) as captured:
        sent = reminders.run_due_reminders()
    notifications = Notification.objects.count()
    queries = len(captured.captured_queries)

    per = round(queries / sent, 1) if sent else 0
    print(f"\n  REMINDER ENGINE PROFILE")
    print(f"    tasks scanned      : 1000")
    print(f"    reminders sent     : {sent}")
    print(f"    notifications made : {notifications}")
    print(f"    queries            : {queries}")
    print(f"    queries per send   : {per}")
    print(f"    projected @5000 due: ~{int(per * sent * 5)} queries\n")
    assert sent > 0
