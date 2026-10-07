"""WebSocket consumer, event broadcasting and device health.

Runs on InMemoryChannelLayer — no Redis needed, so CI stays as-is. What that
cannot prove is cross-process fan-out through Redis; that is covered by the
end-to-end validation against a live gunicorn+uvicorn+redis stack.
"""
from datetime import date, datetime

import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from attendance.models import Attendance
from biometric import events
from biometric.consumers import CLOSE_UNAUTHORIZED, AttendanceConsumer
from biometric.models import AttendancePunch, BiometricDevice
from biometric.services import set_device_status

# transaction=True is required: the consumer reads the DB through
# database_sync_to_async, which runs on its own connection and therefore cannot
# see an uncommitted test transaction.
pytestmark = pytest.mark.django_db(transaction=True)

DAY = date(2026, 8, 3)


@pytest.fixture(scope="module", autouse=True)
def restore_flushed_seed_data(django_db_setup, django_db_blocker):
    """Put back everything a transactional test flushes.

    Every test in this module is transactional, and Django FLUSHES all tables
    when such a test tears down. That takes the migration-seeded rows with it —
    LeaveType, Holiday, EntitlementRule and friends from leaves/migrations/0005,
    0010 and 0013 — and nothing ever re-creates them. Without this fixture,
    adding a realtime test silently breaks leaves/tests/test_cluster_b.py, and
    which tests pass depends on file collection order.

    serialized_rollback=True does not fix it: that restores the snapshot for
    THIS test's setup, not for whatever runs next.

    Module-scoped on purpose. A function-scoped teardown is not guaranteed to
    run after pytest-django's flush, but a module-scoped one always outlives
    every function-scoped fixture in the module. Django's own serialization is
    used rather than a hand-picked model list, which would rot the moment
    someone adds a data migration.
    """
    import json

    from django.db import connection

    with django_db_blocker.unblock():
        # tenancy.* AS WELL AS leaves.*, AND TENANCY FIRST (Phase S2).
        #
        # Every leaves row now carries a non-null FK to tenancy.Organization.
        # Restoring leaves.* alone re-inserts children whose parent the flush
        # has just deleted, and the next constraint check fails with:
        #
        #     IntegrityError: The row in table 'leaves_holiday' ... has an
        #     invalid foreign key
        #
        # The sort puts the parents in first; it is stable, so the relative
        # order within each group is unchanged.
        rows = [
            obj for obj in json.loads(connection.creation.serialize_db_to_string())
            if obj["model"].startswith(("leaves.", "tenancy."))
        ]
        snapshot = json.dumps(
            sorted(rows, key=lambda obj: not obj["model"].startswith("tenancy.")))

    yield

    with django_db_blocker.unblock():
        connection.creation.deserialize_db_from_string(snapshot)


@pytest.fixture(autouse=True)
def in_memory_layer(settings):
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    yield


def token_for(user):
    return str(RefreshToken.for_user(user).access_token)


async def connect(token, *, protocols=None):
    """Open a socket. Returns (communicator, connected, subprotocol)."""
    subprotocols = protocols if protocols is not None else (["jwt", token] if token else [])
    comm = WebsocketCommunicator(
        AttendanceConsumer.as_asgi(), "/ws/attendance/", subprotocols=subprotocols)
    connected, detail = await comm.connect()
    return comm, connected, detail


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

async def test_connection_without_a_subprotocol_is_rejected():
    comm, connected, _ = await connect(None)
    assert connected is False
    await comm.disconnect()


async def test_connection_with_a_garbage_token_is_rejected():
    comm, connected, _ = await connect("not-a-jwt")
    assert connected is False
    await comm.disconnect()


async def test_wrong_subprotocol_name_is_rejected(hr):
    """Only ["jwt", <token>] is accepted — a stray protocol list must not pass."""
    token = await sync(token_for)(hr)
    comm, connected, _ = await connect(None, protocols=["graphql-ws", token])
    assert connected is False
    await comm.disconnect()


async def test_valid_token_connects_and_echoes_the_subprotocol(hr):
    token = await sync(token_for)(hr)
    comm, connected, subprotocol = await connect(token)
    assert connected is True
    # A browser aborts if the server accepts without confirming a subprotocol
    # it offered, so this echo is load-bearing, not cosmetic.
    assert subprotocol == "jwt"
    ready = await comm.receive_json_from()
    assert ready["type"] == "connection.ready.v1"
    await comm.disconnect()


async def test_deactivated_user_is_rejected_despite_a_valid_token(employee):
    """A JWT stays cryptographically valid for its full 30 minutes after an
    account is disabled — the consumer must re-check is_active."""
    token = await sync(token_for)(employee)
    await sync(_deactivate)(employee)
    comm, connected, _ = await connect(token)
    assert connected is False
    await comm.disconnect()


