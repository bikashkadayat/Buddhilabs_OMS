"""Shared query fragments and number formatting for every metric module.

Two rules encoded here, both of which exist because breaking them is how an
analytics layer starts lying:

* **Divide by zero returns ``None``, never 0.** An empty department has no
  attendance rate; reporting 0% would put it bottom of a ranking it should not
  appear in at all.
* **Percentages leave here as rounded floats, not ``Decimal``.** Every one of
  these values ends up on a chart axis, and ``Decimal`` serialises to a string
  that recharts silently plots as zero.
"""
from decimal import Decimal

from django.db.models import Case, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import TruncMonth, TruncQuarter, TruncYear
from django.utils import timezone

from attendance.models import Attendance
from leaves.models import LeaveDayRecord

ZERO = Decimal("0.00")

# Statuses that mean the employee turned up in some form. Derived from the enum
# rather than written out, so a new status is a deliberate decision here instead
# of a silent omission (the Phase 8 WORK_FROM_HOME lesson).
ATTENDED_STATUSES = (
    Attendance.Status.PRESENT,
    Attendance.Status.LATE,
    Attendance.Status.HALF_DAY,
    Attendance.Status.WORK_FROM_HOME,
)
SATURDAY_WEEK_DAY = 7  # Django's __week_day: 1 = Sunday ... 7 = Saturday


# ---------------------------------------------------------------------------
# numbers
# ---------------------------------------------------------------------------
def pct(numerator, denominator, places=1):
    """Percentage, or None when there is nothing to divide by."""
    if not denominator:
        return None
    return round(float(numerator) / float(denominator) * 100, places)


def ratio(numerator, denominator, places=3):
    if not denominator:
        return None
    return round(float(numerator) / float(denominator), places)


def num(value, places=2):
    """A Decimal/None-safe float for JSON."""
    if value is None:
        return 0.0
    return round(float(value), places)


def clamp(value, low=0.0, high=100.0):
    if value is None:
        return None
    return max(low, min(high, value))


def delta(current, previous, places=1):
    """Signed change against a comparison period, or None if either is missing."""
    if current is None or previous is None:
        return None
    return round(float(current) - float(previous), places)


# ---------------------------------------------------------------------------
# querysets
# ---------------------------------------------------------------------------
def attendance_rows(scope, window, calendar=None, working_days_only=False):
    """Attendance rows for a scope and window.

    ``working_days_only`` matters for rates but NOT for hours: Saturday work is
    real overtime and must stay in the overtime total, while counting it as an
    attended working day would inflate every compliance figure.
    """
    qs = Attendance.objects.filter(
        employee_id__in=scope.employee_ids,
        date__gte=window.start, date__lte=window.end,
    )
    if working_days_only and calendar is not None:
        qs = qs.filter(working_day_q(calendar))
    return qs


def leave_rows(scope, window, working_days_only=True):
    """Approved leave day records. Weekend and holiday records are excluded by
    default -- the model already flags them, and leave on a Saturday consumes no
    working day."""
    qs = LeaveDayRecord.objects.filter(
        user_id__in=scope.employee_ids,
        status=LeaveDayRecord.Status.APPROVED,
        leave_request__is_deleted=False,
        date__gte=window.start, date__lte=window.end,
    )
    if working_days_only:
        qs = qs.filter(is_weekend=False, is_holiday=False)
    return qs


def working_day_q(calendar, field="date"):
    """Exclude Saturdays, active holidays and the future.

    Built from the calendar's own holiday set so the numerator can never be
    computed over a different set of days than the denominator.
    """
    horizon = min(calendar.window.end, calendar.today)
    condition = Q(**{f"{field}__lte": horizon}) & ~Q(**{f"{field}__week_day": SATURDAY_WEEK_DAY})
    if calendar.holidays:
        condition &= ~Q(**{f"{field}__in": sorted(calendar.holidays)})
    return condition


# ---------------------------------------------------------------------------
# bucketing
# ---------------------------------------------------------------------------
_TRUNC = {"month": TruncMonth, "quarter": TruncQuarter, "year": TruncYear}


def bucket_expr(granularity, field="date"):
    """The GROUP BY expression for a granularity. Day needs no truncation."""
    if granularity == "day":
        return F(field)
    return _TRUNC[granularity](field)


def bucketed(qs, window, field="date"):
    """Annotate a queryset with the window's bucket, ready to group on."""
    return qs.annotate(bucket=bucket_expr(window.granularity, field))


# ---------------------------------------------------------------------------
# leave weighting
# ---------------------------------------------------------------------------
def leave_weight_sum():
    """SUM of leave-day weights: a half-day counts 0.5.

    Done in SQL rather than by iterating rows and reading the ``portion_days``
    property, which would be one Python object per leave day in the window.
    """
    return Sum(Case(
        When(day_portion=LeaveDayRecord.DayPortion.FULL, then=Value(Decimal("1.0"))),
        default=Value(Decimal("0.5")),
        output_field=DecimalField(max_digits=10, decimal_places=2),
    ))


def status_counts(prefix=""):
    """Per-status counts as aggregate kwargs, derived from the enum."""
    from django.db.models import Count

    return {
        f"{prefix}{status.value}": Count("id", filter=Q(status=status))
        for status in Attendance.Status
    }


def today():
    return timezone.localdate()
