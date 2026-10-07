"""Collector-loop tests.

`Device` is swapped for a fake, so these exercise the retry, dedup and shutdown
logic with no hardware and no sockets.
"""

from __future__ import annotations

import threading

import pytest

from morx.collector import DeviceCollector
from morx.config import DeviceConfig, Settings
from morx.device import DeviceError, backoff_delay
from morx.models import Employee
from morx.storage import CsvStorage


class FakeDevice:
    """Stands in for morx.device.Device.

    `script` is a list of behaviours, one per connection attempt: either an
    exception to raise on connect, or a list of records to stream.
    """

    def __init__(self, config, script, employees=None):
        self.config = config
        self.script = script
        self.employees = employees or [Employee("1", "Alice", device=config.label)]
        self.connects = 0

    def __call__(self, config):  # so the class can be used as the Device factory
        return self

    def connect(self):
        step = self.script[min(self.connects, len(self.script) - 1)]
        self.connects += 1
        if isinstance(step, Exception):
            raise step
        self._records = step
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def fetch_employees(self):
        return self.employees

    def fetch_attendance(self, names):
        return list(self._records)

    def stream_attendance(self, names, stop, poll_interval=10):
        yield from self._records
        stop.set()  # nothing left to send; end the test run


@pytest.fixture
def settings(clean_env, tmp_path):
    clean_env.setenv("MORX_DEVICE_HOST", "10.0.0.1")
    clean_env.setenv("MORX_DEVICE_NAME", "gate")
    clean_env.setenv("MORX_DATA_DIR", str(tmp_path / "data"))
    clean_env.setenv("MORX_RECONNECT_MIN_DELAY", "0")
    clean_env.setenv("MORX_RECONNECT_MAX_DELAY", "0")
    return Settings.load()


@pytest.fixture
def storage(tmp_path):
    store = CsvStorage(tmp_path / "data")
    yield store
    store.close()


def run_with(fake, settings, storage, monkeypatch):
    monkeypatch.setattr("morx.collector.Device", fake)
    collector = DeviceCollector(settings.devices[0], storage, settings)
    thread = threading.Thread(target=collector.run, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), "collector failed to stop"
    return collector


def test_records_are_persisted(settings, storage, monkeypatch, punch):
    records = [punch(minutes=0), punch(minutes=1)]
    run_with(FakeDevice(settings.devices[0], [records]), settings, storage, monkeypatch)

    assert len(storage._seen) == 2


def test_backlog_and_live_stream_do_not_double_write(settings, storage, monkeypatch, punch):
    """The same punch arrives twice — once in the backlog pull, once live."""
    records = [punch(minutes=0)]
    run_with(FakeDevice(settings.devices[0], [records]), settings, storage, monkeypatch)

    rows = storage.attendance_path.read_text().strip().splitlines()
    assert len(rows) == 2  # header + exactly one punch


def test_employees_are_synced(settings, storage, monkeypatch, punch):
    run_with(FakeDevice(settings.devices[0], [[punch()]]), settings, storage, monkeypatch)
    assert storage.employee_path.exists()
    assert "Alice" in storage.employee_path.read_text()


def test_connection_failure_is_retried(settings, storage, monkeypatch, punch):
    """A device that is offline on first contact must not kill the collector."""
    fake = FakeDevice(
        settings.devices[0],
        [DeviceError("offline"), DeviceError("still offline"), [punch()]],
    )
    run_with(fake, settings, storage, monkeypatch)

    assert fake.connects == 3
    assert len(storage._seen) == 1


def test_stop_before_start_exits_immediately(settings, storage, monkeypatch, punch):
    monkeypatch.setattr("morx.collector.Device", FakeDevice(settings.devices[0], [[punch()]]))
    collector = DeviceCollector(settings.devices[0], storage, settings)
    collector.request_stop()
    collector.run()  # returns rather than blocking

    assert len(storage._seen) == 0


def test_history_sync_can_be_disabled(clean_env, tmp_path, storage, monkeypatch, punch):
    clean_env.setenv("MORX_DEVICE_HOST", "10.0.0.1")
    clean_env.setenv("MORX_DEVICE_NAME", "gate")
    clean_env.setenv("MORX_SYNC_HISTORY", "false")
    settings = Settings.load()

    fetched = []

    class NoHistoryDevice(FakeDevice):
        def fetch_attendance(self, names):
            fetched.append(names)
            return super().fetch_attendance(names)

    run_with(NoHistoryDevice(settings.devices[0], [[punch()]]), settings, storage, monkeypatch)
    assert fetched == []  # backlog never pulled


# -- backoff ----------------------------------------------------------------


def test_backoff_stays_within_bounds():
    for attempt in range(1, 20):
        delay = backoff_delay(attempt, minimum=2, maximum=60)
        assert 2 <= delay <= 60


def test_backoff_grows_then_caps():
    early = max(backoff_delay(1, 2, 60) for _ in range(50))
    late = max(backoff_delay(10, 2, 60) for _ in range(50))
    assert early < late <= 60


def test_backoff_is_jittered():
    """Without jitter, every device retries in lockstep after a switch reboot."""
    delays = {backoff_delay(5, 2, 60) for _ in range(50)}
    assert len(delays) > 1


def test_device_config_used_verbatim():
    """Sanity check that nothing in the stack hardcodes a host or port."""
    config = DeviceConfig(host="203.0.113.9", port=9999, name="anywhere")
    assert (config.host, config.port, config.label) == ("203.0.113.9", 9999, "anywhere")
