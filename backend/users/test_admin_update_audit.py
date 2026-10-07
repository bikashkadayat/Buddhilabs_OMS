"""Audit completeness for the admin user-management mutation surface.

`AdminUserViewSet.perform_update` (users/admin_views.py:56-58) calls
`log_action` with no `changes` payload, and `log_action` stores
`changes=changes or {}` (audit/services.py:29). The resulting row therefore
names the actor, the timestamp and the target, but records nothing about WHAT
was changed — no field list and no before/after.

That matters most on this endpoint specifically, because the fields reachable
here include ones that widen authority:

  * `employee_type` — consulted directly by memos.governance.can_manage_assignments
    and inventory.roles.is_supervisor, so changing it grants real permissions;
  * `is_active`, `department_ref` — scope and access.

The dedicated `change-role` action does record before/after
(`{'event': 'ROLE_CHANGED', 'from': ..., 'to': ...}`), which is the standard the
generic update path is measured against here.
"""
from datetime import date

import pytest
from rest_framework.test import APIClient

from audit.models import AuditLog
from users.models import User


def _user(username, role=User.Roles.MAKER, **extra):
    return User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.capitalize(), last_name="T", role=role, department="ENG",
        employment_type=User.EmploymentType.PERMANENT, date_of_joining=date(2018, 1, 1),
        **extra,
    )


@pytest.fixture
def admin_client(db):
    admin = _user("au_admin", User.Roles.ADMIN, is_staff=True, is_superuser=True)
    c = APIClient(); c.force_authenticate(admin)
    return c


@pytest.mark.django_db
def test_change_role_records_before_and_after(admin_client):
    """The standard: the dedicated role endpoint records the transition."""
    target = _user("au_target1")
    resp = admin_client.post(f"/api/v1/admin/users/{target.id}/change-role/",
                             {"role": User.Roles.CHECKER}, format="json")
    assert resp.status_code == 200, resp.data

    row = AuditLog.objects.order_by("-id").first()
    assert row.changes.get("from") == User.Roles.MAKER
    assert row.changes.get("to") == User.Roles.CHECKER


@pytest.mark.django_db
def test_admin_update_of_employee_type_is_recorded_in_the_audit_row(admin_client):
    """Changing employee_type grants memo/inventory authority — it must be legible.

    employee_type is not merely profile data: memos.governance.can_manage_assignments
    returns True for SUPERVISOR, so this PATCH hands the target memo authority its
    stored `role` (maker / Employee) does not carry.
    """
    target = _user("au_target2")
    before = AuditLog.objects.count()

    resp = admin_client.patch(f"/api/v1/admin/users/{target.id}/",
                              {"employee_type": User.EmployeeType.SUPERVISOR}, format="json")
    assert resp.status_code == 200, resp.data
    target.refresh_from_db()
    assert target.employee_type == User.EmployeeType.SUPERVISOR, "precondition: the field did change"

    assert AuditLog.objects.count() > before, "an audit row was written at all"
    row = AuditLog.objects.order_by("-id").first()
    assert row.changes, (
        "admin user update wrote an audit row with an EMPTY changes payload — "
        "the log cannot say which field was altered, on the endpoint that can "
        "grant memo and inventory authority via employee_type"
    )
    blob = str(row.changes)
    assert "employee_type" in blob, (
        f"audit row does not name the changed field; changes={row.changes!r}"
    )
