"""The eight workforce reports (Phase 9).

Every builder returns ``(bytes, filename, content_type)`` — the same contract
``report_service`` already uses — so these plug straight into the existing
``ReportRun`` machinery: the async worker, the download endpoint, the retention
job and ``ScheduledReport`` email delivery all work unchanged.

Three formats. Excel and PDF reuse ``report_service``'s helpers rather than
re-implementing styling; CSV is added here because a workforce report is the
kind of thing people paste into payroll.

None of these recompute status. Every figure reads ``Attendance.status``, which
already carries the NIF arrival rules, so a report can never disagree with the
dashboard it was exported from.
"""
import csv
import io
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from attendance.models import Attendance, EmployeeShift, Shift, WFHRequest
from leaves.models import CompensatoryLedger, Department, Leave, LeaveDayRecord
from users.models import User

from .report_service import (
    EXCEL_CT,
    PDF_CT,
    _bars,
    _employee_label,
    _new_workbook,
    _render_pdf,
    _workbook_bytes,
    _write_sheet,
)

CSV_CT = "text/csv"
ZERO = Decimal("0.00")


def _hours(value):
    """Render an hours total as a fixed 2dp string.

    Not cosmetic. ``Sum()`` over a DecimalField returns a value whose exponent
    depends on the database backend: PostgreSQL preserves the column's scale and
    yields ``Decimal('2.50')``, SQLite goes through a float and yields
    ``Decimal('2.5')``. Formatting straight off that made the same report render
    differently in production and in development, which is exactly the kind of
    difference that hides a real discrepancy during UAT.
    """
    return str((value or ZERO).quantize(Decimal("0.01")))


# ---------------------------------------------------------------------------
# shared plumbing
# ---------------------------------------------------------------------------
def _window(params):
    """`from`/`to`, defaulting to the current month."""
    today = timezone.localdate()
    try:
        end = date.fromisoformat(params["to"]) if params.get("to") else today
    except (ValueError, TypeError):
        end = today
    try:
        start = (date.fromisoformat(params["from"]) if params.get("from")
                 else end.replace(day=1))
    except (ValueError, TypeError):
        start = end.replace(day=1)
    if end < start:
        start, end = end, start
    return start, end


def _employees(params):
    """Report scope. ``department`` narrows it; ``employee_ids`` pins it exactly.

    ``employee_ids`` is how a department head gets a department-scoped report:
    the view resolves who they may see and passes the list in, so the builder
    itself never needs to know about roles.
    """
    qs = User.objects.filter(is_active=True).select_related("department_ref")
    ids = params.get("employee_ids")
    if ids:
        qs = qs.filter(pk__in=ids)
    dept = params.get("department")
    if dept:
        qs = qs.filter(Q(department_ref__code__iexact=dept)
                       | Q(department_ref__id=dept) if _looks_like_uuid(dept)
                       else Q(department_ref__code__iexact=dept))
    return qs.order_by("first_name", "last_name")


def _looks_like_uuid(value):
    return isinstance(value, str) and len(value) == 36 and value.count("-") == 4


