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

DEFAULT_OFFLINE_MINUTES = 15


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
        for device in BiometricDevice.objects.filter(is_active=True):
            # A device that has never checked in is not "offline" — it has never
            # been configured. Reporting it as a failure every 5 minutes would
            # train people to ignore the alert.
            if device.last_seen_at is None:
                continue
            online = device.last_seen_at >= cutoff
            # immediate=True: a management command is not inside a transaction,
            # so there is no commit to wait for.
            if set_device_status(device, online, immediate=True):
                changed.append((device, online))

        if changed:
            for device, online in changed:
                state = "ONLINE" if online else "OFFLINE"
                style = self.style.SUCCESS if online else self.style.WARNING
                self.stdout.write(style(
                    f"{device.label} -> {state} (last seen {device.last_seen_at:%Y-%m-%d %H:%M})"))
        elif not options["quiet"]:
            total = BiometricDevice.objects.filter(is_active=True).count()
            self.stdout.write(f"No status changes across {total} active device(s).")
