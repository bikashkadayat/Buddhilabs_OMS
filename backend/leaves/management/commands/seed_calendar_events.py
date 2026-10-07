"""
Load Nepali calendar events (festivals, observances, jayantis) from a fixture.

    python manage.py seed_calendar_events --year 2026
    python manage.py seed_calendar_events --file /path/to/events.json --dry-run

Fixture shape — a list of objects, one per event. A day may appear more than
once, which is the point:

    [
      {"date": "2026-10-20", "name": "Vijaya Dashami", "name_np": "विजया दशमी",
       "event_type": "festival", "tithi": "दशमी", "is_public_holiday": true},
      {"date": "2026-10-20", "name": "Ghatasthapana", "name_np": "घटस्थापना",
       "event_type": "religious"}
    ]

WHY THE DATES COME FROM A FILE AND ARE NEVER COMPUTED
-----------------------------------------------------
Most Nepali festivals are lunar: their Gregorian date moves each year and is
fixed by panchanga, not by arithmetic anyone should reimplement casually. A
festival placed on the wrong day in an HR system means somebody works a public
holiday, so the dates are supplied by whoever owns the organisation's calendar
and this command only loads them.
"""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from leaves.models import CalendarEvent, Holiday

VALID_TYPES = {c[0] for c in CalendarEvent.EventType.choices}


class Command(BaseCommand):
    help = "Load Nepali calendar events from a JSON fixture (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int,
                            help="Loads leaves/fixtures/calendar_events_np_<year>.json")
        parser.add_argument("--file", type=str, help="Explicit path to a fixture.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Validate and report; write nothing.")

    def handle(self, *args, **opts):
        if opts["file"]:
            path = Path(opts["file"])
        elif opts["year"]:
            path = (Path(settings.BASE_DIR) / "leaves" / "fixtures"
                    / f"calendar_events_np_{opts['year']}.json")
        else:
            raise CommandError("Give --year or --file.")
        if not path.exists():
            raise CommandError(f"Fixture not found: {path}")

        try:
            entries = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise CommandError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(entries, list):
            raise CommandError("The fixture must be a LIST of event objects.")

        # Validate everything BEFORE writing anything: a half-loaded calendar
        # is worse than an unloaded one, because it looks complete.
        problems = []
        for i, e in enumerate(entries):
            if not isinstance(e, dict):
                problems.append(f"[{i}] not an object"); continue
            if not e.get("date"):
                problems.append(f"[{i}] missing 'date'")
            if not e.get("name"):
                problems.append(f"[{i}] missing 'name'")
            kind = e.get("event_type", CalendarEvent.EventType.FESTIVAL)
            if kind not in VALID_TYPES:
                problems.append(f"[{i}] event_type {kind!r} is not one of {sorted(VALID_TYPES)}")
        if problems:
            for p in problems[:20]:
                self.stderr.write(self.style.ERROR(f"  {p}"))
            raise CommandError(f"{len(problems)} invalid entr{'y' if len(problems)==1 else 'ies'}; nothing written.")

        created = updated = 0
        hol_created = hol_existing = 0
        with transaction.atomic():
            for e in entries:
                is_holiday = bool(e.get("is_public_holiday", False))
                _, was_created = CalendarEvent.objects.update_or_create(
                    date=e["date"], name=e["name"],
                    defaults={
                        "name_np": e.get("name_np", ""),
                        "event_type": e.get("event_type", CalendarEvent.EventType.FESTIVAL),
                        "tithi": e.get("tithi", ""),
                        "detail": e.get("detail", ""),
                        "is_public_holiday": is_holiday,
                        "is_active": bool(e.get("is_active", True)),
                    },
                )
                created += int(was_created)
                updated += int(not was_created)

                # A DAY OFF HAS TO EXIST IN BOTH TABLES.
                #
                # CalendarEvent is display-only: the leave engine reads Holiday
                # and nothing else. Marking a festival `is_public_holiday` in
                # the fixture alone therefore painted the calendar red while
                # leave carried on deducting the day — measured, not assumed:
                # working days for such a date stayed 1.00 until a Holiday row
                # existed, then dropped to 0.00.
                #
                # So the flag creates the Holiday row too, and one fixture stays
                # the single source for "the office is closed".
                if is_holiday:
                    _, made = Holiday.objects.get_or_create(
                        date=e["date"],
                        defaults={"name": e["name"],
                                  "holiday_type": Holiday.HolidayType.PUBLIC},
                    )
                    hol_created += int(made)
                    hol_existing += int(not made)
            if opts["dry_run"]:
                transaction.set_rollback(True)

        self.stdout.write(
            f"{len(entries)} entries: {created} created, {updated} updated")
        self.stdout.write(
            f"public holidays: {hol_created} Holiday row(s) created, "
            f"{hol_existing} already present")
        # get_or_create, not update_or_create: an existing Holiday may have been
        # entered by HR with their own wording, and a fixture reload should not
        # quietly rename it. Removing one is never automatic either — leave
        # already taken against that date was calculated with it.
        if hol_existing:
            self.stdout.write(
                "  (existing Holiday rows were left as they are — rename or "
                "remove them in the admin if they are wrong)")
        self.stdout.write(
            self.style.WARNING("dry run — nothing written") if opts["dry_run"]
            else self.style.SUCCESS(f"loaded from {path.name}"))
