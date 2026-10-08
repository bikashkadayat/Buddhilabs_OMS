"""Legacy (NIF-style) biometric integration: devices managed from Settings.

Covers the brief end to end against the in-process ZK simulator:
device setup and the address guard, Test Connection, Sync now, the scheduler,
employee mapping (auto-match by ID), the App + Biometric merge rule, the
attendance-mode setting, and tenant isolation.
"""
from datetime import date, datetime, timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from attendance.models import Attendance
from audit.models import AuditLog
from biometric import devices
from biometric.derivation import derive_daily_attendance, merge_day_times
from biometric.models import AttendancePunch, BiometricDevice, BiometricEmployee, DeviceSyncLog
from biometric.services import auto_match
from tenancy.context import tenant_context
from users.models import User

from .conftest import make_user
from .zk_simulator import FakeZKDevice, pack_attendance, pack_user, table

pytestmark = pytest.mark.django_db

DAY = date(2026, 8, 3)
LOOPBACK_OK = override_settings(BIOMETRIC_DEVICE_ALLOW_LOOPBACK=True)


def at(hh, mm=0, day=DAY):
    return timezone.make_aware(datetime(day.year, day.month, day.day, hh, mm))


def naive(hh, mm=0, day=DAY):
    return datetime(day.year, day.month, day.day, hh, mm)


def fake_terminal(serial="ZK-SN-0001", users=(("1", "Ram"), ("2", "Sita")),
                  punches=(("1", 9, 0, 0), ("1", 18, 0, 1), ("2", 9, 30, 0))):
    roster = table([pack_user(i + 1, uid, name) for i, (uid, name) in enumerate(users)])
    logs = table([pack_attendance(uid, naive(hh, mm), punch=code)
                  for uid, hh, mm, code in punches])
    return FakeZKDevice(
        users=roster, attendance=logs, clock=datetime.now(),
        sizes={"users": len(users), "records": len(punches)},
        firmware="Ver 6.60 Apr 13 2016",
        options={"~SerialNumber": serial, "~Platform": "ZMM220_TFT",
                 "~DeviceName": "K40"} if serial else {"~Platform": "ZMM220_TFT"})


@pytest.fixture
def nif(db):
    from tenancy.models import Organization
    return Organization.objects.get(slug="nif")


@pytest.fixture
def org_b(db):
    from tenancy import services
    from tenancy.models import Plan

    return services.provision_organization(
        name="Beta Clinic", slug="betaclinic", document_prefix="BTA",
        email="admin@beta.test", plan=Plan.objects.get(code="monthly"))


@pytest.fixture
def admin_b(org_b):
    with tenant_context(org_b):
        return User.objects.create_user(
            username="beta_admin", email="admin2@beta.test", password="x-Beta-Admin-1",
            role=User.Roles.ADMIN)


def _url(name, **kw):
    return reverse(name, kwargs=kw or None)


# ---------------------------------------------------------------------------
# 1. Address guard
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254", "0.0.0.0", "::1",
                                  "224.0.0.1", "::ffff:127.0.0.1"])
def test_addresses_that_are_never_a_terminal_are_refused(host):
    with pytest.raises(devices.DeviceAddressError):
        devices.check_device_address(host, 4370)


@pytest.mark.parametrize("host", ["http://192.168.1.100", "192.168.1.100:4370",
                                  "192.168.1.100/admin", ""])
def test_malformed_addresses_are_refused(host):
    with pytest.raises(devices.DeviceAddressError):
        devices.check_device_address(host, 4370)


def test_a_lan_address_is_accepted_and_returned_as_the_ip_to_dial():
    assert devices.check_device_address("192.168.1.100", "4370") == ("192.168.1.100", 4370)


@override_settings(BIOMETRIC_DEVICE_ALLOW_PRIVATE_HOSTS=False)
def test_private_ranges_can_be_switched_off_for_a_cloud_deployment():
    with pytest.raises(devices.DeviceAddressError):
        devices.check_device_address("192.168.1.100", 4370)


def test_port_must_be_in_range():
    with pytest.raises(devices.DeviceAddressError):
        devices.check_device_address("192.168.1.100", 70000)


