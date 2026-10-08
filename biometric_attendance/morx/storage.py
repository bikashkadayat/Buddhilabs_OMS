"""Storage backends.

One interface, two implementations. Both are idempotent — writing the same
punch twice is a no-op, so re-running the collector or replaying a device's
backlog never duplicates rows — and thread-safe, since there is one collector
thread per device sharing a single store.

Adding Postgres later means implementing `Storage` and one line in `open_storage`.
"""

from __future__ import annotations

import csv
import logging
import shutil
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from .config import Settings
from .models import PUNCH_LABELS, AttendanceRecord, Employee

logger = logging.getLogger(__name__)

ATTENDANCE_FIELDS = [
    "device",
    "employee_id",
    "name",
    "timestamp",
    "status",
    "punch",
    "punch_label",
    "source",
]
EMPLOYEE_FIELDS = ["device", "employee_id", "name", "privilege", "card", "group_id"]

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class Storage(ABC):
    """Persistence contract."""

    @abstractmethod
    def save_employees(self, employees: list[Employee]) -> int:
        """Upsert employees. Returns how many were new or changed."""

    @abstractmethod
    def save_attendance(self, record: AttendanceRecord) -> bool:
        """Write one punch. Returns False if it was already stored."""

    @abstractmethod
    def close(self) -> None: ...

    def save_many(self, records: list[AttendanceRecord]) -> int:
        return sum(1 for record in records if self.save_attendance(record))

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


class CsvStorage(Storage):
    """Append-only CSV.

    Dedup runs against an in-memory set seeded from the file at startup — cheap
    at the scale a door terminal produces. Employees are rewritten wholesale
    rather than appended, which is what stops repeated syncs from growing the
    roster file forever.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.attendance_path = data_dir / "attendance.csv"
        self.employee_path = data_dir / "employees.csv"
        self._lock = threading.Lock()

        _migrate(self.attendance_path, ATTENDANCE_FIELDS, _upgrade_attendance)
        _migrate(self.employee_path, EMPLOYEE_FIELDS, _upgrade_employee)

        self._seen = self._load_seen()
        self._employees = self._load_employees()
        logger.info(
            "CSV storage ready (%d punches, %d employees) in %s",
            len(self._seen),
            len(self._employees),
            data_dir,
        )

    def _load_seen(self) -> set[tuple[str, str, str, int]]:
        if not self.attendance_path.exists():
            return set()
        seen = set()
        with self.attendance_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    punch = int(row.get("punch") or 0)
                except ValueError:
                    continue  # tolerate a hand-edited row rather than refuse to start
                seen.add(
                    (row.get("device", ""), row.get("employee_id", ""), row.get("timestamp", ""), punch)
                )
        return seen

    def _load_employees(self) -> dict[tuple[str, str], dict]:
        if not self.employee_path.exists():
            return {}
        with self.employee_path.open(newline="", encoding="utf-8") as fh:
            return {
                (row.get("device", ""), row.get("employee_id", "")): row
                for row in csv.DictReader(fh)
            }

    def save_employees(self, employees: list[Employee]) -> int:
        with self._lock:
            changed = 0
            for e in employees:
                row = {
                    "device": e.device,
                    "employee_id": e.employee_id,
                    "name": e.name,
                    "privilege": str(e.privilege),
                    "card": str(e.card),
                    "group_id": e.group_id,
                }
                if self._employees.get((e.device, e.employee_id)) != row:
                    self._employees[(e.device, e.employee_id)] = row
                    changed += 1
            if changed:
                _atomic_write(self.employee_path, EMPLOYEE_FIELDS, list(self._employees.values()))
            return changed

    def save_attendance(self, record: AttendanceRecord) -> bool:
        with self._lock:
            if record.dedup_key in self._seen:
                return False

            new_file = not self.attendance_path.exists()
            with self.attendance_path.open("a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=ATTENDANCE_FIELDS)
                if new_file:
                    writer.writeheader()
                writer.writerow(
                    {
                        "device": record.device,
                        "employee_id": record.employee_id,
                        "name": record.name,
                        "timestamp": record.timestamp.strftime(TIME_FORMAT),
                        "status": record.status,
                        "punch": record.punch,
                        "punch_label": record.punch_label,
                        "source": record.source,
                    }
                )
            self._seen.add(record.dedup_key)
            return True

    def close(self) -> None:
        return None


def _atomic_write(path: Path, fields: list[str], rows: list[dict]) -> None:
    """Temp file + rename, so a crash mid-write can't truncate the real file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def _migrate(path: Path, fields: list[str], upgrade) -> None:
    """Bring a pre-existing CSV up to the current column set, once.

    Older versions wrote fewer columns. Rather than fail on startup — or worse,
    append mismatched rows — rewrite in place and keep a .bak.
    """
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        if header == fields:
            return
        rows = [upgrade(row) for row in reader]

    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    _atomic_write(path, fields, rows)
    logger.warning("Migrated %s to new columns (original kept as %s)", path.name, backup.name)


