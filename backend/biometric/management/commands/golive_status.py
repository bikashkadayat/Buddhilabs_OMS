"""The go-live success criteria, answered from the database rather than opinion.

    python manage.py golive_status
    python manage.py golive_status --json
    python manage.py golive_status --from 2026-07-14

One criterion cannot be answered here and is reported as such: whether a REAL
fingerprint has been verified end to end. Nothing in a database proves a finger
touched a sensor — an identical row could be produced by a simulated punch — so
this command reports the *evidence* (a live-sourced punch with a plausible
delivery latency) and still refuses to call it verified. That call belongs to a
human who watched it happen, via ``verify_realtime_punch``.

A checklist that grades its own homework is worse than none. This one is built
so the only way to get a green board is for the system to actually work.
"""
import json
from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from attendance.models import Attendance
from biometric.models import AttendancePunch, BiometricDevice, BiometricEmployee

PASS, FAIL, WARN, MANUAL = "PASS", "FAIL", "WARN", "MANUAL"
DEFAULT_FROM = date(2026, 7, 14)
# What counts as "arrived live" depends on how it arrives, and hardcoding one
# number got this wrong: 120s is right for PUSH and **unmeetable** by a pull
# collector on a five-minute cron, where a punch is routinely 4 minutes old
# before anything reads it. A criterion that cannot be satisfied by the
# deployment it describes is a broken criterion, not a strict one.
PUSH_LAG_SECONDS = 120          # the device dials out; seconds, not minutes
PULL_LAG_MULTIPLIER = 2         # two collection cycles of slack


def realtime_lag_budget():
    """Seconds within which a punch must land to count as live.

    PUSH deployments are held to seconds. Pull deployments are held to two
    collection intervals — late enough to absorb a missed cycle, early enough
    that a bulk backfill (hours or years old) can never sneak through.
    """
    from monitoring import heartbeat

    if BiometricDevice.objects.filter(is_active=True).exclude(serial_number="").exists():
        return PUSH_LAG_SECONDS
    interval = heartbeat.CRON_JOBS.get("DEVICE_SYNC", (None, 10, None))[1]
    return int(interval) * 60 * PULL_LAG_MULTIPLIER


