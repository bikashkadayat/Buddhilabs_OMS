"""The device is the source of truth for identity — asserted, not assumed.

Device User ID X must remain ``BiometricEmployee.device_user_id = X`` forever,
and every punch must stay attached to the ID that created it.

These tests exist because a renumbering is **silently** catastrophic. Historical
punches keep the ``employee_device_id`` they were ingested with, so nothing
raises and nothing looks wrong — but future punches from the original ID stop
resolving, punches from the new ID land on the wrong person, and the first
symptom is a wrong payslip weeks later. There is no error to catch, so the
guarantee has to be enforced and tested.
"""
from datetime import date, datetime, timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from biometric.models import AttendancePunch, BiometricDevice, BiometricEmployee
from users.models import User

pytestmark = pytest.mark.django_db


def run(command, *args, **kwargs):
    out = StringIO()
    call_command(command, *args, stdout=out, stderr=StringIO(), **kwargs)
    return out.getvalue()


@pytest.fixture
def device(db):
    return BiometricDevice.objects.create(
        name="Main Gate", label="main-gate", host="192.168.77.201",
        is_active=True, api_key_hash="0" * 64)


@pytest.fixture
def employee(db):
    user = User.objects.create_user(
        username="id_emp", email="id.emp@nif.test", password="pass12345",
        first_name="Bikash", last_name="Kadayat", role=User.Roles.MAKER,
        employee_id="NIFN-EMP-2026-0017", date_of_joining=date(2020, 1, 1))
    User.objects.filter(pk=user.pk).update(
        date_joined=timezone.make_aware(datetime(2020, 1, 1, 9, 0)))
    return user


@pytest.fixture
def other_employee(db):
    return User.objects.create_user(
        username="id_other", email="id.other@nif.test", password="pass12345",
        first_name="Sita", last_name="Gurung", role=User.Roles.MAKER)


@pytest.fixture
def admin(db):
    return User.objects.create_user(
        username="id_admin", email="id.admin@nif.test", password="pass12345",
        first_name="Ops", last_name="Admin", role=User.Roles.ADMIN)


@pytest.fixture
def mapping(device, employee):
    return BiometricEmployee.objects.create(
        device=device, device_user_id="17", device_name="Bikashkadayat",
        card=4017, user=employee, is_active=True)


# ===========================================================================
# the invariant
# ===========================================================================
class TestDeviceUserIdIsImmutable:
    def test_the_model_refuses_to_change_it(self, mapping):
        mapping.device_user_id = "99"
        with pytest.raises(ValueError, match="immutable"):
            mapping.save()

    def test_the_error_names_the_correct_procedure(self, mapping):
        """A refusal that does not say what to do instead gets worked around."""
        mapping.device_user_id = "99"
        with pytest.raises(ValueError, match="remap_device_user"):
            mapping.save()

    def test_the_device_cannot_be_changed_either(self, mapping, db):
        other = BiometricDevice.objects.create(
            name="Warehouse", label="warehouse", is_active=True)
        mapping.device = other
        with pytest.raises(ValueError, match="immutable"):
            mapping.save()

    def test_everything_else_stays_editable(self, mapping, other_employee):
        """Immutable identity, not an immutable row — HR still has to be able to
        correct the mapping, the validity window and the roster snapshot."""
        mapping.user = other_employee
        mapping.device_name = "Corrected Name"
        mapping.effective_from = date(2026, 7, 14)
        mapping.save()

        mapping.refresh_from_db()
        assert mapping.user == other_employee
        assert mapping.device_name == "Corrected Name"
        assert mapping.device_user_id == "17"   # unchanged throughout

    def test_a_no_op_save_is_fine(self, mapping):
        mapping.device_user_id = "17"  # same value, re-assigned
        mapping.save()
        assert BiometricEmployee.objects.get(pk=mapping.pk).device_user_id == "17"

    def test_creating_a_mapping_still_works(self, device, employee):
        created = BiometricEmployee.objects.create(
            device=device, device_user_id="42", user=employee, is_active=True)
        assert created.device_user_id == "42"


class TestApiCannotRenumber:
    """The hole this closed: BiometricEmployeeViewSet is a ModelViewSet and
    device_user_id was writable, so a PATCH could repoint an enrolment."""

    def test_patch_cannot_change_the_device_user_id(self, mapping, admin):
        client = APIClient()
        client.force_authenticate(user=admin)
        response = client.patch(
            f"/api/v1/biometric/mappings/{mapping.pk}/",
            {"device_user_id": "99"}, format="json")

        assert response.status_code in (200, 400)
        mapping.refresh_from_db()
        assert mapping.device_user_id == "17", "the API renumbered a device ID"

    def test_put_cannot_change_it_either(self, mapping, admin, device, employee):
        client = APIClient()
        client.force_authenticate(user=admin)
        client.put(
            f"/api/v1/biometric/mappings/{mapping.pk}/",
            {"device": str(device.pk), "device_user_id": "99",
             "user": str(employee.pk), "is_active": True}, format="json")

        mapping.refresh_from_db()
        assert mapping.device_user_id == "17"

    def test_the_mapping_itself_can_still_be_corrected(self, mapping, admin,
                                                       other_employee):
        client = APIClient()
        client.force_authenticate(user=admin)
        response = client.patch(
            f"/api/v1/biometric/mappings/{mapping.pk}/",
            {"device_name": "Renamed"}, format="json")
        assert response.status_code == 200
        mapping.refresh_from_db()
        assert mapping.device_name == "Renamed"
        assert mapping.device_user_id == "17"


