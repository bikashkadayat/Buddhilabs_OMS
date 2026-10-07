"""Nightly: discard export bundles past their retention date.

Run alongside ``subscriptions_advance``. Safe to run repeatedly: a bundle
already expired has no file to discard and is not selected again.
"""
from django.core.management.base import BaseCommand

from tenancy import retention


class Command(BaseCommand):
    help = ("Discard tenant export bundles past their retention date. Keeps "
            "every receipt; never touches tenant data.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be discarded and change nothing.")

    def handle(self, *args, **options):
        result = retention.expire_exports(dry_run=options["dry_run"])
        for record in result["expired"]:
            self.stdout.write(
                f"  {record['organization']}  {record['export_id']}  "
                f"{record['bytes']} bytes  "
                f"{record['downloads']} download(s)")
        verb = "would discard" if result["dry_run"] else "discarded"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {len(result['expired'])} bundle(s), "
            f"{result['freed_bytes']} bytes "
            f"(retention: {result['retention_days']} days)"))
