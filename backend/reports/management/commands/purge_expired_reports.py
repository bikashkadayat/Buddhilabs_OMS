"""Delete generated report files older than the retention window. Run daily."""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from reports.models import ReportRun

from monitoring import heartbeat


class Command(BaseCommand):
    help = "Purge ReportRun rows and files older than REPORTS_RETENTION_DAYS."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=getattr(settings, "REPORTS_RETENTION_DAYS", 30))

    def handle(self, *args, **options):
        # Phase 11 (audit finding M5): record a heartbeat so a job that stops
        # running becomes a red tile and an alert instead of silence. On an
        # exception the heartbeat is written FAILED and the error re-raised, so
        # cron still sees a non-zero exit.
        with heartbeat.heartbeat("PURGE_REPORTS"):
            return self._handle(*args, **options)

    def _handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options["days"])
        expired = ReportRun.objects.filter(created_at__lt=cutoff)
        count = 0
        for run in expired:
            if run.file:
                run.file.delete(save=False)
            run.delete()
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Purged {count} expired report(s) older than {options['days']} days."))
