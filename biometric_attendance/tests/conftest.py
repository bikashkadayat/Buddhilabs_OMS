"""Shared fixtures.

Nothing here touches a real device — the whole suite runs on a laptop with no
hardware on the network.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from morx.models import AttendanceRecord, Employee  # noqa: E402

MORX_VARS = [
    "MORX_DEVICE_HOST",
    "MORX_DEVICE_PORT",
    "MORX_DEVICE_NAME",
    "MORX_DEVICE_PASSWORD",
    "MORX_DEVICE_TIMEOUT",
    "MORX_DEVICE_FORCE_UDP",
    "MORX_DEVICE_OMIT_PING",
    "MORX_DEVICE_ENCODING",
    "MORX_DEVICES_FILE",
    "MORX_STORAGE_BACKEND",
    "MORX_DATA_DIR",
    "MORX_SQLITE_PATH",
    "MORX_SYNC_HISTORY",
    "MORX_LIVE_POLL_INTERVAL",
    "MORX_HEARTBEAT_INTERVAL",
    "MORX_RECONNECT_MIN_DELAY",
    "MORX_RECONNECT_MAX_DELAY",
    "MORX_LOG_LEVEL",
    "MORX_LOG_FORMAT",
    "MORX_LOG_FILE",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Stop a developer's real .env from leaking into assertions."""
    for var in MORX_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture
def punch():
    """Factory for attendance records."""

    def make(
        employee_id: str = "1",
        minutes: int = 0,
        punch: int = 0,
        source: str = "LIVE",
        device: str = "gate",
        name: str = "Alice",
    ) -> AttendanceRecord:
        return AttendanceRecord(
            employee_id=employee_id,
            name=name,
            timestamp=datetime(2026, 8, 3, 9, 0, 0) + timedelta(minutes=minutes),
            status=1,
            punch=punch,
            source=source,
            device=device,
        )

    return make


@pytest.fixture
def employee():
    def make(employee_id: str = "1", name: str = "Alice", device: str = "gate") -> Employee:
        return Employee(employee_id=employee_id, name=name, device=device)

    return make


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """Run a test inside an empty directory so relative paths stay contained."""
    monkeypatch.chdir(tmp_path)
    os.makedirs(tmp_path / "data", exist_ok=True)
    return tmp_path
