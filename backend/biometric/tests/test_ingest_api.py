"""Device ingest endpoints: auth, HMAC, idempotency, timezone, roster."""
import hashlib
import hmac
import json
import time
from datetime import date, datetime, timedelta

import pytest
from django.utils import timezone

from attendance.models import Attendance
from biometric.models import AttendancePunch, BiometricEmployee, DeviceSyncLog
from biometric.services import map_employee, rotate_device_api_key

pytestmark = pytest.mark.django_db

PUNCH = "/api/v1/biometric/punch/"
BULK = "/api/v1/biometric/bulk-sync/"
ROSTER = "/api/v1/biometric/roster-sync/"


@pytest.fixture
def device_key(device):
    return rotate_device_api_key(device)


def sign_headers(raw_key, method, path, body, timestamp=None, secret=None):
    """Mirror of what morx/api_client.py produces."""
    timestamp = str(timestamp if timestamp is not None else int(time.time()))
    secret = secret or hashlib.sha256(raw_key.encode()).hexdigest()
    signing_string = f"{method.upper()}\n{path}\n{timestamp}\n{hashlib.sha256(body).hexdigest()}"
    signature = hmac.new(secret.encode(), signing_string.encode(), hashlib.sha256).hexdigest()
    return {
        "HTTP_X_API_KEY": raw_key,
        "HTTP_X_TIMESTAMP": timestamp,
        "HTTP_X_SIGNATURE": signature,
    }


def post(api, path, payload, raw_key, **overrides):
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = sign_headers(raw_key, "POST", path, body, **overrides)
    return api.post(path, data=body, content_type="application/json", **headers)


def punch_payload(hh=9, mm=15, employee_id="1", punch=0, day=date(2026, 8, 3)):
    ts = timezone.make_aware(datetime(day.year, day.month, day.day, hh, mm))
    return {"employee_id": employee_id, "name": "Bikash",
            "timestamp": ts.isoformat(), "punch": punch, "status": 1}


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

def test_no_credentials_is_rejected(api):
    assert api.post(BULK, {"punches": []}, format="json").status_code == 401


def test_unknown_key_is_rejected(api, device, device_key):
    resp = post(api, BULK, {"punches": []}, "nifbio_not-a-real-key")
    assert resp.status_code == 401
    assert "Unknown or inactive device" in str(resp.data)


def test_revoked_key_is_rejected(api, device, device_key):
    rotate_device_api_key(device)  # issue a new one; the old must die
    assert post(api, BULK, {"punches": []}, device_key).status_code == 401


def test_inactive_device_is_rejected(api, device, device_key):
    device.is_active = False
    device.save(update_fields=["is_active"])
    assert post(api, BULK, {"punches": []}, device_key).status_code == 401


def test_valid_key_is_accepted(api, device, device_key):
    assert post(api, BULK, {"punches": []}, device_key).status_code == 200


def test_jwt_users_cannot_reach_the_ingest_endpoints(auth, hr):
    """HR holds a JWT, not a device key — ingest is machine-only."""
    # 403, not 401: the JWT authenticated fine, it simply is not a device.
    assert auth(hr).post(BULK, {"punches": []}, format="json").status_code == 403


# --------------------------------------------------------------------------
# HMAC signature
# --------------------------------------------------------------------------

def test_missing_signature_headers_are_rejected(api, device, device_key):
    resp = api.post(BULK, {"punches": []}, format="json", HTTP_X_API_KEY=device_key)
    assert resp.status_code == 401
    assert "X-TIMESTAMP and X-SIGNATURE" in str(resp.data)


def test_wrong_signature_is_rejected(api, device, device_key):
    body = json.dumps({"punches": []}).encode()
    headers = sign_headers(device_key, "POST", BULK, body)
    headers["HTTP_X_SIGNATURE"] = "0" * 64
    assert api.post(BULK, data=body, content_type="application/json",
                    **headers).status_code == 401


