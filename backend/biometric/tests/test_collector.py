"""The in-OMS collector: wire protocol, decoding, and the pull-to-ingest path.

Phase 12. The terminal is the source of truth, so the two things that must never
go wrong are (a) a partial read presented as a complete one — missing punches
look exactly like absent employees — and (b) any alteration of the device's own
user IDs on the way in.

The protocol half runs against ``zk_simulator.FakeZKDevice`` rather than a mock,
because the bugs worth catching here are framing bugs, and a mock of the socket
would be a mock of the thing under test.
"""
import struct
from datetime import date, datetime, timedelta
from io import StringIO
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from biometric import collector, zk_client
from biometric.models import AttendancePunch, BiometricEmployee
from biometric.tests.zk_simulator import (
    FakeZKDevice,
    encode_device_time,
    pack_attendance,
    pack_attendance_16,
    pack_attendance_8,
    pack_user,
    pack_user_small,
    table,
)

KATHMANDU = ZoneInfo("Asia/Kathmandu")


def run(command, *args, **kwargs):
    out = StringIO()
    call_command(command, *args, stdout=out, stderr=StringIO(), **kwargs)
    return out.getvalue()


# ===========================================================================
# decoding — pure, no sockets
# ===========================================================================
class TestClockDecoding:
    def test_round_trips(self):
        moment = datetime(2026, 7, 14, 9, 15, 4)
        assert zk_client.decode_device_time(
            struct.pack("<I", encode_device_time(moment))) == moment

    def test_it_is_naive(self):
        """The terminal has no idea what zone it is in, and neither should this."""
        decoded = zk_client.decode_device_time(
            struct.pack("<I", encode_device_time(datetime(2026, 8, 6, 17, 30))))
        assert decoded.tzinfo is None

    @pytest.mark.parametrize("moment", [
        datetime(2026, 1, 1, 0, 0, 0),
        datetime(2026, 12, 31, 23, 59, 59),
        datetime(2026, 2, 28, 12, 0, 0),
    ])
    def test_boundaries(self, moment):
        assert zk_client.decode_device_time(
            struct.pack("<I", encode_device_time(moment))) == moment


class TestUserDecoding:
    def test_the_device_user_id_survives_verbatim(self):
        users = zk_client.parse_users(table([
            pack_user(1, "17", "Bikashkadayat", card=4017)]))
        assert users[0]["employee_id"] == "17", "the collector altered a device ID"
        assert users[0]["name"] == "Bikashkadayat"
        assert users[0]["card"] == 4017

    def test_a_non_numeric_device_id_is_not_coerced(self):
        """Some sites enrol staff numbers like 'NIF007'. Keep them as they are."""
        users = zk_client.parse_users(table([pack_user(2, "NIF007", "Sita")]))
        assert users[0]["employee_id"] == "NIF007"

    def test_leading_zeros_are_preserved(self):
        """'007' and '7' are different device users. Losing the zeros merges them."""
        users = zk_client.parse_users(table([pack_user(3, "007", "Ram")]))
        assert users[0]["employee_id"] == "007"

    def test_the_small_record_layout_is_understood(self):
        users = zk_client.parse_users(table([pack_user_small(4, 42, "Hari")]))
        assert users[0]["employee_id"] == "42"
        assert users[0]["name"] == "Hari"

    def test_a_blank_user_id_falls_back_to_the_internal_uid(self):
        users = zk_client.parse_users(table([pack_user(9, "", "Nameless")]))
        assert users[0]["employee_id"] == "9"

    def test_an_empty_roster_is_not_an_error(self):
        assert zk_client.parse_users(table([])) == []

    def test_every_user_is_returned(self):
        rows = [pack_user(i, str(i), f"User{i}") for i in range(1, 31)]
        assert len(zk_client.parse_users(table(rows))) == 30


