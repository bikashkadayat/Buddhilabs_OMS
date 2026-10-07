"""Go-live readiness audit for the biometric pipeline (Phase 12).

Walks the whole chain the way a punch travels and reports the state of each
link, so "is the device connected?" becomes a checklist with evidence instead of
a yes/no somebody remembers being told:

    fingerprint -> device -> device_sync -> AttendancePunch
                -> derivation -> Attendance -> dashboards

    python manage.py device_readiness                    # everything in the DB
    python manage.py device_readiness --host 192.168.77.201   # + live probe
    python manage.py device_readiness --json             # machine-readable

The live probe is strictly read-only and never disables the terminal — see
``biometric.device_probe``. It is off by default because it needs the terminal
to be reachable from wherever this runs, which is only true on the production
host.

This audits the pipeline's *state*. To test the pipeline itself, run
``device_sync --dry-run`` — it reads the terminal end to end and writes nothing.
"""
import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from biometric import device_probe
from biometric.models import (
    AttendancePunch, BiometricDevice, BiometricEmployee, DeviceSyncLog,
)

OK, WARN, FAIL, INFO = "OK", "WARN", "FAIL", "INFO"

STYLES = {OK: "SUCCESS", WARN: "WARNING", FAIL: "ERROR", INFO: "NOTICE"}


