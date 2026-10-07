"""Delete autosave snapshots untouched for the retention window. Run daily."""
from django.core.management.base import BaseCommand

from monitoring import heartbeat

from drafts import services


class Command(BaseCommand):
    help = "Purge DocumentDraft rows older than DRAFT_RETENTION_DAYS."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=services.RETENTION_DAYS)
        parser.add_argument("--quiet", action="store_true")

    def handle(self, *args, **options):
        # Same contract as every other scheduled job: the heartbeat turns a job
        # that silently stops running into a red tile and an alert.
        with heartbeat.heartbeat("PURGE_DRAFTS"):
            removed = services.purge_expired(days=options["days"])
            # Phase TASK-AUTOSAVE-AND-SUBTASKS: the same daily slot tells the
            # owners of idle task drafts that there is something to recover.
            reminded = services.remind_unfinished_task_drafts()
            if not options["quiet"]:
                self.stdout.write(
                    f"Purged {removed} draft(s) older than {options['days']} days; "
                    f"reminded {reminded} task draft owner(s).")
            return None
