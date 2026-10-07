"""Every dashboard number, computed once, in one role-agnostic place.

The three dashboards differ only in *which employees* they cover and *which
sections* they include. Keeping the arithmetic here means the employee view, the
manager view, the HR view and a plain page refresh can never disagree — the same
reason ``dashboard_views`` exists for the live widgets.

Query discipline (Phase 9 budgets):

* Holiday and approved-leave sets are fetched ONCE per call and passed into the
  per-employee resolver, never queried inside the loop.
* ``resolve_day_status`` performs no queries — ``absent_floor`` reads model
  fields already loaded on the User.
* Trends and breakdowns are single grouped aggregates, not a query per bucket.
* Callers wrap the whole handler in ``resolver.policy_cache()``.
"""
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.utils import timezone

from users.models import User

from .. import services
from ..models import Attendance

TREND_DAYS = 30
RECENT_HISTORY_DAYS = 14


# ---------------------------------------------------------------------------
# scoping
# ---------------------------------------------------------------------------
def scoped_employees(user):
    """Employees this user may see.

    Delegates to the Phase 7 helper so the workforce dashboards and the live
    attendance widgets can never drift apart.
    """
    from ..dashboard_views import scoped_employees as _scoped

    return _scoped(user)


def empty_counts():
    """A zeroed bucket per status, derived from the enum.

    Never a literal dict: a hardcoded one turned into an HTTP 500 the moment
    Phase 8 added WORK_FROM_HOME, and it would do so again.
    """
    counts = {s.value: 0 for s in Attendance.Status}
    counts["not_applicable"] = 0
    return counts


# ---------------------------------------------------------------------------
# day status, in bulk
# ---------------------------------------------------------------------------
def leave_user_ids(emp_ids, day):
    from leaves.models import Leave

    return set(Leave.objects.filter(
        user_id__in=emp_ids, status=Leave.Status.APPROVED, is_deleted=False,
        start_date__lte=day, end_date__gte=day,
    ).values_list("user_id", flat=True))


def day_counts(employees, day, records=None, now=None):
    """Status counts for a set of employees on one day.

    Three queries total regardless of headcount: the attendance rows, the leave
    set, and the holiday check.
    """
    emp_ids = [e.pk for e in employees]
    if records is None:
        records = {a.employee_id: a for a in Attendance.objects.filter(
            employee_id__in=emp_ids, date=day)}
    holiday = services.is_holiday(day)
    on_leave = leave_user_ids(emp_ids, day)
    today = timezone.localdate()
    now = now or services.now_local()

    counts = empty_counts()
    per_employee = {}
    for employee in employees:
        status = services.resolve_day_status(
            record=records.get(employee.pk),
            is_holiday_day=holiday,
            is_leave_day=employee.pk in on_leave,
            d=day, floor=services.absent_floor(employee), today=today, now=now,
        )
        key = "not_applicable" if status is None else status
        counts[key] += 1
        per_employee[employee.pk] = status
    return counts, per_employee, holiday


def present_now(counts):
    """Everyone who has actually started work today, however they did it."""
    return (counts["present"] + counts["late"] + counts["half_day"]
            + counts[Attendance.Status.WORK_FROM_HOME])


# ---------------------------------------------------------------------------
# trends and breakdowns — one grouped query each
# ---------------------------------------------------------------------------
def attendance_trend(emp_ids, days=TREND_DAYS, end=None):
    """Daily status counts over a window. ONE grouped query."""
    end = end or timezone.localdate()
    start = end - timedelta(days=days - 1)
    rows = (Attendance.objects
            .filter(employee_id__in=emp_ids, date__gte=start, date__lte=end)
            .values("date", "status")
            .annotate(n=Count("id")))

    buckets = defaultdict(lambda: {s.value: 0 for s in Attendance.Status})
    for row in rows:
        buckets[row["date"]][row["status"]] = row["n"]

    series = []
    day = start
    while day <= end:
        bucket = buckets.get(day, {s.value: 0 for s in Attendance.Status})
        series.append({"date": day.isoformat(), **bucket})
        day += timedelta(days=1)
    return series


