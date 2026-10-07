"""Who approves attendance corrections for whom.

Routing is by DEPARTMENT, not by the employee's chosen leave approver. A
correction is an attendance decision, and department membership is what already
decides whose attendance a manager can see (``dashboard_views.scoped_employees``,
``AttendanceListView``). Routing any other way would let a manager approve a
change to a record they are not allowed to look at.
"""
from users.models import User
from users.roles import has_org_wide_read as _org_wide_read


def department_head_for(employee):
    """The manager who reviews this employee's corrections, or None.

    Order:
      1. the department's designated head,
      2. failing that, any active checker in the same department,
      3. None — the request skips straight to HR rather than becoming stuck.

    Never returns the employee themselves: nobody reviews their own correction.
    """
    department = getattr(employee, "department_ref", None)
    if department is None:
        return None

    head = department.head
    if head is not None and head.is_active and head.pk != employee.pk:
        return head

    return (User.objects.filter(
        department_ref=department, role=User.Roles.CHECKER, is_active=True)
        .exclude(pk=employee.pk)
        .order_by("first_name", "last_name")
        .first())


def can_act_as_manager(actor, correction):
    """May ``actor`` decide the department-head stage of this request?

    Admin and HR can always act — HR because they are the next stage anyway and
    a stalled queue helps nobody, Admin as the oversight role. A department head
    may act for their own department, but never on their own request.
    """
    if not actor.is_authenticated:
        return False
    if correction.employee_id == actor.pk:
        return False
    if actor.role in (User.Roles.APPROVER, User.Roles.ADMIN):
        return True
    if actor.role != User.Roles.CHECKER:
        return False
    if correction.manager_id == actor.pk:
        return True
    employee_dept = correction.employee.department_ref_id
    return bool(employee_dept) and employee_dept == actor.department_ref_id


def can_act_as_hr(actor):
    return actor.is_authenticated and actor.role in (User.Roles.APPROVER, User.Roles.ADMIN)


def scoped_corrections(queryset, user):
    """Role-scope a correction queryset — same rules as the attendance list.

    Nobody sees a correction for an employee whose attendance they could not
    already read.
    """
    if _org_wide_read(user):  # Board sees this read-only (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE).
        return queryset
    if user.role == User.Roles.CHECKER and user.department_ref_id:
        return queryset.filter(employee__department_ref_id=user.department_ref_id)
    return queryset.filter(employee=user)
