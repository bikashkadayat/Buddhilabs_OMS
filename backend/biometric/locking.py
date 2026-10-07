"""One collector per terminal, enforced across processes.

WHY
---
Two collectors reading the same terminal at once is not a hypothetical. The
crontab runs ``device_sync`` every five minutes; a full-backlog read with
``--timeout 90`` against a terminal holding years of records can take longer
than five minutes, at which point cron starts a second one on top of the first.
``device_sync_loop`` adds another way in, and an operator running a manual sync
while either is going adds a third.

The terminal is the part that cannot cope. A ZK unit has a connection pool a
few slots deep and no queueing: the second session does not wait its turn, it
gets a timeout or a truncated transfer. The truncated case is the dangerous one,
because a short attendance table is indistinguishable from a quiet day —
``zk_client`` catches the transfers that declare their own length, but the
cheapest defence is simply not to overlap.

Ingest itself is already safe under concurrency (the unique constraint on
``AttendancePunch`` makes a double read idempotent), so this protects the device
conversation, not the database.

BEHAVIOUR
---------
Non-blocking, and a busy lock is **not an error**. A skipped cycle costs
nothing: the terminal keeps its entire log, so whatever this run would have
collected, the next one collects. Waiting instead would just stack cron ticks up
behind a slow sync until they all fire at once.

The lock is advisory and per-device, keyed on the device label, and it is
released by the kernel if the holder dies — so a killed collector does not
require anyone to clear a stale lock file by hand.
"""
import contextlib
import logging
import os
import tempfile

from django.conf import settings

logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover — POSIX only; the app is deployed on Linux
    fcntl = None


class CollectorBusy(Exception):
    """Another process is already collecting from this terminal."""


def lock_path(label):
    """Where the lock for ``label`` lives.

    Under the system temp dir by default rather than the repo, so it never ends
    up committed and is cleared by a reboot. ``BIOMETRIC_LOCK_DIR`` overrides it
    for a deployment that wants locks on a specific volume — which matters if
    /tmp is per-service (systemd PrivateTmp), because then two units would each
    get their own /tmp and the lock would not be shared at all.
    """
    directory = getattr(settings, "BIOMETRIC_LOCK_DIR", None) or tempfile.gettempdir()
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(label))
    return os.path.join(directory, f"nifn-biometric-{safe}.lock")


@contextlib.contextmanager
def device_lock(label):
    """Hold the collector lock for ``label``, or raise ``CollectorBusy``.

    On a platform without ``fcntl`` this degrades to a no-op rather than
    refusing to run: the lock is a safeguard against a performance problem, and
    losing it must not stop attendance being collected at all.
    """
    if fcntl is None:
        yield
        return

    path = lock_path(label)
    handle = open(path, "w")  # noqa: SIM115 — held for the duration of the block
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CollectorBusy(
                f"another collector is already reading {label}. Skipping this "
                f"run — the terminal keeps its whole log, so the next run "
                f"collects anything this one would have.") from exc

        # Recorded for diagnosis only; the flock is what actually holds.
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