async def test_close_code_tells_the_client_not_to_retry():
    comm = WebsocketCommunicator(
        AttendanceConsumer.as_asgi(), "/ws/attendance/", subprotocols=["jwt", "bad"])
    connected, code = await comm.connect()
    assert connected is False
    assert code == CLOSE_UNAUTHORIZED
    await comm.disconnect()


# --------------------------------------------------------------------------
# Group scoping — mirrors AttendanceListView
# --------------------------------------------------------------------------

async def test_hr_joins_org_wide_and_device_groups(hr):
    ready = await _connect_and_read(hr)
    org = hr.organization_id
    assert events.group_all(org) in ready["data"]["groups"]
    assert events.group_devices(org) in ready["data"]["groups"]


async def test_admin_joins_org_wide_groups(admin_user):
    ready = await _connect_and_read(admin_user)
    assert events.group_all(admin_user.organization_id) in ready["data"]["groups"]


async def test_department_head_is_scoped_to_their_department(dept_head, dept):
    ready = await _connect_and_read(dept_head)
    groups = ready["data"]["groups"]
    org = dept_head.organization_id
    assert events.group_for_department(dept.pk, org) in groups
    assert events.group_all(org) not in groups, \
        "a checker must not see the whole org"
    assert events.group_devices(org) not in groups


async def test_employee_sees_only_themselves(employee):
    ready = await _connect_and_read(employee)
    assert ready["data"]["groups"] == [
        events.group_for_user(employee.pk, employee.organization_id)]


async def test_every_group_a_socket_joins_names_its_own_tenant(hr):
    """Phase S3: a subscription can only ever be to the token's own tenant.

    The organization is read off the authenticated user's row, never from the
    handshake -- so there is nothing a client can send that subscribes it to
    another tenant's feed.
    """
    from tenancy.keys import token_for

    ready = await _connect_and_read(hr)
    token = token_for(hr.organization_id)
    for group in ready["data"]["groups"]:
        assert f".{token}." in f"{group}." or group.endswith(f".{token}"), (
            f"group {group!r} does not name tenant {token}")


# --------------------------------------------------------------------------
# Event delivery
# --------------------------------------------------------------------------

async def test_punch_event_reaches_hr(hr, employee, device, mapping):
    comm, _, _ = await connect(await sync(token_for)(hr))
    await comm.receive_json_from()

    await sync(_punch)(device, mapping, employee)

    evt = await comm.receive_json_from(timeout=3)
    assert evt["type"] == "attendance.punch.v1"
    assert evt["data"]["employee_name"] == employee.get_full_name()
    assert evt["data"]["punch_label"] == "check_in"
    assert evt["data"]["is_mapped"] is True
    await comm.disconnect()


async def test_events_are_versioned(hr, employee, device, mapping):
    comm, _, _ = await connect(await sync(token_for)(hr))
    await comm.receive_json_from()
    await sync(_punch)(device, mapping, employee)
    evt = await comm.receive_json_from(timeout=3)
    assert evt["type"].endswith(".v1"), "clients pin to a version; unversioned would break them"
    await comm.disconnect()


async def test_unrelated_employee_receives_nothing(other_employee, employee, device, mapping):
    comm, _, _ = await connect(await sync(token_for)(other_employee))
    await comm.receive_json_from()

    await sync(_punch)(device, mapping, employee)

    assert await comm.receive_nothing(timeout=1), "attendance must not leak between employees"
    await comm.disconnect()


async def test_attendance_updated_event(hr, employee):
    comm, _, _ = await connect(await sync(token_for)(hr))
    await comm.receive_json_from()

    await sync(_attendance_row)(employee)

    evt = await comm.receive_json_from(timeout=3)
    assert evt["type"] == "attendance.updated.v1"
    assert evt["data"]["status"] == Attendance.Status.PRESENT
    await comm.disconnect()


async def test_device_events_go_to_hr_only(hr, employee, device):
    hr_comm, _, _ = await connect(await sync(token_for)(hr))
    await hr_comm.receive_json_from()
    emp_comm, _, _ = await connect(await sync(token_for)(employee))
    await emp_comm.receive_json_from()

    await sync(_offline)(device)

    evt = await hr_comm.receive_json_from(timeout=3)
    assert evt["type"] == "device.offline.v1"
    assert evt["data"]["label"] == device.label
    assert await emp_comm.receive_nothing(timeout=1)
    await hr_comm.disconnect()
    await emp_comm.disconnect()


async def test_heartbeat_ping_pong(hr):
    comm, _, _ = await connect(await sync(token_for)(hr))
    await comm.receive_json_from()
    await comm.send_json_to({"type": "ping"})
    assert (await comm.receive_json_from(timeout=3))["type"] == "pong"
    await comm.disconnect()


async def test_reconnect_after_disconnect(hr):
    token = await sync(token_for)(hr)
    first, connected, _ = await connect(token)
    assert connected
    await first.receive_json_from()
    await first.disconnect()

    second, reconnected, _ = await connect(token)
    assert reconnected is True
    assert (await second.receive_json_from())["type"] == "connection.ready.v1"
    await second.disconnect()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def sync(fn):
    from channels.db import database_sync_to_async
    return database_sync_to_async(fn)


