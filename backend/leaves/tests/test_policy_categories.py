"""
Phase LEAVE-POLICY-ENTERPRISE-IMPLEMENTATION.

The organisation's published leave policy, asserted end to end: engagement type
plus length of service in, category and entitlement days out, for all six
tiers. Written from the policy document rather than from the code, so it fails
if the code is changed away from the policy — which is the whole point, since
the entitlement matrix is HR-editable at runtime.

The bug these were written against: Category D meant "Intern / Volunteer" and
granted both 8 annual and 8 sick days. Policy gives an intern 4 and 5 and a
volunteer 2 and 3, so volunteers held four times their annual entitlement.
"""
from datetime import date, timedelta

import pytest

from leaves import category_engine as ce
from leaves.models import EntitlementRule
from users.models import User

from .conftest import _user, YEAR

ET = User.EmploymentType
LC = User.LeaveCategory
def _joined(months_ago):
    """
    A joining date exactly `months_ago` whole months before TODAY.

    Anchored to the engine's own idea of today rather than a literal: several
    of the paths under test (ensure_category_balances) resolve against the real
    current date, so a hard-coded anchor made these tests pass or fail
    depending on the month they were run in.
    """
    today = ce.nepal_today()
    y, m = divmod((today.year * 12 + today.month - 1) - months_ago, 12)
    return date(y, m + 1, min(today.day, 28))


# --- the published policy, transcribed -------------------------------------
# category: {leave code: days}, plus which codes are offered at all.
POLICY_DAYS = {
    "PROBATION": {"ANNUAL": 0, "SICK": 5, "MATERNITY": 30, "PATERNITY": 15},
    "C":         {"ANNUAL": 8, "SICK": 10, "MATERNITY": 30, "PATERNITY": 15},
    "B":         {"ANNUAL": 10, "SICK": 10, "MATERNITY": 30, "PATERNITY": 15},
    "A":         {"ANNUAL": 12, "SICK": 12, "MATERNITY": 30, "PATERNITY": 15},
    "D":         {"ANNUAL": 4, "SICK": 5},
    "E":         {"ANNUAL": 2, "SICK": 3},
}
COMPENSATORY_APPLIES = {"PROBATION": True, "C": True, "B": True, "A": True,
                        "D": False, "E": False}
BY_ARRANGEMENT = {"D": {"MATERNITY", "PATERNITY"}}
NOT_APPLICABLE = {"E": {"MATERNITY", "PATERNITY"}}


@pytest.mark.django_db
@pytest.mark.parametrize("category,codes", sorted(POLICY_DAYS.items()))
def test_entitlement_days_match_the_published_policy(category, codes):
    for code, days in codes.items():
        rule = EntitlementRule.objects.get(
            category=category, leave_type__code=code)
        assert rule.applicable, f"{category}/{code} should be offered"
        assert float(rule.entitlement_days) == days, (
            f"{category}/{code}: policy says {days}, matrix says "
            f"{rule.entitlement_days}")


@pytest.mark.django_db
@pytest.mark.parametrize("category,applies", sorted(COMPENSATORY_APPLIES.items()))
def test_compensatory_availability_matches_policy(category, applies):
    rule = EntitlementRule.objects.get(
        category=category, leave_type__code="COMPENSATORY")
    assert rule.applicable is applies, (
        f"{category}: compensatory should be "
        f"{'available' if applies else 'not applicable'}")


@pytest.mark.django_db
def test_intern_maternity_is_by_arrangement_not_zero_and_not_hidden():
    """
    "As per organization policy" is a THIRD state. Zero days reads as "you get
    none" and applicable=False hides it; both tell an intern something untrue.
    """
    for code in BY_ARRANGEMENT["D"]:
        rule = EntitlementRule.objects.get(category="D", leave_type__code=code)
        assert rule.applicable, f"D/{code} must still be offered"
        assert rule.by_arrangement, f"D/{code} must be marked by-arrangement"


@pytest.mark.django_db
def test_volunteer_maternity_really_is_not_applicable():
    for code in NOT_APPLICABLE["E"]:
        rule = EntitlementRule.objects.get(category="E", leave_type__code=code)
        assert not rule.applicable, f"E/{code} is Not Applicable under policy"


