"""Evaluate the alert rules and notify on state changes.

Runs every five minutes from cron. Notifies on transitions only — see
``monitoring.alerts`` for why that matters more than the detection itself.
"""
from django.core.management.base import BaseCommand

from monitoring import alerts, heartbeat


class Command(BaseCommand):
    help = "Evaluate alert rules; notify on new, escalated and resolved alerts."

    def add_arguments(self, parser):
        parser.add_argument("--quiet", action="store_true",
                            help="Only print when something changed.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Show what would fire without notifying anyone.")

    def handle(self, *args, **options):
        with heartbeat.heartbeat("CHECK_ALERTS"):
            self._check(options)

    def _check(self, options):
        if options["dry_run"]:
            firing = alerts.evaluate()
            if not firing:
                self.stdout.write(self.style.SUCCESS("Nothing would fire."))
                return
            for alert in firing:
                self.stdout.write(
                    f"[{alert['severity'].upper():8s}] {alert['title']}: "
                    f"{alert['value']} ({alert['state']})")
            return

        result = alerts.process()

        if result["notified"] or result["resolved"]:
            for key in result["notified"]:
                self.stdout.write(self.style.WARNING(f"ALERT sent: {key}"))
            for key in result["resolved"]:
                self.stdout.write(self.style.SUCCESS(f"RESOLVED: {key}"))
        elif not options["quiet"]:
            if result["firing"]:
                self.stdout.write(
                    f"{len(result['firing'])} alert(s) still firing, "
                    f"already notified: {', '.join(result['firing'])}")
            else:
                self.stdout.write(self.style.SUCCESS("All clear."))
