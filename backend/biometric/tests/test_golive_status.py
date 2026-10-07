"""The success-criteria board.

The failure mode worth testing for is a checklist that grades itself green. So
most of these break something and assert the board notices — and one asserts
the board *refuses* to certify the criterion no database can settle.
"""
from datetime import date, timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from attendance.models import Attendance
from biometric.models import AttendancePunch, BiometricDevice, BiometricEmployee

pytestmark = pytest.mark.django_db


def run(*args):
    out = StringIO()
    call_command("golive_status", *args, stdout=out, stderr=out)
    return out.getvalue()


def report(*args):
    import json
    return json.loads(run("--json", *args))


def status_of(data, fragment):
    for item in data["criteria"]:
        if fragment.lower() in item["criterion"].lower():
            return item["status"]
    raise AssertionError(f"no criterion matching {fragment!r}")


@pytest.fixture
def device(db):
    return BiometricDevice.objects.create(
        name="Main Gate", label="main-gate", serial_number="CJXK205060099",
        device_timezone="Asia/Kathmandu", is_active=True)


@pytest.fixture
def working_system(device, employee):
    """A mapped employee with device-derived attendance across the window."""
    BiometricEmployee.objects.create(
        device=device, device_user_id="17", user=employee, is_active=True)
    day = date(2026, 7, 14)
    today = timezone.localdate()
    while day <= today:
        if day.weekday() != 5:
            moment = timezone.make_aware(
                timezone.datetime(day.year, day.month, day.day, 9, 5))
            AttendancePunch.objects.create(
                device=device, employee_device_id="17", user=employee,
                timestamp=moment, local_date=day, punch=0, punch_label="check_in",
                source=AttendancePunch.Source.LIVE)
            Attendance.objects.create(
                employee=employee, date=day, check_in=moment,
                source=Attendance.Source.BIOMETRIC,
                status=Attendance.Status.PRESENT)
        day += timedelta(days=1)
    return device


class TestEveryCriterionIsAnswered:
    def test_all_nine_appear(self, working_system):
        data = report()
        assert len(data["criteria"]) == 9

    def test_the_named_criteria_are_present(self, working_system):
        data = report()
        for fragment in ("Historical attendance", "biometric users",
                         "Device IDs unchanged", "Real fingerprint",
                         "continues automatically", "dashboard", "Reports",
                         "Analytics", "manual attendance"):
            status_of(data, fragment)   # raises if missing


class TestItRefusesToSelfCertify:
    def test_the_real_fingerprint_criterion_is_never_auto_passed(self, working_system):
        """Even with a perfect database, a row cannot prove a finger."""
        assert status_of(report(), "Real fingerprint") == "MANUAL"

    def test_it_judges_liveness_by_latency_not_by_the_source_label(self, device,
                                                                   employee):
        """A pull collector labels every punch HISTORY, because that is how it
        arrived. Keying on source == LIVE made this criterion unsatisfiable for
        the deployment it was written for."""
        BiometricEmployee.objects.create(
            device=device, device_user_id="28", user=employee, is_active=True)
        AttendancePunch.objects.create(
            device=device, employee_device_id="28", user=employee,
            timestamp=timezone.now() - timedelta(seconds=20),
            local_date=timezone.localdate(), punch=0, punch_label="check_in",
            source=AttendancePunch.Source.HISTORY)

        data = report()
        for item in data["criteria"]:
            if "Real fingerprint" in item["criterion"]:
                assert "promptly-arrived" in item["detail"], item["detail"]
                assert "28" in item["detail"]
                break
        else:
            raise AssertionError("criterion missing")

    def test_the_overall_verdict_stops_at_awaiting_human(self, working_system):
        assert report()["verdict"] == "AWAITING HUMAN VERIFICATION"

    def test_the_board_says_what_the_human_must_do(self, working_system):
        output = run()
        assert "verify_realtime_punch" in output
        assert "cannot" in output.lower()


class TestItCatchesBreakage:
    def test_no_history_fails(self, device, db):
        assert status_of(report(), "Historical attendance") == "FAIL"

    def test_unmapped_enrolments_fail(self, device, db):
        BiometricEmployee.objects.create(
            device=device, device_user_id="9", is_active=True)
        data = report()
        assert status_of(data, "biometric users") == "FAIL"
        assert data["verdict"] == "NOT READY"

    def test_a_punch_from_an_unenrolled_id_is_flagged(self, working_system, device):
        AttendancePunch.objects.create(
            device=device, employee_device_id="999", user=None,
            timestamp=timezone.now(), local_date=timezone.localdate(),
            punch=0, punch_label="check_in")
        assert status_of(report(), "Device IDs unchanged") == "WARN"

    def test_no_delivery_mechanism_fails(self, employee, db):
        """No PUSH serial and no device_sync heartbeat means attendance only
        arrives when a human runs something — which the requirement forbids."""
        BiometricDevice.objects.create(name="Old", label="old", is_active=True)
        assert status_of(report(), "continues automatically") == "FAIL"

    def test_a_registered_push_serial_satisfies_automation(self, working_system):
        assert status_of(report(), "continues automatically") == "PASS"

    def test_punches_with_no_derived_attendance_fail_the_dashboard(self, device, employee):
        BiometricEmployee.objects.create(
            device=device, device_user_id="17", user=employee, is_active=True)
        AttendancePunch.objects.create(
            device=device, employee_device_id="17", user=employee,
            timestamp=timezone.now(), local_date=timezone.localdate(),
            punch=0, punch_label="check_in")
        assert status_of(report(), "dashboard") == "FAIL"

    def test_mostly_manual_attendance_is_flagged(self, device, employee):
        """HR entries are the measurable proxy for a manual workflow."""
        BiometricEmployee.objects.create(
            device=device, device_user_id="17", user=employee, is_active=True)
        for offset in range(4):
            day = timezone.localdate() - timedelta(days=offset)
            Attendance.objects.create(
                employee=employee, date=day,
                check_in=timezone.now(), source=Attendance.Source.HR,
                status=Attendance.Status.PRESENT)
        assert status_of(report(), "manual attendance") == "WARN"


class TestOutput:
    def test_it_renders_a_readable_board(self, working_system):
        output = run()
        assert "Go-live success criteria" in output
        assert "VERDICT:" in output

    def test_it_creates_nothing(self, working_system):
        before = (AttendancePunch.objects.count(), Attendance.objects.count())
        run()
        assert before == (AttendancePunch.objects.count(), Attendance.objects.count())
