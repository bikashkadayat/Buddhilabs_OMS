"""Generate and email any scheduled reports that are due. Run hourly via cron."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from reports import report_service
from reports.emails import send_report_email
from reports.models import ScheduledReport

from monitoring import heartbeat

INTERVALS = {
    "@hourly": timedelta(hours=1),
    "@daily": timedelta(days=1),
    "@weekly": timedelta(weeks=1),
    "@monthly": timedelta(days=30),
}


def is_due(scheduled, now):
    if not scheduled.is_active:
        return False
    if scheduled.last_run_at is None:
        return True
    interval = INTERVALS.get(scheduled.cron_expression, INTERVALS["@daily"])
    return (now - scheduled.last_run_at) >= interval


class Command(BaseCommand):
    help = "Generate and email scheduled reports that are due."

    def handle(self, *args, **options):
        # Phase 11 (audit finding M5): record a heartbeat so a job that stops
        # running becomes a red tile and an alert instead of silence. On an
        # exception the heartbeat is written FAILED and the error re-raised, so
        # cron still sees a non-zero exit.
        with heartbeat.heartbeat("SCHEDULED_REPORTS"):
            return self._handle(*args, **options)

    def _handle(self, *args, **options):
        now = timezone.now()
        sent, skipped, failed = 0, 0, 0
        for scheduled in ScheduledReport.objects.filter(is_active=True):
            if not is_due(scheduled, now):
                skipped += 1
                continue
            try:
                content, filename, content_type = report_service.build_report_file(
                    scheduled.report_type, scheduled.params or {}
                )
                send_report_email(scheduled, content, filename, content_type)
                scheduled.last_run_at = now
                scheduled.save(update_fields=["last_run_at"])
                sent += 1
                self.stdout.write(f"  sent {scheduled.report_type} to {len(scheduled.recipients)} recipient(s)")
            except Exception as exc:  # noqa: BLE001 - keep going on individual failures
                failed += 1
                self.stderr.write(f"  FAILED {scheduled.report_type}: {exc}")

        self.stdout.write(self.style.SUCCESS(f"Scheduled reports: {sent} sent, {skipped} skipped, {failed} failed."))
