"""
Daily warranty, maintenance-contract and end-of-life alerts
(Phase ASSET-LIFECYCLE-DISPOSAL).

Tells the store about every asset still on the books whose warranty or AMC is
about to end or has just ended, or which is approaching or past the end of its
useful life. Disposed and archived assets are skipped: nobody renews the warranty
on something that has been scrapped.

Safe to re-run: each send is keyed on the asset, the due date and whether the date
has passed (see notifications.lifecycle_alert), so a retry after a failed night -
or running it twice by hand - sends nothing twice. An asset is announced once on
entering the warning window and once more when the date passes.

    python manage.py send_asset_lifecycle_alerts
    python manage.py send_asset_lifecycle_alerts --dry-run
"""
from django.core.management.base import BaseCommand

from monitoring import heartbeat

from inventory.models import InventoryItem
from inventory import notifications


def collect():
    """Every (kind, item, due date, state) that should be alerted today."""
    alerts = []
    items = (InventoryItem.objects.exclude(status__in=InventoryItem.TERMINAL_STATUSES)
             .select_related("category"))
    for item in items:
        if item.warranty_state in ("expiring", "expired"):
            alerts.append(("warranty", item, item.warranty_expiry, item.warranty_state))
        if item.amc_state in ("expiring", "expired"):
            alerts.append(("amc", item, item.amc_end, item.amc_state))
        if item.end_of_life_state in ("approaching", "past"):
            alerts.append(("end_of_life", item, item.end_of_life_date,
                           item.end_of_life_state))
    return alerts


class Command(BaseCommand):
    help = "Alert the store about expiring warranties, AMCs and assets reaching end of life."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="List what would be alerted without sending anything.")

    def handle(self, *args, **options):
        with heartbeat.heartbeat("ASSET_LIFECYCLE_ALERTS"):
            alerts = collect()
            for kind, item, due, state in alerts:
                self.stdout.write(f"{kind:12} {item.asset_code:14} {state:11} due {due}")
                if not options["dry_run"]:
                    notifications.lifecycle_alert(kind, item, due, state)
            verb = "would alert" if options["dry_run"] else "alerted"
            self.stdout.write(self.style.SUCCESS(f"{verb} on {len(alerts)} item(s)."))
