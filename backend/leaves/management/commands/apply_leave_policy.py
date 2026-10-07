"""
Re-apply the organisation's leave-entitlement policy, and re-resolve every
employee's category from their engagement type and length of service.

Why this exists: the entitlement matrix is HR-editable, which is the point --
but it also means the database can drift away from the written policy with
nothing to notice. This command puts it back, and reports what it changed
rather than doing it silently.

    python manage.py apply_leave_policy --dry-run
    python manage.py apply_leave_policy
    python manage.py apply_leave_policy --rebuild-balances --year 2026

It NEVER touches leave that has been taken, approved or recorded. Only the
entitlement rules, the cached category on each user, and -- when asked --
the derived balance rows, which are regenerated from the corrected matrix.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from leaves import category_engine


class Command(BaseCommand):
    help = "Re-apply the leave entitlement matrix and re-resolve staff categories."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would change; write nothing.")
        parser.add_argument("--rebuild-balances", action="store_true",
                            help="Also regenerate LeaveBalance rows from the matrix.")
        parser.add_argument("--year", type=int, default=None,
                            help="Year for --rebuild-balances (default: current).")

    def handle(self, *args, **opts):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        dry = opts["dry_run"]
        year = opts["year"] or category_engine.nepal_today().year

        with transaction.atomic():
            stats = category_engine.seed_entitlement_matrix()
            self.stdout.write(
                f"entitlement rules: {stats['created']} created, "
                f"{stats['corrected']} corrected, {stats['unchanged']} already correct")

            moved, rebuilt = [], 0
            for user in User.objects.filter(is_active=True):
                before = user.leave_category
                after, flag = category_engine.resolve_and_cache(user, save=not dry)
                if before != after:
                    moved.append(f"  {user.email}: {before or '—'} -> {after}"
                                 + (f"   [{flag}]" if flag else ""))
                if opts["rebuild_balances"] and not dry:
                    category_engine.ensure_category_balances(user, year)
                    rebuilt += 1

            self.stdout.write(f"categories re-resolved: {len(moved)} changed")
            for line in moved:
                self.stdout.write(line)
            if rebuilt:
                self.stdout.write(f"balances rebuilt for {year}: {rebuilt} employees")

            if dry:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("dry run — nothing written"))
            else:
                self.stdout.write(self.style.SUCCESS("policy applied"))
