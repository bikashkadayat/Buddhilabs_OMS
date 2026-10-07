"""Workforce routes, under the project's existing /api/v1/ prefix."""
from django.urls import path
from rest_framework.routers import SimpleRouter

from .corrections import AttendanceCorrectionViewSet
from .dashboards import (
    CompOffSummaryView,
    ConflictReportView,
    EmployeeDashboardView,
    HRCommandCenterView,
    LeaveConflictPreviewView,
    ManagerDashboardView,
    WFHSummaryView,
)

router = SimpleRouter()
router.register("workforce/corrections", AttendanceCorrectionViewSet,
                basename="workforce-correction")

urlpatterns = [
    path("workforce/me/", EmployeeDashboardView.as_view(), name="workforce-me"),
    path("workforce/team/", ManagerDashboardView.as_view(), name="workforce-team"),
    path("workforce/hr/", HRCommandCenterView.as_view(), name="workforce-hr"),
    path("workforce/conflicts/", ConflictReportView.as_view(), name="workforce-conflicts"),
    path("workforce/leave-preview/", LeaveConflictPreviewView.as_view(),
         name="workforce-leave-preview"),
    path("workforce/wfh/summary/", WFHSummaryView.as_view(), name="workforce-wfh-summary"),
    path("workforce/comp-off/summary/", CompOffSummaryView.as_view(),
         name="workforce-comp-off-summary"),
] + router.urls
