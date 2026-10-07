"""Reconcile every tenant's seat and storage counters.

    python manage.py tenancy_refresh_counters [--no-storage] [--slug nif]

``seat_count`` is maintained incrementally by signals (tenancy/seat_signals.py)
and can drift: ``bulk_create`` emits no ``post_save``, and a raw UPDATE emits
nothing at all. ``storage_bytes`` is never maintained incrementally -- it is
only ever computed here, because summing eleven file columns is not something
to do on a page load.

Intended for the existing cron container, nightly. Idempotent.

NEEDS A ROLE THAT CAN CROSS TENANTS? No -- and that is deliberate. It binds
each tenant in turn (``tenant_context``) and only ever reads one tenant's rows
at a time, so it runs correctly under row-level security as the ordinary
application role. Compare ``monitoring.backup_verify``, which genuinely cannot
and refuses to try.
"""
from django.core.management.base import BaseCommand

from tenancy import counters
from tenancy.context import no_tenant, tenant_context
from tenancy.models import Organization


class Command(BaseCommand):
    help = "Recompute Organization.seat_count and storage_bytes."

    def add_arguments(self, parser):
        parser.add_argument("--slug", help="One organization, by slug.")
        parser.add_argument(
            "--no-storage", action="store_true",
            help="Seats only. Much faster; storage is the expensive half.")
        parser.add_argument(
            "--check", action="store_true",
            help="Report drift without writing. Exit code stays 0.")

    def handle(self, *args, **options):
        with no_tenant():
            queryset = Organization.objects.all()
            if options["slug"]:
                queryset = queryset.filter(slug=options["slug"])
            organizations = list(queryset)

        if not organizations:
            self.stdout.write(self.style.WARNING("No organization matched."))
            return

        drifted = 0
        for organization in organizations:
            with tenant_context(organization):
                seats = counters.seats_for(organization)
                storage = (organization.storage_bytes
                           if options["no_storage"]
                           else counters.storage_for(organization))

                seat_drift = seats - organization.seat_count
                storage_drift = storage - organization.storage_bytes
                if seat_drift or storage_drift:
                    drifted += 1

                if options["check"]:
                    if seat_drift or storage_drift:
                        self.stdout.write(self.style.WARNING(
                            f"  {organization.slug}: seats "
                            f"{organization.seat_count} -> {seats} "
                            f"({seat_drift:+d}), storage "
                            f"{organization.storage_bytes} -> {storage} "
                            f"({storage_drift:+d})"))
                    continue

                fields = {"seat_count": seats}
                if not options["no_storage"]:
                    fields["storage_bytes"] = storage
                Organization.objects.filter(pk=organization.pk).update(**fields)
                self.stdout.write(
                    f"  {organization.slug}: {seats} seat(s), "
                    f"{storage} byte(s)"
                    + (f"  [drift {seat_drift:+d}]" if seat_drift else ""))

        verb = "would change" if options["check"] else "reconciled"
        self.stdout.write(self.style.SUCCESS(
            f"{len(organizations)} organization(s) checked; {drifted} {verb}."))
