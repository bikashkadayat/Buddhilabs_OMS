"""Fixtures for the analytics suite.

Two things these fixtures work hard to guarantee, because every assertion below
depends on them:

* **A settled window.** Analytics divides by expected working days, and "today"
  is only half-finished. Every dataset is anchored to a month that has fully
  passed, so an assertion cannot flip depending on the hour the suite runs.
* **Backdated accounts.** ``absent_floor`` takes the later of ``date_joined``
  and ``date_of_joining``, and ``date_joined`` defaults to now -- so without
  backdating, every historical day falls before the floor, expected days is
  zero, and every rate is ``None``. This is the same trap the Phase 9 fixtures
  document.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from attendance.models import Attendance, WFHRequest
from biometric.models import BiometricDevice, BiometricEmployee, DeviceSyncLog
from leaves.models import (
    CompensatoryLedger, Department, EnterpriseLeaveBalance, Holiday, Leave,
    LeaveDayRecord, LeaveType,
)
from users.models import User
from tenancy.stamping import stamp_all

SATURDAY = 5


def make_user(username, role=User.Roles.MAKER, dept=None, joined=date(2020, 1, 1)):
    user = User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.replace("_", " ").title(), last_name="Analytics",
        role=role, department_ref=dept,
        employment_type=User.EmploymentType.PERMANENT, date_of_joining=joined,
    )
    User.objects.filter(pk=user.pk).update(
        date_joined=timezone.make_aware(datetime(joined.year, joined.month, joined.day, 9, 0)))
    user.refresh_from_db()
    return user


def working_days(start, end, holidays=()):
    """The days the analytics calendar will count. Kept independent of
    ``analytics.calendar`` so a bug there cannot make its own test pass."""
    out, day = [], start
    holidays = set(holidays)
    while day <= end:
        if day.weekday() != SATURDAY and day not in holidays:
            out.append(day)
        day += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# window anchors
# ---------------------------------------------------------------------------
@pytest.fixture
def last_month():
    """(first, last) of the most recent fully-completed month."""
    today = timezone.localdate()
    last_day = today.replace(day=1) - timedelta(days=1)
    return last_day.replace(day=1), last_day


@pytest.fixture
def last_month_days(last_month):
    return working_days(*last_month)


# ---------------------------------------------------------------------------
# organisation
# ---------------------------------------------------------------------------
@pytest.fixture
def dept_eng(db):
    return Department.objects.create(name="Engineering", code="AN-ENG")


@pytest.fixture
def dept_ops(db):
    return Department.objects.create(name="Operations", code="AN-OPS")


@pytest.fixture
def dept_tiny(db):
    """Two people: below MIN_DEPARTMENT_SAMPLE, so it must never be ranked."""
    return Department.objects.create(name="Legal", code="AN-LEG")


@pytest.fixture
def eng_team(dept_eng):
    return [make_user(f"an_eng_{i}", User.Roles.MAKER, dept_eng) for i in range(4)]


@pytest.fixture
def ops_team(dept_ops):
    return [make_user(f"an_ops_{i}", User.Roles.MAKER, dept_ops) for i in range(3)]


@pytest.fixture
def tiny_team(dept_tiny):
    return [make_user(f"an_leg_{i}", User.Roles.MAKER, dept_tiny) for i in range(2)]


@pytest.fixture
def manager(dept_eng):
    """Department Head of Engineering. Department-scoped analytics."""
    return make_user("an_head", User.Roles.CHECKER, dept_eng)


@pytest.fixture
def hr_user(dept_ops):
    return make_user("an_hr", User.Roles.APPROVER, dept_ops)


@pytest.fixture
def admin_user(dept_ops):
    return make_user("an_admin", User.Roles.ADMIN, dept_ops)


@pytest.fixture
def employee(eng_team):
    return eng_team[0]


@pytest.fixture
def org(eng_team, ops_team, tiny_team, manager, hr_user, admin_user):
    """The whole cast. 4 + 3 + 2 employees, a head, HR and an admin = 12 active."""
    return {"eng": eng_team, "ops": ops_team, "tiny": tiny_team,
            "manager": manager, "hr": hr_user, "admin": admin_user}


# ---------------------------------------------------------------------------
# data builders
# ---------------------------------------------------------------------------
@pytest.fixture
def mark():
    """Create attendance rows for a user across given days."""
    def _mark(user, days, status=Attendance.Status.PRESENT, *,
              working_hours="8.00", regular_hours="8.00", overtime_hours="0.00",
              late_minutes=0, is_wfh=False, check_in=True):
        rows = []
        for day in days:
            rows.append(Attendance(
                employee=user, date=day, status=status,
                check_in=(timezone.make_aware(datetime(day.year, day.month, day.day, 10, 0))
                          if check_in else None),
                check_out=(timezone.make_aware(datetime(day.year, day.month, day.day, 18, 0))
                           if check_in else None),
                working_hours=Decimal(working_hours),
                regular_hours=Decimal(regular_hours),
                overtime_hours=Decimal(overtime_hours),
                late_minutes=late_minutes, is_wfh=is_wfh))
        return Attendance.objects.bulk_create(stamp_all(rows))
    return _mark


@pytest.fixture
def leave_type(db):
    # Code must match the legacy Leave.leave_type string ("annual"), because
    # leaves.services.resolve_leave_type maps one to the other and raises
    # otherwise. get_or_create, not create: a data migration already seeds the
    # standard leave types, so creating one here collides on the unique code.
    return LeaveType.objects.get_or_create(
        code="ANNUAL", defaults={"name": "Annual Leave", "display_color": "#2563EB"})[0]


@pytest.fixture
def take_leave(leave_type):
    """Approved leave for specific days.

    One ``Leave`` per day on purpose. A ``post_save`` signal on ``Leave``
    generates day records for the whole contiguous start..end range, so a single
    request spanning a non-contiguous list would silently book the days in
    between as leave too.
    """
    from leaves.services import generate_leave_day_records

    def _take(user, days, portion=LeaveDayRecord.DayPortion.FULL):
        requests = []
        for day in sorted(days):
            request = Leave.objects.create(
                user=user, leave_type="annual", start_date=day, end_date=day,
                reason="Analytics fixture", status=Leave.Status.APPROVED)
            if portion != LeaveDayRecord.DayPortion.FULL:
                generate_leave_day_records(request, day_portion=portion)
            requests.append(request)
        return requests
    return _take


@pytest.fixture
def wfh_request(db):
    def _make(user, start, end=None, status=WFHRequest.Status.APPROVED):
        return WFHRequest.objects.create(
            user=user, start_date=start, end_date=end or start,
            reason="Analytics fixture", status=status)
    return _make


@pytest.fixture
def comp_entry(db):
    def _make(user, days="1.00", entry_type=CompensatoryLedger.EntryType.EARN,
              status=CompensatoryLedger.Status.CONFIRMED, source_date=None,
              source=CompensatoryLedger.Source.ATTENDANCE):
        return CompensatoryLedger.objects.create(
            user=user, entry_type=entry_type, days=Decimal(days), source=source,
            status=status, source_date=source_date)
    return _make


@pytest.fixture
def balance(leave_type):
    """An entitlement balance.

    update_or_create, not create: approving leave fires a signal that recomputes
    (and therefore creates) the balance row, so a test that books leave AND sets
    a balance would otherwise collide on the unique (user, type, year) key.
    """
    def _make(user, entitled="20.00", used="5.00", year=None):
        return EnterpriseLeaveBalance.objects.update_or_create(
            user=user, leave_type=leave_type, year=year or timezone.localdate().year,
            defaults={"entitled_days": Decimal(entitled),
                      "used_days": Decimal(used)})[0]
    return _make


@pytest.fixture
def holiday(db):
    def _make(day, name="Analytics Holiday"):
        return Holiday.objects.create(date=day, name=name, is_active=True)
    return _make


@pytest.fixture
def device(db):
    def _make(name="Main Gate", status=BiometricDevice.Status.ONLINE, **extra):
        return BiometricDevice.objects.create(
            name=name, label=name[:12], connection_status=status, is_active=True,
            **extra)
    return _make


@pytest.fixture
def sync_log(db):
    def _make(device_obj, status=DeviceSyncLog.Status.SUCCESS, when=None, **extra):
        log = DeviceSyncLog.objects.create(
            device=device_obj, sync_type=DeviceSyncLog.SyncType.LIVE, status=status,
            records_received=extra.pop("received", 10),
            records_created=extra.pop("created", 10), **extra)
        if when:
            DeviceSyncLog.objects.filter(pk=log.pk).update(
                started_at=timezone.make_aware(datetime(when.year, when.month, when.day, 9, 0)))
        return log
    return _make


@pytest.fixture
def enrol(db):
    def _make(device_obj, user=None, device_user_id="1"):
        return BiometricEmployee.objects.create(
            device=device_obj, device_user_id=device_user_id, user=user,
            is_active=True)
    return _make


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
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
def month_params(last_month):
    start, end = last_month
    return {"from": start.isoformat(), "to": end.isoformat(), "granularity": "day"}
