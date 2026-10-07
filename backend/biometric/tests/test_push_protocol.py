"""The iClock / PUSH path — the one the real terminal actually speaks.

192.168.77.201 is a ZKTeco ZLM60 (Linux/MIPS), which is a PUSH-mode platform:
it dials out over HTTP rather than answering SDK calls on 4370. These tests
drive the endpoints exactly as that firmware does — same paths, same query
parameters, same tab-separated plain-text bodies.

The invariant under nearly every test here is the acknowledgement contract:
**``OK`` tells the device to delete its copy.** So OK must follow a durable
write and nothing else. A test suite for this protocol that only checks the
happy path is testing the half that cannot lose data.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from django.db import DatabaseError

from biometric import push_protocol
from biometric.models import AttendancePunch, BiometricDevice, BiometricEmployee

pytestmark = pytest.mark.django_db

SERIAL = "CJXK205060099"
KATHMANDU = ZoneInfo("Asia/Kathmandu")


@pytest.fixture
def push_device(db):
    return BiometricDevice.objects.create(
        name="Main Gate", label="main-gate", host="192.168.77.201",
        serial_number=SERIAL, device_timezone="Asia/Kathmandu", is_active=True)


def cdata(client, body="", *, serial=SERIAL, table="ATTLOG", **extra):
    return client.post(f"/iclock/cdata?SN={serial}&table={table}",
                       data=body.encode(), content_type="text/plain", **extra)


# ===========================================================================
# parsing
# ===========================================================================
class TestAttlogParsing:
    def test_a_real_line_decodes(self):
        records, errors = push_protocol.parse_attlog(
            "17\t2026-08-06 09:15:04\t0\t1\t0\t0\t0")
        assert not errors
        assert records == [{"employee_id": "17",
                            "timestamp": datetime(2026, 8, 6, 9, 15, 4),
                            "punch": 0, "status": 1}]

    def test_the_pin_is_not_coerced(self):
        """The PIN is the device's own ID. '007' and '7' are two people."""
        records, _ = push_protocol.parse_attlog("007\t2026-08-06 09:15:04\t0\t1")
        assert records[0]["employee_id"] == "007"

    def test_multiple_lines(self):
        body = ("1\t2026-08-06 09:02:00\t0\t1\n"
                "2\t2026-08-06 09:07:11\t0\t1\n"
                "1\t2026-08-06 17:40:02\t1\t1\n")
        records, errors = push_protocol.parse_attlog(body)
        assert len(records) == 3 and not errors

    def test_a_short_line_still_works(self):
        """Some firmware sends only PIN and timestamp."""
        records, errors = push_protocol.parse_attlog("17\t2026-08-06 09:15:04")
        assert not errors
        assert records[0]["punch"] == 0

    def test_space_separated_firmware(self):
        """The trap: the timestamp itself contains a space, so a naive
        whitespace split shifts every later column and reads the punch type out
        of the seconds field."""
        records, errors = push_protocol.parse_attlog("17 2026-08-06 09:15:04 1 1")
        assert not errors
        assert records[0]["employee_id"] == "17"
        assert records[0]["timestamp"] == datetime(2026, 8, 6, 9, 15, 4)
        assert records[0]["punch"] == 1, "columns were shifted by the space in the timestamp"

    def test_a_bad_line_does_not_cost_the_good_ones(self):
        body = ("1\t2026-08-06 09:02:00\t0\t1\n"
                "GARBAGE\n"
                "2\t2026-08-06 09:07:11\t0\t1\n")
        records, errors = push_protocol.parse_attlog(body)
        assert len(records) == 2
        assert len(errors) == 1

    def test_a_blank_pin_is_an_error_not_a_punch(self):
        records, errors = push_protocol.parse_attlog("\t2026-08-06 09:15:04\t0\t1")
        assert not records and errors

    def test_the_timestamp_is_naive(self):
        """The terminal does not know its zone; only the OMS does."""
        records, _ = push_protocol.parse_attlog("1\t2026-08-06 09:15:04\t0\t1")
        assert records[0]["timestamp"].tzinfo is None


