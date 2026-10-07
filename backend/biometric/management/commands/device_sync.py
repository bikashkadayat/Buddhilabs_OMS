"""Pull the roster and attendance log straight off the terminal.

This is the OMS's collector. It replaces the external pusher the Phase 6 ingest
API was written for, because Phase 12 requires the device to be part of this
system rather than fed by a separate one. The ingest API is unchanged and still
works — this is a second door into the same room.

TWO JOBS, ONE COMMAND
---------------------
* ``--since``/``--until``: the historical import, run by hand, once.
* no window: the recurring sync, run by cron every few minutes. The terminal
  keeps its whole log, so each run re-reads it and ingest discards what it has
  already stored. That is why re-reading is cheap and why a missed cron cycle
  needs no catch-up logic — the next one collects it.

The recurring form writes a heartbeat; the one-off historical form does not,
because an operator running a backfill at 2am should not reset the clock on
"has the sync cron been running".
"""
import json

from django.core.management.base import BaseCommand, CommandError

from biometric import collector, locking, services, zk_client
from biometric.models import AttendancePunch, BiometricDevice

from monitoring import heartbeat


class Command(BaseCommand):
    help = ("Read the roster and attendance log from a biometric terminal and "
            "ingest them. Read-only on the device.")

    def add_arguments(self, parser):
        parser.add_argument("--device", help="BiometricDevice label. Optional when only one is active.")
        parser.add_argument("--host", help="Override the terminal's address for this run.")
        parser.add_argument("--comm-key", type=int, default=None,
                            help="Comm key, if one is set on the terminal.")
        parser.add_argument("--since", help="First local date to import (YYYY-MM-DD).")
        parser.add_argument("--until", help="Last local date to import (YYYY-MM-DD).")
        parser.add_argument("--roster-only", action="store_true")
        parser.add_argument("--punches-only", action="store_true")
        parser.add_argument("--source", choices=[c[0] for c in AttendancePunch.Source.choices],
                            help="Punch source label (default HISTORY).")
        parser.add_argument("--no-derive", action="store_true",
                            help="Skip attendance derivation — for a very large "
                                 "backfill, followed by `rederive_attendance`.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Read everything, write nothing, report the counts.")
        parser.add_argument("--timeout", type=int, default=None)
        parser.add_argument("--driver", choices=["native", "pyzk"],
                            default="native",
                            help="Transport. Switch to pyzk if the built-in "
                                 "reader stumbles on this firmware.")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        historical = bool(options["since"] or options["until"])
        if historical:
            return self._handle(*args, **options)
        with heartbeat.heartbeat("DEVICE_SYNC"):
            return self._handle(*args, **options)

    def _handle(self, *args, **options):
        device = self._resolve_device(options["device"])
        since = collector.parse_day(options["since"]) if options["since"] else None
        until = collector.parse_day(options["until"]) if options["until"] else None
        if since and until and since > until:
            raise CommandError("--since is after --until.")

        if options["roster_only"] and options["punches_only"]:
            raise CommandError("--roster-only and --punches-only are mutually exclusive.")

        # One collector per terminal. A backlog read can outlast the five-minute
        # cron interval, and the overlapping run would compete for the device's
        # small connection pool rather than queue behind the first. A busy lock
        # is a skip, not a failure — see biometric/locking.py.
        try:
            with locking.device_lock(device.label):
                summary = collector.sync_device(
                    device,
                    host=options["host"],
                    comm_key=options["comm_key"],
                    since=since, until=until,
                    include_roster=not options["punches_only"],
                    include_punches=not options["roster_only"],
                    dry_run=options["dry_run"],
                    source=options["source"],
                    derive=not options["no_derive"],
                    timeout=options["timeout"],
                    driver=options["driver"],
                )
        except locking.CollectorBusy as exc:
            self.stdout.write(self.style.WARNING(f"Skipped: {exc}"))
            return
        except zk_client.ZKAuthError as exc:
            raise CommandError(str(exc)) from exc
        except (zk_client.ZKError, collector.CollectorError) as exc:
            raise CommandError(f"Sync failed: {exc}") from exc

        if options["json"]:
            self.stdout.write(json.dumps(summary, indent=2, default=str))
            return
        self._render(summary)

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
                "No active BiometricDevice exists. Create one first — its "
                "`label`, `host` and `device_timezone` are what this command reads.")
        if len(active) > 1:
            raise CommandError(
                "More than one device is active; pass --device <label>. Guessing "
                "would risk filing one terminal's punches under another's.")
        return active[0]

    # -- output -------------------------------------------------------------
    def _render(self, summary):
        write = self.stdout.write
        write(self.style.MIGRATE_HEADING(
            f"Device sync — {summary['device']} ({summary['host']})"))
        if summary["dry_run"]:
            write(self.style.WARNING("  DRY RUN — nothing was written."))

        window = summary["window"]
        if window["since"] or window["until"]:
            note = " (from the high-water mark)" if window.get("from_high_water_mark") else ""
            write(f"  Window: {window['since'] or 'start'} .. "
                  f"{window['until'] or 'today'}{note}")

        sizes = summary.get("sizes") or {}
        if sizes:
            write(f"  On device: {sizes.get('users', '?')} users, "
                  f"{sizes.get('records', '?')} stored records")

        drift = summary["clock"].get("drift_seconds")
        if drift is None:
            write("  Clock: not readable")
        else:
            style = self.style.WARNING if abs(drift) > collector.CLOCK_DRIFT_ALARM_SECONDS else self.style.SUCCESS
            write(style(f"  Clock drift: {drift:+d}s (device minus server)"))

        roster = summary.get("roster")
        if roster:
            write(self.style.MIGRATE_LABEL("  Roster"))
            write(f"    read {roster['read']} enrolled users")
            if "created" in roster:
                write(f"    created {roster['created']}, updated {roster.get('updated', 0)}, "
                      f"unmapped total {roster.get('unmapped_total', '?')}")
            ids = roster.get("device_user_ids") or []
            if ids:
                write(f"    device user IDs: {_join_ids(ids)}")

        punches = summary.get("punches")
        if punches:
            write(self.style.MIGRATE_LABEL("  Punches"))
            write(f"    read {punches['read']}, in window {punches['in_window']} "
                  f"(skipped {punches['skipped_before_window']} before, "
                  f"{punches['skipped_after_window']} after)")
            if punches.get("earliest"):
                write(f"    range {punches['earliest']} .. {punches['latest']}")
            if "created" in punches:
                write(f"    created {punches['created']}, duplicate "
                      f"{punches['duplicate']}, unmapped {punches['unmapped']}")
            if punches.get("derivation"):
                write(self.style.WARNING(f"    {punches['derivation']}"))

        for warning in summary.get("warnings", []):
            write(self.style.WARNING(f"  ! {warning}"))

        unmapped = (summary.get("punches") or {}).get("unmapped")
        if unmapped:
            write(self.style.WARNING(
                f"  {unmapped} punches have no employee mapping. They are stored, "
                f"not lost — resolve them in the HR mapping queue and the "
                f"attendance backfills automatically."))


def _join_ids(ids):
    """Numeric-first ordering, so a roster reads 1, 2, 10 rather than 1, 10, 2."""
    ordered = sorted(ids, key=services.device_id_sort_key)
    if len(ordered) > 40:
        return ", ".join(ordered[:40]) + f", … (+{len(ordered) - 40} more)"
    return ", ".join(ordered)
