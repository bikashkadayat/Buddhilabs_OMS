"""The full role x endpoint matrix from the approved permission design.

Employees get nothing. Department heads get the department-scoped subset. HR and
Admin get everything. The export types are covered here too, because a report is
just an analytics payload with a file attached and the same rules must hold.
"""
import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

# (url name, roles that must get 200)
MATRIX = [
    ("analytics-executive", {"approver", "admin"}),
    ("analytics-hr", {"approver", "admin"}),
    ("analytics-management", {"checker", "approver", "admin"}),
    ("analytics-attendance", {"checker", "approver", "admin"}),
    ("analytics-departments", {"checker", "approver", "admin"}),
    ("analytics-leave", {"checker", "approver", "admin"}),
    ("analytics-wfh", {"checker", "approver", "admin"}),
    ("analytics-comp-off", {"checker", "approver", "admin"}),
    ("analytics-devices", {"approver", "admin"}),
    ("analytics-meta", {"checker", "approver", "admin"}),
]


@pytest.fixture
def actors(org):
    return {
        "maker": org["eng"][0],
        "checker": org["manager"],
        "approver": org["hr"],
        "admin": org["admin"],
    }


@pytest.mark.parametrize("url_name,allowed", MATRIX)
@pytest.mark.parametrize("role", ["maker", "checker", "approver", "admin"])
def test_endpoint_role_matrix(url_name, allowed, role, actors, auth):
    client = auth(actors[role])
    response = client.get(reverse(url_name))
    if role in allowed:
        assert response.status_code == 200, f"{role} should reach {url_name}"
    else:
        assert response.status_code == 403, f"{role} must not reach {url_name}"


def test_anonymous_is_rejected_everywhere(api):
    for url_name, _allowed in MATRIX:
        assert api.get(reverse(url_name)).status_code in (401, 403)


class TestExportPermissions:
    """Analytics exports go through the existing reports hub, so they inherit
    its async worker and audit trail -- and must inherit its access rules."""

    def _request(self, client, report_type, fmt):
        return client.post(reverse("report-request-report"),
                           {"report_type": report_type, "params": {"format": fmt}},
                           format="json")

    @pytest.mark.parametrize("report_type,fmt", [
        ("executive_summary", "pdf"),
        ("department_analytics", "pdf"),
        ("attendance_analytics", "excel"),
        ("overtime_analytics", "excel"),
        ("workforce_analytics", "csv"),
    ])
    def test_employee_cannot_request_any_analytics_export(
            self, report_type, fmt, actors, auth):
        response = self._request(auth(actors["maker"]), report_type, fmt)
        # The hub itself is closed to employees.
        assert response.status_code == 403

    def test_department_head_cannot_request_the_executive_summary(self, actors, auth,
                                                                  settings):
        settings.REPORTS_RUN_SYNC = True
        response = self._request(auth(actors["checker"]), "executive_summary", "pdf")
        assert response.status_code == 403

    @pytest.mark.parametrize("report_type,fmt", [
        ("department_analytics", "pdf"),
        ("attendance_analytics", "excel"),
        ("overtime_analytics", "excel"),
        ("workforce_analytics", "csv"),
    ])
    def test_department_head_may_request_the_scoped_exports(
            self, report_type, fmt, actors, auth, settings):
        settings.REPORTS_RUN_SYNC = True
        response = self._request(auth(actors["checker"]), report_type, fmt)
        assert response.status_code == 202

    def test_department_head_export_is_pinned_to_their_own_people(
            self, actors, auth, settings, org):
        """Scope is injected server-side, so editing the payload cannot widen it."""
        settings.REPORTS_RUN_SYNC = True
        from reports.models import ReportRun

        response = self._request(auth(actors["checker"]), "attendance_analytics", "excel")
        run = ReportRun.objects.get(pk=response.data["id"])
        injected = set(run.params["employee_ids"])

        engineering = {str(member.pk) for member in org["eng"]} | {str(org["manager"].pk)}
        operations = {str(member.pk) for member in org["ops"]}
        assert injected == engineering
        assert not (injected & operations)

    def test_hr_export_is_not_narrowed(self, actors, auth, settings):
        settings.REPORTS_RUN_SYNC = True
        from reports.models import ReportRun

        response = self._request(auth(actors["approver"]), "executive_summary", "pdf")
        run = ReportRun.objects.get(pk=response.data["id"])
        assert "employee_ids" not in run.params

    def test_format_is_validated_against_the_report_type(self, actors, auth):
        response = self._request(auth(actors["admin"]), "executive_summary", "csv")
        assert response.status_code == 400
        assert "format" in response.data


class TestKillSwitch:
    def test_urls_are_absent_when_analytics_is_disabled(self):
        """The rollback plan's first rung: no code deploy, no routes."""
        from importlib import reload

        from django.test import override_settings

        from analytics import urls as analytics_urls

        with override_settings(ANALYTICS_ENABLED=False):
            reload(analytics_urls)
            assert analytics_urls.urlpatterns == []
        reload(analytics_urls)
        assert len(analytics_urls.urlpatterns) == 10
