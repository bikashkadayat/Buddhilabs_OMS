"""Run the device sync continuously, in the foreground.

This is the recurring collector for a machine that is not running cron — a
local workstation, or any host where "start the OMS" should also start
attendance collection. It is a supervisor around exactly the same work
``device_sync`` does: one call to ``collector.sync_device`` per cycle, through
the same ingest path, with the same dedup and the same heartbeat.

WHY A LOOP AND NOT JUST CRON
----------------------------
Cron's floor is one minute, and each tick pays the full Django start-up cost
before it can talk to the terminal. Holding the process open instead makes a
30-60 second cycle practical, which is the difference between a punch appearing
on the dashboard while the employee is still walking to their desk and
appearing several minutes later.

Cron remains perfectly valid and ``deploy/crontab`` still uses it — this does
not replace that, it is an alternative for hosts without one. **Do not run both
against the same terminal.** Two collectors mean two concurrent TCP sessions
competing for a connection pool that is only a few slots deep, and the loser
gets a timeout rather than a queue.

FAILURE POLICY
--------------
A sync failure is expected and survivable: the terminal reboots, the LAN drops,
someone unplugs it. The device keeps its whole log, so a missed cycle is
collected by the next one and nothing is lost. That is why a failed cycle backs
off and retries rather than exiting — an exiting collector would need a
supervisor to restart it, and on a workstation there usually is not one.

An error that will not fix itself by waiting — no such device, a bad timezone
on the row — exits immediately instead, because retrying a misconfiguration
every 60 seconds forever just fills the log.
"""
import logging
import signal
import time

from django.core.management.base import BaseCommand, CommandError

from biometric import collector, locking, zk_client
from biometric.models import AttendancePunch, BiometricDevice

from monitoring import heartbeat

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = 60
# The terminal is on the LAN and holds its whole log, so a fast retry is cheap
# and a slow one costs visible attendance lag. Backoff is capped well below a
# cron cycle so a recovered device is picked up promptly.
BACKOFF_START = 15
BACKOFF_MAX = 300


