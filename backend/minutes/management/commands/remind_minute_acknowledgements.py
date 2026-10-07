"""
Remind members whose acknowledgement is outstanding.

Run daily from cron:

    30 7 * * *  cd /srv/nifn && .venv/bin/python manage.py remind_minute_acknowledgements

Only touches minutes whose acknowledgement round is OPEN, and only participants still
pending. Rate-limited by `reminded_at`: somebody is not reminded twice within
--min-interval days, so a daily cron does not become daily nagging. That field already
existed for the manual reminder endpoint; this reuses it rather than adding a second
notion of "last reminded".
"""
import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from minutes.models import Minute, MinuteParticipant
from minutes.services import notify_user, record_audit
from notifications.models import Category


class Command(BaseCommand):
    help = "Remind participants with outstanding minute acknowledgements."

    def add_arguments(self, parser):
        parser.add_argument(
            "--min-interval", type=int, default=3,
            help="Days to wait before reminding the same person again (default 3).")
        parser.add_argument(
            "--only-overdue", action="store_true",
            help="Remind only where the acknowledgement deadline has already passed.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        interval = options["min_interval"]
        cutoff = timezone.now() - datetime.timedelta(days=interval)
        today = timezone.localdate()

        minutes = (Minute.objects
                   .filter(acknowledgement_opened_at__isnull=False,
                           status=Minute.Status.PENDING_ACKNOWLEDGEMENT)
                   .prefetch_related("participants__user"))

        reminded = overdue_only = 0
        for minute in minutes:
            deadline = minute.acknowledgement_deadline
            if options["only_overdue"] and not (deadline and today > deadline):
                continue

            due = []
            for participant in minute.participants.all():
                if participant.ack_status != MinuteParticipant.AckStatus.PENDING:
                    continue
                if not participant.must_acknowledge:
                    continue
                # Not reminded at all, or not recently enough.
                if participant.reminded_at and participant.reminded_at > cutoff:
                    continue
                due.append(participant)

            if not due:
                continue
            late = bool(deadline and today > deadline)
            if late:
                overdue_only += 1

            if options["dry_run"]:
                self.stdout.write(
                    f"  {minute.minute_number}: {len(due)} pending"
                    f"{' (deadline passed)' if late else ''}")
                reminded += len(due)
                continue

            now = timezone.now()
            for participant in due:
                notify_user(
                    participant.user,
                    Category.MINUTE_ACK_REMINDER,
                    (f"{'Overdue' if late else 'Reminder'} — acknowledgement pending: "
                     f"{minute.minute_number}"),
                    (f"{minute.display_title} · due "
                     f"{deadline.isoformat() if deadline else 'on receipt'}"),
                    minute,
                )
                participant.reminded_at = now
                participant.save(update_fields=["reminded_at"])
            record_audit(
                minute, None, "ack_reminded",
                remarks=(f"Scheduled reminder sent to {len(due)} pending "
                         f"participant(s){' after the deadline' if late else ''}."),
                metadata={"reminded": len(due), "overdue": late},
            )
            reminded += len(due)

        verb = "would remind" if options["dry_run"] else "reminded"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {reminded} participant(s) across {minutes.count()} open round(s); "
            f"{overdue_only} minute(s) past deadline"))