# ---------------------------------------------------------------------------
# 2. Device setup (API)
# ---------------------------------------------------------------------------
def test_admin_adds_a_device_with_ip_port_and_type(auth, admin_user):
    response = auth(admin_user).post(_url("biometric-device-list"), {
        "name": "Main Gate", "device_type": "zkteco", "host": "192.168.1.100",
        "port": 4370, "location": "Ground floor", "is_active": True,
        "sync_interval_minutes": 15, "comm_key": 1234}, format="json")
    assert response.status_code == 201, response.data
    body = response.data
    assert body["label"] == "main-gate"              # derived from the name
    assert body["device_type_display"] == "ZKTeco"
    assert body["sync_mode"] == "pull"
    assert body["has_comm_key"] is True
    assert "comm_key" not in body                    # write-only
    assert body["connection_status"] == "unknown"
    device = BiometricDevice.objects.get(pk=body["id"])
    assert device.comm_key == 1234 and device.created_by == admin_user
    assert AuditLog.objects.filter(changes__event="BIOMETRIC_DEVICE_CREATED").exists()


def test_the_api_refuses_a_loopback_address(auth, admin_user):
    response = auth(admin_user).post(_url("biometric-device-list"), {
        "name": "Sneaky", "host": "127.0.0.1", "port": 6379}, format="json")
    assert response.status_code == 400
    assert "loopback" in str(response.data["host"])


def test_duplicate_device_names_are_refused_readably(auth, admin_user, device):
    response = auth(admin_user).post(_url("biometric-device-list"), {
        "name": "main gate", "host": "192.168.1.101"}, format="json")
    assert response.status_code == 400
    assert "name" in response.data


def test_only_an_admin_may_add_a_device_but_hr_may_see_the_dashboard(auth, hr, employee, device):
    assert auth(hr).post(_url("biometric-device-list"), {
        "name": "X", "host": "192.168.1.9"}, format="json").status_code == 403
    assert auth(hr).get(_url("biometric-device-dashboard")).status_code == 200
    assert auth(employee).get(_url("biometric-device-list")).status_code == 403


def test_a_device_with_history_is_deactivated_not_deleted(auth, admin_user, device, make_punch):
    make_punch()
    response = auth(admin_user).delete(_url("biometric-device-detail", pk=device.pk))
    assert response.status_code == 200 and response.data["deactivated"] is True
    device.refresh_from_db()
    assert device.is_active is False


def test_a_device_without_history_is_deleted(auth, admin_user, second_device):
    response = auth(admin_user).delete(_url("biometric-device-detail", pk=second_device.pk))
    assert response.status_code == 204
    assert not BiometricDevice.objects.filter(pk=second_device.pk).exists()


# ---------------------------------------------------------------------------
# 3. Test Connection
# ---------------------------------------------------------------------------
@LOOPBACK_OK
def test_test_before_saving_reads_info_users_and_logs_and_writes_nothing(auth, admin_user):
    with fake_terminal() as fake:
        response = auth(admin_user).post(_url("biometric-device-test-unsaved"), {
            "host": fake.host, "port": fake.port}, format="json")
    assert response.status_code == 200
    body = response.data
    assert body["ok"] is True and body["status"] == "online"
    assert body["device_info"]["serial_number"] == "ZK-SN-0001"
    assert body["device_info"]["firmware"].startswith("Ver 6.60")
    assert body["users"]["count"] == 2
    assert {u["device_user_id"] for u in body["users"]["sample"]} == {"1", "2"}
    assert body["attendance"]["count"] == 3
    assert not AttendancePunch.objects.exists()
    assert not BiometricEmployee.objects.exists()


@LOOPBACK_OK
def test_testing_a_saved_device_marks_it_online_and_pins_its_serial(auth, admin_user):
    with fake_terminal() as fake:
        device = BiometricDevice.objects.create(name="Gate", label="gate",
                                                host=fake.host, port=fake.port)
        response = auth(admin_user).post(
            _url("biometric-device-test-connection", pk=device.pk))
    assert response.status_code == 200 and response.data["ok"]
    device.refresh_from_db()
    assert device.connection_status == "online"
    assert device.hardware_serial == "ZK-SN-0001"
    assert device.device_info["platform"] == "ZMM220_TFT"
    assert device.device_info["users"] == 2
    log = DeviceSyncLog.objects.get(device=device)
    assert (log.sync_type, log.trigger, log.status) == ("test", "test", "success")
    assert log.triggered_by == admin_user