class TestPunchesStayAttachedToTheirOriginalId:
    def test_a_punch_keeps_the_id_it_arrived_with(self, device, employee, mapping):
        punch = AttendancePunch.objects.create(
            device=device, employee_device_id="17", user=employee,
            timestamp=timezone.now(), local_date=date(2026, 7, 14),
            punch=0, punch_label="in")

        # Remapping to a different PERSON must not touch the device ID on
        # history: the punch was made by device user 17 and always will have been.
        mapping.user = None
        mapping.save()

        punch.refresh_from_db()
        assert punch.employee_device_id == "17"

    def test_historical_punches_survive_a_retire_and_reassign(
            self, device, employee, other_employee, mapping):
        """A device ID reassigned to a new hire must not hand the leaver's
        history to their replacement."""
        old_punch = AttendancePunch.objects.create(
            device=device, employee_device_id="17", user=employee,
            timestamp=timezone.now() - timedelta(days=30),
            local_date=date(2026, 7, 14), punch=0, punch_label="in")

        # Retire, then create the successor with the SAME device ID.
        mapping.is_active = False
        mapping.effective_until = date(2026, 7, 20)
        mapping.save()
        successor = BiometricEmployee.objects.create(
            device=device, device_user_id="17", user=other_employee,
            is_active=True, effective_from=date(2026, 7, 21))

        old_punch.refresh_from_db()
        assert old_punch.employee_device_id == "17"
        assert old_punch.user == employee, "the leaver's punch was reassigned"
        assert successor.device_user_id == "17"


# ===========================================================================
# the verification report
# ===========================================================================
class TestVerificationReport:
    def test_it_reports_every_required_section(self, mapping):
        output = run("verify_biometric_ids")
        for heading in ("Device user count", "Device user IDs",
                        "OMS user mappings", "Mapping conflicts",
                        "Unmapped users", "Historical punch integrity"):
            assert heading in output

    def test_a_clean_system_passes(self, mapping):
        output = run("verify_biometric_ids")
        assert "PASS" in output
        assert "zero biometric ID changes" in output

    def test_unmapped_enrolments_block_a_pass(self, device):
        BiometricEmployee.objects.create(
            device=device, device_user_id="8", device_name="Ghost", is_active=True)
        output = run("verify_biometric_ids")
        assert "INCOMPLETE" in output

    def test_ids_sort_numerically_not_lexically(self, device, employee):
        for value in ("2", "10", "1"):
            BiometricEmployee.objects.create(
                device=device, device_user_id=value, is_active=True)
        output = run("verify_biometric_ids")
        assert "1, 2, 10" in output, "IDs must read 1, 2, 10 — not 1, 10, 2"

    def test_it_compares_against_the_device_roster(self, mapping):
        output = run("verify_biometric_ids", "--expect", "17")
        assert "EXACT MATCH" in output

    def test_an_id_on_the_device_but_missing_from_the_oms_is_an_error(self, mapping):
        output = run("verify_biometric_ids", "--expect", "17,18,19")
        assert "MISSING from the OMS" in output
        assert "18" in output and "19" in output

    def test_an_id_in_the_oms_but_not_on_the_device_is_explained(self, mapping):
        output = run("verify_biometric_ids", "--expect", "99")
        assert "not on the device" in output
        # ...and the mapping must be kept, because history hangs off it.
        assert "historical" in output and "attached" in output

    def test_one_employee_cannot_hold_two_ids_on_one_device(self, device, employee):
        """Stronger than the report check: the database refuses the state.

        The conflict detector still looks for it (cheap, and defence in depth
        against a future migration relaxing the constraint), but the real
        guarantee is that this row cannot exist — so the test asserts the
        constraint, not the report.
        """
        from django.db.utils import IntegrityError

        BiometricEmployee.objects.create(
            device=device, device_user_id="17", user=employee, is_active=True)
        with pytest.raises(IntegrityError):
            BiometricEmployee.objects.create(
                device=device, device_user_id="18", user=employee, is_active=True)

    def test_punches_from_an_unenrolled_id_are_surfaced(self, device, employee,
                                                        mapping):
        AttendancePunch.objects.create(
            device=device, employee_device_id="404", user=None,
            timestamp=timezone.now(), local_date=timezone.localdate(),
            punch=0, punch_label="in")
        output = run("verify_biometric_ids")
        assert "404" in output
        assert "NOT enrolled" in output

    def test_json_output_carries_every_section(self, mapping):
        import json

        report = json.loads(run("verify_biometric_ids", "--json"))
        for key in ("device_user_count", "device_user_ids", "mappings",
                    "conflicts", "unmapped", "punch_integrity"):
            assert key in report
        assert report["device_user_ids"] == ["17"]