def test_tampered_body_invalidates_the_signature(api, device, device_key):
    """Sign an empty batch, then send a real one under that signature."""
    signed_body = json.dumps({"punches": []}, separators=(",", ":")).encode()
    headers = sign_headers(device_key, "POST", BULK, signed_body)
    tampered = json.dumps({"punches": [punch_payload()]}, separators=(",", ":")).encode()

    assert api.post(BULK, data=tampered, content_type="application/json",
                    **headers).status_code == 401
    assert AttendancePunch.objects.count() == 0


def test_signature_from_another_endpoint_is_rejected(api, device, device_key):
    """Path is inside the signing string, so a /punch/ signature is useless here."""
    payload = {"punches": []}
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = sign_headers(device_key, "POST", PUNCH, body)  # wrong path
    assert api.post(BULK, data=body, content_type="application/json",
                    **headers).status_code == 401


def test_signature_made_with_the_raw_key_is_rejected(api, device, device_key):
    """The shared secret is sha256(key), not the key itself."""
    payload = {"punches": []}
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = sign_headers(device_key, "POST", BULK, body, secret=device_key)
    assert api.post(BULK, data=body, content_type="application/json",
                    **headers).status_code == 401


# --------------------------------------------------------------------------
# Replay protection
# --------------------------------------------------------------------------

def test_stale_timestamp_is_rejected(api, device, device_key):
    old = int(time.time()) - 3600
    resp = post(api, BULK, {"punches": []}, device_key, timestamp=old)
    assert resp.status_code == 401
    assert "out of date" in str(resp.data)


def test_future_timestamp_is_rejected(api, device, device_key):
    ahead = int(time.time()) + 3600
    assert post(api, BULK, {"punches": []}, device_key, timestamp=ahead).status_code == 401


def test_timestamp_inside_the_window_is_accepted(api, device, device_key, settings):
    settings.BIOMETRIC_CLOCK_SKEW_SECONDS = 300
    recent = int(time.time()) - 120
    assert post(api, BULK, {"punches": []}, device_key, timestamp=recent).status_code == 200


def test_non_numeric_timestamp_is_rejected(api, device, device_key):
    body = json.dumps({"punches": []}).encode()
    headers = sign_headers(device_key, "POST", BULK, body)
    headers["HTTP_X_TIMESTAMP"] = "not-a-number"
    assert api.post(BULK, data=body, content_type="application/json",
                    **headers).status_code == 401


def test_replaying_a_valid_request_creates_no_duplicates(api, device, device_key, mapping, employee):
    """Replay is harmless by construction: the UNIQUE constraint absorbs it."""
    payload = {"punches": [punch_payload()], "source": "LIVE"}
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = sign_headers(device_key, "POST", BULK, body)

    first = api.post(BULK, data=body, content_type="application/json", **headers)
    replay = api.post(BULK, data=body, content_type="application/json", **headers)

    assert (first.status_code, replay.status_code) == (200, 200)
    assert first.data["created"] == 1
    assert replay.data["created"] == 0 and replay.data["duplicate"] == 1
    assert AttendancePunch.objects.count() == 1


# --------------------------------------------------------------------------
# Bulk sync
# --------------------------------------------------------------------------

def test_bulk_sync_stores_punches_and_derives_attendance(api, device, device_key,
                                                         mapping, employee):
    payload = {"punches": [punch_payload(9, 0), punch_payload(18, 0, punch=1)],
               "source": "HISTORY", "queue_depth": 7}
    resp = post(api, BULK, payload, device_key)

    assert resp.status_code == 200
    assert resp.data["received"] == 2 and resp.data["created"] == 2
    assert AttendancePunch.objects.count() == 2

    att = Attendance.objects.get(employee=employee, date=date(2026, 8, 3))
    assert att.source == Attendance.Source.BIOMETRIC
    assert str(att.working_hours) == "9.00"
    assert att.punch_count == 2


def test_unmapped_punches_are_stored_not_dropped(api, device, device_key):
    resp = post(api, BULK, {"punches": [punch_payload(employee_id="99")]}, device_key)
    assert resp.data["created"] == 1
    assert resp.data["unmapped"] == 1
    assert AttendancePunch.objects.filter(employee_device_id="99", user__isnull=True).count() == 1


