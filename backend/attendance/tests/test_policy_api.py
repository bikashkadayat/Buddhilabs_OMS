"""Policy administration API, and the regressions the Phase 8 audit predicted.

Risk R1 in particular: the live dashboard counted statuses into a hardcoded
dict, so shipping WORK_FROM_HOME would have turned every dashboard request into
a KeyError -> HTTP 500.
"""
from datetime import date, time
from decimal import Decimal

import pytest

from attendance.models import Attendance, AttendancePolicy, PolicyAssignment, Shift

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _location_capture_optional(settings):
    """Relax the geofence's mandatory-location gate for this module.

    ``ATTENDANCE_REQUIRE_LOCATION`` defaults ON, so a check-in with no
    coordinates is refused with 400. Nothing in this module is about location —
    these tests predate the geofence — and making each one post coordinates
    would test the geofence by accident and obscure what they actually assert.

    The gate itself is covered where it belongs, in ``test_geofence.py``, which
    uses this same override for its own non-location cases.
    """
    settings.ATTENDANCE_REQUIRE_LOCATION = False

POLICIES = "/api/v1/attendance/policies/"
SHIFTS = "/api/v1/attendance/shifts/"
ASSIGNMENTS = "/api/v1/attendance/policy-assignments/"
EMPLOYEE_SHIFTS = "/api/v1/attendance/employee-shifts/"
RESOLVED = "/api/v1/attendance/policy/resolved/"
DASHBOARD = "/api/v1/attendance/dashboard/"
FLOOR = date(2000, 1, 1)


# --------------------------------------------------------------------------
# permissions
# --------------------------------------------------------------------------
@pytest.mark.parametrize("url", [POLICIES, SHIFTS, ASSIGNMENTS, EMPLOYEE_SHIFTS, RESOLVED])
def test_policy_administration_is_hr_only(employee, auth, url):
    assert auth(employee).get(url).status_code == 403


@pytest.mark.parametrize("url", [POLICIES, SHIFTS, ASSIGNMENTS])
def test_hr_can_read_policy_administration(hr, auth, url):
    assert auth(hr).get(url).status_code == 200


def test_a_department_head_cannot_edit_policies(dept_head, auth):
    assert auth(dept_head).post(POLICIES, {"name": "Sneaky"}, format="json").status_code == 403


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------
def test_hr_can_create_a_policy(hr, auth):
    response = auth(hr).post(POLICIES, {
        "name": "Morning team", "office_start_time": "09:00", "grace_minutes": 10,
        "half_day_hours": "4.00", "full_day_hours": "8.00"}, format="json")
    assert response.status_code == 201
    assert AttendancePolicy.objects.filter(name="Morning team").exists()


def test_creating_a_policy_is_audited(hr, auth):
    from audit.models import AuditLog

    auth(hr).post(POLICIES, {"name": "Audited"}, format="json")
    assert AuditLog.objects.filter(changes__event="ATTENDANCE_POLICY_CREATE").exists()


def test_an_invalid_policy_is_rejected(hr, auth):
    response = auth(hr).post(POLICIES, {
        "name": "Impossible", "half_day_hours": "9.00", "full_day_hours": "8.00",
    }, format="json")
    assert response.status_code == 400
    assert "half_day_hours" in response.data


def test_an_overnight_shift_is_rejected_by_the_api(hr, auth):
    response = auth(hr).post(SHIFTS, {
        "code": "night", "name": "Night", "start_time": "22:00", "end_time": "06:00",
        "crosses_midnight": True}, format="json")
    assert response.status_code == 400
    assert not Shift.objects.filter(code="night").exists()


def test_hr_can_assign_a_policy_to_a_department(hr, dept, auth):
    policy = AttendancePolicy.objects.create(name="Dept policy")
    response = auth(hr).post(ASSIGNMENTS, {
        "policy": str(policy.id), "scope": "department", "department": str(dept.id),
        "effective_from": FLOOR.isoformat()}, format="json")
    assert response.status_code == 201
    assert PolicyAssignment.objects.filter(department=dept).exists()


def test_a_scope_mismatch_is_rejected(hr, dept, auth):
    policy = AttendancePolicy.objects.create(name="Mismatched")
    response = auth(hr).post(ASSIGNMENTS, {
        "policy": str(policy.id), "scope": "user", "department": str(dept.id),
        "effective_from": FLOOR.isoformat()}, format="json")
    assert response.status_code == 400


def test_hr_can_put_an_employee_on_a_shift(hr, employee, auth):
    evening = Shift.objects.get(code="evening")
    response = auth(hr).post(EMPLOYEE_SHIFTS, {
        "user": str(employee.id), "shift": str(evening.id),
        "effective_from": FLOOR.isoformat()}, format="json")
    assert response.status_code == 201