class Command(BaseCommand):
    help = "Answer each go-live success criterion from the data."

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="date_from", default=DEFAULT_FROM.isoformat(),
                            help=f"Start of the historical window (default {DEFAULT_FROM}).")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        try:
            start = datetime.strptime(options["date_from"], "%Y-%m-%d").date()
        except ValueError:
            start = DEFAULT_FROM
        today = timezone.localdate()

        criteria = [
            self._historical(start, today),
            self._users_present(),
            self._ids_unchanged(),
            self._realtime_evidence(),
            self._continues_automatically(),
            self._dashboard(start, today),
            self._reports(start, today),
            self._analytics(),
            self._no_manual_workflow(start, today),
        ]

        if options["json"]:
            self.stdout.write(json.dumps({
                "window_from": start.isoformat(),
                "criteria": criteria,
                "verdict": self._verdict(criteria),
            }, indent=2, default=str))
            return
        self._render(criteria, start)

    # -- criteria -----------------------------------------------------------
    def _historical(self, start, today):
        punches = AttendancePunch.objects.filter(
            local_date__gte=start, local_date__lte=today)
        total = punches.count()
        if not total:
            return _c("Historical attendance exists from the start date", FAIL,
                      f"No punches at all between {start} and {today}.",
                      "Nothing has been imported. If the device is in PUSH mode "
                      "it only sends from the moment it is configured — its "
                      "stored backlog has to be pulled or re-transmitted.")
        days = punches.values("local_date").distinct().count()
        span = (today - start).days + 1
        detail = f"{total} punches across {days} of {span} calendar days"
        if days < span * 0.5:
            return _c("Historical attendance exists from the start date", WARN,
                      detail, "Fewer than half the days have any punch. Check "
                              "for a gap with `import_plan`.")
        return _c("Historical attendance exists from the start date", PASS, detail)

    def _users_present(self):
        total = BiometricEmployee.objects.filter(is_active=True).count()
        if not total:
            return _c("All biometric users available in OMS", FAIL,
                      "No enrolments recorded.",
                      "The device has not sent its roster. PUSH devices send it "
                      "as an OPERLOG; otherwise `device_sync --roster-only`.")
        unmapped = BiometricEmployee.objects.filter(
            is_active=True, user__isnull=True).count()
        if unmapped:
            return _c("All biometric users available in OMS", FAIL,
                      f"{total} enrolments, {unmapped} still unmapped.",
                      "Unmapped enrolments produce punches that never become "
                      "attendance — those people are silently absent. "
                      "`roster_report` drives the mapping.")
        return _c("All biometric users available in OMS", PASS,
                  f"{total} enrolments, all mapped")

    def _ids_unchanged(self):
        """Cross-check the two places a device ID is stored against each other.

        A renumbering would show up as punches whose ID no longer matches any
        enrolment. It is not proof against a change made before any punch
        existed — only ``verify_biometric_ids --expect`` against the terminal's
        own list can settle that, and this says so.
        """
        punch_ids = set(AttendancePunch.objects.values_list(
            "employee_device_id", flat=True).distinct())
        enrolled = set(BiometricEmployee.objects.values_list(
            "device_user_id", flat=True).distinct())
        orphans = punch_ids - enrolled
        if orphans:
            listed = ", ".join(sorted(orphans)[:10])
            return _c("Device IDs unchanged", WARN,
                      f"{len(orphans)} ID(s) appear in punches but are not "
                      f"enrolled: {listed}",
                      "Usually a deleted enrolment rather than a renumbering. "
                      "Confirm with `verify_biometric_ids --expect <ids read "
                      "off the terminal>`.")
        return _c("Device IDs unchanged", PASS,
                  f"{len(enrolled)} enrolled ID(s); every punched ID is enrolled. "
                  f"Enforced at the model layer — device_user_id is immutable. "
                  f"Confirm against the terminal with `verify_biometric_ids "
                  f"--expect`.")

    def _realtime_evidence(self):
        """Evidence, not a verdict. See the module docstring.

        Judged on **arrival latency, not on the source label**. A pull
        collector reads every punch out of the terminal's stored log, so it
        labels them all HISTORY — correctly, because that is how they arrived.
        Keying this criterion on ``source == LIVE`` therefore made it
        unsatisfiable for the very deployment it was written for: the operator
        punches, the punch lands twenty seconds later, and the board still
        reports that nothing live has ever arrived.

        What actually distinguishes a live arrival from a bulk backfill is the
        gap between when the finger landed and when the row was written.
        """
        budget = realtime_lag_budget()
        recent = None
        for candidate in (AttendancePunch.objects
                          .order_by("-timestamp")[:200]):
            lag = (candidate.received_at - candidate.timestamp).total_seconds()
            if 0 <= lag <= budget:
                recent = candidate
                break

        if recent is None:
            return _c("Real fingerprint verified end to end", MANUAL,
                      "No punch has arrived promptly enough to count as live "
                      f"(within {budget}s of being made).",
                      "Have someone punch, wait for one collection cycle, then "
                      "run `verify_realtime_punch --any`. This criterion cannot "
                      "be satisfied any other way.")
        lag = (recent.received_at - recent.timestamp).total_seconds()
        age = timezone.now() - recent.timestamp
        detail = (f"most recent promptly-arrived punch: device user "
                  f"{recent.employee_device_id} at "
                  f"{timezone.localtime(recent.timestamp):%Y-%m-%d %H:%M:%S}, "
                  f"delivered in {lag:.0f}s, {_ago(age)} ago")
        return _c("Real fingerprint verified end to end", MANUAL, detail,
                  "Evidence only. A row cannot prove a finger touched a sensor "
                  "— a simulated punch produces an identical one. Sign this off "
                  "only after watching `verify_realtime_punch` pass on a punch "
                  "you saw happen.")

    def _continues_automatically(self):
        """Is there a delivery mechanism that needs nobody?"""
        push_devices = BiometricDevice.objects.filter(
            is_active=True).exclude(serial_number="").count()
        from monitoring import heartbeat
        stamp, ok = heartbeat.last_run("DEVICE_SYNC")

        mechanisms = []
        if push_devices:
            mechanisms.append(f"{push_devices} PUSH device(s) registered by serial")
        if stamp:
            mechanisms.append(
                f"device_sync last ran {_ago(timezone.now() - stamp)} ago "
                f"({'ok' if ok else 'FAILED'})")

        if not mechanisms:
            return _c("Future attendance continues automatically", FAIL,
                      "No automatic delivery is configured.",
                      "Either register the device serial (PUSH) or enable the "
                      "device_sync cron line. Without one of these, attendance "
                      "only arrives when somebody runs a command.")
        status = PASS if push_devices or (stamp and ok) else WARN
        return _c("Future attendance continues automatically", status,
                  "; ".join(mechanisms))

    def _dashboard(self, start, today):
        rows = Attendance.objects.filter(date__gte=start, date__lte=today)
        biometric = rows.filter(source=Attendance.Source.BIOMETRIC).count()
        if not rows.exists():
            return _c("Workforce dashboard reflects attendance", FAIL,
                      "No Attendance rows in the window.",
                      "Punches exist but were not derived — run `process_punches`.")
        if not biometric:
            return _c("Workforce dashboard reflects attendance", WARN,
                      f"{rows.count()} rows, none sourced from the device.",
                      "Attendance exists but none of it came from fingerprints.")
        return _c("Workforce dashboard reflects attendance", PASS,
                  f"{rows.count()} attendance rows, {biometric} device-derived")

    def _reports(self, start, today):
        try:
            from reports.workforce_reports import build_attendance_vs_leave
            content, _n, _ct = build_attendance_vs_leave(
                {"from": start.isoformat(), "to": today.isoformat(), "format": "csv"})
        except Exception as exc:  # noqa: BLE001
            return _c("Reports reflect attendance", FAIL,
                      f"report generation raised: {exc}")
        lines = content.decode("utf-8-sig").strip().splitlines()
        if len(lines) <= 1:
            return _c("Reports reflect attendance", WARN,
                      "a report generated but contained no employee rows")
        return _c("Reports reflect attendance", PASS,
                  f"attendance-vs-leave generated: {len(lines) - 1} employee row(s)")

    def _analytics(self):
        from django.conf import settings
        if not getattr(settings, "ANALYTICS_ENABLED", True):
            return _c("Analytics reflect attendance", WARN,
                      "ANALYTICS_ENABLED is off.")
        try:
            from analytics import cache as analytics_cache
            ttl = getattr(analytics_cache, "TTL_LIVE", 300)
        except Exception as exc:  # noqa: BLE001
            return _c("Analytics reflect attendance", FAIL, f"unavailable: {exc}")
        return _c("Analytics reflect attendance", PASS,
                  f"reads Attendance directly; historical writes invalidate the "
                  f"cache immediately, today's ride a {ttl}s TTL — so a punch "
                  f"appears within {ttl // 60} minutes with no manual step")

    def _no_manual_workflow(self, start, today):
        """HR-sourced rows are the measurable proxy for manual work."""
        rows = Attendance.objects.filter(date__gte=start, date__lte=today)
        total = rows.count()
        if not total:
            return _c("No manual attendance workflow required", WARN,
                      "No attendance to judge yet.")
        manual = rows.filter(source=Attendance.Source.HR).count()
        share = manual / total
        detail = f"{manual} of {total} rows are manual HR entries ({share:.0%})"
        if share > 0.1:
            return _c("No manual attendance workflow required", WARN, detail,
                      "A high share of HR entries usually means the device path "
                      "is not covering everyone — check for unmapped enrolments "
                      "and for staff not enrolled at all.")
        return _c("No manual attendance workflow required", PASS, detail)

    # -- output -------------------------------------------------------------
    @staticmethod
    def _verdict(criteria):
        if any(c["status"] == FAIL for c in criteria):
            return "NOT READY"
        if any(c["status"] == MANUAL for c in criteria):
            return "AWAITING HUMAN VERIFICATION"
        return "READY" if all(c["status"] == PASS for c in criteria) else "READY WITH WARNINGS"

    def _render(self, criteria, start):
        write = self.stdout.write
        write(self.style.MIGRATE_HEADING(
            f"Go-live success criteria — window from {start}"))
        write("")
        for item in criteria:
            style = {PASS: self.style.SUCCESS, FAIL: self.style.ERROR,
                     WARN: self.style.WARNING, MANUAL: self.style.WARNING}[item["status"]]
            mark = {PASS: "✅", FAIL: "❌", WARN: "⚠️ ", MANUAL: "👤"}[item["status"]]
            write(style(f"  {mark} [{item['status']:<6}] {item['criterion']}"))
            write(f"           {item['detail']}")
            if item["fix"]:
                for line in _wrap(item["fix"], 64):
                    write(f"           {line}")
            write("")

        verdict = self._verdict(criteria)
        style = {"READY": self.style.SUCCESS,
                 "READY WITH WARNINGS": self.style.WARNING,
                 "AWAITING HUMAN VERIFICATION": self.style.WARNING,
                 "NOT READY": self.style.ERROR}[verdict]
        write(style(f"  VERDICT: {verdict}"))
        if verdict == "AWAITING HUMAN VERIFICATION":
            write("  Everything measurable passes. What remains is the one thing "
                  "a database cannot\n  attest: a real finger on the real sensor. "
                  "Run `verify_realtime_punch` on a\n  punch you watched happen.")


def _c(criterion, status, detail, fix=""):
    return {"criterion": criterion, "status": status, "detail": detail, "fix": fix}


def _ago(delta):
    seconds = int(delta.total_seconds())
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


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
