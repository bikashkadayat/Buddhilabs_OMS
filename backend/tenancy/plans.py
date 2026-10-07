"""Plan price resolution.

The ONLY place a price is looked up. Nothing else in the codebase may name an
amount: a price is a row in ``PlanPrice``, selected by date, and that is the
whole of the "no hardcoded pricing" rule made operational.
"""
from django.utils import timezone

from .exceptions import NoActivePrice
from .models import Plan, PlanPrice

DEFAULT_CURRENCY = "NPR"


def current_price(plan, *, on=None, currency=DEFAULT_CURRENCY):
    """The PlanPrice effective for ``plan`` on ``on`` (default: today).

    Picks the latest ``effective_from`` that is not in the future and whose
    ``effective_until`` has not passed, so superseding a price is a matter of
    inserting a row with a later start date -- no update, no deletion, and the
    old row stays readable for the subscriptions and invoices that cite it.
    """
    on = on or timezone.localdate()
    price = (PlanPrice.objects
             .filter(plan=plan, currency=currency, effective_from__lte=on)
             .filter(models_q_until(on))
             .order_by("-effective_from")
             .first())
    if price is None:
        raise NoActivePrice(
            f"Plan '{plan.code}' has no {currency} price effective on {on}. "
            f"Insert a PlanPrice row rather than hardcoding an amount.")
    return price


def models_q_until(on):
    from django.db.models import Q

    return Q(effective_until__isnull=True) | Q(effective_until__gte=on)


def purchasable_plans():
    """Plans a customer may be sold today, cheapest commitment first."""
    return (Plan.objects.filter(is_active=True, is_public=True)
            .order_by("sort_order", "interval_months"))


def price_table(*, on=None, currency=DEFAULT_CURRENCY):
    """``[(plan, price)]`` for every purchasable plan, for a plan picker.

    Plans with no effective price are SKIPPED rather than shown at zero: a plan
    that cannot be priced cannot be sold, and showing "NPR 0" would be a
    promise the platform has to keep.
    """
    rows = []
    for plan in purchasable_plans():
        try:
            rows.append((plan, current_price(plan, on=on, currency=currency)))
        except NoActivePrice:
            continue
    return rows
