"""Optional `pyzk` driver, exposing the same interface as ``ZKReadOnlyClient``.

WHY THIS EXISTS
---------------
The native client in ``zk_client`` was written against the ZK specification and
exercised against a simulator, because the terminal does not answer from the
machine it was written on. That is honest testing, but a simulator cannot
reproduce a firmware quirk nobody knew to encode.

Meanwhile the device's owner already has ``pyzk`` talking to the real terminal
successfully. So this exists as insurance: if the native client stumbles on the
real firmware, the import does not stop and wait for a fix — it switches driver
and proceeds with the library that is already proven against that exact device.

``pyzk`` stays an **optional** dependency. It is imported lazily and only when
asked for, so nothing about the default path depends on it being installed.

READ-ONLY, ENFORCED THE SAME WAY
--------------------------------
``pyzk`` is a full read/write SDK: it can clear the attendance log, delete
users, unlock the door and power the terminal off. This wrapper exposes exactly
four operations and calls nothing else.

Specifically it never calls ``disable_device()``. Plenty of pyzk examples do
that around a read, and it locks the terminal's keypad and screen for the
duration — on a cron, during working hours, that stops staff punching in. The
library does not require it; the examples merely habitually use it.
"""
import logging

logger = logging.getLogger(__name__)

# Anything not on this list is not called. Kept as data so the guarantee is
# checkable rather than a claim in prose.
PERMITTED_CALLS = ("connect", "disconnect", "get_users", "get_attendance",
                   "get_time", "read_sizes")


class PyzkUnavailable(RuntimeError):
    """pyzk was requested but is not installed."""


def available():
    try:
        import zk  # noqa: F401
        return True
    except ImportError:
        return False


class PyzkReadOnlyClient:
    """Duck-compatible with ``zk_client.ZKReadOnlyClient``.

    Same constructor, same context-manager behaviour, same four read methods
    returning the same dict shapes — so ``collector.sync_device`` cannot tell
    the two apart and neither driver needs a special case anywhere downstream.
    """

    def __init__(self, host, port=4370, *, timeout=30, comm_key=0, encoding="utf-8"):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.comm_key = int(comm_key or 0)
        self.encoding = encoding
        self._conn = None
        self.connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.disconnect()
        return False

    def connect(self):
        try:
            from zk import ZK
        except ImportError as exc:
            raise PyzkUnavailable(
                "pyzk is not installed. `pip install pyzk`, or drop --driver "
                "pyzk to use the built-in client.") from exc

        # ommit_ping: the OMS host may not be permitted to ICMP the terminal
        # even where TCP is fine, and a failed ping would refuse a connection
        # that would otherwise work.
        machine = ZK(self.host, port=self.port, timeout=self.timeout,
                     password=self.comm_key, force_udp=False, ommit_ping=True)
        self._conn = machine.connect()
        self.connected = True
        return self

    def disconnect(self):
        if self._conn is None:
            return
        try:
            self._conn.disconnect()
        except Exception:  # noqa: BLE001 — a failed goodbye must not mask a real error
            logger.debug("pyzk disconnect from %s failed; continuing", self.host)
        finally:
            self._conn = None
            self.connected = False

    # -- reads --------------------------------------------------------------
    def sizes(self):
        try:
            self._conn.read_sizes()
        except Exception as exc:  # noqa: BLE001
            logger.warning("pyzk read_sizes failed: %s", exc)
            return {}
        return {"users": getattr(self._conn, "users", 0) or 0,
                "records": getattr(self._conn, "records", 0) or 0,
                "users_capacity": getattr(self._conn, "users_cap", 0) or 0,
                "records_capacity": getattr(self._conn, "rec_cap", 0) or 0}

    def device_time(self):
        """Naive device wall clock, matching the native client's contract."""
        moment = self._conn.get_time()
        return moment.replace(tzinfo=None) if moment.tzinfo else moment

    def users(self):
        """Roster in ingest's shape.

        ``user_id`` is the terminal's own identifier and is passed through as a
        string with no normalisation — no int(), no strip of leading zeros.
        ``uid`` is pyzk's internal index and is only a fallback for firmware
        that leaves ``user_id`` blank.
        """
        return [{
            "employee_id": str(u.user_id) if u.user_id not in (None, "") else str(u.uid),
            "name": (u.name or "").strip(),
            "privilege": int(u.privilege or 0),
            "card": int(u.card or 0),
            "group_id": str(u.group_id or ""),
            "uid": u.uid,
        } for u in self._conn.get_users()]

    def attendance(self):
        return [{
            "employee_id": str(a.user_id),
            "timestamp": a.timestamp,          # naive, device-local
            "punch": int(getattr(a, "punch", 0) or 0),
            "status": int(getattr(a, "status", 0) or 0),
        } for a in self._conn.get_attendance()]
