"""Post-import validation (Phase 12).

Run immediately after a backfill. Confirms the import produced what the plan
predicted, and — more importantly — that the numbers are *plausible*. A backfill
that silently derives half the days it should looks exactly like a successful
one until somebody's payslip is wrong.

    python manage.py validate_import --from 2026-07-14

Every figure is read from stored columns, the same ones the dashboards and the
reports read, so a discrepancy here is a real discrepancy and not a different
way of counting.
"""
import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q, Sum
from django.utils import timezone

from attendance.models import Attendance
from biometric.models import AttendancePunch, BiometricEmployee

DEFAULT_FROM = date(2026, 7, 14)

# Below this share of expected employee-days, the import is treated as
# incomplete rather than merely low — chosen because a genuine month rarely
# derives under 70% of the days its punches cover.
COVERAGE_FLOOR = 0.70


class Command(BaseCommand):
    help = "Validate the results of a historical import."

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

        result = self._validate(start, end)
        if options["as_json"]:
            self.stdout.write(json.dumps(result, indent=2, default=str))
            return
        self._render(result)

    def _validate(self, start, end):
        punches = AttendancePunch.objects.filter(local_date__gte=start,
                                                 local_date__lte=end)
        rows = Attendance.objects.filter(date__gte=start, date__lte=end)

        counts = rows.aggregate(
            total=Count("id"),
            present=Count("id", filter=Q(status=Attendance.Status.PRESENT)),
            late=Count("id", filter=Q(status=Attendance.Status.LATE)),
            half_day=Count("id", filter=Q(status=Attendance.Status.HALF_DAY)),
            wfh=Count("id", filter=Q(status=Attendance.Status.WORK_FROM_HOME)),
            absent=Count("id", filter=Q(status=Attendance.Status.ABSENT)),
            comp_eligible=Count("id", filter=Q(comp_off_eligible=True)),
            with_overtime=Count("id", filter=Q(overtime_hours__gt=0)),
            overtime_hours=Sum("overtime_hours"),
            worked_hours=Sum("working_hours"),
            late_minutes=Sum("late_minutes"),
        )

        expected_days = (punches.filter(user__isnull=False)
                         .values("user_id", "local_date").distinct().count())
        derived = rows.filter(source=Attendance.Source.BIOMETRIC).count()
        coverage = (derived / expected_days) if expected_days else None

        return {
            "window": {"from": start.isoformat(), "to": end.isoformat()},
            "users": {
                "distinct_with_attendance": rows.values("employee_id").distinct().count(),
                "enrolments_active": BiometricEmployee.objects.filter(is_active=True).count(),
                "enrolments_mapped": BiometricEmployee.objects.filter(
                    is_active=True, user__isnull=False).count(),
            },
            "punches": {
                "total": punches.count(),
                "unprocessed": punches.filter(is_processed=False).count(),
                "unmapped": punches.filter(user__isnull=True).count(),
            },
            "attendance": {k: (float(v) if hasattr(v, "quantize") else (v or 0))
                           for k, v in counts.items()},
            "derivation": {
                "employee_days_expected": expected_days,
                "rows_from_biometric": derived,
                "coverage": round(coverage, 4) if coverage is not None else None,
            },
            "checks": self._checks(counts, punches, expected_days, derived, coverage),
        }

    def _checks(self, counts, punches, expected_days, derived, coverage):
        checks = []

        def add(name, ok, detail):
            checks.append({"check": name, "ok": ok, "detail": detail})

        unprocessed = punches.filter(is_processed=False).count()
        add("All punches processed", unprocessed == 0,
            f"{unprocessed} punch(es) still unprocessed"
            + ("" if unprocessed == 0 else " — run process_punches"))

        unmapped = punches.filter(user__isnull=True).count()
        add("No punches from unmapped users", unmapped == 0,
            f"{unmapped} punch(es) from unmapped enrolments"
            + ("" if unmapped == 0 else " — those days are MISSING for those people"))

        add("Attendance produced", derived > 0,
            f"{derived} row(s) with source=biometric")

        if coverage is not None:
            add("Derivation coverage", coverage >= COVERAGE_FLOOR,
                f"{coverage:.1%} of {expected_days} expected employee-days"
                + ("" if coverage >= COVERAGE_FLOOR
                   else f" — below the {COVERAGE_FLOOR:.0%} floor, the import looks incomplete"))

        # A run of punches that produced zero Late and zero Half Day is
        # suspicious rather than excellent: the NIF boundaries are 11:45 and
        # 13:00, and a real month almost always crosses them.
        attended = (counts["present"] or 0) + (counts["late"] or 0) + (counts["half_day"] or 0)
        if attended > 50:
            add("Status distribution plausible",
                (counts["late"] or 0) + (counts["half_day"] or 0) > 0,
                f"present={counts['present']}, late={counts['late']}, "
                f"half_day={counts['half_day']}"
                + ("" if (counts["late"] or 0) + (counts["half_day"] or 0) > 0
                   else " — zero late AND zero half days across a whole window "
                        "suggests the policy boundaries did not apply"))
        return checks

    def _render(self, result):
        window = result["window"]
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nImport validation — {window['from']} to {window['to']}\n"))

        attendance = result["attendance"]
        self._section("Users", [
            ("Employees with attendance", result["users"]["distinct_with_attendance"]),
            ("Active enrolments", result["users"]["enrolments_active"]),
            ("Mapped enrolments", result["users"]["enrolments_mapped"]),
        ])
        self._section("Punches", [
            ("Total", result["punches"]["total"]),
            ("Unprocessed", result["punches"]["unprocessed"]),
            ("From unmapped users", result["punches"]["unmapped"]),
        ])
        self._section("Attendance records", [
            ("Total", attendance["total"]),
            ("Present", attendance["present"]),
            ("Late", attendance["late"]),
            ("Half day", attendance["half_day"]),
            ("Work from home", attendance["wfh"]),
            ("Absent", attendance["absent"]),
            ("Comp-off eligible", attendance["comp_eligible"]),
            ("Days with overtime", attendance["with_overtime"]),
            ("Total overtime hours", attendance["overtime_hours"]),
            ("Total worked hours", attendance["worked_hours"]),
            ("Total late minutes", attendance["late_minutes"]),
        ])
        self._section("Derivation", [
            ("Employee-days expected", result["derivation"]["employee_days_expected"]),
            ("Rows from biometric", result["derivation"]["rows_from_biometric"]),
            ("Coverage", f"{result['derivation']['coverage']:.1%}"
                         if result["derivation"]["coverage"] is not None else "n/a"),
        ])

        self.stdout.write(self.style.MIGRATE_HEADING("\n  Checks\n"))
        failed = 0
        for check in result["checks"]:
            if check["ok"]:
                self.stdout.write(f"      {self.style.SUCCESS('PASS')}  "
                                  f"{check['check']}: {check['detail']}")
            else:
                failed += 1
                self.stdout.write(f"      {self.style.ERROR('FAIL')}  "
                                  f"{check['check']}: {check['detail']}")

        self.stdout.write("")
        if failed:
            self.stdout.write(self.style.ERROR(
                f"  {failed} check(s) failed — do not sign off the import."))
        else:
            self.stdout.write(self.style.SUCCESS(
                "  All checks passed. Cross-check one employee's month against "
                "the device log by hand before signing off."))

    def _section(self, title, pairs):
        self.stdout.write(self.style.HTTP_INFO(f"\n  {title}"))
        for label, value in pairs:
            self.stdout.write(f"      {label:<34} {value}")