def test_an_unreachable_device_reports_offline_and_is_logged(auth, admin_user):
    # TEST-NET-1: guaranteed unroutable, so nothing answers.
    device = BiometricDevice.objects.create(name="Gone", label="gone",
                                            host="192.0.2.1", port=4370)
    with override_settings(BIOMETRIC_DEVICE_ALLOW_LOOPBACK=False):
        devices.TEST_TIMEOUT_SECONDS, saved = 1, devices.TEST_TIMEOUT_SECONDS
        try:
            result = devices.test_connection(host=device.host, port=device.port,
                                             device=device, actor=admin_user)
        finally:
            devices.TEST_TIMEOUT_SECONDS = saved
    assert result["ok"] is False and result["status"] in ("offline", "not_zk")
    device.refresh_from_db()
    assert device.connection_status == "offline"
    assert DeviceSyncLog.objects.get(device=device).status == "failed"


@LOOPBACK_OK
def test_a_wrong_comm_key_is_reported_as_such():
    with FakeZKDevice(comm_key=4321, clock=datetime.now()) as fake:
        result = devices.test_connection(host=fake.host, port=fake.port, comm_key=1)
    assert result["status"] == "auth_required"


# ---------------------------------------------------------------------------
# 4. Sync now + identity
# ---------------------------------------------------------------------------
@LOOPBACK_OK
def test_sync_now_imports_users_and_punches_and_derives_attendance(auth, admin_user, employee):
    employee.biometric_id = "1"
    employee.save(update_fields=["biometric_id"])
    with fake_terminal() as fake:
        device = BiometricDevice.objects.create(name="Gate", label="gate",
                                                host=fake.host, port=fake.port)
        BiometricEmployee.objects.create(device=device, device_user_id="1", user=employee)
        response = auth(admin_user).post(_url("biometric-device-sync", pk=device.pk))
    assert response.status_code == 200, response.data
    body = response.data
    assert body["status"] == "partial"           # user 2 is not mapped yet
    assert body["punches"]["created"] == 3 and body["punches"]["unmapped"] == 1
    assert "not yet mapped" in body["message"]

    device.refresh_from_db()
    assert device.last_sync_status == "partial" and device.last_sync_imported == 3
    assert device.hardware_serial == "ZK-SN-0001"
    assert device.connection_status == "online"
    assert BiometricEmployee.objects.filter(device=device).count() == 2   # roster read

    pull = DeviceSyncLog.objects.get(device=device, sync_type="pull")
    assert (pull.trigger, pull.triggered_by, pull.records_created) == ("manual", admin_user, 3)

    record = Attendance.objects.get(employee=employee, date=DAY)
    assert record.source == Attendance.Source.BIOMETRIC
    assert record.check_in_source == record.check_out_source == "biometric"
    assert record.device == device

    # Second sync: nothing new, still logged.
    with fake_terminal() as fake:
        BiometricDevice.objects.filter(pk=device.pk).update(host=fake.host, port=fake.port)
        response = auth(admin_user).post(_url("biometric-device-sync", pk=device.pk))
    assert response.data["punches"]["created"] == 0
    assert DeviceSyncLog.objects.filter(device=device, sync_type="pull").count() == 2


@LOOPBACK_OK
def test_a_different_terminal_at_the_same_address_is_refused(admin_user):
    device = BiometricDevice.objects.create(name="Gate", label="gate", host="127.0.0.1",
                                            hardware_serial="ZK-SN-ORIGINAL")
    with fake_terminal(serial="ZK-SN-IMPOSTER") as fake:
        BiometricDevice.objects.filter(pk=device.pk).update(port=fake.port)
        device.refresh_from_db()
        result = devices.run_pull_sync(device, trigger="manual", actor=admin_user)
    assert result["status"] == "failed"
    assert "ZK-SN-IMPOSTER" in result["message"]
    assert not AttendancePunch.objects.exists()
    assert not BiometricEmployee.objects.exists()


@LOOPBACK_OK
def test_one_terminal_cannot_be_registered_by_two_organizations(nif, org_b, admin_b):
    with tenant_context(nif):
        BiometricDevice.objects.create(name="NIF Gate", label="gate", host="10.0.0.5",
                                       hardware_serial="ZK-SN-0001")
    with tenant_context(org_b), fake_terminal(serial="ZK-SN-0001") as fake:
        theirs = BiometricDevice.objects.create(name="Their Gate", label="gate",
                                                host=fake.host, port=fake.port)
        result = devices.run_pull_sync(theirs, trigger="manual", actor=admin_b)
        assert result["status"] == "failed"
        assert "another account" in result["message"]
        assert not AttendancePunch.objects.filter(device=theirs).exists()


