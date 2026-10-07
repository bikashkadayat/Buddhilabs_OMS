"""
Apply the organisation's leave policy for Interns and Volunteers.

WHAT WAS WRONG
--------------
Category D meant "Intern / Volunteer" and granted both 8 annual and 8 sick
days. The policy grants an intern 4 and 5, and a volunteer 2 and 3 — so every
volunteer on the system held four times the annual leave the policy allows, and
every intern twice it. Compensatory leave was marked applicable for both, which
the policy gives to neither.

Maternity and paternity were marked `applicable=False` for D, which reads as
"not offered". For an intern the policy is "as per organization policy" — real
leave, arranged case by case — so those rows become applicable with the new
`by_arrangement` flag instead. For a volunteer "Not Applicable" is correct and
`applicable=False` stays.

EXISTING RECORDS ARE NOT REWRITTEN. This corrects the entitlement matrix and
moves volunteers onto Category E; leave already taken, approved or recorded is
history and is left exactly as it stands. Balances re-derive from the corrected
matrix on the next sync, which is what makes the change safe to apply mid-year.
"""
from decimal import Decimal

from django.db import migrations

# Frozen copy — migrations must not import app code, which changes underneath
# them. leaves.category_engine.ENTITLEMENT_MATRIX holds the live version and
# `test_matrix_matches_policy` asserts the two agree.
# (code, days, is_working_day_based, applicable, by_arrangement)
POLICY = {
    "D": [("ANNUAL", 4, True, True, False), ("SICK", 5, True, True, False),
          ("MATERNITY", 0, False, True, True), ("PATERNITY", 0, False, True, True),
          ("COMPENSATORY", 0, False, False, False)],
    "E": [("ANNUAL", 2, True, True, False), ("SICK", 3, True, True, False),
          ("MATERNITY", 0, False, False, False), ("PATERNITY", 0, False, False, False),
          ("COMPENSATORY", 0, False, False, False)],
}

# What D held before, so the migration can be reversed cleanly.
PREVIOUS_D = [("ANNUAL", 8, True, True, False), ("SICK", 8, True, True, False),
              ("MATERNITY", 0, False, False, False), ("PATERNITY", 0, False, False, False),
              ("COMPENSATORY", 0, False, True, False)]


def _write(EntitlementRule, LeaveType, category, rows):
    for code, days, working, applicable, by_arrangement in rows:
        leave_type = LeaveType.objects.filter(code=code).first()
        if leave_type is None:      # a deployment without the seeded types
            continue
        EntitlementRule.objects.update_or_create(
            category=category, leave_type=leave_type,
            defaults={
                "entitlement_days": Decimal(str(days)),
                "is_working_day_based": working,
                "applicable": applicable,
                "by_arrangement": by_arrangement,
            },
        )


def forwards(apps, schema_editor):
    EntitlementRule = apps.get_model("leaves", "EntitlementRule")
    LeaveType = apps.get_model("leaves", "LeaveType")
    User = apps.get_model("users", "User")

    for category, rows in POLICY.items():
        _write(EntitlementRule, LeaveType, category, rows)

    # Move existing volunteers onto their own category. The engine would do this
    # on their next resolution anyway; doing it here means nobody is shown an
    # intern's entitlement in the window before that happens.
    User.objects.filter(employment_type="volunteer").update(leave_category="E")


def backwards(apps, schema_editor):
    EntitlementRule = apps.get_model("leaves", "EntitlementRule")
    LeaveType = apps.get_model("leaves", "LeaveType")
    User = apps.get_model("users", "User")

    _write(EntitlementRule, LeaveType, "D", PREVIOUS_D)
    User.objects.filter(leave_category="E").update(leave_category="D")
    EntitlementRule.objects.filter(category="E").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("leaves", "0018_entitlementrule_by_arrangement_and_more"),
        ("users", "0010_alter_user_leave_category"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