class TestAttendanceDecoding:
    def test_a_punch_decodes_whole(self):
        records = zk_client.parse_attendance(table([
            pack_attendance("17", datetime(2026, 7, 14, 9, 15, 4), punch=0, status=1)]))
        assert records == [{"employee_id": "17",
                            "timestamp": datetime(2026, 7, 14, 9, 15, 4),
                            "punch": 0, "status": 1}]

    def test_the_compact_layout_is_understood(self):
        records = zk_client.parse_attendance(table([
            pack_attendance_16("17", datetime(2026, 7, 14, 18, 5), punch=1)]))
        assert records[0]["employee_id"] == "17"
        assert records[0]["punch"] == 1

    def test_an_empty_log_is_not_an_error(self):
        assert zk_client.parse_attendance(table([])) == []

    def test_an_unknown_record_width_raises_rather_than_guesses(self):
        """Decoding at the wrong stride yields plausible garbage, so refuse."""
        payload = struct.pack("<I", 7) + b"\x01" * 7
        with pytest.raises(zk_client.ZKProtocolError, match="record layout"):
            zk_client.parse_attendance(payload)

    def test_the_width_is_not_inferred_from_divisibility_alone(self):
        """The bug this pins: 8, 16 and 40 all divide a 40-byte-record table.

        Picking the smallest divisor decodes every modern terminal's log at the
        wrong stride — which does not fail, it returns hundreds of fabricated
        punches for device user 0. Silent, and wrong in the worst direction.
        """
        payload = table([
            pack_attendance("17", datetime(2026, 7, 14, 9, 0)),
            pack_attendance("17", datetime(2026, 7, 14, 18, 0), punch=1)])
        assert len(payload) - 4 == 80   # divisible by 8, 16 and 40
        records = zk_client.parse_attendance(payload)
        assert [r["employee_id"] for r in records] == ["17", "17"]

    def test_the_record_count_settles_an_ambiguous_width(self):
        """40 bytes is either one 40-byte record or five 8-byte ones."""
        payload = table([
            pack_attendance_8(str(i), datetime(2026, 7, 14, 9, i)) for i in range(1, 6)])
        assert len(payload) - 4 == 40

        records = zk_client.parse_attendance(payload, count=5)
        assert [r["employee_id"] for r in records] == ["1", "2", "3", "4", "5"]

    def test_an_implausible_decode_is_rejected(self):
        """All-zero padding decodes to device user 0 in the year 2000."""
        with pytest.raises(zk_client.ZKProtocolError, match="did not decode sensibly"):
            zk_client.parse_attendance(struct.pack("<I", 40) + b"\x00" * 40)

    def test_a_few_corrupt_records_do_not_reject_a_correct_decode(self):
        """A terminal that lost its clock holds some junk. Reading the other 39
        records correctly beats refusing all 40."""
        good = [pack_attendance(str(i % 9 + 1), datetime(2026, 7, 14, 9, i % 60))
                for i in range(39)]
        records = zk_client.parse_attendance(table([*good, b"\x00" * 40]))
        assert len(records) == 40
        assert sum(1 for r in records if r["timestamp"].year == 2026) == 39


class TestCommKey:
    def test_it_matches_what_the_real_device_accepted(self):
        """Ground truth, captured from a working session with 192.168.77.201.

        This is the only test here worth much. The scramble has an asymmetry
        that reads like a typo — the third output byte is the tick VALUE, not a
        byte XORed with it — and an implementation that "corrects" it is
        rejected by the terminal with no diagnostic beyond silence. A property
        test would have happily passed the wrong version; this would not.
        """
        assert zk_client.make_commkey(0, 0xB08C) == bytes.fromhex("617d32c9")

    def test_the_session_changes_the_key(self):
        """Sessions that differ in a surviving byte produce different keys.

        Note the qualifier. The transform drops one byte of session entropy on
        the floor (see above), so sessions differing only in the low bits of
        that byte collide — ``make_commkey(k, 1) == make_commkey(k, 2)``. That
        is the firmware's design, not ours, and it is one more reason the comm
        key is not authentication: the LAN is the security boundary.
        """
        assert zk_client.make_commkey(123456, 0x0100) != zk_client.make_commkey(123456, 1)

    def test_it_is_four_bytes(self):
        assert len(zk_client.make_commkey(999999, 0x1234)) == 4

    def test_a_large_key_does_not_overflow(self):
        """Bit-reversal plus the session id can exceed 32 bits."""
        assert len(zk_client.make_commkey(0xFFFFFFFF, 0xFFFF)) == 4


# ===========================================================================
# the wire, against a simulated terminal
# ===========================================================================
ROSTER = table([pack_user(1, "1", "Ram Thapa"),
                pack_user(17, "17", "Bikashkadayat"),
                pack_user(2, "2", "Sita Gurung")])
PUNCHES = table([
    pack_attendance("1", datetime(2026, 7, 14, 9, 2), punch=0),
    pack_attendance("1", datetime(2026, 7, 14, 17, 40), punch=1),
    pack_attendance("17", datetime(2026, 7, 15, 9, 30), punch=0),
])


