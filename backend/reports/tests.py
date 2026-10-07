from datetime import date, timedelta

import pytest
from django.core import mail
from django.core.management import call_command

from leaves.models import Leave, LeaveType, EnterpriseLeaveBalance
from reports.models import ReportRun, ReportType, ScheduledReport
from reports import report_service
from users.models import User

MONDAY = date.fromisocalendar(2026, 24, 1)


@pytest.fixture
def sync_reports(settings):
    settings.REPORTS_RUN_SYNC = True
    return settings


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(username="radmin", email="radmin@nif.test", password="pass12345", role=User.Roles.ADMIN, department="ENG")


@pytest.fixture
def maker_user(db):
    return User.objects.create_user(username="rmaker", email="rmaker@nif.test", password="pass12345", role=User.Roles.MAKER, department="ENG")


@pytest.fixture
def approved_leave(maker_user):
    leave = Leave.objects.create(user=maker_user, leave_type="annual", reason="x", start_date=MONDAY, end_date=MONDAY + timedelta(days=2))
    leave.status = Leave.Status.APPROVED
    leave.save()
    return leave


# --- generators produce real files ----------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("report_type,params,magic", [
    (ReportType.EMPLOYEE_REGISTER, {}, b"PK"),            # xlsx = zip
    (ReportType.MONTHLY_ATTENDANCE, {"format": "excel"}, b"PK"),
    (ReportType.MONTHLY_ATTENDANCE, {"format": "pdf"}, b"%PDF"),
    (ReportType.LEAVE_UTILIZATION, {}, b"%PDF"),
    (ReportType.COMPLIANCE, {}, b"%PDF"),
    (ReportType.AUDIT_TRAIL, {}, b"PK"),
])
def test_report_generators_produce_files(approved_leave, report_type, params, magic):
    params = {"year": 2026, **params}
    content, filename, ct = report_service.build_report_file(report_type, params)
    assert content[:4].startswith(magic)
    assert filename
    assert ct


# --- async request flow ----------------------------------------------------
@pytest.mark.django_db
def test_request_generates_and_downloads(sync_reports, admin_user, approved_leave):
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(admin_user)

    resp = client.post("/api/v1/reports/request/", {"report_type": "employee_register", "params": {"year": 2026}}, format="json")
    assert resp.status_code == 202, resp.data
    run_id = resp.data["id"]
    assert resp.data["status"] == "ready"  # sync mode completes immediately

    status_resp = client.get(f"/api/v1/reports/{run_id}/status/")
    assert status_resp.data["status"] == "ready"

    dl = client.get(f"/api/v1/reports/{run_id}/download/")
    assert dl.status_code == 200
    body = b"".join(dl.streaming_content)
    assert body[:2] == b"PK"


@pytest.mark.django_db
def test_download_before_ready_conflicts(admin_user):
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(admin_user)
    run = ReportRun.objects.create(report_type=ReportType.COMPLIANCE, requested_by=admin_user, status=ReportRun.Status.PENDING)
    assert client.get(f"/api/v1/reports/{run.id}/download/").status_code == 409


@pytest.mark.django_db
def test_reports_require_admin(maker_user):
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(maker_user)
    assert client.get("/api/v1/reports/").status_code == 403
    assert client.get("/api/v1/reports/analytics/").status_code == 403


# --- analytics -------------------------------------------------------------
@pytest.mark.django_db
def test_analytics_shape(admin_user, approved_leave):
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(admin_user)
    resp = client.get("/api/v1/reports/analytics/?year=2026")
    assert resp.status_code == 200
    for key in ["kpis", "leave_trend", "by_department", "by_type", "top10", "heatmap", "approval_turnaround_hours"]:
        assert key in resp.data
    assert set(["on_leave_today", "attendance_this_month", "pending_approvals", "approaching_limit"]).issubset(resp.data["kpis"].keys())


# --- scheduled reports -----------------------------------------------------
@pytest.mark.django_db
def test_run_scheduled_reports_sends_email(admin_user, approved_leave):
    ScheduledReport.objects.create(
        report_type=ReportType.AUDIT_TRAIL, recipients=["hr@nif.test"],
        cron_expression="@daily", is_active=True, created_by=admin_user,
    )
    mail.outbox.clear()  # ignore any notification emails from fixtures
    call_command("run_scheduled_reports")
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["hr@nif.test"]
    assert mail.outbox[0].attachments  # report attached

    sched = ScheduledReport.objects.first()
    assert sched.last_run_at is not None
    # Second immediate run is not due -> no new email.
    call_command("run_scheduled_reports")
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_purge_expired_reports(admin_user):
    from django.utils import timezone
    old = ReportRun.objects.create(report_type=ReportType.COMPLIANCE, requested_by=admin_user, status=ReportRun.Status.READY)
    ReportRun.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=40))
    call_command("purge_expired_reports")
    assert not ReportRun.objects.filter(pk=old.pk).exists()
