"""Mapping API — CRUD, unmapped queue, suggestions, backfill, permissions."""
from datetime import date

import pytest
from django.utils import timezone

from audit.models import AuditLog
from biometric.models import AttendancePunch, BiometricEmployee

from .conftest import make_user

pytestmark = pytest.mark.django_db

MAPPINGS = "/api/v1/biometric/mappings/"
UNMAPPED = "/api/v1/biometric/unmapped/"
SUGGESTIONS = "/api/v1/biometric/suggestions/"


@pytest.fixture
def unmapped_row(device):
    return BiometricEmployee.objects.create(
        device=device, device_user_id="17", device_name="Bikash")


def _punch(device, d, device_user_id="17"):
    return AttendancePunch.objects.create(
        device=device, employee_device_id=device_user_id,
        timestamp=timezone.make_aware(timezone.datetime(d.year, d.month, d.day, 9, 0)),
        punch=0,
    )


# --------------------------------------------------------------------------
# Permissions — the gate on every endpoint
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url", [MAPPINGS, UNMAPPED, SUGGESTIONS])
def test_anonymous_is_rejected(api, url):
    assert api.get(url).status_code == 401


@pytest.mark.parametrize("url", [MAPPINGS, UNMAPPED, SUGGESTIONS])
def test_plain_employee_is_forbidden(auth, employee, url):
    assert auth(employee).get(url).status_code == 403


@pytest.mark.parametrize("url", [MAPPINGS, UNMAPPED, SUGGESTIONS])
def test_department_head_is_forbidden(auth, dept_head, url):
    """Checker manages their department's leave, not biometric identity."""
    assert auth(dept_head).get(url).status_code == 403


@pytest.mark.parametrize("url", [MAPPINGS, UNMAPPED, SUGGESTIONS])
def test_hr_and_admin_are_allowed(auth, hr, admin_user, url):
    assert auth(hr).get(url).status_code == 200
    assert auth(admin_user).get(url).status_code == 200


def test_employee_cannot_map_or_backfill(auth, employee, unmapped_row, other_employee):
    client = auth(employee)
    assert client.post(f"{MAPPINGS}{unmapped_row.pk}/map/",
                       {"user": str(other_employee.pk)}, format="json").status_code == 403
    assert client.post(f"{MAPPINGS}{unmapped_row.pk}/backfill/", {}, format="json").status_code == 403
    assert client.delete(f"{MAPPINGS}{unmapped_row.pk}/").status_code == 403


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------

def test_list_and_filter_mappings(auth, hr, device, unmapped_row, employee):
    BiometricEmployee.objects.create(device=device, device_user_id="2", user=employee)
    client = auth(hr)

    assert client.get(MAPPINGS).data["count"] == 2
    assert client.get(f"{MAPPINGS}?mapped=false").data["count"] == 1
    assert client.get(f"{MAPPINGS}?mapped=true").data["count"] == 1
    assert client.get(f"{MAPPINGS}?device={device.pk}").data["count"] == 2


def test_create_mapping(auth, hr, device, employee):
    resp = auth(hr).post(MAPPINGS, {
        "device": str(device.pk), "device_user_id": "42",
        "user": str(employee.pk), "effective_from": "2026-01-01",
    }, format="json")
    assert resp.status_code == 201
    assert resp.data["is_mapped"] is True
    assert resp.data["user_detail"]["full_name"] == employee.get_full_name()
    assert AuditLog.objects.filter(changes__event="BIOMETRIC_MAPPING_CREATED").exists()


def test_map_action_attaches_an_employee(auth, hr, unmapped_row, employee):
    resp = auth(hr).post(f"{MAPPINGS}{unmapped_row.pk}/map/", {
        "user": str(employee.pk), "effective_from": "2026-08-01",
    }, format="json")
    assert resp.status_code == 200
    unmapped_row.refresh_from_db()
    assert unmapped_row.user == employee
    assert unmapped_row.effective_from == date(2026, 8, 1)
    assert unmapped_row.mapped_by == hr


def test_duplicate_active_mapping_for_one_employee_is_rejected(auth, hr, device,
                                                               unmapped_row, employee):
    client = auth(hr)
    client.post(f"{MAPPINGS}{unmapped_row.pk}/map/", {"user": str(employee.pk)}, format="json")
    other = BiometricEmployee.objects.create(device=device, device_user_id="18")

    resp = client.post(f"{MAPPINGS}{other.pk}/map/", {"user": str(employee.pk)}, format="json")
    assert resp.status_code == 400
    assert "already mapped to device ID 17" in str(resp.data)


