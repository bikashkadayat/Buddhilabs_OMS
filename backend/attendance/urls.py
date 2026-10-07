from django.urls import path

from .biometric import MyBiometricView
from .dashboard_views import AttendanceDashboardView
from .policy.urls import urlpatterns as policy_urlpatterns
from .workforce.urls import urlpatterns as workforce_urlpatterns
from .report_views import (
    AllReportView,
    EmployeeMonthlyReportView,
    EmployeeWeeklyReportView,
    ReportOptionsView,
)
from .views import (
    AttendanceListView,
    CheckInView,
    CheckOutView,
    ManualAttendanceView,
    MyCalendarView,
    TodayView,
)

urlpatterns = policy_urlpatterns + workforce_urlpatterns + [
    # Policy routes come first: they are all more specific than the bare
    # "attendance/" list route at the bottom of this list.
    path("attendance/check-in/", CheckInView.as_view(), name="attendance-check-in"),
    path("attendance/check-out/", CheckOutView.as_view(), name="attendance-check-out"),
    path("attendance/today/", TodayView.as_view(), name="attendance-today"),
    path("attendance/me/", MyCalendarView.as_view(), name="attendance-me"),
    # Live dashboard feed — what the WebSocket tells the browser to re-read.
    path("attendance/dashboard/", AttendanceDashboardView.as_view(), name="attendance-dashboard"),
    path("attendance/biometric/me/", MyBiometricView.as_view(), name="attendance-biometric-me"),
    # Reports (Admin/HR only)
    path("attendance/report/employee/<uuid:pk>/weekly", EmployeeWeeklyReportView.as_view(), name="attendance-report-weekly"),
    path("attendance/report/employee/<uuid:pk>/monthly", EmployeeMonthlyReportView.as_view(), name="attendance-report-monthly"),
    path("attendance/report/options/", ReportOptionsView.as_view(), name="attendance-report-options"),
    path("attendance/report/all", AllReportView.as_view(), name="attendance-report-all"),
    path("attendance/manual/", ManualAttendanceView.as_view(), name="attendance-manual"),
    path("attendance/", AttendanceListView.as_view(), name="attendance-list"),
]
