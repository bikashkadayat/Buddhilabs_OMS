"""
Phase FIX — task report exports, end to end.

Companion to appraisal/tests/test_exports.py. Same lesson: assert what the file
CONTAINS and what its headers SAY. The frontend tests that covered these buttons
passed throughout the period the downloads were completely broken, because they
checked that a link existed rather than that anything was downloaded.
"""
import csv
import io

import pytest

from tasks.models import Task

pytestmark = pytest.mark.django_db

REPORTS = "/api/v1/tasks/reports/"
Status = Task.Status

# Every slug the module publishes. Parametrised from a literal rather than from
# the endpoint, so a report that silently disappears fails here.
SLUGS = ["completion", "status", "department", "workload", "overdue",
         "reviewer", "template-usage", "task-health", "trend", "executive",
         "employee-evidence", "department-summary", "task-contribution",
         # Phase TASK-GOVERNANCE-HARDENING.
         "department-performance", "cycle-time", "review-turnaround",
         "overdue-analysis",
         # Phase TASK-PLANNING-AND-DEPARTMENT-OWNERSHIP.
         "department-productivity", "goal-completion",
         "department-progress"]


def test_the_slug_list_is_exactly_what_the_module_publishes(cast, auth):
    body = auth(cast["hr"]).get(REPORTS).data
    rows = body if isinstance(body, list) else body.get("reports", body)
    assert sorted(r["slug"] for r in rows) == sorted(SLUGS)


@pytest.fixture
def populated(cast, make_task, departments):
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.COMPLETED, department=departments["engineering"])
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.UNDER_REVIEW, department=departments["engineering"])


@pytest.mark.parametrize("slug", SLUGS)
def test_every_report_downloads_as_csv(cast, auth, populated, slug):
    response = auth(cast["hr"]).get(f"{REPORTS}{slug}/?export=csv")

    assert response.status_code == 200, slug
    assert response["Content-Type"].startswith("text/csv")
    assert "attachment;" in response["Content-Disposition"]
    assert response.content


@pytest.mark.parametrize("slug", SLUGS)
def test_every_report_downloads_as_pdf(cast, auth, populated, slug):
    response = auth(cast["hr"]).get(f"{REPORTS}{slug}/?export=pdf")

    assert response.status_code == 200, slug
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert b"%%EOF" in response.content[-1024:]


def test_the_csv_opens_correctly_in_excel(cast, auth, populated):
    """Excel on Windows reads a CSV as the system codepage unless it opens with
    a byte-order mark."""
    response = auth(cast["hr"]).get(f"{REPORTS}completion/?export=csv")
    assert response.content.startswith(b"\xef\xbb\xbf"), "no UTF-8 BOM"
    assert "charset=utf-8" in response["Content-Type"]


def test_non_ascii_survives_the_round_trip(cast, auth, make_task, departments):
    """
    A Devanagari name must come back out of the CSV as the same characters.

    Asserted on the EMPLOYEE-EVIDENCE report because it names people, which is
    where non-ASCII actually appears in this system — a report that happens not
    to carry the row would prove nothing about the encoding.
    """
    nepali = "प्रशान्त आचार्य"
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              department=departments["engineering"])
    cast["employee"].first_name = "प्रशान्त"
    cast["employee"].last_name = "आचार्य"
    cast["employee"].save(update_fields=["first_name", "last_name"])

    raw = auth(cast["hr"]).get(f"{REPORTS}employee-evidence/?export=csv").content
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert nepali in text

    # And it parses as one cell rather than being split or mangled.
    rows = list(csv.reader(io.StringIO(text)))
    assert any(cell == nepali for row in rows for cell in row)


def test_a_comma_in_a_task_title_does_not_shift_the_columns(
        cast, auth, make_task, departments):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW,
                     department=departments["engineering"])
    Task.objects.filter(pk=task.pk).update(
        title='Prepare the return, then "review" it')

    text = auth(cast["hr"]).get(
        f"{REPORTS}reviewer/?export=csv").content.decode("utf-8-sig")
    rows = [r for r in csv.reader(io.StringIO(text)) if r]
    header, *body = rows
    assert all(len(row) == len(header) for row in body)


def test_the_csv_header_matches_the_json_columns(cast, auth, populated):
    body = auth(cast["hr"]).get(f"{REPORTS}completion/").data
    text = auth(cast["hr"]).get(
        f"{REPORTS}completion/?export=csv").content.decode("utf-8-sig")
    header = next(csv.reader(io.StringIO(text)))
    assert header == [c["label"] for c in body["columns"]]


def test_the_screens_filters_travel_with_the_export(cast, auth, make_task,
                                                    departments):
    """A report downloaded from a filtered view must contain what was on the
    view — otherwise the file and the screen disagree and the file wins."""
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              department=departments["engineering"])
    make_task(cast["other_hod"], [cast["outsider"]], status=Status.IN_PROGRESS,
              department=departments["finance"])

    text = auth(cast["hr"]).get(
        f"{REPORTS}department/?export=csv&department=Engineering"
    ).content.decode("utf-8-sig")
    assert "Engineering" in text
    assert "Finance" not in text


def test_an_export_carries_the_upload_hardening_headers(cast, auth, populated):
    response = auth(cast["hr"]).get(f"{REPORTS}completion/?export=csv")
    assert response["X-Content-Type-Options"] == "nosniff"


# ---------------------------------------------------------------------------
# Security — Step 9
# ---------------------------------------------------------------------------
def test_an_export_refuses_an_anonymous_caller(api, populated):
    """A plain <a href> sends no credentials. This is what the server answers,
    and the browser used to render it as the 'file'."""
    assert api.get(f"{REPORTS}completion/?export=csv").status_code == 401


def test_an_employees_export_contains_only_their_own_rows(
        cast, auth, make_task, departments):
    make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS,
              department=departments["engineering"])
    make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS,
              department=departments["engineering"])

    text = auth(cast["employee"]).get(
        f"{REPORTS}employee-evidence/?export=csv").content.decode("utf-8-sig")
    assert cast["employee"].get_full_name() in text
    assert cast["peer"].get_full_name() not in text


def test_an_unknown_slug_is_a_clean_404_not_a_500(cast, auth):
    assert auth(cast["hr"]).get(
        f"{REPORTS}not-a-report/?export=csv").status_code == 404
