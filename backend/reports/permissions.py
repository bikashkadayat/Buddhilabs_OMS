"""Who may run which report.

Before Phase 9 the reports hub was ``IsAdminOrSuperuser`` only — HR
(``role='approver'``) was locked out, even though ``attendance/report_views.py``
has always let HR run attendance reports. Adding eight workforce reports without
fixing that would have shut HR out of their own command centre's exports.

Widening is strictly additive: every existing admin capability is unchanged.
"""
from rest_framework.permissions import IsAuthenticated

from users.models import User

from .models import MANAGER_ANALYTICS_REPORT_TYPES, WORKFORCE_REPORT_TYPES


def is_admin(user):
    return bool(user and (getattr(user, "is_staff", False)
                          or getattr(user, "is_superuser", False)
                          or getattr(user, "role", None) == User.Roles.ADMIN))


def is_hr(user):
    return bool(user and getattr(user, "role", None) == User.Roles.APPROVER)


def is_manager(user):
    return bool(user and getattr(user, "role", None) == User.Roles.CHECKER)


def is_bod(user):
    return bool(user and getattr(user, "role", None) == User.Roles.BOD)


class CanUseReports(IsAuthenticated):
    """Admin, HR, and department heads.

    Department heads are admitted to the hub, but ``ReportsViewSet`` narrows
    both what they can request (workforce types only) and what those reports
    contain (their own department) — see ``allowed_report_types`` and the
    scope injected in ``request_report``.
    """

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        user = request.user
        return is_admin(user) or is_hr(user) or is_manager(user) or is_bod(user)


class IsReportAdmin(IsAuthenticated):
    """Admin or HR. Used for scheduled reports, which are org-wide by nature."""

    def has_permission(self, request, view):
        return super().has_permission(request, view) and (
            is_admin(request.user) or is_hr(request.user))


def allowed_report_types(user):
    """Report types this user may request. None means 'no restriction'.

    Phase 10 widens the department-head set with the analytics exports, minus
    the executive summary: that document is organisation-wide by definition, and
    a department-scoped copy would carry a title its contents do not support.
    """
    if is_admin(user) or is_hr(user):
        return None
    if is_bod(user):
        # Every organisation-wide report, unscoped - except the Audit Trail,
        # which carries client IPs and user agents (Phase BOD).
        from .models import ReportType
        return {value for value, _ in ReportType.choices} - {ReportType.AUDIT_TRAIL}
    if is_manager(user):
        # Not the governance register: it is the organisation's ownership map,
        # and a department head's business is their own department's work.
        return set(WORKFORCE_REPORT_TYPES) | set(MANAGER_ANALYTICS_REPORT_TYPES)
    return set()
