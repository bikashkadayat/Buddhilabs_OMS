"""
Phase 9 — experience-based category engine + entitlement matrix.

Verifies: category resolution across all bands, category-driven auto-assignment
(intern vs permanent), the maternity/paternity eligibility gate, the apply guard
(category-limited types + working-day counting), and the compensatory ledger.
"""
from datetime import date, timedelta

import pytest
from rest_framework.test import APIClient

from users.models import User
from leaves import category_engine as ce
from leaves.models import LeaveBalance, CompensatoryLedger
from .conftest import MONDAY, YEAR

ET = User.EmploymentType
LC = User.LeaveCategory
G = User.Gender


def _mk(username, etype, months, role=User.Roles.MAKER, **kw):
    doj = date(YEAR - months // 12, ((MONDAY.month - 1 - months % 12) % 12) + 1, 1)
    u = User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        role=role, employment_type=etype, date_of_joining=doj, **kw,
    )
    ce.ensure_category_balances(u, YEAR)
    return u


@pytest.mark.parametrize("etype,months,expected,flagged", [
    (ET.INTERN, 40, LC.D, False),
    # Volunteers resolved to D alongside interns until Phase
    # LEAVE-POLICY-ENTERPRISE-IMPLEMENTATION. The policy gives them their own
    # tier (2 annual / 3 sick, and no maternity, paternity or compensatory
    # leave), so D paid them roughly double what they are entitled to.
    (ET.VOLUNTEER, 2, LC.E, False),
    (ET.VOLUNTEER, 60, LC.E, False),   # length of service never promotes one
    (ET.PERMANENT, 40, LC.A, False),
    (ET.PERMANENT, 24, LC.B, False),
    (ET.PERMANENT, 6, LC.C, False),          # fallback: permanent <1yr -> C
    (ET.POST_PROBATION, 6, LC.C, False),
    (ET.POST_PROBATION, 15, LC.B, True),     # >1yr -> B + flag
    (ET.POST_PROBATION, 1, LC.PROBATION, True),
    (ET.PROBATION, 1, LC.PROBATION, True),
    (ET.PROBATION, 5, LC.C, True),           # completed probation -> C + promote flag
])
def test_category_resolution(etype, months, expected, flagged):
    category, flag = ce.resolve_category(etype, months)
    assert category == expected
    assert (flag is not None) == flagged


@pytest.mark.django_db
def test_no_joining_date_falls_back_and_flags():
    category, flag = ce.resolve_category(ET.PERMANENT, None)
    assert category == LC.PROBATION
    assert flag and "joining" in flag.lower()


@pytest.mark.django_db
def test_intern_gets_4_5_and_maternity_by_arrangement():
    """
    THIS ASSERTION CHANGED WITH THE POLICY, and the history is the point.

    It pinned `{"annual": 8, "sick": 8}` — the figures the code happened to
    hold, not the ones the organisation publishes, which are 4 and 5. It also
    asserted that an eligible intern gets no maternity leave at all; the policy
    says "as per organization policy", i.e. granted case by case. That is now
    modelled as EntitlementRule.by_arrangement: the entitlement is OFFERED (so
    the intern is told it exists) but generates no balance row, because there
    is no yearly number to hold.

    The test passed throughout, which is exactly how the wrong entitlement
    survived: it was asserting the code back to itself.
    """
    intern = _mk("ci_intern", ET.INTERN, 5, gender=G.FEMALE, maternity_eligible=True)
    bal = {b.leave_type: b.total_allocated for b in LeaveBalance.objects.filter(user=intern, year=YEAR)}
    assert bal == {"annual": 4, "sick": 5}

    offered = {r.leave_type.code for r in ce.entitlements_for_user(intern)}
    assert "MATERNITY" in offered, "an intern is told maternity leave exists"
    rule = next(r for r in ce.entitlements_for_user(intern)
                if r.leave_type.code == "MATERNITY")
    assert rule.by_arrangement and float(rule.entitlement_days) == 0


@pytest.mark.django_db
def test_permanent_over_3yr_female_gets_12_12_maternity():
    perm = _mk("ci_perm", ET.PERMANENT, 40, gender=G.FEMALE, maternity_eligible=True)
    bal = {b.leave_type: b.total_allocated for b in LeaveBalance.objects.filter(user=perm, year=YEAR)}
    assert bal.get("annual") == 12 and bal.get("sick") == 12
    assert bal.get("maternity") == 30
    assert "paternity" not in bal  # not paternity-eligible


@pytest.mark.django_db
def test_permanent_1_to_3yr_gets_10_10():
    permB = _mk("ci_permB", ET.PERMANENT, 24)
    bal = {b.leave_type: b.total_allocated for b in LeaveBalance.objects.filter(user=permB, year=YEAR)}
    assert bal.get("annual") == 10 and bal.get("sick") == 10


@pytest.mark.django_db
def test_apply_blocks_type_not_in_category():
    """An intern cannot apply for maternity leave (not in Category D)."""
    intern = _mk("ci_intern2", ET.INTERN, 5, gender=G.FEMALE, maternity_eligible=True)
    err = ce.check_apply(intern, "maternity", MONDAY, MONDAY + timedelta(days=2))
    assert err and "not available" in err.lower()


@pytest.mark.django_db
def test_apply_guard_respects_category_annual_limit():
    """Category A annual = 12; a 13-working-day request is blocked, 5 is allowed."""
    perm = _mk("ci_perm2", ET.PERMANENT, 40)
    ok = ce.check_apply(perm, "annual", MONDAY, MONDAY + timedelta(days=4))  # 5 working days
    assert ok is None
    # 3 calendar weeks -> >12 working days
    big = ce.check_apply(perm, "annual", MONDAY, MONDAY + timedelta(days=20))
    assert big and "exceed" in big.lower()


@pytest.mark.django_db
def test_compensatory_grant_and_apply_validation():
    emp = _mk("ci_comp", ET.PERMANENT, 40)
    # No comp earned yet -> applying comp is blocked.
    assert ce.check_apply(emp, "compensatory", MONDAY, MONDAY) is not None
    # HR grants 2 comp days (confirmed) -> now 1 day is allowed.
    CompensatoryLedger.objects.create(
        user=emp, entry_type=CompensatoryLedger.EntryType.EARN, days=2,
        source=CompensatoryLedger.Source.HR_GRANT, status=CompensatoryLedger.Status.CONFIRMED,
    )
    assert float(ce.comp_available(emp)) == 2.0
    assert ce.check_apply(emp, "compensatory", MONDAY, MONDAY) is None  # 1 working day <= 2
