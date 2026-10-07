from decimal import Decimal
from datetime import date

from django.db import migrations


LEAVE_TYPES = [
    # code, name, days, is_paid, half, carry, max_carry, doc, notice, color
    ("SICK", "Sick Leave", "12.00", True, True, False, None, True, 0, "#EF4444"),
    ("CASUAL", "Casual Leave", "12.00", True, True, False, None, False, 1, "#F59E0B"),
    ("ANNUAL", "Annual Leave", "18.00", True, True, True, "9.00", False, 3, "#3B82F6"),
    ("MATERNITY", "Maternity Leave", "98.00", True, False, False, None, True, 15, "#EC4899"),
    ("UNPAID", "Unpaid Leave", "0.00", False, True, False, None, False, 0, "#6B7280"),
]

# Sample Nepal public holidays (2026). Admins manage these via the CMS afterward.
HOLIDAYS = [
    (date(2026, 1, 1), "English New Year", "public"),
    (date(2026, 5, 1), "Labour Day", "public"),
    (date(2026, 9, 19), "Constitution Day", "public"),
    (date(2026, 10, 20), "Dashain (Vijaya Dashami)", "religious"),
    (date(2026, 11, 8), "Tihar (Laxmi Puja)", "religious"),
]


def seed(apps, schema_editor):
    LeaveType = apps.get_model("leaves", "LeaveType")
    Holiday = apps.get_model("leaves", "Holiday")

    for code, name, days, is_paid, half, carry, max_carry, doc, notice, color in LEAVE_TYPES:
        LeaveType.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "default_days_per_year": Decimal(days),
                "is_paid": is_paid,
                "allow_half_day": half,
                "allow_carry_forward": carry,
                "max_carry_forward_days": Decimal(max_carry) if max_carry else None,
                "requires_document": doc,
                "min_notice_days": notice,
                "is_active": True,
                "display_color": color,
            },
        )

    for holiday_date, name, htype in HOLIDAYS:
        Holiday.objects.update_or_create(
            date=holiday_date,
            defaults={"name": name, "holiday_type": htype, "is_active": True},
        )


def unseed(apps, schema_editor):
    LeaveType = apps.get_model("leaves", "LeaveType")
    Holiday = apps.get_model("leaves", "Holiday")
    LeaveType.objects.filter(code__in=[lt[0] for lt in LEAVE_TYPES]).delete()
    Holiday.objects.filter(date__in=[h[0] for h in HOLIDAYS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("leaves", "0004_holiday_leavetype_department_leavepolicy_and_more"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
