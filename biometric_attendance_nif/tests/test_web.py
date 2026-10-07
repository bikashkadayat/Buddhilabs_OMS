"""Dashboard tests.

Offline like the rest of the suite: the loader reads files a test writes, and
the server is exercised over a real socket bound to an ephemeral port.

The cases that matter are the ones a real `data/` directory forced: a CSV that
holds two different column layouts at once, a roster with the same employee
listed once per sync, and timestamps that must survive the trip to the browser
without being shifted into another timezone.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from morx.config import DeviceConfig, Settings
from morx.storage import SqliteStorage
from morx.web import dataset, server

CURRENT_HEADER = dataset.ATTENDANCE_COLUMNS
LEGACY_HEADER = dataset.LEGACY_ATTENDANCE_COLUMNS
ROSTER_HEADER = ["device", "employee_id", "name", "privilege", "card", "group_id"]


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def append_csv(path: Path, rows: list[list], header: list[str] | None = None) -> None:
    """Append rows, optionally under a second header -- which is exactly what an
    older script doing its own `open(..., 'a')` produced in the real file."""
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if header:
            writer.writerow(header)
        writer.writerows(rows)


def current_row(employee_id="1", name="Alice", ts="2026-08-03 09:00:00", punch=0, device="gate", source="HISTORY"):
    # punch_label is written by storage but ignored on read -- the dashboard
    # derives the label from the code, so a stale label can never mislabel a row.
    label = {0: "check_in", 1: "check_out"}.get(punch, f"unknown_{punch}")
    return [device, employee_id, name, ts, 1, punch, label, source]


def legacy_row(employee_id="1", name="Alice", ts="2026-08-03 09:00:00", punch=0, source="HISTORY"):
    return [employee_id, name, ts, 1, punch, source]


@pytest.fixture
def data_dir(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    return directory


# -- parsing ----------------------------------------------------------------


def test_reads_current_layout(data_dir):
    write_csv(data_dir / "attendance.csv", CURRENT_HEADER, [current_row()])

    data = dataset.load_csv(data_dir)

    assert len(data.punches) == 1
    assert data.punches[0].employee_id == "1"
    assert data.punches[0].device == "gate"
    assert data.punches[0].timestamp == datetime(2026, 8, 3, 9, 0, 0)


def test_reads_legacy_layout(data_dir):
    write_csv(data_dir / "attendance.csv", LEGACY_HEADER, [legacy_row()])

    data = dataset.load_csv(data_dir)

    assert len(data.punches) == 1
    # No device column existed, so the punch can't be attributed to a terminal.
    assert data.punches[0].device == dataset.UNKNOWN_DEVICE


def test_legacy_rows_appended_behind_current_rows_collapse(data_dir):
    """The exact shape of the real data/attendance.csv.

    `storage.py` rewrote the header to the eight-column layout, then an older
    script kept appending six-column rows behind it -- so every punch appears
    twice, in two formats. Counting them twice would double every figure on
    the dashboard.
    """
    path = data_dir / "attendance.csv"
    write_csv(path, CURRENT_HEADER, [current_row(ts="2026-08-03 09:00:00"), current_row(ts="2026-08-03 18:00:00", punch=1)])
    append_csv(path, [legacy_row(ts="2026-08-03 09:00:00"), legacy_row(ts="2026-08-03 18:00:00", punch=1)], LEGACY_HEADER)

    data = dataset.load_csv(data_dir)

    assert len(data.punches) == 2
    assert {p.device for p in data.punches} == {"gate"}
    assert any("duplicate rows collapsed" in note for note in data.notes)


def test_same_punch_on_two_devices_is_kept(data_dir):
    """Dedup must not merge terminals -- two doors are two events."""
    write_csv(data_dir / "attendance.csv", CURRENT_HEADER, [
        current_row(device="gate"),
        current_row(device="warehouse"),
    ])

    data = dataset.load_csv(data_dir)

    assert len(data.punches) == 2


def test_malformed_rows_are_skipped_not_fatal(data_dir):
    write_csv(data_dir / "attendance.csv", CURRENT_HEADER, [
        current_row(),
        ["gate", "2", "Bob", "not-a-date", 1, 0, "check_in", "HISTORY"],
        ["gate", "", "Nobody", "2026-08-03 10:00:00", 1, 0, "check_in", "HISTORY"],
        ["too", "few"],
    ])

    data = dataset.load_csv(data_dir)

    assert len(data.punches) == 1


def test_punches_are_sorted_by_time(data_dir):
    write_csv(data_dir / "attendance.csv", CURRENT_HEADER, [
        current_row(ts="2026-08-03 18:00:00", punch=1),
        current_row(ts="2026-08-01 09:00:00"),
        current_row(ts="2026-08-03 09:00:00"),
    ])

    data = dataset.load_csv(data_dir)

    assert [p.timestamp for p in data.punches] == sorted(p.timestamp for p in data.punches)


# -- roster -----------------------------------------------------------------


def test_roster_collapses_repeated_sync_rows(data_dir):
    write_csv(data_dir / "attendance.csv", CURRENT_HEADER, [current_row()])
    write_csv(data_dir / "employees.csv", ROSTER_HEADER, [
        ["gate", "1", "Alice", 0, 0, ""],
        ["gate", "1", "Alice", 0, 0, ""],
        ["gate", "2", "Bob", 14, 0, ""],
    ])

    data = dataset.load_csv(data_dir)

    assert set(data.people) == {"1", "2"}
    assert data.people["2"].privilege == 14


def test_employee_seen_punching_but_not_enrolled_is_flagged(data_dir):
    write_csv(data_dir / "attendance.csv", CURRENT_HEADER, [current_row(employee_id="99", name="Ghost")])
    write_csv(data_dir / "employees.csv", ROSTER_HEADER, [])

    data = dataset.load_csv(data_dir)

    assert data.people["99"].enrolled is False
    assert any("absent from the roster" in note for note in data.notes)


def test_same_employee_on_two_devices_is_one_person(data_dir):
    """Storage keys by (device, id); a reader wants one person, all doors."""
    write_csv(data_dir / "attendance.csv", CURRENT_HEADER, [
        current_row(device="gate"),
        current_row(device="warehouse", ts="2026-08-03 09:05:00"),
    ])

    data = dataset.load_csv(data_dir)

    assert set(data.people) == {"1"}
    assert data.people["1"].devices == {"gate", "warehouse"}


def test_future_timestamps_are_kept_and_reported(data_dir):
    ahead = (datetime.now() + timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
    write_csv(data_dir / "attendance.csv", CURRENT_HEADER, [current_row(), current_row(ts=ahead)])

    data = dataset.load_csv(data_dir)

    assert len(data.punches) == 2  # kept -- they are real scans on a wrong clock
    assert any("dated in the future" in note for note in data.notes)


# -- payload ----------------------------------------------------------------


def test_payload_columns_are_parallel(data_dir):
    write_csv(data_dir / "attendance.csv", CURRENT_HEADER, [
        current_row(),
        current_row(employee_id="2", name="Bob", ts="2026-08-03 10:00:00"),
    ])

    payload = dataset.load_csv(data_dir).to_payload()
    records = payload["records"]

    assert len({len(records[key]) for key in "etpds"} ) == 1
    assert len(records["t"]) == 2
    assert all(records["e"][i] < len(payload["employees"]) for i in range(2))
    assert all(records["d"][i] < len(payload["devices"]) for i in range(2))


def test_payload_preserves_wall_clock():
    """A 09:05 punch must read 09:05 in the browser, in any timezone.

    The payload encodes the device's naive local time as if it were UTC, so
    the browser's getUTC* accessors return the original wall clock.
    """
    punch = dataset.Punch(
        device="gate", employee_id="1", name="Alice",
        timestamp=datetime(2026, 8, 3, 9, 5, 0), status=1, punch=0, source="LIVE",
    )

    assert datetime(1970, 1, 1) + timedelta(seconds=punch.epoch) == punch.timestamp


def test_payload_marks_live_records(data_dir):
    write_csv(data_dir / "attendance.csv", CURRENT_HEADER, [
        current_row(source="HISTORY"),
        current_row(ts="2026-08-03 09:05:00", source="LIVE"),
    ])

    payload = dataset.load_csv(data_dir).to_payload()

    assert payload["records"]["s"] == [0, 1]


def test_employees_sort_numerically(data_dir):
    write_csv(data_dir / "attendance.csv", CURRENT_HEADER, [
        current_row(employee_id=str(i), ts=f"2026-08-03 09:{i:02d}:00") for i in (2, 10, 1)
    ])

    payload = dataset.load_csv(data_dir).to_payload()

    assert [e["id"] for e in payload["employees"]] == ["1", "2", "10"]


def test_missing_files_produce_an_empty_dataset(data_dir):
    payload = dataset.load_csv(data_dir).to_payload()

    assert payload["records"]["t"] == []
    assert payload["employees"] == []


# -- sqlite -----------------------------------------------------------------


def test_sqlite_backend_matches_csv(tmp_path, punch, employee):
    db = tmp_path / "attendance.db"
    with SqliteStorage(db) as store:
        store.save_employees([employee(employee_id="1", name="Alice")])
        store.save_attendance(punch(employee_id="1"))
        store.save_attendance(punch(employee_id="1", minutes=30, punch=1))

    data = dataset.load_sqlite(db)

    assert len(data.punches) == 2
    assert data.people["1"].name == "Alice"
    assert data.people["1"].enrolled is True


def test_sqlite_open_is_read_only(tmp_path, punch, employee):
    db = tmp_path / "attendance.db"
    with SqliteStorage(db) as store:
        store.save_attendance(punch())

    dataset.load_sqlite(db)  # must not leave the file locked or modified

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 1


def test_missing_database_is_not_an_error(tmp_path):
    data = dataset.load_sqlite(tmp_path / "nope.db")

    assert data.punches == []
    assert data.notes


# -- fingerprint ------------------------------------------------------------


def test_fingerprint_moves_when_data_changes(data_dir):
    path = data_dir / "attendance.csv"
    write_csv(path, CURRENT_HEADER, [current_row()])
    before = dataset.fingerprint("csv", data_dir, data_dir / "attendance.db")

    append_csv(path, [current_row(ts="2026-08-03 18:00:00", punch=1)])

    assert dataset.fingerprint("csv", data_dir, data_dir / "attendance.db") != before


# -- server -----------------------------------------------------------------


@pytest.fixture
def live_server(data_dir):
    write_csv(data_dir / "attendance.csv", CURRENT_HEADER, [
        current_row(),
        current_row(ts="2026-08-03 18:00:00", punch=1),
    ])
    settings = Settings(
        devices=[DeviceConfig(host="10.0.0.1", name="gate")],
        storage_backend="csv",
        data_dir=data_dir,
        sqlite_path=data_dir / "attendance.db",
    )
    httpd = server.build_server(settings, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def get(url: str):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.read()


def test_api_data_returns_the_payload(live_server):
    status, body = get(live_server + "/api/data")
    payload = json.loads(body)

    assert status == 200
    assert len(payload["records"]["t"]) == 2
    assert payload["source"]["backend"] == "csv"


def test_api_meta_is_a_cheap_summary(live_server):
    status, body = get(live_server + "/api/meta")
    meta = json.loads(body)

    assert status == 200
    assert meta["punches"] == 2
    assert "revision" in meta


def test_index_and_its_assets_are_served(live_server):
    """The built React app is wired up end to end.

    Asset names carry a content hash that changes on every rebuild, so they are
    read out of index.html rather than hardcoded -- which also checks that what
    the page asks for is what the server can actually deliver.
    """
    status, body = get(live_server + "/")
    assert status == 200

    assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', body.decode("utf-8"))
    assert assets, "index.html references no built assets -- run `npm run build` in frontend/"

    for path in assets:
        status, content = get(live_server + path)
        assert status == 200
        assert content


def test_built_assets_are_cacheable(live_server):
    """Hashed filenames are safe to cache forever; index.html is not."""
    body = get(live_server + "/")[1].decode("utf-8")
    asset = re.search(r'(?:src|href)="(/assets/[^"]+)"', body).group(1)

    with urllib.request.urlopen(live_server + asset, timeout=5) as response:
        assert "immutable" in response.headers["Cache-Control"]
    with urllib.request.urlopen(live_server + "/", timeout=5) as response:
        assert response.headers["Cache-Control"] == "no-store"


def test_unknown_path_is_404(live_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(live_server + "/nope.js")
    assert exc.value.code == 404


def test_path_traversal_is_refused(live_server):
    # urllib normalises "..", so hit the handler with the escaped form.
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(live_server + "/%2e%2e/%2e%2e/config.py")
    assert exc.value.code == 404


def test_data_updates_when_the_file_changes(live_server, data_dir):
    assert json.loads(get(live_server + "/api/meta")[1])["punches"] == 2

    append_csv(data_dir / "attendance.csv", [current_row(employee_id="2", name="Bob", ts="2026-08-04 09:00:00")])

    assert json.loads(get(live_server + "/api/meta")[1])["punches"] == 3