class Command(BaseCommand):
    help = "Audit every link in the biometric pipeline before go-live."

    def add_arguments(self, parser):
        parser.add_argument("--host", help="Probe this device address (read-only).")
        parser.add_argument("--port", type=int, default=4370)
        parser.add_argument("--timeout", type=int, default=6)
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        checks = []
        checks += self._devices()
        checks += self._credentials()
        checks += self._roster()
        checks += self._ingest()
        checks += self._derivation()
        checks += self._dashboards()

        probe = None
        if options["host"]:
            probe = device_probe.full_probe(
                options["host"], options["port"], options["timeout"])
            checks.append(self._probe_check(probe))

        if options["as_json"]:
            self.stdout.write(json.dumps(
                {"checks": checks, "probe": probe,
                 "generated_at": timezone.localtime().isoformat()},
                indent=2, default=str))
            return

        self._render(checks, probe)

    # -- links in the chain ------------------------------------------------
    def _devices(self):
        total = BiometricDevice.objects.count()
        active = BiometricDevice.objects.filter(is_active=True)
        online = active.filter(connection_status=BiometricDevice.Status.ONLINE).count()

        if total == 0:
            return [self._check(
                "Device registered", FAIL, "No BiometricDevice rows exist.",
                "Register the terminal in Django admin and issue its API key. "
                "Nothing downstream can work until this exists.")]

        rows = [self._check(
            "Device registered", OK,
            f"{total} device(s), {active.count()} active, {online} online.")]

        for device in active:
            age = ("never" if not device.last_seen_at
                   else f"{int((timezone.now() - device.last_seen_at).total_seconds() // 60)} min ago")
            state = OK if device.connection_status == BiometricDevice.Status.ONLINE else WARN
            rows.append(self._check(
                f"  {device.name} ({device.host or 'no host recorded'})", state,
                f"status={device.connection_status}, last seen {age}, "
                f"pending={device.pending_punches}, "
                f"batches ok/failed={device.successful_batches}/{device.failed_batches}"))
        return rows

    def _credentials(self):
        active = BiometricDevice.objects.filter(is_active=True)
        without_key = [d.name for d in active if not d.api_key_hash]
        if not active:
            return []
        if without_key:
            # WARN, not FAIL: the API key authenticates the *push* path
            # (/api/v1/biometric/bulk-sync/). A deployment that collects with
            # `device_sync` never uses it, and failing go-live over a credential
            # nothing needs would be a false blocker.
            return [self._check(
                "Device API keys", WARN,
                f"No key set for: {', '.join(without_key)}.",
                "Only needed if something POSTs to the ingest API. `device_sync` "
                "does not — it calls ingest in-process. Issue a key in admin if "
                "you need the push path; it is shown once and stored hashed, so "
                "record it immediately.")]
        return [self._check("Device API keys", OK,
                            f"All {active.count()} active device(s) have a key.")]

    def _roster(self):
        total = BiometricEmployee.objects.filter(is_active=True).count()
        mapped = BiometricEmployee.objects.filter(
            is_active=True, user__isnull=False).count()
        unmapped = total - mapped

        if total == 0:
            return [self._check(
                "Roster imported", FAIL, "No enrolments have been synced.",
                "Run `device_sync --roster-only`. Until the roster exists "
                "there is nobody to map punches to.")]
        if unmapped:
            return [self._check(
                "Employee mapping", WARN,
                f"{mapped}/{total} mapped, {unmapped} unmapped.",
                "Unmapped enrolments produce punches that never become "
                "attendance. Run `roster_report` and have HR map them.")]
        return [self._check("Employee mapping", OK,
                            f"All {total} enrolments are mapped.")]

    def _ingest(self):
        total = AttendancePunch.objects.count()
        if total == 0:
            return [self._check(
                "Punches received", FAIL, "No punches have ever been ingested.",
                "Nothing has arrived. Run `device_sync --dry-run` — it reads the "
                "terminal end to end and writes nothing, so it isolates whether "
                "the problem is the device or this side of it.")]

        latest = AttendancePunch.objects.order_by("-timestamp").values_list(
            "timestamp", flat=True).first()
        age_hours = (timezone.now() - latest).total_seconds() / 3600
        state = OK if age_hours < 24 else WARN
        rows = [self._check(
            "Punches received", state,
            f"{total} punches, most recent {age_hours:.1f}h ago.")]

        since = timezone.now() - timedelta(hours=24)
        logs = DeviceSyncLog.objects.filter(started_at__gte=since)
        failed = logs.filter(status=DeviceSyncLog.Status.FAILED).count()
        if logs.exists():
            rows.append(self._check(
                "Ingest batches (24h)", WARN if failed else OK,
                f"{logs.count()} batches, {failed} failed."))
        else:
            rows.append(self._check(
                "Ingest batches (24h)", WARN, "No batches in the last 24 hours.",
                "Normal outside working hours; investigate if it is mid-shift."))
        return rows

    def _derivation(self):
        from attendance.models import Attendance

        pending = AttendancePunch.objects.filter(is_processed=False)
        awaiting_derivation = pending.filter(user__isnull=False).count()
        awaiting_mapping = pending.filter(user__isnull=True).count()

        rows = []
        if awaiting_derivation:
            rows.append(self._check(
                "Derivation queue", WARN if awaiting_derivation < 500 else FAIL,
                f"{awaiting_derivation} mapped punches not yet derived.",
                "Run `process_punches`. If it does not clear, derivation is failing."))
        else:
            rows.append(self._check("Derivation queue", OK, "Nothing pending."))

        if awaiting_mapping:
            rows.append(self._check(
                "Punches from unmapped users", WARN,
                f"{awaiting_mapping} punches belong to unmapped enrolments.",
                "These are blocked on an HR mapping decision, not on the system. "
                "They derive automatically once mapped."))

        derived = Attendance.objects.filter(
            source=Attendance.Source.BIOMETRIC).count()
        rows.append(self._check(
            "Attendance from device", OK if derived else WARN,
            f"{derived} attendance row(s) with source=biometric."))
        return rows

    def _dashboards(self):
        """The last link: can the read side actually answer?

        Checked by calling the aggregate the dashboards call, because a
        dashboard that 500s on real data is indistinguishable from a healthy one
        until somebody opens it.
        """
        try:
            from attendance.dashboard_views import scoped_employees
            from attendance.workforce import aggregates
            from users.models import User

            admin = User.objects.filter(role=User.Roles.ADMIN, is_active=True).first()
            if admin is None:
                return [self._check(
                    "Dashboard read path", WARN, "No admin user to evaluate with.")]
            employees = list(scoped_employees(admin))
            counts, _per, _holiday = aggregates.day_counts(
                employees, timezone.localdate())
            return [self._check(
                "Dashboard read path", OK,
                f"Resolved today for {len(employees)} employees "
                f"(present={counts.get('present', 0)}, late={counts.get('late', 0)}).")]
        except Exception as exc:  # noqa: BLE001
            return [self._check("Dashboard read path", FAIL,
                                f"{type(exc).__name__}: {exc}",
                                "The dashboards cannot render. Fix before go-live.")]

    def _probe_check(self, probe):
        verdict = probe["verdict"]
        state = (OK if verdict.startswith("READY")
                 else WARN if verdict.startswith("REACHABLE")
                 else FAIL)
        return self._check(f"Device probe {probe['host']}:{probe['port']}", state,
                           verdict)

    # -- output ------------------------------------------------------------
    @staticmethod
    def _check(name, state, detail, action=None):
        return {"check": name, "state": state, "detail": detail, "action": action}

    def _render(self, checks, probe):
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\nBiometric pipeline readiness\n"))
        for entry in checks:
            style = getattr(self.style, STYLES[entry["state"]], self.style.NOTICE)
            self.stdout.write(f"  [{style(entry['state']):<4}] {entry['check']}")
            self.stdout.write(f"         {entry['detail']}")
            if entry["action"]:
                self.stdout.write(f"         -> {entry['action']}")

        if probe:
            self.stdout.write(self.style.MIGRATE_HEADING("\nLive device probe\n"))
            for transport in ("tcp", "udp"):
                data = probe[transport]
                self.stdout.write(f"  {transport.upper():4s} "
                                  f"reply={data.get('protocol_reply') or '—'} "
                                  f"error={data.get('error') or 'none'}")
            control = probe["control"]
            self.stdout.write(f"  control {control.get('host', '—')}: "
                              f"{control.get('warning') or control.get('note', '')}")

        failures = sum(1 for c in checks if c["state"] == FAIL)
        warnings = sum(1 for c in checks if c["state"] == WARN)
        self.stdout.write("")
        if failures:
            self.stdout.write(self.style.ERROR(
                f"NOT READY — {failures} blocking issue(s), {warnings} warning(s)."))
        elif warnings:
            self.stdout.write(self.style.WARNING(
                f"READY WITH WARNINGS — {warnings} item(s) to review."))
        else:
            self.stdout.write(self.style.SUCCESS("READY — every link verified."))
