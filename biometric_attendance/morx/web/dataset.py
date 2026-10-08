"""Load collected attendance into memory for the dashboard.

Read-only. This module never writes to `data/` — the collector owns those files
and may be appending to them while the dashboard reads.

Two things here are less obvious than they look:

* **The CSV on disk is not one format.** Early prototypes wrote a six-column
  row (`employee_id,name,timestamp,status,punch,source`); the package writes
  eight, with `device` in front and `punch_label` before `source`. A real
  deployment ends up with both in the same file — the migration in
  `storage.py` rewrites the header, then an older script appends behind it. So
  rows are parsed by width, not by header, and the leftovers are deduped away.
* **Employees are keyed by id alone, not (device, id).** Storage keys by both,
  because two terminals are two rosters. A *reader* wants the opposite: one
  person, all doors. So punches merge per employee_id and `device` survives as
  a filter.
"""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Wall-clock timestamps from the device are naive local time. Encoding them as
# "seconds since epoch, pretending the wall clock is UTC" is what lets the
# browser render them unchanged (via getUTC*) instead of shifting them into the
# viewer's zone -- a punch at 09:05 must read 09:05 everywhere.
_EPOCH = datetime(1970, 1, 1)

TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M")

LEGACY_ATTENDANCE_COLUMNS = ["employee_id", "name", "timestamp", "status", "punch", "source"]
ATTENDANCE_COLUMNS = [
    "device",
    "employee_id",
    "name",
    "timestamp",
    "status",
    "punch",
    "punch_label",
    "source",
]

UNKNOWN_DEVICE = "unknown"


@dataclass(frozen=True)
class Punch:
    """One scan, flattened for reading."""

    device: str
    employee_id: str
    name: str
    timestamp: datetime
    status: int
    punch: int
    source: str

    @property
    def epoch(self) -> int:
        return int((self.timestamp - _EPOCH).total_seconds())


@dataclass
class Person:
    employee_id: str
    name: str = ""
    privilege: int = 0
    card: int = 0
    group_id: str = ""
    devices: set[str] = field(default_factory=set)
    enrolled: bool = False  # present in the roster, not merely seen punching


@dataclass
class Dataset:
    """Everything the dashboard renders, already deduped and sorted."""

    punches: list[Punch] = field(default_factory=list)
    people: dict[str, Person] = field(default_factory=dict)
    backend: str = "csv"
    location: str = ""
    notes: list[str] = field(default_factory=list)

    # -- payload ------------------------------------------------------------

    def to_payload(self) -> dict:
        """Compact columnar JSON.

        Columnar rather than a list of objects because the keys repeat on every
        row otherwise -- at ~11k punches that is the difference between a ~1.5MB
        response and a ~250KB one, and the browser does the aggregation anyway.
        """
        devices = sorted({p.device for p in self.punches} | {d for p in self.people.values() for d in p.devices})
        device_index = {name: i for i, name in enumerate(devices)}

        order = sorted(self.people.values(), key=lambda p: (_sort_key(p.employee_id), p.name))
        people_index = {p.employee_id: i for i, p in enumerate(order)}

        counts = dict.fromkeys(people_index, 0)
        for p in self.punches:
            counts[p.employee_id] += 1

        employees = [
            {
                "id": p.employee_id,
                "name": p.name or f"#{p.employee_id}",
                "privilege": p.privilege,
                "card": p.card,
                "group_id": p.group_id,
                "devices": sorted(device_index[d] for d in p.devices if d in device_index),
                "enrolled": p.enrolled,
                "punches": counts[p.employee_id],
            }
            for p in order
        ]

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": {"backend": self.backend, "location": self.location},
            "devices": devices,
            "employees": employees,
            "records": {
                # Parallel arrays, all the same length, sorted by t ascending.
                "e": [people_index[p.employee_id] for p in self.punches],
                "t": [p.epoch for p in self.punches],
                "p": [p.punch for p in self.punches],
                "d": [device_index[p.device] for p in self.punches],
                "s": [1 if p.source.upper() == "LIVE" else 0 for p in self.punches],
            },
            "notes": self.notes,
        }