def department_breakdown(employees, day, records=None):
    """Per-department status counts. No query per department."""
    emp_ids = [e.pk for e in employees]
    if records is None:
        records = {a.employee_id: a for a in Attendance.objects.filter(
            employee_id__in=emp_ids, date=day)}
    _totals, per_employee, _holiday = day_counts(employees, day, records=records)

    grouped = defaultdict(empty_counts)
    names = {}
    for employee in employees:
        department = employee.department_ref
        key = str(department.pk) if department else "unassigned"
        names[key] = department.name if department else "Unassigned"
        status = per_employee.get(employee.pk)
        grouped[key]["not_applicable" if status is None else status] += 1

    return [{
        "department_id": None if key == "unassigned" else key,
        "department": names[key],
        "counts": dict(counts),
        "present_now": present_now(counts),
        "headcount": sum(counts.values()),
    } for key, counts in sorted(grouped.items(), key=lambda kv: names[kv[0]])]


def hours_summary(emp_ids, start, end):
    """Worked / regular / overtime totals for a window. ONE aggregate query."""
    totals = Attendance.objects.filter(
        employee_id__in=emp_ids, date__gte=start, date__lte=end,
    ).aggregate(
        working=Sum("working_hours"), regular=Sum("regular_hours"),
        overtime=Sum("overtime_hours"),
        late_days=Count("id", filter=Q(status=Attendance.Status.LATE)),
        late_minutes=Sum("late_minutes"),
    )
    return {key: (value or 0) for key, value in totals.items()}


# ---------------------------------------------------------------------------
# queues — one count each, never a list
# ---------------------------------------------------------------------------
def pending_queue_counts(user, emp_ids=None):
    """Sizes of everything waiting on this user's role."""
    from leaves.models import CompensatoryLedger, Leave

    from ..models import WFHRequest
    from .models import AttendanceCorrectionRequest

    is_hr = user.role in (User.Roles.APPROVER, User.Roles.ADMIN)

    corrections = AttendanceCorrectionRequest.objects.all()
    wfh = WFHRequest.objects.filter(status=WFHRequest.Status.PENDING)
    if not is_hr and emp_ids is not None:
        corrections = corrections.filter(employee_id__in=emp_ids)
        wfh = wfh.filter(user_id__in=emp_ids)

    queues = {
        "corrections_manager_stage": corrections.filter(
            status=AttendanceCorrectionRequest.Status.PENDING).count(),
        "corrections_hr_stage": corrections.filter(
            status=AttendanceCorrectionRequest.Status.MANAGER_APPROVED).count(),
        "wfh_pending": wfh.count(),
    }
    if is_hr:
        queues["comp_off_pending"] = CompensatoryLedger.objects.filter(
            entry_type=CompensatoryLedger.EntryType.EARN,
            source=CompensatoryLedger.Source.ATTENDANCE,
            status=CompensatoryLedger.Status.PENDING).count()
        queues["leave_pending_hr"] = Leave.objects.filter(
            status=Leave.Status.PENDING_HR, is_deleted=False).count()
    return queues


# ---------------------------------------------------------------------------
# WFH and comp-off summaries
# ---------------------------------------------------------------------------
def wfh_summary(emp_ids, start, end):
    from ..models import WFHRequest

    qs = WFHRequest.objects.filter(
        user_id__in=emp_ids, start_date__lte=end, end_date__gte=start)
    by_status = {row["status"]: row["n"] for row in
                 qs.values("status").annotate(n=Count("id"))}
    approved_days = Attendance.objects.filter(
        employee_id__in=emp_ids, date__gte=start, date__lte=end, is_wfh=True,
    ).count()
    worked_days = Attendance.objects.filter(
        employee_id__in=emp_ids, date__gte=start, date__lte=end,
        status=Attendance.Status.WORK_FROM_HOME,
    ).count()
    return {
        "requests": {s.value: by_status.get(s.value, 0) for s in WFHRequest.Status},
        "approved_day_rows": approved_days,
        "worked_from_home_days": worked_days,
        "conversion": round(worked_days / approved_days, 2) if approved_days else None,
    }


