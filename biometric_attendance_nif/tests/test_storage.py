"""Storage tests.

The dedup and migration cases are parametrised over both backends so CSV and
SQLite are held to exactly the same contract.
"""

from __future__ import annotations

import csv
import sqlite3

import pytest

from morx.storage import CsvStorage, SqliteStorage


@pytest.fixture(params=["csv", "sqlite"])
def store(request, tmp_path):
    if request.param == "csv":
        backend = CsvStorage(tmp_path / "data")
    else:
        backend = SqliteStorage(tmp_path / "data" / "attendance.db")
    yield backend
    backend.close()


# -- shared contract --------------------------------------------------------


def test_first_write_is_new(store, punch):
    assert store.save_attendance(punch()) is True


def test_identical_punch_is_rejected(store, punch):
    store.save_attendance(punch())
    assert store.save_attendance(punch()) is False


def test_same_punch_from_history_and_live_stored_once(store, punch):
    assert store.save_attendance(punch(source="HISTORY")) is True
    assert store.save_attendance(punch(source="LIVE")) is False


def test_distinct_punches_all_stored(store, punch):
    records = [punch(minutes=0), punch(minutes=1), punch(employee_id="2"), punch(device="wh")]
    assert store.save_many(records) == 4


def test_save_many_counts_only_new(store, punch):
    store.save_attendance(punch(minutes=0))
    assert store.save_many([punch(minutes=0), punch(minutes=5)]) == 1


def test_employees_upserted_not_duplicated(store, employee):
    assert store.save_employees([employee(), employee("2", "Bob")]) == 2
    assert store.save_employees([employee(), employee("2", "Bob")]) == 0  # unchanged
    assert store.save_employees([employee(name="Alice Smith")]) == 1  # renamed


@pytest.mark.parametrize("backend", ["csv", "sqlite"])
def test_dedup_survives_restart(backend, tmp_path, punch):
    """A restarted collector re-pulls the backlog; it must not re-append it."""

    def open_store():
        if backend == "csv":
            return CsvStorage(tmp_path / "data")
        return SqliteStorage(tmp_path / "data" / "attendance.db")

    first = open_store()
    assert first.save_attendance(punch()) is True
    first.close()

    second = open_store()
    try:
        assert second.save_attendance(punch()) is False
    finally:
        second.close()


# -- CSV specifics ----------------------------------------------------------


def test_csv_writes_header_once(tmp_path, punch):
    store = CsvStorage(tmp_path / "data")
    store.save_attendance(punch(minutes=0))
    store.save_attendance(punch(minutes=1))

    lines = (store.attendance_path).read_text().strip().splitlines()
    assert lines[0].startswith("device,employee_id")
    assert len(lines) == 3  # header + 2 rows


def test_csv_employee_file_is_rewritten_not_appended(tmp_path, employee):
    store = CsvStorage(tmp_path / "data")
    store.save_employees([employee()])
    store.save_employees([employee(name="Alice Smith")])

    rows = list(csv.DictReader(store.employee_path.open()))
    assert len(rows) == 1
    assert rows[0]["name"] == "Alice Smith"


def test_csv_migrates_legacy_columns(tmp_path):
    """The old format had no `device` column and called the employee id `id`."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "attendance.csv").write_text(
        "employee_id,name,timestamp,status,punch,source\n"
        "1,Bikash,2023-01-08 17:12:44,1,0,HISTORY\n"
    )
    (data / "employees.csv").write_text("id,name\n1,Bikash\n")

    store = CsvStorage(data)

    rows = list(csv.DictReader((data / "attendance.csv").open()))
    assert rows[0]["device"] == "legacy"
    assert rows[0]["punch_label"] == "check_in"

    employees = list(csv.DictReader((data / "employees.csv").open()))
    assert employees[0]["employee_id"] == "1"

    # originals preserved
    assert (data / "attendance.csv.bak").exists()
    assert (data / "employees.csv.bak").exists()
    store.close()


def test_csv_tolerates_a_corrupt_row_on_load(tmp_path, punch):
    """A hand-edited file should not stop the service from starting."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "attendance.csv").write_text(
        "device,employee_id,name,timestamp,status,punch,punch_label,source\n"
        "gate,1,Alice,2026-08-03 09:00:00,1,not-a-number,check_in,LIVE\n"
    )
    store = CsvStorage(data)
    assert store.save_attendance(punch()) is True  # unreadable row simply isn't deduped against
    store.close()


# -- SQLite specifics -------------------------------------------------------


def test_sqlite_unique_constraint_enforced_at_db_level(tmp_path, punch):
    store = SqliteStorage(tmp_path / "a.db")
    store.save_attendance(punch())
    store.close()

    conn = sqlite3.connect(tmp_path / "a.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO attendance "
            "(device, employee_id, name, timestamp, status, punch, punch_label, source) "
            "VALUES ('gate','1','Alice','2026-08-03 09:00:00',1,0,'check_in','LIVE')"
        )
    conn.close()


def test_sqlite_employee_update_changes_name(tmp_path, employee):
    store = SqliteStorage(tmp_path / "a.db")
    store.save_employees([employee()])
    store.save_employees([employee(name="Alice Smith")])
    row = store._conn.execute("SELECT name FROM employees").fetchone()
    assert row["name"] == "Alice Smith"
    store.close()
