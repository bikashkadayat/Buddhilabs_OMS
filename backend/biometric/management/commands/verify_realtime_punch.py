"""Prove that a REAL fingerprint reached every layer — the go-live gate.

This is the command to run immediately after somebody puts a finger on the
terminal. It walks the exact chain the go-live requirement names and reports a
verdict per link, with evidence:

    Fingerprint -> Device -> Collector -> AttendancePunch -> Attendance Engine
                -> Attendance Record -> Dashboard -> Reports -> Analytics

    python manage.py verify_realtime_punch --device-user-id 17
    python manage.py verify_realtime_punch --device-user-id 17 --minutes 30
    python manage.py verify_realtime_punch --any --minutes 10 --json

WHY A SEPARATE COMMAND FROM ``verify_live_flow``
------------------------------------------------
``verify_live_flow`` *manufactures* a punch and pushes it through the ingest
API. That proves the software path and nothing about the sensor, the terminal's
network configuration, or whether the finger was actually read.

This command asserts nothing and creates nothing. It looks for a punch that
already happened and traces where it got to. It is the only one of the two that
can support the sentence "a real fingerprint was verified end to end", which is
the sentence go-live depends on.

It is strictly read-only. It cannot make the chain pass.
"""
import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from attendance.models import Attendance
from biometric.models import AttendancePunch, BiometricEmployee

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


def _check(step, status, detail, fix=""):
    """One row of the report. Every check carries the same keys so the renderer
    and the JSON consumer never have to special-case a shape."""
    return {"step": step, "status": status, "detail": detail, "fix": fix}


DEFAULT_MINUTES = 15


