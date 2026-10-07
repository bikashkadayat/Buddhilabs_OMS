from datetime import date, datetime, timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from attendance.models import Attendance
from leaves.models import Department, Holiday
from users.models import User


def make_user(username, role=User.Roles.MAKER, dept=None, joined=date(2020, 1, 1), **extra):
    """Create an employee whose account-registration date is in the past.

    ``absent_floor`` takes the LATER of User.date_joined and date_of_joining, and
    date_joined defaults to now — so without backdating it, every historical day
    resolves to Not Applicable and absence can never be tested.
    """
    user = User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.replace("_", " ").title(), last_name="Test",
        role=role, department_ref=dept,
        employment_type=User.EmploymentType.PERMANENT,
        date_of_joining=joined, **extra,
    )
    User.objects.filter(pk=user.pk).update(
        date_joined=timezone.make_aware(datetime(joined.year, joined.month, joined.day, 9, 0)))
    user.refresh_from_db()
    return user


@pytest.fixture
def dept(db):
    return Department.objects.create(name="Engineering", code="ATT-ENG")


@pytest.fixture
def other_dept(db):
    return Department.objects.create(name="Operations", code="ATT-OPS")


@pytest.fixture
def employee(db, dept):
    return make_user("att_emp", User.Roles.MAKER, dept)


@pytest.fixture
def coworker(db, dept):
    return make_user("att_mate", User.Roles.MAKER, dept)


@pytest.fixture
def outsider(db, other_dept):
    return make_user("att_out", User.Roles.MAKER, other_dept)


@pytest.fixture
def dept_head(db, dept):
    return make_user("att_head", User.Roles.CHECKER, dept)


@pytest.fixture
def hr(db, dept):
    return make_user("att_hr", User.Roles.APPROVER, dept)


@pytest.fixture
def admin_user(db, dept):
    return make_user("att_admin", User.Roles.ADMIN, dept)


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def auth(api):
    def _login(user):
        api.force_authenticate(user=user)
        return api
    return _login


@pytest.fixture
def local():
    """Timezone-aware datetime in the project zone (Asia/Kathmandu)."""
    def _make(y, m, d, hh=9, mm=0, ss=0):
        return timezone.make_aware(datetime(y, m, d, hh, mm, ss))
    return _make


@pytest.fixture
def a_weekday():
    """A settled past weekday that is not Saturday — safe to judge as Absent."""
    d = timezone.localdate() - timedelta(days=10)
    while d.weekday() == 5:
        d -= timedelta(days=1)
    return d


@pytest.fixture
def a_weekday_this_month():
    """A non-Saturday day inside the CURRENT month, for calendar assertions.

    build_calendar renders one month at a time, so a cross-month anchor cannot
    be asserted against it.
    """
    today = timezone.localdate()
    d = today
    while d.month == today.month:
        if d.weekday() != 5:
            return d
        d -= timedelta(days=1)
    pytest.skip("no non-Saturday day available in the current month yet")


@pytest.fixture
def a_saturday():
    d = timezone.localdate() - timedelta(days=1)
    while d.weekday() != 5:
        d -= timedelta(days=1)
    return d


@pytest.fixture
def make_attendance(employee, local):
    def _make(d, check_in=None, check_out=None, **extra):
        return Attendance.objects.create(
            employee=extra.pop("employee", employee), date=d,
            check_in=check_in, check_out=check_out, **extra,
        )
    return _make


@pytest.fixture
def public_holiday(db):
    def _make(d, name="Test Holiday"):
        return Holiday.objects.create(date=d, name=name, is_active=True)
    return _make


# ---------------------------------------------------------------------------
# Carried over from the geofence work so its tests keep their fixtures.
# ---------------------------------------------------------------------------
def _user(username, role, department_ref=None):
    return User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.capitalize(), last_name="T", role=role,
        department_ref=department_ref,
        employment_type=User.EmploymentType.PERMANENT,
        date_of_joining=date(2018, 1, 1),
    )


@pytest.fixture
def maker(db, dept):
    """Plain employee — checks themselves in and out."""
    return _user("att_emp_maker", User.Roles.MAKER, dept)
