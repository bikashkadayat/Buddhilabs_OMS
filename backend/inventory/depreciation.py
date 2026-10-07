"""
Asset depreciation (Phase ASSET-LIFECYCLE-DISPOSAL).

THE METHOD
----------
Straight-line, by whole months, from the purchase date:

    depreciable basis   = purchase cost - salvage value
    monthly charge      = basis / useful life (months)
    accumulated to date = basis * months elapsed / useful life, capped at basis
    book value          = purchase cost - accumulated

Straight-line because it is what an organisation of this size reports and what an
auditor expects to reconcile; a declining-balance schedule would need a rate
policy nobody here has set, and inventing one would be worse than not offering it.

WHY NOTHING HERE IS STORED
--------------------------
Only the inputs live on the asset. Accumulated depreciation and book value are
computed when they are asked for, so they are right on the day of the report and
can never drift from the cost they came from - a stored book value is wrong the
first morning nobody ran the job that updates it.

WHY WHOLE MONTHS, AND WHY THE MONTH COUNTS ON ITS DAY
-----------------------------------------------------
A month is charged once the purchase day-of-month has been reached. Charging by
day would produce a book value that changes every morning by a few paisa, which
is noise on a report and matches no accounting policy in use here.

Accumulated depreciation is computed as basis x months / life in one division,
NOT as a rounded monthly charge times months. Multiplying a rounded charge
accumulates the rounding error, and on the last month the asset would be written
down to a few paisa above or below its salvage value.

A DISPOSED ASSET STOPS DEPRECIATING
-----------------------------------
Its book value is frozen at the disposal date. That frozen figure is what the
write-off report compares disposal proceeds against.
"""
import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def add_months(start, months):
    """`start` plus `months`, clamped to the last day of a shorter month."""
    total = start.month - 1 + months
    year, month = start.year + total // 12, total % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def months_elapsed(start, as_of):
    """Whole months from `start` to `as_of`, counting a month once its day arrives."""
    if as_of <= start:
        return 0
    months = (as_of.year - start.year) * 12 + (as_of.month - start.month)
    # The last month only counts once its day-of-month has been reached, allowing
    # for a start day (e.g. the 31st) that a shorter month does not have.
    anniversary = add_months(start, months)
    if anniversary > as_of:
        months -= 1
    return max(months, 0)


def _money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def depreciation(item, as_of=None):
    """
    The depreciation position of `item` on `as_of` (default: today, or the
    disposal date for a disposed asset). Always returns a dict; when the asset
    cannot be depreciated, `depreciable` is False and `reason` says why in words
    a store officer can act on.
    """
    from django.utils import timezone

    today = timezone.localdate()
    disposed_on = timezone.localtime(item.disposed_at).date() if item.disposed_at else None
    as_of = as_of or today
    if disposed_on and as_of > disposed_on:
        as_of = disposed_on

    cost = item.purchase_cost
    life = item.effective_useful_life_months
    missing = [label for label, present in (
        ("purchase cost", cost is not None),
        ("purchase date", item.purchase_date is not None),
        ("useful life", bool(life)),
    ) if not present]

    base = {
        "as_of": as_of,
        "frozen_at_disposal": bool(disposed_on and as_of == disposed_on),
        "method": "straight_line",
        "purchase_cost": _money(cost) if cost is not None else None,
        "purchase_date": item.purchase_date,
        "useful_life_months": life,
        "useful_life_source": ("asset" if item.useful_life_months
                               else "category" if life else None),
        "end_of_life_date": item.end_of_life_date,
        "end_of_life_state": item.end_of_life_state,
    }
    if missing:
        return {**base, "depreciable": False,
                "reason": f"Record the {' and '.join(missing)} to depreciate this asset.",
                "salvage_value": None, "monthly_charge": None,
                "months_elapsed": None, "accumulated": None, "book_value": None,
                "percent_depreciated": None, "fully_depreciated": None}

    cost = Decimal(cost)
    salvage = Decimal(item.salvage_value or 0)
    if salvage > cost:
        # A salvage value above cost is a data-entry mistake, not a policy; clamp
        # rather than depreciate by a negative amount.
        salvage = cost
    basis = cost - salvage
    months = months_elapsed(item.purchase_date, as_of)
    charged = min(months, life)
    accumulated = basis if charged >= life else basis * charged / life
    book = cost - accumulated

    return {
        **base,
        "depreciable": True,
        "reason": "",
        "salvage_value": _money(salvage),
        "monthly_charge": _money(basis / life),
        "months_elapsed": months,
        "accumulated": _money(accumulated),
        "book_value": _money(book),
        # Computed here, never on the client (project rule).
        "percent_depreciated": (int((accumulated / basis * 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP)) if basis else 100),
        "fully_depreciated": charged >= life,
    }
