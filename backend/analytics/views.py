"""The nine analytics endpoints.

Every view is the same four lines of work: resolve the window, resolve the
scope, ask the cache for the payload (building it through a ``metrics``
function on a miss), and wrap it in the envelope. All the arithmetic lives in
``metrics/``; all the access control lives in ``permissions`` and ``scope``.

Keeping the views this thin is what lets the export builders reuse the exact
same metric calls -- a PDF and the dashboard it came from cannot disagree,
because they run the same function.
"""
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from . import cache as analytics_cache, calendar as work_calendar, periods, scope as scoping
from .metrics import attendance as attendance_metrics
from .metrics import comp_off as comp_off_metrics
from .metrics import departments as department_metrics
from .metrics import devices as device_metrics
from .metrics import kpis as kpi_metrics
from .metrics import leave as leave_metrics
from .metrics import wfh as wfh_metrics
from .permissions import CanViewAnalytics, IsInfrastructureAnalytics, IsOrgAnalytics
from .serializers import envelope


class AnalyticsView(APIView):
    """Shared plumbing: window, scope, cache, envelope.

    Subclasses provide ``endpoint``, ``default_preset`` and ``build(...)``.
    """
    permission_classes = [CanViewAnalytics]
    endpoint = "analytics"
    default_preset = "last_12m"
    needs_calendar = True

    def get(self, request):
        today = timezone.localdate()
        window = periods.parse_window(request.query_params,
                                      default_preset=self.default_preset, today=today)
        current_scope = self.resolve_scope(request)
        calendar = work_calendar.build(window, today) if self.needs_calendar else None

        payload, cached = analytics_cache.get_or_build(
            self.endpoint, current_scope, window,
            lambda: self.build(request, current_scope, window, calendar, today),
            extra_token=self.cache_token(request), today=today)

        return Response(envelope(
            window=window, scope=current_scope, data=payload, cached=cached,
            ttl=analytics_cache.ttl_for(window, self.endpoint, today)))

    def resolve_scope(self, request):
        return scoping.resolve_scope(request.user,
                                     request.query_params.get("department"))

    def cache_token(self, request):
        """Extra request state that changes the payload. Overridden where a view
        reads a query parameter the window does not already cover."""
        return ""

    def build(self, request, current_scope, window, calendar, today):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# composed dashboards
# ---------------------------------------------------------------------------
class ExecutiveDashboardView(AnalyticsView):
    """GET /api/v1/analytics/executive/ -- headline organisational health."""
    permission_classes = [IsOrgAnalytics]
    endpoint = "executive"
    default_preset = "mtd"

    def build(self, request, current_scope, window, calendar, today):
        return kpi_metrics.executive(current_scope, window, calendar, today)


class HRKpiView(AnalyticsView):
    """GET /api/v1/analytics/hr/ -- operational quality and approval health."""
    permission_classes = [IsOrgAnalytics]
    endpoint = "hr"
    default_preset = "last_12m"

    def build(self, request, current_scope, window, calendar, today):
        return kpi_metrics.hr(current_scope, window, calendar, today)


class ManagementKpiView(AnalyticsView):
    """GET /api/v1/analytics/management/ -- trends, utilization, capacity."""
    endpoint = "management"
    default_preset = "last_12m"

    def build(self, request, current_scope, window, calendar, today):
        return kpi_metrics.management(current_scope, window, calendar, today)


# ---------------------------------------------------------------------------
# domain drill-downs
# ---------------------------------------------------------------------------
class AttendanceAnalyticsView(AnalyticsView):
    """GET /api/v1/analytics/attendance/ -- rates, exceptions, comparisons."""
    endpoint = "attendance"

    def build(self, request, current_scope, window, calendar, today):
        return attendance_metrics.dashboard(current_scope, window, calendar, today)


class DepartmentAnalyticsView(AnalyticsView):
    """GET /api/v1/analytics/departments/ -- comparison and compliance ranking.

    The ranking is computed over the ORGANISATION even for a department head, so
    their rank and the org average are meaningful. ``departments.redact`` then
    removes every other department's identity and figures before the response is
    assembled -- see the approved permission matrix.
    """
    endpoint = "departments"

    def build(self, request, current_scope, window, calendar, today):
        org = (None if current_scope.level == scoping.ORG
               else scoping.resolve_org_scope())
        return department_metrics.dashboard(current_scope, window, calendar, org)


class LeaveAnalyticsView(AnalyticsView):
    """GET /api/v1/analytics/leave/ -- usage, utilization, distribution, forecast."""
    endpoint = "leave"

    def build(self, request, current_scope, window, calendar, today):
        return leave_metrics.dashboard(current_scope, window, today)


class WfhAnalyticsView(AnalyticsView):
    """GET /api/v1/analytics/wfh/ -- trend, approval rate, conversion."""
    endpoint = "wfh"

    def build(self, request, current_scope, window, calendar, today):
        return wfh_metrics.dashboard(current_scope, window)