def test_batch_over_the_limit_is_rejected(api, device, device_key, settings):
    settings.BIOMETRIC_MAX_BATCH = 5
    resp = post(api, BULK, {"punches": [punch_payload(mm=i) for i in range(6)]}, device_key)
    assert resp.status_code == 400
    assert "Batch too large" in str(resp.data)
    assert AttendancePunch.objects.count() == 0


def test_repeated_punch_inside_one_payload_counts_once(api, device, device_key, mapping):
    same = punch_payload()
    resp = post(api, BULK, {"punches": [same, same, same]}, device_key)
    assert resp.data["created"] == 1
    assert resp.data["duplicate"] == 2
    assert AttendancePunch.objects.count() == 1


def test_empty_batch_is_accepted(api, device, device_key):
    resp = post(api, BULK, {"punches": []}, device_key)
    assert resp.status_code == 200 and resp.data["received"] == 0


def test_full_backlog_replay_is_free_the_second_time(api, device, device_key, mapping):
    batch = {"punches": [punch_payload(mm=i) for i in range(50)], "source": "HISTORY"}
    first = post(api, BULK, batch, device_key)
    second = post(api, BULK, batch, device_key)
    assert first.data["created"] == 50
    assert second.data["created"] == 0 and second.data["duplicate"] == 50
    assert AttendancePunch.objects.count() == 50


# --------------------------------------------------------------------------
# Single punch
# --------------------------------------------------------------------------

def test_single_punch_endpoint(api, device, device_key, mapping, employee):
    resp = post(api, PUNCH, {**punch_payload(), "source": "LIVE", "queue_depth": 0}, device_key)
    assert resp.status_code == 201
    assert resp.data["created"] == 1
    assert AttendancePunch.objects.get().source == AttendancePunch.Source.LIVE
    assert Attendance.objects.filter(employee=employee).exists()


def test_duplicate_single_punch_returns_200_not_201(api, device, device_key, mapping):
    payload = {**punch_payload(), "source": "LIVE"}
    assert post(api, PUNCH, payload, device_key).status_code == 201
    second = post(api, PUNCH, payload, device_key)
    assert second.status_code == 200 and second.data["duplicate"] == 1


# --------------------------------------------------------------------------
# Timezone validation
# --------------------------------------------------------------------------

def test_naive_timestamp_is_rejected(api, device, device_key):
    """USE_TZ=True would read a naive value as UTC — 5h45m out for Kathmandu."""
    bad = {**punch_payload(), "timestamp": "2026-08-03T09:15:00"}
    resp = post(api, BULK, {"punches": [bad]}, device_key)
    assert resp.status_code == 400
    assert "UTC offset" in str(resp.data)
    assert AttendancePunch.objects.count() == 0


def test_offset_timestamp_is_stored_at_the_right_local_time(api, device, device_key, mapping):
    payload = {"employee_id": "1", "name": "B", "punch": 0, "status": 1,
               "timestamp": "2026-08-03T00:15:00+05:45"}
    post(api, BULK, {"punches": [payload]}, device_key)
    stored = AttendancePunch.objects.get()
    assert stored.local_date == date(2026, 8, 3), "00:15 KTM belongs to that day"
    assert timezone.localtime(stored.timestamp).strftime("%H:%M") == "00:15"


def test_far_future_timestamp_is_rejected(api, device, device_key):
    future = (timezone.now() + timedelta(days=5)).isoformat()
    resp = post(api, BULK, {"punches": [{**punch_payload(), "timestamp": future}]}, device_key)
    assert resp.status_code == 400
    assert "device clock" in str(resp.data)


def test_blank_employee_id_is_rejected(api, device, device_key):
    resp = post(api, BULK, {"punches": [{**punch_payload(), "employee_id": "  "}]}, device_key)
    assert resp.status_code == 400


