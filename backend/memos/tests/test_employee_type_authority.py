"""CHARACTERIZATION: `employee_type` grants memo authority independently of `role`.

This test does not assert a defect — it pins down behaviour that is currently
deliberate (memos/governance.py:308-331, "Phase 49 audit") but that contradicts
the project-level invariant "permissions come from `role` only".

`can_manage_assignments` returns True when EITHER holds:

    role == CHECKER                                  (permission axis)
    employee_type in {SUPERVISOR, MANAGER, ...}      (org-rank axis)

So a user whose stored role is ``maker`` — displayed as **Employee**, the lowest
permission level — can declare absences and move workflow steps on someone
else's memo purely because HR set their org-rank field. `employee_type` is not
self-writable (SelfProfileSerializer omits it), so this is not a self-escalation
path; it requires an admin PATCH, which is itself recorded with an empty
`changes` payload (see users/test_admin_update_audit.py).

If the org-rank axis is intended to carry authority, this test documents it and
should stay. If it is not, this test is the failing case to fix against.
"""
import pytest

from memos.governance import can_manage_assignments
from memos.models import Memo
from users.models import User

from .conftest import *  # noqa: F401,F403  — shared memo fixtures


@pytest.fixture
def author(db):
    return User.objects.create_user(
        username="et_author", email="et_author@nif.test", password="pass12345",
        first_name="Ada", last_name="T", role=User.Roles.MAKER)


@pytest.fixture
def memo(db, author):
    return Memo.objects.create(subject="Org rank vs role", created_by=author)


@pytest.mark.django_db
def test_plain_employee_cannot_manage_someone_elses_memo(memo):
    """Control: role=maker, employee_type=employee -> no authority."""
    plain = User.objects.create_user(
        username="et_plain", email="et_plain@nif.test", password="pass12345",
        first_name="Bo", last_name="T", role=User.Roles.MAKER,
        employee_type=User.EmployeeType.EMPLOYEE)
    assert can_manage_assignments(plain, memo) is False


@pytest.mark.django_db
def test_employee_type_supervisor_confers_authority_without_any_role_change(memo):
    """The documented deviation: org rank alone is sufficient.

    Stored role is `maker` (displayed "Employee"), which carries no review or
    approval permission anywhere else in the system — yet this user may manage
    another author's memo assignments.
    """
    ranked = User.objects.create_user(
        username="et_super", email="et_super@nif.test", password="pass12345",
        first_name="Cy", last_name="T", role=User.Roles.MAKER,
        employee_type=User.EmployeeType.SUPERVISOR)

    assert ranked.role == User.Roles.MAKER, "precondition: permission axis is the lowest"
    assert can_manage_assignments(ranked, memo) is True, (
        "employee_type no longer confers memo authority — if that change was "
        "intended, delete this characterization test; if not, it caught a regression"
    )