class TestOperlogParsing:
    def test_a_user_record_decodes(self):
        users, others = push_protocol.parse_operlog(
            "USER PIN=17\tName=Bikash Kadayat\tPri=0\tPasswd=\tCard=4017\tGrp=1")
        assert others == 0
        assert users[0] == {"employee_id": "17", "name": "Bikash Kadayat",
                            "privilege": 0, "card": 4017, "group_id": "1"}

    def test_non_user_lines_are_counted_not_parsed(self):
        users, others = push_protocol.parse_operlog(
            "OPLOG 4\t1\t2026-08-06 09:00:00\n"
            "USER PIN=2\tName=Ram\tPri=0\tCard=0\tGrp=1\n")
        assert len(users) == 1 and others == 1

    def test_the_pin_survives(self):
        users, _ = push_protocol.parse_operlog("USER PIN=007\tName=Ram")
        assert users[0]["employee_id"] == "007"


class TestHandshake:
    def test_it_carries_the_settings_that_make_push_work(self):
        text = push_protocol.handshake_response(SERIAL)
        assert f"GET OPTION FROM: {SERIAL}" in text
        # Realtime=1 is what makes "no manual sync" literally true, and
        # TransFlag is the classic cause of a device that is online but silent.
        assert "Realtime=1" in text
        assert "TransFlag=1111000000" in text

    def test_the_nepal_offset_is_not_an_integer(self):
        """+05:45 is 5.75. Rounding it to 5 or 6 misfiles punches by 45 minutes."""
        offset = push_protocol.timezone_offset_hours(
            KATHMANDU, datetime(2026, 8, 6, 12, 0, tzinfo=KATHMANDU))
        assert offset == "5.75"


# ===========================================================================
# the endpoints
# ===========================================================================
class TestHandshakeEndpoint:
    def test_a_known_device_gets_its_configuration(self, client, push_device):
        response = client.get(f"/iclock/cdata?SN={SERIAL}&options=all&pushver=2.4.1")
        assert response.status_code == 200
        body = response.content.decode()
        assert f"GET OPTION FROM: {SERIAL}" in body
        assert "Realtime=1" in body

    def test_the_handshake_marks_the_device_seen(self, client, push_device):
        client.get(f"/iclock/cdata?SN={SERIAL}&options=all")
        push_device.refresh_from_db()
        assert push_device.last_seen_at is not None


class TestAttendanceDelivery:
    def test_a_punch_becomes_an_attendancepunch(self, client, push_device):
        response = cdata(client, "17\t2026-08-06 09:15:04\t0\t1\t0\t0\t0\n")
        assert response.status_code == 200
        assert response.content.decode().startswith("OK")

        punch = AttendancePunch.objects.get()
        assert punch.employee_device_id == "17"
        assert punch.device == push_device
        assert punch.source == AttendancePunch.Source.LIVE

    def test_the_timestamp_lands_in_the_device_timezone(self, client, push_device):
        cdata(client, "17\t2026-08-06 09:15:04\t0\t1\n")
        punch = AttendancePunch.objects.get()
        assert punch.timestamp.astimezone(KATHMANDU).hour == 9
        assert punch.local_date == date(2026, 8, 6)

    def test_a_punch_just_after_midnight_stays_on_its_own_day(self, client, push_device):
        """+05:45 means reading the wall clock as UTC lands it the day before."""
        cdata(client, "17\t2026-08-06 00:05:00\t0\t1\n")
        assert AttendancePunch.objects.get().local_date == date(2026, 8, 6)

    def test_a_replayed_batch_creates_nothing(self, client, push_device):
        """The device re-posts when unsure it was heard. That must be free."""
        body = "17\t2026-08-06 09:15:04\t0\t1\n17\t2026-08-06 17:40:00\t1\t1\n"
        cdata(client, body)
        cdata(client, body)
        assert AttendancePunch.objects.count() == 2

    def test_an_unmapped_pin_is_stored_not_dropped(self, client, push_device):
        cdata(client, "404\t2026-08-06 09:15:04\t0\t1\n")
        punch = AttendancePunch.objects.get()
        assert punch.employee_device_id == "404"
        assert punch.user is None

    def test_a_mapped_pin_resolves_to_the_employee(self, client, push_device, employee):
        BiometricEmployee.objects.create(
            device=push_device, device_user_id="17", user=employee, is_active=True)
        cdata(client, "17\t2026-08-06 09:15:04\t0\t1\n")
        assert AttendancePunch.objects.get().user == employee

    def test_the_count_in_the_reply_is_what_was_stored(self, client, push_device):
        response = cdata(client, "1\t2026-08-06 09:00:00\t0\t1\n"
                                 "2\t2026-08-06 09:01:00\t0\t1\n")
        assert response.content.decode().strip() == "OK: 2"


