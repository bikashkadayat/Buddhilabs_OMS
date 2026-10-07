from datetime import date, timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from users.models import User
from leaves.models import Department
from inventory.models import InventoryItem


def _user(username, role, department_ref=None, **extra):
    defaults = dict(
        employment_type=User.EmploymentType.PERMANENT,
        date_of_joining=date(2018, 1, 1),
    )
    defaults.update(extra)
    return User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.capitalize(), last_name="T", role=role,
        department_ref=department_ref, **defaults,
    )


@pytest.fixture
def today():
    # settings.TIME_ZONE is Asia/Kathmandu, so this is "today" in Nepal.
    return timezone.localtime(timezone.now()).date()


@pytest.fixture
def soon(today):
    return today + timedelta(days=2)


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def eng(db):
    return Department.objects.create(name="Engineering", code="INV-ENG")


@pytest.fixture
def ops(db):
    return Department.objects.create(name="Operations", code="INV-OPS")


@pytest.fixture
def employee(db, eng):
    """Plain employee (maker) — can request take-outs, not a manager."""
    return _user("inv_emp", User.Roles.MAKER, eng)


@pytest.fixture
def head(db, eng):
    """Department Head (checker) — manager, scoped to their own department."""
    return _user("inv_head", User.Roles.CHECKER, eng)


@pytest.fixture
def hr(db, eng):
    """HR (approver) — manager, org-wide."""
    return _user("inv_hr", User.Roles.APPROVER, eng)


@pytest.fixture
def admin(db, eng):
    return _user("inv_admin", User.Roles.ADMIN, eng)


@pytest.fixture
def item(db, eng):
    return InventoryItem.objects.create(
        asset_code="NIF-INV-T001", name="Test Laptop", department=eng)


@pytest.fixture
def auth(api):
    def _login(user):
        api.force_authenticate(user=user)
        return api
    return _login


# --------------------------------------------------------------------------- #
# Phase 70 fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def supervisor(db, eng):
    """
    A plain employee by ROLE whose employee_type makes them a supervisor.

    Deliberately a maker: the whole point of the Phase 70 role work is that being
    able to approve a colleague's asset request is an org-chart fact, not a
    permissions rank. If this fixture were a checker the first gate would be
    satisfied by the pre-existing manager test and prove nothing.
    """
    return _user("inv_super", User.Roles.MAKER, eng,
                 employee_type=User.EmployeeType.SUPERVISOR)


@pytest.fixture
def officer(db, eng):
    """
    An inventory officer: a plain employee in the Inventory Officer group.

    Also a maker, for the same reason - the store may be run by a junior member of
    staff, and making the role a rank would either promote them or exclude them.
    """
    from django.contrib.auth.models import Group

    from inventory.roles import INVENTORY_OFFICER_GROUP

    user = _user("inv_officer", User.Roles.MAKER, eng)
    group, _ = Group.objects.get_or_create(name=INVENTORY_OFFICER_GROUP)
    user.groups.add(group)
    return user


@pytest.fixture
def other_employee(db, ops):
    """A second plain employee, in a different department. The control subject."""
    return _user("inv_other", User.Roles.MAKER, ops)


@pytest.fixture
def stock_item(db, eng):
    """An asset checked into stock and ready to issue."""
    return InventoryItem.objects.create(
        asset_code="NIF-INV-T100", name="Stock Laptop", department=eng,
        status=InventoryItem.Status.AVAILABLE,
        condition=InventoryItem.Condition.GOOD)


@pytest.fixture
def on_order_item(db, eng):
    """An asset at the very front of the lifecycle."""
    return InventoryItem.objects.create(
        asset_code="NIF-INV-T200", name="Ordered Monitor", department=eng,
        status=InventoryItem.Status.PROCUREMENT)


@pytest.fixture
def laptop_category(db):
    from inventory.models import InventoryCategory
    return InventoryCategory.objects.get_or_create(name="Laptop")[0]


def dispose_via_workflow(item, requester, head, admin, *, reason="Beyond economic repair.",
                         disposal_type="scrap", proceeds=None):
    """
    Take an asset off the books the only way there now is (Phase
    ASSET-LIFECYCLE-DISPOSAL): request, submit, department head, administrator.
    """
    from inventory import disposals

    d = disposals.create_disposal(item=item, requested_by=requester,
                                  disposal_type=disposal_type, reason=reason,
                                  expected_proceeds=proceeds)
    disposals.submit(d.id, requester)
    disposals.approve(d.id, head, remarks="Department agrees to write it off.")
    return disposals.approve(d.id, admin, remarks="Approved for disposal.")
