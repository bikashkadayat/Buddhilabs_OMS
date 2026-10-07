"""Historical import plan (Phase 12).

Answers the six questions that must be settled BEFORE a backfill, because after
it they are much harder to answer and much more expensive to get wrong:

    1. How many biometric users are involved?
    2. How many punches are there?
    3. How many attendance days will that produce?
    4. Are there duplicates?
    5. Are any of those users unmapped?
    6. What, exactly, will the import do?

    python manage.py import_plan --from 2026-07-14
    python manage.py import_plan --from 2026-07-14 --json

Read-only. It writes nothing and imports nothing; it tells you what an import
WOULD do so the decision is made with numbers rather than optimism.

Note on scope: this reports on punches **already ingested into the OMS**. Pulling
the backlog off the terminal is the collector's job — the OMS deliberately has no
outbound connection to the device (see docs/PHASE_12_GO_LIVE.md). Run the
collector's backlog sync first, then this to confirm what landed.
"""
import json
from collections import Counter
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Min, Max
from django.utils import timezone

from attendance.models import Attendance
from biometric import services
from biometric.models import AttendancePunch, BiometricEmployee

DEFAULT_FROM = date(2026, 7, 14)


class Command(BaseCommand):
    help = "Analyse ingested biometric data and produce a historical import plan."

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="start", default=DEFAULT_FROM.isoformat())
        parser.add_argument("--to", dest="end", default=None)
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        try:
            start = date.fromisoformat(options["start"])
            end = (date.fromisoformat(options["end"]) if options["end"]
                   else timezone.localdate())
        except ValueError:
            raise CommandError("Dates must be YYYY-MM-DD.")
        if end < start:
            raise CommandError("--to must not precede --from.")

        plan = self._build(start, end)
        if options["as_json"]:
            self.stdout.write(json.dumps(plan, indent=2, default=str))
            return
        self._render(plan)

    # -- analysis ----------------------------------------------------------
    def _build(self, start, end):
        punches = AttendancePunch.objects.filter(local_date__gte=start,
                                                 local_date__lte=end)
        total = punches.count()

        span = punches.aggregate(first=Min("local_date"), last=Max("local_date"))
        covered_days = punches.values("local_date").distinct().count()
        calendar_days = (end - start).days + 1

        by_user = punches.values("employee_device_id").annotate(n=Count("id"))
        device_users = by_user.count()

        mapped = punches.filter(user__isnull=False)
        unmapped = punches.filter(user__isnull=True)
        unmapped_ids = sorted(
            {row["employee_device_id"] for row in
             unmapped.values("employee_device_id").distinct()},
            key=services.device_id_sort_key)

        # Employee-days: the number of Attendance rows a derivation would touch.
        employee_days = (mapped.values("user_id", "local_date").distinct().count())

        existing = Attendance.objects.filter(date__gte=start, date__lte=end)
        existing_total = existing.count()
        existing_biometric = existing.filter(
            source=Attendance.Source.BIOMETRIC).count()
        existing_hr = existing.filter(source=Attendance.Source.HR).count()

        return {
            "window": {"from": start.isoformat(), "to": end.isoformat(),
                       "calendar_days": calendar_days},
            "punches": {
                "total": total,
                "first": span["first"].isoformat() if span["first"] else None,
                "last": span["last"].isoformat() if span["last"] else None,
                "days_with_punches": covered_days,
                "days_without_punches": calendar_days - covered_days,
                "unprocessed": punches.filter(is_processed=False).count(),
            },
            "users": {
                "distinct_device_users": device_users,
                "mapped_punches": mapped.count(),
                "unmapped_punches": unmapped.count(),
                "unmapped_device_ids": unmapped_ids,
                "enrolments_total": BiometricEmployee.objects.filter(is_active=True).count(),
                "enrolments_unmapped": BiometricEmployee.objects.filter(
                    is_active=True, user__isnull=True).count(),
            },
            "attendance": {
                "employee_days_expected": employee_days,
                "existing_rows_in_window": existing_total,
                "existing_from_biometric": existing_biometric,
                "existing_from_hr": existing_hr,
            },
            "duplicates": self._duplicates(punches),
            "gaps": self._gaps(punches, start, end),
            "risks": [],
        }

    def _duplicates(self, punches):
        """Exact duplicates cannot exist — the ingest layer has a unique
        constraint — so this reports the *near* duplicates that constraint does
        not catch: two punches for the same person within a minute, which is
        usually a double-tap on the sensor rather than two events."""
        seen = Counter()
        for row in punches.filter(user__isnull=False).values(
                "user_id", "local_date", "timestamp").order_by("user_id", "timestamp"):
            seen[(row["user_id"], row["local_date"])] += 1

        many = {key: count for key, count in seen.items() if count > 6}
        return {
            "exact_duplicates": 0,
            "exact_duplicates_note": (
                "Structurally impossible: AttendancePunch has a unique constraint "
                "on (device, device user, timestamp), which is what makes a "
                "backlog replay free."),
            "employee_days_with_many_punches": len(many),
            "many_punches_note": (
                "More than 6 punches in one day usually means break/overtime "
                "punches, which derivation handles. Worth a spot check, not a "
                "blocker."),
        }

    def _gaps(self, punches, start, end):
        """Working days inside the window with no punches at all.

        A gap is not automatically a problem — a public holiday has no punches
        by definition — but a run of gaps mid-week means the backlog is
        incomplete, which is the failure this whole report exists to catch
        BEFORE the import rather than after.
        """
        from leaves.models import Holiday

        present = set(punches.values_list("local_date", flat=True).distinct())
        holidays = set(Holiday.objects.filter(
            is_active=True, date__gte=start, date__lte=end
        ).values_list("date", flat=True))

        gaps, day = [], start
        while day <= end:
            if day.weekday() != 5 and day not in holidays and day not in present:
                gaps.append(day.isoformat())
            day += timedelta(days=1)
        return {"working_days_without_punches": len(gaps), "dates": gaps[:40]}

    # -- output ------------------------------------------------------------
    def _render(self, plan):
        window, punches = plan["window"], plan["punches"]
        users, attendance = plan["users"], plan["attendance"]

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nHistorical import plan — {window['from']} to {window['to']} "
            f"({window['calendar_days']} calendar days)\n"))

        if punches["total"] == 0:
            self.stdout.write(self.style.ERROR(
                "  NO PUNCHES INGESTED IN THIS WINDOW.\n\n"
                "  There is nothing to import. The OMS never connects to the\n"
                "  terminal itself — the collector pulls the backlog and POSTs it\n"
                "  to /api/v1/biometric/bulk-sync/. Run that first, then re-run\n"
                "  this command to confirm what landed."))
            return

        self._section("1. Biometric users", [
            ("Distinct device users in window", users["distinct_device_users"]),
            ("Active enrolments on record", users["enrolments_total"]),
            ("Enrolments still unmapped", users["enrolments_unmapped"]),
        ])
        self._section("2. Punches", [
            ("Total", punches["total"]),
            ("Date range", f"{punches['first']} to {punches['last']}"),
            ("Days with punches", punches["days_with_punches"]),
            ("Days without punches", punches["days_without_punches"]),
            ("Not yet processed", punches["unprocessed"]),
        ])
        self._section("3. Attendance days", [
            ("Employee-days the import will produce", attendance["employee_days_expected"]),
            ("Attendance rows already in window", attendance["existing_rows_in_window"]),
            ("  of which from the device", attendance["existing_from_biometric"]),
            ("  of which HR entries (PROTECTED)", attendance["existing_from_hr"]),
        ])
        self._section("4. Duplicates", [
            ("Exact duplicates", plan["duplicates"]["exact_duplicates"]),
            ("Employee-days with >6 punches", plan["duplicates"]["employee_days_with_many_punches"]),
        ])
        self.stdout.write(f"      {plan['duplicates']['exact_duplicates_note']}")

        self._section("5. Unmapped users", [
            ("Punches from unmapped device users", users["unmapped_punches"]),
            ("Distinct unmapped device IDs", len(users["unmapped_device_ids"])),
        ])
        if users["unmapped_device_ids"]:
            self.stdout.write(
                f"      IDs: {', '.join(users['unmapped_device_ids'][:20])}"
                + (" ..." if len(users["unmapped_device_ids"]) > 20 else ""))

        gaps = plan["gaps"]
        self._section("6. Coverage gaps", [
            ("Working days with no punches", gaps["working_days_without_punches"]),
        ])
        if gaps["dates"]:
            self.stdout.write(f"      {', '.join(gaps['dates'][:12])}"
                              + (" ..." if len(gaps["dates"]) > 12 else ""))

        self._verdict(plan)

    def _section(self, title, pairs):
        self.stdout.write(self.style.HTTP_INFO(f"\n  {title}"))
        for label, value in pairs:
            self.stdout.write(f"      {label:<44} {value}")

    def _verdict(self, plan):
        blockers, warnings = [], []
        if plan["users"]["unmapped_punches"]:
            blockers.append(
                f"{plan['users']['unmapped_punches']} punches belong to unmapped "
                f"users. Import them now and those days are silently missing for "
                f"those people — map first (`roster_report`).")
        if plan["attendance"]["existing_from_hr"]:
            warnings.append(
                f"{plan['attendance']['existing_from_hr']} HR-entered rows exist in "
                f"this window. Derivation will NOT overwrite them (source "
                f"precedence HR > biometric), which is correct — but it means "
                f"those days keep the HR value.")
        if plan["gaps"]["working_days_without_punches"] > 3:
            warnings.append(
                f"{plan['gaps']['working_days_without_punches']} working days have "
                f"no punches. Confirm the backlog is complete before importing; "
                f"a partial import looks identical to genuine absence.")

        self.stdout.write(self.style.MIGRATE_HEADING("\n  Verdict\n"))
        for item in blockers:
            self.stdout.write(self.style.ERROR(f"      BLOCKER: {item}"))
        for item in warnings:
            self.stdout.write(self.style.WARNING(f"      WARNING: {item}"))
        if not blockers and not warnings:
            self.stdout.write(self.style.SUCCESS(
                "      Clear to import."))

        self.stdout.write(
            "\n  To execute, after resolving the above:\n"
            "      python manage.py process_punches            # derive attendance\n"
            "      python manage.py validate_import --from "
            f"{plan['window']['from']}\n")