async def _connect_and_read(user):
    comm, connected, _ = await connect(await sync(token_for)(user))
    assert connected is True
    ready = await comm.receive_json_from()
    await comm.disconnect()
    return ready


def _deactivate(user):
    user.is_active = False
    user.save(update_fields=["is_active"])


def _punch(device, mapping, user):
    punch = AttendancePunch.objects.create(
        device=device, biometric_employee=mapping, employee_device_id=mapping.device_user_id,
        user=user, employee_name=user.get_full_name(),
        timestamp=timezone.make_aware(datetime(DAY.year, DAY.month, DAY.day, 9, 15)), punch=0)
    events.punch_recorded(punch)
    return punch


def _attendance_row(user):
    record = Attendance.objects.create(
        employee=user, date=DAY, status=Attendance.Status.PRESENT,
        check_in=timezone.make_aware(datetime(DAY.year, DAY.month, DAY.day, 9, 0)),
        source=Attendance.Source.BIOMETRIC, marked_by=Attendance.MarkedBy.SYSTEM)
    events.attendance_updated(record)
    return record


def _offline(device):
    device.connection_status = BiometricDevice.Status.ONLINE
    device.save(update_fields=["connection_status"])
    set_device_status(device, online=False, immediate=True)


# --------------------------------------------------------------------------
# Broadcast safety — the two rules from events.py
# --------------------------------------------------------------------------

def test_broadcast_waits_for_the_commit(employee, device, mapping, settings):
    """Rule 1: a broadcast from inside a transaction would reach the browser
    before the commit, so the refetch it triggers would read stale data."""
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    layer = get_channel_layer()
    sent = []
    original = layer.group_send

    async def spy(group, message):
        sent.append((group, message["type"]))
        return await original(group, message)

    layer.group_send = spy
    try:
        with transaction.atomic():
            _punch(device, mapping, employee)
            assert sent == [], "must not broadcast before the transaction commits"
        assert sent, "must broadcast once the transaction commits"
        assert all(t == "attendance.punch.v1" for _, t in sent)
    finally:
        layer.group_send = original


def test_a_broken_channel_layer_never_breaks_ingest(employee, device, mapping, settings):
    """Rule 2: a Redis outage must not take down attendance recording."""
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "biometric.tests.test_realtime.ExplodingLayer"}}

    punch = AttendancePunch.objects.create(
        device=device, biometric_employee=mapping, employee_device_id="1", user=employee,
        timestamp=timezone.make_aware(datetime(DAY.year, DAY.month, DAY.day, 9, 15)), punch=0)

    events.punch_recorded(punch)          # must not raise
    assert AttendancePunch.objects.filter(pk=punch.pk).exists()


class ExplodingLayer:
    """Stands in for an unreachable Redis."""

    def __init__(self, *args, **kwargs):
        pass

    async def group_send(self, group, message):
        raise ConnectionError("redis is down")

    async def group_add(self, group, channel):
        raise ConnectionError("redis is down")


# --------------------------------------------------------------------------
# Device health
# --------------------------------------------------------------------------

def test_status_change_is_broadcast_once_not_per_ingest(device, settings):
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    assert set_device_status(device, online=True) is True
    assert set_device_status(device, online=True) is False, "no event when nothing changed"
    assert set_device_status(device, online=False) is True


def test_check_device_health_marks_a_quiet_device_offline(device, settings):
    from datetime import timedelta
    from io import StringIO

    from django.core.management import call_command

    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    device.connection_status = BiometricDevice.Status.ONLINE
    device.last_seen_at = timezone.now() - timedelta(hours=2)
    device.save(update_fields=["connection_status", "last_seen_at"])

    out = StringIO()
    call_command("check_device_health", "--minutes", "15", stdout=out)
    device.refresh_from_db()
    assert device.connection_status == BiometricDevice.Status.OFFLINE
    assert "OFFLINE" in out.getvalue()


def test_check_device_health_leaves_a_recent_device_online(device, settings):
    from io import StringIO

    from django.core.management import call_command

    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    device.connection_status = BiometricDevice.Status.ONLINE
    device.last_seen_at = timezone.now()
    device.save(update_fields=["connection_status", "last_seen_at"])

    call_command("check_device_health", stdout=StringIO())
    device.refresh_from_db()
    assert device.connection_status == BiometricDevice.Status.ONLINE


def test_a_never_seen_device_is_not_reported_offline(device, settings):
    """It has never been configured, not failed. Alerting every 5 minutes on it
    would train people to ignore the alert."""
    from io import StringIO

    from django.core.management import call_command

    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    assert device.last_seen_at is None
    call_command("check_device_health", stdout=StringIO())
    device.refresh_from_db()
    assert device.connection_status == BiometricDevice.Status.UNKNOWN