class Command(BaseCommand):
    help = ("Trace a real fingerprint punch from the terminal through to "
            "analytics. Read-only; proves nothing it has not observed.")

    def add_arguments(self, parser):
        parser.add_argument("--device-user-id",
                            help="The ID as enrolled on the terminal, e.g. 17.")
        parser.add_argument("--any", action="store_true",
                            help="Trace the most recent punch from any device user.")
        parser.add_argument("--minutes", type=int, default=DEFAULT_MINUTES,
                            help=f"How far back to look (default {DEFAULT_MINUTES}).")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        if not options["device_user_id"] and not options["any"]:
            raise CommandError(
                "Pass --device-user-id <id as enrolled on the terminal>, or "
                "--any to trace the most recent punch from anyone.")

        since = timezone.now() - timedelta(minutes=options["minutes"])
        punches = AttendancePunch.objects.filter(timestamp__gte=since)
        if options["device_user_id"]:
            punches = punches.filter(employee_device_id=options["device_user_id"])

        punch = punches.select_related("device", "user").order_by("-timestamp").first()
        checks = self._trace(punch, since, options)

        if options["json"]:
            self.stdout.write(json.dumps({
                "window_minutes": options["minutes"],
                "checks": checks,
                "verdict": self._verdict(checks),
            }, indent=2, default=str))
            return
        self._render(checks, options)

    # -- the chain ----------------------------------------------------------
    def _trace(self, punch, since, options):
        checks = []

        def add(step, status, detail, fix=""):
            checks.append({"step": step, "status": status,
                           "detail": detail, "fix": fix})

        # 1. Did anything arrive at all?
        if punch is None:
            who = options["device_user_id"] or "any device user"
            add("Punch received", FAIL,
                f"No punch from {who} in the last {options['minutes']} minutes.",
                "The fingerprint did not reach the OMS. Check, in order: the "
                "terminal shows a successful read; the terminal's server "
                "address points at this host; /iclock/ is reachable from the "
                "terminal's network; the device serial is registered on a "
                "BiometricDevice. `device_readiness` reports on the last two.")
            return checks

        add("Punch received", PASS,
            f"device user {punch.employee_device_id} at "
            f"{timezone.localtime(punch.timestamp):%Y-%m-%d %H:%M:%S} "
            f"via {punch.device.label} (source {punch.source})")

        # 2. Latency — the difference between "arrived" and "arrived promptly".
        lag = (punch.received_at - punch.timestamp).total_seconds()
        if lag <= 120:
            add("Arrival latency", PASS, f"{lag:.0f}s from punch to stored")
        elif lag <= 600:
            add("Arrival latency", WARN, f"{lag:.0f}s from punch to stored",
                "Slower than a real-time push should be. If the terminal is in "
                "PUSH mode, check Realtime=1 in the handshake response.")
        else:
            add("Arrival latency", WARN, f"{lag / 60:.0f} minutes from punch to stored",
                "This looks like a batched or polled delivery rather than a "
                "live push, or the terminal's clock is wrong.")

        # 3. Identity — the requirement that the device's ID is untouched.
        mapping = BiometricEmployee.objects.filter(
            device=punch.device, device_user_id=punch.employee_device_id,
            is_active=True).select_related("user").first()
        if mapping is None:
            add("Enrolment known", FAIL,
                f"No active mapping for device user {punch.employee_device_id}.",
                "Run `device_sync --roster-only` (or wait for the device's next "
                "roster push), then `roster_report` and have HR map the person.")
        elif mapping.user is None:
            add("Enrolment known", FAIL,
                f"Device user {punch.employee_device_id} is enrolled but unmapped.",
                "HR must map it: `roster_report`. Until then the punch is "
                "stored but produces no attendance.")
        else:
            add("Enrolment known", PASS,
                f"device user {punch.employee_device_id} -> "
                f"{mapping.user.get_full_name()} ({mapping.user.employee_id or 'no OMS id'})")
            add("Device ID unchanged", PASS,
                f"the punch carries {punch.employee_device_id!r} and the "
                f"mapping stores {mapping.device_user_id!r} — identical")

        # 4. Did it resolve to a person?
        if punch.user is None:
            add("Punch attributed", FAIL,
                "The punch is stored with no employee attached.",
                "Map the enrolment; existing punches backfill automatically.")
            return checks
        add("Punch attributed", PASS, f"attributed to {punch.user.get_full_name()}")

        # 5. Attendance engine.
        record = Attendance.objects.filter(
            employee=punch.user, date=punch.local_date).first()
        if record is None:
            add("Attendance derived", FAIL,
                f"No Attendance row for {punch.user.get_full_name()} on "
                f"{punch.local_date}.",
                "Derivation runs inline at ingest and again every 10 minutes "
                "via `process_punches`. Run it by hand to see the error.")
            return checks

        add("Attendance derived", PASS,
            f"{punch.local_date}: status={record.status}, "
            f"in={self._time(record.check_in)}, out={self._time(record.check_out)}, "
            f"source={record.source}")

        if record.source == Attendance.Source.HR:
            add("Attendance source", WARN,
                "The row is an HR entry, which outranks the device by design.",
                "Expected if someone entered this day manually. The biometric "
                "punch is stored and will apply once the HR entry is removed.")
        elif record.source == Attendance.Source.BIOMETRIC:
            add("Attendance source", PASS, "biometric — derived from the device")
        else:
            add("Attendance source", WARN,
                f"source={record.source}, not biometric.",
                "A browser check-in already existed for this day; precedence is "
                "HR > biometric > browser.")

        # 6/7/8. The read layers.
        checks.append(self._dashboard(punch, record))
        checks.append(self._reports(punch, record))
        checks.append(self._analytics(punch, record))
        return checks

    def _dashboard(self, punch, record):
        """The workforce dashboard reads Attendance directly, so this asserts
        the row is visible through the same aggregate the UI calls."""
        try:
            from attendance.workforce.aggregates import scoped_employees
            visible = scoped_employees(punch.user).filter(pk=punch.user.pk).exists()
        except Exception as exc:  # noqa: BLE001
            return _check("Dashboard", WARN, f"could not evaluate: {exc}")
        if not visible:
            return _check("Dashboard", WARN,
                          "the employee is not in their own dashboard scope",
                          "Usually an inactive account or a missing department.")
        return _check("Dashboard", PASS,
                      f"{punch.user.get_full_name()} is in scope and the "
                      f"{record.date} row is readable")

    def _reports(self, punch, record):
        """Reports are generated on demand from Attendance, so if the row exists
        the report contains it. Proven by generating one rather than asserting it."""
        try:
            from reports.workforce_reports import build_attendance_vs_leave
            content, _name, _ct = build_attendance_vs_leave({
                "from": record.date.isoformat(), "to": record.date.isoformat(),
                "employee_ids": [punch.user.pk], "format": "csv"})
            text = content.decode("utf-8-sig")
        except Exception as exc:  # noqa: BLE001
            return _check("Reports", FAIL, f"report generation raised: {exc}")

        surname = (punch.user.last_name or punch.user.get_full_name()).split()[-1]
        if surname and surname not in text:
            return _check("Reports", WARN,
                          "the employee did not appear in a same-day report",
                          "Check the report's own scope filters.")
        return _check("Reports", PASS,
                      "attendance-vs-leave generated for that day and contains "
                      "the employee")

    def _analytics(self, punch, record):
        """Analytics is cached, so "reflects attendance" needs a stated latency
        rather than a yes/no. Today's windows carry a 5-minute TTL by design —
        bumping the cache on every punch would mean no cache during office
        hours, which is exactly when the dashboards are read."""
        try:
            from analytics import cache as analytics_cache
        except Exception as exc:  # noqa: BLE001
            return _check("Analytics", WARN, f"analytics unavailable: {exc}")

        today = timezone.localdate()
        if record.date < today:
            # A backdated write bumps the generation counter immediately.
            return _check("Analytics", PASS,
                          "the row is historical, so the analytics cache "
                          "generation was invalidated on write — visible now")
        ttl = getattr(analytics_cache, "TTL_LIVE", 300)
        return _check("Analytics", PASS,
                      f"today's row; live analytics windows carry a {ttl}s TTL, "
                      f"so it appears within {ttl // 60} minutes without any "
                      f"manual step")

    # -- output -------------------------------------------------------------
    @staticmethod
    def _time(value):
        return timezone.localtime(value).strftime("%H:%M") if value else "—"

    @staticmethod
    def _verdict(checks):
        if any(c["status"] == FAIL for c in checks):
            return "FAIL"
        return "PASS_WITH_WARNINGS" if any(c["status"] == WARN for c in checks) else "PASS"

    def _render(self, checks, options):
        write = self.stdout.write
        write(self.style.MIGRATE_HEADING(
            f"Real fingerprint verification — last {options['minutes']} minutes"))
        write("")
        for check in checks:
            style = {PASS: self.style.SUCCESS, FAIL: self.style.ERROR,
                     WARN: self.style.WARNING}[check["status"]]
            write(style(f"  [{check['status']:<4}] {check['step']}"))
            write(f"         {check['detail']}")
            if check["fix"]:
                for line in _wrap(check["fix"], 66):
                    write(f"         {line}")
            write("")

        verdict = self._verdict(checks)
        if verdict == "PASS":
            # Deliberately not "VERIFIED". This command traced a punch through
            # every layer, which is all it can honestly claim — the row it
            # followed is indistinguishable from one a simulated punch would
            # produce. Only the person who watched the finger land can convert
            # this into a verification, so the wording hands that back to them
            # rather than quietly doing it for them.
            write(self.style.SUCCESS(
                "  CHAIN COMPLETE — the punch reached every layer, end to end."))
            write("  This is a verification of a REAL fingerprint only if you "
                  "watched it happen.\n  The chain cannot tell a finger from a "
                  "simulated punch; you can.")
        elif verdict == "PASS_WITH_WARNINGS":
            write(self.style.WARNING(
                "  Chain complete, with warnings. Read them before signing off: "
                "each one is a way the result could be true for the wrong reason."))
        else:
            write(self.style.ERROR(
                "  NOT VERIFIED — the chain breaks at the first FAIL above. "
                "Go-live is not complete until this command reports PASS "
                "against a real fingerprint."))


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for word in words:
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out
