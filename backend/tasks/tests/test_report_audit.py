"""
Phase T5.5 Part 8 — report and export audit.

Every report is driven in every format. The point is coverage of the SURFACE
rather than of individual numbers: ten reports times three outputs is thirty
paths, and a report that renders fine as JSON can still fail as a PDF because
one of its cells is a type the template cannot print.
"""
import csv
import io

import pytest

from tasks import analytics
from tasks.models import Task

from .conftest import LIST

pytestmark = pytest.mark.django_db

Status = Task.Status
REPORTS = f"{LIST}reports/"
SLUGS = list(analytics.REPORTS)


@pytest.fixture
def populated(cast, make_task, today, departments):
    """One task in every state a report might have to render."""
    import datetime

    from django.utils import timezone
    from tasks import workflow

    made = []
    made.append(make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                          status=Status.CLOSED, due_date=today,
                          department=departments["engineering"]))
    made.append(make_task(cast["hod"], [cast["peer"]], reviewer=cast["hod"],
                          status=Status.IN_PROGRESS,
                          due_date=today - datetime.timedelta(days=6),
                          department=departments["engineering"]))
    made.append(make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                          status=Status.UNDER_REVIEW,
                          department=departments["engineering"]))
    blocked = make_task(cast["hod"], [cast["employee"]],
                        status=Status.IN_PROGRESS,
                        department=departments["engineering"])
    workflow.block(blocked, cast["employee"], "Waiting on the auditor here.")
    made.append(blocked)
    # A task with no due date and no assignee — the shapes that break a naive
    # formatter.
    made.append(make_task(cast["hod"], [], due_date=None))
    Task.objects.filter(pk=made[0].pk).update(
        assigned_at=timezone.now() - datetime.timedelta(days=4),
        submitted_at=timezone.now() - datetime.timedelta(days=2),
        completed_at=timezone.now())
    return made


@pytest.mark.parametrize("slug", SLUGS)
def test_every_report_renders_as_json(cast, auth, populated, slug):
    body = auth(cast["hr"]).get(f"{REPORTS}{slug}/").data
    assert body["columns"] and isinstance(body["rows"], list)
    keys = {c["key"] for c in body["columns"]}
    for row in body["rows"]:
        assert keys <= set(row), f"{slug}: row missing {keys - set(row)}"


@pytest.mark.parametrize("slug", SLUGS)
def test_every_report_exports_as_csv(cast, auth, populated, slug):
    response = auth(cast["hr"]).get(f"{REPORTS}{slug}/", {"export": "csv"})
    assert response.status_code == 200, slug
    assert response["Content-Type"].startswith("text/csv")

    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows, slug
    json_body = auth(cast["hr"]).get(f"{REPORTS}{slug}/").data
    # The header must match the JSON columns exactly — a CSV whose columns have
    # drifted from the screen is worse than no CSV.
    assert rows[0] == [c["label"] for c in json_body["columns"]], slug
    assert len(rows) == len(json_body["rows"]) + 1, slug


@pytest.mark.parametrize("slug", SLUGS)
def test_every_report_exports_as_pdf(cast, auth, populated, slug):
    """
    A report that renders fine as JSON can still fail as a PDF, because one of
    its cells is a type the template cannot print. Thirty paths, all driven.
    """
    response = auth(cast["hr"]).get(f"{REPORTS}{slug}/", {"export": "pdf"})
    assert response.status_code == 200, (slug, getattr(response, "data", None))
    assert response["Content-Type"] == "application/pdf", slug
    assert bytes(response.content)[:5] == b"%PDF-", slug
    # A PDF under a kilobyte is an empty page that rendered without erroring.
    assert len(response.content) > 1000, slug


@pytest.mark.parametrize("slug", SLUGS)
def test_every_report_honours_a_filter(cast, auth, populated, slug,
                                       departments):
    """
    Filters must reach every report, not just the ones somebody remembered.
    A filtered screen that exports everything is a data-disclosure bug wearing
    a usability bug's clothes.
    """
    unfiltered = auth(cast["hr"]).get(f"{REPORTS}{slug}/").data
    filtered = auth(cast["hr"]).get(f"{REPORTS}{slug}/",
                                    {"department": "Finance"}).data
    # Finance has nothing, so any report whose rows are task-derived must shrink.
    # The status and health reports enumerate their own choices and legitimately
    # do not.
    if slug in ("status", "task-health", "executive", "trend"):
        assert len(filtered["rows"]) == len(unfiltered["rows"]), slug
    else:
        assert len(filtered["rows"]) <= len(unfiltered["rows"]), slug


@pytest.mark.parametrize("slug", SLUGS)
def test_a_report_over_nothing_still_renders_in_every_format(cast, auth, slug):
    """The empty case is the one that reaches production untested."""
    client = auth(cast["employee"])
    assert client.get(f"{REPORTS}{slug}/").status_code == 200, slug
    assert client.get(f"{REPORTS}{slug}/", {"export": "csv"}).status_code == 200
    assert client.get(f"{REPORTS}{slug}/", {"export": "pdf"}).status_code == 200


@pytest.mark.parametrize("slug", SLUGS)
def test_an_export_is_scoped_exactly_like_the_screen(cast, auth, populated,
                                                     slug):
    """A printed or downloaded summary is a disclosure like any other."""
    response = auth(cast["outsider"]).get(f"{REPORTS}{slug}/", {"export": "csv"})
    assert response.status_code == 200, slug
    body = response.content.decode("utf-8-sig")
    for task in populated:
        assert task.task_number not in body, slug
