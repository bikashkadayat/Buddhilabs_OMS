"""
Repair take-out requests left stuck at APPROVED after their item was already
returned (historical data damaged by the return_item bug, which freed the item but
never closed the request). A lingering APPROVED row makes assert_item_takeable
treat the item as permanently in-flight, so it can never be taken out again.

A request is considered stuck when it is APPROVED but its item is demonstrably NOT
out — i.e. the item's status is anything other than 'out' (available / assigned /
maintenance / retired), or the item no longer exists at all.

Dry-run by default; pass --fix to write. Exit code 0 = clean, 1 = stuck rows found
(so it can be used as a CI/cron integrity gate like audit_data_integrity).
"""
import sys

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from inventory.models import InventoryItem, TakeOutRequest


class Command(BaseCommand):
    help = "Close take-out requests stuck at 'approved' after the item was returned."

    def add_arguments(self, parser):
        parser.add_argument("--fix", action="store_true",
                            help="Apply the repair (default is a dry run).")

    def handle(self, *args, **options):
        fix = options["fix"]

        # APPROVED requests whose item is not actually out (or is gone).
        stuck = (TakeOutRequest.objects
                 .filter(status=TakeOutRequest.Status.APPROVED)
                 .filter(Q(item__isnull=True) | ~Q(item__status=InventoryItem.Status.OUT))
                 .select_related("item")
                 .order_by("created_at"))

        rows = list(stuck)
        if not rows:
            self.stdout.write(self.style.SUCCESS("No stuck take-out requests found."))
            return

        for r in rows:
            item_state = r.item.status if r.item_id else "<item deleted>"
            # action_date can be NULL on rows written before/around the bug.
            approved_on = f"{r.action_date:%Y-%m-%d}" if r.action_date else "unknown date"
            self.stdout.write(
                f"  {r.reference} · {r.item_code or '—'} · approved {approved_on} "
                f"· item status={item_state}")

        if not fix:
            self.stdout.write(self.style.WARNING(
                f"{len(rows)} stuck request(s) found. Re-run with --fix to close them."))
            sys.exit(1)

        today = timezone.localtime(timezone.now()).date()
        with transaction.atomic():
            # Re-select under a row lock so a concurrent approve/return cannot race us.
            locked = (TakeOutRequest.objects.select_for_update()
                      .filter(pk__in=[r.pk for r in rows],
                              status=TakeOutRequest.Status.APPROVED))
            fixed = 0
            for r in locked:
                r.status = TakeOutRequest.Status.RETURNED
                # Prefer a real return date if one was somehow recorded.
                r.actual_return_date = r.actual_return_date or today
                r.save(update_fields=["status", "actual_return_date", "updated_at"])
                fixed += 1

        self.stdout.write(self.style.SUCCESS(
            f"Closed {fixed} stuck take-out request(s) -> 'returned'. "
            f"Their items can be taken out again."))