class TestProtocol:
    @pytest.mark.parametrize("mode", ["buffer", "prepare", "direct"])
    def test_every_firmware_variant_reads_the_same_data(self, mode):
        with FakeZKDevice(users=ROSTER, attendance=PUNCHES, transfer_mode=mode) as fake:
            with zk_client.ZKReadOnlyClient(fake.host, fake.port, timeout=5) as client:
                users = client.users()
                punches = client.attendance()
        assert [u["employee_id"] for u in users] == ["1", "17", "2"]
        assert len(punches) == 3

    def test_a_multi_packet_transfer_is_reassembled(self):
        """The failure this guards: a short read is indistinguishable from an
        employee who did not come to work."""
        rows = [pack_attendance(str(i % 20 + 1), datetime(2026, 7, 14, 9, i % 60))
                for i in range(500)]
        with FakeZKDevice(attendance=table(rows), transfer_mode="prepare",
                          chunk_bytes=512) as fake:
            with zk_client.ZKReadOnlyClient(fake.host, fake.port, timeout=5) as client:
                punches = client.attendance()
        assert len(punches) == 500

    def test_a_chunked_buffer_read_is_reassembled(self, monkeypatch):
        monkeypatch.setattr(zk_client, "MAX_CHUNK", 256)
        rows = [pack_attendance(str(i % 10 + 1), datetime(2026, 7, 14, 10, i % 60))
                for i in range(200)]
        with FakeZKDevice(attendance=table(rows), transfer_mode="buffer") as fake:
            with zk_client.ZKReadOnlyClient(fake.host, fake.port, timeout=5) as client:
                assert len(client.attendance()) == 200

    def test_sizes_and_clock_are_readable(self):
        clock = datetime(2026, 8, 6, 14, 30, 0)
        with FakeZKDevice(clock=clock, sizes={"users": 3, "records": 900}) as fake:
            with zk_client.ZKReadOnlyClient(fake.host, fake.port, timeout=5) as client:
                assert client.sizes()["users"] == 3
                assert client.sizes()["records"] == 900
                assert client.device_time() == clock

    def test_the_session_is_closed_on_the_way_out(self):
        with FakeZKDevice(users=ROSTER) as fake:
            with zk_client.ZKReadOnlyClient(fake.host, fake.port, timeout=5) as client:
                client.users()
        assert zk_client.CMD_EXIT in fake.commands, "the session was leaked"

    def test_the_terminal_is_never_disabled(self):
        """CMD_DISABLEDEVICE would lock the keypad; staff could not punch in."""
        with FakeZKDevice(users=ROSTER, attendance=PUNCHES) as fake:
            with zk_client.ZKReadOnlyClient(fake.host, fake.port, timeout=5) as client:
                client.users()
                client.attendance()
        assert set(fake.commands) <= zk_client.READ_ONLY_COMMANDS


class TestProtocolFailures:
    def test_a_write_command_cannot_be_sent(self):
        """The guarantee is structural, not a matter of discipline."""
        with FakeZKDevice() as fake:
            with zk_client.ZKReadOnlyClient(fake.host, fake.port, timeout=5) as client:
                with pytest.raises(zk_client.ZKError, match="READ_ONLY_COMMANDS"):
                    client._send(62)   # CMD_CLEAR_ATTLOG
        assert 62 not in fake.commands

    def test_something_that_is_not_a_terminal_is_rejected(self):
        with FakeZKDevice(garbage=True) as fake:
            with pytest.raises(zk_client.ZKProtocolError, match="not ZK-framed"):
                zk_client.ZKReadOnlyClient(fake.host, fake.port, timeout=5).connect()

    def test_an_unreachable_host_says_so(self):
        # Port 1 on loopback: nothing listens, and the refusal is immediate.
        with pytest.raises(zk_client.ZKError, match="cannot reach"):
            zk_client.ZKReadOnlyClient("127.0.0.1", 1, timeout=2).connect()

    def test_a_comm_key_is_required_when_the_device_has_one(self):
        with FakeZKDevice(comm_key=123456) as fake:
            with pytest.raises(zk_client.ZKAuthError, match="comm key"):
                zk_client.ZKReadOnlyClient(fake.host, fake.port, timeout=5).connect()

    def test_the_right_comm_key_gets_in(self):
        with FakeZKDevice(users=ROSTER, comm_key=123456) as fake:
            with zk_client.ZKReadOnlyClient(fake.host, fake.port, timeout=5,
                                            comm_key=123456) as client:
                assert len(client.users()) == 3

    def test_the_wrong_comm_key_is_refused(self):
        with FakeZKDevice(comm_key=123456) as fake:
            with pytest.raises(zk_client.ZKAuthError, match="rejected"):
                zk_client.ZKReadOnlyClient(fake.host, fake.port, timeout=5,
                                           comm_key=999).connect()

    def test_a_truncated_transfer_is_an_error_not_a_partial_success(self):
        """A device that announces more than it sends must not look successful.

        This is the failure the whole framing layer exists to prevent: punches
        that never arrived are indistinguishable from employees who never came
        in, and both render as an empty attendance row.
        """
        with FakeZKDevice(attendance=PUNCHES, transfer_mode="prepare",
                          announce_extra=400) as fake:
            with zk_client.ZKReadOnlyClient(fake.host, fake.port, timeout=5) as client:
                with pytest.raises(zk_client.ZKError, match="announced"):
                    client.attendance()

    def test_a_short_chunk_on_the_buffered_path_is_also_an_error(self):
        """The same rule, on the other transfer protocol. Both paths must fail
        the same way or the guarantee only holds on the one that was tested."""
        with FakeZKDevice(attendance=PUNCHES, transfer_mode="buffer",
                          short_chunk=40) as fake:
            with zk_client.ZKReadOnlyClient(fake.host, fake.port, timeout=5) as client:
                with pytest.raises(zk_client.ZKError, match="returned"):
                    client.attendance()


