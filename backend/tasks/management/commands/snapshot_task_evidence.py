"""
Write the daily task-evidence snapshot for every active employee (Phase T5.8).

Read-only with respect to tasks: nothing about anybody's work changes because
this ran. It records what the data said today, so that an appraisal months later
is not reading a figure recomputed against tasks that have since moved.

Safe to run twice: one snapshot per employee per day, enforced by a unique
constraint and a get_or_create that never updates.
"""
import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from monitoring import heartbeat

from tasks import evidence


class Command(BaseCommand):
    help = "Freeze today's task evidence for every employee with activity."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date", help="Snapshot as at this date (YYYY-MM-DD), for backfills.")
        parser.add_argument(
            "--period", choices=["daily", "monthly", "quarterly", "annual"],
            help="Which cadence to write. Omit to write whichever cadences are "
                 "due today — daily always, monthly on the 1st, quarterly on "
                 "the 1st of a quarter, annual on 1 January.")

    def handle(self, *args, **options):
        if options.get("date"):
            try:
                day = datetime.date.fromisoformat(options["date"])
            except ValueError:
                self.stderr.write(self.style.ERROR("--date must be YYYY-MM-DD."))
                return
        else:
            day = timezone.localdate()

        # TWO MEANINGS, DELIBERATELY DIFFERENT
        # ------------------------------------
        # Asked EXPLICITLY (`--period monthly`), the window is the period the
        # date sits in, up to that date — "give me the monthly figures" most
        # naturally means this month so far.
        #
        # Run AUTOMATICALLY (no `--period`), the window is the period that just
        # CLOSED: the scheduler fires on the 1st and the monthly row covers the
        # whole preceding month. Firing on the last day instead would produce a
        # row missing whatever happened after the job ran that evening, and
        # nobody would ever know which hours were absent.
        explicit = bool(options.get("period"))
        cadences = [options["period"]] if explicit else self._due_today(day)

        with heartbeat.heartbeat("TASK_EVIDENCE_SNAPSHOT"):
            written = {
                cadence: evidence.snapshot_everyone(
                    snapshot_date=day, period_type=cadence,
                    closing=(not explicit and cadence != "daily"))
                for cadence in cadences
            }

        for cadence, count in written.items():
            self.stdout.write(f"  {cadence}: {count}")
        self.stdout.write(self.style.SUCCESS(
            f"Evidence snapshots written for {day}."))

    @staticmethod
    def _due_today(day):
        """
        Which cadences a given day closes.

        Written on the FIRST of a period rather than the last, so the monthly row
        covers the month that has just ended in full. Writing it on the 31st
        would produce a row that omitted whatever happened after the job ran that
        evening — and nobody would ever know which hours were missing.

        The daily row is always written; the others accumulate on top of it.
        """
        cadences = ["daily"]
        if day.day == 1:
            cadences.append("monthly")
            if day.month in (1, 4, 7, 10):
                cadences.append("quarterly")
            if day.month == 1:
                cadences.append("annual")
        return cadences
