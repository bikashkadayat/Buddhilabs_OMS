"""
The role hierarchy, and the questions every module asks of it, in one place
(Phase BOD-ROLE-EXECUTIVE-GOVERNANCE).

    Admin  >  Board of Directors  >  HR  >  Department Head  >  Employee

WHY THIS FILE EXISTS
--------------------
Before the Board, "may this person see the whole organisation?" was written out
as a literal `role in (APPROVER, ADMIN)` in attendance, leave, analytics,
inventory, memos, circulars, minutes and tasks - separately, each time. Adding a
role to that question meant finding every copy, and the copy that was missed
would be a Board member silently seeing one department's attendance while seeing
every department's leave.

So the question is asked HERE, and the modules ask this file.

WHAT THE BOARD IS - AND IS NOT
------------------------------
  * ORGANISATION-WIDE READ: every department, asset, report, attendance and
    leave summary (`has_org_wide_read`).
  * ONE DECISION: a Department Head's leave (`leaves.approvals`).
  * NOTHING OPERATIONAL TO WRITE: no creating assets, editing registers,
    approving memos on others' behalf, or running store actions. Write guards
    elsewhere are allow-lists that do not name this role, so it is refused there
    without anyone having to remember to refuse it.
  * NO CONFIGURATION: `can_configure_system` is Admin only. User, role and
    authentication management, server settings and admin controls all check
    Admin (or is_staff / is_superuser, which a Board account never has).

The IP-bearing forensic audit trails stay with HR and Admin
(`has_forensic_read`): client addresses and user agents are security data, not
governance data, and "sees all reports" is not "sees who logged in from where".
"""
from .models import User

Roles = User.Roles

# The roles that see the WHOLE organisation, read-only for anyone but Admin.
ORG_WIDE_READ_ROLES = (Roles.APPROVER, Roles.BOD, Roles.ADMIN)

# The roles that may see IP addresses and user agents in audit trails.
FORENSIC_READ_ROLES = (Roles.APPROVER, Roles.ADMIN)

# Seniority, lowest first. Used for "at least" questions and for ordering.
HIERARCHY = (Roles.MAKER, Roles.CHECKER, Roles.APPROVER, Roles.BOD, Roles.ADMIN)


def _role(user):
    return getattr(user, "role", None) if user is not None else None


def is_admin(user):
    return _role(user) == Roles.ADMIN


def is_bod(user):
    return _role(user) == Roles.BOD


def is_hr(user):
    return _role(user) == Roles.APPROVER


def is_department_head(user):
    return _role(user) == Roles.CHECKER


def has_org_wide_read(user):
    """Sees every department, asset, report and summary. Grants no write."""
    return bool(user and getattr(user, "is_authenticated", True)
                and _role(user) in ORG_WIDE_READ_ROLES)


def has_forensic_read(user):
    """Audit trails carrying client IPs and user agents: HR and Admin only."""
    return bool(user and _role(user) in FORENSIC_READ_ROLES)


def can_configure_system(user):
    """System configuration, user/role management, auth and server settings."""
    return is_admin(user) or bool(
        user and (getattr(user, "is_superuser", False) or getattr(user, "is_staff", False)))


def rank(user_or_role):
    """Position in the hierarchy (higher is more senior); -1 for unknown."""
    role = user_or_role if isinstance(user_or_role, str) else _role(user_or_role)
    try:
        return HIERARCHY.index(role)
    except ValueError:
        return -1