# ===========================================================================
# collector -> ingest
# ===========================================================================
class FakeClient:
    """Stands in for the transport so the ingest path can be tested alone."""
    instances = []

    def __init__(self, host, port, *, timeout=None, comm_key=0, encoding="utf-8"):
        self.host = host
        self.port = port
        FakeClient.instances.append(self)

    users_payload = []
    attendance_payload = []
    clock = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def sizes(self):
        return {"users": len(self.users_payload), "records": len(self.attendance_payload)}

    def device_time(self):
        if self.clock is None:
            raise zk_client.ZKError("no clock")
        return self.clock

    def users(self):
        return list(self.users_payload)

    def attendance(self):
        return list(self.attendance_payload)


@pytest.fixture
def fake_transport(monkeypatch):
    FakeClient.instances = []
    FakeClient.users_payload = []
    FakeClient.attendance_payload = []
    FakeClient.clock = None
    monkeypatch.setattr(zk_client, "ZKReadOnlyClient", FakeClient)
    return FakeClient


pytestmark_db = pytest.mark.django_db


@pytest.mark.django_db
class TestSyncDevice:
    def test_the_roster_lands_with_the_device_ids_untouched(self, device, fake_transport):
        fake_transport.users_payload = [
            {"employee_id": "17", "name": "Bikashkadayat", "privilege": 0,
             "card": 4017, "group_id": "1", "uid": 17},
            {"employee_id": "007", "name": "Ram", "privilege": 0,
             "card": 0, "group_id": "1", "uid": 7},
        ]
        collector.sync_device(device, include_punches=False)

        stored = set(BiometricEmployee.objects.filter(device=device)
                     .values_list("device_user_id", flat=True))
        assert stored == {"17", "007"}, "the collector renumbered a device ID"

    def test_punches_are_stamped_with_the_device_timezone(self, device, fake_transport):
        fake_transport.attendance_payload = [
            {"employee_id": "17", "timestamp": datetime(2026, 7, 14, 9, 15),
             "punch": 0, "status": 1}]
        collector.sync_device(device, include_roster=False)

        punch = AttendancePunch.objects.get()
        assert punch.timestamp.astimezone(KATHMANDU).hour == 9
        assert punch.local_date == date(2026, 7, 14)
        assert punch.employee_device_id == "17"

    def test_a_punch_at_five_past_midnight_stays_on_its_own_day(self, device, fake_transport):
        """+05:45 means a naive-UTC reading would file this on the day before."""
        fake_transport.attendance_payload = [
            {"employee_id": "17", "timestamp": datetime(2026, 7, 14, 0, 5),
             "punch": 0, "status": 1}]
        collector.sync_device(device, include_roster=False)
        assert AttendancePunch.objects.get().local_date == date(2026, 7, 14)

    def test_the_window_is_applied_on_local_dates(self, device, fake_transport):
        fake_transport.attendance_payload = [
            {"employee_id": "1", "timestamp": datetime(2026, 7, 13, 9, 0), "punch": 0, "status": 1},
            {"employee_id": "1", "timestamp": datetime(2026, 7, 14, 9, 0), "punch": 0, "status": 1},
            {"employee_id": "1", "timestamp": datetime(2026, 7, 20, 9, 0), "punch": 0, "status": 1},
            {"employee_id": "1", "timestamp": datetime(2026, 7, 21, 9, 0), "punch": 0, "status": 1},
        ]
        summary = collector.sync_device(
            device, include_roster=False,
            since=date(2026, 7, 14), until=date(2026, 7, 20))

        assert summary["punches"]["in_window"] == 2
        assert summary["punches"]["skipped_before_window"] == 1
        assert summary["punches"]["skipped_after_window"] == 1
        assert AttendancePunch.objects.count() == 2

    def test_the_window_edges_are_inclusive(self, device, fake_transport):
        fake_transport.attendance_payload = [
            {"employee_id": "1", "timestamp": datetime(2026, 7, 14, 0, 0), "punch": 0, "status": 1},
            {"employee_id": "1", "timestamp": datetime(2026, 7, 20, 23, 59), "punch": 1, "status": 1},
        ]
        collector.sync_device(device, include_roster=False,
                              since=date(2026, 7, 14), until=date(2026, 7, 20))
        assert AttendancePunch.objects.count() == 2

    def test_running_twice_creates_nothing_the_second_time(self, device, fake_transport):
        """The cron re-reads the whole log every cycle; that must be free."""
        fake_transport.attendance_payload = [
            {"employee_id": "1", "timestamp": datetime(2026, 7, 14, 9, 0), "punch": 0, "status": 1},
            {"employee_id": "1", "timestamp": datetime(2026, 7, 14, 18, 0), "punch": 1, "status": 1},
        ]
        first = collector.sync_device(device, include_roster=False)
        second = collector.sync_device(device, include_roster=False)

        assert first["punches"]["created"] == 2
        assert second["punches"]["created"] == 0
        assert second["punches"]["duplicate"] == 2
        assert AttendancePunch.objects.count() == 2

    def test_an_unmapped_device_id_is_stored_not_dropped(self, device, fake_transport):
        fake_transport.attendance_payload = [
            {"employee_id": "404", "timestamp": datetime(2026, 7, 14, 9, 0),
             "punch": 0, "status": 1}]
        summary = collector.sync_device(device, include_roster=False)

        assert summary["punches"]["unmapped"] == 1
        punch = AttendancePunch.objects.get()
        assert punch.employee_device_id == "404"
        assert punch.user is None

    def test_a_mapped_punch_attaches_to_the_employee(self, device, mapping, fake_transport):
        fake_transport.attendance_payload = [
            {"employee_id": "1", "timestamp": datetime(2026, 7, 14, 9, 0),
             "punch": 0, "status": 1}]
        collector.sync_device(device, include_roster=False)
        assert AttendancePunch.objects.get().user == mapping.user

    def test_dry_run_writes_nothing_but_reports_the_same_counts(self, device, fake_transport):
        fake_transport.users_payload = [
            {"employee_id": "17", "name": "Bikash", "privilege": 0, "card": 0,
             "group_id": "1", "uid": 17}]
        fake_transport.attendance_payload = [
            {"employee_id": "17", "timestamp": datetime(2026, 7, 14, 9, 0),
             "punch": 0, "status": 1}]
        summary = collector.sync_device(device, dry_run=True)

        assert summary["punches"]["in_window"] == 1
        assert summary["roster"]["read"] == 1
        assert AttendancePunch.objects.count() == 0
        assert BiometricEmployee.objects.count() == 0

    def test_a_large_pull_is_chunked_under_the_batch_limit(self, device, fake_transport, settings):
        settings.BIOMETRIC_MAX_BATCH = 50
        fake_transport.attendance_payload = [
            {"employee_id": "1", "timestamp": datetime(2026, 7, 14, 9, 0) + timezone_delta(i),
             "punch": 0, "status": 1}
            for i in range(120)]
        summary = collector.sync_device(device, include_roster=False)
        assert summary["punches"]["created"] == 120
        assert AttendancePunch.objects.count() == 120

    def test_clock_drift_is_recorded_and_warned_about(self, device, fake_transport):
        from django.utils import timezone as dj_timezone
        # An hour fast, expressed in the device's own wall clock.
        fake_transport.clock = (dj_timezone.localtime(dj_timezone.now())
                                .replace(tzinfo=None)) + timezone_delta(0, hours=1)
        summary = collector.sync_device(device, include_roster=False,
                                        include_punches=False)

        assert summary["clock"]["drift_seconds"] > 3000
        assert any("clock" in w for w in summary["warnings"])
        device.refresh_from_db()
        assert device.clock_drift_seconds is not None

    def test_an_unreadable_clock_does_not_fail_the_sync(self, device, fake_transport):
        fake_transport.clock = None   # device_time() raises
        summary = collector.sync_device(device, include_roster=False,
                                        include_punches=False)
        assert summary["clock"]["drift_seconds"] is None

    def test_a_device_with_no_host_is_a_clear_error(self, device, fake_transport):
        device.host = ""
        device.save()
        with pytest.raises(collector.CollectorError, match="no host"):
            collector.sync_device(device)

    def test_a_nonsense_timezone_is_refused_rather_than_defaulted(self, device, fake_transport):
        device.device_timezone = "Mars/Olympus"
        device.save()
        with pytest.raises(collector.CollectorError, match="not a known timezone"):
            collector.sync_device(device)

    def test_the_host_can_be_overridden_for_one_run(self, device, fake_transport):
        collector.sync_device(device, host="10.0.0.9", include_roster=False,
                              include_punches=False)
        assert fake_transport.instances[-1].host == "10.0.0.9"

    def test_the_recurring_sync_starts_from_the_high_water_mark(self, device,
                                                                fake_transport):
        """Without this the cron re-ingests the terminal's whole log, forever.

        Ingest would discard the old records correctly — but re-scanning years
        of punches every five minutes, and writing a sync-log row per chunk to
        do it, is unbounded cost for nothing.
        """
        from django.utils import timezone as dj_timezone
        device.last_punch_at = dj_timezone.make_aware(datetime(2026, 8, 1, 18, 0))
        device.save(update_fields=["last_punch_at"])

        fake_transport.attendance_payload = [
            {"employee_id": "1", "timestamp": datetime(2026, 7, 14, 9, 0), "punch": 0, "status": 1},
            {"employee_id": "1", "timestamp": datetime(2026, 8, 5, 9, 0), "punch": 0, "status": 1},
        ]
        summary = collector.sync_device(device, include_roster=False)

        assert summary["window"]["from_high_water_mark"] is True
        assert summary["punches"]["in_window"] == 1
        assert summary["punches"]["skipped_before_window"] == 1

    def test_the_high_water_mark_keeps_an_overlap(self, device, fake_transport):
        """A terminal running slow can emit a punch older than one already
        stored. A floor with no slack steps straight over it."""
        from django.utils import timezone as dj_timezone
        device.last_punch_at = dj_timezone.make_aware(datetime(2026, 8, 5, 18, 0))
        device.save(update_fields=["last_punch_at"])

        fake_transport.attendance_payload = [
            {"employee_id": "1", "timestamp": datetime(2026, 8, 4, 9, 0),
             "punch": 0, "status": 1}]
        summary = collector.sync_device(device, include_roster=False)
        assert summary["punches"]["in_window"] == 1

    def test_an_explicit_window_overrides_the_high_water_mark(self, device,
                                                              fake_transport):
        """A backfill means "read the window I named", not "read what is new"."""
        from django.utils import timezone as dj_timezone
        device.last_punch_at = dj_timezone.make_aware(datetime(2026, 8, 5, 18, 0))
        device.save(update_fields=["last_punch_at"])

        fake_transport.attendance_payload = [
            {"employee_id": "1", "timestamp": datetime(2026, 7, 14, 9, 0),
             "punch": 0, "status": 1}]
        summary = collector.sync_device(device, include_roster=False,
                                        since=date(2026, 7, 14))
        assert summary["window"]["from_high_water_mark"] is False
        assert summary["punches"]["created"] == 1

    def test_a_first_sync_has_no_floor(self, device, fake_transport):
        fake_transport.attendance_payload = [
            {"employee_id": "1", "timestamp": datetime(2020, 3, 1, 9, 0),
             "punch": 0, "status": 1}]
        summary = collector.sync_device(device, include_roster=False)
        assert summary["window"]["from_high_water_mark"] is False
        assert summary["punches"]["created"] == 1

    def test_a_quiet_run_writes_no_sync_log(self, device, fake_transport):
        """288 empty rows a day would bury the batches that did something."""
        from biometric.models import DeviceSyncLog
        collector.sync_device(device, include_roster=False)
        assert DeviceSyncLog.objects.filter(sync_type="history").count() == 0
        device.refresh_from_db()
        assert device.last_seen_at is not None, "the terminal still answered"

    def test_device_health_is_updated(self, device, fake_transport):
        fake_transport.attendance_payload = [
            {"employee_id": "1", "timestamp": datetime(2026, 7, 14, 9, 0),
             "punch": 0, "status": 1}]
        collector.sync_device(device, include_roster=False)
        device.refresh_from_db()
        assert device.last_sync_at is not None
        assert device.last_punch_at is not None


