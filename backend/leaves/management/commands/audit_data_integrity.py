"""
Detect (and optionally repair) inconsistencies between Leaves, their per-day
records, and the derived balances. Exit code 0 = clean, 1 = issues found.
"""
import sys
from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from leaves.models import (
    EnterpriseLeaveBalance,
    Leave,
    LeaveDayRecord,
)
from leaves import services

from monitoring import heartbeat

ZERO = Decimal("0")
WORKING = dict(is_weekend=False, is_holiday=False)


def _expected_calendar_days(leave):
    return (leave.end_date - leave.start_date).days + 1


def _sum(records):
    return sum((r.portion_days for r in records), ZERO)


class Command(BaseCommand):
    help = "Audit leave data integrity; use --fix to repair safe issues."

    def add_arguments(self, parser):
        parser.add_argument("--fix", action="store_true", help="Auto-repair safe issues.")

    def handle(self, *args, **options):
        # Phase 11 (audit finding M5): record a heartbeat so a job that stops
        # running becomes a red tile and an alert instead of silence. On an
        # exception the heartbeat is written FAILED and the error re-raised, so
        # cron still sees a non-zero exit.
        with heartbeat.heartbeat("INTEGRITY_AUDIT"):
            return self._handle(*args, **options)

    def _handle(self, *args, **options):
        fix = options["fix"]
        issues = []

        issues += self._check_missing_records(fix)
        issues += self._check_balance_mismatches(fix)
        issues += self._check_stale_records_for_deleted(fix)
        issues += self._check_duplicate_bookings()  # never auto-fixed

        self.stdout.write("")
        if issues:
            self.stdout.write(self.style.ERROR(f"Integrity issues found: {len(issues)}"))
            for line in issues:
                self.stdout.write(f"  - {line}")
            if fix:
                # Re-audit read-only checks after repair.
                remaining = (
                    self._check_missing_records(False)
                    + self._check_balance_mismatches(False)
                    + self._check_stale_records_for_deleted(False)
                    + self._check_duplicate_bookings()
                )
                if not remaining:
                    self.stdout.write(self.style.SUCCESS("All auto-fixable issues repaired."))
                    services.audit_log(None, "SYSTEM_INTEGRITY_AUDIT",
                                       metadata={"result": "repaired", "fixed": len(issues)})
                    return
                services.audit_log(None, "SYSTEM_INTEGRITY_AUDIT",
                                   metadata={"result": "issues_remain", "count": len(remaining)})
                sys.exit(1)
            services.audit_log(None, "SYSTEM_INTEGRITY_AUDIT",
                               metadata={"result": "issues", "count": len(issues)})
            sys.exit(1)

        self.stdout.write(self.style.SUCCESS("Data integrity: CLEAN. No issues found."))
        services.audit_log(None, "SYSTEM_INTEGRITY_AUDIT", metadata={"result": "clean"})

    # -- checks -----------------------------------------------------------
    def _check_missing_records(self, fix):
        issues = []
        approved = Leave.objects.filter(status=Leave.Status.APPROVED, is_deleted=False)
        for leave in approved:
            actual = leave.day_records.count()
            expected = _expected_calendar_days(leave)
            if actual != expected:
                issues.append(
                    f"Leave {leave.id} ({leave.user}) approved but has {actual} "
                    f"day records (expected {expected})."
                )
                if fix:
                    services.generate_leave_day_records(leave)
                    services.sync_leave_day_records(leave)
        return issues

    def _check_balance_mismatches(self, fix):
        issues = []
        # Locked (year-end) balances are frozen by design; skip them.
        for balance in EnterpriseLeaveBalance.objects.filter(locked_at__isnull=True).select_related("leave_type", "user"):
            used = _sum(LeaveDayRecord.objects.filter(
                user=balance.user, leave_type=balance.leave_type, year=balance.year,
                status=LeaveDayRecord.Status.APPROVED, **WORKING,
            ))
            pending = _sum(LeaveDayRecord.objects.filter(
                user=balance.user, leave_type=balance.leave_type, year=balance.year,
                status=LeaveDayRecord.Status.PENDING, **WORKING,
            ))
            if balance.used_days != used or balance.pending_days != pending:
                issues.append(
                    f"Balance {balance.user}/{balance.leave_type.code}/{balance.year}: "
                    f"stored used={balance.used_days} pending={balance.pending_days}, "
                    f"records used={used} pending={pending}."
                )
                if fix:
                    services.recompute_leave_balance(balance.user, balance.leave_type, balance.year)
        return issues

    def _check_stale_records_for_deleted(self, fix):
        issues = []
        stale = LeaveDayRecord.objects.filter(
            leave_request__is_deleted=True,
        ).exclude(status=LeaveDayRecord.Status.CANCELLED)
        grouped = defaultdict(int)
        for rec in stale:
            grouped[rec.leave_request_id] += 1
        for leave_id, count in grouped.items():
            issues.append(f"Leave {leave_id} is soft-deleted but has {count} active day records.")
        if fix and stale.exists():
            with transaction.atomic():
                stale.update(status=LeaveDayRecord.Status.CANCELLED)
        return issues

    def _check_duplicate_bookings(self):
        """Same user + date booked by more than one active leave (double-booking)."""
        issues = []
        active = LeaveDayRecord.objects.exclude(
            status__in=[LeaveDayRecord.Status.REJECTED, LeaveDayRecord.Status.CANCELLED]
        ).values("user_id", "date", "leave_request_id")
        seen = defaultdict(set)
        for row in active:
            seen[(row["user_id"], row["date"])].add(row["leave_request_id"])
        for (user_id, day), leave_ids in seen.items():
            if len(leave_ids) > 1:
                issues.append(
                    f"Double-booking: user {user_id} on {day} appears in "
                    f"{len(leave_ids)} active leaves (manual review needed)."
                )
        return issues
