"""Detect devices that have gone quiet and announce the transition.

A terminal that loses power or its network link cannot tell us it is gone, so
offline is inferred from silence: no authenticated contact for longer than the
threshold. That means device.offline lands up to one cron interval late — the
honest cost of not having the collector heartbeat.
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from biometric.models import BiometricDevice
from biometric.services import set_device_status

from monitoring import heartbeat
from tenancy.context import tenant_context

DEFAULT_OFFLINE_MINUTES = 15


def _organizations():
    from tenancy.context import no_tenant
    from tenancy.models import Organization

    with no_tenant():
        return list(Organization.objects.exclude(status=Organization.Status.ARCHIVED))


def _cutoff_for(device, default_cutoff):
    """The silence that means "offline" for THIS device, or None to skip it.

    A pulled device is only heard from when the server pulls it, so silence
    is measured against its own interval: a terminal pulled every 30 minutes
    is not offline after 15. Two missed pulls is the threshold. A pulled
    device set to manual sync is heard from only when someone presses Sync
    now, so silence says nothing about it -- its status is whatever the last
    test or sync found, and this command leaves it alone.
    """
    if not device.host:
        return default_cutoff                  # pushes: silence is the signal
    interval = device.sync_interval_minutes or 0
    if interval <= 0:
        return None
    allowed = timezone.timedelta(minutes=interval * 2 + 1)
    return min(default_cutoff, timezone.now() - allowed)


class Command(BaseCommand):
    help = "Mark quiet biometric devices offline and broadcast status changes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--minutes", type=int, default=None,
            help="Silence before a device counts as offline "
                 f"(default: BIOMETRIC_DEVICE_OFFLINE_MINUTES or {DEFAULT_OFFLINE_MINUTES}).")
        parser.add_argument("--quiet", action="store_true",
                            help="Only report when a status actually changed.")

    def handle(self, *args, **options):
        # Phase 11 (audit finding M5): record a heartbeat so a job that stops
        # running becomes a red tile and an alert instead of silence. On an
        # exception the heartbeat is written FAILED and the error re-raised, so
        # cron still sees a non-zero exit.
        with heartbeat.heartbeat("CHECK_DEVICE_HEALTH"):
            return self._handle(*args, **options)

    def _handle(self, *args, **options):
        minutes = options["minutes"] or getattr(
            settings, "BIOMETRIC_DEVICE_OFFLINE_MINUTES", DEFAULT_OFFLINE_MINUTES)
        cutoff = timezone.now() - timezone.timedelta(minutes=minutes)

        changed = []
        total = 0
        # Every tenant, one at a time: with no tenant bound the scoped manager
        # would only ever see the single-tenant fallback organization.
        for organization in _organizations():
            with tenant_context(organization):
                for device in BiometricDevice.objects.filter(is_active=True):
                    total += 1
                    # A device that has never checked in is not "offline" — it
                    # has never been configured. Reporting it as a failure
                    # every 5 minutes would train people to ignore the alert.
                    if device.last_seen_at is None:
                        continue
                    device_cutoff = _cutoff_for(device, cutoff)
                    if device_cutoff is None:
                        continue
                    online = device.last_seen_at >= device_cutoff
                    # immediate=True: a management command is not inside a
                    # transaction, so there is no commit to wait for.
                    if set_device_status(device, online, immediate=True):
                        changed.append((device, online))

        if changed:
            for device, online in changed:
                state = "ONLINE" if online else "OFFLINE"
                style = self.style.SUCCESS if online else self.style.WARNING
                self.stdout.write(style(
                    f"{device.label} -> {state} (last seen {device.last_seen_at:%Y-%m-%d %H:%M})"))
        elif not options["quiet"]:
            self.stdout.write(f"No status changes across {total} active device(s).")