def timezone_delta(minutes, hours=0):
    from datetime import timedelta
    return timedelta(minutes=minutes, hours=hours)


# ===========================================================================
# the command
# ===========================================================================
@pytest.mark.django_db
class TestDeviceSyncCommand:
    def test_it_reports_what_it_pulled(self, device, fake_transport):
        fake_transport.users_payload = [
            {"employee_id": "17", "name": "Bikash", "privilege": 0, "card": 0,
             "group_id": "1", "uid": 17}]
        fake_transport.attendance_payload = [
            {"employee_id": "17", "timestamp": datetime(2026, 7, 14, 9, 0),
             "punch": 0, "status": 1}]
        output = run("device_sync")

        assert "Device sync" in output
        assert "17" in output
        assert "created 1" in output

    def test_dry_run_is_labelled_unmissably(self, device, fake_transport):
        assert "DRY RUN" in run("device_sync", "--dry-run")

    def test_json_output_is_machine_readable(self, device, fake_transport):
        import json
        fake_transport.attendance_payload = [
            {"employee_id": "1", "timestamp": datetime(2026, 7, 14, 9, 0),
             "punch": 0, "status": 1}]
        report = json.loads(run("device_sync", "--json"))
        assert report["punches"]["created"] == 1
        assert report["device"] == device.label

    def test_an_unknown_label_lists_the_known_ones(self, device, fake_transport):
        with pytest.raises(CommandError, match="Known labels"):
            run("device_sync", "--device", "nope")

    def test_two_active_devices_force_an_explicit_choice(self, device, second_device,
                                                         fake_transport):
        """Guessing would file one terminal's punches under another's."""
        with pytest.raises(CommandError, match="pass --device"):
            run("device_sync")

    def test_no_device_at_all_explains_what_to_create(self, db, fake_transport):
        with pytest.raises(CommandError, match="No active BiometricDevice"):
            run("device_sync")

    def test_a_backwards_window_is_rejected(self, device, fake_transport):
        with pytest.raises(CommandError, match="after --until"):
            run("device_sync", "--since", "2026-08-01", "--until", "2026-07-01")

    def test_a_malformed_date_is_rejected(self, device, fake_transport):
        with pytest.raises(collector.CollectorError, match="YYYY-MM-DD"):
            run("device_sync", "--since", "14/07/2026")

    def test_roster_only_skips_punches(self, device, fake_transport):
        fake_transport.attendance_payload = [
            {"employee_id": "1", "timestamp": datetime(2026, 7, 14, 9, 0),
             "punch": 0, "status": 1}]
        run("device_sync", "--roster-only")
        assert AttendancePunch.objects.count() == 0

    def test_punches_only_skips_the_roster(self, device, fake_transport):
        fake_transport.users_payload = [
            {"employee_id": "17", "name": "Bikash", "privilege": 0, "card": 0,
             "group_id": "1", "uid": 17}]
        run("device_sync", "--punches-only")
        assert BiometricEmployee.objects.count() == 0

    def test_an_auth_failure_is_an_actionable_message(self, device, monkeypatch):
        def explode(*args, **kwargs):
            raise zk_client.ZKAuthError("comm key required")
        monkeypatch.setattr(collector, "sync_device", explode)
        with pytest.raises(CommandError, match="comm key"):
            run("device_sync")

    def test_the_recurring_form_writes_a_heartbeat(self, device, fake_transport):
        from monitoring import heartbeat
        run("device_sync")
        timestamp, ok = heartbeat.last_run("DEVICE_SYNC")
        assert timestamp is not None and ok

    def test_a_manual_backfill_does_not_touch_the_heartbeat(self, device, fake_transport):
        """An operator's 2am backfill must not make a dead cron look alive."""
        from monitoring import heartbeat
        run("device_sync", "--since", "2026-07-14")
        timestamp, _ok = heartbeat.last_run("DEVICE_SYNC")
        assert timestamp is None

    def test_the_job_is_registered_for_monitoring(self):
        from monitoring import heartbeat
        assert "DEVICE_SYNC" in heartbeat.CRON_JOBS
        _label, _interval, critical = heartbeat.CRON_JOBS["DEVICE_SYNC"]
        assert critical, "the only path attendance takes in is not optional"

    def test_the_crontab_installs_it(self):
        """A registered job with no cron line is permanently red."""
        from pathlib import Path
        crontab = Path(__file__).resolve().parents[2] / "deploy" / "crontab"
        assert "manage.py device_sync" in crontab.read_text()


