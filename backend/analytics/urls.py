"""Analytics routes, under the project's existing /api/v1/ prefix.

Gated by ``ANALYTICS_ENABLED``: with the flag off this module registers nothing,
every analytics path 404s and the app is inert. That is the sub-minute kill
switch from the approved rollback plan -- it needs no code deploy.
"""
from django.conf import settings
from django.urls import path

from .views import (
    AnalyticsMetaView,
    AttendanceAnalyticsView,
    CompOffAnalyticsView,
    DepartmentAnalyticsView,
    DeviceAnalyticsView,
    ExecutiveDashboardView,
    HRKpiView,
    LeaveAnalyticsView,
    ManagementKpiView,
    WfhAnalyticsView,
)

urlpatterns = []

if getattr(settings, "ANALYTICS_ENABLED", True):
    urlpatterns = [
        path("analytics/executive/", ExecutiveDashboardView.as_view(),
             name="analytics-executive"),
        path("analytics/hr/", HRKpiView.as_view(), name="analytics-hr"),
        path("analytics/management/", ManagementKpiView.as_view(),
             name="analytics-management"),
        path("analytics/attendance/", AttendanceAnalyticsView.as_view(),
             name="analytics-attendance"),
        path("analytics/departments/", DepartmentAnalyticsView.as_view(),
             name="analytics-departments"),
        path("analytics/leave/", LeaveAnalyticsView.as_view(), name="analytics-leave"),
        path("analytics/wfh/", WfhAnalyticsView.as_view(), name="analytics-wfh"),
        path("analytics/comp-off/", CompOffAnalyticsView.as_view(),
             name="analytics-comp-off"),
        path("analytics/devices/", DeviceAnalyticsView.as_view(),
             name="analytics-devices"),
        path("analytics/meta/", AnalyticsMetaView.as_view(), name="analytics-meta"),
    ]