class TestTheAcknowledgementContract:
    """``OK`` means the device deletes its copy. These are the tests that stop
    attendance being destroyed by a cheerful reply."""

    def test_an_unknown_serial_is_never_told_ok(self, client, db):
        response = cdata(client, "17\t2026-08-06 09:15:04\t0\t1\n", serial="NOTREGISTERED")
        assert response.status_code != 200, "an unregistered device was told to discard data"
        assert AttendancePunch.objects.count() == 0

    def test_the_refusal_says_how_to_fix_it(self, client, db):
        response = cdata(client, "17\t2026-08-06 09:15:04\t0\t1\n", serial="NOTREGISTERED")
        assert "serial_number" in response.content.decode()

    def test_an_inactive_device_is_never_told_ok(self, client, push_device):
        push_device.is_active = False
        push_device.save()
        response = cdata(client, "17\t2026-08-06 09:15:04\t0\t1\n")
        assert response.status_code != 200

    def test_a_missing_serial_is_refused(self, client, db):
        response = client.post("/iclock/cdata?table=ATTLOG",
                               data=b"17\t2026-08-06 09:15:04\t0\t1\n",
                               content_type="text/plain")
        assert response.status_code != 200

    def test_a_wholly_unparseable_body_is_refused(self, client, push_device):
        response = cdata(client, "!!! not attendance !!!\n")
        assert response.status_code != 200
        assert AttendancePunch.objects.count() == 0

    def test_a_storage_failure_is_never_told_ok(self, client, push_device, monkeypatch):
        """The one case where OK would definitely destroy data."""
        def explode(*args, **kwargs):
            raise DatabaseError("disk full")
        monkeypatch.setattr("biometric.push_views.ingest_punches", explode)

        response = cdata(client, "17\t2026-08-06 09:15:04\t0\t1\n")
        assert response.status_code != 200
        assert AttendancePunch.objects.count() == 0

    def test_a_partly_bad_batch_is_acknowledged_for_what_stored(self, client, push_device):
        """The device cannot resend individual lines. Refusing the whole batch
        would replay the good records forever and never fix the bad one."""
        response = cdata(client, "1\t2026-08-06 09:00:00\t0\t1\n"
                                 "CORRUPT LINE\n"
                                 "2\t2026-08-06 09:01:00\t0\t1\n")
        assert response.status_code == 200
        assert AttendancePunch.objects.count() == 2

    def test_a_table_we_ignore_is_acknowledged(self, client, push_device):
        """Making the device retain data we will never consume fills its buffer
        and eventually costs us the attendance we do want."""
        response = cdata(client, "some biodata\n", table="BIODATA")
        assert response.status_code == 200


class TestRosterDelivery:
    def test_enrolments_arrive_with_their_ids_intact(self, client, push_device):
        response = cdata(client,
                         "USER PIN=1\tName=Ram Thapa\tPri=0\tCard=0\tGrp=1\n"
                         "USER PIN=17\tName=Bikash\tPri=0\tCard=4017\tGrp=1\n",
                         table="OPERLOG")
        assert response.status_code == 200
        stored = set(BiometricEmployee.objects.values_list("device_user_id", flat=True))
        assert stored == {"1", "17"}, "the push path renumbered a device ID"

    def test_a_roster_push_never_auto_maps(self, client, push_device, employee):
        """Linking a device ID to a person is an HR decision, always."""
        cdata(client, "USER PIN=1\tName=Ram Thapa\tPri=0\tCard=0\tGrp=1\n",
              table="OPERLOG")
        assert BiometricEmployee.objects.get().user is None

    def test_a_roster_storage_failure_is_never_told_ok(self, client, push_device,
                                                       monkeypatch):
        def explode(*args, **kwargs):
            raise DatabaseError("disk full")
        monkeypatch.setattr("biometric.push_views.ingest_roster", explode)
        response = cdata(client, "USER PIN=1\tName=Ram\n", table="OPERLOG")
        assert response.status_code != 200


