"""Pull every biometric device whose sync interval has elapsed, in every tenant.

    python manage.py device_sync_due            # run once (cron, every minute)
    python manage.py device_sync_due --loop     # run continuously, every 60s
    python manage.py device_sync_due --slug acme --dry-run

The scheduler behind Organization Settings -> Biometric Devices -> Auto Sync.
Each device carries its own interval (5, 15 or 30 minutes, or manual only);
this command is what honours it. Run it every minute and it does nothing for
a device that is not due yet.

TENANCY. It binds each organization in turn with ``tenant_context`` and only
ever reads that tenant's devices, so it runs under row-level security as the
ordinary application role, and every punch a device yields is written into
the organization that owns the device -- never another. Suspended, cancelled
and archived organizations are skipped: their workspace is closed, and
continuing to pull their terminals would keep collecting personal data for an
account that has stopped.

One device failing never stops the others: each pull is logged on its own
(``DeviceSyncLog``, sync type ``pull``) and the loop moves on.
"""
import logging
import signal
import threading

from django.core.management.base import BaseCommand

from biometric import devices
from biometric.models import DeviceSyncLog
from tenancy.context import no_tenant, tenant_context
from tenancy.models import Organization

logger = logging.getLogger(__name__)

# Organizations whose terminals are pulled. PROVISIONING has no users yet,
# and the closed states are explained in the module docstring.
SYNCING_STATUSES = (Organization.Status.TRIAL, Organization.Status.ACTIVE,
                    Organization.Status.GRACE)


class Command(BaseCommand):
    help = "Pull biometric devices whose auto-sync interval has elapsed (all tenants)."

    def add_arguments(self, parser):
        parser.add_argument("--slug", help="Only this organization.")
        parser.add_argument("--dry-run", action="store_true",
                            help="List the devices that are due; pull nothing.")
        parser.add_argument("--loop", action="store_true",
                            help="Keep running, checking every --interval seconds.")
        parser.add_argument("--interval", type=int, default=60)

    def handle(self, *args, **options):
        if not options["loop"]:
            self._run_once(options)
            return
        # The loop writes a heartbeat per cycle (inside _run_once), so a hung
        # loop goes stale on the monitoring board like a missing cron run.
        stopping = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stopping.set())
        while not stopping.is_set():
            try:
                self._run_once(options)
            except Exception:                       # noqa: BLE001
                logger.exception("device_sync_due cycle failed")
            stopping.wait(max(10, options["interval"]))

    def _organizations(self, slug):
        with no_tenant():
            qs = Organization.objects.filter(status__in=SYNCING_STATUSES)
            if slug:
                qs = qs.filter(slug=slug)
            return list(qs.order_by("slug"))

    def _run_once(self, options):
        if options["dry_run"]:
            return self._run_cycle(options)
        # The heartbeat the monitoring board has always watched for device
        # collection (critical, 10-minute window). One per run, not per
        # device: "the scheduler is alive" is what it reports; a device that
        # fails shows on its own row and in its sync log.
        from monitoring import heartbeat

        with heartbeat.heartbeat("DEVICE_SYNC"):
            return self._run_cycle(options)

    def _run_cycle(self, options):
        totals = {"due": 0, "success": 0, "partial": 0, "failed": 0, "skipped": 0}
        for organization in self._organizations(options["slug"]):
            with tenant_context(organization):
                for device in devices.devices_due():
                    totals["due"] += 1
                    if options["dry_run"]:
                        self.stdout.write(f"  due: {organization.slug}/{device.label} "
                                          f"({device.host}:{device.port}, every "
                                          f"{device.sync_interval_minutes} min)")
                        continue
                    result = devices.run_pull_sync(device, trigger=DeviceSyncLog.Trigger.AUTO)
                    totals[result["status"] if result["status"] in totals else "failed"] += 1
                    line = (f"  {organization.slug}/{device.label}: "
                            f"{result['status']} - {result['message']}")
                    self.stdout.write(self.style.ERROR(line) if result["status"] == "failed"
                                      else line)
        if totals["due"] or options["dry_run"]:
            self.stdout.write(
                f"device_sync_due: {totals['due']} due, {totals['success']} ok, "
                f"{totals['partial']} partial, {totals['failed']} failed, "
                f"{totals['skipped']} skipped")
        return totals
