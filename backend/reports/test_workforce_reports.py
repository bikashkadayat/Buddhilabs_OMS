"""The eight workforce reports, in all three formats.

These plug into the existing ReportRun machinery, so the tests cover both the
builders directly (does the arithmetic hold?) and the request path (can the
right people ask for them, and is a department head's scope pinned server-side?).
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from attendance.models import (
    Attendance,
    AttendancePolicy,
    EmployeeShift,
    PolicyAssignment,
    Shift,
    WFHRequest,
)
from attendance.services import recompute_status
from leaves.models import CompensatoryLedger, Department
from reports import report_service
from reports.models import WORKFORCE_REPORT_TYPES, ReportRun, ReportType
from users.models import User

pytestmark = pytest.mark.django_db

REQUEST_URL = "/api/v1/reports/request/"
EXCEL_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FLOOR = date(2000, 1, 1)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
def make_user(username, role=User.Roles.MAKER, dept=None):
    from datetime import datetime

    user = User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.replace("_", " ").title(), last_name="Test",
        role=role, department_ref=dept,
        employment_type=User.EmploymentType.PERMANENT,
        date_of_joining=date(2020, 1, 1))
    User.objects.filter(pk=user.pk).update(
        date_joined=timezone.make_aware(datetime(2020, 1, 1, 9, 0)))
    user.refresh_from_db()
    return user


@pytest.fixture
def dept(db):
    return Department.objects.create(name="Engineering", code="RPT-ENG")


@pytest.fixture
def other_dept(db):
    return Department.objects.create(name="Operations", code="RPT-OPS")


@pytest.fixture
def employee(db, dept):
    return make_user("rpt_emp", User.Roles.MAKER, dept)


@pytest.fixture
def outsider(db, other_dept):
    return make_user("rpt_out", User.Roles.MAKER, other_dept)


@pytest.fixture
def manager(db, dept):
    return make_user("rpt_head", User.Roles.CHECKER, dept)


@pytest.fixture
def hr(db, dept):
    return make_user("rpt_hr", User.Roles.APPROVER, dept)


@pytest.fixture
def admin_user(db, dept):
    return make_user("rpt_admin", User.Roles.ADMIN, dept)


@pytest.fixture
def api():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def auth(api):
    def _login(user):
        api.force_authenticate(user=user)
        return api
    return _login


@pytest.fixture
def window():
    end = timezone.localdate()
    return {"from": (end - timedelta(days=20)).isoformat(), "to": end.isoformat()}


@pytest.fixture
def worked():
    def _worked(user, day, check_in=(10, 0), check_out=(18, 30), **extra):
        record = Attendance(
            employee=user, date=day,
            check_in=timezone.make_aware(
                timezone.datetime(day.year, day.month, day.day, *check_in)),
            check_out=timezone.make_aware(
                timezone.datetime(day.year, day.month, day.day, *check_out))
            if check_out else None, **extra)
        recompute_status(record)
        record.save()
        return record
    return _worked


@pytest.fixture
def overtime_policy(employee):
    policy = AttendancePolicy.objects.create(
        name="OT for reports", overtime_threshold_hours=Decimal("8.00"),
        late_after_time=None, half_day_after_time=None)
    PolicyAssignment.objects.create(
        policy=policy, scope=PolicyAssignment.Scope.USER, user=employee,
        effective_from=FLOOR)
    return policy


# --------------------------------------------------------------------------
# every report, every format
# --------------------------------------------------------------------------
@pytest.mark.parametrize("report_type", WORKFORCE_REPORT_TYPES)
@pytest.mark.parametrize("fmt", ["excel", "csv"])
def test_every_report_builds(report_type, fmt, employee, worked, window):
    """Excel and CSV are pure-Python; PDF needs weasyprint and is covered
    separately so a missing system library cannot mask a logic failure."""
    worked(employee, timezone.localdate() - timedelta(days=1), (10, 0), (19, 0))
    content, filename, content_type = report_service.build_report_file(
        report_type, {**window, "format": fmt})
    assert content, f"{report_type}/{fmt} produced no bytes"
    assert filename.endswith(".xlsx" if fmt == "excel" else ".csv")
    assert content_type


@pytest.mark.parametrize("report_type", WORKFORCE_REPORT_TYPES)
def test_every_report_builds_as_pdf(report_type, employee, worked, window):
    pytest.importorskip("weasyprint")
    worked(employee, timezone.localdate() - timedelta(days=1), (10, 0), (19, 0))
    content, filename, content_type = report_service.build_report_file(
        report_type, {**window, "format": "pdf"})
    assert content.startswith(b"%PDF")
    assert filename.endswith(".pdf")
    assert content_type == "application/pdf"


def test_csv_carries_a_bom_so_excel_opens_it(employee, window):
    content, _name, _ct = report_service.build_report_file(
        "overtime_summary", {**window, "format": "csv"})
    assert content.startswith(b"\xef\xbb\xbf")


def test_all_workforce_types_declare_three_formats():
    from reports.models import REPORT_FORMATS

    for value in WORKFORCE_REPORT_TYPES:
        assert set(REPORT_FORMATS[ReportType(value)]) == {"excel", "pdf", "csv"}


# --------------------------------------------------------------------------
# the arithmetic
# --------------------------------------------------------------------------
def test_overtime_summary_reports_real_overtime(employee, overtime_policy, worked, window):
    from reports.workforce_reports import build_overtime_summary

    day = timezone.localdate() - timedelta(days=2)
    worked(employee, day, (10, 0), (20, 30))  # 10.5h -> 2.5h overtime

    content, _n, _ct = build_overtime_summary({**window, "format": "csv"})
    text = content.decode("utf-8-sig")
    assert "2.50" in text
    assert employee.get_full_name() in text


def test_late_arrival_summary_counts_late_days(employee, worked, window):
    from reports.workforce_reports import build_late_arrival_summary

    day = timezone.localdate() - timedelta(days=2)
    record = worked(employee, day, (12, 30), (18, 30))
    if record.status != Attendance.Status.LATE:
        pytest.skip("fixture day resolves a policy where 12:30 is not Late")

    content, _n, _ct = build_late_arrival_summary({**window, "format": "csv"})
    assert employee.get_full_name() in content.decode("utf-8-sig")


def test_attendance_vs_wfh_flags_approved_but_not_worked(employee, hr, worked, window):
    from reports.workforce_reports import build_attendance_vs_wfh

    day = timezone.localdate() - timedelta(days=2)
    WFHRequest.objects.create(user=employee, start_date=day, end_date=day,
                              status=WFHRequest.Status.APPROVED, reviewed_by=hr)
    # Approved, but nothing recorded: the gap the Phase 8 rule makes visible.
    content, _n, _ct = build_attendance_vs_wfh({**window, "format": "csv"})
    lines = content.decode("utf-8-sig").strip().splitlines()
    assert len(lines) == 2
    assert lines[1].split(",")[5] == "1", "one approved day was not worked"


def test_shift_utilization_groups_by_shift(employee, worked, window):
    from reports.workforce_reports import build_shift_utilization

    EmployeeShift.objects.create(user=employee, shift=Shift.objects.get(code="general"),
                                 effective_from=FLOOR)
    worked(employee, timezone.localdate() - timedelta(days=2), (10, 0), (18, 0))
    content, _n, _ct = build_shift_utilization({**window, "format": "csv"})
    assert "General Shift" in content.decode("utf-8-sig")


def test_department_attendance_lists_every_department(employee, outsider, worked, window):
    from reports.workforce_reports import build_department_attendance

    day = timezone.localdate() - timedelta(days=2)
    worked(employee, day, (10, 0), (18, 30))
    worked(outsider, day, (10, 0), (18, 30))
    text = build_department_attendance({**window, "format": "csv"})[0].decode("utf-8-sig")
    assert "Engineering" in text and "Operations" in text


def test_comp_off_report_shows_pending_and_confirmed(employee, window):
    from reports.workforce_reports import build_comp_off_report

    CompensatoryLedger.objects.create(
        user=employee, entry_type=CompensatoryLedger.EntryType.EARN,
        days=Decimal("1.00"), source=CompensatoryLedger.Source.ATTENDANCE,
        status=CompensatoryLedger.Status.PENDING,
        source_date=timezone.localdate() - timedelta(days=3))
    text = build_comp_off_report({**window, "format": "csv"})[0].decode("utf-8-sig")
    assert employee.get_full_name() in text
    assert "1.00" in text


def test_monthly_workforce_summary_is_a_metric_table(employee, worked, window):
    from reports.workforce_reports import build_monthly_workforce_summary

    worked(employee, timezone.localdate() - timedelta(days=2), (10, 0), (18, 30))
    text = build_monthly_workforce_summary({**window, "format": "csv"})[0].decode("utf-8-sig")
    assert "Headcount" in text
    assert "Overtime hours" in text


def test_attendance_vs_leave_counts_the_overlap(employee, worked, window):
    from leaves.models import Leave, LeaveDayRecord
    from reports.workforce_reports import build_attendance_vs_leave

    day = timezone.localdate() - timedelta(days=3)
    worked(employee, day, (10, 0), (18, 30))
    leave = Leave.objects.create(
        user=employee, leave_type="annual", start_date=day, end_date=day,
        reason="t", status=Leave.Status.APPROVED)
    LeaveDayRecord.objects.filter(leave_request=leave).update(
        status=LeaveDayRecord.Status.APPROVED, day_portion="full")

    text = build_attendance_vs_leave({**window, "format": "csv"})[0].decode("utf-8-sig")
    assert text.strip().splitlines()[1].endswith(",1"), "one overlapping day"


def test_employee_ids_pin_the_scope(employee, outsider, worked, window):
    from reports.workforce_reports import build_department_attendance

    day = timezone.localdate() - timedelta(days=2)
    worked(employee, day, (10, 0), (18, 30))
    worked(outsider, day, (10, 0), (18, 30))
    text = build_department_attendance({
        **window, "format": "csv", "employee_ids": [str(employee.pk)]}).__getitem__(0).decode("utf-8-sig")
    assert "Engineering" in text
    assert "Operations" not in text


# --------------------------------------------------------------------------
# permissions — the F1 fix
# --------------------------------------------------------------------------
def test_hr_can_reach_the_reports_hub(hr, auth):
    """Before Phase 9 the hub was admin-only and HR was locked out."""
    assert auth(hr).get("/api/v1/reports/").status_code == 200


def test_a_manager_can_reach_the_reports_hub(manager, auth):
    assert auth(manager).get("/api/v1/reports/").status_code == 200


def test_an_employee_cannot(employee, auth):
    assert auth(employee).get("/api/v1/reports/").status_code == 403


def test_hr_can_request_a_workforce_report(hr, auth, settings, window):
    settings.REPORTS_RUN_SYNC = True
    response = auth(hr).post(REQUEST_URL, {
        "report_type": "overtime_summary",
        "params": {**window, "format": "csv"}}, format="json")
    assert response.status_code == 202
    assert ReportRun.objects.get(pk=response.data["id"]).status == ReportRun.Status.READY


def test_a_manager_can_request_a_workforce_report(manager, auth, settings, window):
    settings.REPORTS_RUN_SYNC = True
    response = auth(manager).post(REQUEST_URL, {
        "report_type": "department_attendance",
        "params": {**window, "format": "csv"}}, format="json")
    assert response.status_code == 202


def test_a_manager_cannot_request_a_leave_register(manager, auth, window):
    assert auth(manager).post(REQUEST_URL, {
        "report_type": "employee_register", "params": window},
        format="json").status_code == 403


def test_a_managers_scope_is_pinned_server_side(manager, employee, outsider, auth,
                                                settings, window):
    """A manager cannot widen their own report by editing the payload."""
    settings.REPORTS_RUN_SYNC = True
    response = auth(manager).post(REQUEST_URL, {
        "report_type": "department_attendance",
        "params": {**window, "format": "csv", "employee_ids": [str(outsider.pk)]}},
        format="json")
    run = ReportRun.objects.get(pk=response.data["id"])
    assert str(outsider.pk) not in run.params["employee_ids"]
    assert str(employee.pk) in run.params["employee_ids"]


def test_a_manager_only_sees_their_own_runs(manager, hr, auth, settings, window):
    settings.REPORTS_RUN_SYNC = True
    auth(hr).post(REQUEST_URL, {"report_type": "overtime_summary",
                                "params": {**window, "format": "csv"}}, format="json")
    rows = auth(manager).get("/api/v1/reports/").data
    results = rows["results"] if isinstance(rows, dict) else rows
    assert results == [] or all(r["requested_by"] == manager.id for r in results)


def test_hr_sees_every_run(hr, admin_user, auth, settings, window):
    settings.REPORTS_RUN_SYNC = True
    auth(admin_user).post(REQUEST_URL, {"report_type": "overtime_summary",
                                        "params": {**window, "format": "csv"}},
                          format="json")
    rows = auth(hr).get("/api/v1/reports/").data
    results = rows["results"] if isinstance(rows, dict) else rows
    assert len(results) >= 1


def test_a_manager_cannot_schedule_reports(manager, auth):
    assert auth(manager).get("/api/v1/scheduled-reports/").status_code == 403


def test_hr_can_schedule_reports(hr, auth):
    assert auth(hr).get("/api/v1/scheduled-reports/").status_code == 200


def test_admin_access_is_unchanged(admin_user, auth):
    assert auth(admin_user).get("/api/v1/reports/").status_code == 200
    assert auth(admin_user).get("/api/v1/scheduled-reports/").status_code == 200


def test_existing_report_types_still_build(hr, auth, settings):
    """Rule 1: the five pre-existing reports must be untouched."""
    settings.REPORTS_RUN_SYNC = True
    response = auth(hr).post(REQUEST_URL, {
        "report_type": "employee_register",
        "params": {"year": timezone.localdate().year}}, format="json")
    assert response.status_code == 202
    run = ReportRun.objects.get(pk=response.data["id"])
    assert run.status == ReportRun.Status.READY, run.error