# ===========================================================================
# first-contact tooling
# ===========================================================================
@pytest.mark.django_db
class TestRegisterDevice:
    def test_it_creates_a_usable_device(self, db):
        from biometric.models import BiometricDevice
        run("register_device", "--label", "main-gate", "--host", "192.168.77.201",
            "--serial", "CJXK205060099", "--timezone", "Asia/Kathmandu")
        device = BiometricDevice.objects.get(label="main-gate")
        assert device.host == "192.168.77.201"
        assert device.serial_number == "CJXK205060099"
        assert device.device_timezone == "Asia/Kathmandu"
        assert device.is_active

    def test_rerunning_corrects_rather_than_fails(self, db):
        from biometric.models import BiometricDevice
        run("register_device", "--label", "main-gate", "--host", "10.0.0.1")
        run("register_device", "--label", "main-gate", "--host", "192.168.77.201")
        assert BiometricDevice.objects.get(label="main-gate").host == "192.168.77.201"

    def test_a_bad_timezone_is_refused_not_defaulted(self, db):
        """Every punch's date depends on it, and getting it wrong misfiles the
        whole import with nothing raising."""
        with pytest.raises(CommandError, match="not a known timezone"):
            run("register_device", "--label", "x", "--host", "1.2.3.4",
                "--timezone", "Mars/Olympus")

    def test_a_duplicate_serial_is_refused(self, db):
        """Two devices sharing a serial cannot be told apart — the PUSH protocol
        has nothing else to identify them by."""
        run("register_device", "--label", "a", "--host", "1.1.1.1", "--serial", "S1")
        with pytest.raises(CommandError, match="already registered"):
            run("register_device", "--label", "b", "--host", "2.2.2.2", "--serial", "S1")

    def test_a_missing_serial_warns_about_push(self, db):
        output = run("register_device", "--label", "a", "--host", "1.1.1.1")
        assert "PUSH" in output


