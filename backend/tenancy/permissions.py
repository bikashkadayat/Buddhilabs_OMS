"""Who may reach the platform console.

The rule is structural, not a role check: a platform user has
``is_platform_staff = True`` and ``organization IS NULL``, and the database
refuses any other combination (``user_platform_staff_has_no_org``). So
"company admins must never access platform administration" is enforced by the
shape of the row, not by remembering to test a flag.

``IsPlatformStaff`` deliberately does NOT accept ``is_staff`` or
``is_superuser``. Every admin guard in this project is an allow-list (see
users/roles.py), and widening this one to Django's staff flag would hand the
platform console to anybody who had ever been given Django admin access for an
unrelated reason.
"""
from rest_framework import permissions


class IsPlatformStaff(permissions.BasePermission):
    """Authenticated platform operator, bound to no organization."""

    message = "Platform administration is restricted to platform staff."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and getattr(user, "is_platform_staff", False)
            and getattr(user, "organization_id", None) is None
        )


class IsTenantUser(permissions.BasePermission):
    """Authenticated customer employee, scoped to exactly one organization."""

    message = "This endpoint is for organization members."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and not getattr(user, "is_platform_staff", False)
        )
