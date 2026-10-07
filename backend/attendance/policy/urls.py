"""Policy-engine routes, mounted under the project's existing /api/v1/ prefix.

SimpleRouter (no API-root view) matches the convention the other routers in this
project already use, so nothing clashes at /api/v1/.
"""
from rest_framework.routers import SimpleRouter

from .views import (
    AttendancePolicyViewSet,
    EmployeeShiftViewSet,
    PolicyAssignmentViewSet,
    ResolvedPolicyView,
    ShiftViewSet,
    WFHRequestViewSet,
)

router = SimpleRouter()
router.register("attendance/policies", AttendancePolicyViewSet, basename="attendance-policy")
router.register("attendance/shifts", ShiftViewSet, basename="attendance-shift")
router.register("attendance/policy-assignments", PolicyAssignmentViewSet,
                basename="attendance-policy-assignment")
router.register("attendance/employee-shifts", EmployeeShiftViewSet,
                basename="attendance-employee-shift")
router.register("attendance/wfh", WFHRequestViewSet, basename="attendance-wfh")
router.register("attendance/policy/resolved", ResolvedPolicyView,
                basename="attendance-policy-resolved")

urlpatterns = router.urls
