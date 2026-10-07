"""Management commands: process_punches and rederive_attendance."""
from datetime import date, datetime
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from attendance.models import Attendance
from biometric.models import AttendancePunch

pytestmark = pytest.mark.django_db

DAY = date(2026, 8, 3)


@pytest.fixture
def punch(device, employee, mapping):
    def _make(hh, mm, code=0, day=DAY, user=employee):
        return AttendancePunch.objects.create(
            device=device, employee_device_id=mapping.device_user_id,
            biometric_employee=mapping, user=user,
            timestamp=timezone.make_aware(datetime(day.year, day.month, day.day, hh, mm)),
            punch=code,
        )
    return _make


def run(command, *args, **kwargs):
    out, err = StringIO(), StringIO()
    call_command(command, *args, stdout=out, stderr=err, **kwargs)
    return out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------
# process_punches
# --------------------------------------------------------------------------

def test_process_punches_derives_attendance(employee, punch):
    punch(9, 0, 0)
    punch(18, 0, 1)
    out, _ = run("process_punches")
    assert "1 derived" in out
    assert Attendance.objects.filter(employee=employee, date=DAY).exists()


def test_process_punches_is_safe_to_run_repeatedly(employee, punch):
    punch(9, 0, 0)
    for _ in range(10):
        run("process_punches")
    assert Attendance.objects.filter(employee=employee, date=DAY).count() == 1


def test_process_punches_reports_the_unmapped_backlog(device):
    AttendancePunch.objects.create(
        device=device, employee_device_id="99", user=None,
        timestamp=timezone.make_aware(datetime(2026, 8, 3, 9, 0)), punch=0)
    out, _ = run("process_punches")
    assert "waiting on an employee mapping" in out


def test_process_punches_quiet_is_silent_when_idle():
    out, _ = run("process_punches", quiet=True)
    assert out.strip() == ""


def test_process_punches_respects_limit(employee, punch):
    punch(9, 0, 0, day=date(2026, 8, 3))
    punch(9, 0, 0, day=date(2026, 8, 4))
    out, _ = run("process_punches", limit=1)
    assert "Processed 1 employee-day" in out
    assert Attendance.objects.count() == 1


# --------------------------------------------------------------------------
# rederive_attendance — argument handling
# --------------------------------------------------------------------------

def test_requires_a_date_selector():
    with pytest.raises(CommandError, match="Provide --date"):
        run("rederive_attendance")


def test_date_and_range_are_mutually_exclusive():
    with pytest.raises(CommandError, match="not both"):
        run("rederive_attendance", date="2026-08-03", date_from="2026-08-01")


def test_malformed_date_is_rejected():
    with pytest.raises(CommandError, match="YYYY-MM-DD"):
        run("rederive_attendance", date="03/08/2026")


def test_inverted_range_is_rejected():
    with pytest.raises(CommandError, match="is after"):
        run("rederive_attendance", date_from="2026-08-10", date_to="2026-08-01")


def test_unknown_employee_is_rejected(employee, punch):
    punch(9, 0, 0)
    with pytest.raises(CommandError, match="No employee matches"):
        run("rederive_attendance", date="2026-08-03", employee=["nobody@nowhere.test"])


def test_open_ended_range_without_punches_is_rejected():
    with pytest.raises(CommandError, match="nothing to re-derive"):
        run("rederive_attendance", date_to="2026-08-03")


# --------------------------------------------------------------------------
# rederive_attendance — behaviour
# --------------------------------------------------------------------------

def test_rederive_single_date_rebuilds_the_row(employee, punch):
    punch(9, 0, 0)
    punch(18, 0, 1)
    run("process_punches")

    # Simulate drift: something corrupted the derived row.
    Attendance.objects.filter(employee=employee, date=DAY).update(
        status=Attendance.Status.ABSENT, working_hours=Decimal("0.00"))

    out, _ = run("rederive_attendance", date="2026-08-03")
    assert "1 written" in out
    rec = Attendance.objects.get(employee=employee, date=DAY)
    assert rec.status == Attendance.Status.PRESENT
    assert rec.working_hours == Decimal("9.00")


