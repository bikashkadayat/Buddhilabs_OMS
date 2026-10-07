"""The whole go-live import, in one command.

    python manage.py golive_import --host 192.168.77.201 --dry-run
    python manage.py golive_import --host 192.168.77.201

Run it **on a host on the terminal's network**. It performs every step of the
import in the correct order and finishes with the verification table:

    1. Total imported users        4. Total Attendance rows
    2. Total imported punches      5. Mapped employees
    3. Total AttendancePunch rows  6. Unmapped employees

WHY ONE COMMAND INSTEAD OF SIX
------------------------------
The steps have a required order, and two of the orderings are not obvious:
the roster must land before the punches (or every punch is unmapped), and
derivation must run after mapping (or the dashboards show wrong totals for a
window somebody will screenshot). Leaving that sequence to be retyped at 2am is
how an import goes wrong in a way nobody notices for a fortnight.

It is also **restartable**. Every step is idempotent — the roster upserts, the
punches deduplicate on a database constraint, derivation recomputes from source.
If it dies halfway, run it again.

WHAT IT WILL NOT DO
-------------------
* It will not renumber a device user ID. Nothing in the OMS can; the model
  refuses. The IDs it stores are the ones the terminal sent.
* It will not map anybody to an employee. That is HR's decision and auto-mapping
  on a name match is exactly how one person's attendance ends up filed under
  another's. Unmapped enrolments are reported loudly instead.
* It will not claim a fingerprint was verified. See ``verify_realtime_punch``.
"""
import json
from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from attendance.models import Attendance
from biometric import collector, services, zk_client
from biometric.models import AttendancePunch, BiometricDevice, BiometricEmployee

DEFAULT_FROM = date(2026, 7, 14)


