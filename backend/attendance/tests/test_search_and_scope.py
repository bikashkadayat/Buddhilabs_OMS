"""Attendance search, and the department-head scope it rides on."""
from datetime import date

import pytest

from attendance.models import Attendance

from .conftest import make_user

pytestmark = pytest.mark.django_db


def _mark(user, day=date(2026, 9, 1)):
    return Attendance.objects.create(employee=user, date=day, status="present")


def test_a_department_head_with_no_department_sees_only_their_own(api, auth, employee):
    """Found by audit: `department_ref_id=None` filtered as IS NULL, so a head
    with no department saw every unassigned employee's attendance."""
    head = make_user("headless", "checker", None)
    unassigned = make_user("floater", "maker", None)
    _mark(head)
    _mark(unassigned)
    _mark(employee)
    auth(head)
    rows = api.get("/api/v1/attendance/").json()
    assert len(rows) == 1, "only the head's own record"


def test_search_narrows_by_person_within_scope(api, auth, dept_head, employee, outsider):
    _mark(employee)
    _mark(outsider)
    auth(dept_head)
    rows = api.get("/api/v1/attendance/", {"search": employee.first_name}).json()
    assert len(rows) == 1
    rows = api.get("/api/v1/attendance/", {"search": outsider.first_name}).json()
    assert rows == [], "search must never widen what a head may see"