class Command(BaseCommand):
    help = ("Continuously pull punches from a biometric terminal. Read-only on "
            "the device. Foreground process — stop with Ctrl-C.")

    def add_arguments(self, parser):
        parser.add_argument("--device", help="BiometricDevice label. Optional when only one is active.")
        parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                            help=f"Seconds between cycles (default {DEFAULT_INTERVAL}).")
        parser.add_argument("--timeout", type=int, default=None,
                            help="Per-connection device timeout in seconds.")
        parser.add_argument("--host", help="Override the terminal's address for this run.")
        parser.add_argument("--comm-key", type=int, default=None)
        parser.add_argument("--driver", choices=["native", "pyzk"], default="native")
        parser.add_argument("--roster-every", type=int, default=10,
                            help="Read the enrolled-user roster every Nth cycle "
                                 "(default 10). The roster changes when HR "
                                 "enrols someone, not every minute, and reading "
                                 "it is a second full table transfer.")
        parser.add_argument("--once", action="store_true",
                            help="Run a single cycle and exit — for testing the "
                                 "configuration without leaving a process up.")

    def handle(self, *args, **options):
        if options["interval"] < 5:
            # Below this the terminal spends more time serving us than serving
            # the people trying to punch on it.
            raise CommandError("--interval must be at least 5 seconds.")

        device = self._resolve_device(options["device"])
        stopping = self._install_signal_handlers()

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Collecting from {device.label} ({options['host'] or device.host}) "
            f"every {options['interval']}s. Ctrl-C to stop."))

        cycle = 0
        backoff = BACKOFF_START
        while not stopping.is_set():
            cycle += 1
            # The roster is read on the first cycle so a fresh start always has
            # current enrolments, then only occasionally after that.
            include_roster = (cycle == 1
                              or (options["roster_every"] > 0
                                  and cycle % options["roster_every"] == 0))
            try:
                summary = self._cycle(device, options, include_roster=include_roster)
            except locking.CollectorBusy:
                # Cron, or a manual sync, got there first. Nothing to do and
                # nothing wrong — wait for the normal interval, not the error
                # backoff, because the terminal is fine.
                self.stdout.write(f"  cycle {cycle}: another collector holds the "
                                  f"device — skipping")
                stopping.wait(options["interval"])
                continue
            except (zk_client.ZKError, collector.CollectorError) as exc:
                # Transient by assumption: the device or the network is having a
                # moment. Report, back off, keep going.
                self.stderr.write(self.style.WARNING(
                    f"  cycle {cycle}: {exc} — retrying in {backoff}s"))
                logger.warning("device_sync_loop cycle failed: %s", exc)
                if options["once"] or stopping.wait(backoff):
                    break
                backoff = min(BACKOFF_MAX, backoff * 2)
                continue

            backoff = BACKOFF_START
            self._report(cycle, summary)

            if options["once"]:
                break
            stopping.wait(options["interval"])

        self.stdout.write(self.style.SUCCESS("Collector stopped."))

    # -- one cycle ----------------------------------------------------------
    def _cycle(self, device, options, *, include_roster):
        """One sync, wrapped in the heartbeat the monitoring dashboard reads.

        The device row is re-fetched every cycle rather than held: `last_punch_at`
        is the resume cursor and it is advanced by ingest, so a stale in-memory
        copy would keep asking for a window that has already been collected.
        """
        device.refresh_from_db()
        # Lock outside the heartbeat: a skipped cycle is not a run, and writing
        # a heartbeat for it would report the collector as healthy on the
        # strength of work it did not do.
        with locking.device_lock(device.label), heartbeat.heartbeat("DEVICE_SYNC"):
            return collector.sync_device(
                device,
                host=options["host"],
                comm_key=options["comm_key"],
                include_roster=include_roster,
                include_punches=True,
                source=AttendancePunch.Source.HISTORY,
                timeout=options["timeout"],
                driver=options["driver"],
            )

    # -- setup --------------------------------------------------------------
    def _resolve_device(self, label):
        if label:
            try:
                return BiometricDevice.objects.get(label=label)
            except BiometricDevice.DoesNotExist as exc:
                known = ", ".join(BiometricDevice.objects.values_list("label", flat=True)) or "none"
                raise CommandError(
                    f"No device with label {label!r}. Known labels: {known}.") from exc

        active = list(BiometricDevice.objects.filter(is_active=True)[:2])
        if not active:
            raise CommandError(
                "No active BiometricDevice exists. Register one first with "
                "`manage.py register_device`.")
        if len(active) > 1:
            raise CommandError(
                "More than one device is active; pass --device <label>. Guessing "
                "would risk filing one terminal's punches under another's.")
        return active[0]

    def _install_signal_handlers(self):
        """Stop at the end of the current cycle, not in the middle of one.

        An Event rather than a flag so the inter-cycle wait is interruptible:
        with `time.sleep` a Ctrl-C during a 60-second gap would hang the
        terminal for up to a minute before the process noticed.
        """
        import threading

        stopping = threading.Event()

        def stop(signum, _frame):
            self.stdout.write("\n  stop requested — finishing the current cycle…")
            stopping.set()

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        return stopping

    # -- output -------------------------------------------------------------
    def _report(self, cycle, summary):
        punches = summary.get("punches") or {}
        created = punches.get("created", 0)
        stamp = time.strftime("%H:%M:%S")

        line = (f"  [{stamp}] cycle {cycle}: read {punches.get('read', 0)}, "
                f"in window {punches.get('in_window', 0)}, "
                f"new {created}, duplicate {punches.get('duplicate', 0)}")
        # New punches are the whole point, so they are the one thing that gets
        # colour — an operator watching this should be able to see a real
        # fingerprint land without reading every line.
        self.stdout.write(self.style.SUCCESS(line) if created else line)

        if punches.get("unmapped"):
            self.stdout.write(self.style.WARNING(
                f"    {punches['unmapped']} punch(es) have no employee mapping — "
                f"stored, but not attributed. Resolve in the HR mapping queue."))
        for warning in summary.get("warnings", []):
            self.stdout.write(self.style.WARNING(f"    ! {warning}"))
