"""
Phase FIX — export downloads, end to end.

WHY THESE EXIST
---------------
Nine export buttons across five pages were plain `<a href>` links to the API.
This app authenticates with a JWT held in JavaScript, so those navigations
carried no Authorization header, got 401, and the browser rendered the JSON or
saved it as the "file". Three frontend tests covered those buttons and all three
passed, because they asserted a LINK EXISTED with the right href — which was
true, and useless.

The lesson these encode: assert what the export CONTAINS and what its headers
SAY, not that a control is on the page.
"""
import csv
import io

import pytest

from appraisal.models import Appraisal
from .conftest import APPRAISALS

pytestmark = pytest.mark.django_db

Status = Appraisal.Status
SLUGS = ["appraisal-summary", "department-summary", "goal-completion",
         "training-needs", "development-plans", "promotion-readiness"]


@pytest.fixture
def populated(cast, cycle, make_appraisal, competencies):
    """One closed appraisal so every report has at least one row to render."""
    return make_appraisal(cycle, cast["employee"], cast["supervisor"],
                          [cast["committee"]], status=Status.CLOSED)


@pytest.mark.parametrize("slug", SLUGS)
def test_every_report_downloads_as_csv(cast, auth, populated, slug):
    response = auth(cast["hr"]).get(f"{APPRAISALS}reports/{slug}/?export=csv")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert "attachment;" in response["Content-Disposition"]
    assert f"{slug}" in response["Content-Disposition"]
    assert response.content


@pytest.mark.parametrize("slug", SLUGS)
def test_every_report_downloads_as_pdf(cast, auth, populated, slug):
    response = auth(cast["hr"]).get(f"{APPRAISALS}reports/{slug}/?export=pdf")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert "attachment;" in response["Content-Disposition"]
    # A real PDF, not an error page with the wrong content type on it.
    assert response.content.startswith(b"%PDF")
    assert b"%%EOF" in response.content[-1024:]


def test_the_csv_opens_correctly_in_excel(cast, auth, populated):
    """
    Excel on Windows does not sniff encodings: without a byte-order mark it
    reads a CSV as the system codepage, and a Nepali name arrives as mojibake in
    the one application these are most often opened in.
    """
    response = auth(cast["hr"]).get(
        f"{APPRAISALS}reports/appraisal-summary/?export=csv")

    assert response.content.startswith(b"\xef\xbb\xbf"), "no UTF-8 BOM"
    assert "charset=utf-8" in response["Content-Type"]


def test_non_ascii_survives_the_round_trip(cast, auth, cycle, make_appraisal):
    """A Devanagari name must come back out of the CSV as the same characters."""
    nepali = "प्रशान्त आचार्य"
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    Appraisal.objects.filter(pk=appraisal.pk).update(employee_name=nepali)

    response = auth(cast["hr"]).get(
        f"{APPRAISALS}reports/appraisal-summary/?export=csv")
    text = response.content.decode("utf-8-sig")
    assert nepali in text

    rows = list(csv.reader(io.StringIO(text)))
    assert any(nepali in cell for row in rows for cell in row)


def test_a_comma_in_the_data_does_not_shift_the_columns(cast, auth, cycle,
                                                        make_appraisal):
    """The classic CSV failure: an unescaped comma silently moves every column
    after it, and the file still opens."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    Appraisal.objects.filter(pk=appraisal.pk).update(
        employee_name='Acharya, Prashanta "PA"')

    response = auth(cast["hr"]).get(
        f"{APPRAISALS}reports/appraisal-summary/?export=csv")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    header, *body = rows
    assert all(len(row) == len(header) for row in body if row)
    assert any('Acharya, Prashanta "PA"' in cell for row in body for cell in row)


def test_the_csv_header_matches_the_json_columns(cast, auth, populated):
    """A CSV whose headers drift from the screen is a file nobody can reconcile
    with what they were looking at."""
    body = auth(cast["hr"]).get(
        f"{APPRAISALS}reports/appraisal-summary/").data
    response = auth(cast["hr"]).get(
        f"{APPRAISALS}reports/appraisal-summary/?export=csv")

    header = next(csv.reader(io.StringIO(
        response.content.decode("utf-8-sig"))))
    assert header == [c["label"] for c in body["columns"]]


def test_an_export_carries_the_upload_hardening_headers(cast, auth, populated):
    """A spreadsheet is opened by a desktop application; nosniff stops a browser
    rendering it in this origin."""
    response = auth(cast["hr"]).get(
        f"{APPRAISALS}reports/appraisal-summary/?export=csv")
    assert response["X-Content-Type-Options"] == "nosniff"


# ---------------------------------------------------------------------------
# Security — Step 9
# ---------------------------------------------------------------------------
def test_an_export_refuses_an_anonymous_caller(api, populated):
    """The bug's real shape: a plain link sends no credentials, and this is what
    the server correctly answers."""
    response = api.get(f"{APPRAISALS}reports/appraisal-summary/?export=csv")
    assert response.status_code == 401


def test_an_export_contains_only_what_the_caller_may_read(cast, auth, cycle,
                                                          make_appraisal):
    """
    Reports carry no permission of their own — they run over the caller's
    VISIBLE set, so this is the check that the visible set is really applied on
    the export path and not only on the JSON one.
    """
    make_appraisal(cycle, cast["employee"], cast["supervisor"])
    make_appraisal(cycle, cast["peer"], cast["committee"])

    text = auth(cast["supervisor"]).get(
        f"{APPRAISALS}reports/appraisal-summary/?export=csv"
    ).content.decode("utf-8-sig")
    assert cast["employee"].get_full_name() in text
    assert cast["peer"].get_full_name() not in text


def test_an_outsiders_export_is_empty_rather_than_filtered_afterwards(
        cast, auth, cycle, make_appraisal):
    make_appraisal(cycle, cast["employee"], cast["supervisor"])
    text = auth(cast["outsider"]).get(
        f"{APPRAISALS}reports/appraisal-summary/?export=csv"
    ).content.decode("utf-8-sig")
    rows = [r for r in csv.reader(io.StringIO(text)) if r]
    assert len(rows) == 1          # the header, and nothing else


def test_an_unknown_slug_is_a_clean_404_not_a_500(cast, auth):
    response = auth(cast["hr"]).get(f"{APPRAISALS}reports/not-a-report/?export=csv")
    assert response.status_code == 404
