from decimal import Decimal
from datetime import timedelta

from django.db import migrations

# Official NIF policy. Casual is corrected from the old 12 to 6.
ENTITLEMENTS = {"annual": 18, "sick": 12, "casual": 6}
YEAR = 2026  # current operating year (today: 2026)


def _working_days(start, end, holiday_set):
    """Count working days, skipping Saturday (weekday 5) + public holidays."""
    if end < start:
        return 0
    d, n = start, 0
    while d <= end:
        if d.weekday() != 5 and d not in holiday_set:
            n += 1
        d += timedelta(days=1)
    return n


def seed(apps, schema_editor):
    LeaveType = apps.get_model("leaves", "LeaveType")
    LeaveBalance = apps.get_model("leaves", "LeaveBalance")
    Leave = apps.get_model("leaves", "Leave")
    Holiday = apps.get_model("leaves", "Holiday")
    User = apps.get_model("users", "User")

    # 1) Correct the official Casual entitlement (18 / 12 / 6).
    LeaveType.objects.filter(code__iexact="casual").update(default_days_per_year=Decimal("6.00"))

    holiday_set = set(Holiday.objects.filter(is_active=True).values_list("date", flat=True))

    # 2) Seed simple-model balances for every existing user, computing used days
    #    from their already-approved leaves so history stays accurate.
    for u in User.objects.all():
        for code, days in ENTITLEMENTS.items():
            used = 0
            for lv in Leave.objects.filter(user=u, leave_type=code, status="approved", is_deleted=False):
                if lv.start_date and lv.start_date.year == YEAR:
                    used += _working_days(lv.start_date, lv.end_date, holiday_set)
            LeaveBalance.objects.update_or_create(
                user=u, leave_type=code, year=YEAR,
                defaults={"total_allocated": days, "used_so_far": used},
            )


def unseed(apps, schema_editor):
    # Non-destructive reverse: keep balances (they hold real usage).
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("leaves", "0009_seed_departments"),
        ("users", "0006_user_employee_type_alter_user_role"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
