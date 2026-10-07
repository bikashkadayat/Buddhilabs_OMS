"""
Year-end carry-forward for all users. Runs once per year: it refuses to run
again for a year whose balances are already locked (unless --force).
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from leaves.models import EnterpriseLeaveBalance
from leaves import services

from monitoring import heartbeat

User = get_user_model()


class Command(BaseCommand):
    help = "Run leave carry-forward for all users and lock the closing year."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, required=True, help="Closing year to process.")
        parser.add_argument("--force", action="store_true",
                            help="Re-run even if the year is already locked.")

    def handle(self, *args, **options):
        # Phase 11 (audit finding M5): record a heartbeat so a job that stops
        # running becomes a red tile and an alert instead of silence. On an
        # exception the heartbeat is written FAILED and the error re-raised, so
        # cron still sees a non-zero exit.
        with heartbeat.heartbeat("YEAR_END"):
            return self._handle(*args, **options)

    def _handle(self, *args, **options):
        year = options["year"]

        already_locked = EnterpriseLeaveBalance.objects.filter(
            year=year, locked_at__isnull=False
        ).exists()
        if already_locked and not options["force"]:
            raise CommandError(
                f"Year {year} is already locked. Re-run with --force to override."
            )

        users = list(User.objects.filter(is_active=True))
        with transaction.atomic():
            for user in users:
                services.process_year_end_carry_forward(user, year, actor=None)

            locked = EnterpriseLeaveBalance.objects.filter(year=year).update(
                locked_at=timezone.now()
            )

        # Year-end report
        totals = EnterpriseLeaveBalance.objects.filter(year=year)
        forfeited = sum((b.forfeited_days for b in totals), Decimal("0"))
        carried = sum(
            (b.carried_forward_days for b in EnterpriseLeaveBalance.objects.filter(year=year + 1)),
            Decimal("0"),
        )

        services.audit_log(
            None, "SYSTEM_YEAR_END",
            metadata={"year": year, "users": len(users),
                      "carried": str(carried), "forfeited": str(forfeited)},
        )

        self.stdout.write(self.style.SUCCESS(
            f"Year {year} closed: {len(users)} users, {locked} balances locked, "
            f"{carried} days carried to {year + 1}, {forfeited} days forfeited."
        ))
