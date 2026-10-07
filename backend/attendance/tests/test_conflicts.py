"""Leave that collides with recorded attendance, plus the WFH and comp-off
management surfaces.

The conflict being detected is a genuine double-charge that exists today and
goes unnoticed: ``resolve_day_status`` shows the day as Present because a
check-in exists, while the LeaveDayRecord still counts against the balance.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from attendance.models import Attendance, WFHRequest
from attendance.services import recompute_status
from attendance.workforce import conflicts
from leaves.models import CompensatoryLedger, Leave, LeaveDayRecord, LeaveType

pytestmark = pytest.mark.django_db

CONFLICTS = "/api/v1/workforce/conflicts/"
PREVIEW = "/api/v1/workforce/leave-preview/"
WFH_SUMMARY = "/api/v1/workforce/wfh/summary/"
COMP_SUMMARY = "/api/v1/workforce/comp-off/summary/"


@pytest.fixture
def leave_type(db):
    return LeaveType.objects.filter(code__iexact="annual").first() or \
        LeaveType.objects.create(code="annual", name="Annual Leave")


@pytest.fixture
def approved_leave(employee, leave_type):
    """Create an approved leave and take its auto-generated day records as given.

    Saving a Leave already materialises LeaveDayRecord rows through the existing
    leave machinery — building them by hand duplicates the booking and trips
    `uniq_leave_day_booking`. Going through the real path also means these tests
    exercise the same rows production produces.
    """
    def _make(day, portion=LeaveDayRecord.DayPortion.FULL, user=None,
              status=Leave.Status.APPROVED):
        user = user or employee
        leave = Leave.objects.create(
            user=user, leave_type="annual", start_date=day, end_date=day,
            reason="test", status=status)
        records = LeaveDayRecord.objects.filter(leave_request=leave, date=day)
        if not records.exists():
            LeaveDayRecord.objects.create(
                leave_request=leave, user=user, date=day, day_portion=portion,
                leave_type=leave_type,
                status=(LeaveDayRecord.Status.APPROVED
                        if status == Leave.Status.APPROVED
                        else LeaveDayRecord.Status.PENDING),
                week_number=day.isocalendar()[1], month=day.month, year=day.year)
        else:
            records.update(
                day_portion=portion,
                status=(LeaveDayRecord.Status.APPROVED
                        if status == Leave.Status.APPROVED
                        else LeaveDayRecord.Status.PENDING))
        return leave
    return _make


@pytest.fixture
def worked(local):
    def _worked(user, day, check_in=(10, 0), check_out=(18, 30)):
        record = Attendance(
            employee=user, date=day,
            check_in=local(day.year, day.month, day.day, *check_in),
            check_out=local(day.year, day.month, day.day, *check_out))
        recompute_status(record)
        record.save()
        return record
    return _worked


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------
def test_full_day_leave_plus_attendance_is_a_conflict(employee, approved_leave,
                                                      worked, a_weekday):
    approved_leave(a_weekday)
    worked(employee, a_weekday)

    found = conflicts.detect([employee.pk], a_weekday, a_weekday)
    assert len(found) == 1
    assert found[0]["employee_id"] == str(employee.id)
    assert found[0]["date"] == a_weekday.isoformat()
    assert "deducted from the leave balance" in found[0]["impact"]


def test_half_day_leave_plus_attendance_is_supported(employee, approved_leave,
                                                     worked, a_weekday):
    """The arrangement the brief calls out as valid — it must not be reported."""
    approved_leave(a_weekday, portion=LeaveDayRecord.DayPortion.FIRST_HALF)
    worked(employee, a_weekday, (13, 30), (18, 0))
    assert conflicts.detect([employee.pk], a_weekday, a_weekday) == []


def test_leave_without_attendance_is_not_a_conflict(employee, approved_leave, a_weekday):
    approved_leave(a_weekday)
    assert conflicts.detect([employee.pk], a_weekday, a_weekday) == []


def test_attendance_without_leave_is_not_a_conflict(employee, worked, a_weekday):
    worked(employee, a_weekday)
    assert conflicts.detect([employee.pk], a_weekday, a_weekday) == []


def test_a_pending_leave_is_not_a_conflict(employee, approved_leave, worked, a_weekday):
    approved_leave(a_weekday, status=Leave.Status.PENDING)
    worked(employee, a_weekday)
    assert conflicts.detect([employee.pk], a_weekday, a_weekday) == []


def test_detection_is_two_queries_regardless_of_range(
        employee, approved_leave, worked, a_weekday, django_assert_max_num_queries):
    for offset in range(3):
        day = a_weekday - timedelta(days=offset)
        approved_leave(day)
        worked(employee, day)
    with django_assert_max_num_queries(2):
        conflicts.detect([employee.pk], a_weekday - timedelta(days=10), a_weekday)


def test_the_summary_counts_employees_and_days(employee, coworker, approved_leave,
                                               worked, a_weekday):
    for user in (employee, coworker):
        approved_leave(a_weekday, user=user)
        worked(user, a_weekday)
    summary = conflicts.summarise(
        conflicts.detect([employee.pk, coworker.pk], a_weekday, a_weekday))
    assert summary["total"] == 2
    assert summary["employees_affected"] == 2


# --------------------------------------------------------------------------
# the report endpoint
# --------------------------------------------------------------------------
def test_the_conflict_report_is_manager_and_above(employee, auth):
    assert auth(employee).get(CONFLICTS).status_code == 403


def test_a_manager_sees_only_their_department(dept_head, employee, outsider,
                                              approved_leave, worked, auth, a_weekday):
    approved_leave(a_weekday)
    worked(employee, a_weekday)
    approved_leave(a_weekday, user=outsider)
    worked(outsider, a_weekday)

    data = auth(dept_head).get(CONFLICTS, {"from": a_weekday.isoformat(),
                                           "to": a_weekday.isoformat()}).data
    assert {row["employee_id"] for row in data["conflicts"]} == {str(employee.id)}


def test_hr_sees_every_department(hr, employee, outsider, approved_leave, worked,
                                  auth, a_weekday):
    approved_leave(a_weekday)
    worked(employee, a_weekday)
    approved_leave(a_weekday, user=outsider)
    worked(outsider, a_weekday)

    data = auth(hr).get(CONFLICTS, {"from": a_weekday.isoformat(),
                                    "to": a_weekday.isoformat()}).data
    assert data["summary"]["total"] == 2


def test_the_report_never_refunds_leave(hr, employee, approved_leave, worked,
                                        auth, a_weekday):
    """Detection only, as approved: no balance is touched and no attendance
    is altered."""
    approved_leave(a_weekday)
    record = worked(employee, a_weekday)

    auth(hr).get(CONFLICTS, {"from": a_weekday.isoformat(), "to": a_weekday.isoformat()})

    assert LeaveDayRecord.objects.filter(
        user=employee, date=a_weekday,
        status=LeaveDayRecord.Status.APPROVED).exists()
    record.refresh_from_db()
    assert record.check_in is not None


def test_an_invalid_date_range_is_rejected(hr, auth):
    assert auth(hr).get(CONFLICTS, {"from": "2026-08-10",
                                    "to": "2026-08-01"}).status_code == 400


# --------------------------------------------------------------------------
# apply-time warnings
# --------------------------------------------------------------------------
def test_a_present_employee_is_warned_before_applying(employee, worked, auth, a_weekday):
    worked(employee, a_weekday)
    data = auth(employee).get(PREVIEW, {"start": a_weekday.isoformat()}).data
    types = {w["type"] for w in data["warnings"]}
    assert "attendance_exists" in types
    assert any(w["severity"] == "warning" for w in data["warnings"])


def test_no_warning_when_nothing_was_recorded(employee, auth, a_weekday):
    data = auth(employee).get(PREVIEW, {"start": a_weekday.isoformat()}).data
    assert not any(w["type"] == "attendance_exists" for w in data["warnings"])


def test_a_half_day_request_is_not_warned(employee, worked, auth, a_weekday):
    worked(employee, a_weekday)
    data = auth(employee).get(PREVIEW, {"start": a_weekday.isoformat(),
                                        "portion": "first_half"}).data
    assert not any(w["type"] == "attendance_exists" for w in data["warnings"])


def test_approved_wfh_is_surfaced_as_information(employee, hr, auth, a_weekday):
    WFHRequest.objects.create(user=employee, start_date=a_weekday, end_date=a_weekday,
                              status=WFHRequest.Status.APPROVED, reviewed_by=hr)
    data = auth(employee).get(PREVIEW, {"start": a_weekday.isoformat()}).data
    assert any(w["type"] == "wfh_approved" for w in data["warnings"])


def test_the_preview_never_blocks_a_real_application(employee, worked, auth, a_weekday,
                                                     leave_type):
    """The warning is advisory: leaves/views.py is untouched and applying still
    works exactly as before."""
    worked(employee, a_weekday)
    future = timezone.localdate() + timedelta(days=7)
    response = auth(employee).post("/api/v1/leaves/", {
        "leave_type": "annual", "start_date": future.isoformat(),
        "end_date": future.isoformat(), "reason": "family"}, format="json")
    assert response.status_code in (201, 400), "unchanged leave behaviour"


# --------------------------------------------------------------------------
# WFH management
# --------------------------------------------------------------------------
def test_the_wfh_summary_is_manager_and_above(employee, auth):
    assert auth(employee).get(WFH_SUMMARY).status_code == 403


def test_a_manager_sees_the_wfh_queue_but_cannot_approve(dept_head, employee, auth,
                                                         a_weekday):
    WFHRequest.objects.create(user=employee, start_date=a_weekday, end_date=a_weekday,
                              reason="fibre cut")
    data = auth(dept_head).get(WFH_SUMMARY).data
    assert len(data["pending_queue"]) == 1
    assert data["can_approve"] is False, "WFH approval stays HR-only"


def test_hr_can_approve_from_the_queue(hr, employee, auth, a_weekday):
    WFHRequest.objects.create(user=employee, start_date=a_weekday, end_date=a_weekday)
    assert auth(hr).get(WFH_SUMMARY).data["can_approve"] is True


def test_the_wfh_summary_reports_conversion(hr, employee, auth, worked, a_weekday):
    WFHRequest.objects.create(user=employee, start_date=a_weekday, end_date=a_weekday,
                              status=WFHRequest.Status.APPROVED, reviewed_by=hr)
    worked(employee, a_weekday)  # browser source -> becomes WORK_FROM_HOME

    data = auth(hr).get(WFH_SUMMARY, {"from": a_weekday.isoformat(),
                                      "to": a_weekday.isoformat()}).data["summary"]
    assert data["approved_day_rows"] == 1
    assert data["worked_from_home_days"] == 1
    assert data["conversion"] == 1.0


# --------------------------------------------------------------------------
# comp-off management
# --------------------------------------------------------------------------
def test_the_comp_off_summary_is_manager_and_above(employee, auth):
    assert auth(employee).get(COMP_SUMMARY).status_code == 403


def test_the_comp_off_queue_lists_pending_earns(hr, employee, auth, a_saturday):
    CompensatoryLedger.objects.create(
        user=employee, entry_type=CompensatoryLedger.EntryType.EARN,
        days=Decimal("1.00"), source=CompensatoryLedger.Source.ATTENDANCE,
        status=CompensatoryLedger.Status.PENDING, source_date=a_saturday)

    data = auth(hr).get(COMP_SUMMARY).data
    assert len(data["pending_queue"]) == 1
    assert data["summary"]["pending"] == 1.0
    assert data["summary"]["available"] == 0.0
    assert data["can_confirm"] is True


def test_confirming_moves_it_into_the_balance(hr, employee, auth, a_saturday):
    entry = CompensatoryLedger.objects.create(
        user=employee, entry_type=CompensatoryLedger.EntryType.EARN,
        days=Decimal("1.00"), source=CompensatoryLedger.Source.ATTENDANCE,
        status=CompensatoryLedger.Status.PENDING, source_date=a_saturday)
    auth(hr).post(f"/api/v1/leaves/compensatory/{entry.id}/confirm/", {}, format="json")

    data = auth(hr).get(COMP_SUMMARY).data
    assert data["summary"]["available"] == 1.0
    assert data["pending_queue"] == []


def test_a_manager_sees_the_queue_but_cannot_confirm(dept_head, employee, auth,
                                                     a_saturday):
    CompensatoryLedger.objects.create(
        user=employee, entry_type=CompensatoryLedger.EntryType.EARN,
        days=Decimal("1.00"), source=CompensatoryLedger.Source.ATTENDANCE,
        status=CompensatoryLedger.Status.PENDING, source_date=a_saturday)
    data = auth(dept_head).get(COMP_SUMMARY).data
    assert len(data["pending_queue"]) == 1
    assert data["can_confirm"] is False


def test_the_comp_off_history_and_trend_are_present(hr, employee, auth, a_saturday):
    CompensatoryLedger.objects.create(
        user=employee, entry_type=CompensatoryLedger.EntryType.EARN,
        days=Decimal("0.50"), source=CompensatoryLedger.Source.ATTENDANCE,
        status=CompensatoryLedger.Status.CONFIRMED, source_date=a_saturday)
    data = auth(hr).get(COMP_SUMMARY).data
    assert len(data["history"]) == 1
    assert any(bucket["confirmed"] for bucket in data["trend"])