class CompOffAnalyticsView(AnalyticsView):
    """GET /api/v1/analytics/comp-off/ -- earned, used, pending, liability."""
    endpoint = "comp_off"

    def build(self, request, current_scope, window, calendar, today):
        return comp_off_metrics.dashboard(current_scope, window)


class DeviceAnalyticsView(AnalyticsView):
    """GET /api/v1/analytics/devices/ -- fleet health and mapping progress.

    HR and Admin only, and deliberately unscoped by employee: a terminal belongs
    to the organisation, not to a department. Not the Board: infrastructure.
    """
    permission_classes = [IsInfrastructureAnalytics]
    endpoint = "devices"
    default_preset = "last_30d"
    needs_calendar = False

    def build(self, request, current_scope, window, calendar, today):
        return device_metrics.dashboard(window)


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------
class AnalyticsMetaView(APIView):
    """GET /api/v1/analytics/meta/ -- window presets, departments, KPI definitions.

    The definitions are served rather than duplicated in the frontend so a
    tooltip can never drift from the formula that produced the number. A KPI
    nobody can define is a KPI nobody trusts.
    """
    permission_classes = [CanViewAnalytics]

    def get(self, request):
        current_scope = scoping.resolve_scope(request.user)
        return Response({
            "presets": list(periods.PRESETS),
            "granularities": list(periods.GRANULARITIES),
            "max_window_months": periods.MAX_WINDOW_MONTHS,
            "max_points": periods.MAX_POINTS,
            "scope": current_scope.as_dict(),
            "departments": ([{"id": None if key == scoping.UNASSIGNED else key,
                              "name": name,
                              "headcount": current_scope.department_headcount(key)}
                             for key, name in sorted(current_scope.departments.items(),
                                                     key=lambda item: item[1])]
                            if current_scope.named_departments else []),
            "health_weights": department_metrics.HEALTH_WEIGHTS,
            "overtime_stress_ceiling": department_metrics.OVERTIME_STRESS_CEILING,
            "min_department_sample": scoping.MIN_DEPARTMENT_SAMPLE,
            "definitions": DEFINITIONS,
        })


# Served to the UI as tooltips. Kept beside the endpoints rather than in a
# document nobody opens.
DEFINITIONS = {
    "expected_working_days": (
        "Days in the window that are not Saturday, not an active holiday, on or "
        "after the employee joined, and not in the future. The denominator for "
        "every attendance percentage."),
    "present_pct": (
        "(Present + Late + WFH + half of Half Days) / expected working days."),
    "absent_pct": (
        "Expected working days not covered by attendance or approved leave, as a "
        "percentage of expected working days. A half day counts half absent."),
    "late_pct": (
        "Late days / days actually attended. The denominator is attendance, not "
        "expectation -- being late requires turning up."),
    "half_day_pct": "Half days / days actually attended.",
    "wfh_pct": (
        "Work-from-home days / days actually attended. An approved WFH day with "
        "no check-in is not counted: approval alone is not attendance."),
    "leave_pct": (
        "Approved leave days (half days weighted 0.5) / expected working days."),
    "compliance_pct": (
        "Expected working days minus unexplained absences, over expected working "
        "days. Approved leave is compliant; an unexplained absence is a working "
        "day with no attendance record and no approved leave."),
    "overtime_hours": (
        "Hours recorded beyond the policy threshold, summed from the stored "
        "per-day overtime column. Payroll-ready."),
    "avg_working_hours": "Total worked hours / days actually attended.",
    "department_health_score": (
        "Composite 0-100: 50% attendance compliance, 20% punctuality "
        "(100 - late %), 20% presence (100 - absent %), 10% overtime load. "
        "Weights are returned with every response."),
    "compliance_rank": (
        "Dense rank on attendance compliance only, descending. Departments with "
        "fewer than three active employees are listed but not ranked."),
    "utilization_pct": (
        "Regular hours worked / (expected working days x the policy's standard "
        "working day)."),
    "capacity_days": (
        "Working days available in the next 30 days after subtracting approved "
        "leave. Nothing is predicted."),
    "leave_forecast": (
        "A projection, not a model: the greater of leave already approved for "
        "that month and the mean of the same calendar month in prior years. "
        "Returns nothing when there is under six months of history."),
    "wfh_approval_rate_pct": (
        "Approved / (approved + rejected). Cancelled and pending requests are "
        "excluded -- a withdrawn request says nothing about how HR decides."),
    "sync_success_rate_pct": (
        "Successful ingest batches / total batches. Partial batches count as "
        "neither."),
    "approval_time": (
        "Median and 90th-percentile hours from submission to decision. Median, "
        "not mean: one forgotten request distorts a mean beyond use."),
}