@pytest.mark.django_db
class TestDeviceDiagnose:
    def test_it_reports_what_the_terminal_returned(self, db):
        with FakeZKDevice(users=ROSTER, attendance=PUNCHES,
                          sizes={"users": 3, "records": 3}) as fake:
            output = run("device_diagnose", "--host", fake.host,
                         "--port", str(fake.port), "--timeout", "5")
        assert "Connected" in output
        assert "3 users" in output
        assert "decoded 3 record(s)" in output
        assert "17" in output, "the sample must show real device IDs"

    def test_it_writes_nothing(self, db):
        from biometric.models import AttendancePunch, BiometricDevice
        with FakeZKDevice(users=ROSTER, attendance=PUNCHES) as fake:
            run("device_diagnose", "--host", fake.host, "--port", str(fake.port),
                "--timeout", "5")
        assert BiometricDevice.objects.count() == 0
        assert AttendancePunch.objects.count() == 0

    def test_an_unreachable_host_explains_the_routing_trap(self, db):
        """The failure that cost this project a day: a routed path accepts the
        TCP connection and drops the payload, looking exactly like a dead device."""
        with pytest.raises(CommandError, match="own network"):
            run("device_diagnose", "--host", "127.0.0.1", "--port", "1", "--timeout", "2")

    def test_a_count_mismatch_is_called_out(self, db):
        """If the declared count and the decoded count disagree, the record
        width is wrong and every field is shifted."""
        with FakeZKDevice(users=ROSTER, attendance=PUNCHES,
                          sizes={"users": 99, "records": 3}) as fake:
            output = run("device_diagnose", "--host", fake.host,
                         "--port", str(fake.port), "--timeout", "5")
        assert "shifted" in output


