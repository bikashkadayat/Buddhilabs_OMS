"""Leave requests are searchable, within the caller's own scope."""
from datetime import date

import pytest

from leaves.models import Leave
from users.models import User

pytestmark = pytest.mark.django_db


def _person(username, first, role=User.Roles.MAKER):
    return User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=first, last_name="T", role=role)


def _leave(user, reason="Family function"):
    return Leave.objects.create(user=user, leave_type="annual",
                                start_date=date(2026, 9, 1), end_date=date(2026, 9, 2),
                                reason=reason)


def test_an_employee_searches_only_their_own(api):
    me, other = _person("ls_me", "Sita"), _person("ls_other", "Gita")
    _leave(me, "Sister's wedding")
    _leave(other, "Sister's wedding")
    api.force_authenticate(user=me)
    rows = api.get("/api/v1/leaves/", {"search": "wedding"}).json()
    rows = rows.get("results", rows) if isinstance(rows, dict) else rows
    assert len(rows) == 1


def test_hr_finds_a_request_by_name(api):
    hr = _person("ls_hr", "Hari", User.Roles.APPROVER)
    target = _person("ls_target", "Bikash")
    _leave(target)
    _leave(_person("ls_noise", "Ram"))
    api.force_authenticate(user=hr)
    rows = api.get("/api/v1/leaves/", {"search": "Bikash"}).json()
    rows = rows.get("results", rows) if isinstance(rows, dict) else rows
    assert len(rows) == 1
