"""First-contact diagnostic: read the terminal and show exactly what came back.

    python manage.py device_diagnose --host 192.168.77.201
    python manage.py device_diagnose --host 192.168.77.201 --comm-key 0
    python manage.py device_diagnose --host 192.168.77.201 --raw > raw.txt

Run this **before** the import, on a host that can reach the terminal.

WHY IT EXISTS
-------------
The collector's wire code was written against the ZK specification and tested
against a simulator, because the terminal does not answer from the machine it
was written on. That is honest testing but it is not proof: the one thing a
simulator cannot reproduce is a firmware quirk it was not told about.

So this command does the risky part on its own, writes nothing, and prints what
it saw — counts, decoded samples, and on a decode failure the **raw bytes**.
A firmware surprise then costs one round trip instead of an afternoon of
"import failed, try again".

It is strictly read-only. It never disables the terminal, never writes, never
clears, and cannot create a database row.
"""
import json

from django.core.management.base import BaseCommand, CommandError

from biometric import zk_client
from biometric.models import BiometricDevice

SAMPLE = 5


class Command(BaseCommand):
    help = ("Read a biometric terminal and report exactly what it returned. "
            "Writes nothing, to the device or the database.")

    def add_arguments(self, parser):
        parser.add_argument("--host", help="Terminal address, e.g. 192.168.77.201.")
        parser.add_argument("--device", help="Use a registered BiometricDevice's host instead.")
        parser.add_argument("--port", type=int, default=zk_client.DEFAULT_PORT)
        parser.add_argument("--comm-key", type=int, default=0)
        parser.add_argument("--timeout", type=int, default=30)
        parser.add_argument("--raw", action="store_true",
                            help="Dump the raw table bytes as hex. Use when a "
                                 "decode fails and send the output on.")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        host, port = self._target(options)
        report = {"host": host, "port": port, "connected": False,
                  "sizes": {}, "clock": None, "users": {}, "attendance": {},
                  "errors": []}

        try:
            client = zk_client.ZKReadOnlyClient(
                host, port, timeout=options["timeout"], comm_key=options["comm_key"])
            client.connect()
        except zk_client.ZKAuthError as exc:
            raise CommandError(f"{exc}") from exc
        except zk_client.ZKError as exc:
            raise CommandError(
                f"Could not reach {host}:{port} — {exc}\n\n"
                f"This must be run from a host on the terminal's own network. "
                f"A routed path can accept the TCP connection and silently drop "
                f"the payload, which looks identical to a dead device.") from exc

        report["connected"] = True
        try:
            report["sizes"] = client.sizes()
            try:
                report["clock"] = client.device_time().isoformat()
            except zk_client.ZKError as exc:
                report["errors"].append(f"clock: {exc}")

            report["users"] = self._table(
                client, "users", lambda: client.users(),
                lambda: client._read(zk_client.CMD_USERTEMP_RRQ, zk_client.FCT_USER),
                options["raw"])
            report["attendance"] = self._table(
                client, "attendance", lambda: client.attendance(),
                lambda: client._read(zk_client.CMD_ATTLOG_RRQ, zk_client.FCT_ATTLOG),
                options["raw"])
        finally:
            client.disconnect()

        if options["json"]:
            self.stdout.write(json.dumps(report, indent=2, default=str))
            return
        self._render(report)

    def _target(self, options):
        if options["device"]:
            device = BiometricDevice.objects.filter(label=options["device"]).first()
            if device is None:
                raise CommandError(f"No device labelled {options['device']!r}.")
            if not device.host:
                raise CommandError(f"{device.label} has no host recorded.")
            return device.host, device.port
        if not options["host"]:
            raise CommandError("Pass --host <address> or --device <label>.")
        return options["host"], options["port"]

    def _table(self, client, name, decode, fetch_raw, want_raw):
        """Decode a table, and on failure keep the bytes so it can be diagnosed.

        The raw dump is the point: a decode error alone says "this firmware is
        different" without saying how, and the terminal is not somewhere I can
        reach to look.
        """
        result = {"decoded": None, "count": 0, "sample": [], "error": None}
        try:
            records = decode()
        except Exception as exc:  # noqa: BLE001 -- the diagnostic must survive it
            result["error"] = f"{type(exc).__name__}: {exc}"
            try:
                payload = fetch_raw()
                result["raw_bytes"] = len(payload)
                result["raw_head"] = payload[:256].hex()
                self.stderr.write(self.style.ERROR(
                    f"  {name}: decode failed — raw head captured "
                    f"({len(payload)} bytes). Send the hex on."))
            except Exception as inner:  # noqa: BLE001
                result["raw_error"] = f"{type(inner).__name__}: {inner}"
            return result

        result["decoded"] = True
        result["count"] = len(records)
        result["sample"] = records[:SAMPLE]
        if want_raw:
            try:
                payload = fetch_raw()
                result["raw_bytes"] = len(payload)
                result["raw_head"] = payload[:256].hex()
            except Exception as exc:  # noqa: BLE001
                result["raw_error"] = str(exc)
        return result

    # -- output -------------------------------------------------------------
    def _render(self, report):
        write = self.stdout.write
        write(self.style.MIGRATE_HEADING(
            f"Terminal diagnostic — {report['host']}:{report['port']}"))
        write(self.style.SUCCESS("  Connected. Read-only; nothing was written."))

        sizes = report["sizes"]
        if sizes:
            write(f"  Reported by the device: {sizes.get('users', '?')} users, "
                  f"{sizes.get('records', '?')} attendance records")
        else:
            write(self.style.WARNING(
                "  The device did not report its capacity counts. Record width "
                "will be inferred instead of derived — usually fine, but it is "
                "the likeliest source of a decode problem."))

        write(f"  Device clock: {report['clock'] or 'not readable'}")
        write("")

        for name, key in (("Users", "users"), ("Attendance", "attendance")):
            block = report[key]
            write(self.style.MIGRATE_LABEL(f"  {name}"))
            if block.get("error"):
                write(self.style.ERROR(f"    DECODE FAILED: {block['error']}"))
                if block.get("raw_head"):
                    write(f"    raw {block['raw_bytes']} bytes, first 256:")
                    write(f"    {block['raw_head']}")
                write(self.style.WARNING(
                    "    Send that hex on — the record layout can be added "
                    "without guessing."))
                write("")
                continue

            write(f"    decoded {block['count']} record(s)")
            declared = sizes.get("users" if key == "users" else "records")
            if declared and declared != block["count"]:
                write(self.style.WARNING(
                    f"    the device said {declared} but {block['count']} "
                    f"decoded — a mismatch here means the record width is wrong "
                    f"and every field is shifted"))
            for record in block["sample"]:
                write(f"      {record}")
            if block.get("raw_head"):
                write(f"    raw {block['raw_bytes']} bytes, first 256: "
                      f"{block['raw_head'][:96]}…")
            write("")

        users = report["users"]
        if users.get("decoded"):
            ids = [r["employee_id"] for r in users["sample"]]
            write(self.style.SUCCESS(
                f"  Device user IDs read verbatim, e.g. {ids}. "
                f"Nothing here renumbers them."))
        for error in report["errors"]:
            write(self.style.WARNING(f"  ! {error}"))
