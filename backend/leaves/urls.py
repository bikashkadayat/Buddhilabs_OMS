from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views
from . import admin_views
from . import comp_views

# SimpleRouter (no API-root view) avoids clashing with the other routers that
# are also mounted under /api/v1/.
router = SimpleRouter()
router.register("leave-types", views.LeaveTypeViewSet, basename="leave-type")
router.register("holidays", views.HolidayViewSet, basename="holiday")
router.register("leave-day-records", views.LeaveDayRecordViewSet, basename="leave-day-record")
router.register("leave-balances", views.EnterpriseLeaveBalanceViewSet, basename="enterprise-balance")
router.register("weekly-summaries", views.WeeklyLeaveSummaryViewSet, basename="weekly-summary")
router.register("monthly-summaries", views.MonthlyLeaveSummaryViewSet, basename="monthly-summary")

# Phase 7 - HR/Admin (mounted under /api/v1/leaves/admin/*)
admin_router = SimpleRouter()
admin_router.register("leaves/admin/employees", admin_views.AdminEmployeeLeaveViewSet, basename="admin-employee")
admin_router.register("leaves/admin/policies", admin_views.AdminPolicyViewSet, basename="admin-policy")
admin_router.register("leaves/admin/holidays", admin_views.AdminHolidayViewSet, basename="admin-holiday")
admin_router.register("leaves/admin/calendar-events", admin_views.AdminCalendarEventViewSet, basename="admin-calendar-event")
admin_router.register("leaves/admin/departments", admin_views.AdminDepartmentViewSet, basename="admin-department")
admin_router.register("leaves/admin/leave-types", admin_views.AdminLeaveTypeViewSet, basename="admin-leave-type")

# Custom endpoints. These are listed (and included) before the Level 1
# /leaves/<pk>/ route so they are not swallowed by the detail matcher.
urlpatterns = [
    path("leaves/departments/governance/", views.DepartmentGovernanceView.as_view(),
         name="department-governance"),
    path("leaves/my-entitlements/", views.MyEntitlementsView.as_view(), name="leave-my-entitlements"),
    path("leaves/leave-policy/", views.LeavePolicyView.as_view(), name="leave-policy"),
    path("leaves/calendar-events/", views.CalendarEventsView.as_view(),
         name="leave-calendar-events"),
    path("leaves/my-report/<str:period>/", views.MyLeaveReportView.as_view(), name="leave-my-report"),
    path("leaves/hr/category-review/", views.HRCategoryReviewView.as_view(), name="leave-hr-category-review"),
    # Phase 8 - HR review of attendance-earned comp days. Listed before the
    # existing /compensatory/ route's siblings; the literal "pending" segment
    # cannot collide with the <uuid:pk> matcher below it.
    path("leaves/compensatory/pending/", comp_views.CompensatoryPendingView.as_view(),
         name="leave-compensatory-pending"),
    path("leaves/compensatory/<uuid:pk>/confirm/", comp_views.CompensatoryConfirmView.as_view(),
         name="leave-compensatory-confirm"),
    path("leaves/compensatory/<uuid:pk>/reject/", comp_views.CompensatoryRejectView.as_view(),
         name="leave-compensatory-reject"),
    path("leaves/compensatory/", views.CompensatoryView.as_view(), name="leave-compensatory"),
    path("leaves/my-history/", views.MyLeaveHistoryView.as_view(), name="leave-my-history"),
    path("leaves/calendar/", views.LeaveCalendarRecordsView.as_view(), name="leave-day-calendar"),
    path("leaves/team-attendance/", views.TeamAttendanceView.as_view(), name="leave-team-attendance"),
    path("leaves/recompute-balance/", views.RecomputeBalanceView.as_view(), name="leave-recompute-balance"),
    path("leaves/year-end-carry-forward/", views.YearEndCarryForwardView.as_view(), name="leave-year-end-carry-forward"),
    path("leaves/admin/leaves/bulk-action/", admin_views.AdminBulkLeaveActionView.as_view(), name="admin-bulk-leave-action"),
    path("leaves/admin/reports/", admin_views.AdminReportsView.as_view(), name="admin-reports"),
] + admin_router.urls + router.urls