def test_rederive_range(employee, punch):
    for d in (date(2026, 8, 1), date(2026, 8, 3), date(2026, 8, 5)):
        punch(9, 0, 0, day=d)
    out, _ = run("rederive_attendance", date_from="2026-08-01", date_to="2026-08-03")
    assert "2 employee-day" in out
    assert Attendance.objects.count() == 2


def test_rederive_ignores_is_processed(employee, punch):
    """The repair tool must work on already-processed punches — that is the
    whole point of running it after a mapping change."""
    punch(9, 0, 0)
    run("process_punches")
    assert AttendancePunch.objects.filter(is_processed=False).count() == 0

    Attendance.objects.filter(employee=employee).delete()
    run("rederive_attendance", date="2026-08-03")
    assert Attendance.objects.filter(employee=employee, date=DAY).exists()


def test_rederive_scoped_to_one_employee(employee, other_employee, second_device, punch):
    from biometric.models import BiometricEmployee
    other_map = BiometricEmployee.objects.create(
        device=second_device, device_user_id="2", user=other_employee)
    punch(9, 0, 0)
    AttendancePunch.objects.create(
        device=second_device, employee_device_id="2", biometric_employee=other_map,
        user=other_employee,
        timestamp=timezone.make_aware(datetime(2026, 8, 3, 9, 0)), punch=0)

    run("rederive_attendance", date="2026-08-03", employee=[employee.email])
    assert Attendance.objects.filter(employee=employee).exists()
    assert not Attendance.objects.filter(employee=other_employee).exists()


def test_employee_can_be_selected_by_employee_code(employee, punch):
    employee.employee_id = "NIFN-EMP-2026-0042"
    employee.save(update_fields=["employee_id"])
    punch(9, 0, 0)
    run("rederive_attendance", date="2026-08-03", employee=["NIFN-EMP-2026-0042"])
    assert Attendance.objects.filter(employee=employee).exists()


def test_rederive_preserves_hr_rows_by_default(employee, punch):
    Attendance.objects.create(
        employee=employee, date=DAY, source=Attendance.Source.HR,
        marked_by=Attendance.MarkedBy.HR, status=Attendance.Status.HALF_DAY)
    punch(9, 0, 0)
    punch(18, 0, 1)

    run("rederive_attendance", date="2026-08-03")
    assert Attendance.objects.get(employee=employee, date=DAY).status == Attendance.Status.HALF_DAY


def test_force_overwrites_hr_rows_and_warns(employee, punch):
    Attendance.objects.create(
        employee=employee, date=DAY, source=Attendance.Source.HR,
        marked_by=Attendance.MarkedBy.HR, status=Attendance.Status.HALF_DAY)
    punch(9, 0, 0)
    punch(18, 0, 1)

    out, _ = run("rederive_attendance", date="2026-08-03", force=True)
    assert "HR-corrected rows were overwritten" in out
    assert Attendance.objects.get(employee=employee, date=DAY).status == Attendance.Status.PRESENT


# --------------------------------------------------------------------------
# dry-run
# --------------------------------------------------------------------------

def test_dry_run_writes_nothing(employee, punch):
    punch(9, 0, 0)
    out, _ = run("rederive_attendance", date="2026-08-03", dry_run=True)
    assert "[dry-run]" in out
    assert "1 employee-day(s) would be re-derived" in out
    assert "nothing was written" in out
    assert not Attendance.objects.exists()


def test_dry_run_flags_protected_hr_rows(employee, punch):
    Attendance.objects.create(
        employee=employee, date=DAY, source=Attendance.Source.HR,
        marked_by=Attendance.MarkedBy.HR, status=Attendance.Status.HALF_DAY)
    punch(9, 0, 0)

    out, _ = run("rederive_attendance", date="2026-08-03", dry_run=True)
    assert "would be preserved" in out

    out_forced, _ = run("rederive_attendance", date="2026-08-03", dry_run=True, force=True)
    assert "would be OVERWRITTEN" in out_forced
    assert Attendance.objects.get(employee=employee, date=DAY).status == Attendance.Status.HALF_DAY
