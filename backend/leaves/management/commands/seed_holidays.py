"""Idempotently load holidays for a year/country from a JSON fixture."""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from leaves.models import Holiday


class Command(BaseCommand):
    help = "Load holidays from backend/leaves/fixtures/holidays_<country>_<year>.json (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, required=True)
        parser.add_argument("--country", type=str, default="NP")

    def handle(self, *args, **options):
        year = options["year"]
        country = options["country"].lower()
        fixture = (
            Path(settings.BASE_DIR) / "leaves" / "fixtures"
            / f"holidays_{country}_{year}.json"
        )
        if not fixture.exists():
            raise CommandError(f"Fixture not found: {fixture}")

        entries = json.loads(fixture.read_text())
        created, updated = 0, 0
        for entry in entries:
            _, was_created = Holiday.objects.update_or_create(
                date=entry["date"],
                defaults={
                    "name": entry["name"],
                    "holiday_type": entry.get("holiday_type", "public"),
                    "description": entry.get("description", ""),
                    "is_active": True,
                },
            )
            created += int(was_created)
            updated += int(not was_created)
        self.stdout.write(self.style.SUCCESS(
            f"Holidays seeded from {fixture.name}: {created} created, {updated} updated."
        ))
