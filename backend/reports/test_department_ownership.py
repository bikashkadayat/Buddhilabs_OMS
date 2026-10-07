"""
The Department Ownership Report (Phase DEPARTMENT-GOVERNANCE-HARDENING).

The register that answers the question every departmental workflow ends at:
who is accountable for this department? It is the report somebody runs BECAUSE
a banner told them there was a gap, so the one thing it must never do is
disagree with the banner - which is why it reads the same function
(leaves.governance) rather than counting for itself.
"""
from datetime import date

import pytest
from django.apps import apps
from rest_framework.test import APIClient

from reports.models import REPORT_FORMATS, ReportType
from reports.permissions import allowed_report_types
from reports.report_service import build_report_file
from users.models import User

pytestmark = pytest.mark.django_db
Department = apps.get_model("leaves", "Department")
SLUG = "department_ownership"


def _user(username, role):
    return User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.capitalize(), last_name="T", role=role,
        date_of_joining=date(2020, 1, 1))


@pytest.fixture
def org(db):
    head = _user("own_head", User.Roles.CHECKER)
    owned = Department.objects.create(name="Owned Unit", code="OWN", head=head)
    orphan = Department.objects.create(name="Orphan Unit", code="ORPH")
    head.department_ref = owned
    head.save(update_fields=["department_ref"])
    return {"head": head, "owned": owned, "orphan": orphan,
            "hr": _user("own_hr", User.Roles.APPROVER),
            "admin": _user("own_admin", User.Roles.ADMIN),
            "employee": _user("own_emp", User.Roles.MAKER)}


def _rows(content):
    return content.decode("utf-8-sig").splitlines()


@pytest.mark.parametrize("fmt", ["excel", "csv", "pdf"])
def test_the_register_builds_in_every_format_it_declares(org, fmt):
    assert set(REPORT_FORMATS[ReportType.DEPARTMENT_OWNERSHIP]) == {"excel", "pdf", "csv"}
    content, filename, content_type = build_report_file(SLUG, {"format": fmt})
    assert content, f"{fmt} produced no bytes"
    assert content_type
    if fmt == "pdf":
        assert content.startswith(b"%PDF")
    if fmt == "csv":
        assert content.startswith(b"\xef\xbb\xbf"), "Excel needs the BOM"


def test_the_register_names_who_answers_for_each_department(org):
    content, _, _ = build_report_file(SLUG, {"format": "csv"})
    body = content.decode("utf-8-sig")
    assert "Owned Unit" in body
    assert org["head"].get_full_name() in body
    assert "Orphan Unit" in body


def test_a_department_with_nobody_is_marked_rather_than_left_blank(org):
    """
    A blank cell reads as missing data. "NO HEAD" reads as a finding, which is
    what it is - and it is what somebody scanning the column is looking for.
    """
    content, _, _ = build_report_file(SLUG, {"format": "csv"})
    line = next(l for l in _rows(content) if l.startswith("Orphan Unit"))
    assert "NO HEAD" in line
    assert "Not assigned" in line


def test_a_head_whose_account_is_closed_is_reported_as_such(org):
    """
    "Nobody was ever assigned" and "the person assigned has left" are different
    problems with different fixes, and one blank cell would hide both.
    """
    org["head"].is_active = False
    org["head"].save(update_fields=["is_active"])
    content, _, _ = build_report_file(SLUG, {"format": "csv"})
    line = next(l for l in _rows(content) if l.startswith("Owned Unit"))
    assert "Inactive account" in line
    assert "NO HEAD" in line


def test_a_department_stood_down_is_listed_but_not_counted_as_a_gap(org):
    org["orphan"].is_active = False
    org["orphan"].save(update_fields=["is_active"])
    content, _, _ = build_report_file(SLUG, {"format": "csv"})
    line = next(l for l in _rows(content) if l.startswith("Orphan Unit"))
    assert "Stood down" in line


def test_it_is_for_the_people_who_govern_not_for_a_department_head(org):
    """
    The organisation's ownership map. A department head's business is their own
    department's work; HR, Admin and the Board answer for the shape of the
    organisation.
    """
    assert allowed_report_types(org["admin"]) is None    # unrestricted
    assert allowed_report_types(org["hr"]) is None
    assert SLUG not in allowed_report_types(org["head"])
    assert SLUG not in allowed_report_types(org["employee"])


def test_requesting_it_through_the_api_is_refused_for_a_department_head(org):
    api = APIClient()
    api.force_authenticate(org["head"])
    res = api.post("/api/v1/reports/request/",
                   {"report_type": SLUG, "format": "csv"}, format="json")
    assert res.status_code == 403, res.data


def test_hr_may_request_it(org, settings):
    settings.REPORTS_RUN_SYNC = True
    api = APIClient()
    api.force_authenticate(org["hr"])
    res = api.post("/api/v1/reports/request/",
                   {"report_type": SLUG, "format": "csv"}, format="json")
    assert res.status_code == 202, res.data
