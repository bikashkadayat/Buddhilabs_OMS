"""Idempotently seed the default LeaveTypes. Safe to re-run."""
from decimal import Decimal

from django.core.management.base import BaseCommand

from leaves.models import LeaveType

DEFAULTS = [
    # code, name, days, is_paid, half, carry, max_carry, doc, notice, color
    ("SICK", "Sick Leave", "12.00", True, True, False, None, True, 0, "#EF4444"),
    ("CASUAL", "Casual Leave", "12.00", True, True, False, None, False, 1, "#F59E0B"),
    ("ANNUAL", "Annual Leave", "18.00", True, True, True, "9.00", False, 3, "#3B82F6"),
    ("MATERNITY", "Maternity Leave", "98.00", True, False, False, None, True, 15, "#EC4899"),
    ("UNPAID", "Unpaid Leave", "0.00", False, True, False, None, False, 0, "#6B7280"),
]


class Command(BaseCommand):
    help = "Create the default leave types if they do not already exist (idempotent)."

    def handle(self, *args, **options):
        created, updated = 0, 0
        for code, name, days, is_paid, half, carry, max_carry, doc, notice, color in DEFAULTS:
            _, was_created = LeaveType.objects.update_or_create(
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
            created += int(was_created)
            updated += int(not was_created)
        self.stdout.write(self.style.SUCCESS(
            f"Leave types seeded: {created} created, {updated} updated."
        ))
