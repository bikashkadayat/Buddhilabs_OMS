"""Register a biometric terminal from the command line.

    python manage.py register_device --label main-gate --host 192.168.77.201
    python manage.py register_device --label main-gate --host 192.168.77.201 \
        --serial CJXK205060099 --name "Main Gate"

Exists because the fields that matter are easy to get subtly wrong in a form,
and two of them fail *silently* when they are:

* ``device_timezone`` decides which day every punch lands on. Wrong by one zone
  and the whole import is misfiled, with nothing raising.
* ``serial_number`` is the only identity a PUSH terminal presents. Wrong, and
  its posts are refused — which is at least safe, because the device then keeps
  its data and retries.

Re-running with the same ``--label`` updates the row rather than failing, so
this is safe to use to correct a mistake. It never touches ``device_user_id`` on
any mapping: the device is the source of truth for identity and nothing here
can change that.
"""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from biometric.models import BiometricDevice


class Command(BaseCommand):
    help = "Create or update a BiometricDevice row."

    def add_arguments(self, parser):
        parser.add_argument("--label", required=True,
                            help="Short handle, e.g. main-gate. Used by --device.")
        parser.add_argument("--host", required=True,
                            help="Terminal LAN address, e.g. 192.168.77.201.")
        parser.add_argument("--name", help="Human name (defaults to the label).")
        parser.add_argument("--serial", default="",
                            help="Device serial (Menu > Info > Device). Required "
                                 "for PUSH/ADMS terminals.")
        parser.add_argument("--port", type=int, default=4370)
        parser.add_argument("--timezone", default=settings.TIME_ZONE,
                            help=f"The terminal's local zone (default {settings.TIME_ZONE}).")
        parser.add_argument("--location", default="")

    def handle(self, *args, **options):
        try:
            ZoneInfo(options["timezone"])
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise CommandError(
                f"{options['timezone']!r} is not a known timezone. Every "
                f"punch's date depends on this, so it is refused rather than "
                f"defaulted.") from exc

        serial = options["serial"].strip()
        if serial:
            clash = BiometricDevice.objects.filter(
                serial_number=serial).exclude(label=options["label"]).first()
            if clash:
                raise CommandError(
                    f"Serial {serial!r} is already registered to {clash.label!r}. "
                    f"Two devices sharing a serial cannot be told apart, and the "
                    f"PUSH protocol has nothing else to identify them by.")

        device, created = BiometricDevice.objects.update_or_create(
            label=options["label"],
            defaults={
                "name": options["name"] or options["label"],
                "host": options["host"],
                "port": options["port"],
                "serial_number": serial,
                "device_timezone": options["timezone"],
                "location": options["location"],
                "is_active": True,
            },
        )

        write = self.stdout.write
        write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} device {device.label}"))
        write(f"  name      : {device.name}")
        write(f"  host:port : {device.host}:{device.port}")
        write(f"  serial    : {device.serial_number or '(none — PUSH will be refused)'}")
        write(f"  timezone  : {device.device_timezone}")
        write("")

        if not device.serial_number:
            write(self.style.WARNING(
                "  No serial. The SDK pull path (`device_sync`) works without "
                "one, but a PUSH terminal cannot be identified and its posts "
                "will be refused. Read it from Menu > Info > Device."))
        write("  Next:")
        write(f"    python manage.py device_diagnose --device {device.label}")
        write(f"    python manage.py device_sync --device {device.label} --dry-run")
