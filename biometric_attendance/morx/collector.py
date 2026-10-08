"""The collection loop.

One `DeviceCollector` per terminal, each on its own thread, all writing to a
shared `Storage`. A device that goes offline retries on its own and cannot stall
the others.

Shutdown is cooperative via a `threading.Event`, not a global flag: the event is
what lets a thread blocked on a socket read wake up and exit cleanly.
"""

from __future__ import annotations

import logging
import threading
import time

from .config import DeviceConfig, Settings
from .device import Device, DeviceError, backoff_delay
from .storage import Storage

logger = logging.getLogger(__name__)


class DeviceCollector:
    """Keeps one device connected and streaming into storage."""

    def __init__(self, config: DeviceConfig, storage: Storage, settings: Settings) -> None:
        self.config = config
        self.storage = storage
        self.settings = settings
        self.stop = threading.Event()
        self.log = logging.getLogger(f"morx.collector.{config.label}")

    def request_stop(self) -> None:
        self.stop.set()

    def run(self) -> None:
        """Connect, sync, stream — reconnecting forever until asked to stop."""
        attempt = 0

        while not self.stop.is_set():
            try:
                with Device(self.config).connect() as device:
                    attempt = 0  # a successful connect resets the backoff
                    self._session(device)

            except DeviceError as exc:
                self.log.error("%s", exc)
            except Exception:
                # Unexpected: log the traceback, then keep the service alive.
                self.log.exception("Unhandled error in collector loop")

            if self.stop.is_set():
                break

            attempt += 1
            delay = backoff_delay(
                attempt, self.settings.reconnect_min_delay, self.settings.reconnect_max_delay
            )
            self.log.info("Reconnecting in %.1fs (attempt %d)", delay, attempt)
            # Event.wait doubles as an interruptible sleep.
            self.stop.wait(delay)

        self.log.info("Collector stopped")

    def _session(self, device: Device) -> None:
        """One connected session: roster, backlog, then the live stream."""
        employees = device.fetch_employees()
        changed = self.storage.save_employees(employees)
        self.log.info("Roster: %d employees (%d new/changed)", len(employees), changed)

        names = {e.employee_id: e.name for e in employees}

        if self.settings.sync_history:
            self._sync_history(device, names)

        if not self.stop.is_set():
            self._stream(device, names)

    def _sync_history(self, device: Device, names: dict[str, str]) -> None:
        """Pull the device's stored backlog.

        Runs on every reconnect on purpose: writes are idempotent, so this is
        how punches that happened while we were offline get backfilled.
        """
        records = device.fetch_attendance(names)
        started = time.monotonic()
        new = self.storage.save_many(records)
        self.log.info(
            "Backlog: %d records on device, %d new (%.1fs)",
            len(records),
            new,
            time.monotonic() - started,
        )

    def _stream(self, device: Device, names: dict[str, str]) -> None:
        self.log.info("Listening for live punches")
        last_event = time.monotonic()
        heartbeat = self.settings.heartbeat_interval

        for record in device.stream_attendance(
            names, self.stop, self.settings.live_poll_interval
        ):
            if self.stop.is_set():
                break

            if record is None:
                # Idle tick from the socket timeout — use it to prove liveness.
                idle = time.monotonic() - last_event
                if heartbeat and idle >= heartbeat:
                    self.log.info("Alive, idle for %.0fs", idle)
                    last_event = time.monotonic()
                continue

            last_event = time.monotonic()

            if self.storage.save_attendance(record):
                self.log.info(
                    "PUNCH %s (%s) at %s -> %s",
                    record.employee_id,
                    record.name,
                    record.timestamp,
                    record.punch_label,
                )
            else:
                self.log.debug("Duplicate punch ignored: %s", record.dedup_key)


class CollectorService:
    """Runs every configured device and shuts them all down together."""

    def __init__(self, settings: Settings, storage: Storage) -> None:
        self.settings = settings
        self.storage = storage
        self.collectors = [
            DeviceCollector(device, storage, settings) for device in settings.devices
        ]
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        logger.info("Starting collectors for %d device(s)", len(self.collectors))
        for collector in self.collectors:
            thread = threading.Thread(
                target=collector.run, name=f"collector-{collector.config.label}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def shutdown(self, timeout: float = 15.0) -> None:
        logger.info("Shutting down")
        for collector in self.collectors:
            collector.request_stop()
        for thread in self._threads:
            thread.join(timeout=timeout)
            if thread.is_alive():
                # Daemon threads; the process can still exit. Say so rather than hang.
                logger.warning("%s did not stop within %.0fs", thread.name, timeout)

    def wait(self) -> None:
        """Block the main thread while workers run, staying responsive to signals."""
        try:
            while any(thread.is_alive() for thread in self._threads):
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
