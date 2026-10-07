"""Advance expired subscriptions. Intended for the existing `cron` container.

    python manage.py subscriptions_advance [--dry-run] [--date YYYY-MM-DD]

Two indexed sweeps, in order: ACTIVE/TRIAL past its period end becomes GRACE,
and GRACE past its grace date becomes SUSPENDED. Idempotent -- running it twice
in a day is a no-op the second time -- so a cron that fires on overlap or a
retry after a partial failure is safe.

Also reconciles the Organization subscription mirror, because denormalisation
without a reconciler is just a bug with a delay.

AND IT NOW WARNS THE CUSTOMER FIRST (Phase S8 Part 8). Everything needed to
suspend a tenant has been here since Phase S6 and worked correctly; nothing
told the customer it was coming. The first they knew was an office unable to
sign in on a Monday, over an invoice nobody had mentioned. The notices run
BEFORE the sweep, so a tenant that is about to be moved into grace today is
warned on the days leading up to it rather than in the same breath.
"""
import datetime

from django.core.management.base import BaseCommand, CommandError

from tenancy import services


class Command(BaseCommand):
    help = "Move expired subscriptions into grace, then into suspension."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date", dest="date",
            help="Run as if today were this date (YYYY-MM-DD). For testing.")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would change without changing it.")

    def handle(self, *args, **options):
        today = None
        if options.get("date"):
            try:
                today = datetime.date.fromisoformat(options["date"])
            except ValueError as exc:
                raise CommandError(f"--date must be YYYY-MM-DD: {exc}")

        if options["dry_run"]:
            from tenancy.models import Subscription

            reference = today or datetime.date.today()
            S = Subscription.Status
            to_grace = Subscription.objects.filter(
                status__in=[S.ACTIVE, S.TRIAL],
                current_period_end__lt=reference)
            to_suspend = Subscription.objects.filter(
                status=S.GRACE, grace_until__lt=reference)
            self.stdout.write(
                f"[dry-run] would move {to_grace.count()} to grace and "
                f"{to_suspend.count()} to suspended")
            for sub in list(to_grace) + list(to_suspend):
                self.stdout.write(f"  - {sub.organization.slug}: {sub.status}")

            from tenancy import expiry_notices

            notices = expiry_notices.run(today=today, dry_run=True)
            self.stdout.write(
                f"[dry-run] would send {len(notices['notices'])} notice(s)")
            for key in notices["notices"]:
                self.stdout.write(f"  - {key}")
            return

        # NOTICES FIRST, then the sweep. Both are idempotent, so the order
        # is about meaning rather than safety: a customer should hear "three
        # days left" on the day there are three days left, not alongside the
        # transition that happens when there are none.
        from tenancy import expiry_notices

        notices = expiry_notices.run(today=today)
        self.stdout.write(
            f"notices -- renewal due: {notices['renewal_due']}, "
            f"in grace: {notices['in_grace']}, "
            f"expired: {notices['expired']}, "
            f"suspended: {notices['suspended']}")

        counts = services.advance_expired(today=today)
        self.stdout.write(
            f"entered grace: {counts['entered_grace']}, "
            f"suspended: {counts['suspended']}")

        drift = services.reconcile_mirrors()
        if drift:
            # Not a crash: the mirror is a cache of the subscription, so drift
            # is a correctness alarm rather than an outage. It is logged at
            # ERROR by reconcile_mirrors() and surfaced here for the cron log.
            self.stderr.write(
                self.style.ERROR(f"subscription mirror drift: {drift}"))
        else:
            self.stdout.write("subscription mirrors consistent")
