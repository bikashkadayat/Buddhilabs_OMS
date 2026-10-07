"""Derive attendance from any punches that have not been processed yet.

Runs from cron as a safety net behind the inline derivation that happens at
ingest, so a punch that arrived during a blip is still picked up.
"""
from django.core.management.base import BaseCommand

from biometric import derivation

from monitoring import heartbeat


class Command(BaseCommand):
    help = "Build attendance.Attendance rows from unprocessed biometric punches."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=None,
            help="Cap the number of employee-days processed in one run.")
        parser.add_argument(
            "--quiet", action="store_true",
            help="Only emit output when something was processed or failed.")

    def handle(self, *args, **options):
        # Phase 11 (audit finding M5): record a heartbeat so a job that stops
        # running becomes a red tile and an alert instead of silence. On an
        # exception the heartbeat is written FAILED and the error re-raised, so
        # cron still sees a non-zero exit.
        with heartbeat.heartbeat("PROCESS_PUNCHES"):
            return self._handle(*args, **options)

    def _handle(self, *args, **options):
        summary = derivation.process_unprocessed_punches(limit_days=options["limit"])

        if options["quiet"] and not summary["days_processed"] and not summary["failed"]:
            return

        self.stdout.write(
            f"Processed {summary['days_processed']} employee-day(s): "
            f"{summary['derived']} derived, {summary['reverted']} reverted, "
            f"{summary['failed']} failed."
        )
        if summary["unmapped_backlog"]:
            # Not an error: these are blocked on an HR mapping decision, not on
            # this command. Surfaced so the queue never rots unnoticed.
            self.stdout.write(self.style.WARNING(
                f"{summary['unmapped_backlog']} punch(es) are waiting on an employee "
                f"mapping — see /api/v1/biometric/unmapped/."
            ))
        if summary["failed"]:
            self.stderr.write(self.style.ERROR(
                f"{summary['failed']} employee-day(s) failed; see logs for tracebacks."))
