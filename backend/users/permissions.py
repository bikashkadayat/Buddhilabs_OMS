from rest_framework import permissions
from .models import User

class IsMaker(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in [
            User.Roles.MAKER,
            User.Roles.ADMIN,
        ]

class IsChecker(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in [
            User.Roles.CHECKER,
            User.Roles.ADMIN,
        ]

class IsApproverOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in [
            User.Roles.APPROVER,
            User.Roles.ADMIN,
        ]

class IsCheckerOrApprover(permissions.BasePermission):
    """
    Allows access only to Checkers, Approvers, and Admins.
    """
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in [
            User.Roles.CHECKER,
            User.Roles.APPROVER,
            User.Roles.ADMIN
        ]

