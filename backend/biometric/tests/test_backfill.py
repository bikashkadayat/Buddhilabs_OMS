"""Backfill and the recycled-device-ID scenario.

Backfill is the one operation in this app that rewrites existing attribution,
so every guard around it is tested: it never runs automatically, it never runs
unbounded by accident, and it never reaches into a previous occupant's history.
"""
from datetime import date, timedelta

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from audit.models import AuditLog
from biometric.models import AttendancePunch, BiometricEmployee
from biometric.services import (
    backfill_punches,
    implicit_backfill_floor,
    map_employee,
    preview_backfill,
    remap_employee,
    unmap_employee,
)

from .conftest import make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def unmapped_row(device):
    return BiometricEmployee.objects.create(
        device=device, device_user_id="17", device_name="Bikash")


def punches_on(device, dates, device_user_id="17", **extra):
    made = []
    for i, d in enumerate(dates):
        made.append(AttendancePunch.objects.create(
            device=device, employee_device_id=device_user_id,
            timestamp=timezone.make_aware(
                timezone.datetime(d.year, d.month, d.day, 9, 0) + timedelta(seconds=i)),
            punch=0, **extra,
        ))
    return made


# --------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------

def test_preview_requires_a_mapped_user(unmapped_row):
    with pytest.raises(ValidationError):
        preview_backfill(unmapped_row)


def test_preview_reports_count_range_device_and_user(unmapped_row, employee, hr):
    punches_on(unmapped_row.device, [date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 3)])
    map_employee(unmapped_row, employee, actor=hr, effective_from=date(2026, 8, 1))

    preview = preview_backfill(unmapped_row)
    assert preview["records_count"] == 3
    assert preview["first_punch_date"] == date(2026, 8, 1)
    assert preview["last_punch_date"] == date(2026, 8, 3)
    assert preview["device_user_id"] == "17"
    assert preview["user_name"] == employee.get_full_name()
    assert preview["unbounded"] is False
    assert len(preview["sample"]) == 3


def test_preview_does_not_write_anything(unmapped_row, employee, hr):
    punches_on(unmapped_row.device, [date(2026, 8, 1)])
    map_employee(unmapped_row, employee, actor=hr, effective_from=date(2026, 8, 1))
    preview_backfill(unmapped_row)
    assert AttendancePunch.objects.filter(user__isnull=True).count() == 1


def test_preview_warns_when_unbounded(unmapped_row, employee, hr):
    punches_on(unmapped_row.device, [date(2026, 8, 1)])
    map_employee(unmapped_row, employee, actor=hr)  # no effective_from
    preview = preview_backfill(unmapped_row)
    assert preview["unbounded"] is True
    assert "another employee's attendance" in preview["warning"]


