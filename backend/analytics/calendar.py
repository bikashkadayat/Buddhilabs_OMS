"""Expected working days -- the denominator every attendance percentage needs.

Why this exists at all: an Absent day usually has NO stored ``Attendance`` row.
Rows are written for a real check-in or an HR entry; absence is derived at read
time (see ``attendance.services.resolve_day_status``). So counting rows and
dividing gives a denominator smaller than reality, and an attendance rate that
flatters. This module supplies the honest denominator instead:

    expected_working_days(e, W) = days in W that are
        not Saturday, not an active holiday,
        on or after e's eligibility floor, and not in the future

The eligibility floor is the same rule ``absent_floor`` uses (the later of
account registration and date_of_joining), so analytics and the Phase 9
dashboards agree about when a person's history starts.

Cost: ONE query for holidays. Everything after that is pure Python over a sorted
array with ``bisect``, so a 250-employee, 36-month window is O(N log D) rather
than a query per employee.
"""
from bisect import bisect_left
from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

from . import periods

SATURDAY = 5


class WorkCalendar:
    """The working days of one window, and how many of them each employee owes.

    Immutable after construction and free of database access, so it can be built
    once per request and handed to every metric.
    """

    def __init__(self, window, holidays, today):
        self.window = window
        self.today = today
        # A future day cannot be attended, so a month-to-date rate must not be
        # divided by a month that has not happened yet.
        horizon = min(window.end, today)
        self.days = tuple(_working_days(window.start, horizon, holidays))
        self.holidays = frozenset(holidays)
        self._bucket_ranges = _bucket_ranges(self.days, window.granularity)

    # -- totals -----------------------------------------------------------
    def expected_for(self, floor):
        """Working days on or after one employee's eligibility floor."""
        if floor is None:
            # No registration date at all: nothing can be judged, so this
            # employee contributes no denominator rather than a full one.
            return 0
        return len(self.days) - bisect_left(self.days, floor)

    def total(self, floors):
        """Σ expected days over an iterable of eligibility floors."""
        return sum(self.expected_for(floor) for floor in floors)

    # -- per bucket -------------------------------------------------------
    def expected_by_bucket(self, floors):
        """{bucket key: expected working days} summed over many employees.

        Employees are grouped by floor first: a whole organisation usually has a
        handful of distinct joining dates, so this collapses to a few passes
        over the bucket ranges instead of one pass per person.
        """
        totals = defaultdict(int)
        for floor, count in _group(floors).items():
            index = 0 if floor is None else bisect_left(self.days, floor)
            if floor is None:
                continue
            for key, (low, high) in self._bucket_ranges.items():
                available = high - max(low, index)
                if available > 0:
                    totals[key] += available * count
        return dict(totals)

    def bucket_days(self):
        """{bucket key: working days in that bucket}, ignoring eligibility."""
        return {key: high - low for key, (low, high) in self._bucket_ranges.items()}


def build(window, today=None):
    """Construct the calendar for a window. ONE query."""
    from leaves.models import Holiday

    today = today or timezone.localdate()
    holidays = set(Holiday.objects.filter(
        is_active=True, date__gte=window.start, date__lte=window.end,
    ).values_list("date", flat=True))
    return WorkCalendar(window, holidays, today)


# ---------------------------------------------------------------------------
# scope-level helpers -- what the metrics actually call
# ---------------------------------------------------------------------------
def expected_total(scope, calendar):
    """Organisation- (or department-) wide expected working days."""
    return calendar.total(scope.floors.values())


def expected_by_department(scope, calendar):
    """{department key: expected working days}."""
    grouped = defaultdict(list)
    for employee_id, key in scope.department_of.items():
        grouped[key].append(scope.floors.get(employee_id))
    return {key: calendar.total(floors) for key, floors in grouped.items()}


def expected_series(scope, calendar):
    """{bucket key: expected working days} for the whole scope."""
    return calendar.expected_by_bucket(list(scope.floors.values()))


def expected_department_series(scope, calendar):
    """{department key: {bucket key: expected working days}}."""
    grouped = defaultdict(list)
    for employee_id, key in scope.department_of.items():
        grouped[key].append(scope.floors.get(employee_id))
    return {key: calendar.expected_by_bucket(floors) for key, floors in grouped.items()}


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------
def _working_days(start, end, holidays):
    day = start
    while day <= end:
        if day.weekday() != SATURDAY and day not in holidays:
            yield day
        day += timedelta(days=1)


def _bucket_ranges(days, granularity):
    """{bucket key: (first index, last index + 1)} over the sorted day array.

    Half-open index ranges make "how many working days of this bucket fall on or
    after index i" a subtraction instead of a scan.
    """
    ranges = {}
    for index, day in enumerate(days):
        key = periods.bucket_key(day, granularity)
        low, _high = ranges.get(key, (index, index))
        ranges[key] = (low, index + 1)
    return ranges


def _group(values):
    counts = defaultdict(int)
    for value in values:
        counts[value] += 1
    return counts