# --------------------------------------------------------------------------
# explainability
# --------------------------------------------------------------------------
def test_resolved_policy_explains_the_effective_rules(hr, employee, auth):
    policy = AttendancePolicy.objects.create(name="User 11:00", office_start_time=time(11, 0),
                                             grace_minutes=20)
    PolicyAssignment.objects.create(policy=policy, scope=PolicyAssignment.Scope.USER,
                                    user=employee, effective_from=FLOOR)
    response = auth(hr).get(RESOLVED, {"user": str(employee.id)})
    assert response.status_code == 200
    assert response.data["policy"]["name"] == "User 11:00"
    assert response.data["effective_start_time"] == "11:00"
    assert response.data["effective_grace_minutes"] == 20
    assert response.data["policy_source"] == "assignment"


def test_resolved_policy_reports_the_settings_fallback(hr, employee, auth):
    PolicyAssignment.objects.all().delete()
    response = auth(hr).get(RESOLVED, {"user": str(employee.id)})
    assert response.data["policy_source"] == "settings-fallback"
    assert response.data["policy"]["id"] is None


# --------------------------------------------------------------------------
# regressions the audit predicted
# --------------------------------------------------------------------------
def test_the_dashboard_survives_a_wfh_day(hr, employee, auth, local):
    """R1: a hardcoded counts dict would raise KeyError -> HTTP 500 here."""
    today = local(2026, 1, 1).date()
    from django.utils import timezone

    today = timezone.localdate()
    Attendance.objects.create(
        employee=employee, date=today, status=Attendance.Status.WORK_FROM_HOME,
        check_in=local(today.year, today.month, today.day, 10, 0), is_wfh=True)

    response = auth(hr).get(DASHBOARD)
    assert response.status_code == 200
    assert "wfh" in response.data["counts"]
    assert response.data["present_now"] >= 1, "a WFH day counts as present"


def test_reports_summarise_the_new_status(employee, local):
    """The report summary was a hardcoded key list; a WFH day would have been
    silently dropped from every PDF and Excel export."""
    from django.utils import timezone

    from attendance.reports import STATUS_KEYS, build_employee_report

    assert "wfh" in STATUS_KEYS
    day = timezone.localdate()
    Attendance.objects.create(
        employee=employee, date=day, status=Attendance.Status.WORK_FROM_HOME,
        check_in=local(day.year, day.month, day.day, 10, 0))
    report = build_employee_report(employee, day, day)
    assert report["summary"]["wfh"] == 1


def test_hr_cannot_manually_set_work_from_home(hr, employee, auth):
    """WORK_FROM_HOME is derived from an approved request plus a real check-in;
    typing it in would bypass both gates."""
    from django.utils import timezone

    response = auth(hr).post("/api/v1/attendance/manual/", {
        "employee": str(employee.id), "date": timezone.localdate().isoformat(),
        "status": "wfh"}, format="json")
    assert response.status_code == 400


def test_hr_can_still_set_every_other_status(hr, employee, auth):
    from django.utils import timezone

    response = auth(hr).post("/api/v1/attendance/manual/", {
        "employee": str(employee.id), "date": timezone.localdate().isoformat(),
        "status": "present"}, format="json")
    assert response.status_code == 200


def test_today_reports_the_resolved_office_start(employee, auth):
    """P3: the widget renders this string verbatim, so the shape must not
    change — only the value, which is now per-employee."""
    from attendance.models import EmployeeShift

    EmployeeShift.objects.create(user=employee, shift=Shift.objects.get(code="morning"),
                                 effective_from=FLOOR)
    response = auth(employee).get("/api/v1/attendance/today/")
    assert response.status_code == 200
    assert response.data["office_start"] == "06:00"


def test_the_attendance_payload_keeps_every_existing_field(employee, auth, local, a_weekday):
    """Phase 8 fields are appended; nothing existing may disappear."""
    Attendance.objects.create(
        employee=employee, date=a_weekday,
        check_in=local(a_weekday.year, a_weekday.month, a_weekday.day, 10, 0))
    row = auth(employee).get("/api/v1/attendance/").data[0]
    for field in ("id", "employee", "date", "check_in", "check_out", "status",
                  "working_hours", "remarks", "marked_by", "source", "punch_count"):
        assert field in row
    for field in ("regular_hours", "overtime_hours", "late_minutes",
                  "comp_off_days", "comp_off_eligible", "is_wfh"):
        assert field in row


def test_check_in_still_works_end_to_end(employee, auth):
    """Rule 1/2: the browser path must be untouched by the policy engine."""
    from django.utils import timezone

    from attendance import services

    if services.is_holiday(timezone.localdate()):
        pytest.skip("today is a holiday; check-in is refused by design")
    response = auth(employee).post("/api/v1/attendance/check-in/", {}, format="json")
    assert response.status_code == 201
    record = Attendance.objects.get(employee=employee, date=timezone.localdate())
    assert record.check_in is not None
    # Which of the three it lands on depends on the wall-clock time the suite
    # runs at, since 8.1 derives status from the arrival time. Pinned-clock
    # assertions live in test_arrival_rules.py.
    assert record.status in (Attendance.Status.PRESENT, Attendance.Status.LATE,
                             Attendance.Status.HALF_DAY)
    assert record.applied_policy is not None, "the seeded Global policy applied"
    assert record.regular_hours == Decimal("0.00")