@LOOPBACK_OK
def test_an_old_terminal_without_a_serial_still_syncs():
    with fake_terminal(serial=None) as fake:
        device = BiometricDevice.objects.create(name="Old", label="old",
                                                host=fake.host, port=fake.port)
        result = devices.run_pull_sync(device, trigger="manual")
        assert result["status"] in ("success", "partial")
        test = devices.test_connection(host=fake.host, port=fake.port)
    assert any("serial" in w for w in test["warnings"])


def test_a_failed_pull_is_logged_and_marks_the_device_offline():
    device = BiometricDevice.objects.create(name="Gate", label="gate", host="192.0.2.1",
                                            connection_status="online")
    result = devices.run_pull_sync(device, trigger="auto", timeout=1)
    assert result["status"] == "failed"
    device.refresh_from_db()
    assert device.connection_status == "offline"
    assert device.last_sync_status == "failed" and device.last_sync_error
    log = DeviceSyncLog.objects.get(device=device, sync_type="pull")
    assert log.status == "failed" and log.trigger == "auto"


def test_a_concurrent_pull_is_skipped_and_logged(monkeypatch):
    from biometric import locking

    device = BiometricDevice.objects.create(name="Gate", label="gate", host="192.168.1.5")
    with locking.device_lock(f"device-{device.pk}"):
        result = devices.run_pull_sync(device, trigger="auto")
    assert result["status"] == "skipped"
    assert DeviceSyncLog.objects.get(device=device).status == "skipped"


# ---------------------------------------------------------------------------
# 5. Auto sync scheduler
# ---------------------------------------------------------------------------
def test_is_due_honours_each_interval():
    now = timezone.now()
    device = BiometricDevice(name="d", label="d", host="192.168.1.5", is_active=True,
                             sync_interval_minutes=15)
    assert devices.is_due(device, now)                                # never synced
    device.last_sync_attempt_at = now - timedelta(minutes=10)
    assert not devices.is_due(device, now)
    device.last_sync_attempt_at = now - timedelta(minutes=15)
    assert devices.is_due(device, now)
    device.sync_interval_minutes = 0                                  # manual only
    assert not devices.is_due(device, now)
    device.sync_interval_minutes, device.host = 5, ""                 # push device
    assert not devices.is_due(device, now)


@LOOPBACK_OK
def test_the_scheduler_pulls_due_devices_in_every_tenant_into_their_own_tenant(nif, org_b):
    with fake_terminal(serial="SN-NIF") as a, fake_terminal(serial="SN-BETA") as b:
        with tenant_context(nif):
            ours = BiometricDevice.objects.create(name="NIF", label="nif", host=a.host,
                                                  port=a.port, sync_interval_minutes=5)
            manual = BiometricDevice.objects.create(name="Manual", label="manual",
                                                    host=a.host, port=a.port,
                                                    sync_interval_minutes=0)
        with tenant_context(org_b):
            theirs = BiometricDevice.objects.create(name="Beta", label="beta",
                                                    host=b.host, port=b.port,
                                                    sync_interval_minutes=30)
        out = StringIO()
        call_command("device_sync_due", stdout=out)
    assert "2 due" in out.getvalue()

    # Read each tenant's rows AS that tenant: under PostgreSQL RLS the
    # database hides the other organization's rows from this test too.
    with tenant_context(nif):
        assert set(AttendancePunch.all_tenants.filter(device=ours)
                   .values_list("organization_id", flat=True)) == {nif.pk}
        assert not AttendancePunch.all_tenants.filter(device=manual).exists()
    with tenant_context(org_b):
        assert set(AttendancePunch.all_tenants.filter(device=theirs)
                   .values_list("organization_id", flat=True)) == {org_b.pk}
        assert (DeviceSyncLog.all_tenants.get(device=theirs, sync_type="pull")
                .organization_id == org_b.pk)

    # Ran a moment ago: not due again.
    out = StringIO()
    call_command("device_sync_due", "--dry-run", stdout=out)
    assert "0 due" in out.getvalue()


def test_the_scheduler_skips_suspended_organizations(org_b):
    from tenancy.models import Organization

    Organization.objects.filter(pk=org_b.pk).update(status=Organization.Status.SUSPENDED)
    with tenant_context(org_b):
        BiometricDevice.objects.create(name="Beta", label="beta", host="192.168.1.5")
    out = StringIO()
    call_command("device_sync_due", "--dry-run", "--slug", "betaclinic", stdout=out)
    assert "0 due" in out.getvalue()


