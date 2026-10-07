"""Fail reports whose generating thread no longer exists.

Phase 11 audit finding M4. ``reports.views._spawn_generation`` runs report
generation in a daemon thread. Daemon threads die with the process, so a
container restart, a redeploy or an OOM kill mid-generation left the ``ReportRun``
row in ``generating`` forever: no file, no error, no retry, and a user watching
a spinner that would never resolve.

This is the reaper. It does not retry — a report is cheap to request again, and
an automatic retry of something that may have died from a resource problem is
how a restart loop starts. It marks the run failed with a truthful reason so the
UI can say so and the user can press the button again.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from monitoring import heartbeat
from reports.models import ReportRun

# Longer than any observed generation. The slowest real report is a multi-sheet
# workbook over a full year, which is seconds; 30 minutes means "the process
# that owned this is definitely gone", not "this is taking a while".
DEFAULT_STUCK_MINUTES = 30


class Command(BaseCommand):
    help = "Mark reports stuck in 'generating' as failed."

    def add_arguments(self, parser):
        parser.add_argument("--minutes", type=int, default=DEFAULT_STUCK_MINUTES)
        parser.add_argument("--quiet", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        with heartbeat.heartbeat("REAP_STUCK_REPORTS"):
            self._reap(options)

    def _reap(self, options):
        cutoff = timezone.now() - timedelta(minutes=options["minutes"])
        stuck = ReportRun.objects.filter(
            status=ReportRun.Status.GENERATING, created_at__lt=cutoff)

        count = stuck.count()
        if not count:
            if not options["quiet"]:
                self.stdout.write("No stuck reports.")
            return

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(
                f"[dry-run] would fail {count} stuck report(s)."))
            for run in stuck[:20]:
                self.stdout.write(f"  {run.id} {run.report_type} {run.created_at}")
            return

        # update() rather than a loop: this runs every 15 minutes and there is no
        # per-row work to do beyond the status change.
        stuck.update(
            status=ReportRun.Status.FAILED,
            error=(f"Generation did not finish within {options['minutes']} minutes. "
                   f"The worker was most likely restarted mid-run. "
                   f"Please request the report again."),
            completed_at=timezone.now())

        self.stdout.write(self.style.WARNING(
            f"Failed {count} report(s) stuck in generating."))
