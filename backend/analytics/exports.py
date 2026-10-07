"""The five analytics exports.

Each builder returns ``(bytes, filename, content_type)`` -- the contract
``reports.report_service`` has used since Phase 8 -- so all five plug straight
into the existing machinery: the async worker, the signed download URL, audit
logging, the retention purge and ``ScheduledReport`` email delivery all work
untouched. No new file pipeline exists in this phase.

Every builder calls the SAME ``metrics`` function as the dashboard it exports.
That is the point: a PDF handed to a director in a meeting and the screen it was
generated from cannot disagree, because there is one implementation of each
formula and both call it.
"""
import csv
import io

from django.utils import timezone

from reports.report_service import (
    EXCEL_CT,
    PDF_CT,
    _bars,
    _new_workbook,
    _render_pdf,
    _workbook_bytes,
    _write_sheet,
)

from . import calendar as work_calendar, periods, scope as scoping
from .metrics import attendance as attendance_metrics
from .metrics import comp_off as comp_off_metrics
from .metrics import departments as department_metrics
from .metrics import leave as leave_metrics
from .metrics import wfh as wfh_metrics

CSV_CT = "text/csv"


# ---------------------------------------------------------------------------
# shared plumbing
# ---------------------------------------------------------------------------
def _context(params, default_preset="last_12m", granularity=None):
    """Turn ``ReportRun.params`` into the (scope, window, calendar) trio.

    ``employee_ids`` is what makes a department head's export department-scoped:
    the view injects it, and this rebuilds the same population the dashboard
    showed them.
    """
    today = timezone.localdate()
    request_params = dict(params or {})
    if granularity:
        request_params.setdefault("granularity", granularity)
    window = periods.parse_window(request_params, default_preset=default_preset,
                                  today=today)
    scope = scoping.scope_from_ids(params.get("employee_ids"),
                                   params.get("department"))
    return scope, window, work_calendar.build(window, today), today


def _stamp(window):
    return f"{window.start}_{window.end}"


def _csv_bytes(headers, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")  # BOM: Excel opens it correctly


def _fmt(value, suffix=""):
    """Render a metric for a document. ``None`` prints as an em dash.

    A blank cell reads as zero to anyone skimming a PDF, and the whole reason
    these metrics return ``None`` is that zero would be a lie.
    """
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:g}{suffix}"
    return f"{value}{suffix}"


def _kpi_table(pairs):
    return "".join(f"<div class='kpi'><b>{_fmt(value)}</b>{label}</div>"
                   for label, value in pairs)


