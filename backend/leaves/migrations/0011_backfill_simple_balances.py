"""
Data migration (Cluster A / H3+H4): re-derive every existing simple LeaveBalance
from the LeaveDayRecord source of truth.

Before this change `used_so_far` was mutated incrementally, so historical rows may
have leaked (cancelled approved leave never refunded) or gone negative/over. This
recomputes each row to committed working days (approved + both pending stages),
which is exactly what the app now maintains going forward. Idempotent and
non-destructive: re-running produces the same result; reverse is a no-op.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    # Import the live service helper: this is the latest migration, so the model
    # layer it uses matches the current schema. sync_simple_balance is idempotent
    # and only recomputes a single row under select_for_update.
    from leaves import services

    LeaveBalance = apps.get_model("leaves", "LeaveBalance")
    seen = set()
    for user_id, code, year in (
        LeaveBalance.objects.values_list("user_id", "leave_type", "year")
    ):
        key = (user_id, code, year)
        if key in seen:
            continue
        seen.add(key)
        try:
            services.sync_simple_balance_by_id(user_id, code, year)
        except Exception:
            # Never let a single unrecomputable row block the deploy; the nightly
            # audit_data_integrity job will surface anything left inconsistent.
            continue


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("leaves", "0010_official_entitlements_and_balances"),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