def _upgrade_attendance(row: dict) -> dict:
    try:
        punch = int(row.get("punch") or 0)
    except ValueError:
        punch = 0
    return {
        "device": row.get("device") or "legacy",
        "employee_id": row.get("employee_id", ""),
        "name": row.get("name", ""),
        "timestamp": row.get("timestamp", ""),
        "status": row.get("status", ""),
        "punch": punch,
        "punch_label": row.get("punch_label") or PUNCH_LABELS.get(punch, f"unknown_{punch}"),
        "source": row.get("source", ""),
    }


def _upgrade_employee(row: dict) -> dict:
    return {
        "device": row.get("device") or "legacy",
        "employee_id": row.get("employee_id") or row.get("id", ""),  # old column was "id"
        "name": row.get("name", ""),
        "privilege": row.get("privilege", "0"),
        "card": row.get("card", "0"),
        "group_id": row.get("group_id", ""),
    }


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    device       TEXT NOT NULL,
    employee_id  TEXT NOT NULL,
    name         TEXT NOT NULL,
    privilege    INTEGER NOT NULL DEFAULT 0,
    card         INTEGER NOT NULL DEFAULT 0,
    group_id     TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (device, employee_id)
);

CREATE TABLE IF NOT EXISTS attendance (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    device       TEXT NOT NULL,
    employee_id  TEXT NOT NULL,
    name         TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    status       INTEGER NOT NULL,
    punch        INTEGER NOT NULL,
    punch_label  TEXT NOT NULL,
    source       TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (device, employee_id, timestamp, punch)
);

CREATE INDEX IF NOT EXISTS idx_attendance_ts ON attendance (timestamp);
CREATE INDEX IF NOT EXISTS idx_attendance_emp ON attendance (device, employee_id);
"""

_INSERT_PUNCH = """
INSERT OR IGNORE INTO attendance
    (device, employee_id, name, timestamp, status, punch, punch_label, source)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""


class SqliteStorage(Storage):
    """SQLite backend.

    Dedup is enforced by the database (UNIQUE + INSERT OR IGNORE) rather than by
    application state, so it stays correct across concurrent writers.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")  # reports can read while we write
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        logger.info("SQLite storage ready at %s", path)

    @staticmethod
    def _row(r: AttendanceRecord) -> tuple:
        return (
            r.device,
            r.employee_id,
            r.name,
            r.timestamp.strftime(TIME_FORMAT),
            r.status,
            r.punch,
            r.punch_label,
            r.source,
        )

    def save_employees(self, employees: list[Employee]) -> int:
        if not employees:
            return 0
        with self._lock, self._conn:
            before = self._conn.total_changes
            self._conn.executemany(
                """
                INSERT INTO employees (device, employee_id, name, privilege, card, group_id)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (device, employee_id) DO UPDATE SET
                    name       = excluded.name,
                    privilege  = excluded.privilege,
                    card       = excluded.card,
                    group_id   = excluded.group_id,
                    updated_at = datetime('now')
                WHERE
                    -- Without this guard an unchanged roster still counts as a
                    -- write, so `save_employees` would report bogus churn and
                    -- bump updated_at on every reconnect.
                    employees.name      IS NOT excluded.name
                 OR employees.privilege IS NOT excluded.privilege
                 OR employees.card      IS NOT excluded.card
                 OR employees.group_id  IS NOT excluded.group_id
                """,
                [(e.device, e.employee_id, e.name, e.privilege, e.card, e.group_id) for e in employees],
            )
            return self._conn.total_changes - before

    def save_attendance(self, record: AttendanceRecord) -> bool:
        with self._lock, self._conn:
            return self._conn.execute(_INSERT_PUNCH, self._row(record)).rowcount > 0

    def save_many(self, records: list[AttendanceRecord]) -> int:
        """Batch insert in one transaction — much faster for a backlog pull."""
        if not records:
            return 0
        with self._lock, self._conn:
            before = self._conn.total_changes
            self._conn.executemany(_INSERT_PUNCH, [self._row(r) for r in records])
            return self._conn.total_changes - before

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def open_storage(settings: Settings) -> Storage:
    if settings.storage_backend == "sqlite":
        return SqliteStorage(settings.sqlite_path)
    return CsvStorage(settings.data_dir)