def test_health_check_does_not_flag_a_30_minute_device_offline_after_15(device):
    BiometricDevice.objects.filter(pk=device.pk).update(
        sync_interval_minutes=30, connection_status="online",
        last_seen_at=timezone.now() - timedelta(minutes=20))
    call_command("check_device_health", "--quiet", stdout=StringIO())
    device.refresh_from_db()
    assert device.connection_status == "online"
    BiometricDevice.objects.filter(pk=device.pk).update(
        last_seen_at=timezone.now() - timedelta(minutes=70))
    call_command("check_device_health", "--quiet", stdout=StringIO())
    device.refresh_from_db()
    assert device.connection_status == "offline"


# ---------------------------------------------------------------------------
# 6. Employee mapping: auto-match by ID
# ---------------------------------------------------------------------------
@pytest.fixture
def roster(device):
    def _make(*ids):
        return [BiometricEmployee.objects.create(device=device, device_user_id=i,
                                                 device_name=f"User {i}") for i in ids]
    return _make


def test_auto_match_previews_exact_id_matches_without_writing(roster, employee, other_employee):
    User.objects.filter(pk=employee.pk).update(employee_id="NIFN-EMP-2026-0007")
    User.objects.filter(pk=other_employee.pk).update(biometric_id="17")
    roster("17", "nifn-emp-2026-0007", "99")
    result = auto_match()
    pairs = {(r["device_user_id"], r["user"], r["matched_on"]) for r in result["matched"]}
    assert pairs == {("17", str(other_employee.pk), "biometric_id"),
                     ("nifn-emp-2026-0007", str(employee.pk), "employee_id")}
    assert result["applied"] is False
    assert not BiometricEmployee.objects.filter(user__isnull=False).exists()


def test_auto_match_applies_and_audits(auth, admin_user, roster, employee):
    roster("5")
    User.objects.filter(pk=employee.pk).update(biometric_id="5")
    response = auth(admin_user).post(_url("biometric-auto-match"), {"apply": True},
                                     format="json")
    assert response.status_code == 200 and len(response.data["matched"]) == 1
    assert BiometricEmployee.objects.get(device_user_id="5").user == employee
    assert AuditLog.objects.filter(changes__event="BIOMETRIC_AUTO_MATCH_APPLIED").exists()


def test_auto_match_never_guesses_between_two_employees(roster, employee, other_employee):
    roster("8")
    User.objects.filter(pk__in=[employee.pk, other_employee.pk]).update(biometric_id="8")
    result = auto_match()
    assert result["matched"] == []
    assert result["skipped"][0]["reason"].startswith("More than one employee")


def test_auto_match_does_not_use_name_similarity(roster, employee):
    BiometricEmployee.objects.create(device=roster("1")[0].device, device_user_id="2",
                                     device_name="Ram Thapa")
    assert auto_match()["matched"] == []


# ---------------------------------------------------------------------------
# 7. Merge rule (App + Biometric)
# ---------------------------------------------------------------------------
class _P:
    def __init__(self, ts, punch=0):
        self.timestamp, self.punch = ts, punch


def test_merge_takes_earliest_check_in_and_latest_check_out():
    punches = [_P(at(9, 0), 0), _P(at(17, 0), 1)]
    assert merge_day_times(punches, at(8, 30), at(19, 0), "mobile") == (
        at(8, 30), "mobile", at(19, 0), "mobile")
    assert merge_day_times(punches, at(9, 30), at(16, 0), "browser") == (
        at(9, 0), "biometric", at(17, 0), "biometric")


def test_a_lone_gate_punch_after_an_app_check_in_is_the_departure():
    assert merge_day_times([_P(at(18, 0), 0)], at(9, 0), None, "browser") == (
        at(9, 0), "browser", at(18, 0), "biometric")


def test_biometric_only_mode_ignores_app_times():
    punches = [_P(at(9, 0), 0), _P(at(17, 0), 1)]
    assert merge_day_times(punches, at(8, 0), at(20, 0), "browser", include_app=False) == (
        at(9, 0), "biometric", at(17, 0), "biometric")


