"""The go-live gate: does a real punch reach every layer, and does the command
say so honestly?

The thing being tested is as much the *reporting* as the tracing. A verification
command that says PASS when a link is broken is worse than no command — it
converts an unknown into a signed-off assurance, and go/no-go gets decided on
it. So most of these tests break one link and assert the command notices.
"""
from datetime import date, datetime, timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from attendance.models import Attendance
from biometric.models import AttendancePunch, BiometricDevice, BiometricEmployee

pytestmark = pytest.mark.django_db


def run(*args, **kwargs):
    out = StringIO()
    call_command("verify_realtime_punch", *args, stdout=out, stderr=out, **kwargs)
    return out.getvalue()


@pytest.fixture
def push_device(db):
    return BiometricDevice.objects.create(
        name="Main Gate", label="main-gate", host="192.168.77.201",
        serial_number="CJXK205060099", device_timezone="Asia/Kathmandu",
        is_active=True)


@pytest.fixture
def punch_now(push_device, employee):
    """A mapped employee who punched in a minute ago, fully derived."""
    BiometricEmployee.objects.create(
        device=push_device, device_user_id="17", user=employee, is_active=True)
    moment = timezone.now() - timedelta(minutes=1)
    punch = AttendancePunch.objects.create(
        device=push_device, employee_device_id="17", user=employee,
        timestamp=moment, local_date=timezone.localdate(),
        punch=0, punch_label="check_in",
        source=AttendancePunch.Source.LIVE)
    Attendance.objects.create(
        employee=employee, date=timezone.localdate(), check_in=moment,
        source=Attendance.Source.BIOMETRIC, status=Attendance.Status.PRESENT)
    return punch


class TestTheHappyPath:
    def test_it_does_not_claim_to_have_verified_a_finger(self, punch_now):
        """It traced a row. A simulated punch produces an identical one, so
        claiming "verified" would hand a human judgement to a query."""
        output = run("--device-user-id", "17")
        assert "only if you watched it happen" in output

    def test_a_complete_chain_verifies(self, punch_now):
        output = run("--device-user-id", "17")
        assert "CHAIN COMPLETE" in output
        assert "[FAIL" not in output

    def test_every_link_in_the_requirement_is_reported(self, punch_now):
        output = run("--device-user-id", "17")
        for step in ("Punch received", "Enrolment known", "Device ID unchanged",
                     "Punch attributed", "Attendance derived", "Attendance source",
                     "Dashboard", "Reports", "Analytics"):
            assert step in output, f"the chain does not report {step!r}"

    def test_any_finds_the_most_recent_punch(self, punch_now):
        assert "CHAIN COMPLETE" in run("--any")

    def test_json_carries_a_machine_verdict(self, punch_now):
        import json
        report = json.loads(run("--device-user-id", "17", "--json"))
        assert report["verdict"] in ("PASS", "PASS_WITH_WARNINGS")
        assert len(report["checks"]) >= 9


class TestItRefusesToPassABrokenChain:
    def test_no_punch_at_all_is_a_failure_with_a_diagnosis(self, push_device, db):
        output = run("--device-user-id", "17")
        assert "NOT VERIFIED" in output
        assert "[FAIL" in output
        # The operator needs to know where to look, not just that it failed.
        assert "server address" in output or "serial" in output

    def test_an_unmapped_enrolment_fails(self, push_device, db):
        BiometricEmployee.objects.create(
            device=push_device, device_user_id="17", is_active=True)
        AttendancePunch.objects.create(
            device=push_device, employee_device_id="17", user=None,
            timestamp=timezone.now() - timedelta(minutes=1),
            local_date=timezone.localdate(), punch=0, punch_label="check_in")
        output = run("--device-user-id", "17")
        assert "NOT VERIFIED" in output
        assert "unmapped" in output.lower()

    def test_a_punch_with_no_attendance_row_fails(self, push_device, employee):
        BiometricEmployee.objects.create(
            device=push_device, device_user_id="17", user=employee, is_active=True)
        AttendancePunch.objects.create(
            device=push_device, employee_device_id="17", user=employee,
            timestamp=timezone.now() - timedelta(minutes=1),
            local_date=timezone.localdate(), punch=0, punch_label="check_in")
        output = run("--device-user-id", "17")
        assert "NOT VERIFIED" in output
        assert "Attendance derived" in output

    def test_an_hr_row_is_flagged_rather_than_silently_passing(self, push_device,
                                                               employee):
        """HR outranks the device. The chain is technically intact but the
        displayed attendance did not come from the fingerprint, and signing off
        on that would be signing off on the wrong thing."""
        BiometricEmployee.objects.create(
            device=push_device, device_user_id="17", user=employee, is_active=True)
        moment = timezone.now() - timedelta(minutes=1)
        AttendancePunch.objects.create(
            device=push_device, employee_device_id="17", user=employee,
            timestamp=moment, local_date=timezone.localdate(),
            punch=0, punch_label="check_in")
        Attendance.objects.create(
            employee=employee, date=timezone.localdate(), check_in=moment,
            source=Attendance.Source.HR, status=Attendance.Status.PRESENT)

        output = run("--device-user-id", "17")
        assert "[WARN" in output
        assert "HR entry" in output

    def test_a_stale_punch_outside_the_window_is_not_counted(self, push_device,
                                                             employee, punch_now):
        AttendancePunch.objects.all().update(
            timestamp=timezone.now() - timedelta(hours=6))
        output = run("--device-user-id", "17", "--minutes", "5")
        assert "NOT VERIFIED" in output


class TestLatency:
    def test_a_prompt_punch_passes(self, punch_now):
        assert "Arrival latency" in run("--device-user-id", "17")

    def test_a_slow_arrival_is_flagged_as_not_real_time(self, push_device, employee):
        """Arriving eventually is not the same as arriving live, and the
        requirement is automatic real-time delivery."""
        BiometricEmployee.objects.create(
            device=push_device, device_user_id="17", user=employee, is_active=True)
        punched = timezone.now() - timedelta(hours=3)
        punch = AttendancePunch.objects.create(
            device=push_device, employee_device_id="17", user=employee,
            timestamp=punched, local_date=timezone.localdate(),
            punch=0, punch_label="check_in")
        # received_at is auto_now_add, i.e. now — a 3-hour delivery lag.
        Attendance.objects.create(
            employee=employee, date=timezone.localdate(), check_in=punched,
            source=Attendance.Source.BIOMETRIC, status=Attendance.Status.PRESENT)

        output = run("--device-user-id", "17", "--minutes", "300")
        assert "[WARN" in output
        assert "batched" in output or "polled" in output
        assert punch.employee_device_id == "17"


class TestUsage:
    def test_it_requires_a_target(self, db):
        with pytest.raises(CommandError, match="device-user-id"):
            run()

    def test_it_creates_nothing(self, push_device, employee, punch_now):
        """A verification tool that writes cannot verify — it would be proving
        its own side effects."""
        before = (AttendancePunch.objects.count(), Attendance.objects.count(),
                  BiometricEmployee.objects.count())
        run("--device-user-id", "17")
        after = (AttendancePunch.objects.count(), Attendance.objects.count(),
                 BiometricEmployee.objects.count())
        assert before == after
