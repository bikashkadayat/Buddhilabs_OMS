"""
Emit the `task_overdue` event for tasks that have passed their due date
(Phase T3, Part 7).

WHY A COMMAND AND NOT A REQUEST
-------------------------------
"Became overdue" is the only one of the five events with no transition behind it:
nothing happened, a date passed. There is no request to hang it off, so it is
detected on a schedule.

WHY IT DELIVERS NOTHING
-----------------------
By instruction, T3 prepares hooks only. This command emits the signal and reports
what it found; whether anything listens is a later phase's decision. Run with
`--dry-run` to see the set without emitting at all.

IDEMPOTENCY IS THE CALLER'S JOB, DELIBERATELY
---------------------------------------------
A task is overdue every day until it is finished, so a daily run emits for the
same task repeatedly — by design: "three days overdue" is a different fact from
"one day overdue", and `overdue_days` carries it. What must NOT happen is the
same day being emitted twice because the command was run twice, so the receiver
(when one exists) is responsible for collapsing on (task, date). Recording state
here would mean this module owning a delivery concern it has no other part in.
"""
import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from tasks import events
from tasks.models import Task


class Command(BaseCommand):
    help = "Emit task_overdue events for open tasks past their due date."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be emitted without emitting anything.")
        parser.add_argument(
            "--min-days", type=int, default=1,
            help="Only tasks at least this many days overdue (default 1).")

    def handle(self, *args, **options):
        today = timezone.localdate()
        # `--min-days 1` (the default) means "due yesterday or earlier", which
        # is the first day a task can honestly be called late.
        min_days = max(options["min_days"], 1)
        cutoff = today - datetime.timedelta(days=min_days - 1)

        overdue = (Task.objects
                   .filter(due_date__lt=cutoff, status__in=Task.OPEN_STATUSES)
                   .select_related("department")
                   .prefetch_related("assignees")
                   .order_by("due_date"))

        emitted = 0
        for task in overdue:
            days = (today - task.due_date).days
            if options["dry_run"]:
                self.stdout.write(
                    f"  would emit: {task.task_number} — {days}d overdue "
                    f"({task.get_status_display()})")
                continue
            events.emit_overdue(task, days)
            emitted += 1

        verb = "would emit" if options["dry_run"] else "emitted"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} task_overdue for {overdue.count() if options['dry_run'] else emitted} "
            f"task(s) as at {today}."))