# --- automatic assignment ---------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("employment_type,months,expected", [
    (ET.PERMANENT, 0, LC.C),          # permanent under a year
    (ET.PERMANENT, 11, LC.C),
    (ET.PERMANENT, 13, LC.B),         # past one year
    (ET.PERMANENT, 35, LC.B),
    (ET.PERMANENT, 37, LC.A),         # past three years
    (ET.PROBATION, 1, LC.PROBATION),  # inside probation
    (ET.PROBATION, 2, LC.PROBATION),
    (ET.PROBATION, 3, LC.C),          # probation served
    (ET.POST_PROBATION, 6, LC.C),
    (ET.INTERN, 0, LC.D),
    (ET.INTERN, 60, LC.D),            # service never promotes an intern
    (ET.VOLUNTEER, 0, LC.E),
    (ET.VOLUNTEER, 60, LC.E),         # nor a volunteer
])
def test_category_is_assigned_automatically(employment_type, months, expected):
    category, _flag = ce.resolve_category(employment_type, months)
    assert category == expected


@pytest.mark.django_db
def test_crossing_a_service_threshold_promotes_without_intervention():
    """A permanent employee one day short of a year, then past it."""
    today = ce.nepal_today()
    user = _user("threshold", "maker", employment_type=ET.PERMANENT,
                 date_of_joining=_joined(11))
    # 11 months of service -> Category C
    assert ce.resolve_and_cache(user, today=today)[0] == LC.C
    # the same employee two months later -> B, with nobody editing anything
    later = today + timedelta(days=62)
    assert ce.resolve_and_cache(user, today=later)[0] == LC.B
    user.refresh_from_db()
    assert user.leave_category == LC.B


# --- balances the employee actually sees ------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("employment_type,months,category,annual,sick", [
    (ET.PROBATION, 1, "PROBATION", 0, 5),
    (ET.POST_PROBATION, 6, "C", 8, 10),
    (ET.PERMANENT, 24, "B", 10, 10),
    (ET.PERMANENT, 48, "A", 12, 12),
    (ET.INTERN, 2, "D", 4, 5),
    (ET.VOLUNTEER, 2, "E", 2, 3),
])
def test_generated_balances_match_the_policy(employment_type, months,
                                             category, annual, sick):
    user = _user(f"bal_{category.lower()}_{months}", "maker",
                 employment_type=employment_type, date_of_joining=_joined(months))
    ce.ensure_category_balances(user, YEAR)
    user.refresh_from_db()
    assert user.leave_category == category

    from leaves.models import LeaveBalance
    rows = {b.leave_type: b for b in LeaveBalance.objects.filter(user=user, year=YEAR)}
    assert rows["annual"].total_allocated == annual
    assert rows["sick"].total_allocated == sick
    # No allocation may ever be negative.
    assert all(b.total_allocated >= 0 and b.used_so_far >= 0 for b in rows.values())


@pytest.mark.django_db
def test_an_intern_gets_no_zero_day_maternity_balance_row():
    """
    A by-arrangement entitlement must not become a balance row: "0 of 0
    remaining" is the opposite of what "as per organisation policy" means.
    """
    from leaves.models import LeaveBalance
    intern = _user("intern_no_zero", "maker", employment_type=ET.INTERN,
                   date_of_joining=_joined(2), gender=User.Gender.FEMALE,
                   maternity_eligible=True)
    ce.ensure_category_balances(intern, YEAR)
    codes = set(LeaveBalance.objects.filter(user=intern, year=YEAR)
                .values_list("leave_type", flat=True))
    assert "maternity" not in codes
    # but it IS still offered, so the UI can show the arrangement note
    offered = {r.leave_type.code for r in ce.entitlements_for_user(intern)}
    assert "MATERNITY" in offered


@pytest.mark.django_db
def test_a_volunteer_is_offered_neither_maternity_nor_compensatory():
    volunteer = _user("vol_none", "maker", employment_type=ET.VOLUNTEER,
                      date_of_joining=_joined(30), gender=User.Gender.FEMALE,
                      maternity_eligible=True)
    ce.ensure_category_balances(volunteer, YEAR)
    offered = {r.leave_type.code for r in ce.entitlements_for_user(volunteer)}
    assert "MATERNITY" not in offered
    assert "COMPENSATORY" not in offered
    assert {"ANNUAL", "SICK"} <= offered


# --- drift guard ------------------------------------------------------------
@pytest.mark.django_db
def test_matrix_matches_policy():
    """
    The engine's literal and the database must agree. Migration 0013 and 0019
    carry frozen copies (migrations must not import app code), so without this
    the two can diverge and nothing would say so.
    """
    stats = ce.seed_entitlement_matrix()
    assert stats["created"] == 0 and stats["corrected"] == 0, (
        "EntitlementRule rows have drifted from ENTITLEMENT_MATRIX: "
        f"{stats}")
