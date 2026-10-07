from datetime import date

import pytest
from rest_framework.test import APIClient

from users.models import User
from leaves.models import Department, LeaveType

# A clean mid-June 2026 Monday (ISO week 24) with no seeded holiday nearby.
MONDAY = date.fromisocalendar(2026, 24, 1)
YEAR = 2026


def _user(username, role, department="ENG", **extra):
    # Default to a long-tenured permanent employee so the category engine resolves
    # Category A (Annual 12 / Sick 12) - gives tests a non-zero entitlement to work
    # with. Individual tests can override employment_type / date_of_joining.
    defaults = dict(
        employment_type=User.EmploymentType.PERMANENT,
        date_of_joining=date(2018, 1, 1),
    )
    defaults.update(extra)
    return User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.capitalize(), last_name="T", role=role, department=department,
        **defaults,
    )


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def maker(db):
    return _user("lmaker", User.Roles.MAKER)


@pytest.fixture
def checker(db):
    return _user("lchecker", User.Roles.CHECKER)


@pytest.fixture
def admin(db):
    return _user("ladmin", User.Roles.ADMIN)


@pytest.fixture
def annual(db):
    return LeaveType.objects.get(code="ANNUAL")


@pytest.fixture
def sick(db):
    return LeaveType.objects.get(code="SICK")


@pytest.fixture
def eng_department(db):
    return Department.objects.create(name="Engineering", code="ENG")
