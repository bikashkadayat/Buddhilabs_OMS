"""Employee, manager and HR dashboards.

Two things are asserted throughout: the numbers are correct, and they are
role-scoped — nobody sees over a dashboard what they could not already see over
REST. Query budgets from the Phase 9 design are enforced here so an N+1 cannot
creep back in unnoticed.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from attendance.models import Attendance, AttendanceCorrectionRequest, WFHRequest
from attendance.services import recompute_status
from attendance.workforce import aggregates
from users.models import User

pytestmark = pytest.mark.django_db

ME = "/api/v1/workforce/me/"
TEAM = "/api/v1/workforce/team/"
HR = "/api/v1/workforce/hr/"

# From the approved design. These are ceilings, not targets.
BUDGET_ME = 12
BUDGET_TEAM = 15
BUDGET_HR = 20


@pytest.fixture
def worked(local):
    """Record a day for someone and evaluate it through the real engine."""
    def _worked(user, day, check_in=(10, 0), check_out=(18, 30), **extra):
        record = Attendance(
            employee=user, date=day,
            check_in=local(day.year, day.month, day.day, *check_in),
            check_out=local(day.year, day.month, day.day, *check_out) if check_out else None,
            **extra)
        recompute_status(record)
        record.save()
        return record
    return _worked


# --------------------------------------------------------------------------
# employee dashboard
# --------------------------------------------------------------------------
def test_the_employee_dashboard_reports_today(employee, auth, worked):
    today = timezone.localdate()
    worked(employee, today, (10, 0), (18, 30))

    data = auth(employee).get(ME).data
    assert data["employee"]["id"] == str(employee.id)
    assert data["today"]["check_in"] is not None
    assert data["today"]["working_hours"] == "8.50"
    assert data["today"]["can_check_in"] is False
    assert data["today"]["can_check_out"] is False


def test_the_employee_dashboard_exposes_the_effective_policy(employee, auth):
    data = auth(employee).get(ME).data
    assert data["policy"]["office_start"] == "10:00"
    # The NIF cutover is effective from today, so today resolves the new rules.
    assert data["policy"]["late_after"] == "11:45"
    assert data["policy"]["half_day_after"] == "13:00"


def test_the_employee_dashboard_includes_comp_off_and_leave(employee, auth):
    data = auth(employee).get(ME).data
    assert set(data["comp_off"]) == {"earned", "used", "available", "pending"}
    assert isinstance(data["leave_balances"], list)


def test_the_employee_dashboard_includes_recent_history(employee, auth, worked):
    today = timezone.localdate()
    worked(employee, today - timedelta(days=1), (12, 30), (18, 30))
    history = auth(employee).get(ME).data["recent_attendance"]
    assert len(history) == aggregates.RECENT_HISTORY_DAYS
    assert history[0]["date"] == today.isoformat(), "newest first"


def test_the_employee_dashboard_counts_my_corrections(employee, auth, a_weekday):
    AttendanceCorrectionRequest.objects.create(
        employee=employee, attendance_date=a_weekday, reason="x",
        requested_status=Attendance.Status.PRESENT)
    assert auth(employee).get(ME).data["corrections"]["open"] == 1


def test_the_employee_dashboard_shows_wfh_state(employee, hr, auth):
    today = timezone.localdate()
    WFHRequest.objects.create(user=employee, start_date=today, end_date=today,
                              status=WFHRequest.Status.APPROVED, reviewed_by=hr)
    assert auth(employee).get(ME).data["wfh"]["approved_today"] is True


def test_the_employee_dashboard_only_ever_shows_yourself(employee, coworker, auth, worked):
    today = timezone.localdate()
    worked(coworker, today, (10, 0), (18, 30))
    data = auth(employee).get(ME).data
    assert data["employee"]["id"] == str(employee.id)
    assert data["today"]["check_in"] is None


def test_the_employee_dashboard_stays_within_its_query_budget(
        employee, auth, worked, django_assert_max_num_queries,
        tenant_binding_queries):
    worked(employee, timezone.localdate(), (10, 0), (18, 30))
    client = auth(employee)
    client.get(ME)  # warm content types / permissions
    # + the tenant binding: one round trip per request under RLS, zero
    # otherwise. See the `tenant_binding_queries` fixture.
    with django_assert_max_num_queries(BUDGET_ME + tenant_binding_queries):
        client.get(ME)


# --------------------------------------------------------------------------
# manager dashboard
# --------------------------------------------------------------------------
def test_an_employee_cannot_open_the_manager_dashboard(employee, auth):
    assert auth(employee).get(TEAM).status_code == 403


def test_the_manager_dashboard_counts_the_department(dept_head, employee, coworker,
                                                     auth, worked):
    today = timezone.localdate()
    worked(employee, today, (10, 0), (18, 30))
    worked(coworker, today, (12, 30), (18, 30))

    data = auth(dept_head).get(TEAM).data
    assert data["counts"]["present"] >= 1
    assert data["counts"]["late"] >= 1
    assert data["present_now"] >= 2


def test_the_manager_dashboard_excludes_other_departments(dept_head, outsider, auth,
                                                          worked):
    worked(outsider, timezone.localdate(), (10, 0), (18, 30))
    data = auth(dept_head).get(TEAM).data
    assert all(row["department"] != "Operations"
               for row in data["department_summary"])


def test_the_manager_dashboard_reports_its_queues(dept_head, employee, auth, a_weekday):
    AttendanceCorrectionRequest.objects.create(
        employee=employee, attendance_date=a_weekday, reason="x",
        requested_status=Attendance.Status.PRESENT, manager=dept_head)
    WFHRequest.objects.create(user=employee, start_date=a_weekday, end_date=a_weekday)

    queues = auth(dept_head).get(TEAM).data["queues"]
    assert queues["corrections_manager_stage"] == 1
    assert queues["wfh_pending"] == 1


def test_the_manager_dashboard_returns_a_trend_series(dept_head, auth):
    trend = auth(dept_head).get(TEAM).data["trends"]
    assert len(trend) == aggregates.TREND_DAYS
    assert trend[-1]["date"] == timezone.localdate().isoformat()


def test_the_manager_dashboard_names_absent_employees(dept_head, employee, auth):
    data = auth(dept_head).get(TEAM).data
    names = {row["id"] for row in data["absent_employees"]}
    # Whether today is already judged absent depends on the cut-off; either way
    # the field must be a list of identifiable people, not a bare number.
    assert isinstance(data["absent_employees"], list)
    assert names <= {str(e.pk) for e in aggregates.scoped_employees(dept_head)}


def test_the_manager_dashboard_stays_within_its_query_budget(
        dept_head, employee, coworker, auth, worked,
        django_assert_max_num_queries, tenant_binding_queries):
    worked(employee, timezone.localdate(), (10, 0), (18, 30))
    client = auth(dept_head)
    client.get(TEAM)
    # + the tenant binding: one round trip per request under RLS, zero
    # otherwise. See the `tenant_binding_queries` fixture.
    with django_assert_max_num_queries(BUDGET_TEAM + tenant_binding_queries):
        client.get(TEAM)


# --------------------------------------------------------------------------
# HR command center
# --------------------------------------------------------------------------
@pytest.mark.parametrize("role_fixture", ["employee", "dept_head"])
def test_the_hr_dashboard_is_hr_only(request, auth, role_fixture):
    user = request.getfixturevalue(role_fixture)
    assert auth(user).get(HR).status_code == 403


def test_the_hr_dashboard_covers_every_department(hr, employee, outsider, auth, worked):
    today = timezone.localdate()
    worked(employee, today, (10, 0), (18, 30))
    worked(outsider, today, (10, 0), (18, 30))

    data = auth(hr).get(HR).data
    departments = {row["department"] for row in data["department_breakdown"]}
    assert {"Engineering", "Operations"} <= departments
    assert data["present_now"] >= 2


def test_the_hr_dashboard_splits_devices_by_state(hr, auth):
    from biometric.models import BiometricDevice

    BiometricDevice.objects.create(name="Gate", label="gate")
    devices = auth(hr).get(HR).data["devices"]
    assert "online" in devices and "offline" in devices
    assert devices["pending_mapping"] == 0


def test_the_hr_dashboard_reports_every_queue(hr, employee, auth, a_weekday):
    AttendanceCorrectionRequest.objects.create(
        employee=employee, attendance_date=a_weekday, reason="x",
        requested_status=Attendance.Status.PRESENT,
        status=AttendanceCorrectionRequest.Status.MANAGER_APPROVED)
    queues = auth(hr).get(HR).data["queues"]
    assert queues["corrections_hr_stage"] == 1
    assert "comp_off_pending" in queues
    assert "leave_pending_hr" in queues


def test_the_hr_dashboard_includes_comp_off_and_wfh(hr, auth):
    data = auth(hr).get(HR).data
    assert set(data["comp_off"]) == {"earned", "used", "available", "pending"}
    assert "conversion" in data["wfh"]


def test_the_hr_dashboard_survives_every_status(hr, employee, auth, worked):
    """The Phase 8 R1 lesson: a hardcoded counts dict turned a new status into
    an HTTP 500. Every status value must be representable."""
    today = timezone.localdate()
    record = worked(employee, today, (10, 0), (18, 30))
    for status in Attendance.Status:
        Attendance.objects.filter(pk=record.pk).update(status=status)
        response = auth(hr).get(HR)
        assert response.status_code == 200, f"failed for status={status}"
        assert status.value in response.data["counts"]


def test_the_hr_dashboard_stays_within_its_query_budget(
        hr, employee, auth, worked, django_assert_max_num_queries,
        tenant_binding_queries):
    worked(employee, timezone.localdate(), (10, 0), (18, 30))
    client = auth(hr)
    client.get(HR)
    # + the tenant binding: one round trip per request under RLS, zero
    # otherwise. See the `tenant_binding_queries` fixture.
    with django_assert_max_num_queries(BUDGET_HR + tenant_binding_queries):
        client.get(HR)


# --------------------------------------------------------------------------
# aggregation layer directly
# --------------------------------------------------------------------------
def test_empty_counts_is_derived_from_the_enum():
    counts = aggregates.empty_counts()
    for status in Attendance.Status:
        assert status.value in counts
    assert "not_applicable" in counts


def test_present_now_includes_work_from_home():
    counts = aggregates.empty_counts()
    counts["present"], counts["late"] = 2, 1
    counts["half_day"], counts[Attendance.Status.WORK_FROM_HOME] = 1, 3
    assert aggregates.present_now(counts) == 7


def test_the_trend_query_does_not_scale_with_the_window(
        employee, worked, django_assert_num_queries):
    today = timezone.localdate()
    for offset in range(5):
        worked(employee, today - timedelta(days=offset + 1), (10, 0), (18, 30))
    with django_assert_num_queries(1):
        aggregates.attendance_trend([employee.pk])


def test_the_department_breakdown_does_not_scale_with_departments(
        hr, employee, outsider, worked, django_assert_max_num_queries):
    today = timezone.localdate()
    worked(employee, today, (10, 0), (18, 30))
    worked(outsider, today, (10, 0), (18, 30))
    # scoped_employees is what production passes in, and it select_related's
    # department_ref — without that the per-employee department lookup is an
    # N+1 that grows with headcount.
    employees = list(aggregates.scoped_employees(hr))
    assert len({e.department_ref_id for e in employees}) >= 2
    # RAISED FROM 4 TO 5 (app-attendance phase), deliberately and once.
    #
    # Attendance settings are now resolved per TENANT rather than from one
    # deployment-wide value -- `OrganizationSettings.full_day_hours` and its
    # neighbours were editable in the console and read by nothing, so every
    # customer shared one office location and one working day. The honest
    # cost is one query for the settings row, cached for five minutes per
    # organization, and this budget is measured on a cold cache.
    #
    # The PROPERTY this test is named for is unchanged: the cost is +1
    # constant, not +1 per department. The assertion below is what keeps it
    # that way.
    with django_assert_max_num_queries(5):
        aggregates.department_breakdown(employees, today)

    # And it stays flat as departments grow, which is the actual claim.
    from leaves.models import Department

    for n in range(6):
        Department.objects.create(name=f"Extra {n}", code=f"ATT-X{n}")
    with django_assert_max_num_queries(5):
        aggregates.department_breakdown(employees, today)
