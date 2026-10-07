"""
Run the reminder and escalation engines (Phase T4.4, T4.5).

Scheduled daily. Safe to run more than once: every send is guarded by a
(task, kind, day) unique row, so a second run the same afternoon sends nothing.
See tasks/reminders.py.
"""
import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from monitoring import heartbeat

from tasks import reminders


class Command(BaseCommand):
    help = "Send task due reminders, review nudges and overdue escalations."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date", help="Run as at this date (YYYY-MM-DD), for backfills "
                           "and for reproducing what a given day would send.")
        parser.add_argument(
            "--only", choices=["due", "review", "escalation"],
            help="Run one engine rather than all three.")

    def handle(self, *args, **options):
        if options.get("date"):
            try:
                today = datetime.date.fromisoformat(options["date"])
            except ValueError:
                self.stderr.write(self.style.ERROR(
                    "--date must be YYYY-MM-DD."))
                return
        else:
            today = timezone.localdate()

        # Same contract as every other scheduled job: the heartbeat turns a job
        # that silently stops running into a red tile and an alert. It matters
        # more here than most — this job's failure mode IS silence.
        with heartbeat.heartbeat("TASK_REMINDERS"):
            results = self._run(options, today)

        for name, count in results.items():
            self.stdout.write(f"  {name}: {count}")
        self.stdout.write(self.style.SUCCESS(
            f"Task reminders complete for {today}."))

    def _run(self, options, today):
        only = options.get("only")
        if only == "due":
            results = {"due_reminders": reminders.run_due_reminders(today=today)}
        elif only == "review":
            results = {"review_reminders": reminders.run_review_reminders(today=today)}
        elif only == "escalation":
            results = {"escalations": reminders.run_escalations(today=today)}
        else:
            results = reminders.run_all(today=today)
        return results
