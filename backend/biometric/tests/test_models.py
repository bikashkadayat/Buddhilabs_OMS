"""Model-level guarantees for the biometric raw layer.

The unique constraints here are load-bearing: the collector re-POSTs a device's
entire backlog on every reconnect by design, so anything that lets a duplicate
through turns into double-counted attendance downstream.
"""
from datetime import date

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from biometric.models import (
    ENTRY_PUNCHES,
    EXIT_PUNCHES,
    PUNCH_LABELS,
    AttendancePunch,
    BiometricEmployee,
    punch_label,
)

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# Punch dedup — mirrors morx/models.py dedup_key
# --------------------------------------------------------------------------

def test_identical_punch_is_rejected(make_punch):
    """The exact tuple the collector dedups on must be unique in the DB too."""
    make_punch()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            make_punch()


def test_same_instant_different_punch_type_is_allowed(make_punch, aware_dt):
    """punch is part of the key: an in and an out can share a timestamp."""
    ts = aware_dt(2026, 8, 3, 9, 15)
    make_punch(timestamp=ts, punch=0)
    make_punch(timestamp=ts, punch=1)
    assert AttendancePunch.objects.count() == 2


def test_same_punch_on_different_devices_is_allowed(make_punch, second_device, aware_dt):
    """device is part of the key — two terminals can log the same person."""
    ts = aware_dt(2026, 8, 3, 9, 15)
    make_punch(timestamp=ts)
    make_punch(device=second_device, timestamp=ts)
    assert AttendancePunch.objects.count() == 2


def test_source_is_not_part_of_the_dedup_key(make_punch):
    """A punch seen live and again in the backlog must collapse to one row.

    This is the single most important constraint in the app: HISTORY and LIVE
    deliver the same event, and the collector deliberately excludes `source`
    from its dedup key so they cannot diverge.
    """
    make_punch(source=AttendancePunch.Source.LIVE)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            make_punch(source=AttendancePunch.Source.HISTORY)


def test_verify_status_is_not_part_of_the_dedup_key(make_punch):
    make_punch(verify_status=1)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            make_punch(verify_status=0)


# --------------------------------------------------------------------------
# Punch derivation
# --------------------------------------------------------------------------

def test_punch_label_is_derived_on_save(make_punch):
    assert make_punch(punch=0).punch_label == "check_in"
    assert make_punch(punch=4, timestamp=timezone.now()).punch_label == "overtime_in"


def test_unknown_punch_code_is_preserved_not_dropped(make_punch, aware_dt):
    """Clone firmware emits codes outside 0-5; losing them loses attendance."""
    p = make_punch(punch=9, timestamp=aware_dt(2026, 8, 3, 10, 0))
    assert p.punch_label == "unknown_9"


def test_punch_labels_match_the_collector():
    """Drift here silently relabels historical data."""
    assert PUNCH_LABELS == {
        0: "check_in", 1: "check_out", 2: "break_out",
        3: "break_in", 4: "overtime_in", 5: "overtime_out",
    }
    assert punch_label(3) == "break_in"


def test_entry_and_exit_punch_sets_are_disjoint_and_complete():
    assert ENTRY_PUNCHES.isdisjoint(EXIT_PUNCHES)
    assert ENTRY_PUNCHES | EXIT_PUNCHES == set(PUNCH_LABELS)


def test_local_date_derived_in_project_timezone(device, aware_dt):
    """A 00:30 Kathmandu punch belongs to that day, not the UTC day before.

    Asia/Kathmandu is UTC+5:45, so a naive UTC reading of this instant would
    file the punch under the previous date and break every daily report.
    """
    p = AttendancePunch.objects.create(
        device=device, employee_device_id="1", punch=0,
        timestamp=aware_dt(2026, 8, 3, 0, 30),
    )
    assert p.local_date == date(2026, 8, 3)
    assert timezone.localtime(p.timestamp).hour == 0


def test_is_entry_and_is_exit(make_punch, aware_dt):
    assert make_punch(punch=0).is_entry
    assert make_punch(punch=1, timestamp=aware_dt(2026, 8, 3, 17, 0)).is_exit


# --------------------------------------------------------------------------
# Employee mapping constraints
# --------------------------------------------------------------------------

def test_device_user_id_is_unique_per_device(device, mapping, other_employee):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            BiometricEmployee.objects.create(
                device=device, device_user_id="1", user=other_employee)


def test_same_device_user_id_allowed_on_another_device(second_device, mapping, employee):
    """Device IDs restart at 1 on every terminal — they are only unique per device."""
    BiometricEmployee.objects.create(
        device=second_device, device_user_id="1", user=employee)
    assert BiometricEmployee.objects.filter(device_user_id="1").count() == 2


def test_employee_cannot_hold_two_active_identities_on_one_device(device, mapping, employee):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            BiometricEmployee.objects.create(
                device=device, device_user_id="99", user=employee)


def test_deactivated_mapping_frees_the_employee(device, mapping, employee):
    """Re-enrolling someone under a new device ID must not need a DB surgery."""
    mapping.is_active = False
    mapping.save(update_fields=["is_active"])
    BiometricEmployee.objects.create(
        device=device, device_user_id="99", user=employee, is_active=True)
    assert BiometricEmployee.objects.filter(user=employee, is_active=True).count() == 1


def test_multiple_unmapped_rows_allowed_on_one_device(device):
    """The partial constraint must not collapse NULL users into one row —
    every unrecognised device ID needs its own entry in the HR queue."""
    BiometricEmployee.objects.create(device=device, device_user_id="7")
    BiometricEmployee.objects.create(device=device, device_user_id="8")
    assert BiometricEmployee.objects.filter(user__isnull=True).count() == 2


def test_is_mapped_flag(device, mapping):
    assert mapping.is_mapped
    assert not BiometricEmployee.objects.create(device=device, device_user_id="7").is_mapped


def test_punch_survives_mapping_deletion(make_punch, mapping, employee):
    """Deleting a mapping must never delete attendance evidence."""
    p = make_punch(biometric_employee=mapping, user=employee)
    mapping.delete()
    p.refresh_from_db()
    assert p.biometric_employee_id is None
    assert p.employee_device_id == "1"  # still re-attributable


def test_device_with_punches_cannot_be_deleted(device, make_punch):
    """PROTECT on the device FK: punches are audit data."""
    make_punch()
    from django.db.models import ProtectedError
    with pytest.raises(ProtectedError):
        device.delete()
