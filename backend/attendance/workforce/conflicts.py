"""Approved leave that collides with recorded attendance.

The conflict that matters is a real double-charge, not a cosmetic clash.
``resolve_day_status`` returns the stored status whenever a record has a
``check_in``, so a day the employee actually worked renders **Present** — while
the ``LeaveDayRecord`` for that same day still counts toward ``used_so_far`` in
``category_engine``. The employee works the day *and* loses the leave day, and
nothing in the system currently notices.

Half-day leave beside a half-day of attendance is NOT a conflict: that is the
supported arrangement, and reporting it would bury the real ones.

Detection only. No leave is refunded, no balance is touched, no attendance is
altered — resolution stays an explicit human decision, and touching
``LeaveDayRecord`` would mean changing the leave workflow this phase must not
touch.
"""
from datetime import timedelta

from django.utils import timezone

from ..models import Attendance

# A leave day is only in conflict when the employee was credited with a whole
# day of work against a whole day of leave.
FULL_DAY = "full"


def _leave_day_rows(emp_ids, start, end):
    from leaves.models import LeaveDayRecord

    # LeaveDayRecord carries `user` directly and names its parent
    # `leave_request` — no join through Leave is needed to scope by employee.
    return (LeaveDayRecord.objects
            .filter(user_id__in=emp_ids,
                    leave_request__is_deleted=False,
                    status=LeaveDayRecord.Status.APPROVED,
                    date__gte=start, date__lte=end)
            .select_related("user", "user__department_ref", "leave_request",
                            "leave_type")
            .order_by("date"))


def detect(emp_ids, start, end):
    """Every (employee, date) where approved full-day leave meets real attendance.

    Two queries regardless of range size: the approved leave days, and the
    attendance rows that could collide with them.
    """
    leave_rows = list(_leave_day_rows(emp_ids, start, end))
    if not leave_rows:
        return []

    candidate_days = {row.date for row in leave_rows}
    candidate_users = {row.user_id for row in leave_rows}
    attendance = {
        (a.employee_id, a.date): a
        for a in Attendance.objects.filter(
            employee_id__in=candidate_users, date__in=candidate_days,
            check_in__isnull=False,
        ).select_related("employee")
    }

    conflicts = []
    for row in leave_rows:
        record = attendance.get((row.user_id, row.date))
        if record is None:
            continue
        if row.day_portion != FULL_DAY:
            # Half-day leave + attendance is the supported arrangement.
            continue
        conflicts.append({
            "employee_id": str(row.user_id),
            "employee_name": row.user.get_full_name(),
            "employee_code": row.user.employee_id,
            "department": (row.user.department_ref.name
                           if row.user.department_ref else None),
            "date": row.date.isoformat(),
            "leave_id": str(row.leave_request_id),
            "leave_type": row.leave_type.code,
            "day_portion": row.day_portion,
            "attendance_status": record.status,
            "check_in": record.check_in.isoformat(),
            "check_out": record.check_out.isoformat() if record.check_out else None,
            "working_hours": str(record.working_hours),
            "attendance_source": record.source,
            # Why it matters, stated on the row so a report reads on its own.
            "impact": ("Recorded as worked and deducted from the leave balance "
                       "for the same day."),
        })
    return conflicts


def warnings_for_request(employee, start, end, day_portion=FULL_DAY):
    """Advisory warnings for a leave request that has not been submitted yet.

    Deliberately a separate read-only call rather than a hook inside
    ``LeaveViewSet.perform_create``: that method holds row locks inside an
    atomic block while the category engine validates balances, and adding a
    query there would put the concurrency guard at risk for a message. Nothing
    here can block a submission.
    """
    out = []
    if start is None or end is None or end < start:
        return out

    records = {
        a.date: a for a in Attendance.objects.filter(
            employee=employee, date__gte=start, date__lte=end,
            check_in__isnull=False)
    }
    from ..models import WFHRequest

    wfh_days = set()
    for wfh in WFHRequest.objects.filter(
            user=employee, status=WFHRequest.Status.APPROVED,
            start_date__lte=end, end_date__gte=start):
        day = max(wfh.start_date, start)
        while day <= min(wfh.end_date, end):
            wfh_days.add(day)
            day += timedelta(days=1)

    today = timezone.localdate()
    for date, record in sorted(records.items()):
        if day_portion != FULL_DAY:
            continue  # half-day leave beside attendance is supported
        out.append({
            "date": date.isoformat(),
            "type": "attendance_exists",
            "severity": "warning",
            "message": (f"You were recorded {record.get_status_display()} on "
                        f"{date} (in at "
                        f"{timezone.localtime(record.check_in):%H:%M}). "
                        f"Applying for full-day leave will deduct a leave day "
                        f"for a day you worked."),
        })
    for date in sorted(wfh_days):
        out.append({
            "date": date.isoformat(),
            "type": "wfh_approved",
            "severity": "info",
            "message": f"You have approved work-from-home on {date}.",
        })
    if start < today:
        out.append({
            "date": start.isoformat(),
            "type": "backdated",
            "severity": "info",
            "message": "This request covers days that have already passed.",
        })
    return out


def summarise(conflicts):
    """Headline figures for the conflict report."""
    by_employee = {}
    for conflict in conflicts:
        by_employee.setdefault(conflict["employee_id"], 0)
        by_employee[conflict["employee_id"]] += 1
    return {
        "total": len(conflicts),
        "employees_affected": len(by_employee),
        "leave_days_double_counted": len(conflicts),
    }
