"""Who may see analytics.

Three gates, matching the approved permission matrix:

* ``CanViewAnalytics``  -- Department Head, HR, Admin. Employees have none.
* ``IsOrgAnalytics``    -- HR and Admin only: the executive and HR dashboards,
  and device health (infrastructure, not attendance).
* ``analytics_scope``   -- resolves the population, and is what actually stops a
  department head reading another department. The permission class only decides
  whether the door opens; ``scope.py`` decides what is behind it.

Executive audience note: the data model has four roles (maker/checker/approver/
admin) and no Director. Per the approved design, "Executive" maps to HR +
Admin -- the same gate Phase 9.1 uses for the HR command centre.
"""
from rest_framework.permissions import IsAuthenticated

from users.models import User

ANALYTICS_ROLES = (User.Roles.CHECKER, User.Roles.APPROVER, User.Roles.BOD, User.Roles.ADMIN)
# The Board is the "Executive" audience this module was designed for before the
# role existed (see the note above) - Phase BOD-ROLE-EXECUTIVE-GOVERNANCE.
ORG_ROLES = (User.Roles.APPROVER, User.Roles.BOD, User.Roles.ADMIN)
# Device fleet health is infrastructure, not governance: HR and Admin only.
INFRASTRUCTURE_ROLES = (User.Roles.APPROVER, User.Roles.ADMIN)


def can_view_analytics(user):
    return bool(user and getattr(user, "role", None) in ANALYTICS_ROLES)


def is_org_analyst(user):
    return bool(user and getattr(user, "role", None) in ORG_ROLES)


class CanViewAnalytics(IsAuthenticated):
    """Department Head and above. Employees get 403 on every analytics path."""

    def has_permission(self, request, view):
        return super().has_permission(request, view) and can_view_analytics(request.user)


class IsInfrastructureAnalytics(IsAuthenticated):
    """HR and Admin. Biometric device health - the Board configures no infrastructure."""

    def has_permission(self, request, view):
        return super().has_permission(request, view) and bool(
            request.user and getattr(request.user, "role", None) in INFRASTRUCTURE_ROLES)


class IsOrgAnalytics(IsAuthenticated):
    """HR and Admin. Organisation-wide dashboards and device infrastructure."""

    def has_permission(self, request, view):
        return super().has_permission(request, view) and is_org_analyst(request.user)