def test_mapping_an_already_mapped_device_id_requires_remap(auth, hr, unmapped_row,
                                                            employee, other_employee):
    client = auth(hr)
    client.post(f"{MAPPINGS}{unmapped_row.pk}/map/", {"user": str(employee.pk)}, format="json")
    resp = client.post(f"{MAPPINGS}{unmapped_row.pk}/map/",
                       {"user": str(other_employee.pk)}, format="json")
    assert resp.status_code == 400
    assert "remap" in str(resp.data).lower()


def test_remap_retires_the_old_row_and_creates_a_successor(auth, hr, unmapped_row,
                                                           employee, other_employee):
    client = auth(hr)
    client.post(f"{MAPPINGS}{unmapped_row.pk}/map/", {"user": str(employee.pk)}, format="json")

    resp = client.post(f"{MAPPINGS}{unmapped_row.pk}/remap/",
                       {"user": str(other_employee.pk)}, format="json")
    assert resp.status_code == 201
    unmapped_row.refresh_from_db()
    assert unmapped_row.is_active is False
    assert unmapped_row.user == employee, "retired row keeps its history"
    assert unmapped_row.superseded_by_id is not None
    assert str(unmapped_row.superseded_by_id) == resp.data["id"]
    assert AuditLog.objects.filter(changes__event="BIOMETRIC_MAPPING_REMAPPED").exists()


def test_multi_device_mapping_is_allowed_via_api(auth, hr, device, second_device, employee):
    client = auth(hr)
    a = client.post(MAPPINGS, {"device": str(device.pk), "device_user_id": "17",
                               "user": str(employee.pk)}, format="json")
    b = client.post(MAPPINGS, {"device": str(second_device.pk), "device_user_id": "4",
                               "user": str(employee.pk)}, format="json")
    assert (a.status_code, b.status_code) == (201, 201)
    assert BiometricEmployee.objects.filter(user=employee, is_active=True).count() == 2


def test_invalid_validity_window_is_rejected(auth, hr, device, employee):
    resp = auth(hr).post(MAPPINGS, {
        "device": str(device.pk), "device_user_id": "9", "user": str(employee.pk),
        "effective_from": "2026-12-01", "effective_until": "2026-01-01",
    }, format="json")
    assert resp.status_code == 400


# --------------------------------------------------------------------------
# DELETE = soft unmap
# --------------------------------------------------------------------------

def test_delete_soft_unmaps_and_keeps_the_row(auth, hr, unmapped_row, employee, device):
    client = auth(hr)
    client.post(f"{MAPPINGS}{unmapped_row.pk}/map/", {"user": str(employee.pk)}, format="json")
    _punch(device, date(2026, 8, 1))
    client.post(f"{MAPPINGS}{unmapped_row.pk}/backfill/",
                {"allow_unbounded": True}, format="json")

    resp = client.delete(f"{MAPPINGS}{unmapped_row.pk}/")
    assert resp.status_code == 200
    assert resp.data["punches_detached"] == 1

    unmapped_row.refresh_from_db()  # would raise DoesNotExist if hard-deleted
    assert unmapped_row.user_id is None
    assert unmapped_row.is_active is False
    assert unmapped_row.effective_until == timezone.localdate()
    assert AuditLog.objects.filter(changes__event="BIOMETRIC_MAPPING_UNMAPPED").exists()


def test_delete_can_preserve_attribution(auth, hr, unmapped_row, employee, device):
    client = auth(hr)
    client.post(f"{MAPPINGS}{unmapped_row.pk}/map/", {"user": str(employee.pk)}, format="json")
    _punch(device, date(2026, 8, 1))
    client.post(f"{MAPPINGS}{unmapped_row.pk}/backfill/", {"allow_unbounded": True}, format="json")

    resp = client.delete(f"{MAPPINGS}{unmapped_row.pk}/", {"detach_punches": False}, format="json")
    assert resp.data["punches_detached"] == 0
    assert AttendancePunch.objects.filter(user=employee).count() == 1


# --------------------------------------------------------------------------
# Unmapped queue
# --------------------------------------------------------------------------

def test_unmapped_queue_groups_by_device_id(auth, hr, device):
    for d in (date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 3)):
        _punch(device, d, "17")
    _punch(device, date(2026, 8, 1), "18")

    rows = auth(hr).get(UNMAPPED).data
    assert len(rows) == 2, "one row per device ID, not per punch"
    row17 = next(r for r in rows if r["device_user_id"] == "17")
    assert row17["punch_count"] == 3
    assert row17["first_punch_date"] == "2026-08-01"
    assert row17["last_punch_date"] == "2026-08-03"
    assert row17["device_label"] == device.label


def test_unmapped_queue_excludes_attributed_punches(auth, hr, device, employee):
    _punch(device, date(2026, 8, 1), "17")
    AttendancePunch.objects.update(user=employee)
    assert auth(hr).get(UNMAPPED).data == []