def _sort_key(employee_id: str) -> tuple[int, object]:
    """Numeric ids sort numerically; anything else falls in behind them."""
    return (0, int(employee_id)) if employee_id.isdigit() else (1, employee_id)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def parse_timestamp(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _int(raw: object, default: int = 0) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _row_to_punch(row: list[str]) -> Punch | None:
    """Map one CSV row to a punch, choosing the layout by width."""
    if len(row) >= len(ATTENDANCE_COLUMNS):
        device, employee_id, name, ts, status, punch, _label, source = row[:8]
    elif len(row) == len(LEGACY_ATTENDANCE_COLUMNS):
        employee_id, name, ts, status, punch, source = row
        device = ""  # unknown -- resolved during dedup
    else:
        return None

    timestamp = parse_timestamp(ts)
    if timestamp is None or not employee_id.strip():
        return None

    return Punch(
        device=device.strip(),
        employee_id=employee_id.strip(),
        name=name.strip(),
        timestamp=timestamp,
        status=_int(status),
        punch=_int(punch),
        source=(source or "").strip() or "HISTORY",
    )


def _dedup(punches: list[Punch]) -> tuple[list[Punch], int]:
    """Collapse rows that describe the same scan.

    Storage's key is (device, employee, timestamp, punch). Legacy rows carry no
    device, so they can't be compared on it directly -- but they are the *same*
    punches an upgraded row already covers. Two passes: index everything that
    names a device, then admit a device-less row only if no device has already
    claimed that (employee, timestamp, punch).
    """
    kept: list[Punch] = []
    seen: set[tuple[str, str, int, int]] = set()
    seen_anywhere: set[tuple[str, int, int]] = set()

    for p in punches:
        if not p.device:
            continue
        key = (p.device, p.employee_id, p.epoch, p.punch)
        if key in seen:
            continue
        seen.add(key)
        seen_anywhere.add((p.employee_id, p.epoch, p.punch))
        kept.append(p)

    for p in punches:
        if p.device:
            continue
        key = (p.employee_id, p.epoch, p.punch)
        if key in seen_anywhere:
            continue
        seen_anywhere.add(key)
        kept.append(replace(p, device=UNKNOWN_DEVICE))

    kept.sort(key=lambda p: (p.epoch, p.employee_id, p.punch))
    return kept, len(punches) - len(kept)


def _build(punches: list[Punch], roster: dict[str, Person]) -> tuple[list[Punch], dict[str, Person], list[str]]:
    kept, collapsed = _dedup(punches)

    people = dict(roster)
    for p in kept:
        person = people.get(p.employee_id)
        if person is None:
            person = Person(employee_id=p.employee_id, name=p.name)
            people[p.employee_id] = person
        # The roster is authoritative for the name; fall back to whatever the
        # punch carried so an un-enrolled id still shows something readable.
        if not person.name:
            person.name = p.name
        person.devices.add(p.device)

    notes = []
    if collapsed:
        notes.append(f"{collapsed:,} duplicate rows collapsed (legacy and current CSV layouts overlap)")
    unenrolled = sum(1 for p in people.values() if not p.enrolled)
    if unenrolled:
        notes.append(f"{unenrolled} employee id(s) seen punching but absent from the roster")

    # A terminal that lost its clock stamps punches years out. They are real
    # scans, so they are kept -- but they would otherwise drag every "last N
    # days" window with them, so say so plainly and let the dashboard anchor
    # its ranges to today instead of to the newest row.
    horizon = datetime.now() + timedelta(days=1)
    future = [p for p in kept if p.timestamp > horizon]
    if future:
        newest = max(p.timestamp for p in future)
        notes.append(
            f"{len(future):,} punch(es) are dated in the future (up to {newest:%d %b %Y}) -- "
            "the device clock was wrong. They are included, but relative date ranges "
            "are measured from today."
        )
    return kept, people, notes


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def load_csv(data_dir: Path) -> Dataset:
    attendance_path = data_dir / "attendance.csv"
    employee_path = data_dir / "employees.csv"

    punches: list[Punch] = []
    if attendance_path.exists():
        with attendance_path.open(newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.reader(fh):
                if not row or row[0].strip() in ("device", "employee_id", "id"):
                    continue  # header -- may appear more than once in an appended file
                punch = _row_to_punch(row)
                if punch is not None:
                    punches.append(punch)

    roster = _load_roster_csv(employee_path)
    kept, people, notes = _build(punches, roster)
    return Dataset(
        punches=kept,
        people=people,
        backend="csv",
        location=str(attendance_path.resolve()),
        notes=notes,
    )


def _load_roster_csv(path: Path) -> dict[str, Person]:
    if not path.exists():
        return {}

    people: dict[str, Person] = {}
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.reader(fh):
            if not row or row[0].strip() in ("device", "employee_id", "id"):
                continue
            if len(row) >= 6:
                device, employee_id, name, privilege, card, group_id = row[:6]
            elif len(row) == 2:  # the original prototype wrote just id,name
                employee_id, name = row
                device, privilege, card, group_id = "", "0", "0", ""
            else:
                continue

            employee_id = employee_id.strip()
            if not employee_id:
                continue

            person = people.setdefault(employee_id, Person(employee_id=employee_id))
            person.enrolled = True
            person.name = name.strip() or person.name
            person.privilege = max(person.privilege, _int(privilege))
            person.card = person.card or _int(card)
            person.group_id = person.group_id or group_id.strip()
            if device.strip():
                person.devices.add(device.strip())
    return people


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


def load_sqlite(path: Path) -> Dataset:
    if not path.exists():
        return Dataset(backend="sqlite", location=str(path), notes=["database not created yet"])

    # Read-only URI so a dashboard can never lock or alter the collector's file.
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        roster: dict[str, Person] = {}
        for row in conn.execute("SELECT device, employee_id, name, privilege, card, group_id FROM employees"):
            person = roster.setdefault(row["employee_id"], Person(employee_id=row["employee_id"]))
            person.enrolled = True
            person.name = row["name"] or person.name
            person.privilege = max(person.privilege, _int(row["privilege"]))
            person.card = person.card or _int(row["card"])
            person.group_id = person.group_id or (row["group_id"] or "")
            person.devices.add(row["device"])

        punches = []
        for row in conn.execute(
            "SELECT device, employee_id, name, timestamp, status, punch, source"
            " FROM attendance ORDER BY timestamp"
        ):
            timestamp = parse_timestamp(row["timestamp"])
            if timestamp is None:
                continue
            punches.append(
                Punch(
                    device=row["device"],
                    employee_id=row["employee_id"],
                    name=row["name"] or "",
                    timestamp=timestamp,
                    status=_int(row["status"]),
                    punch=_int(row["punch"]),
                    source=row["source"] or "HISTORY",
                )
            )
    finally:
        conn.close()

    kept, people, notes = _build(punches, roster)
    return Dataset(punches=kept, people=people, backend="sqlite", location=str(path.resolve()), notes=notes)


def load(backend: str, data_dir: Path, sqlite_path: Path) -> Dataset:
    return load_sqlite(sqlite_path) if backend == "sqlite" else load_csv(data_dir)


def fingerprint(backend: str, data_dir: Path, sqlite_path: Path) -> tuple:
    """Cheap change detector: (size, mtime) of whatever backs the data.

    The dashboard caches the parsed dataset and rebuilds only when this moves,
    so a browser polling every few seconds costs a `stat`, not a reparse.
    """
    paths = [sqlite_path] if backend == "sqlite" else [data_dir / "attendance.csv", data_dir / "employees.csv"]
    return tuple(
        (str(p), p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else (str(p), -1, -1) for p in paths
    )
