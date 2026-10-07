"""Phase 12 go-live tooling.

These commands run once, against production, under time pressure, on the day
that matters. That is the worst possible moment to discover one of them raises
on an empty table — so every one is exercised here against both an empty
database and a populated one.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from attendance.models import Attendance
from biometric import device_probe
from biometric.models import (
    AttendancePunch, BiometricDevice, BiometricEmployee,
)
from users.models import User

pytestmark = pytest.mark.django_db

WINDOW_START = date(2026, 7, 14)


def run(command, *args, **kwargs):
    out = StringIO()
    call_command(command, *args, stdout=out, stderr=StringIO(), **kwargs)
    return out.getvalue()


@pytest.fixture
def device(db):
    return BiometricDevice.objects.create(
        name="Main Gate", label="main-gate", host="192.168.77.201",
        is_active=True, api_key_prefix="abcd1234",
        api_key_hash="0" * 64, connection_status=BiometricDevice.Status.ONLINE,
        last_seen_at=timezone.now())


@pytest.fixture
def employee(db):
    user = User.objects.create_user(
        username="gl_emp", email="gl.emp@nif.test", password="pass12345",
        first_name="Bikash", last_name="Kadayat", role=User.Roles.MAKER,
        employee_id="NIFN-EMP-2026-0017", date_of_joining=date(2020, 1, 1))
    User.objects.filter(pk=user.pk).update(
        date_joined=timezone.make_aware(datetime(2020, 1, 1, 9, 0)))
    return user


@pytest.fixture
def admin(db):
    return User.objects.create_user(
        username="gl_admin", email="gl.admin@nif.test", password="pass12345",
        first_name="Ops", last_name="Admin", role=User.Roles.ADMIN)


# ===========================================================================
# device probe
# ===========================================================================
class TestDeviceProbe:
    def test_unreachable_host_is_reported_not_raised(self):
        """192.0.2.1 is TEST-NET-1: guaranteed never routable."""
        result = device_probe.full_probe("192.0.2.1", timeout=2)
        assert result["tcp"]["tcp_open"] is False
        assert result["verdict"].startswith("UNREACHABLE")

    def test_a_control_that_also_answers_makes_the_verdict_inconclusive(self):
        """The check that stops a friendly firewall reading as a healthy device."""
        verdict = device_probe.verdict(
            tcp={"tcp_open": True, "reply_command": 2000},
            udp={}, control={"tcp_open": True})
        assert verdict.startswith("INCONCLUSIVE")

    def test_an_ack_ok_reply_reads_as_ready(self):
        verdict = device_probe.verdict(
            tcp={"tcp_open": True, "reply_command": device_probe.CMD_ACK_OK},
            udp={}, control={"tcp_open": False})
        assert verdict.startswith("READY")

    def test_a_comm_key_is_distinguished_from_a_dead_device(self):
        verdict = device_probe.verdict(
            tcp={"tcp_open": True, "reply_command": device_probe.CMD_ACK_UNAUTH},
            udp={}, control={"tcp_open": False})
        assert "AUTH REQUIRED" in verdict

    def test_an_open_port_alone_is_not_treated_as_ready(self):
        """A TCP handshake proves a listener, not a terminal."""
        verdict = device_probe.verdict(
            tcp={"tcp_open": True, "reply_command": None},
            udp={"reply_command": None}, control={"tcp_open": False})
        assert "NOT SPEAKING ZK" in verdict

    def test_packets_carry_the_checksum_the_firmware_validates(self):
        """The checksum covers the PRE-INCREMENT header, not the bytes sent.

        This test used to assert the opposite — that re-checksumming the
        transmitted packet reproduces its own checksum field. That is what a
        checksum normally means, and it is wrong here.

        `pack('<4H', command, 0, session_id, reply_id)` is checksummed, *then*
        reply_id is incremented and the header repacked with the new value. The
        vendor SDK does it (zk/base.py:186 `__create_header`, whose own comment
        traces it to zkemsdk.c) and so does the firmware: against the ZLM60 at
        192.168.77.201, the vendor-convention packet gets a reply and the
        self-consistent one is dropped without a word.

            sent  50 50 82 7d 08 00 00 00 e8 03 16 fc 00 00 01 00
            reply 50 50 82 7d 08 00 00 00 d5 07 07 28 22 d0 01 00   (CMD_ACK_*)

            sent  50 50 82 7d 08 00 00 00 e8 03 15 fc 00 00 01 00
            (silence)

        So "fixing" the arithmetic here breaks the collector, and breaks it
        silently — a dead terminal, not a protocol error. Both byte strings are
        pinned below so the regression cannot come back as a green test.
        """
        import struct

        packet = device_probe.build_packet(device_probe.CMD_CONNECT)
        assert len(packet) == 8
        assert struct.unpack("<H", packet[0:2])[0] == device_probe.CMD_CONNECT

        # Ground truth: the exact bytes the terminal answered.
        assert packet == bytes.fromhex("e803" "16fc" "0000" "0100")

        # The convention, stated as arithmetic rather than as a literal.
        pre_increment_header = struct.pack(
            "<4H", device_probe.CMD_CONNECT, 0, 0, 0)
        assert device_probe._checksum(pre_increment_header) == packet[2:4]
        assert struct.unpack("<H", packet[6:8])[0] == 1, "reply_id is transmitted +1"

        # And the value that must NOT be sent: the self-consistent checksum.
        zeroed = bytearray(packet)
        zeroed[2:4] = b"\x00\x00"
        assert device_probe._checksum(bytes(zeroed)) == b"\x15\xfc"
        assert device_probe._checksum(bytes(zeroed)) != packet[2:4]

    def test_the_checksum_matches_the_vendor_sdk_byte_for_byte(self):
        """Differential check against pyzk, which is a pinned dependency.

        `_checksum` is a hand port of zkemsdk.c's routine — the ones-complement
        sum with its two unusual details: the fold-down happens *inside* the
        loop with USHRT_MAX (65535, not 65536), and a trailing odd byte is added
        raw. Either detail is easy to "tidy" into something that is correct
        ones-complement arithmetic and rejected by the device.
        """
        from zk.base import ZK

        theirs = ZK._ZK__create_checksum
        for payload in (b"\x01", b"\xff\xff", b"\xff\xff\xff",
                        b"\xe8\x03\x00\x00\x00\x00\x00\x00",
                        bytes(range(256))):
            assert device_probe._checksum(payload) == theirs(None, tuple(payload)), (
                f"diverged from pyzk on {payload.hex()}")


# ===========================================================================
# device_readiness
# ===========================================================================
class TestDeviceReadiness:
    def test_an_empty_system_reports_blockers_not_a_crash(self, db, admin):
        output = run("device_readiness")
        assert "NOT READY" in output
        assert "No BiometricDevice rows exist" in output

    def test_a_device_without_an_api_key_warns_but_does_not_block(self, db, admin):
        """The API key authenticates the *push* path only.

        Since Phase 12 the OMS collects by pulling (`device_sync`), which calls
        ingest in-process and never presents a key. Blocking go-live on a
        credential nothing in the deployment uses would be a false blocker —
        and a checklist with false blockers gets overridden wholesale.
        """
        BiometricDevice.objects.create(name="Keyless", label="keyless",
                                       is_active=True, api_key_hash="")
        output = run("device_readiness")
        assert "Device API keys" in output
        assert "device_sync" in output, "the warning must say what does not need it"
        assert "[WARN]" in output

    def test_unmapped_enrolments_are_surfaced(self, device, admin):
        BiometricEmployee.objects.create(
            device=device, device_user_id="7", device_name="Ghost", is_active=True)
        output = run("device_readiness")
        assert "unmapped" in output.lower()

    def test_a_fully_wired_system_reports_the_chain(self, device, employee, admin):
        BiometricEmployee.objects.create(
            device=device, device_user_id="17", device_name="Bikash",
            user=employee, is_active=True)
        AttendancePunch.objects.create(
            device=device, employee_device_id="17", user=employee,
            timestamp=timezone.now(), local_date=timezone.localdate(),
            punch=0, punch_label="in", is_processed=True)
        output = run("device_readiness")
        assert "Punches received" in output
        assert "Dashboard read path" in output

    def test_json_output_is_machine_readable(self, device, admin):
        import json

        payload = json.loads(run("device_readiness", "--json"))
        assert "checks" in payload and isinstance(payload["checks"], list)


# ===========================================================================
# roster_report
# ===========================================================================
class TestRosterReport:
    def test_an_empty_roster_says_so_plainly(self, db):
        assert "No active enrolments" in run("roster_report")

    def test_it_suggests_the_obvious_match(self, device, employee):
        BiometricEmployee.objects.create(
            device=device, device_user_id="17", device_name="Bikashkadayat",
            is_active=True)
        output = run("roster_report")
        assert "Bikash Kadayat" in output
        assert "UNMAPPED" in output

    def test_it_never_writes_a_mapping(self, device, employee):
        enrolment = BiometricEmployee.objects.create(
            device=device, device_user_id="17", device_name="Bikashkadayat",
            is_active=True)
        run("roster_report")
        enrolment.refresh_from_db()
        # A wrong auto-mapping silently misattributes attendance, so proposing
        # is the whole contract.
        assert enrolment.user_id is None

    def test_two_similar_names_are_flagged_ambiguous(self, device, employee):
        twin = User.objects.create_user(
            username="gl_twin", email="gl.twin@nif.test", password="x",
            first_name="Bikash", last_name="Kadayat", role=User.Roles.MAKER)
        BiometricEmployee.objects.create(
            device=device, device_user_id="17", device_name="Bikashkadayat",
            is_active=True)
        output = run("roster_report")
        assert "AMBIGUOUS" in output
        assert twin.get_full_name() in output

    def test_mapped_rows_are_reported_as_mapped(self, device, employee):
        BiometricEmployee.objects.create(
            device=device, device_user_id="17", user=employee, is_active=True)
        output = run("roster_report")
        assert "mapped" in output

    def test_csv_export_carries_every_column(self, device, employee, tmp_path):
        BiometricEmployee.objects.create(
            device=device, device_user_id="17", device_name="Bikashkadayat",
            is_active=True)
        path = tmp_path / "mapping.csv"
        run("roster_report", "--csv", str(path))

        import csv

        with open(path, encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        assert rows and "Match Score" in rows[0] and "Confidence" in rows[0]


# ===========================================================================
# import_plan
# ===========================================================================
class TestImportPlan:
    def test_an_empty_window_says_there_is_nothing_to_import(self, db):
        output = run("import_plan", "--from", WINDOW_START.isoformat())
        assert "NO PUNCHES INGESTED" in output
        assert "bulk-sync" in output

    def test_unmapped_punches_are_a_blocker(self, device, employee):
        AttendancePunch.objects.create(
            device=device, employee_device_id="404", user=None,
            timestamp=timezone.now(), local_date=timezone.localdate(),
            punch=0, punch_label="in")
        output = run("import_plan", "--from", WINDOW_START.isoformat())
        assert "BLOCKER" in output
        assert "unmapped" in output.lower()

    def test_hr_rows_are_flagged_as_protected(self, device, employee):
        day = timezone.localdate() - timedelta(days=1)
        AttendancePunch.objects.create(
            device=device, employee_device_id="17", user=employee,
            timestamp=timezone.now(), local_date=day, punch=0, punch_label="in")
        Attendance.objects.create(
            employee=employee, date=day, status=Attendance.Status.PRESENT,
            source=Attendance.Source.HR, working_hours=Decimal("8.00"))
        output = run("import_plan", "--from", WINDOW_START.isoformat())
        assert "HR" in output
        assert "precedence" in output.lower()

    def test_exact_duplicates_are_reported_as_impossible(self, device, employee):
        AttendancePunch.objects.create(
            device=device, employee_device_id="17", user=employee,
            timestamp=timezone.now(), local_date=timezone.localdate(),
            punch=0, punch_label="in")
        output = run("import_plan", "--from", WINDOW_START.isoformat())
        assert "unique constraint" in output.lower()

    def test_json_output_carries_every_section(self, device, employee):
        import json

        AttendancePunch.objects.create(
            device=device, employee_device_id="17", user=employee,
            timestamp=timezone.now(), local_date=timezone.localdate(),
            punch=0, punch_label="in")
        plan = json.loads(run("import_plan", "--from", WINDOW_START.isoformat(),
                              "--json"))
        for key in ("window", "punches", "users", "attendance", "duplicates", "gaps"):
            assert key in plan

    def test_an_inverted_window_is_refused(self, db):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            run("import_plan", "--from", "2026-08-01", "--to", "2026-07-01")


# ===========================================================================
# validate_import
# ===========================================================================
class TestValidateImport:
    def test_unprocessed_punches_fail_the_check(self, device, employee):
        AttendancePunch.objects.create(
            device=device, employee_device_id="17", user=employee,
            timestamp=timezone.now(), local_date=timezone.localdate(),
            punch=0, punch_label="in", is_processed=False)
        output = run("validate_import", "--from", WINDOW_START.isoformat())
        assert "FAIL" in output
        assert "process_punches" in output

    def test_unmapped_punches_are_called_out_as_missing_days(self, device):
        AttendancePunch.objects.create(
            device=device, employee_device_id="404", user=None,
            timestamp=timezone.now(), local_date=timezone.localdate(),
            punch=0, punch_label="in", is_processed=True)
        output = run("validate_import", "--from", WINDOW_START.isoformat())
        assert "MISSING" in output

    def test_it_counts_every_required_status(self, device, employee):
        day = timezone.localdate() - timedelta(days=1)
        Attendance.objects.create(
            employee=employee, date=day, status=Attendance.Status.LATE,
            source=Attendance.Source.BIOMETRIC, late_minutes=30,
            comp_off_eligible=True, overtime_hours=Decimal("1.50"),
            working_hours=Decimal("9.50"))
        output = run("validate_import", "--from", WINDOW_START.isoformat())
        for label in ("Late", "Half day", "Work from home", "Comp-off eligible"):
            assert label in output

    def test_json_output_is_machine_readable(self, device, employee):
        import json

        result = json.loads(run("validate_import", "--from",
                                WINDOW_START.isoformat(), "--json"))
        assert "attendance" in result and "checks" in result


# ===========================================================================
# verify_live_flow
# ===========================================================================
class TestVerifyLiveFlow:
    def test_an_unknown_device_is_refused_with_the_options_listed(self, db):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError, match="No device named"):
            run("verify_live_flow", "--device", "Nope", "--api-key", "x")

    def test_an_inactive_device_is_refused(self, db):
        BiometricDevice.objects.create(name="Old Gate", label="old",
                                       is_active=False)
        from django.core.management.base import CommandError

        with pytest.raises(CommandError, match="inactive"):
            run("verify_live_flow", "--device", "Old Gate", "--api-key", "x")

    def test_the_probe_never_touches_a_real_employee(self, device, employee):
        """A fabricated punch against a real person would put a fabricated day
        on their attendance record — the one thing this system must not do."""
        from biometric.management.commands import verify_live_flow

        assert verify_live_flow.PROBE_USERNAME.startswith("_")
        assert verify_live_flow.PROBE_DEVICE_USER_ID == "999999"

        try:
            run("verify_live_flow", "--device", device.name, "--api-key", "wrong")
        except Exception:  # noqa: BLE001 -- a bad key is expected to fail
            pass

        # Whatever happened, the real employee is untouched.
        assert not Attendance.objects.filter(employee=employee).exists()
        assert not AttendancePunch.objects.filter(user=employee).exists()

    def test_probe_fixtures_are_cleaned_up_after_a_failed_run(self, device):
        from biometric.management.commands.verify_live_flow import PROBE_USERNAME

        try:
            run("verify_live_flow", "--device", device.name, "--api-key", "wrong")
        except Exception:  # noqa: BLE001
            pass
        assert not User.objects.filter(username=PROBE_USERNAME).exists()
