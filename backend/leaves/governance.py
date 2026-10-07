"""
Department ownership, as one set of facts (Phase DEPARTMENT-GOVERNANCE-HARDENING).

WHY THIS MODULE EXISTS
----------------------
Four different surfaces answer the question "does every department have
somebody answerable for it?" - System Health, the dashboard line, the
Departments page and the Department Ownership report. Four copies of that query
would drift, and the drift would show up as a green health check beside a red
banner, which teaches people to believe neither.

WHAT AN UNOWNED DEPARTMENT COSTS
--------------------------------
Leave routing falls back to HR, and department reporting has no owner. It does
NOT stop task work: the task module dropped that dependency in
TASK-SIMPLIFICATION, and every message here was corrected in OMS-FINAL-FREEZE-
HARDENING to stop saying otherwise.

WHAT COUNTS AS HAVING A HEAD
----------------------------
An ACTIVE user recorded as `head`. A department whose head has left and been
deactivated is exactly as unowned as one that never had a head - the work routes
nowhere either way - so both are reported the same. That is the whole point of
checking rather than trusting the column not to be null.

INACTIVE DEPARTMENTS ARE NOT COUNTED
------------------------------------
A department that has been stood down has no work to route and nobody to answer
for it. Counting it would put a permanent red number on the health board that
nobody can ever clear, and a warning nobody can clear is a warning people learn
to ignore.

NOTE FOR THE TASK MODULE
------------------------
tasks/services.py has its own `departments_without_a_head`, reached through the
app registry rather than by importing this module: Task Management is required
to stand alone, and a test enforces that it imports no peer module. The two
agree by construction - both ask the same question of the same table - and the
task module's copy exists for the boundary, not because the rule differs.
"""
from django.db.models import Q

from .models import Department

__all__ = ["departments_missing_head", "governance_summary", "ownership_rows"]

# The states every surface reports. "critical" is the domain word for "some
# department has nobody answerable for it"; what that COSTS is leave routing
# falling back to HR and department reporting having no owner. It does not stop
# task work - that dependency went in TASK-SIMPLIFICATION - and the monitoring
# probe bands it as a warning accordingly.
OK = "ok"
CRITICAL = "critical"


def _missing_head_filter():
    """No head at all, or a head whose account is no longer active."""
    return Q(head__isnull=True) | Q(head__is_active=False)


def departments_missing_head():
    """Active departments nobody answers for, in name order."""
    return list(Department.objects.filter(is_active=True)
                .filter(_missing_head_filter())
                .select_related("head").order_by("name"))


def governance_summary():
    """
    The numbers every surface reports, computed once.

    `status` is derived here rather than by each caller applying its own
    threshold - two surfaces disagreeing about whether the same number is a
    problem is the failure this module exists to prevent.
    """
    active = Department.objects.filter(is_active=True)
    missing = departments_missing_head()
    return {
        "total": Department.objects.count(),
        "active": active.count(),
        "missing_head": len(missing),
        "missing_head_names": [d.name for d in missing],
        "status": CRITICAL if missing else OK,
    }


def ownership_rows():
    """
    Every department and who answers for it - the Department Ownership report.

    Inactive departments are INCLUDED here, unlike in the counts above: this is
    the register of ownership, and "stood down" is a fact about a department
    that the register should show rather than hide. `is_active` is a column, so
    a reader can see why a row has no head without wondering whether it is a gap.
    """
    rows = []
    for dept in (Department.objects.select_related("head", "parent")
                 .order_by("name")):
        head = dept.head if (dept.head and dept.head.is_active) else None
        rows.append({
            "department": dept.name,
            "code": dept.code,
            "parent": dept.parent.name if dept.parent else "",
            "head": head.get_full_name() if head else "",
            "head_email": head.email if head else "",
            # Why the head cell is empty, which is not the same question as
            # whether it is empty: nobody recorded, or somebody who has left.
            "head_status": ("Assigned" if head
                            else "Inactive account" if dept.head
                            else "Not assigned"),
            "members": dept.members.count(),
            "is_active": dept.is_active,
            "governance": ("Owned" if head
                           else "Stood down" if not dept.is_active
                           else "NO HEAD"),
        })
    return rows
