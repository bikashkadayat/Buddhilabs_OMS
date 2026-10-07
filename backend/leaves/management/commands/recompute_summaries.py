"""Recompute materialized weekly/monthly leave summaries."""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from leaves.models import LeaveDayRecord
from leaves import services

from monitoring import heartbeat


class Command(BaseCommand):
    help = "Recompute weekly and/or monthly leave summaries from LeaveDayRecord."

    def add_arguments(self, parser):
        parser.add_argument("--scope", choices=["weekly", "monthly", "both"], default="both")
        parser.add_argument("--year", type=int, default=timezone.now().year)
        parser.add_argument("--month", type=int, default=None, help="Target a single month (1-12).")
        parser.add_argument("--week", type=int, default=None, help="Target a single ISO week (1-53).")
        parser.add_argument("--this-week", action="store_true", help="Only the current ISO week.")
        parser.add_argument("--this-month", action="store_true", help="Only the current month.")

    def handle(self, *args, **options):
        # Phase 11 (audit finding M5): record a heartbeat so a job that stops
        # running becomes a red tile and an alert instead of silence. On an
        # exception the heartbeat is written FAILED and the error re-raised, so
        # cron still sees a non-zero exit.
        with heartbeat.heartbeat("RECOMPUTE_SUMMARIES"):
            return self._handle(*args, **options)

    def _handle(self, *args, **options):
        scope = options["scope"]
        year = options["year"]
        today = date.today()

        weekly = 0
        monthly = 0
        if scope in ("weekly", "both"):
            week = options["week"]
            if options["this_week"]:
                iso = today.isocalendar()
                year, week = iso.year, iso.week
            weekly = self._recompute_weekly(year, week)
        if scope in ("monthly", "both"):
            month = options["month"]
            if options["this_month"]:
                year, month = today.year, today.month
            monthly = self._recompute_monthly(year, month)

        services.audit_log(
            None, "SYSTEM_RECOMPUTE_SUMMARIES",
            metadata={"year": year, "scope": scope, "weekly": weekly, "monthly": monthly},
        )
        self.stdout.write(self.style.SUCCESS(
            f"Recomputed {weekly} weekly and {monthly} monthly summaries (year {year})."
        ))

    def _recompute_weekly(self, year, week):
        qs = LeaveDayRecord.objects.filter(year=year)
        if week:
            qs = qs.filter(week_number=week)
        combos = qs.values_list("user_id", "year", "week_number").distinct()
        count = 0
        for user_id, yr, wk in combos:
            services.recompute_weekly_summary_by_id(user_id, yr, wk)
            count += 1
        self.stdout.write(f"  weekly: {count} (user, week) summaries")
        return count

    def _recompute_monthly(self, year, month):
        qs = LeaveDayRecord.objects.filter(year=year)
        if month:
            qs = qs.filter(month=month)
        combos = qs.values_list("user_id", "year", "month").distinct()
        count = 0
        for user_id, yr, mo in combos:
            services.recompute_monthly_summary_by_id(user_id, yr, mo)
            count += 1
        self.stdout.write(f"  monthly: {count} (user, month) summaries")
        return count
