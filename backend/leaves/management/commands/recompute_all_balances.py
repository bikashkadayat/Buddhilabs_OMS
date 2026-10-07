"""Recompute every user's enterprise leave balances for a year (idempotent)."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from leaves.models import LeaveType
from leaves import services

User = get_user_model()


class Command(BaseCommand):
    help = "Recompute EnterpriseLeaveBalance for all (or one) active users for a year."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, default=timezone.now().year,
                            help="Calendar year to recompute (default: current year).")
        parser.add_argument("--user-id", type=str, default=None,
                            help="Restrict to a single user UUID.")

    def handle(self, *args, **options):
        year = options["year"]
        users = User.objects.filter(is_active=True)
        if options["user_id"]:
            users = users.filter(pk=options["user_id"])

        leave_types = list(LeaveType.objects.filter(is_active=True))
        total = 0
        for user in users.iterator():
            for leave_type in leave_types:
                services.recompute_leave_balance(user, leave_type, year)
                total += 1
            self.stdout.write(f"  recomputed {len(leave_types)} balances for {user}")

        services.audit_log(
            None, "SYSTEM_RECOMPUTE_BALANCES",
            metadata={"year": year, "users": users.count(), "balances": total},
        )
        self.stdout.write(self.style.SUCCESS(
            f"Recomputed {total} balances for {users.count()} user(s), year {year}."
        ))
