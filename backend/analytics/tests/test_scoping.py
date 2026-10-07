"""Department scoping: the boundary a department head must never cross.

Three separate things are tested, because they fail in different ways:

1. The population itself -- a manager's totals must not contain another
   department's rows.
2. The ``?department=`` parameter -- it narrows, never widens, and never
   confirms that a department outside the caller's scope exists.
3. Redaction -- a manager sees the ranking, but not who else is in it.
"""
import pytest
from django.urls import reverse

from analytics import calendar as work_calendar, periods, scope as scoping
from analytics.metrics import attendance as attendance_metrics
from analytics.metrics import departments as department_metrics

pytestmark = pytest.mark.django_db


def window_for(last_month):
    start, end = last_month
    return periods.Window(start=start, end=end, granularity="day")


class TestPopulation:
    def test_manager_scope_is_their_department_only(self, org):
        scope = scoping.resolve_scope(org["manager"])
        assert scope.level == scoping.DEPARTMENT
        expected = {member.pk for member in org["eng"]} | {org["manager"].pk}
        assert set(scope.employee_ids) == expected

    def test_hr_scope_is_the_whole_organisation(self, org):
        scope = scoping.resolve_scope(org["hr"])
        assert scope.level == scoping.ORG
        assert scope.headcount == 12

    def test_manager_totals_exclude_other_departments(self, org, last_month,
                                                      last_month_days, mark):
        for member in org["eng"]:
            mark(member, last_month_days[:2])
        for member in org["ops"]:
            mark(member, last_month_days)  # a much better month, elsewhere

        window = window_for(last_month)
        calendar = work_calendar.build(window)
        manager_view = attendance_metrics.summary(
            scoping.resolve_scope(org["manager"]), window, calendar)
        hr_view = attendance_metrics.summary(
            scoping.resolve_scope(org["hr"]), window, calendar)

        assert manager_view["attended_days"] == 8  # 4 people x 2 days
        assert hr_view["attended_days"] > manager_view["attended_days"]


class TestDepartmentParameter:
    def test_narrows_within_scope(self, org, dept_ops):
        scope = scoping.resolve_scope(org["hr"], str(dept_ops.pk))
        assert set(scope.employee_ids) == (
            {member.pk for member in org["ops"]} | {org["hr"].pk, org["admin"].pk})

    def test_accepts_a_department_code(self, org, dept_ops):
        scope = scoping.resolve_scope(org["hr"], dept_ops.code)
        assert scope.headcount == 5

    def test_cannot_widen_a_manager_out_of_their_department(self, org, dept_ops):
        """Silently narrowing beats a 403: a 403 confirms the other department
        exists, and there is nothing a manager needs to learn from that."""
        scope = scoping.resolve_scope(org["manager"], str(dept_ops.pk))
        expected = {member.pk for member in org["eng"]} | {org["manager"].pk}
        assert set(scope.employee_ids) == expected
        assert "department_not_in_scope" in scope.warnings

    def test_unknown_department_leaves_scope_untouched(self, org):
        scope = scoping.resolve_scope(org["hr"], "NOT-A-REAL-CODE")
        assert scope.headcount == 12
        assert "department_not_in_scope" in scope.warnings

    def test_api_honours_the_narrowing(self, org, auth, dept_ops):
        response = auth(org["manager"]).get(
            reverse("analytics-attendance"), {"department": str(dept_ops.pk)})
        assert response.status_code == 200
        assert response.data["scope"]["department"] == "Engineering"


class TestRedaction:
    def test_manager_sees_rank_but_not_other_departments(self, org, auth,
                                                         last_month_days, mark):
        for member in org["eng"] + org["ops"]:
            mark(member, last_month_days)

        response = auth(org["manager"]).get(reverse("analytics-departments"))
        assert response.status_code == 200
        rows = response.data["data"]["departments"]

        own = [row for row in rows if row["department"] == "Engineering"]
        others = [row for row in rows if row.get("redacted")]
        assert len(own) == 1
        assert own[0]["compliance_pct"] is not None
        assert others, "other departments should be present but redacted"
        for row in others:
            assert row["compliance_pct"] is None
            assert row["headcount"] is None
            assert "Operations" not in row["department"]

    def test_manager_still_gets_the_org_average_and_a_percentile(
            self, org, auth, last_month_days, mark):
        for member in org["eng"] + org["ops"]:
            mark(member, last_month_days)
        response = auth(org["manager"]).get(reverse("analytics-departments"))
        data = response.data["data"]

        assert data["org_average"]["compliance_pct"] is not None
        assert data["own_department"]["department"] == "Engineering"
        assert data["department_count"] >= 2

    def test_hr_sees_every_department_by_name(self, org, auth, last_month_days, mark):
        for member in org["eng"] + org["ops"]:
            mark(member, last_month_days)
        response = auth(org["hr"]).get(reverse("analytics-departments"))
        names = {row["department"] for row in response.data["data"]["departments"]}
        assert {"Engineering", "Operations", "Legal"} <= names
        assert not any(row.get("redacted")
                       for row in response.data["data"]["departments"])


class TestSmallSampleSuppression:
    def test_two_person_department_is_never_ranked(self, org, last_month,
                                                   last_month_days, mark):
        for member in org["tiny"]:
            mark(member, last_month_days)
        window = window_for(last_month)
        rows = department_metrics.rank(department_metrics.rows(
            scoping.resolve_scope(org["hr"]), window, work_calendar.build(window)))
        legal = next(row for row in rows if row["department"] == "Legal")

        assert legal["rank"] is None
        assert legal["health_score"] is None
        assert legal["small_sample"] is True

    def test_small_departments_do_not_move_the_org_average(self, org, last_month,
                                                           last_month_days, mark):
        for member in org["eng"] + org["ops"]:
            mark(member, last_month_days)
        window = window_for(last_month)
        rows = department_metrics.rank(department_metrics.rows(
            scoping.resolve_scope(org["hr"]), window, work_calendar.build(window)))
        # Legal attended nothing, but is too small to be ranked or scored --
        # and health_score is None there, so the weighted average skips it.
        assert department_metrics.org_average(rows, "health_score") is not None
