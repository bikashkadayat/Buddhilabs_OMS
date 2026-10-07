"""
Nudge whoever owns an appraisal stage whose deadline is approaching.

Scheduled daily. Safe to run more than once: every send carries an idempotency
key of (appraisal, deadline, days-out), so a second run the same afternoon sends
nothing and a retry after a failed night sends each thing once.

See appraisal/reminders.py for what it deliberately does NOT do — no daily
chasing, no escalation chain.
"""
import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from monitoring import heartbeat

from appraisal import reminders


class Command(BaseCommand):
    help = "Send appraisal deadline reminders."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date", help="Run as at this date (YYYY-MM-DD), for backfills "
                           "and for reproducing what a given day would send.")

    def handle(self, *args, **options):
        if options.get("date"):
            try:
                today = datetime.date.fromisoformat(options["date"])
            except ValueError:
                self.stderr.write(self.style.ERROR("--date must be YYYY-MM-DD."))
                return
        else:
            today = timezone.localdate()

        # The heartbeat turns a job that silently stops running into a red tile
        # and an alert. It matters here for the same reason it matters for task
        # reminders: this job's failure mode IS silence.
        with heartbeat.heartbeat("APPRAISAL_REMINDERS"):
            results = reminders.run_all(today=today)

        for name, count in results.items():
            self.stdout.write(f"  {name}: {count}")
        self.stdout.write(self.style.SUCCESS(
            f"Appraisal reminders complete for {today}."))
