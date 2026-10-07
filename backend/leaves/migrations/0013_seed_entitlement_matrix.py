"""
Seed the DB-configurable entitlement engine:
  * new LeaveTypes: MATERNITY, PATERNITY, COMPENSATORY (idempotent get_or_create)
  * EntitlementRule matrix for categories A/B/C/D/PROBATION.

HR/Admin can edit these rows afterwards; this only establishes the defaults.
Idempotent (update_or_create) and reversible (removes only the seeded rules).
"""
from decimal import Decimal

from django.db import migrations

# Category -> [(leave_type_code, days, is_working_day_based, applicable)]
MATRIX = {
    "A": [("ANNUAL", 12, True, True), ("SICK", 12, True, True),
          ("MATERNITY", 30, False, True), ("PATERNITY", 15, False, True),
          ("COMPENSATORY", 0, False, True)],
    "B": [("ANNUAL", 10, True, True), ("SICK", 10, True, True),
          ("MATERNITY", 30, False, True), ("PATERNITY", 15, False, True),
          ("COMPENSATORY", 0, False, True)],
    "C": [("ANNUAL", 8, True, True), ("SICK", 10, True, True),
          ("MATERNITY", 30, False, True), ("PATERNITY", 15, False, True),
          ("COMPENSATORY", 0, False, True)],
    "D": [("ANNUAL", 8, True, True), ("SICK", 8, True, True),
          ("MATERNITY", 0, False, False), ("PATERNITY", 0, False, False),
          ("COMPENSATORY", 0, False, True)],
    "PROBATION": [("ANNUAL", 0, True, True), ("SICK", 5, True, True),
                  ("MATERNITY", 30, False, True), ("PATERNITY", 15, False, True),
                  ("COMPENSATORY", 0, False, True)],
}

# code -> (name, default_days, is_paid, allow_half_day, allow_carry_forward, color)
NEW_LEAVE_TYPES = {
    "MATERNITY": ("Maternity Leave", "30.00", True, False, False, "#DB2777"),
    "PATERNITY": ("Paternity Leave", "15.00", True, False, False, "#7C3AED"),
    "COMPENSATORY": ("Compensatory Leave", "0.00", True, True, True, "#0891B2"),
}


def seed(apps, schema_editor):
    LeaveType = apps.get_model("leaves", "LeaveType")
    EntitlementRule = apps.get_model("leaves", "EntitlementRule")

    for code, (name, days, paid, half, carry, color) in NEW_LEAVE_TYPES.items():
        LeaveType.objects.get_or_create(
            code=code,
            defaults={
                "name": name, "default_days_per_year": Decimal(days),
                "is_paid": paid, "allow_half_day": half,
                "allow_carry_forward": carry, "is_active": True,
                "display_color": color,
            },
        )

    by_code = {lt.code.upper(): lt for lt in LeaveType.objects.all()}
    for category, rows in MATRIX.items():
        for code, day_count, wdb, applicable in rows:
            lt = by_code.get(code)
            if lt is None:
                continue
            EntitlementRule.objects.update_or_create(
                category=category, leave_type=lt,
                defaults={
                    "entitlement_days": Decimal(str(day_count)),
                    "is_working_day_based": wdb,
                    "applicable": applicable,
                },
            )


def unseed(apps, schema_editor):
    EntitlementRule = apps.get_model("leaves", "EntitlementRule")
    EntitlementRule.objects.filter(category__in=list(MATRIX.keys())).delete()
    # LeaveTypes are left in place (they may now hold history / balances).


class Migration(migrations.Migration):

    dependencies = [
        ("leaves", "0012_compensatoryledger_entitlementrule"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