def _table(headers, rows):
    head = "".join(f"<th>{header}</th>" for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
                   for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _scope_line(scope, window):
    where = (scope.departments[scope.own_department_id]
             if scope.own_department_id and scope.own_department_id in scope.departments
             else "Whole organisation")
    return (f"{window.start} to {window.end} · {where} · "
            f"{scope.headcount} employees · generated {timezone.localtime():%Y-%m-%d %H:%M}")


# ===========================================================================
# 1. Executive summary (PDF)
# ===========================================================================
def build_executive_summary(params):
    """The one-page picture: headline KPIs, the trend, and department health."""
    scope, window, calendar, today = _context(params, default_preset="mtd",
                                              granularity="month")
    summary = attendance_metrics.summary(scope, window, calendar)
    department_rows = department_metrics.rank(
        department_metrics.rows(scope, window, calendar))
    comp = comp_off_metrics.balances(scope)
    trend = attendance_metrics.trend(scope, window, calendar)

    kpis = _kpi_table([
        ("Headcount", scope.headcount),
        ("Attendance compliance", _fmt(summary["compliance_pct"], "%")),
        ("Present", _fmt(summary["present_pct"], "%")),
        ("Absent", _fmt(summary["absent_pct"], "%")),
        ("Late", _fmt(summary["late_pct"], "%")),
        ("Work from home", _fmt(summary["wfh_pct"], "%")),
        ("On leave", _fmt(summary["leave_pct"], "%")),
        ("Overtime hours", summary["overtime_hours"]),
        ("Comp days outstanding", comp["available"]),
    ])

    ranking = _table(
        ["Rank", "Department", "Headcount", "Compliance %", "Present %",
         "Late %", "Overtime hrs", "Health"],
        [[_fmt(row["rank"]), row["department"], _fmt(row["headcount"]),
          _fmt(row["compliance_pct"]), _fmt(row["present_pct"]),
          _fmt(row["late_pct"]), _fmt(row["overtime_hours"]),
          _fmt(row["health_score"])] for row in department_rows])

    trend_bars = _bars([(point["label"], point["compliance_pct"] or 0)
                        for point in trend], unit="%")

    weights = " · ".join(f"{name.replace('_', ' ')} {int(value * 100)}%"
                         for name, value in department_metrics.HEALTH_WEIGHTS.items())
    inner = (
        f"<h1>Executive Summary</h1>"
        f"<div class='sub'>{_scope_line(scope, window)}</div>"
        f"{kpis}"
        f"<h2>Attendance compliance trend</h2>{trend_bars}"
        f"<h2>Departments, ranked by attendance compliance</h2>{ranking}"
        f"<div class='sub'>Health score weights: {weights}. "
        f"Departments under {scoping.MIN_DEPARTMENT_SAMPLE} people are not ranked. "
        f"Compliance counts approved leave as compliant; an unexplained absence "
        f"is an expected working day with no attendance record and no leave.</div>")
    return _render_pdf(inner, "Executive Summary"), \
        f"executive_summary_{_stamp(window)}.pdf", PDF_CT


# ===========================================================================
# 2. Department analytics (PDF)
# ===========================================================================
def build_department_analytics(params):
    scope, window, calendar, today = _context(params, granularity="month")
    department_rows = department_metrics.rank(
        department_metrics.rows(scope, window, calendar))
    leave_split = {row["department"]: row for row in leave_metrics.by_department(scope, window)}
    wfh_split = {row["department"]: row for row in wfh_metrics.by_department(scope, window)}

    rows = []
    for row in department_rows:
        leave_row = leave_split.get(row["department"], {})
        wfh_row = wfh_split.get(row["department"], {})
        rows.append([
            _fmt(row["rank"]), row["department"], _fmt(row["headcount"]),
            _fmt(row["compliance_pct"]), _fmt(row["present_pct"]),
            _fmt(row["late_pct"]), _fmt(row["absent_pct"]),
            _fmt(leave_row.get("leave_days")), _fmt(wfh_row.get("wfh_days")),
            _fmt(row["overtime_hours"]), _fmt(row["overtime_per_capita"]),
            _fmt(row["health_score"]),
        ])

    bars = [(row["department"], row["compliance_pct"] or 0)
            for row in department_rows if not row["small_sample"]]
    average = department_metrics.org_average(department_rows)

    inner = (
        f"<h1>Department Analytics</h1>"
        f"<div class='sub'>{_scope_line(scope, window)}</div>"
        f"{_kpi_table([('Departments', len(department_rows)), ('Org compliance', _fmt(average, '%'))])}"
        f"<h2>Attendance compliance</h2>{_bars(bars, unit='%')}"
        f"<h2>Detail</h2>"
        f"{_table(['Rank', 'Department', 'Headcount', 'Compliance %', 'Present %', 'Late %', 'Absent %', 'Leave days', 'WFH days', 'Overtime hrs', 'OT/head', 'Health'], rows)}"
        f"<div class='sub'>Ranked on attendance compliance only. Individual "
        f"employees are never scored.</div>")
    return _render_pdf(inner, "Department Analytics"), \
        f"department_analytics_{_stamp(window)}.pdf", PDF_CT


# ===========================================================================
# 3. Attendance analytics (Excel)
# ===========================================================================
def build_attendance_analytics(params):
    """Four sheets: the KPI block, the trend, department rows, and comparisons."""
    scope, window, calendar, today = _context(params, granularity="month")
    summary = attendance_metrics.summary(scope, window, calendar)
    trend = attendance_metrics.trend(scope, window, calendar)
    department_rows = department_metrics.rank(
        department_metrics.rows(scope, window, calendar))
    comparisons = attendance_metrics.comparisons(scope, today)

    workbook = _new_workbook()
    _write_sheet(workbook, "Summary", ["Metric", "Value"], [
        ["Window", f"{window.start} to {window.end}"],
        ["Headcount", scope.headcount],
        ["Expected working days", summary["expected_days"]],
        ["Days attended", summary["attended_days"]],
        ["Approved leave days", summary["leave_days"]],
        ["Unexplained absences", summary["unexplained_days"]],
        ["Attendance compliance %", _fmt(summary["compliance_pct"])],
        ["Present %", _fmt(summary["present_pct"])],
        ["Absent %", _fmt(summary["absent_pct"])],
        ["Late %", _fmt(summary["late_pct"])],
        ["Half day %", _fmt(summary["half_day_pct"])],
        ["WFH %", _fmt(summary["wfh_pct"])],
        ["Leave %", _fmt(summary["leave_pct"])],
        ["Overtime hours", summary["overtime_hours"]],
        ["Worked hours", summary["worked_hours"]],
        ["Average hours per attended day", _fmt(summary["avg_working_hours"])],
    ])

    _write_sheet(workbook, "Trend",
                 ["Period", "Expected days", "Attended", "Leave days",
                  "Compliance %", "Present %", "Late %", "Absent %", "WFH %",
                  "Overtime hrs"],
                 [[point["label"], point["expected_days"], point["attended_days"],
                   point["leave_days"], _fmt(point["compliance_pct"]),
                   _fmt(point["present_pct"]), _fmt(point["late_pct"]),
                   _fmt(point["absent_pct"]), _fmt(point["wfh_pct"]),
                   point["overtime_hours"]] for point in trend])

    _write_sheet(workbook, "Departments",
                 ["Rank", "Department", "Headcount", "Expected days", "Attended",
                  "Compliance %", "Present %", "Late %", "Absent %",
                  "Overtime hrs", "Health"],
                 [[_fmt(row["rank"]), row["department"], row["headcount"],
                   row["expected_days"], row["attended_days"],
                   _fmt(row["compliance_pct"]), _fmt(row["present_pct"]),
                   _fmt(row["late_pct"]), _fmt(row["absent_pct"]),
                   row["overtime_hours"], _fmt(row["health_score"])]
                  for row in department_rows])

    comparison_rows = []
    for label, series in (("Monthly", comparisons["monthly"]),
                          ("Quarterly", comparisons["quarterly"]),
                          ("Yearly", comparisons["yearly"])):
        for point in series:
            comparison_rows.append([
                label, point["label"], point["expected_days"],
                _fmt(point["compliance_pct"]), _fmt(point["present_pct"]),
                _fmt(point["late_pct"]), _fmt(point["absent_pct"]),
                point["overtime_hours"]])
    _write_sheet(workbook, "Comparisons",
                 ["View", "Period", "Expected days", "Compliance %", "Present %",
                  "Late %", "Absent %", "Overtime hrs"], comparison_rows)

    return _workbook_bytes(workbook), \
        f"attendance_analytics_{_stamp(window)}.xlsx", EXCEL_CT


# ===========================================================================
# 4. Overtime analytics (Excel)
# ===========================================================================
def build_overtime_analytics(params):
    """Overtime by department and over time.

    Deliberately department-grained, never per employee: Phase 10 does not score
    individuals, and an overtime league table is the most obvious way to break
    that rule by accident. The Phase 9 ``overtime_summary`` report already
    provides the per-employee view for payroll.
    """
    scope, window, calendar, today = _context(params, granularity="month")
    summary = attendance_metrics.summary(scope, window, calendar)
    trend = attendance_metrics.trend(scope, window, calendar)
    department_rows = department_metrics.rank(
        department_metrics.rows(scope, window, calendar))
    from .metrics.kpis import overtime_overview

    overview = overtime_overview(scope, window, summary)

    workbook = _new_workbook()
    _write_sheet(workbook, "Summary", ["Metric", "Value"], [
        ["Window", f"{window.start} to {window.end}"],
        ["Headcount", scope.headcount],
        ["Total overtime hours", overview["total_hours"]],
        ["Overtime hours per head", _fmt(overview["per_capita"])],
        ["Days with overtime", overview["days_with_overtime"]],
        ["Employees with any overtime", overview["employees_with_overtime"]],
        ["Share of employees with overtime %", _fmt(overview["employees_pct"])],
    ])
    _write_sheet(workbook, "By department",
                 ["Department", "Headcount", "Overtime hrs", "OT hrs per head",
                  "Days with OT", "Worked hrs", "Regular hrs"],
                 [[row["department"], row["headcount"], row["overtime_hours"],
                   _fmt(row["overtime_per_capita"]), row["overtime_days"],
                   row["worked_hours"], row["regular_hours"]]
                  for row in sorted(department_rows,
                                    key=lambda row: row["overtime_hours"], reverse=True)])
    _write_sheet(workbook, "Trend", ["Period", "Overtime hrs", "Worked hrs"],
                 [[point["label"], point["overtime_hours"], point["worked_hours"]]
                  for point in trend])

    return _workbook_bytes(workbook), \
        f"overtime_analytics_{_stamp(window)}.xlsx", EXCEL_CT


# ===========================================================================
# 5. Workforce analytics (CSV)
# ===========================================================================
def build_workforce_analytics(params):
    """One flat department-grained table -- the format people paste into a model."""
    scope, window, calendar, today = _context(params, granularity="month")
    department_rows = department_metrics.rank(
        department_metrics.rows(scope, window, calendar))
    leave_split = {row["department"]: row for row in leave_metrics.by_department(scope, window)}
    wfh_split = {row["department"]: row for row in wfh_metrics.by_department(scope, window)}
    comp_split = {row["department"]: row for row in comp_off_metrics.by_department(scope)}

    headers = ["Window start", "Window end", "Department", "Headcount", "Rank",
               "Expected working days", "Days attended", "Leave days",
               "Unexplained absences", "Compliance %", "Present %", "Absent %",
               "Late %", "Half day %", "WFH %", "Leave %", "Overtime hours",
               "Overtime per head", "Worked hours", "Regular hours",
               "WFH days", "Comp earned", "Comp used", "Comp available",
               "Health score", "Small sample"]
    rows = []
    for row in department_rows:
        leave_row = leave_split.get(row["department"], {})
        wfh_row = wfh_split.get(row["department"], {})
        comp_row = comp_split.get(row["department"], {})
        rows.append([
            window.start, window.end, row["department"], row["headcount"],
            row["rank"] if row["rank"] is not None else "",
            row["expected_days"], row["attended_days"], row["leave_days"],
            row["unexplained_days"], _csv_num(row["compliance_pct"]),
            _csv_num(row["present_pct"]), _csv_num(row["absent_pct"]),
            _csv_num(row["late_pct"]), _csv_num(row["half_day_pct"]),
            _csv_num(row["wfh_pct"]), _csv_num(leave_row.get("share_pct")),
            row["overtime_hours"], _csv_num(row["overtime_per_capita"]),
            row["worked_hours"], row["regular_hours"],
            wfh_row.get("wfh_days", 0), comp_row.get("earned", 0),
            comp_row.get("used", 0), comp_row.get("available", 0),
            _csv_num(row["health_score"]), "yes" if row["small_sample"] else "no",
        ])
    return _csv_bytes(headers, rows), \
        f"workforce_analytics_{_stamp(window)}.csv", CSV_CT


def _csv_num(value):
    """Empty cell for a missing metric. A spreadsheet reads "" as blank and 0 as
    zero; conflating them is how a null attendance rate becomes a real one."""
    return "" if value is None else value


BUILDERS = {
    "executive_summary": build_executive_summary,
    "department_analytics": build_department_analytics,
    "attendance_analytics": build_attendance_analytics,
    "overtime_analytics": build_overtime_analytics,
    "workforce_analytics": build_workforce_analytics,
}