def test_unknown_punch_code_is_accepted_and_labelled(api, device, device_key, mapping):
    """Clone firmware invents codes; dropping them would lose real attendance."""
    resp = post(api, BULK, {"punches": [punch_payload(punch=9)]}, device_key)
    assert resp.status_code == 200
    assert AttendancePunch.objects.get().punch_label == "unknown_9"


# ----------------
# Roster sync
# ----------------

def test_roster_sync_creates_unmapped_rows(api, device, device_key):
    resp = post(api, ROSTER, {"employees": [
        {"employee_id": "1", "name": "Bikash", "privilege": 0, "card": 0, "group_id": ""},
        {"employee_id": "2", "name": "Sita", "privilege": 14, "card": 55, "group_id": ""},
    ]}, device_key)

    assert resp.status_code == 200
    assert resp.data["created"] == 2
    assert resp.data["unmapped_total"] == 2
    assert BiometricEmployee.objects.filter(device=device, user__isnull=True).count() == 2


def test_roster_sync_never_auto_maps_an_employee(api, device, device_key, employee):
    """Name matching is a suggestion for HR, never an automatic decision."""
    resp = post(api, ROSTER, {"employees": [
        {"employee_id": "1", "name": employee.get_full_name()}]}, device_key)
    assert resp.status_code == 200
    assert BiometricEmployee.objects.get(device_user_id="1").user is None


def test_roster_sync_is_idempotent(api, device, device_key):
    payload = {"employees": [{"employee_id": "1", "name": "Bikash"}]}
    post(api, ROSTER, payload, device_key)
    second = post(api, ROSTER, payload, device_key)
    assert second.data["created"] == 0
    assert BiometricEmployee.objects.count() == 1


def test_roster_sync_updates_a_renamed_employee_without_unmapping(
        api, device, device_key, mapping, employee, hr):
    map_employee(mapping, employee, actor=hr)
    post(api, ROSTER, {"employees": [
        {"employee_id": mapping.device_user_id, "name": "Bikash Renamed"}]}, device_key)

    mapping.refresh_from_db()
    assert mapping.device_name == "Bikash Renamed"
    assert mapping.user == employee, "a roster sync must never break an HR mapping"


# --------------------------------------------------------------------------
# DeviceSyncLog / metrics
# --------------------------------------------------------------------------

def test_sync_log_records_the_batch(api, device, device_key, mapping):
    post(api, BULK, {"punches": [punch_payload(), punch_payload(employee_id="99")],
                     "queue_depth": 12}, device_key)
    log = DeviceSyncLog.objects.filter(sync_type=DeviceSyncLog.SyncType.HISTORY).first()
    assert log.records_received == 2
    assert log.records_created == 2
    assert log.records_unmapped == 1
    assert log.queue_depth == 12
    assert log.status == DeviceSyncLog.Status.PARTIAL, "unmapped rows must not read as clean"


def test_device_health_metrics_are_updated(api, device, device_key, mapping):
    post(api, BULK, {"punches": [punch_payload()], "queue_depth": 5}, device_key)
    device.refresh_from_db()
    assert device.successful_batches == 1
    assert device.failed_batches == 0
    assert device.pending_punches == 5
    assert device.last_sync_at is not None
    assert device.last_punch_at is not None
    assert device.connection_status == device.Status.ONLINE


def test_failed_batches_are_recorded(api, device, device_key):
    post(api, BULK, {"punches": [{"employee_id": "1"}]}, device_key)  # invalid payload
    device.refresh_from_db()
    assert device.failed_batches == 1
    assert DeviceSyncLog.objects.filter(status=DeviceSyncLog.Status.FAILED).exists()


def test_client_ip_is_captured(api, device, device_key):
    body = json.dumps({"punches": []}, separators=(",", ":")).encode()
    headers = sign_headers(device_key, "POST", BULK, body)
    api.post(BULK, data=body, content_type="application/json",
             HTTP_X_FORWARDED_FOR="203.0.113.9, 10.0.0.1", **headers)
    assert DeviceSyncLog.objects.first().client_ip == "203.0.113.9"