def comp_off_summary(emp_ids):
    """Org- or team-level comp position. Mirrors ``category_engine.comp_summary``
    but aggregated over many users in one query instead of one query per user."""
    from leaves.models import CompensatoryLedger

    rows = (CompensatoryLedger.objects.filter(user_id__in=emp_ids)
            .values("entry_type", "status")
            .annotate(days=Sum("days"), n=Count("id")))
    earned = used = pending = 0
    for row in rows:
        if row["entry_type"] == CompensatoryLedger.EntryType.USE:
            used += row["days"] or 0
        elif row["status"] == CompensatoryLedger.Status.CONFIRMED:
            earned += row["days"] or 0
        else:
            pending += row["days"] or 0
    return {"earned": earned, "used": used,
            "available": earned - used, "pending": pending}


def comp_off_trend(emp_ids, months=6, end=None):
    """Comp days earned per month. ONE grouped query."""
    from django.db.models.functions import TruncMonth

    from leaves.models import CompensatoryLedger

    end = end or timezone.localdate()
    start = (end.replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    rows = (CompensatoryLedger.objects
            .filter(user_id__in=emp_ids,
                    entry_type=CompensatoryLedger.EntryType.EARN,
                    source_date__gte=start, source_date__lte=end)
            .annotate(month=TruncMonth("source_date"))
            .values("month", "status")
            .annotate(days=Sum("days")))
    buckets = defaultdict(lambda: {"confirmed": 0, "pending": 0})
    for row in rows:
        if row["month"] is None:
            continue
        buckets[row["month"].strftime("%Y-%m")][row["status"]] += row["days"] or 0
    return [{"month": month, **values} for month, values in sorted(buckets.items())]


# ---------------------------------------------------------------------------
# one employee
# ---------------------------------------------------------------------------
def employee_window(employee, days=RECENT_HISTORY_DAYS, end=None):
    """Everything needed to resolve one employee's recent days, in 3 queries.

    Returned rather than consumed so the caller can reuse the holiday and leave
    sets for *today* too, instead of paying ``is_holiday`` +
    ``has_approved_leave`` all over again — which is most of what kept the
    employee dashboard over its query budget.
    """
    from leaves.models import Holiday, Leave

    end = end or timezone.localdate()
    start = end - timedelta(days=days - 1)

    records = {a.date: a for a in Attendance.objects.filter(
        employee=employee, date__gte=start, date__lte=end)}
    holidays = set(Holiday.objects.filter(
        is_active=True, date__gte=start, date__lte=end).values_list("date", flat=True))

    leave_days = set()
    for leave_start, leave_end in Leave.objects.filter(
            user=employee, status=Leave.Status.APPROVED, is_deleted=False,
            start_date__lte=end, end_date__gte=start,
    ).values_list("start_date", "end_date"):
        day = max(leave_start, start)
        while day <= min(leave_end, end):
            leave_days.add(day)
            day += timedelta(days=1)

    return {"start": start, "end": end, "records": records,
            "holidays": holidays, "leave_days": leave_days}


def is_holiday_day(day, holidays):
    """Saturday, or an active public holiday from a pre-fetched set."""
    return day.weekday() == 5 or day in holidays


def recent_history(employee, days=RECENT_HISTORY_DAYS, end=None, window=None):
    """The employee's last N days, resolved."""
    window = window or employee_window(employee, days, end)
    start, end = window["start"], window["end"]
    records, holidays, leave_days = (
        window["records"], window["holidays"], window["leave_days"])

    floor = services.absent_floor(employee)
    today = timezone.localdate()
    now = services.now_local()

    out = []
    day = end
    while day >= start:
        record = records.get(day)
        status = services.resolve_day_status(
            record=record,
            is_holiday_day=is_holiday_day(day, holidays),
            is_leave_day=day in leave_days,
            d=day, floor=floor, today=today, now=now)
        out.append({
            "date": day.isoformat(),
            "status": status,
            "check_in": record.check_in.isoformat() if record and record.check_in else None,
            "check_out": record.check_out.isoformat() if record and record.check_out else None,
            "working_hours": str(record.working_hours) if record else "0.00",
            "overtime_hours": str(record.overtime_hours) if record else "0.00",
            "late_minutes": record.late_minutes if record else 0,
            "is_wfh": bool(record and record.is_wfh),
            "source": record.source if record else None,
        })
        day -= timedelta(days=1)
    return out
