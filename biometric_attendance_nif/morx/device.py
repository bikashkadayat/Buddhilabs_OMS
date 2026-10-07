"""Device layer — the only module that knows `pyzk` exists.

Wraps a ZK terminal in something that reconnects, times out, and yields typed
records. Everything above this line is device-agnostic.
"""

from __future__ import annotations

import logging
import random
import threading
from collections.abc import Iterator
from typing import Any

from zk import ZK

from .config import DeviceConfig
from .models import AttendanceRecord, Employee

logger = logging.getLogger(__name__)


class DeviceError(Exception):
    """Talking to the terminal failed."""


class Device:
    """A single ZK terminal.

    Use as a context manager so the socket is always released — a ZK device
    accepts a limited number of concurrent connections and a leaked one can
    lock out the next run until the firmware times it out.

        with Device(cfg).connect() as device:
            employees = device.fetch_employees()
    """

    def __init__(self, config: DeviceConfig) -> None:
        self.config = config
        self.log = logging.getLogger(f"morx.device.{config.label}")
        self._conn: Any | None = None

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> Device:
        cfg = self.config
        self.log.info("Connecting to %s:%s", cfg.host, cfg.port)
        try:
            zk = ZK(
                cfg.host,
                port=cfg.port,
                timeout=cfg.timeout,
                password=cfg.password,
                force_udp=cfg.force_udp,
                ommit_ping=cfg.omit_ping,  # pyzk spells it with two m's
                encoding=cfg.encoding,
            )
            self._conn = zk.connect()
        except Exception as exc:
            raise DeviceError(f"{cfg.label}: connection failed: {exc}") from exc

        self.log.info("Connected (%s)", self.describe())
        return self

    def disconnect(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.disconnect()
            self.log.info("Disconnected")
        except Exception as exc:
            # Nothing useful left to do — we are already tearing down.
            self.log.warning("Unclean disconnect: %s", exc)
        finally:
            self._conn = None

    def __enter__(self) -> Device:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.disconnect()

    @property
    def conn(self) -> Any:
        if self._conn is None:
            raise DeviceError(f"{self.config.label}: not connected")
        return self._conn

    # -- reads -------------------------------------------------------------

    def describe(self) -> str:
        """Best-effort identification string. Never raises — clones vary in
        which of these commands they implement."""
        parts = []
        for label, getter in (
            ("name", "get_device_name"),
            ("serial", "get_serialnumber"),
            ("firmware", "get_firmware_version"),
        ):
            try:
                value = getattr(self.conn, getter)()
                if value:
                    parts.append(f"{label}={value}")
            except Exception:  # noqa: BLE001 - identification is optional
                continue
        return ", ".join(parts) or "unidentified"

    def fetch_employees(self) -> list[Employee]:
        try:
            users = self.conn.get_users()
        except Exception as exc:
            raise DeviceError(f"{self.config.label}: could not read users: {exc}") from exc
        return [Employee.from_zk(u, self.config.label) for u in users]

    def fetch_attendance(self, names: dict[str, str]) -> list[AttendanceRecord]:
        """Pull the device's stored backlog."""
        try:
            logs = self.conn.get_attendance()
        except Exception as exc:
            raise DeviceError(f"{self.config.label}: could not read attendance: {exc}") from exc

        return [
            AttendanceRecord.from_zk(
                log, names.get(str(log.user_id).strip(), "Unknown"), "HISTORY", self.config.label
            )
            for log in logs
        ]

    def stream_attendance(
        self, names: dict[str, str], stop: threading.Event, poll_interval: int = 10
    ) -> Iterator[AttendanceRecord | None]:
        """Yield punches as they happen.

        pyzk yields `None` every `poll_interval` seconds when the socket read
        times out. That tick is load-bearing: it is our only chance to notice
        `stop` and exit without waiting for someone to touch the scanner.
        """
        try:
            for event in self.conn.live_capture(new_timeout=poll_interval):
                if stop.is_set():
                    break
                if event is None:
                    yield None  # idle tick — caller uses it for heartbeats
                    continue
                yield AttendanceRecord.from_zk(
                    event,
                    names.get(str(event.user_id).strip(), "Unknown"),
                    "LIVE",
                    self.config.label,
                )
        except Exception as exc:
            raise DeviceError(f"{self.config.label}: live capture failed: {exc}") from exc
        finally:
            self._end_live_capture()

    def _end_live_capture(self) -> None:
        """Ask pyzk's generator loop to unwind so the socket timeout is restored."""
        if self._conn is not None:
            try:
                self._conn.end_live_capture = True
            except Exception:  # noqa: BLE001
                pass


def backoff_delay(attempt: int, minimum: float, maximum: float) -> float:
    """Exponential backoff with full jitter.

    Jitter matters with more than one device: without it, a switch reboot makes
    every collector retry in lockstep forever.
    """
    ceiling = min(maximum, minimum * (2 ** max(0, attempt - 1)))
    return random.uniform(minimum, max(minimum, ceiling))
