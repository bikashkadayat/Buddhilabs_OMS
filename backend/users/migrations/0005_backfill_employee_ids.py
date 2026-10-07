"""
Backfill existing users:
  * assign each a sequential employee_id (NIFN-EMP-<join-year>-XXXX),
  * clear must_change_password (they already own their passwords; only future
    admin-created accounts are forced to change on first login).
"""
from django.db import migrations


def backfill(apps, schema_editor):
    User = apps.get_model("users", "User")
    from django.utils import timezone

    counters = {}
    for user in User.objects.order_by("date_joined", "id"):
        year = (user.date_joined or timezone.now()).year
        counters[year] = counters.get(year, 0) + 1
        if not user.employee_id:
            user.employee_id = f"NIFN-EMP-{year}-{counters[year]:04d}"
        user.must_change_password = False
        user.save(update_fields=["employee_id", "must_change_password"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0004_user_created_by_user_date_of_joining_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
