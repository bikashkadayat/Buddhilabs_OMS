"""
Who may do what with an asset (Phase 70.15).

THE PROBLEM THIS SOLVES
-----------------------
The module before this phase had exactly one distinction: manager or not, where
manager meant Admin, HR or Department Head. That was enough when the only actions
were "add an item" and "assign it". It is not enough for a workflow whose whole
point is that two DIFFERENT people approve, because with one role both approvals
are the same person and the control is decorative.

The brief names six: Employee, Supervisor, Inventory Officer, Department Head, HR,
Admin. Three of those already exist in `User.role` (checker = Department Head,
approver = HR, admin = Admin) and one is the absence of the others (Employee).
The two that do not exist are Supervisor and Inventory Officer, and they are
genuinely different kinds of thing:

  * SUPERVISOR is an ORG-CHART fact. `User.employee_type` already carries it, and
    the memo module started consulting that field in Phase 49.5 for exactly this
    reason. Reused rather than reinvented.

  * INVENTORY OFFICER is a JOB, not a rank. The person who runs the store may be a
    junior employee, and making it a rank would either promote them or exclude
    them. So it is a Django auth Group - assignable by an administrator with no
    migration, and the same mechanism the memo module uses for employee groups.

WHY NOT JUST ADD A ROLE CHOICE
------------------------------
`User.role` drives permissions across every module in this project. Adding a value
to it would give inventory a say in what somebody can do with a memo, which is a
much bigger change than this phase should make.
"""
from django.conf import settings

from users.models import User
from users.roles import has_org_wide_read as _org_wide_read

# The group an administrator puts the store staff in. Overridable so a deployment
# that already has a differently-named group does not need its data edited.
INVENTORY_OFFICER_GROUP = getattr(
    settings, "INVENTORY_OFFICER_GROUP", "Inventory Officer")

# Employee types that count as a supervisor for the first approval gate.
SUPERVISOR_TYPES = frozenset({
    User.EmployeeType.SUPERVISOR,
    User.EmployeeType.MANAGER,
    User.EmployeeType.DEPARTMENT_HEAD,
    User.EmployeeType.HR_OFFICER,
    User.EmployeeType.SYSTEM_ADMIN,
})


def is_admin(user):
    return bool(user and getattr(user, "role", None) == User.Roles.ADMIN)


def is_hr(user):
    """HR is the APPROVER role - the stored values are maker/checker/approver/admin."""
    return bool(user and getattr(user, "role", None) == User.Roles.APPROVER)


def is_department_head(user):
    return bool(user and getattr(user, "role", None) == User.Roles.CHECKER)


def is_inventory_officer(user):
    """
    In the Inventory Officer group, or senior enough that the distinction is moot.

    HR and Admin are included because they already had full inventory rights before
    this phase, and taking them away would be a regression dressed as a feature.
    """
    if not (user and user.is_authenticated):
        return False
    if is_admin(user) or is_hr(user):
        return True
    return user.groups.filter(name=INVENTORY_OFFICER_GROUP).exists()


def is_supervisor(user):
    """
    Can answer the first gate: "does this person need this asset".

    Rank OR role, because an organisation half-migrated onto `employee_type` will
    have department heads whose type is still the default. Falling back to `role`
    means the approval queue is never empty for want of a data migration.
    """
    if not (user and user.is_authenticated):
        return False
    if is_admin(user) or is_hr(user) or is_department_head(user):
        return True
    return getattr(user, "employee_type", None) in SUPERVISOR_TYPES


def is_manager(user):
    """
    The pre-existing "asset manager" test, kept EXACTLY as it was.

    `services.py`, the old permission classes and three existing test files all
    depend on this meaning Admin / HR / Department Head. Redefining it to include
    inventory officers would have silently widened every endpoint that already used
    it, which is a security change disguised as a refactor. New endpoints use the
    precise predicates above instead.
    """
    return bool(
        user and user.is_authenticated
        and getattr(user, "role", None) in (
            User.Roles.ADMIN, User.Roles.APPROVER, User.Roles.CHECKER))


def can_manage_assets(user):
    """
    Create, edit, receive, stock, dispose, and move custody. The store's own work.

    A DEPARTMENT HEAD IS NO LONGER INCLUDED (Phase ASSET-TRANSFER-GOVERNANCE).
    The brief makes the head an organisation-wide READER: they see every asset in
    every department, and change none of them. Rank stopped granting the store's
    write rights the moment visibility stopped being scoped to a department -
    otherwise widening what a head can see would have quietly widened what a head
    can edit, across the whole organisation rather than their own corner of it.

    A head who genuinely runs a store is put in the Inventory Officer group. That
    is the explicit way to grant it, and the same mechanism Phase 70 chose for
    exactly this reason: running a store is a job, not a rank.

    `is_manager` is deliberately NOT touched. It still means Admin/HR/Dept Head and
    still gates take-out approvals and the assignment board, neither of which is an
    ownership change; redefining it would have moved permissions in modules this
    brief says nothing about.
    """
    return is_inventory_officer(user) or is_admin(user) or is_hr(user)


def can_browse_register(user):
    """
    Read the whole register: every asset, every department, every owner.

    The store roles, plus a Department Head - who reads all of it and writes none
    of it. Deliberately NOT `can_view_all_assets`: that also admits any supervisor
    by employee_type, and turning the register's list endpoint on for them is a
    wider change than this brief asks for.
    """
    # The Board reads the whole register too, and like a head writes none of it
    # (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE) - `can_manage_assets` does not name it.
    return can_manage_assets(user) or is_department_head(user) or _org_wide_read(user)


def can_approve_as_supervisor(user, request):
    """
    The first gate. Not the requester - a control that one person can satisfy on
    both sides is not a control.
    """
    if request is not None and request.requested_by_id == getattr(user, "id", None):
        return False
    return is_supervisor(user)


def can_approve_as_inventory(user, request=None):
    """The second gate."""
    if request is not None and request.requested_by_id == getattr(user, "id", None):
        return False
    return is_inventory_officer(user)


def can_view_all_assets(user):
    """Who sees the whole register rather than only their own assets."""
    return can_manage_assets(user) or is_supervisor(user) or _org_wide_read(user)


def role_summary(user):
    """
    What the client renders its inventory menus from - so authorization has one
    home on the server and the UI never re-derives who may do what.
    """
    return {
        "is_employee": True,
        "is_supervisor": is_supervisor(user),
        "is_inventory_officer": is_inventory_officer(user),
        "is_department_head": is_department_head(user),
        "is_hr": is_hr(user),
        "is_admin": is_admin(user),
        "can_manage_assets": can_manage_assets(user),
        "can_view_all_assets": can_view_all_assets(user),
        # Phase ASSET-TRANSFER-GOVERNANCE. Read-and-write are now different
        # questions for a Department Head, so the client is told both rather than
        # inferring the second from the first.
        "can_browse_register": can_browse_register(user),
        "is_read_only_viewer": is_department_head(user) and not can_manage_assets(user),
    }
