"""Calendar arithmetic for subscription periods.

``dateutil`` is deliberately not a dependency of this project, and a
subscription period cannot be expressed in days: a monthly plan bought on the
31st must renew on the 28th of February and on the 31st of March, not 30 days
later each time. So month addition is written out, once, here.
"""
import calendar
import datetime


def add_months(start, months):
    """``start`` plus ``months`` calendar months, clamped to the month's length.

        2026-01-31 + 1 month -> 2026-02-28   (2028 -> 02-29, a leap year)
        2026-03-31 + 1 month -> 2026-04-30
        2026-01-15 + 12 months -> 2027-01-15

    Clamping down rather than spilling into the next month is the conventional
    billing behaviour and the only one that cannot hand a customer a free day.
    """
    if months < 0:
        raise ValueError("months must be >= 0")
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def add_days(start, days):
    return start + datetime.timedelta(days=days)