@pytest.fixture
def punch(device, employee, mapping):
    def _make(hh, mm=0, code=0, day=DAY):
        return AttendancePunch.objects.create(
            device=device, employee_device_id=mapping.device_user_id,
            biometric_employee=mapping, user=employee, timestamp=at(hh, mm, day),
            punch=code)
    return _make


def test_derivation_merges_an_earlier_app_check_in_and_audits_once(
        employee, punch, local_browser_record):
    local_browser_record(check_in_hour=8)                # app check-in 08:00
    punch(9, 0, 0)
    punch(17, 30, 1)
    for _ in range(3):
        derive_daily_attendance(employee, DAY)
    rec = Attendance.objects.get(employee=employee, date=DAY)
    assert (timezone.localtime(rec.check_in).hour, rec.check_in_source) == (8, "browser")
    assert (timezone.localtime(rec.check_out).strftime("%H:%M"), rec.check_out_source) == (
        "17:30", "biometric")
    assert rec.source == "biometric" and rec.app_source == "browser"
    assert timezone.localtime(rec.browser_check_in).hour == 8   # app time preserved
    merges = AuditLog.objects.filter(changes__event="ATTENDANCE_SOURCES_MERGED")
    assert merges.count() == 1                           # idempotent re-runs are silent
    assert merges.get().changes["after"]["check_in_source"] == "browser"


def test_an_app_check_out_on_a_device_day_survives_the_next_sync(
        auth, employee, punch, monkeypatch):
    from attendance import services as att_services

    punch(9, 0, 0)                                       # gate punch, morning
    monkeypatch.setattr(timezone, "now", lambda: at(18, 0))
    monkeypatch.setattr(att_services, "is_holiday", lambda d: False)
    monkeypatch.setattr(att_services, "location_required", lambda: False)
    derive_daily_attendance(employee, DAY)
    response = auth(employee).post(reverse("attendance-check-out"), {},
                                   format="json", HTTP_X_ATTENDANCE_CLIENT="mobile")
    assert response.status_code == 200, response.data
    derive_daily_attendance(employee, DAY)               # the next sync
    rec = Attendance.objects.get(employee=employee, date=DAY)
    assert rec.check_out == at(18, 0) and rec.check_out_source == "mobile"
    assert rec.check_in == at(9, 0) and rec.check_in_source == "biometric"


@override_settings(ATTENDANCE_MODE="biometric_only")
def test_biometric_only_mode_uses_device_times(employee, punch, local_browser_record):
    from attendance import config
    config.forget()
    local_browser_record(check_in_hour=8)
    punch(9, 0, 0)
    derive_daily_attendance(employee, DAY)
    rec = Attendance.objects.get(employee=employee, date=DAY)
    assert timezone.localtime(rec.check_in).hour == 9 and rec.check_in_source == "biometric"
    assert timezone.localtime(rec.browser_check_in).hour == 8   # still kept


def test_a_future_dated_punch_never_becomes_the_check_out(employee, punch, monkeypatch):
    punch(9, 0, 0)
    punch(18, 0, 1)                                      # "18:00" while it is noon
    monkeypatch.setattr(timezone, "now", lambda: at(12, 0))
    derive_daily_attendance(employee, DAY)
    rec = Attendance.objects.get(employee=employee, date=DAY)
    assert rec.check_in == at(9, 0)
    assert rec.check_out is None


def test_a_day_of_only_future_punches_makes_no_attendance(employee, punch, monkeypatch):
    punch(18, 0, 0)
    monkeypatch.setattr(timezone, "now", lambda: at(12, 0))
    assert derive_daily_attendance(employee, DAY) is None
    assert not Attendance.objects.filter(employee=employee, date=DAY).exists()
    assert AttendancePunch.objects.get().is_processed      # kept, as evidence


def test_check_in_records_web_or_mobile(auth, employee, monkeypatch):
    from attendance import services as att_services

    monkeypatch.setattr(att_services, "is_holiday", lambda d: False)
    monkeypatch.setattr(att_services, "location_required", lambda: False)
    response = auth(employee).post(reverse("attendance-check-in"), {"client": "mobile"},
                                   format="json")
    assert response.status_code == 201, response.data
    assert response.data["source"] == "mobile"
    assert response.data["source_display"] == "Mobile app"
    assert response.data["check_in_source"] == "mobile"