def _csv_bytes(headers, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")  # BOM: Excel opens it correctly


def _dept_name(user):
    return user.department_ref.name if user.department_ref else "Unassigned"


def _emit(params, *, slug, title, headers, rows, kpis=None, bars=None, subtitle=""):
    """One place that turns a header/row table into Excel, PDF or CSV."""
    fmt = (params.get("format") or "excel").lower()
    start, end = _window(params)
    stamp = f"{start}_{end}"

    if fmt == "csv":
        return _csv_bytes(headers, rows), f"{slug}_{stamp}.csv", CSV_CT

    if fmt == "pdf":
        head = "".join(f"<th>{h}</th>" for h in headers)
        body = "".join(
            "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
            for row in rows)
        kpi_html = "".join(
            f"<div class='kpi'><b>{value}</b>{label}</div>"
            for label, value in (kpis or []))
        bar_html = _bars(bars) if bars else ""
        inner = (f"<h1>{title}</h1>"
                 f"<div class='sub'>{subtitle or f'{start} to {end}'}</div>"
                 f"{kpi_html}"
                 f"{'<h2>Distribution</h2>' + bar_html if bar_html else ''}"
                 f"<h2>Detail</h2>"
                 f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
        return _render_pdf(inner, title), f"{slug}_{stamp}.pdf", PDF_CT

    workbook = _new_workbook()
    _write_sheet(workbook, title[:31], headers, rows)
    return _workbook_bytes(workbook), f"{slug}_{stamp}.xlsx", EXCEL_CT


def _attendance_map(emp_ids, start, end):
    rows = Attendance.objects.filter(
        employee_id__in=emp_ids, date__gte=start, date__lte=end)
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.employee_id].append(row)
    return grouped


# ===========================================================================
# 1. Attendance vs Leave
# ===========================================================================
def build_attendance_vs_leave(params):
    """Days worked against days on leave, and the overlap between them.

    The overlap column is the conflict from ``workforce.conflicts``: a full day
    of approved leave on a day the employee was recorded working, which charges
    a leave day for a day they were present.
    """
    start, end = _window(params)
    employees = list(_employees(params))
    emp_ids = [e.pk for e in employees]

    attendance = _attendance_map(emp_ids, start, end)
    leave_days = defaultdict(set)
    full_day_leave = defaultdict(set)
    for row in LeaveDayRecord.objects.filter(
            user_id__in=emp_ids, leave_request__is_deleted=False,
            status=LeaveDayRecord.Status.APPROVED,
            date__gte=start, date__lte=end).only("user_id", "date", "day_portion"):
        leave_days[row.user_id].add(row.date)
        if row.day_portion == "full":
            full_day_leave[row.user_id].add(row.date)

    headers = ["Employee", "Employee ID", "Department", "Present", "Late",
               "Half Day", "WFH", "Absent", "Leave Days", "Overlap (conflict)"]
    rows, total_conflicts = [], 0
    for employee in employees:
        records = attendance.get(employee.pk, [])
        worked = {r.date for r in records if r.check_in}
        overlap = worked & full_day_leave.get(employee.pk, set())
        total_conflicts += len(overlap)
        counts = defaultdict(int)
        for record in records:
            counts[record.status] += 1
        rows.append([
            _employee_label(employee), employee.employee_id or "—", _dept_name(employee),
            counts[Attendance.Status.PRESENT], counts[Attendance.Status.LATE],
            counts[Attendance.Status.HALF_DAY],
            counts[Attendance.Status.WORK_FROM_HOME],
            counts[Attendance.Status.ABSENT],
            len(leave_days.get(employee.pk, set())), len(overlap),
        ])

    return _emit(params, slug="attendance_vs_leave", title="Attendance vs Leave",
                 headers=headers, rows=rows,
                 kpis=[("Employees", len(employees)),
                       ("Leave days", sum(len(v) for v in leave_days.values())),
                       ("Conflicts", total_conflicts)])


# ===========================================================================
# 2. Attendance vs WFH
# ===========================================================================
def build_attendance_vs_wfh(params):
    """Approved WFH against WFH actually worked.

    An approved day with no check-in is the interesting row: the request was
    granted but no work was recorded, which is exactly the gap the Phase 8 rule
    (approval alone is not attendance) was designed to make visible.
    """
    start, end = _window(params)
    employees = list(_employees(params))
    emp_ids = [e.pk for e in employees]

    approved = defaultdict(set)
    for wfh in WFHRequest.objects.filter(
            user_id__in=emp_ids, status=WFHRequest.Status.APPROVED,
            start_date__lte=end, end_date__gte=start):
        day = max(wfh.start_date, start)
        while day <= min(wfh.end_date, end):
            approved[wfh.user_id].add(day)
            day += timedelta(days=1)

    attendance = _attendance_map(emp_ids, start, end)
    headers = ["Employee", "Employee ID", "Department", "Approved WFH Days",
               "Worked From Home", "Approved But Not Worked", "In Office While Approved"]
    rows, bars = [], []
    for employee in employees:
        granted = approved.get(employee.pk, set())
        if not granted:
            continue
        records = {r.date: r for r in attendance.get(employee.pk, [])}
        worked = {d for d in granted
                  if records.get(d) and records[d].status == Attendance.Status.WORK_FROM_HOME}
        in_office = {d for d in granted
                     if records.get(d) and records[d].source == Attendance.Source.BIOMETRIC}
        rows.append([
            _employee_label(employee), employee.employee_id or "—", _dept_name(employee),
            len(granted), len(worked), len(granted - worked), len(in_office),
        ])
        bars.append((_employee_label(employee), len(worked)))

    return _emit(params, slug="attendance_vs_wfh", title="Attendance vs WFH",
                 headers=headers, rows=rows, bars=bars[:15],
                 kpis=[("Employees with WFH", len(rows)),
                       ("Approved days", sum(len(v) for v in approved.values()))])


# ===========================================================================
# 3. Overtime summary
# ===========================================================================
def build_overtime_summary(params):
    start, end = _window(params)
    employees = list(_employees(params))
    emp_ids = [e.pk for e in employees]

    totals = {row["employee_id"]: row for row in Attendance.objects.filter(
        employee_id__in=emp_ids, date__gte=start, date__lte=end,
    ).values("employee_id").annotate(
        overtime=Sum("overtime_hours"), regular=Sum("regular_hours"),
        worked=Sum("working_hours"),
        ot_days=Count("id", filter=Q(overtime_hours__gt=0)))}

    headers = ["Employee", "Employee ID", "Department", "Days With Overtime",
               "Overtime Hours", "Regular Hours", "Total Worked Hours"]
    rows, bars, grand = [], [], ZERO
    for employee in employees:
        row = totals.get(employee.pk)
        if not row or not row["overtime"]:
            continue
        grand += row["overtime"] or ZERO
        rows.append([
            _employee_label(employee), employee.employee_id or "—", _dept_name(employee),
            row["ot_days"], _hours(row["overtime"]),
            _hours(row["regular"]), _hours(row["worked"]),
        ])
        bars.append((_employee_label(employee), float(row["overtime"] or 0)))

    rows.sort(key=lambda r: Decimal(r[4]), reverse=True)
    bars.sort(key=lambda b: b[1], reverse=True)
    return _emit(params, slug="overtime_summary", title="Overtime Summary",
                 headers=headers, rows=rows, bars=bars[:15],
                 kpis=[("Employees", len(rows)), ("Overtime hours", _hours(grand))])


# ===========================================================================
# 4. Late arrival summary
# ===========================================================================
def build_late_arrival_summary(params):
    """Under the NIF rules a day is Late only after 11:45, so this is a genuine
    exception report rather than a list of everyone who missed 10:00."""
    start, end = _window(params)
    employees = list(_employees(params))
    emp_ids = [e.pk for e in employees]

    totals = {row["employee_id"]: row for row in Attendance.objects.filter(
        employee_id__in=emp_ids, date__gte=start, date__lte=end,
    ).values("employee_id").annotate(
        late_days=Count("id", filter=Q(status=Attendance.Status.LATE)),
        half_days=Count("id", filter=Q(status=Attendance.Status.HALF_DAY)),
        # Aliases must not shadow the column they aggregate, or Django reads
        # the annotation back as its own input and raises FieldError.
        total_late=Sum("late_minutes"),
        average_late=Avg("late_minutes"))}

    headers = ["Employee", "Employee ID", "Department", "Late Days",
               "Half Days (late arrival)", "Total Late Minutes", "Average Late Minutes"]
    rows, bars = [], []
    for employee in employees:
        row = totals.get(employee.pk)
        if not row or not row["late_days"]:
            continue
        rows.append([
            _employee_label(employee), employee.employee_id or "—", _dept_name(employee),
            row["late_days"], row["half_days"], row["total_late"] or 0,
            round(row["average_late"] or 0, 1),
        ])
        bars.append((_employee_label(employee), row["late_days"]))

    rows.sort(key=lambda r: r[3], reverse=True)
    bars.sort(key=lambda b: b[1], reverse=True)
    return _emit(params, slug="late_arrival_summary", title="Late Arrival Summary",
                 headers=headers, rows=rows, bars=bars[:15],
                 subtitle=f"{start} to {end} · Late = check-in after 11:45",
                 kpis=[("Employees late", len(rows)),
                       ("Late days", sum(r[3] for r in rows))])


# ===========================================================================
# 5. Shift utilization
# ===========================================================================
def build_shift_utilization(params):
    start, end = _window(params)
    employees = list(_employees(params))
    emp_ids = [e.pk for e in employees]

    assigned = defaultdict(int)
    for row in EmployeeShift.objects.filter(user_id__in=emp_ids).select_related("shift"):
        assigned[row.shift.name] += 1

    worked = defaultdict(lambda: {"days": 0, "hours": ZERO, "late": 0})
    for record in Attendance.objects.filter(
            employee_id__in=emp_ids, date__gte=start, date__lte=end,
            check_in__isnull=False).select_related("applied_shift"):
        name = record.applied_shift.name if record.applied_shift else "No shift (policy default)"
        worked[name]["days"] += 1
        worked[name]["hours"] += record.working_hours or ZERO
        worked[name]["late"] += 1 if record.status == Attendance.Status.LATE else 0

    headers = ["Shift", "Employees Assigned", "Days Worked", "Hours Worked",
               "Late Days", "Avg Hours/Day"]
    rows, bars = [], []
    for name in sorted(set(assigned) | set(worked)):
        stats = worked.get(name, {"days": 0, "hours": ZERO, "late": 0})
        average = (stats["hours"] / stats["days"]).quantize(Decimal("0.01")) \
            if stats["days"] else ZERO
        rows.append([name, assigned.get(name, 0), stats["days"], _hours(stats["hours"]),
                     stats["late"], str(average)])
        bars.append((name, stats["days"]))

    return _emit(params, slug="shift_utilization", title="Shift Utilization",
                 headers=headers, rows=rows, bars=bars, subtitle=f"{start} to {end}",
                 kpis=[("Shifts defined", Shift.objects.filter(is_active=True).count()),
                       ("Days worked", sum(r[2] for r in rows))])


# ===========================================================================
# 6. Department attendance
# ===========================================================================
def build_department_attendance(params):
    """Department attendance, on the Phase 10 denominator.

    This report used to divide by ``present + half + absent`` ATTENDANCE ROWS.
    Because an absent day usually has no stored row -- rows exist for a real
    check-in or an HR entry, and absence is derived at read time -- that
    denominator was smaller than reality and the percentage read high.

    Phase 10 defines the honest denominator (``analytics.calendar``: working days
    that are not Saturday, not a holiday, on or after the employee joined, and
    not in the future) and this builder now uses it, so the report and the
    analytics dashboards can never quote different attendance rates. The column
    names are unchanged; "Absent" now means expected working days covered by
    neither attendance nor approved leave, which is what a reader always assumed
    it meant.
    """
    from analytics import calendar as work_calendar, periods, scope as scoping
    from analytics.metrics import departments as department_metrics

    start, end = _window(params)
    window = periods.Window(start=start, end=end, granularity="month")
    scope = scoping.scope_from_ids(params.get("employee_ids"),
                                   params.get("department"))
    rows_data = department_metrics.rank(
        department_metrics.rows(scope, window, work_calendar.build(window)))

    headers = ["Department", "Headcount", "Present", "Late", "Half Day", "WFH",
               "Absent", "On Leave", "Attendance %", "Compliance %"]
    rows, bars = [], []
    for row in sorted(rows_data, key=lambda item: item["department"]):
        attendance_pct = row["present_pct"] if row["present_pct"] is not None else 0.0
        rows.append([
            row["department"], row["headcount"], row["present_days"],
            row["late_days"], row["half_days"], row["wfh_days"],
            row["unexplained_days"], row["leave_days"], attendance_pct,
            row["compliance_pct"] if row["compliance_pct"] is not None else "—",
        ])
        bars.append((row["department"], attendance_pct))

    return _emit(params, slug="department_attendance", title="Department Attendance",
                 headers=headers, rows=rows, bars=bars,
                 subtitle=(f"{start} to {end} · Percentages are over expected "
                           f"working days (excluding Saturdays, holidays and "
                           f"pre-joining days), not over recorded rows"),
                 kpis=[("Departments", len(rows)),
                       ("Headcount", scope.headcount)])


# ===========================================================================
# 7. Comp-off summary
# ===========================================================================
def build_comp_off_report(params):
    start, end = _window(params)
    employees = list(_employees(params))
    emp_ids = [e.pk for e in employees]

    ledger = defaultdict(lambda: {"earned": ZERO, "pending": ZERO, "used": ZERO})
    for entry in CompensatoryLedger.objects.filter(user_id__in=emp_ids):
        bucket = ledger[entry.user_id]
        if entry.entry_type == CompensatoryLedger.EntryType.USE:
            bucket["used"] += entry.days
        elif entry.status == CompensatoryLedger.Status.CONFIRMED:
            bucket["earned"] += entry.days
        else:
            bucket["pending"] += entry.days

    earned_in_window = defaultdict(Decimal)
    for entry in CompensatoryLedger.objects.filter(
            user_id__in=emp_ids, entry_type=CompensatoryLedger.EntryType.EARN,
            source_date__gte=start, source_date__lte=end):
        earned_in_window[entry.user_id] += entry.days

    headers = ["Employee", "Employee ID", "Department", "Earned In Period",
               "Confirmed Total", "Pending Confirmation", "Used", "Available"]
    rows = []
    for employee in employees:
        bucket = ledger.get(employee.pk)
        if not bucket or not any(bucket.values()):
            continue
        rows.append([
            _employee_label(employee), employee.employee_id or "—", _dept_name(employee),
            str(earned_in_window.get(employee.pk, ZERO)), str(bucket["earned"]),
            str(bucket["pending"]), str(bucket["used"]),
            str(bucket["earned"] - bucket["used"]),
        ])

    total_pending = sum(b["pending"] for b in ledger.values())
    return _emit(params, slug="comp_off_report", title="Comp Off Summary",
                 headers=headers, rows=rows,
                 kpis=[("Employees", len(rows)),
                       ("Pending confirmation", str(total_pending))])


# ===========================================================================
# 8. Monthly workforce summary — the one-page picture
# ===========================================================================
def build_monthly_workforce_summary(params):
    start, end = _window(params)
    employees = list(_employees(params))
    emp_ids = [e.pk for e in employees]

    aggregate = Attendance.objects.filter(
        employee_id__in=emp_ids, date__gte=start, date__lte=end,
    ).aggregate(
        present=Count("id", filter=Q(status=Attendance.Status.PRESENT)),
        late=Count("id", filter=Q(status=Attendance.Status.LATE)),
        half=Count("id", filter=Q(status=Attendance.Status.HALF_DAY)),
        wfh=Count("id", filter=Q(status=Attendance.Status.WORK_FROM_HOME)),
        absent=Count("id", filter=Q(status=Attendance.Status.ABSENT)),
        worked=Sum("working_hours"), overtime=Sum("overtime_hours"),
        late_minutes=Sum("late_minutes"))

    leave_total = LeaveDayRecord.objects.filter(
        user_id__in=emp_ids, leave_request__is_deleted=False,
        status=LeaveDayRecord.Status.APPROVED,
        date__gte=start, date__lte=end).count()
    comp_pending = CompensatoryLedger.objects.filter(
        user_id__in=emp_ids, entry_type=CompensatoryLedger.EntryType.EARN,
        status=CompensatoryLedger.Status.PENDING).aggregate(d=Sum("days"))["d"] or ZERO
    wfh_requests = WFHRequest.objects.filter(
        user_id__in=emp_ids, start_date__lte=end, end_date__gte=start).count()
    pending_leave = Leave.objects.filter(
        user_id__in=emp_ids, is_deleted=False,
        status__in=[Leave.Status.PENDING, Leave.Status.PENDING_HR]).count()

    headers = ["Metric", "Value"]
    rows = [
        ["Headcount", len(employees)],
        ["Departments", Department.objects.filter(is_active=True).count()],
        ["Present days", aggregate["present"] or 0],
        ["Late days", aggregate["late"] or 0],
        ["Half days", aggregate["half"] or 0],
        ["Work-from-home days", aggregate["wfh"] or 0],
        ["Absent days", aggregate["absent"] or 0],
        ["Approved leave days", leave_total],
        ["Total hours worked", str(aggregate["worked"] or ZERO)],
        ["Overtime hours", str(aggregate["overtime"] or ZERO)],
        ["Total late minutes", aggregate["late_minutes"] or 0],
        ["WFH requests in period", wfh_requests],
        ["Comp days awaiting confirmation", str(comp_pending)],
        ["Leave applications awaiting a decision", pending_leave],
    ]
    bars = [("Present", aggregate["present"] or 0), ("Late", aggregate["late"] or 0),
            ("Half day", aggregate["half"] or 0), ("WFH", aggregate["wfh"] or 0),
            ("Absent", aggregate["absent"] or 0), ("On leave", leave_total)]

    return _emit(params, slug="monthly_workforce_summary",
                 title="Monthly Workforce Summary", headers=headers, rows=rows,
                 bars=bars, subtitle=f"{start} to {end} · {len(employees)} employees",
                 kpis=[("Headcount", len(employees)),
                       ("Overtime hrs", str(aggregate["overtime"] or ZERO)),
                       ("Absent days", aggregate["absent"] or 0)])


def build_department_ownership(params):
    """
    Who answers for each department (Phase DEPARTMENT-GOVERNANCE-HARDENING).

    NOT A DATE-WINDOWED REPORT. Every other report here answers "what happened
    between these dates"; this one answers "who is accountable, right now", and
    a window would imply the register had a history it does not keep. The window
    the wrapper stamps on the filename is left alone rather than being explained
    away - the file is a snapshot, and the date it was taken is the useful part.

    The rows come from leaves.governance, which is the same function the health
    board and the dashboard banner read, so the report cannot disagree with the
    warning that prompted somebody to run it.
    """
    from leaves.governance import governance_summary, ownership_rows

    rows = ownership_rows()
    summary = governance_summary()
    headers = ["Department", "Code", "Parent", "Department Head", "Email",
               "Head Status", "Members", "Active", "Governance"]
    table = [[r["department"], r["code"], r["parent"] or "—", r["head"] or "—",
              r["head_email"] or "—", r["head_status"], r["members"],
              "Yes" if r["is_active"] else "No", r["governance"]]
             for r in rows]
    kpis = [("Departments", summary["total"]),
            ("Active", summary["active"]),
            ("Missing a head", summary["missing_head"])]
    subtitle = ("Every active department has an active head."
                if not summary["missing_head"]
                else f"{summary['missing_head']} active department(s) have no "
                     f"Department Head assigned: "
                     f"{', '.join(summary['missing_head_names'])}. Leave routing "
                     "falls back to HR and department reporting has no owner; "
                     "task creation and assignment are unaffected.")
    return _emit(params, slug="department_ownership",
                 title="Department Ownership Report", headers=headers, rows=table,
                 kpis=kpis, subtitle=subtitle)


BUILDERS = {
    "attendance_vs_leave": build_attendance_vs_leave,
    "attendance_vs_wfh": build_attendance_vs_wfh,
    "overtime_summary": build_overtime_summary,
    "late_arrival_summary": build_late_arrival_summary,
    "shift_utilization": build_shift_utilization,
    "department_attendance": build_department_attendance,
    "comp_off_report": build_comp_off_report,
    "monthly_workforce_summary": build_monthly_workforce_summary,
    "department_ownership": build_department_ownership,
}
