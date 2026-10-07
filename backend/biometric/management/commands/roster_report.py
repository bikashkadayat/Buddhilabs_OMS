"""The employee mapping report (Phase 12).

Every device enrolment, the OMS employee it is probably is, and how confident
that guess is — so HR can work through one list instead of clicking through the
mapping UI one row at a time.

    python manage.py roster_report
    python manage.py roster_report --csv mapping.csv
    python manage.py roster_report --unmapped-only

It **proposes and never writes**. A wrong auto-mapping silently misattributes
one person's attendance to another and is very hard to notice afterwards —
by the time anyone spots it, weeks of payroll-relevant data are wrong. Every
mapping stays an explicit HR decision made through the Phase 4 API.

Scores come from ``biometric.matching``, which is the same engine the mapping
UI uses, so this report and the suggestions HR sees on screen can never differ.
"""
import csv
import sys

from django.core.management.base import BaseCommand

from biometric import matching
from biometric.models import BiometricEmployee

HEADERS = [
    "Device", "Device User ID", "Device Name", "Device Employee Number",
    "Mapping Status", "Mapped To", "Suggested OMS Employee", "Suggested Employee ID",
    "Match Score", "Match Type", "Confidence", "Runner Up", "Runner Up Score",
]

# A gap this small between the best and second-best candidate means the engine
# is effectively guessing between two people — the most dangerous case, because
# the score alone still looks high.
AMBIGUITY_GAP = 0.05


def confidence(score, gap):
    if score <= 0:
        return "none"
    # Ambiguity is checked BEFORE the exact-match shortcut, and that order is
    # the whole point. Two employees with the same name both score 1.0, so
    # returning "exact" would give the most confident possible verdict to the
    # one case that is genuinely a coin flip — and HR would accept it.
    if gap is not None and gap < AMBIGUITY_GAP:
        return "AMBIGUOUS"
    if score >= matching.SCORE_EXACT_NAME:
        return "exact"
    if score >= matching.SCORE_TOKEN_SUBSET:
        return "high"
    if score >= matching.FUZZY_FLOOR:
        return "medium"
    return "low"


class Command(BaseCommand):
    help = "Report every device enrolment with its suggested OMS employee."

    def add_arguments(self, parser):
        parser.add_argument("--csv", dest="csv_path",
                            help="Write CSV here (use '-' for stdout).")
        parser.add_argument("--unmapped-only", action="store_true")
        parser.add_argument("--device", help="Limit to one device name or label.")

    def handle(self, *args, **options):
        rows = list(self._rows(options))

        if options["csv_path"]:
            self._write_csv(rows, options["csv_path"])
            return
        self._render(rows)

    def _rows(self, options):
        queryset = (BiometricEmployee.objects
                    .filter(is_active=True)
                    .select_related("device", "user")
                    .order_by("device__name", "device_user_id"))
        if options["unmapped_only"]:
            queryset = queryset.filter(user__isnull=True)
        if options["device"]:
            queryset = queryset.filter(device__name__icontains=options["device"])

        for enrolment in queryset:
            if enrolment.user_id:
                yield {
                    "Device": enrolment.device.name,
                    "Device User ID": enrolment.device_user_id,
                    "Device Name": enrolment.device_name or "",
                    "Device Employee Number": enrolment.card or "",
                    "Mapping Status": "mapped",
                    "Mapped To": enrolment.user.get_full_name(),
                    "Suggested OMS Employee": "",
                    "Suggested Employee ID": "",
                    "Match Score": "",
                    "Match Type": "",
                    "Confidence": "",
                    "Runner Up": "",
                    "Runner Up Score": "",
                }
                continue

            suggestions = matching.suggest_for_mapping(enrolment, limit=2)
            best = suggestions[0] if suggestions else None
            runner_up = suggestions[1] if len(suggestions) > 1 else None
            gap = (best["score"] - runner_up["score"]) if (best and runner_up) else None

            yield {
                "Device": enrolment.device.name,
                "Device User ID": enrolment.device_user_id,
                "Device Name": enrolment.device_name or "",
                "Device Employee Number": enrolment.card or "",
                "Mapping Status": "UNMAPPED",
                "Mapped To": "",
                "Suggested OMS Employee": best["user"].get_full_name() if best else "",
                "Suggested Employee ID": (best["user"].employee_id or "") if best else "",
                "Match Score": f"{best['score']:.3f}" if best else "0.000",
                "Match Type": best["match_type"] if best else "manual_required",
                "Confidence": confidence(best["score"] if best else 0.0, gap),
                "Runner Up": runner_up["user"].get_full_name() if runner_up else "",
                "Runner Up Score": f"{runner_up['score']:.3f}" if runner_up else "",
            }

    # -- output ------------------------------------------------------------
    def _write_csv(self, rows, path):
        handle = sys.stdout if path == "-" else open(path, "w", newline="",
                                                     encoding="utf-8-sig")
        try:
            writer = csv.DictWriter(handle, fieldnames=HEADERS)
            writer.writeheader()
            writer.writerows(rows)
        finally:
            if handle is not sys.stdout:
                handle.close()
                self.stderr.write(self.style.SUCCESS(
                    f"Wrote {len(rows)} row(s) to {path}"))

    def _render(self, rows):
        if not rows:
            self.stdout.write(self.style.WARNING(
                "No active enrolments. Run a roster sync from the collector "
                "first — there is nothing to map yet."))
            return

        self.stdout.write(self.style.MIGRATE_HEADING("\nEmployee mapping report\n"))
        self.stdout.write(
            f"{'Device User':<12} {'Device Name':<22} {'Status':<9} "
            f"{'Suggested OMS Employee':<26} {'Score':>6}  Confidence")
        self.stdout.write("-" * 92)

        for row in rows:
            suggested = row["Suggested OMS Employee"] or row["Mapped To"] or "—"
            self.stdout.write(
                f"{row['Device User ID']:<12} {row['Device Name'][:22]:<22} "
                f"{row['Mapping Status']:<9} {suggested[:26]:<26} "
                f"{row['Match Score'] or '—':>6}  {row['Confidence']}")
            if row["Confidence"] == "AMBIGUOUS":
                self.stdout.write(self.style.WARNING(
                    f"{'':<12} ^ also matches {row['Runner Up']} "
                    f"({row['Runner Up Score']}) — decide by hand"))

        self._summary(rows)

    def _summary(self, rows):
        mapped = sum(1 for r in rows if r["Mapping Status"] == "mapped")
        unmapped = len(rows) - mapped
        ambiguous = sum(1 for r in rows if r["Confidence"] == "AMBIGUOUS")
        no_suggestion = sum(1 for r in rows if r["Match Type"] == "manual_required")

        self.stdout.write("")
        self.stdout.write(f"  Total enrolments : {len(rows)}")
        self.stdout.write(f"  Mapped           : {mapped}")
        self.stdout.write(f"  Unmapped         : {unmapped}")
        if ambiguous:
            self.stdout.write(self.style.WARNING(
                f"  Ambiguous        : {ambiguous} — two candidates within "
                f"{AMBIGUITY_GAP} of each other. Do NOT accept these on score alone."))
        if no_suggestion:
            self.stdout.write(
                f"  No suggestion    : {no_suggestion} — manual selection required")
        if unmapped:
            self.stdout.write(self.style.WARNING(
                "\n  Unmapped enrolments produce punches that never become "
                "attendance.\n  Map them before go-live, or that person's days "
                "are simply missing."))