class TestPolling:
    def test_getrequest_answers_ok(self, client, push_device):
        response = client.get(f"/iclock/getrequest?SN={SERIAL}")
        assert response.status_code == 200
        assert response.content.decode().strip() == "OK"

    def test_polling_keeps_the_device_marked_online(self, client, push_device):
        """The poll is the liveness beat the offline detector reads."""
        client.get(f"/iclock/getrequest?SN={SERIAL}")
        push_device.refresh_from_db()
        assert push_device.last_seen_at is not None

    def test_no_command_queue_exists(self, client, push_device):
        """Commands on this endpoint can create, delete and RENUMBER users on
        the terminal. The device is the source of truth for identity, so the
        capability is absent rather than merely unused."""
        response = client.get(f"/iclock/getrequest?SN={SERIAL}")
        body = response.content.decode()
        for dangerous in ("DATA UPDATE USERINFO", "DATA DELETE USERINFO", "CLEAR"):
            assert dangerous not in body

    def test_devicecmd_is_accepted(self, client, push_device):
        assert client.post(f"/iclock/devicecmd?SN={SERIAL}",
                           data=b"ID=1&Return=0", content_type="text/plain"
                           ).status_code == 200


class TestSourceRestriction:
    def test_an_ip_allow_list_is_enforced_when_set(self, client, push_device, settings):
        settings.BIOMETRIC_PUSH_ALLOWED_IPS = "10.9.9.9"
        response = cdata(client, "17\t2026-08-06 09:15:04\t0\t1\n")
        assert response.status_code == 403
        assert AttendancePunch.objects.count() == 0

    def test_the_listed_ip_gets_through(self, client, push_device, settings):
        settings.BIOMETRIC_PUSH_ALLOWED_IPS = "127.0.0.1"
        response = cdata(client, "17\t2026-08-06 09:15:04\t0\t1\n", REMOTE_ADDR="127.0.0.1")
        assert response.status_code == 200

    def test_an_empty_list_does_not_restrict(self, client, push_device, settings):
        settings.BIOMETRIC_PUSH_ALLOWED_IPS = ""
        assert cdata(client, "17\t2026-08-06 09:15:04\t0\t1\n").status_code == 200


# ===========================================================================
# deployment — the two settings whose absence loses data silently
# ===========================================================================
class TestDeploymentWiring:
    def test_iclock_is_exempt_from_the_https_redirect(self):
        """The terminal speaks plain HTTP and will not follow a 301 to https.

        Left enforced, every punch is answered with a redirect the device
        cannot act on, it never receives OK, and it retries until its buffer
        fills. Same shape as the health-check redirect bug already fixed once.
        """
        from django.conf import settings
        assert any("iclock" in pattern for pattern in settings.SECURE_REDIRECT_EXEMPT)

    def test_nginx_routes_iclock_to_the_backend(self):
        """Without this location block /iclock/ hits the SPA fallback and the
        device gets 200 + HTML. Firmware that reads any 200 as success then
        deletes the punches it just sent."""
        from pathlib import Path
        conf = Path(__file__).resolve().parents[3] / "deploy" / "nginx.conf"
        text = conf.read_text()
        assert "location /iclock/" in text
        # It must precede the catch-all, or nginx never reaches it.
        assert text.index("location /iclock/") < text.index("location / {")

    def test_the_push_paths_resolve(self):
        """A 404 on any of these is a device that cannot deliver attendance."""
        from django.urls import resolve
        for path in ("/iclock/cdata", "/iclock/getrequest",
                     "/iclock/devicecmd", "/iclock/ping"):
            assert resolve(path) is not None
