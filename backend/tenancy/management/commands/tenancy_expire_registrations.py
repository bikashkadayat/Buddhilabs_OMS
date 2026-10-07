"""Nightly: expire unverified registrations and release the subdomains they hold.

Pairs with ``tenancy_expire_exports``. Cheap, idempotent, and the only thing
that frees a workspace address somebody asked for and never confirmed -- the
partial unique index that holds the reservation only covers the live
statuses, so moving a row to EXPIRED is what releases the name.
"""
from django.core.management.base import BaseCommand

from tenancy import registration


class Command(BaseCommand):
    help = ("Expire unverified registrations past their verification "
            "deadline, releasing the subdomains they were holding.")

    def handle(self, *args, **options):
        result = registration.expire_stale()
        for slug in result["released_slugs"]:
            self.stdout.write(f"  released {slug}")
        self.stdout.write(self.style.SUCCESS(
            f"expired {result['expired']} registration(s)"))