def test_preview_respects_an_explicit_range(unmapped_row, employee, hr):
    punches_on(unmapped_row.device, [date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)])
    map_employee(unmapped_row, employee, actor=hr)
    preview = preview_backfill(unmapped_row, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    assert preview["records_count"] == 1


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

def test_mapping_alone_never_backfills(unmapped_row, employee, hr):
    """Creating a mapping must leave history untouched — this is the whole
    reason map and backfill are separate operations."""
    punches_on(unmapped_row.device, [date(2026, 8, 1), date(2026, 8, 2)])
    map_employee(unmapped_row, employee, actor=hr, effective_from=date(2026, 8, 1))
    assert AttendancePunch.objects.filter(user=employee).count() == 0
    assert AttendancePunch.objects.filter(user__isnull=True).count() == 2


def test_backfill_attaches_punches_and_derives_attendance(unmapped_row, employee, hr):
    """Since Phase 5, backfill also derives inline — the claimed history shows
    up as real attendance immediately instead of waiting for the cron sweep."""
    from attendance.models import Attendance

    punches_on(unmapped_row.device, [date(2026, 8, 1), date(2026, 8, 2)])
    map_employee(unmapped_row, employee, actor=hr, effective_from=date(2026, 8, 1))

    updated = backfill_punches(unmapped_row, actor=hr)
    assert updated == 2
    claimed = AttendancePunch.objects.filter(user=employee)
    assert claimed.count() == 2
    assert all(p.biometric_employee_id == unmapped_row.pk for p in claimed)

    # Derived, so nothing is left queued.
    assert claimed.filter(is_processed=False).count() == 0
    assert Attendance.objects.filter(
        employee=employee, source=Attendance.Source.BIOMETRIC).count() == 2


def test_unbounded_backfill_is_refused(unmapped_row, employee, hr):
    punches_on(unmapped_row.device, [date(2023, 1, 8)])
    map_employee(unmapped_row, employee, actor=hr)  # no effective_from
    with pytest.raises(ValidationError, match="unbounded"):
        backfill_punches(unmapped_row, actor=hr)
    assert AttendancePunch.objects.filter(user=employee).count() == 0


def test_unbounded_backfill_runs_with_explicit_override(unmapped_row, employee, hr):
    punches_on(unmapped_row.device, [date(2023, 1, 8)])
    map_employee(unmapped_row, employee, actor=hr)
    assert backfill_punches(unmapped_row, actor=hr, allow_unbounded=True) == 1


def test_backfill_requires_a_mapped_user(unmapped_row, hr):
    punches_on(unmapped_row.device, [date(2026, 8, 1)])
    with pytest.raises(ValidationError):
        backfill_punches(unmapped_row, actor=hr, allow_unbounded=True)


def test_backfill_never_steals_already_attributed_punches(unmapped_row, employee,
                                                          other_employee, hr):
    device = unmapped_row.device
    punches_on(device, [date(2026, 8, 1)])
    AttendancePunch.objects.update(user=other_employee)  # already someone else's
    punches_on(device, [date(2026, 8, 2)])

    map_employee(unmapped_row, employee, actor=hr, effective_from=date(2026, 1, 1))
    assert backfill_punches(unmapped_row, actor=hr) == 1
    assert AttendancePunch.objects.filter(user=other_employee).count() == 1


def test_backfill_is_idempotent(unmapped_row, employee, hr):
    punches_on(unmapped_row.device, [date(2026, 8, 1)])
    map_employee(unmapped_row, employee, actor=hr, effective_from=date(2026, 8, 1))
    assert backfill_punches(unmapped_row, actor=hr) == 1
    assert backfill_punches(unmapped_row, actor=hr) == 0


def test_backfill_writes_an_audit_entry(unmapped_row, employee, hr):
    punches_on(unmapped_row.device, [date(2026, 8, 1)])
    map_employee(unmapped_row, employee, actor=hr, effective_from=date(2026, 8, 1))
    backfill_punches(unmapped_row, actor=hr)

    entry = AuditLog.objects.filter(changes__event="BIOMETRIC_BACKFILL_EXECUTED").first()
    assert entry is not None
    assert entry.actor == hr
    assert entry.changes["records_updated"] == 1
    assert entry.changes["device_user_id"] == "17"


# --------------------------------------------------------------------------
# THE recycled device ID scenario
# --------------------------------------------------------------------------

def test_recycled_device_id_backfill_cannot_reach_the_previous_occupant(device, hr, dept):
    """Device 17 was Hari until he left; it is Bikash now.

    Backfilling Bikash must not hand him Hari's two years of attendance. This is
    the scenario the entire validity-window design exists for.
    """
    hari = make_user("recycled_hari", dept=dept, first_name="Hari", last_name="Old")
    bikash = make_user("recycled_bikash", dept=dept, first_name="Bikash", last_name="New")

    mapping = BiometricEmployee.objects.create(
        device=device, device_user_id="17", device_name="Hari")
    map_employee(mapping, hari, actor=hr, effective_from=date(2023, 1, 1))

    hari_punches = punches_on(device, [date(2023, 6, 1), date(2024, 3, 1)])
    backfill_punches(mapping, actor=hr)
    assert AttendancePunch.objects.filter(user=hari).count() == 2

    # Hari leaves; HR reassigns device 17 to Bikash.
    replacement = remap_employee(mapping, bikash, actor=hr)
    mapping.refresh_from_db()
    assert mapping.is_active is False
    assert mapping.effective_until is not None
    assert mapping.superseded_by_id == replacement.pk
    assert replacement.effective_from == mapping.effective_until + timedelta(days=1)

    # Hari's punches keep their attribution — a remap is not a detach.
    assert AttendancePunch.objects.filter(user=hari).count() == 2

    # Hari is later hard-deleted: his punches fall back to unattributed.
    hari.delete()
    assert AttendancePunch.objects.filter(
        pk__in=[p.pk for p in hari_punches], user__isnull=True).count() == 2

    # Bikash starts punching.
    punches_on(device, [date.today()])

    preview = preview_backfill(replacement)
    assert preview["records_count"] == 1, "backfill must not see Hari's orphaned punches"
    assert preview["date_from"] == replacement.effective_from

    assert backfill_punches(replacement, actor=hr) == 1
    assert AttendancePunch.objects.filter(user=bikash).count() == 1
    # Hari's punches remain orphaned rather than being handed to Bikash.
    assert AttendancePunch.objects.filter(
        pk__in=[p.pk for p in hari_punches], user__isnull=True).count() == 2


def test_implicit_floor_comes_from_the_previous_occupants_window(device, hr, dept):
    """Even with no effective_from set, a retired predecessor bounds the claim."""
    old = BiometricEmployee.objects.create(
        device=device, device_user_id="17", is_active=False,
        effective_until=date(2024, 12, 31))
    fresh = BiometricEmployee.objects.create(device=device, device_user_id="17")
    assert fresh.effective_from is None
    assert implicit_backfill_floor(fresh) == date(2025, 1, 1)
    assert implicit_backfill_floor(old) is None


def test_deleted_user_leaves_an_unbounded_mapping_that_refuses_backfill(
        device, employee, hr):
    """The hazard path: a hard-deleted user leaves an active mapping with
    user=NULL and no end date. The unbounded guard is the only thing standing
    between that and misattribution, so it must hold."""
    mapping = BiometricEmployee.objects.create(device=device, device_user_id="17")
    map_employee(mapping, employee, actor=hr)
    punches_on(device, [date(2024, 1, 1)])
    backfill_punches(mapping, actor=hr, allow_unbounded=True)

    employee.delete()
    mapping.refresh_from_db()
    assert mapping.user_id is None and mapping.is_active is True

    newcomer = make_user("newcomer", first_name="New", last_name="Comer")
    map_employee(mapping, newcomer, actor=hr)
    with pytest.raises(ValidationError, match="unbounded"):
        backfill_punches(mapping, actor=hr)


# --------------------------------------------------------------------------
# Soft unmap
# --------------------------------------------------------------------------

def test_unmap_is_soft_and_keeps_the_row(unmapped_row, employee, hr):
    map_employee(unmapped_row, employee, actor=hr, effective_from=date(2026, 1, 1))
    mapping, _ = unmap_employee(unmapped_row, actor=hr)

    assert BiometricEmployee.objects.filter(pk=mapping.pk).exists(), "row must not be deleted"
    assert mapping.user_id is None
    assert mapping.is_active is False
    assert mapping.effective_until == timezone.localdate()


def test_unmap_detaches_punches_and_queues_rederivation(unmapped_row, employee, hr):
    punches_on(unmapped_row.device, [date(2026, 8, 1), date(2026, 8, 2)])
    map_employee(unmapped_row, employee, actor=hr, effective_from=date(2026, 8, 1))
    backfill_punches(unmapped_row, actor=hr)
    AttendancePunch.objects.update(is_processed=True)

    _, detached = unmap_employee(unmapped_row, actor=hr)
    assert detached == 2
    assert AttendancePunch.objects.filter(user__isnull=True).count() == 2
    assert AttendancePunch.objects.filter(is_processed=False).count() == 2


def test_unmap_can_keep_attribution(unmapped_row, employee, hr):
    punches_on(unmapped_row.device, [date(2026, 8, 1)])
    map_employee(unmapped_row, employee, actor=hr, effective_from=date(2026, 8, 1))
    backfill_punches(unmapped_row, actor=hr)

    _, detached = unmap_employee(unmapped_row, actor=hr, detach_punches=False)
    assert detached == 0
    assert AttendancePunch.objects.filter(user=employee).count() == 1


def test_unmap_frees_the_employee_for_a_new_device_id(unmapped_row, employee, hr, device):
    map_employee(unmapped_row, employee, actor=hr)
    unmap_employee(unmapped_row, actor=hr)
    fresh = BiometricEmployee.objects.create(device=device, device_user_id="18")
    map_employee(fresh, employee, actor=hr)
    assert fresh.user == employee


def test_unmap_is_audited(unmapped_row, employee, hr):
    map_employee(unmapped_row, employee, actor=hr)
    unmap_employee(unmapped_row, actor=hr)
    entry = AuditLog.objects.filter(changes__event="BIOMETRIC_MAPPING_UNMAPPED").first()
    assert entry is not None
    assert entry.changes["previous_user_name"] == employee.get_full_name()