def test_unmapped_queue_links_the_mapping_row(auth, hr, device, unmapped_row):
    _punch(device, date(2026, 8, 1), "17")
    row = auth(hr).get(UNMAPPED).data[0]
    assert row["mapping_id"] == str(unmapped_row.pk)
    assert row["device_name"] == "Bikash"


def test_unmapped_queue_flat_mode(auth, hr, device):
    _punch(device, date(2026, 8, 1), "17")
    _punch(device, date(2026, 8, 2), "17")
    rows = auth(hr).get(f"{UNMAPPED}?flat=1").data
    assert len(rows) == 2
    assert rows[0]["punch_label"] == "check_in"


def test_unmapped_queue_can_include_suggestions(auth, hr, device, dept):
    make_user("q1", dept=dept, first_name="Bikash", last_name="Kadayat")
    BiometricEmployee.objects.create(device=device, device_user_id="17", device_name="Bikash")
    _punch(device, date(2026, 8, 1), "17")

    row = auth(hr).get(f"{UNMAPPED}?suggestions=1").data[0]
    assert row["suggestions"], "expected at least one candidate"
    assert row["suggestions"][0]["user"]["full_name"] == "Bikash Kadayat"


# --------------------------------------------------------------------------
# Suggestions
# --------------------------------------------------------------------------

def test_suggestions_endpoint_never_applies_a_mapping(auth, hr, device, dept):
    make_user("g1", dept=dept, first_name="Bikash", last_name="Kadayat")
    mapping = BiometricEmployee.objects.create(
        device=device, device_user_id="17", device_name="Bikash")

    payload = auth(hr).get(SUGGESTIONS).data
    assert len(payload) == 1
    assert payload[0]["requires_confirmation"] is True
    assert payload[0]["suggestions"][0]["match_type"] == "similar_name"

    mapping.refresh_from_db()
    assert mapping.user_id is None, "a suggestion must never write a mapping"


def test_per_mapping_suggestions(auth, hr, device, dept):
    make_user("g2", dept=dept, first_name="Bikash", last_name="Kadayat")
    mapping = BiometricEmployee.objects.create(
        device=device, device_user_id="17", device_name="Bikashkadayat")
    results = auth(hr).get(f"{MAPPINGS}{mapping.pk}/suggestions/").data
    assert results[0]["score"] == 1.0
    assert results[0]["match_type"] == "exact_name"


# --------------------------------------------------------------------------
# Backfill via API
# --------------------------------------------------------------------------

def test_backfill_preview_then_execute(auth, hr, device, unmapped_row, employee):
    client = auth(hr)
    for d in (date(2026, 8, 1), date(2026, 8, 2)):
        _punch(device, d)
    client.post(f"{MAPPINGS}{unmapped_row.pk}/map/",
                {"user": str(employee.pk), "effective_from": "2026-08-01"}, format="json")

    preview = client.get(f"{MAPPINGS}{unmapped_row.pk}/backfill-preview/").data
    assert preview["records_count"] == 2
    assert preview["user_name"] == employee.get_full_name()
    assert AttendancePunch.objects.filter(user__isnull=True).count() == 2, "preview must not write"

    resp = client.post(f"{MAPPINGS}{unmapped_row.pk}/backfill/", {}, format="json")
    assert resp.status_code == 200
    assert resp.data["records_updated"] == 2
    assert AttendancePunch.objects.filter(user=employee).count() == 2


def test_api_refuses_unbounded_backfill_without_override(auth, hr, device,
                                                         unmapped_row, employee):
    client = auth(hr)
    _punch(device, date(2023, 1, 8))
    client.post(f"{MAPPINGS}{unmapped_row.pk}/map/", {"user": str(employee.pk)}, format="json")

    resp = client.post(f"{MAPPINGS}{unmapped_row.pk}/backfill/", {}, format="json")
    assert resp.status_code == 400
    assert "unbounded" in str(resp.data).lower()
    assert AttendancePunch.objects.filter(user=employee).count() == 0

    ok = client.post(f"{MAPPINGS}{unmapped_row.pk}/backfill/",
                     {"allow_unbounded": True}, format="json")
    assert ok.status_code == 200 and ok.data["records_updated"] == 1


def test_backfill_accepts_an_explicit_range(auth, hr, device, unmapped_row, employee):
    client = auth(hr)
    for d in (date(2026, 7, 1), date(2026, 8, 15)):
        _punch(device, d)
    client.post(f"{MAPPINGS}{unmapped_row.pk}/map/", {"user": str(employee.pk)}, format="json")

    resp = client.post(f"{MAPPINGS}{unmapped_row.pk}/backfill/",
                       {"date_from": "2026-08-01", "date_to": "2026-08-31"}, format="json")
    assert resp.data["records_updated"] == 1
