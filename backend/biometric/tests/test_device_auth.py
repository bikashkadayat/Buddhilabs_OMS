"""Device credential handling.

A leaked or forgeable device key means forged attendance, so the guarantees
here are security-relevant: keys are never recoverable, never stored in the
clear, and a rotated key stops working immediately.
"""
import pytest

from biometric.models import (
    API_KEY_PREFIX,
    API_KEY_PREFIX_LENGTH,
    BiometricDevice,
    DeviceSyncLog,
    generate_api_key,
    hash_api_key,
)
from biometric.services import (
    finish_sync_log,
    resolve_device_by_key,
    rotate_device_api_key,
    start_sync_log,
    touch_device,
)

pytestmark = pytest.mark.django_db


def test_generated_keys_are_unique_and_prefixed():
    keys = {generate_api_key() for _ in range(200)}
    assert len(keys) == 200
    assert all(k.startswith(API_KEY_PREFIX) for k in keys)
    assert all(len(k) > 40 for k in keys)


def test_raw_key_is_never_stored(device):
    raw = rotate_device_api_key(device)
    device.refresh_from_db()
    assert raw not in (device.api_key_hash, device.api_key_prefix)
    # The stored value is a digest, and the digest is not reversible to the key.
    assert device.api_key_hash == hash_api_key(raw)
    assert len(device.api_key_hash) == 64


def test_check_api_key_accepts_only_the_real_key(device):
    raw = rotate_device_api_key(device)
    assert device.check_api_key(raw)
    assert not device.check_api_key(raw + "x")
    assert not device.check_api_key(raw[:-1])
    assert not device.check_api_key("")
    assert not device.check_api_key(None)


def test_device_without_a_key_rejects_everything(device):
    assert not device.has_api_key
    assert not device.check_api_key("anything")
    assert not device.check_api_key("")


def test_resolve_device_by_key(device):
    raw = rotate_device_api_key(device)
    assert resolve_device_by_key(raw) == device
    assert resolve_device_by_key(generate_api_key()) is None
    assert resolve_device_by_key("") is None
    assert resolve_device_by_key(None) is None


def test_rotation_invalidates_the_previous_key(device):
    old = rotate_device_api_key(device)
    new = rotate_device_api_key(device)
    assert old != new
    assert resolve_device_by_key(old) is None
    assert resolve_device_by_key(new) == device


def test_inactive_device_cannot_authenticate(device):
    """Deactivating a device must revoke it, not just hide it from lists."""
    raw = rotate_device_api_key(device)
    device.is_active = False
    device.save(update_fields=["is_active"])
    assert resolve_device_by_key(raw) is None


def test_keys_resolve_to_the_right_device_when_prefixes_collide(device, second_device):
    """Prefix lookup is only an index selector — the hash decides.

    Two keys sharing their first 14 characters land in the same candidate set,
    so resolution must still route each to its own device.
    """
    shared = f"{API_KEY_PREFIX}SHARED7"
    assert len(shared) == API_KEY_PREFIX_LENGTH
    raw_a, raw_b = f"{shared}-alpha-secret-aaaa", f"{shared}-bravo-secret-bbbb"
    for dev, raw in ((device, raw_a), (second_device, raw_b)):
        dev.set_api_key(raw)
        dev.save(update_fields=["api_key_prefix", "api_key_hash", "api_key_set_at"])

    assert device.api_key_prefix == second_device.api_key_prefix  # genuine collision
    assert resolve_device_by_key(raw_a) == device
    assert resolve_device_by_key(raw_b) == second_device
    assert resolve_device_by_key(f"{shared}-charlie-nope") is None


def test_touch_device_records_liveness(device):
    touch_device(device, seen=True, synced=True, drift_seconds=-12)
    device.refresh_from_db()
    assert device.last_seen_at is not None
    assert device.last_sync_at is not None
    assert device.clock_drift_seconds == -12
    assert device.connection_status == BiometricDevice.Status.ONLINE


# --------------------------------------------------------------------------
# Sync log outcome classification
# --------------------------------------------------------------------------

def test_clean_batch_is_success(device):
    log = finish_sync_log(
        start_sync_log(device, DeviceSyncLog.SyncType.HISTORY),
        received=10, created=10)
    assert log.status == DeviceSyncLog.Status.SUCCESS
    assert log.duration_seconds is not None


def test_duplicates_alone_still_count_as_success(device):
    """A re-sync of an unchanged backlog is the normal case, not a problem."""
    log = finish_sync_log(
        start_sync_log(device, DeviceSyncLog.SyncType.HISTORY),
        received=10, created=0, duplicate=10)
    assert log.status == DeviceSyncLog.Status.SUCCESS


def test_unmapped_records_downgrade_the_batch_to_partial(device):
    """Silently dropping unmapped punches is how attendance goes missing —
    the batch must not report clean success."""
    log = finish_sync_log(
        start_sync_log(device, DeviceSyncLog.SyncType.HISTORY),
        received=10, created=8, unmapped=2)
    assert log.status == DeviceSyncLog.Status.PARTIAL


def test_invalid_records_downgrade_the_batch_to_partial(device):
    log = finish_sync_log(
        start_sync_log(device, DeviceSyncLog.SyncType.LIVE),
        received=5, created=4, invalid=1)
    assert log.status == DeviceSyncLog.Status.PARTIAL


def test_error_marks_the_batch_failed(device):
    log = finish_sync_log(
        start_sync_log(device, DeviceSyncLog.SyncType.LIVE),
        received=5, error="device unreachable")
    assert log.status == DeviceSyncLog.Status.FAILED
    assert log.error == "device unreachable"
