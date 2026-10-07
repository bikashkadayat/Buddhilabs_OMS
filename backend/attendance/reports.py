"""Attendance report data builders (single employee + efficient bulk).

Reuses the same auto-integration rules as the live views: Saturday + public
holidays -> Holiday; approved leave -> On Leave; excluded from working days.
"""
from collections import defaultdict
from datetime import date as _date, timedelta

from django.utils import timezone

from config.nepali_dates import to_bs
from leaves.models import Holiday, Leave
from . import services
from .models import Attendance

# Python weekday(): Mon=0 .. Sun=6
DAY_NAME = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# Derived from the model so a new status is summarised automatically instead of
# being silently dropped from every PDF and Excel export. Order is fixed for the
# report columns; anything added later is appended.
_LEGACY_ORDER = ["present", "absent", "late", "half_day", "on_leave", "holiday"]
STATUS_KEYS = _LEGACY_ORDER + [
    s.value for s in Attendance.Status if s.value not in _LEGACY_ORDER
]
STATUS_LABEL = {s.value: s.label for s in Attendance.Status}
STATUS_LABEL["upcoming"] = "—"


def week_range(anchor):
    """Sunday–Saturday week containing `anchor` (spec: Sun–Sat)."""
    sunday = anchor - timedelta(days=(anchor.weekday() + 1) % 7)
    return sunday, sunday + timedelta(days=6)


def month_range(year, month):
    start = _date(year, month, 1)
    end = (_date(year, 12, 31) if month == 12
           else _date(year, month + 1, 1) - timedelta(days=1))
    return start, end


def _daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _leave_dates(emp_id, leaves, start, end):
    out = set()
    for lv in leaves:
        d = max(lv.start_date, start)
        last = min(lv.end_date, end)
        while d <= last:
            out.add(d)
            d += timedelta(days=1)
    return out


def _status_for(rec, d, holiday_dates, leave_dates, today, floor):
    """Delegates to the canonical resolver so reports agree with the dashboard /
    calendar. Returns a lowercase status key, or None for Not Applicable days
    (before join/feature-start, today mid-day, or future)."""
    st = services.resolve_day_status(
        record=rec,
        is_holiday_day=(d.weekday() == 5 or d in holiday_dates),
        is_leave_day=(d in leave_dates),
        d=d,
        floor=floor,
        today=today,
    )
    if st is None:
        return None
    # st may be an Attendance.Status member (Holiday/On-Leave/Absent) or a plain
    # stored status string (record.status). Normalise to the lowercase key.
    return getattr(st, "value", st)


def _fmt_time(dt):
    return timezone.localtime(dt).strftime("%H:%M") if dt else "—"


def build_employee_report(emp, start, end, *, holiday_dates=None, leave_dates=None, records=None):
    today = timezone.localtime(timezone.now()).date()
    if holiday_dates is None:
        holiday_dates = set(Holiday.objects.filter(
            is_active=True, date__gte=start, date__lte=end).values_list("date", flat=True))
    if records is None:
        records = {a.date: a for a in Attendance.objects.filter(
            employee=emp, date__gte=start, date__lte=end)}
    if leave_dates is None:
        leave_dates = _leave_dates(emp.id, Leave.objects.filter(
            user=emp, status=Leave.Status.APPROVED, is_deleted=False,
            start_date__lte=end, end_date__gte=start), start, end)

    floor = services.absent_floor(emp)
    rows, summary = [], {k: 0 for k in STATUS_KEYS}
    working_days = 0
    for d in _daterange(start, end):
        rec = records.get(d)
        status = _status_for(rec, d, holiday_dates, leave_dates, today, floor)
        display = status or "upcoming"
        if status in summary:
            summary[status] += 1
        # Working days exclude Saturdays, holidays, and Not-Applicable days
        # before the employee's tracking floor (never counted anywhere).
        if d.weekday() != 5 and d not in holiday_dates and (floor is None or d >= floor):
            working_days += 1
        rows.append({
            "date_ad": d.strftime("%Y-%m-%d"),
            "date_bs": to_bs(d),
            "day": DAY_NAME[d.weekday()],
            "is_saturday": d.weekday() == 5,
            "check_in": _fmt_time(rec.check_in) if rec else "—",
            "check_out": _fmt_time(rec.check_out) if rec else "—",
            "working_hours": str(rec.working_hours) if rec else "0.00",
            "status": display,
            "status_label": STATUS_LABEL.get(display, display),
            "remarks": (rec.remarks if rec else "") or "",
        })
    summary["working_days"] = working_days
    return {
        "employee": {
            "name": emp.get_full_name() or emp.username,
            "employee_id": emp.employee_id or "—",
            "department": emp.department_name or "—",
            "designation": emp.designation or "—",
            "role": emp.get_role_display(),
        },
        "period": {
            "start_ad": start.strftime("%Y-%m-%d"), "start_bs": to_bs(start),
            "end_ad": end.strftime("%Y-%m-%d"), "end_bs": to_bs(end),
        },
        "rows": rows,
        "summary": summary,
    }


def build_bulk(employees, start, end):
    """One combined dataset for many employees — 3 queries total (no N+1)."""
    employees = list(employees)
    ids = [e.id for e in employees]
    holiday_dates = set(Holiday.objects.filter(
        is_active=True, date__gte=start, date__lte=end).values_list("date", flat=True))

    att_by_emp = defaultdict(dict)
    for a in Attendance.objects.filter(employee_id__in=ids, date__gte=start, date__lte=end):
        att_by_emp[a.employee_id][a.date] = a

    leave_by_emp = defaultdict(list)
    for lv in Leave.objects.filter(
            user_id__in=ids, status=Leave.Status.APPROVED, is_deleted=False,
            start_date__lte=end, end_date__gte=start):
        leave_by_emp[lv.user_id].append(lv)

    reports, org_totals = [], {k: 0 for k in STATUS_KEYS}
    org_totals["working_days"] = 0
    dept_totals = defaultdict(lambda: {k: 0 for k in STATUS_KEYS})
    for emp in employees:
        r = build_employee_report(
            emp, start, end,
            holiday_dates=holiday_dates,
            leave_dates=_leave_dates(emp.id, leave_by_emp.get(emp.id, []), start, end),
            records=att_by_emp.get(emp.id, {}),
        )
        reports.append(r)
        for k in STATUS_KEYS:
            org_totals[k] += r["summary"][k]
            dept_totals[r["employee"]["department"]][k] += r["summary"][k]
    org_totals["working_days"] = reports[0]["summary"]["working_days"] if reports else 0
    dept_breakdown = [{"department": k, **v} for k, v in sorted(dept_totals.items())]
    return {
        "reports": reports,
        "org_totals": org_totals,
        "dept_breakdown": dept_breakdown,
        "employee_count": len(reports),
        "period": {
            "start_ad": start.strftime("%Y-%m-%d"), "start_bs": to_bs(start),
            "end_ad": end.strftime("%Y-%m-%d"), "end_bs": to_bs(end),
        },
    }
