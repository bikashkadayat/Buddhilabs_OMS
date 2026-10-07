from datetime import date, datetime

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from biometric.models import AttendancePunch, BiometricDevice, BiometricEmployee
from leaves.models import Department
from users.models import User


def _backdate_registration(user, joined=date(2020, 1, 1)):
    """Push User.date_joined into the past.

    ``absent_floor`` takes the LATER of date_joined (account registration) and
    date_of_joining, and date_joined defaults to *now*. Without this, every
    historical day resolves to Not Applicable — which silently turns any test
    anchored to a fixed past date into a pass-today-fail-tomorrow test.
    """
    User.objects.filter(pk=user.pk).update(
        date_joined=timezone.make_aware(datetime(joined.year, joined.month, joined.day, 9, 0)))
    user.refresh_from_db()
    return user


def make_user(username, role=User.Roles.MAKER, dept=None, **extra):
    defaults = dict(
        employment_type=User.EmploymentType.PERMANENT,
        date_of_joining=date(2020, 1, 1),
    )
    defaults.update(extra)
    user = User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        role=role, department_ref=dept, **defaults,
    )
    return _backdate_registration(user, defaults["date_of_joining"])


@pytest.fixture
def dept(db):
    return Department.objects.create(name="Engineering", code="BIO-ENG")


@pytest.fixture
def employee(db, dept):
    return _backdate_registration(User.objects.create_user(
        username="bio_emp", email="bio_emp@nif.test", password="pass12345",
        first_name="Ram", last_name="Thapa", role=User.Roles.MAKER,
        department_ref=dept, employment_type=User.EmploymentType.PERMANENT,
        date_of_joining=date(2020, 1, 1),
    ))


@pytest.fixture
def other_employee(db, dept):
    return _backdate_registration(User.objects.create_user(
        username="bio_emp2", email="bio_emp2@nif.test", password="pass12345",
        first_name="Sita", last_name="Gurung", role=User.Roles.MAKER,
        department_ref=dept, employment_type=User.EmploymentType.PERMANENT,
        date_of_joining=date(2020, 1, 1),
    ))


@pytest.fixture
def device(db):
    return BiometricDevice.objects.create(
        name="Main Gate", label="192.168.77.201", host="192.168.77.201",
    )


@pytest.fixture
def second_device(db):
    return BiometricDevice.objects.create(name="Warehouse", label="warehouse")


@pytest.fixture
def mapping(db, device, employee):
    return BiometricEmployee.objects.create(
        device=device, device_user_id="1", user=employee, device_name="Ram",
    )


@pytest.fixture
def aware_dt():
    """Build a timezone-aware datetime in the project zone (Asia/Kathmandu)."""
    def _make(y, m, d, hh=9, mm=0, ss=0):
        return timezone.make_aware(datetime(y, m, d, hh, mm, ss))
    return _make


@pytest.fixture
def make_punch(device, aware_dt):
    def _make(**overrides):
        data = dict(
            device=device,
            employee_device_id="1",
            timestamp=aware_dt(2026, 8, 3, 9, 15),
            punch=0,
            source=AttendancePunch.Source.LIVE,
        )
        data.update(overrides)
        return AttendancePunch.objects.create(**data)
    return _make


# --------------------------------------------------------------------------
# API fixtures
# --------------------------------------------------------------------------

@pytest.fixture
def hr(db, dept):
    """HR = approver role, which IsApproverOrAdmin admits."""
    return make_user("bio_hr", User.Roles.APPROVER, dept, first_name="Hari", last_name="HR")


@pytest.fixture
def admin_user(db, dept):
    return make_user("bio_admin", User.Roles.ADMIN, dept, first_name="Admin", last_name="User")


@pytest.fixture
def dept_head(db, dept):
    """Checker role — deliberately NOT allowed to manage mappings."""
    return make_user("bio_head", User.Roles.CHECKER, dept, first_name="Head", last_name="Dept")


@pytest.fixture
def local_browser_record(employee):
    """An existing browser check-in, as CheckInView would have written it."""
    from datetime import date as _date

    from attendance.models import Attendance

    def _make(check_in_hour=9, check_out_hour=None, day=_date(2026, 8, 3), user=None):
        user = user or employee
        ci = timezone.make_aware(datetime(day.year, day.month, day.day, check_in_hour, 0))
        co = (timezone.make_aware(datetime(day.year, day.month, day.day, check_out_hour, 0))
              if check_out_hour is not None else None)
        rec = Attendance(employee=user, date=day, check_in=ci, check_out=co,
                         source=Attendance.Source.BROWSER,
                         marked_by=Attendance.MarkedBy.SELF)
        from attendance import services as att_services
        att_services.recompute_status(rec)
        rec.save()
        return rec
    return _make


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def auth(api):
    def _login(user):
        api.force_authenticate(user=user)
        return api
    return _login
