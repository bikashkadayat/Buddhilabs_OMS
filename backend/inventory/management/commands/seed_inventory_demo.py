"""
Phase 70.19-J — development fixtures for the inventory module.

WHY THIS EXISTS
---------------
Phase 70.18 found TWO causes behind the empty take-out dropdown. The permission bug
was the interesting one, but the boring one was just as fatal: every database on
this project -- SQLite and the Docker PostgreSQL alike -- held ZERO inventory items
and ZERO assignments. With no data, a perfectly correct selector still renders
nothing, and the next person to look at it re-opens the same investigation.

So the fix ships with the data that demonstrates it. After this command a demo user
opens the take-out form and sees assets, which is the only way "it works" can be
checked by eye rather than by test.

Idempotent: re-running adopts the rows it made last time instead of duplicating
them, so it is safe on a database that has already been seeded.
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from users.models import User

from inventory.models import InventoryCategory, InventoryItem, ItemAssignment

# The five asset classes the brief names. Codes are fixed rather than generated so
# the command can find its own rows again on a re-run -- generate_asset_code() would
# mint a new one every time and the seed would grow without bound.
ASSETS = [
    ("NIF-DEMO-0001", "Dell Latitude 7440",      "Laptop",         "assign"),
    ("NIF-DEMO-0002", "HP LaserJet Pro M404",    "Printer",        "stock"),
    ("NIF-DEMO-0003", "Dell UltraSharp U2723QE", "Monitor",        "assign"),
    ("NIF-DEMO-0004", "Epson EB-2250U",          "Projector",      "stock"),
    ("NIF-DEMO-0005", "MikroTik CRS328-24P",     "Network Device", "stock"),
]


class Command(BaseCommand):
    help = ("Seed demo inventory: 5 assets across 5 categories, with assignments so "
            "the take-out selector has something to show.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--employee", default=None,
            help="Email of the user to assign assets to. Defaults to the first "
                 "non-manager, so the demo lands on someone who sees the restricted "
                 "view rather than an admin who sees everything anyway.")
        parser.add_argument(
            "--reset", action="store_true",
            help="Delete previously seeded NIF-DEMO-* assets first.")

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            deleted, _ = InventoryItem.objects.filter(asset_code__startswith="NIF-DEMO-").delete()
            self.stdout.write(self.style.WARNING(f"Removed {deleted} previously seeded rows."))

        holder = self._holder(options["employee"])
        if holder is None:
            self.stderr.write(self.style.ERROR(
                "No user to assign to. Create one first (users.seed_admin, or the app's "
                "own registration), then re-run."))
            return

        actor = (User.objects.filter(role=User.Roles.ADMIN).first() or holder)
        today = timezone.localtime(timezone.now()).date()
        created = assigned = 0

        for code, name, category_name, disposition in ASSETS:
            category, _ = InventoryCategory.objects.get_or_create(name=category_name)
            item, was_created = InventoryItem.objects.get_or_create(
                asset_code=code,
                defaults={
                    "name": name,
                    "category": category,
                    "department": getattr(holder, "department_ref", None),
                    "status": InventoryItem.Status.AVAILABLE,
                    "condition": InventoryItem.Condition.GOOD,
                    "purchase_date": today - timedelta(days=365),
                },
            )
            created += int(was_created)
            # Keep the category on rows that predate it, so a re-run repairs rather
            # than only adds.
            if item.category_id != category.id:
                item.category = category
                item.save(update_fields=["category"])

            if disposition != "assign":
                continue
            if ItemAssignment.objects.filter(item=item, is_active=True).exists():
                continue
            ItemAssignment.objects.create(
                item=item, item_code=item.asset_code, item_name=item.name,
                assigned_to=holder, assigned_to_name=holder.get_full_name() or holder.username,
                assigned_by=actor, assigned_by_name=actor.get_full_name() or actor.username,
                assigned_date=today, handover_condition=item.condition, is_active=True)
            item.status = InventoryItem.Status.ASSIGNED
            item.save(update_fields=["status"])
            assigned += 1

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {created} new asset(s); {assigned} assigned to {holder.email}."))
        self.stdout.write(
            f"  {holder.email} (Employee scope) should now see {assigned} asset(s) "
            f"in the take-out selector.")
        self.stdout.write(
            f"  An Inventory Officer additionally sees "
            f"{InventoryItem.objects.filter(asset_code__startswith='NIF-DEMO-', status=InventoryItem.Status.AVAILABLE).count()} "
            f"in-stock asset(s).")

    def _holder(self, email):
        if email:
            user = User.objects.filter(email=email).first()
            if user is None:
                self.stderr.write(self.style.ERROR(f"No user with email {email}."))
            return user
        # A plain employee is the interesting subject: they exercise the narrowest
        # scope, which is the one that was broken.
        return (User.objects.filter(role=User.Roles.MAKER, is_active=True).order_by("email").first()
                or User.objects.filter(is_active=True).order_by("email").first())
