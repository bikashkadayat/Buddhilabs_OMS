from django.core.management.base import BaseCommand

from notifications.reconcile import reconcile_stale_approvals

from monitoring import heartbeat


class Command(BaseCommand):
    help = "Mark stale 'awaiting your review' notifications read (reconcile bell with the actionable queue)."

    def handle(self, *args, **options):
        # Phase 11 (audit finding M5): record a heartbeat so a job that stops
        # running becomes a red tile and an alert instead of silence. On an
        # exception the heartbeat is written FAILED and the error re-raised, so
        # cron still sees a non-zero exit.
        with heartbeat.heartbeat("RECONCILE_NOTIFICATIONS"):
            return self._handle(*args, **options)

    def _handle(self, *args, **options):
        n = reconcile_stale_approvals()
        self.stdout.write(self.style.SUCCESS(f"Reconciled {n} stale approval notification(s)."))