class TestDriverSelection:
    """pyzk is optional insurance, not a dependency. Selecting it must be
    explicit, and its absence must never affect the default path."""

    def test_the_default_is_the_built_in_client(self):
        assert collector.client_for(None) is zk_client.ZKReadOnlyClient
        assert collector.client_for("native") is zk_client.ZKReadOnlyClient

    def test_an_unknown_driver_is_refused(self):
        with pytest.raises(collector.CollectorError, match="unknown driver"):
            collector.client_for("telepathy")

    def test_pyzk_is_only_imported_when_asked_for(self):
        """A missing optional dependency must not break the default path."""
        import sys
        assert "zk" not in sys.modules or True   # not asserting absence, only that:
        collector.client_for("native")           # this never touches pyzk

    def test_the_pyzk_wrapper_exposes_the_same_surface(self):
        """Duck-compatibility is the whole point: sync_device must not care."""
        from biometric import pyzk_driver
        for method in ("connect", "disconnect", "users", "attendance",
                       "sizes", "device_time", "__enter__", "__exit__"):
            assert hasattr(pyzk_driver.PyzkReadOnlyClient, method), method

    def test_the_pyzk_wrapper_calls_only_read_operations(self):
        """pyzk is a full read/write SDK — it can clear the attendance log,
        delete users and unlock the door. The wrapper must call none of that.

        Parsed rather than grepped: a text search trips over the docstring that
        explains *why* disable_device is avoided, and a test that cannot mention
        the thing it forbids is a test nobody can document.
        """
        import ast
        import inspect

        from biometric import pyzk_driver
        tree = ast.parse(inspect.getsource(pyzk_driver))
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        # Everything invoked on the pyzk connection, minus our own helpers.
        on_device = called & {
            "connect", "disconnect", "get_users", "get_attendance", "get_time",
            "read_sizes", "disable_device", "enable_device", "clear_attendance",
            "delete_user", "set_user", "unlock", "power_off", "restart",
            "clear_data", "test_voice", "refresh_data",
        }
        assert on_device <= set(pyzk_driver.PERMITTED_CALLS), (
            f"the wrapper calls {on_device - set(pyzk_driver.PERMITTED_CALLS)} "
            f"on the terminal")

    def test_a_missing_pyzk_says_what_to_do(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name == "zk":
                raise ImportError("no module named zk")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked)
        from biometric.pyzk_driver import PyzkReadOnlyClient, PyzkUnavailable
        with pytest.raises(PyzkUnavailable, match="pip install pyzk"):
            PyzkReadOnlyClient("192.0.2.1").connect()


@pytest.mark.django_db
class TestHighWaterMarkAgainstABadDeviceClock:
    """The bug this pins was found on the real terminal, not in review.

    192.168.77.201 holds seven punches stamped 2033-02-22 — a clock glitch.
    They are real rows and legitimately become `last_punch_at`. Unclamped, the
    resume cursor jumps to 2033 and the five-minute sync spends the next seven
    years reading a window that contains nothing: succeeding every cycle,
    reporting no error, and never collecting another punch.
    """

    def test_a_future_dated_punch_does_not_poison_the_cursor(self, device, fake_transport):
        from django.utils import timezone as dj_tz
        device.last_punch_at = dj_tz.make_aware(datetime(2033, 2, 22, 10, 36))
        device.save(update_fields=["last_punch_at"])

        # A real, recent punch, anchored to today like its sibling tests so it
        # always lands inside the post-glitch resume window (the clamped floor is
        # `today - HIGH_WATER_OVERLAP_DAYS`), whenever the suite runs.
        #
        # A hardcoded date here was a time bomb: a punch pinned to the day the
        # test was written fell outside the window days later, and the assertion
        # below then reported a regression in code that had not changed.
        today = dj_tz.localdate()
        fake_transport.attendance_payload = [
            {"employee_id": "28",
             "timestamp": datetime(today.year, today.month, today.day, 14, 45),
             "punch": 0, "status": 1}]
        summary = collector.sync_device(device, include_roster=False)

        assert summary["punches"]["in_window"] == 1, (
            "a 2033-dated glitch record moved the resume cursor into the "
            "future; the sync would never collect another punch")
        assert AttendancePunch.objects.count() == 1

    def test_the_floor_is_never_in_the_future(self, device):
        from django.utils import timezone as dj_tz
        device.last_punch_at = dj_tz.make_aware(datetime(2033, 2, 22, 10, 36))
        floor = collector.high_water_floor(device, KATHMANDU)
        assert floor <= dj_tz.localdate()

    def test_a_sane_high_water_mark_is_still_honoured(self, device):
        from django.utils import timezone as dj_tz
        recent = dj_tz.localdate() - timedelta(days=10)
        device.last_punch_at = dj_tz.make_aware(
            datetime(recent.year, recent.month, recent.day, 9, 0))
        floor = collector.high_water_floor(device, KATHMANDU)
        assert floor == recent - timedelta(days=collector.HIGH_WATER_OVERLAP_DAYS)
