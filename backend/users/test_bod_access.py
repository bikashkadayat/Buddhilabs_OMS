"""
What the Board of Directors can and cannot reach (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE).

Two halves, and the second matters more:

  * READS the Board is granted: organisation-wide lists, dashboards, analytics
    and reports, across every module the brief names.
  * NO ADMIN LEAKAGE: user and role management, system configuration, device
    infrastructure, system monitoring, IP-bearing audit trails, and every
    operational write the "documents only" decision withholds.

The same requests are made as HR and Admin as a CONTROL, so a 403 for the Board
is shown to be a decision about the Board rather than an endpoint that refuses
everybody.
"""
from datetime import date

import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from leaves.models import Department
from users.models import User

pytestmark = pytest.mark.django_db


def _u(username, role, **extra):
    return User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.capitalize(), last_name="T", role=role,
        employment_type=User.EmploymentType.PERMANENT, date_of_joining=date(2018, 1, 1),
        **extra)


@pytest.fixture
def people(db):
    dept = Department.objects.create(name="Access Dept", code="ACC-1")
    return {
        "employee": _u("acc_emp", User.Roles.MAKER, department_ref=dept),
        "head": _u("acc_head", User.Roles.CHECKER, department_ref=dept),
        "hr": _u("acc_hr", User.Roles.APPROVER, department_ref=dept),
        "bod": _u("acc_bod", User.Roles.BOD),
        "admin": _u("acc_admin", User.Roles.ADMIN),
    }


def _get(user, path):
    api = APIClient()
    api.force_authenticate(user)
    return api.get(path).status_code


def _post(user, path, body):
    api = APIClient()
    api.force_authenticate(user)
    return api.post(path, body, format="json").status_code


BOARD_READS = [
    "/api/v1/leaves/",
    "/api/v1/leaves/?queue=actionable",
    "/api/v1/attendance/",
    "/api/v1/analytics/executive/",
    "/api/v1/analytics/hr/",
    "/api/v1/analytics/departments/",
    "/api/v1/analytics/leave/",
    "/api/v1/analytics/attendance/",
    "/api/v1/inventory/dashboard/",
    "/api/v1/inventory/items/",
    "/api/v1/inventory/reports/by_department/",
    "/api/v1/inventory/visibility-options/",
    "/api/v1/memos/dashboard/",
    "/api/v1/memos/",
    "/api/v1/circulars/",
    "/api/v1/minutes/",
    "/api/v1/tasks/",
    "/api/v1/appraisals/",
]

ADMIN_ONLY = [
    "/api/v1/admin/users/",
    "/api/v1/users/admin/users/",
    "/api/v1/monitoring/health/",
    "/api/v1/analytics/devices/",
]


@pytest.mark.parametrize("path", BOARD_READS)
def test_the_board_reads_the_organisation(people, path):
    status = _get(people["bod"], path)
    assert status == 200, f"{path} -> {status}"


@pytest.mark.parametrize("path", ADMIN_ONLY)
def test_no_admin_surface_leaks_to_the_board(people, path):
    assert _get(people["bod"], path) == 403, path
    # Control: the endpoint exists and answers the people it is for.
    assert _get(people["admin"], path) == 200, f"{path} is not an admin endpoint at all"


def test_the_board_cannot_manage_users_or_roles(people):
    api = APIClient()
    api.force_authenticate(people["bod"])
    target = people["employee"]
    assert api.post("/api/v1/admin/users/", {"email": "x@nif.test", "role": "admin"},
                    format="json").status_code == 403
    assert api.patch(f"/api/v1/admin/users/{target.id}/", {"role": "admin"},
                     format="json").status_code == 403
    target.refresh_from_db()
    assert target.role == User.Roles.MAKER


def test_a_board_account_can_never_carry_staff_or_superuser(people):
    board = people["bod"]
    for flag in ("is_staff", "is_superuser"):
        setattr(board, flag, True)
        with pytest.raises(ValidationError):
            board.save()
        setattr(board, flag, False)


def test_the_board_never_sees_ip_bearing_audit_trails(people):
    from reports.permissions import allowed_report_types
    allowed = allowed_report_types(people["bod"])
    assert "audit_trail" not in allowed
    assert "executive_summary" in allowed


def test_operational_writes_are_refused_but_documents_are_not(people):
    """
    The line has moved twice, and where it sits now is worth stating plainly.

      BOD-ROLE-EXECUTIVE-GOVERNANCE   the Board wrote documents and nothing else
      TASK-MANAGEMENT-ASANA-MODEL     it could also keep its own task list
      TASK-SIMPLIFICATION             it creates tasks like anybody else

    The last of those was the user's own decision, on the grounds that "any
    authenticated user may create a task" means what it says. What has NEVER
    moved is everything else in this file: assets, user management, system
    configuration, monitoring and IP-bearing audit trails are still refused, and
    that is what this file is for.
    """
    board = people["bod"]
    assert _post(board, "/api/v1/inventory/items/",
                 {"name": "Board laptop", "asset_type": "it"}) == 403
    # Documents, and - since the simplification - tasks.
    assert _post(board, "/api/v1/memos/",
                 {"subject": "Board memo", "memo_type": "general"}) == 201
    assert _post(board, "/api/v1/tasks/", {
        "title": "Board follow-up",
        "assignee_ids": [str(people["employee"].id)],
        "reviewer": str(people["head"].id)}) == 201


def test_the_board_creates_tasks_like_anybody_else(people):
    """
    Phase TASK-SIMPLIFICATION. The Board may put a task on somebody's list,
    because the phase removed the kinds of task that distinguished "my own work"
    from "work for you". Its read-only governance is untouched: the admin
    surfaces above, and the reports below.
    """
    board, target = people["bod"], people["employee"]
    assert _post(board, "/api/v1/tasks/", {
        "title": "Work for the ICT team",
        "assignee_ids": [str(target.id)],
        "reviewer": str(people["head"].id)}) == 201
    from tasks.models import Task
    assert Task.objects.filter(assignees__user=target).exists()


def test_role_rank_puts_the_board_between_hr_and_admin(people):
    from users.roles import rank
    order = sorted(people.values(), key=rank)
    assert [u.role for u in order] == ["maker", "checker", "approver", "bod", "admin"]


def test_employees_and_heads_do_not_gain_organisation_wide_read(people):
    from users.roles import has_org_wide_read
    assert not has_org_wide_read(people["employee"])
    assert not has_org_wide_read(people["head"])
    assert has_org_wide_read(people["bod"])
