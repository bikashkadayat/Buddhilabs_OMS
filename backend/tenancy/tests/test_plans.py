"""Part 2: plans and prices come from the database, and prices are immutable."""
import datetime

import pytest

from tenancy import plans
from tenancy.exceptions import NoActivePrice
from tenancy.models import Plan, PlanPrice

pytestmark = pytest.mark.django_db

# The four plans exactly as the phase brief specifies them, in minor units.
EXPECTED = {
    "monthly": (1, 99900),
    "quarterly": (3, 259900),
    "halfyearly": (6, 499900),
    "annual": (12, 999000),
}


def test_the_four_plans_are_seeded_with_the_specified_prices():
    assert Plan.objects.count() == 4
    for code, (months, amount) in EXPECTED.items():
        plan = Plan.objects.get(code=code)
        assert plan.interval_months == months
        assert plans.current_price(plan).amount_minor == amount
        assert plans.current_price(plan).currency == "NPR"


def test_amounts_are_integer_minor_units_not_floats():
    """A price must never be a float: 999.00 does not survive a JSON round trip."""
    for price in PlanPrice.objects.all():
        assert isinstance(price.amount_minor, int)


def test_price_table_lists_every_purchasable_plan_cheapest_commitment_first():
    table = plans.price_table()
    assert [p.code for p, _ in table] == [
        "monthly", "quarterly", "halfyearly", "annual"]


def test_a_plan_with_no_effective_price_is_refused_not_defaulted_to_zero():
    plan = Plan.objects.create(code="enterprise", name="Enterprise",
                                interval_months=12)
    with pytest.raises(NoActivePrice):
        plans.current_price(plan)
    # ...and it is omitted from the picker rather than shown at NPR 0.
    assert "enterprise" not in [p.code for p, _ in plans.price_table()]


def test_a_new_price_supersedes_the_old_one_without_rewriting_it():
    """The no-hardcoded-pricing rule, and the immutability that makes it safe."""
    plan = Plan.objects.get(code="monthly")
    original = plans.current_price(plan)

    PlanPrice.objects.create(plan=plan, currency="NPR", amount_minor=129900,
                              effective_from=datetime.date(2027, 1, 1))

    # Before the new date, the old price still applies...
    assert plans.current_price(
        plan, on=datetime.date(2026, 12, 31)).amount_minor == 99900
    # ...and from it, the new one does.
    assert plans.current_price(
        plan, on=datetime.date(2027, 1, 1)).amount_minor == 129900
    # The original row is untouched, so anything citing it still reads the
    # amount it was sold at.
    original.refresh_from_db()
    assert original.amount_minor == 99900


def test_no_module_outside_tenancy_plans_names_a_price():
    """The one test that keeps "prices must not be hardcoded" true over time.

    Greps the backend for the seeded amounts. They may appear only in the seed
    migration that creates them and in this test file.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    allowed = {"0002_seed_plans.py", "test_plans.py"}
    offenders = []
    for path in root.rglob("*.py"):
        if "__pycache__" in str(path) or path.name in allowed:
            continue
        text = path.read_text(errors="ignore")
        for amount in ("99900", "259900", "499900", "999000"):
            if amount in text:
                offenders.append(f"{path.relative_to(root)}: {amount}")
    assert not offenders, f"hardcoded plan prices found: {offenders}"