def test_attendance_history_names_the_source_and_device(auth, hr, employee, punch):
    punch(9, 0, 0)
    derive_daily_attendance(employee, DAY)
    response = auth(hr).get(reverse("attendance-list"), {"start": "2026-08-01",
                                                          "end": "2026-08-31"})
    assert response.status_code == 200
    rows = response.data if isinstance(response.data, list) else response.data["results"]
    row = next(r for r in rows if r["date"] == "2026-08-03")
    assert row["source_display"] == "Biometric device"
    assert row["device_name"] == "Main Gate"


# ---------------------------------------------------------------------------
# 8. Attendance mode (Organization Settings)
# ---------------------------------------------------------------------------
def test_admin_sets_the_attendance_mode_and_hr_cannot(auth, admin_user, hr):
    url = reverse("attendance-mode")
    assert auth(hr).patch(url, {"attendance_mode": "app_only"},
                          format="json").status_code == 403
    response = auth(admin_user).patch(url, {"attendance_mode": "biometric_only"},
                                      format="json")
    assert response.status_code == 200
    assert response.data["effective_mode"] == "biometric_only"
    assert auth(hr).get(url).data["effective_mode"] == "biometric_only"
    assert auth(admin_user).patch(url, {"attendance_mode": "sometimes"},
                                  format="json").status_code == 400
    assert AuditLog.objects.filter(changes__event="ATTENDANCE_MODE_CHANGED").exists()


# ---------------------------------------------------------------------------
# 9. Tenant isolation through the API
# ---------------------------------------------------------------------------
@override_settings(TENANCY_BASE_DOMAIN="platform.test", TENANCY_PLATFORM_HOSTS="")
def test_another_organizations_admin_cannot_see_or_touch_our_device(auth, device, admin_b):
    client = auth(admin_b)
    host = {"HTTP_HOST": "betaclinic.platform.test"}
    listing = client.get(_url("biometric-device-list"), **host)
    assert listing.status_code == 200 and listing.data["results"] == []
    dashboard = client.get(_url("biometric-device-dashboard"), **host)
    assert dashboard.data["devices"] == [] and dashboard.data["totals"]["devices"] == 0
    for method, name in (("get", "biometric-device-detail"),
                         ("post", "biometric-device-sync"),
                         ("post", "biometric-device-test-connection"),
                         ("get", "biometric-device-sync-logs")):
        response = getattr(client, method)(_url(name, pk=device.pk), **host)
        assert response.status_code == 404, (name, response.status_code)
    assert client.patch(_url("biometric-device-detail", pk=device.pk),
                        {"host": "192.168.1.200"}, format="json", **host).status_code == 404
    with tenant_context(device.organization_id):
        device.refresh_from_db()
    assert device.host == "192.168.77.201"


# ---------------------------------------------------------------------------
# 10. Platform admin (console) sets up a tenant's device
# ---------------------------------------------------------------------------
@pytest.fixture
def operator(db):
    from tenancy.context import no_tenant

    with no_tenant():
        return User.objects.create_user(
            username="platform-ops", email="ops@platform.test", password="x-Platform-1",
            is_platform_staff=True, organization=None)


@LOOPBACK_OK
def test_a_platform_admin_adds_and_syncs_a_device_for_a_tenant(org_b, operator):
    from rest_framework.test import APIClient

    from tenancy.models import PlatformAuditLog

    client = APIClient()
    client.force_authenticate(user=operator)
    base = f"/api/v1/platform/organizations/{org_b.slug}/biometric-devices/"
    with fake_terminal(serial="SN-CONSOLE") as fake:
        created = client.post(base, {"name": "Clinic Gate", "host": fake.host,
                                     "port": fake.port, "device_type": "zkteco"},
                              format="json")
        assert created.status_code == 201, created.data
        device_id = created.data["id"]
        synced = client.post(f"{base}{device_id}/sync/")
    assert synced.status_code == 200 and synced.data["punches"]["created"] == 3

    with tenant_context(org_b):
        device = BiometricDevice.all_tenants.get(pk=device_id)
        assert device.organization_id == org_b.pk
        assert set(AttendancePunch.all_tenants.filter(device=device)
                   .values_list("organization_id", flat=True)) == {org_b.pk}
    assert client.get(base).data[0]["name"] == "Clinic Gate"
    assert PlatformAuditLog.objects.filter(organization=org_b).count() >= 2


def test_an_organization_admin_cannot_use_the_console_routes(auth, admin_user, org_b):
    response = auth(admin_user).get(
        f"/api/v1/platform/organizations/{org_b.slug}/biometric-devices/")
    assert response.status_code == 403
