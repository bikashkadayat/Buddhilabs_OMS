"""Rebuild derived attendance from raw punches.

The audit and repair tool. Run it after a mapping change, after a backfill, or
whenever derived rows are in doubt — punches are immutable, so re-deriving is
always safe and always converges on the same answer.
"""
from datetime import datetime

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from attendance.models import Attendance
from biometric import derivation
from biometric.models import AttendancePunch
from users.models import User


def _parse_date(raw, flag):
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise CommandError(f"{flag} must be a date in YYYY-MM-DD format, got {raw!r}.")


class Command(BaseCommand):
    help = "Re-derive attendance.Attendance rows from immutable biometric punches."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="A single day (YYYY-MM-DD).")
        parser.add_argument("--from", dest="date_from", help="Range start (YYYY-MM-DD).")
        parser.add_argument("--to", dest="date_to", help="Range end (YYYY-MM-DD), inclusive.")
        parser.add_argument(
            "--employee", action="append", default=[],
            help="Limit to an employee by UUID, employee_id or email. Repeatable.")
        parser.add_argument(
            "--force", action="store_true",
            help="Overwrite HR-corrected rows too. Off by default: an HR correction "
                 "normally outranks the device.")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be rebuilt without writing anything.")

    def handle(self, *args, **options):
        start, end = self._resolve_range(options)
        users = self._resolve_users(options["employee"])

        if options["dry_run"]:
            return self._report_plan(start, end, users, options["force"])

        result = derivation.rederive_range(start, end, users=users, force=options["force"])
        self.stdout.write(
            f"Re-derived {start} → {end}: {result['days']} employee-day(s), "
            f"{result['derived']} written, {result['reverted']} reverted, "
            f"{result['failed']} failed."
        )
        if options["force"]:
            self.stdout.write(self.style.WARNING(
                "--force was used: HR-corrected rows were overwritten."))
        if result["failed"]:
            self.stderr.write(self.style.ERROR(
                f"{result['failed']} employee-day(s) failed; see logs for tracebacks."))

    # -- helpers ---------------------------------------------------------

    def _resolve_range(self, options):
        if options["date"]:
            if options["date_from"] or options["date_to"]:
                raise CommandError("Use --date on its own, or --from/--to — not both.")
            day = _parse_date(options["date"], "--date")
            return day, day

        if not options["date_from"] and not options["date_to"]:
            raise CommandError("Provide --date, or --from and/or --to.")

        # An open-ended range is resolved against the punches that actually
        # exist, so `--from 2023-01-01` does not scan to the end of time.
        start = (_parse_date(options["date_from"], "--from") if options["date_from"]
                 else AttendancePunch.objects.order_by("local_date")
                      .values_list("local_date", flat=True).first())
        end = (_parse_date(options["date_to"], "--to") if options["date_to"]
               else timezone.localdate())
        if start is None:
            raise CommandError("No punches exist, so there is nothing to re-derive.")
        if start > end:
            raise CommandError(f"--from ({start}) is after --to ({end}).")
        return start, end

    def _resolve_users(self, identifiers):
        if not identifiers:
            return None
        users = []
        for ident in identifiers:
            match = User.objects.filter(
                Q(employee_id__iexact=ident) | Q(email__iexact=ident) | Q(username__iexact=ident)
            ).first()
            if match is None:
                try:
                    match = User.objects.filter(pk=ident).first()
                except (ValueError, TypeError, ValidationError):
                    # ident was not a well-formed UUID — fall through to the error.
                    match = None
            if match is None:
                raise CommandError(f"No employee matches {ident!r}.")
            users.append(match)
        return users

    def _report_plan(self, start, end, users, force):
        qs = AttendancePunch.objects.filter(
            user__isnull=False, local_date__gte=start, local_date__lte=end)
        if users:
            qs = qs.filter(user__in=users)
        pairs = qs.values_list("user_id", "local_date").distinct()
        count = pairs.count()

        self.stdout.write(f"[dry-run] {start} → {end}")
        self.stdout.write(f"[dry-run] {count} employee-day(s) would be re-derived "
                          f"from {qs.count()} punch(es).")
        if users:
            self.stdout.write(f"[dry-run] limited to: "
                              f"{', '.join(u.get_full_name() or u.username for u in users)}")

        protected = Attendance.objects.filter(
            date__gte=start, date__lte=end,
        ).filter(Q(source=Attendance.Source.HR) | Q(marked_by=Attendance.MarkedBy.HR))
        if users:
            protected = protected.filter(employee__in=users)
        n_protected = protected.count()
        if n_protected:
            self.stdout.write(
                self.style.WARNING(f"[dry-run] {n_protected} HR-corrected row(s) would be "
                                   f"OVERWRITTEN by --force.")
                if force else
                f"[dry-run] {n_protected} HR-corrected row(s) would be preserved.")
        self.stdout.write("[dry-run] nothing was written.")
