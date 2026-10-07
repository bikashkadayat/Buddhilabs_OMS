"""Seed the three shifts and the Global policy.

This migration is the safety keystone of Phase 8. Every value is read from the
live ``ATTENDANCE_*`` settings rather than hardcoded, and the behaviours that
did not exist before are seeded OFF:

    grace_minutes            = 0     (no grace period exists today)
    overtime_threshold_hours = NULL  (overtime disabled)
    comp_off_enabled         = False (auto comp-off disabled)
    deduct_breaks            = False

So immediately after migrating, every status the engine computes is identical
to the Phase 7 result. Each new behaviour is opt-in via a policy edit, which is
what makes risks R2 (full-day hours going live) and R5 (retroactive comp-off)
survivable and the rollback plan cheap.

The ``general`` shift's start time is ATTENDANCE_OFFICE_START, so assigning it
as the Global default moves nobody.
"""
from datetime import time

from django.db import migrations

GLOBAL_POLICY_NAME = "Global Policy"
SHIFT_CODES = ["general", "morning", "evening"]


def _parse_time(raw, default_h, default_m=0):
    """Local copy of attendance.services._parse_time.

    Migrations must not import application code: a later refactor of services
    would silently change what this historical migration does.
    """
    try:
        h, m = (int(x) for x in str(raw).split(":"))
        return time(h, m)
    except (ValueError, AttributeError, TypeError):
        return time(default_h, default_m)


def seed(apps, schema_editor):
    from django.conf import settings

    Shift = apps.get_model("attendance", "Shift")
    AttendancePolicy = apps.get_model("attendance", "AttendancePolicy")
    PolicyAssignment = apps.get_model("attendance", "PolicyAssignment")

    office_start = _parse_time(getattr(settings, "ATTENDANCE_OFFICE_START", "10:00"), 10)
    office_end = _parse_time(getattr(settings, "ATTENDANCE_ABSENT_CUTOFF", "18:00"), 18)

    specs = [
        ("general", "General Shift", office_start, office_end),
        ("morning", "Morning Shift", time(6, 0), time(14, 0)),
        ("evening", "Evening Shift", time(14, 0), time(22, 0)),
    ]
    shifts = {}
    for code, name, start, end in specs:
        shift, _ = Shift.objects.get_or_create(
            code=code,
            defaults={"name": name, "start_time": start, "end_time": end,
                      "grace_minutes": 0, "break_minutes": 0,
                      "crosses_midnight": False, "is_active": True},
        )
        shifts[code] = shift

    policy, _ = AttendancePolicy.objects.get_or_create(
        name=GLOBAL_POLICY_NAME,
        defaults={
            "description": "Seeded from ATTENDANCE_* settings; "
                           "behaviour-identical to Phase 7.",
            "is_active": True,
            "office_start_time": office_start,
            "absent_cutoff_time": office_end,
            "grace_minutes": 0,
            "half_day_hours": str(getattr(settings, "ATTENDANCE_HALF_DAY_HOURS", 5)),
            "full_day_hours": str(getattr(settings, "ATTENDANCE_FULL_DAY_HOURS", 8)),
            "deduct_breaks": False,
            "overtime_threshold_hours": None,
            "overtime_min_minutes": 30,
            "comp_off_enabled": False,
            "default_shift": shifts["general"],
        },
    )

    # effective_from is the epoch-ish floor rather than "today": the Global
    # policy must cover every historical day so re-deriving an old month
    # resolves a real policy instead of the settings fallback. Comp-off is
    # gated separately (and is off here anyway), so this cannot back-date
    # earned leave.
    PolicyAssignment.objects.get_or_create(
        scope="global", department=None, user=None, effective_until=None,
        defaults={"policy": policy, "effective_from": date_floor()},
    )


def date_floor():
    from datetime import date

    return date(2000, 1, 1)


def unseed(apps, schema_editor):
    """Remove only what seed() created. Any policy an admin added survives."""
    Shift = apps.get_model("attendance", "Shift")
    AttendancePolicy = apps.get_model("attendance", "AttendancePolicy")
    PolicyAssignment = apps.get_model("attendance", "PolicyAssignment")

    PolicyAssignment.objects.filter(
        scope="global", policy__name=GLOBAL_POLICY_NAME).delete()
    AttendancePolicy.objects.filter(name=GLOBAL_POLICY_NAME).delete()
    # Only delete a seeded shift if nobody was ever put on it.
    Shift.objects.filter(code__in=SHIFT_CODES, assignments__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0004_policy_engine_models"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
