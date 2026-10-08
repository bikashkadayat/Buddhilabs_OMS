from datetime import datetime

from morx.models import AttendanceRecord, Employee


def test_punch_label_maps_known_codes(punch):
    assert punch(punch=0).punch_label == "check_in"
    assert punch(punch=1).punch_label == "check_out"


def test_unknown_punch_code_is_kept_not_dropped(punch):
    # Firmware varies; an unrecognised code must still round-trip.
    assert punch(punch=99).punch_label == "unknown_99"


def test_dedup_key_ignores_source(punch):
    """The same event seen in the backlog and live must collapse to one row."""
    live = punch(source="LIVE")
    history = punch(source="HISTORY")
    assert live.dedup_key == history.dedup_key


def test_dedup_key_separates_devices(punch):
    assert punch(device="gate").dedup_key != punch(device="warehouse").dedup_key


def test_dedup_key_separates_punch_types(punch):
    """Check-in and check-out at the same second are two real events."""
    assert punch(punch=0).dedup_key != punch(punch=1).dedup_key


class FakeUser:
    uid = 7
    user_id = "  42 "
    name = " Bob "
    privilege = 14
    card = 0
    group_id = 1


class FakeAttendance:
    user_id = " 42 "
    timestamp = datetime(2026, 8, 3, 9, 30)
    status = 1
    punch = 0


def test_from_zk_strips_device_whitespace():
    """Devices pad fixed-width fields; untrimmed ids break every join downstream."""
    assert Employee.from_zk(FakeUser(), "gate").employee_id == "42"
    assert Employee.from_zk(FakeUser(), "gate").name == "Bob"
    assert AttendanceRecord.from_zk(FakeAttendance(), "Bob", "LIVE", "gate").employee_id == "42"
