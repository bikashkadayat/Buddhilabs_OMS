"""One collector per terminal.

The lock protects the *device conversation*, not the database — ingest is
already idempotent. What it prevents is two readers competing for a ZK
terminal's few connection slots, which is what happens when a backlog read
outlasts the five-minute cron interval and the next tick starts on top of it.
"""
import multiprocessing
import os
import tempfile

import pytest
from django.core.management import call_command

from biometric import locking


@pytest.fixture
def lock_dir(settings):
    """Keep each test's locks out of the shared system temp dir.

    Without this the lock file is keyed only on the device label, so two tests
    using the same label would contend with each other — and worse, with a real
    collector running on the developer's machine.
    """
    with tempfile.TemporaryDirectory() as path:
        settings.BIOMETRIC_LOCK_DIR = path
        yield path


def test_the_lock_is_held_for_the_duration_of_the_block(lock_dir):
    with locking.device_lock("main-gate"):
        assert os.path.exists(locking.lock_path("main-gate"))


def test_a_second_holder_is_refused_rather_than_queued(lock_dir):
    """Refused, not blocked.

    Queueing would stack cron ticks behind a slow sync until they all fired at
    once. Skipping costs nothing because the terminal keeps its whole log.
    """
    with locking.device_lock("main-gate"):
        with pytest.raises(locking.CollectorBusy):
            with locking.device_lock("main-gate"):
                pass


def test_the_lock_is_released_on_exit(lock_dir):
    with locking.device_lock("main-gate"):
        pass
    with locking.device_lock("main-gate"):
        pass  # would raise if the first block had not released


def test_an_exception_inside_the_block_still_releases(lock_dir):
    with pytest.raises(ValueError):
        with locking.device_lock("main-gate"):
            raise ValueError("sync blew up")
    with locking.device_lock("main-gate"):
        pass


def test_locks_are_per_device(lock_dir):
    """Two terminals are two independent conversations and must not block each
    other — otherwise adding a second device halves both devices' sync rate."""
    with locking.device_lock("main-gate"), locking.device_lock("warehouse"):
        pass


def test_a_label_that_is_not_a_safe_filename_still_locks(lock_dir):
    """Labels are free text; the device in this deployment is literally labelled
    '192.168.77.201'. A label must not be able to escape the lock directory."""
    with locking.device_lock("../../etc/passwd"):
        path = locking.lock_path("../../etc/passwd")
    assert os.path.dirname(os.path.abspath(path)) == os.path.abspath(lock_dir)


def _hold_and_signal(lock_dir, acquired, release):
    """Child process: take the lock, tell the parent, wait to be told to stop."""
    from django.conf import settings

    settings.BIOMETRIC_LOCK_DIR = lock_dir
    with locking.device_lock("main-gate"):
        acquired.set()
        release.wait(timeout=30)


def test_the_lock_holds_across_processes(lock_dir):
    """The case that matters: cron and the sync loop are separate processes.

    An in-process guard would pass every other test here and protect nothing in
    production.
    """
    ctx = multiprocessing.get_context("fork")
    acquired, release = ctx.Event(), ctx.Event()
    child = ctx.Process(target=_hold_and_signal, args=(lock_dir, acquired, release))
    child.start()
    try:
        assert acquired.wait(timeout=30), "child never acquired the lock"
        with pytest.raises(locking.CollectorBusy):
            with locking.device_lock("main-gate"):
                pass
    finally:
        release.set()
        child.join(timeout=30)


@pytest.mark.django_db
def test_device_sync_skips_rather_than_fails_when_the_lock_is_held(
        lock_dir, device, capsys):
    """A busy lock must exit 0.

    `device_sync` runs under cron. Raising CommandError would mail the operator
    a failure every five minutes for a condition that is both expected and
    harmless.
    """
    with locking.device_lock(device.label):
        call_command("device_sync", "--device", device.label)

    out = capsys.readouterr().out
    assert "Skipped" in out
    # Never reached the terminal — proved by the absence of a connection error
    # for a host that does not exist in the test environment.
    assert "Sync failed" not in out
