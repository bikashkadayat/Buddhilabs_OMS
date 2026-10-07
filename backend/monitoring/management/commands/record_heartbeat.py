"""Record a heartbeat for a job that runs outside Django.

The backup scripts are shell, not management commands, so they cannot use the
``heartbeat()`` context manager. They call this instead:

    python manage.py record_heartbeat BACKUP --ok --detail "1.2GB, off-host"
    python manage.py record_heartbeat BACKUP --failed --detail "pg_dump exit 1"

Without this, the nightly backup was the single most important job in the system
and the only one that reported nothing at all (audit finding H4).
"""
from django.core.management.base import BaseCommand, CommandError

from monitoring import heartbeat


class Command(BaseCommand):
    help = "Record a success/failure heartbeat for a scheduled job."

    def add_arguments(self, parser):
        parser.add_argument("job", help=f"One of: {', '.join(heartbeat.CRON_JOBS)}")
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--ok", action="store_true", default=True)
        group.add_argument("--failed", action="store_true")
        parser.add_argument("--detail", default="")

    def handle(self, *args, **options):
        job = options["job"].upper()
        if job not in heartbeat.CRON_JOBS:
            # Refused rather than accepted silently: an unregistered job would
            # write heartbeats nothing ever reads, which is worse than none.
            raise CommandError(
                f"Unknown job {job!r}. Register it in monitoring.heartbeat."
                f"CRON_JOBS first, or the dashboard will never show it.")

        ok = not options["failed"]
        heartbeat.record(job, ok=ok, detail=options["detail"] or None)
        self.stdout.write(
            self.style.SUCCESS(f"{job}: recorded {'OK' if ok else 'FAILED'}"))
