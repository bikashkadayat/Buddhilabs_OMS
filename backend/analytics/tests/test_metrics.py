"""Arithmetic correctness for every KPI in the approved matrix.

Each figure is asserted against a number computed independently in the test
(from ``working_days`` in conftest, not from ``analytics.calendar``), so a bug
in the denominator engine cannot make its own test pass.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from attendance.models import Attendance
from leaves.models import CompensatoryLedger, LeaveDayRecord

from analytics import calendar as work_calendar, periods, scope as scoping
from analytics.metrics import attendance as attendance_metrics
from analytics.metrics import comp_off as comp_off_metrics
from analytics.metrics import departments as department_metrics
from analytics.metrics import devices as device_metrics
from analytics.metrics import kpis as kpi_metrics
from analytics.metrics import leave as leave_metrics
from analytics.metrics import wfh as wfh_metrics

pytestmark = pytest.mark.django_db


def window_for(last_month, granularity="day"):
    start, end = last_month
    return periods.Window(start=start, end=end, granularity=granularity)


def scoped(user):
    return scoping.resolve_scope(user)


# ---------------------------------------------------------------------------
# the denominator
# ---------------------------------------------------------------------------
class TestExpectedWorkingDays:
    def test_excludes_saturdays_and_holidays(self, hr_user, last_month,
                                             last_month_days, holiday):
        target = next(day for day in last_month_days if day.weekday() != 5)
        holiday(target)
        window = window_for(last_month)
        calendar = work_calendar.build(window)

        assert len(calendar.days) == len(last_month_days) - 1
        assert target not in calendar.days
        assert not any(day.weekday() == 5 for day in calendar.days)

    def test_never_counts_days_before_an_employee_joined(self, dept_eng, last_month,
                                                         last_month_days):
        from analytics.tests.conftest import make_user

        midpoint = last_month_days[len(last_month_days) // 2]
        joiner = make_user("an_joiner", dept=dept_eng, joined=midpoint)
        window = window_for(last_month)
        calendar = work_calendar.build(window)

        expected = len([day for day in last_month_days if day >= midpoint])
        assert calendar.expected_for(midpoint) == expected
        # And through the real path: the scope must carry the joiner's floor, or
        # a mid-month starter is charged for a month they were not employed for.
        scope = scoping.resolve_scope(joiner)
        assert work_calendar.expected_total(scope, calendar) == expected

    def test_ignores_the_unfinished_part_of_today(self, hr_user):
        """A month-to-date rate must not be divided by days that have not happened."""
        today = timezone.localdate()
        window = periods.Window(start=today.replace(day=1),
                                end=today + timedelta(days=20), granularity="day")
        calendar = work_calendar.build(window, today)
        assert max(calendar.days) <= today


# ---------------------------------------------------------------------------
# attendance
# ---------------------------------------------------------------------------
class TestAttendanceMetrics:
    def test_full_attendance_is_one_hundred_percent(self, eng_team, manager,
                                                    last_month, last_month_days, mark):
        for member in eng_team:
            mark(member, last_month_days)
        mark(manager, last_month_days)

        window = window_for(last_month)
        summary = attendance_metrics.summary(
            scoped(manager), window, work_calendar.build(window))

        assert summary["present_pct"] == 100.0
        assert summary["compliance_pct"] == 100.0
        assert summary["absent_pct"] == 0.0
        assert summary["late_pct"] == 0.0
        assert summary["expected_days"] == len(last_month_days) * 5
        assert summary["attended_days"] == len(last_month_days) * 5

    def test_missing_days_are_absent_even_with_no_stored_row(
            self, eng_team, manager, last_month, last_month_days, mark):
        """The whole reason this phase does not count rows: an absence has no row."""
        attended = last_month_days[:-2]
        for member in eng_team:
            mark(member, attended)
        mark(manager, attended)

        window = window_for(last_month)
        summary = attendance_metrics.summary(
            scoped(manager), window, work_calendar.build(window))

        expected = len(last_month_days) * 5
        assert summary["expected_days"] == expected
        assert summary["unexplained_days"] == 10  # 2 days x 5 people
        assert summary["compliance_pct"] == pytest.approx(
            (expected - 10) / expected * 100, abs=0.05)
        assert summary["present_pct"] < 100

    def test_approved_leave_is_compliant_but_not_present(
            self, eng_team, manager, last_month, last_month_days, mark, take_leave):
        attended = last_month_days[:-1]
        for member in eng_team:
            mark(member, attended)
            take_leave(member, [last_month_days[-1]])
        mark(manager, last_month_days)

        window = window_for(last_month)
        summary = attendance_metrics.summary(
            scoped(manager), window, work_calendar.build(window))

        assert summary["unexplained_days"] == 0
        assert summary["compliance_pct"] == 100.0
        assert summary["leave_days"] == 4
        assert summary["present_pct"] < 100  # on leave is not present

    def test_half_day_counts_half_present_and_half_absent(
            self, employee, last_month, last_month_days, mark):
        mark(employee, last_month_days[:-1])
        mark(employee, last_month_days[-1:], status=Attendance.Status.HALF_DAY)

        window = window_for(last_month)
        scope = scoping.resolve_scope(employee)
        summary = attendance_metrics.summary(scope, window, work_calendar.build(window))

        total = len(last_month_days)
        assert summary["half_days"] == 1
        assert summary["present_pct"] == pytest.approx((total - 0.5) / total * 100, abs=0.05)
        # A half day still has a row, so nothing is unexplained.
        assert summary["compliance_pct"] == 100.0

    def test_late_rate_divides_by_days_attended_not_expected(
            self, employee, last_month, last_month_days, mark):
        attended = last_month_days[:4]
        mark(employee, attended[:1], status=Attendance.Status.LATE, late_minutes=45)
        mark(employee, attended[1:])

        window = window_for(last_month)
        summary = attendance_metrics.summary(
            scoping.resolve_scope(employee), window, work_calendar.build(window))

        assert summary["late_days"] == 1
        assert summary["late_pct"] == pytest.approx(25.0, abs=0.01)
        assert summary["late_minutes"] == 45

    def test_wfh_days_count_as_attended(self, employee, last_month,
                                        last_month_days, mark):
        mark(employee, last_month_days[:2], status=Attendance.Status.WORK_FROM_HOME,
             is_wfh=True)
        mark(employee, last_month_days[2:])

        window = window_for(last_month)
        summary = attendance_metrics.summary(
            scoping.resolve_scope(employee), window, work_calendar.build(window))

        assert summary["wfh_days"] == 2
        assert summary["compliance_pct"] == 100.0
        assert summary["wfh_pct"] == pytest.approx(2 / len(last_month_days) * 100, abs=0.05)

    def test_overtime_includes_saturday_work(self, employee, last_month, mark):
        """Saturday is not an expected working day, but overtime worked on one is
        still overtime -- excluding it would understate payroll."""
        start, end = last_month
        saturday = next(day for day in
                        (start + timedelta(days=offset) for offset in range((end - start).days + 1))
                        if day.weekday() == 5)
        mark(employee, [saturday], working_hours="6.00", regular_hours="0.00",
             overtime_hours="6.00")

        window = window_for(last_month)
        summary = attendance_metrics.summary(
            scoping.resolve_scope(employee), window, work_calendar.build(window))

        assert summary["overtime_hours"] == 6.0
        # ...but it is not an attended working day.
        assert summary["attended_days"] == 0

    def test_empty_scope_returns_none_not_zero(self, hr_user, last_month):
        """An organisation with no expected days has no attendance rate. Zero
        would rank it bottom of a table it should not be in."""
        window = periods.Window(start=last_month[0], end=last_month[0],
                                granularity="day")
        scope = scoping.resolve_scope(hr_user)
        empty = scoping.Scope(level="organization", employee_ids=(), departments={},
                              department_of={}, floors={})
        summary = attendance_metrics.summary(empty, window, work_calendar.build(window))

        assert summary["present_pct"] is None
        assert summary["compliance_pct"] is None
        assert summary["avg_working_hours"] is None
        assert scope.headcount > 0  # sanity: the real scope is not empty


class TestTrend:
    def test_series_is_dense_and_ordered(self, employee, last_month,
                                         last_month_days, mark):
        mark(employee, last_month_days[:1])
        window = window_for(last_month)
        series = attendance_metrics.trend(
            scoping.resolve_scope(employee), window, work_calendar.build(window))

        start, end = last_month
        assert len(series) == (end - start).days + 1  # every calendar day, not just worked
        assert [point["period"] for point in series] == sorted(
            point["period"] for point in series)
        assert all("compliance_pct" in point for point in series)

    def test_rollup_recomputes_rates_rather_than_averaging(self):
        """Two months of different length must not be weighted equally."""
        monthly = [
            {"period": "2026-01", "expected_days": 20, "attended_days": 20,
             "present_days": 20, "late_days": 0, "half_days": 0, "wfh_days": 0,
             "leave_days": 0, "unexplained_days": 0, "overtime_hours": 0,
             "worked_hours": 0},
            {"period": "2026-02", "expected_days": 10, "attended_days": 0,
             "present_days": 0, "late_days": 0, "half_days": 0, "wfh_days": 0,
             "leave_days": 0, "unexplained_days": 10, "overtime_hours": 0,
             "worked_hours": 0},
        ]
        quarterly = attendance_metrics._rollup(monthly, "quarter")
        assert len(quarterly) == 1
        # 20 of 30 days compliant = 66.7%, NOT the mean of 100% and 0%.
        assert quarterly[0]["compliance_pct"] == pytest.approx(66.7, abs=0.1)


# ---------------------------------------------------------------------------
# departments
# ---------------------------------------------------------------------------
class TestDepartments:
    def test_rows_cover_every_department_in_scope(self, org, last_month,
                                                  last_month_days, mark):
        mark(org["eng"][0], last_month_days)
        window = window_for(last_month)
        rows = department_metrics.rows(
            scoped(org["hr"]), window, work_calendar.build(window))

        names = {row["department"] for row in rows}
        assert {"Engineering", "Operations", "Legal"} <= names

    def test_small_departments_are_listed_but_never_ranked(self, org, last_month,
                                                           last_month_days, mark):
        for member in org["eng"] + org["ops"] + org["tiny"]:
            mark(member, last_month_days)
        window = window_for(last_month)
        rows = department_metrics.rank(department_metrics.rows(
            scoped(org["hr"]), window, work_calendar.build(window)))

        legal = next(row for row in rows if row["department"] == "Legal")
        assert legal["small_sample"] is True
        assert legal["rank"] is None
        assert legal["rank_excluded_reason"] == "small_sample"
        assert legal["health_score"] is None

    def test_ranking_orders_by_compliance_descending(self, org, last_month,
                                                     last_month_days, mark):
        for member in org["eng"]:
            mark(member, last_month_days)          # perfect
        for member in org["ops"]:
            mark(member, last_month_days[:2])      # poor
        window = window_for(last_month)
        rows = department_metrics.rank(department_metrics.rows(
            scoped(org["hr"]), window, work_calendar.build(window)))

        ranked = [row for row in rows if row["rank"] is not None]
        assert ranked[0]["department"] == "Engineering"
        assert ranked[0]["rank"] == 1
        compliances = [row["compliance_pct"] for row in ranked]
        assert compliances == sorted(compliances, reverse=True)

    def test_health_score_uses_published_weights(self):
        perfect = {"compliance_pct": 100.0, "late_pct": 0.0, "absent_pct": 0.0,
                   "overtime_per_capita": 0.0}
        assert department_metrics.health_score(perfect) == 100.0
        assert sum(department_metrics.HEALTH_WEIGHTS.values()) == pytest.approx(1.0)

        worst = {"compliance_pct": 0.0, "late_pct": 100.0, "absent_pct": 100.0,
                 "overtime_per_capita": 100.0}
        assert department_metrics.health_score(worst) == 0.0

    def test_health_score_is_none_without_compliance(self):
        assert department_metrics.health_score({"compliance_pct": None}) is None

    def test_org_average_is_headcount_weighted(self):
        rows = [{"headcount": 90, "compliance_pct": 100.0, "small_sample": False},
                {"headcount": 10, "compliance_pct": 0.0, "small_sample": False}]
        assert department_metrics.org_average(rows) == 90.0


# ---------------------------------------------------------------------------
# leave
# ---------------------------------------------------------------------------
class TestLeave:
    def test_by_type_weights_half_days(self, employee, last_month,
                                       last_month_days, take_leave):
        take_leave(employee, last_month_days[:2])
        take_leave(employee, last_month_days[3:4],
                   portion=LeaveDayRecord.DayPortion.FIRST_HALF)

        window = window_for(last_month)
        rows = leave_metrics.by_type(scoping.resolve_scope(employee), window)
        assert rows[0]["days"] == 2.5
        assert rows[0]["share_pct"] == 100.0

    def test_balance_utilization(self, employee, balance):
        balance(employee, entitled="20.00", used="5.00")
        result = leave_metrics.balance_utilization(scoping.resolve_scope(employee))
        annual = result["types"][0]
        assert annual["utilization_pct"] == 25.0
        assert annual["remaining_days"] == 15.0

    def test_forecast_refuses_to_project_without_history(self, employee):
        result = leave_metrics.forecast(scoping.resolve_scope(employee))
        assert result["method"] == "insufficient_history"
        assert result["is_projection"] is True
        assert all(point["projected_days"] is None for point in result["points"])

    def test_forecast_floor_is_already_approved_leave(self, employee, take_leave):
        """Approved future leave is committed: the projection can never be under it.

        The day is placed in NEXT month, not 20 days out: the forecast covers
        whole future months, so a date in the tail of the current month has no
        bucket to land in.
        """
        today = timezone.localdate()
        future = periods.add_months(today.replace(day=1), 1) + timedelta(days=9)
        while future.weekday() == 5:
            future += timedelta(days=1)
        take_leave(employee, [future])
        result = leave_metrics.forecast(scoping.resolve_scope(employee))
        booked = [point for point in result["points"]
                  if point["approved_days"] > 0]
        assert booked, "the approved future day should land in a forecast bucket"
        for point in booked:
            assert point["approved_days"] == 1.0


# ---------------------------------------------------------------------------
# WFH
# ---------------------------------------------------------------------------
class TestWfh:
    def test_approval_rate_excludes_cancelled_and_pending(self, employee, last_month,
                                                          last_month_days, wfh_request):
        from attendance.models import WFHRequest

        wfh_request(employee, last_month_days[0], status=WFHRequest.Status.APPROVED)
        wfh_request(employee, last_month_days[1], status=WFHRequest.Status.REJECTED)
        wfh_request(employee, last_month_days[2], status=WFHRequest.Status.CANCELLED)
        wfh_request(employee, last_month_days[3], status=WFHRequest.Status.PENDING)

        summary = wfh_metrics.summary(scoping.resolve_scope(employee),
                                      window_for(last_month))
        assert summary["approval_rate_pct"] == 50.0
        assert summary["total_requests"] == 4

    def test_approved_but_not_worked_is_visible(self, employee, last_month,
                                                last_month_days, mark):
        """The Phase 8 rule made visible: approval alone is not attendance."""
        mark(employee, last_month_days[:3], is_wfh=True,
             status=Attendance.Status.WORK_FROM_HOME)
        mark(employee, last_month_days[3:5], is_wfh=True,
             status=Attendance.Status.ABSENT, check_in=False)

        summary = wfh_metrics.summary(scoping.resolve_scope(employee),
                                      window_for(last_month))
        assert summary["approved_day_rows"] == 5
        assert summary["worked_from_home_days"] == 3
        assert summary["approved_not_worked_days"] == 2
        assert summary["conversion_pct"] == 60.0


# ---------------------------------------------------------------------------
# comp off
# ---------------------------------------------------------------------------
class TestCompOff:
    def test_balances_match_the_phase_nine_aggregate(self, employee, comp_entry):
        from attendance.workforce import aggregates

        comp_entry(employee, "2.00", status=CompensatoryLedger.Status.CONFIRMED)
        comp_entry(employee, "1.00", status=CompensatoryLedger.Status.PENDING)
        comp_entry(employee, "0.50", entry_type=CompensatoryLedger.EntryType.USE,
                   source=CompensatoryLedger.Source.LEAVE_USE)

        analytics_result = comp_off_metrics.balances(scoping.resolve_scope(employee))
        phase_nine = aggregates.comp_off_summary([employee.pk])

        assert analytics_result["earned"] == float(phase_nine["earned"])
        assert analytics_result["used"] == float(phase_nine["used"])
        assert analytics_result["pending"] == float(phase_nine["pending"])
        assert analytics_result["available"] == float(phase_nine["available"])

    def test_liability_is_available_days(self, employee, comp_entry, last_month):
        comp_entry(employee, "3.00")
        payload = comp_off_metrics.dashboard(scoping.resolve_scope(employee),
                                             window_for(last_month))
        assert payload["liability_days"] == 3.0
        assert payload["liability_per_capita"] == 3.0


# ---------------------------------------------------------------------------
# devices
# ---------------------------------------------------------------------------
class TestDevices:
    def test_fleet_counts_and_mapping(self, device, enrol, employee):
        online = device("Main Gate")
        device("Back Door", status="offline")
        enrol(online, user=employee, device_user_id="1")
        enrol(online, user=None, device_user_id="2")

        fleet = device_metrics.fleet()
        assert fleet["summary"]["total"] == 2
        assert fleet["summary"]["online"] == 1
        assert fleet["summary"]["offline"] == 1

        mapping = device_metrics.mapping_progress()
        assert mapping["device_users"] == 2
        assert mapping["mapped"] == 1
        assert mapping["unmapped"] == 1
        assert mapping["mapping_pct"] == 50.0

    def test_employees_without_a_device_are_reported(self, device, enrol, org):
        """The reverse mapping gap -- nothing else in the system surfaces it."""
        gate = device("Main Gate")
        enrol(gate, user=org["eng"][0])
        mapping = device_metrics.mapping_progress()
        assert mapping["employees_without_device"] == 11  # 12 active, 1 enrolled

    def test_sync_success_rate_ignores_partial(self, device, sync_log, last_month):
        from biometric.models import DeviceSyncLog

        gate = device()
        start, end = last_month
        sync_log(gate, DeviceSyncLog.Status.SUCCESS, when=start)
        sync_log(gate, DeviceSyncLog.Status.FAILED, when=start)
        sync_log(gate, DeviceSyncLog.Status.PARTIAL, when=start)

        health = device_metrics.sync_health(window_for(last_month))
        assert health["batches"] == 3
        assert health["success_rate_pct"] == pytest.approx(33.3, abs=0.1)
        assert health["partial"] == 1


# ---------------------------------------------------------------------------
# KPI composition
# ---------------------------------------------------------------------------
class TestKpis:
    def test_approval_percentiles_use_the_median(self):
        # 1, 2, 3, 4, 500 -- a mean would read 102, the median reads 3.
        assert kpi_metrics._percentiles([1, 2, 3, 4, 500])["median_hours"] == 3.0

    def test_percentiles_of_nothing_are_none(self):
        result = kpi_metrics._percentiles([])
        assert result["median_hours"] is None and result["sample"] == 0

    def test_queue_ages_bucket_correctly(self, employee, last_month):
        now = timezone.now()
        stamps = [now - timedelta(hours=2), now - timedelta(days=2),
                  now - timedelta(days=5), now - timedelta(days=30)]
        buckets = kpi_metrics._age_buckets(stamps, now)
        assert buckets == {"under_1d": 1, "1_to_3d": 1, "3_to_7d": 1, "over_7d": 1}

    def test_standard_day_hours_comes_from_policy_not_a_literal(self, db):
        from attendance.models import AttendancePolicy

        # A migration seeds a global policy, so retire it first: the point is
        # that the value is READ from policy, not that 8 happens to be right.
        AttendancePolicy.objects.update(is_active=False)
        AttendancePolicy.objects.create(name="Analytics test policy",
                                        full_day_hours=Decimal("7.50"),
                                        is_active=True)
        assert kpi_metrics.standard_day_hours() == 7.5

    def test_utilization_against_calendar_capacity(self, employee, last_month,
                                                   last_month_days, mark):
        mark(employee, last_month_days, regular_hours="8.00")
        window = window_for(last_month)
        calendar = work_calendar.build(window)
        scope = scoping.resolve_scope(employee)
        summary = attendance_metrics.summary(scope, window, calendar)
        result = kpi_metrics.utilization(scope, window, calendar,
                                         summary["regular_hours"])

        assert result["expected_days"] == len(last_month_days)
        assert result["utilization_pct"] == pytest.approx(100.0, abs=0.5)

    def test_capacity_subtracts_approved_future_leave(self, employee, take_leave):
        today = timezone.localdate()
        upcoming = [today + timedelta(days=offset) for offset in (3, 4)
                    if (today + timedelta(days=offset)).weekday() != 5]
        take_leave(employee, upcoming)

        result = kpi_metrics.capacity_outlook(scoping.resolve_scope(employee))
        row = result["departments"][0]
        assert row["leave_days"] == float(len(upcoming))
        assert row["available_days"] == row["capacity_days"] - len(upcoming)

    def test_overtime_overview_reports_spread_not_just_total(
            self, org, last_month, last_month_days, mark):
        mark(org["eng"][0], last_month_days[:1], overtime_hours="10.00")
        window = window_for(last_month)
        scope = scoped(org["hr"])
        summary = attendance_metrics.summary(scope, window, work_calendar.build(window))
        overview = kpi_metrics.overtime_overview(scope, window, summary)

        assert overview["total_hours"] == 10.0
        assert overview["employees_with_overtime"] == 1
        assert overview["employees_pct"] == pytest.approx(100 / scope.headcount, abs=0.1)