class Command(BaseCommand):
    help = ("Import the biometric roster and attendance history in one pass, "
            "then report the verification table.")

    def add_arguments(self, parser):
        parser.add_argument("--host", help="Terminal address, e.g. 192.168.77.201.")
        parser.add_argument("--device", help="Existing BiometricDevice label.")
        parser.add_argument("--label", default="main-gate",
                            help="Label to register under when --host is new.")
        parser.add_argument("--name", default="Main Gate")
        parser.add_argument("--serial", default="",
                            help="Device serial, for the PUSH path. Optional here.")
        parser.add_argument("--port", type=int, default=4370)
        parser.add_argument("--timezone", default="Asia/Kathmandu")
        parser.add_argument("--comm-key", type=int, default=None)
        parser.add_argument("--timeout", type=int, default=60,
                            help="Seconds. A 10,000-record transfer is not fast.")
        parser.add_argument("--since", default=DEFAULT_FROM.isoformat())
        parser.add_argument("--until")
        parser.add_argument("--dry-run", action="store_true",
                            help="Read the terminal, write nothing, show the counts.")
        parser.add_argument("--driver", choices=["native", "pyzk"],
                            default="native",
                            help="Transport. Switch to pyzk if the built-in "
                                 "reader stumbles on this firmware.")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        # --json must emit only JSON: the progress narrative would otherwise
        # sit in front of it and every parser downstream would choke.
        self.quiet = options["json"]
        since = _day(options["since"])
        until = _day(options["until"]) if options["until"] else None
        dry = options["dry_run"]

        device = self._device(options)
        self._say(f"Device: {device.label} at {device.host}:{device.port} "
                  f"({device.device_timezone})")
        if dry:
            self._warn("DRY RUN — the terminal will be read; nothing will be written.")

        # --- 1. roster --------------------------------------------------
        self._step("1/5  Reading the enrolled users")
        try:
            roster = collector.sync_device(
                device, comm_key=options["comm_key"], timeout=options["timeout"],
                driver=options["driver"], include_punches=False, dry_run=dry)
        except zk_client.ZKAuthError as exc:
            raise CommandError(str(exc)) from exc
        except (zk_client.ZKError, collector.CollectorError) as exc:
            raise CommandError(
                f"Could not read the roster: {exc}\n\n"
                f"Run this from a host on the terminal's own network. A routed "
                f"path can accept the TCP connection and drop the payload, "
                f"which looks exactly like a dead device.") from exc

        block = roster["roster"] or {}
        ids = block.get("device_user_ids", [])
        self._say(f"     {block.get('read', 0)} enrolled users on the terminal")
        self._say(f"     IDs: {_join(ids)}")

        # --- 2. identity --------------------------------------------------
        self._step("2/5  Confirming no device ID was altered")
        if dry:
            self._say("     skipped in a dry run (nothing was stored to compare)")
        else:
            stored = sorted(
                BiometricEmployee.objects.filter(device=device, is_active=True)
                .values_list("device_user_id", flat=True),
                key=services.device_id_sort_key)
            missing = sorted(set(ids) - set(stored), key=services.device_id_sort_key)
            if missing:
                raise CommandError(
                    f"{len(missing)} ID(s) read from the terminal are not in the "
                    f"OMS: {_join(missing)}. Stopping before the import — an ID "
                    f"discrepancy is cheap to fix now and very expensive after "
                    f"attendance hangs off it.")
            self._ok(f"     {len(stored)} IDs stored, identical to the terminal's")
            self._say("     Verify against the device's own menu with: "
                      "verify_biometric_ids --expect <ids>")

        # --- 3. punches ---------------------------------------------------
        self._step(f"3/5  Reading attendance from {since}"
                   f"{f' to {until}' if until else ' to today'}")
        self._say("     A full history transfer takes a while; this is one read.")
        try:
            history = collector.sync_device(
                device, comm_key=options["comm_key"], timeout=options["timeout"],
                driver=options["driver"],
                include_roster=False, since=since, until=until, dry_run=dry,
                source=AttendancePunch.Source.HISTORY,
                # Derive in one sweep afterwards instead of per batch: for a
                # 10,000-punch import, per-batch derivation recomputes the same
                # employee-days over and over.
                derive=False)
        except (zk_client.ZKError, collector.CollectorError) as exc:
            raise CommandError(f"Could not read the attendance log: {exc}") from exc

        punches = history["punches"] or {}
        self._say(f"     {punches.get('read', 0)} records on the terminal, "
                  f"{punches.get('in_window', 0)} inside the window")
        if punches.get("earliest"):
            self._say(f"     range {punches['earliest']} .. {punches['latest']}")
        if not dry:
            self._say(f"     created {punches.get('created', 0)}, "
                      f"duplicate {punches.get('duplicate', 0)} "
                      f"(already imported), unmapped {punches.get('unmapped', 0)}")

        # --- 4. attribute + derive -------------------------------------------
        self._step("4/5  Attributing punches to employees and deriving attendance")
        if dry:
            self._say("     skipped in a dry run")
        else:
            claimed = self._backfill(device, since, until)
            if claimed:
                self._say(f"     {claimed} previously unattributed punch(es) "
                          f"claimed by their now-mapped employee")
            from io import StringIO

            from django.core.management import call_command
            sink = StringIO()
            call_command("process_punches", stdout=sink, stderr=sink)
            self._say("     " + (sink.getvalue().strip().splitlines() or ["done"])[-1])

        # --- 5. the table --------------------------------------------------
        self._step("5/5  Verification")
        summary = self._verify(device, since, until, roster=block, punches=punches,
                               dry_run=dry)

        if options["json"]:
            self.stdout.write(json.dumps(summary, indent=2, default=str))
        else:
            self._table(summary, dry)
        return None

    def _backfill(self, device, since, until):
        """Claim punches that arrived before their enrolment was mapped.

        This step is the difference between an import that works and one that
        looks like it worked. Mapping an enrolment deliberately does **not**
        re-attribute history — ``services.map_employee`` keeps the two apart
        because re-attributing history is the operation that can silently hand
        one person's attendance to another.

        The consequence on a first import is easy to miss: the roster and the
        punches arrive together, so every punch lands unmapped. HR then maps all
        32 people and *nothing changes* — the punches are still attached to
        nobody, the dashboards are still empty, and there is no error anywhere.

        Re-running this command after mapping fixes it. The backfill is bounded
        to the window the operator asked for, which also satisfies the
        recycled-device-ID guard: an unbounded claim is what could hand a
        leaver's attendance to their replacement, and that is refused.
        """
        total = 0
        end = until or timezone.localdate()
        for mapping in BiometricEmployee.objects.filter(
                device=device, is_active=True, user__isnull=False).select_related("user"):
            try:
                total += services.backfill_punches(
                    mapping, date_from=since, date_to=end)
            except Exception as exc:  # noqa: BLE001
                # One employee's backfill failing must not abandon the other 31.
                self._warn(f"backfill failed for device user "
                           f"{mapping.device_user_id}: {exc}")
        return total


    # -- helpers -------------------------------------------------------------
    def _device(self, options):
        if options["device"]:
            device = BiometricDevice.objects.filter(label=options["device"]).first()
            if device is None:
                raise CommandError(f"No device labelled {options['device']!r}.")
            return device

        if not options["host"]:
            existing = list(BiometricDevice.objects.filter(is_active=True)[:2])
            if len(existing) == 1:
                return existing[0]
            raise CommandError(
                "Pass --host <address> (registers the device if needed) or "
                "--device <label>.")

        device, _created = BiometricDevice.objects.update_or_create(
            label=options["label"],
            defaults={"name": options["name"], "host": options["host"],
                      "port": options["port"], "serial_number": options["serial"],
                      "device_timezone": options["timezone"], "is_active": True})
        return device

    def _verify(self, device, since, until, *, roster, punches, dry_run):
        end = until or timezone.localdate()
        stored = AttendancePunch.objects.filter(
            device=device, local_date__gte=since, local_date__lte=end)
        enrolments = BiometricEmployee.objects.filter(device=device, is_active=True)
        mapped = enrolments.filter(user__isnull=False)
        unmapped = enrolments.filter(user__isnull=True)
        users = list(mapped.values_list("user_id", flat=True))
        attendance = Attendance.objects.filter(
            employee_id__in=users, date__gte=since, date__lte=end)

        return {
            "dry_run": dry_run,
            "window": {"from": since.isoformat(), "to": end.isoformat()},
            "users_on_device": roster.get("read", 0),
            "punches_read_from_device": punches.get("read", 0),
            "punches_in_window": punches.get("in_window", 0),
            "attendancepunch_rows": stored.count(),
            "attendance_rows": attendance.count(),
            "attendance_from_device": attendance.filter(
                source=Attendance.Source.BIOMETRIC).count(),
            "enrolments_in_oms": enrolments.count(),
            "mapped_employees": mapped.count(),
            "unmapped_employees": unmapped.count(),
            "unmapped_ids": sorted(
                unmapped.values_list("device_user_id", flat=True),
                key=services.device_id_sort_key),
            "device_user_ids": sorted(
                enrolments.values_list("device_user_id", flat=True),
                key=services.device_id_sort_key),
        }

    def _table(self, s, dry):
        w = self.stdout.write
        s_from = s["window"]["from"]
        w("")
        w(self.style.MIGRATE_HEADING(
            f"  Import verification — {s['window']['from']} to {s['window']['to']}"))
        if dry:
            w(self.style.WARNING("  DRY RUN — no rows were written."))
        w("")
        rows = [
            ("1. Total imported users", s["enrolments_in_oms"]),
            ("2. Total imported punches", s["punches_in_window"]),
            ("3. Total AttendancePunch rows", s["attendancepunch_rows"]),
            ("4. Total Attendance rows", s["attendance_rows"]),
            ("5. Mapped employees", s["mapped_employees"]),
            ("6. Unmapped employees", s["unmapped_employees"]),
        ]
        for label, value in rows:
            w(f"     {label:<34} {value}")
        w("")
        w(f"     Device user IDs in OMS: {_join(s['device_user_ids'])}")
        w("")

        if s["unmapped_employees"]:
            w(self.style.WARNING(
                f"  {s['unmapped_employees']} enrolment(s) are not mapped to an "
                f"employee: {_join(s['unmapped_ids'])}"))
            w("  Their punches are STORED, not lost — but they produce no")
            w("  attendance, so those people read as absent until mapped. Do:")
            w("      python manage.py roster_report --csv mapping.csv")
            w("      # HR maps each one, then RE-RUN THIS COMMAND:")
            w("      python manage.py golive_import --device <label> "
              "--since " + s_from)
            w("")
            w("  Re-running is what attributes the already-imported punches.")
            w("  Mapping alone does not: map_employee deliberately leaves history")
            w("  untouched, so `process_punches` on its own would find nothing to")
            w("  do and everything would still read as absent.")
        elif not dry:
            w(self.style.SUCCESS("  Every enrolment is mapped."))

        if not dry:
            w("")
            w("  Next: have someone punch, watch it happen, then")
            w("      python manage.py verify_realtime_punch --any")
            w("      python manage.py golive_status")

    # -- output shorthand ----------------------------------------------------
    def _step(self, text):
        if not self.quiet:
            self.stdout.write(self.style.MIGRATE_LABEL(f"\n{text}"))

    def _say(self, text):
        if not self.quiet:
            self.stdout.write(text)

    def _ok(self, text):
        if not self.quiet:
            self.stdout.write(self.style.SUCCESS(text))

    def _warn(self, text):
        # Warnings go to stderr rather than being swallowed by --json: a failed
        # backfill is exactly the thing a machine-readable run must not hide.
        (self.stderr if self.quiet else self.stdout).write(
            self.style.WARNING(f"  {text}"))


def _day(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise CommandError(f"{value!r} is not a date in YYYY-MM-DD form") from exc


def _join(ids, limit=40):
    ordered = sorted(ids, key=services.device_id_sort_key)
    if len(ordered) > limit:
        return ", ".join(ordered[:limit]) + f", … (+{len(ordered) - limit} more)"
    return ", ".join(ordered) or "none"
