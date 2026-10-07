"""Read-only ZK terminal client — the OMS's own collector transport.

Phase 12. The Phase 6 design assumed an external collector process ("morx")
pushing punches at the ingest API. Phase 12 requires the opposite: *everything
must run inside the OMS, no separate attendance system.* So this module pulls
directly from the terminal and hands the result to the same
``ingest.ingest_punches`` / ``ingest.ingest_roster`` functions the HTTP endpoint
uses. One ingest path, two ways in.

WHY NOT pyzk
------------
Three reasons, in order of weight:

1. **Read-only cannot be bolted on.** pyzk is a full read/write SDK: it can
   clear the attendance log, delete users, unlock the door and power the
   terminal off. This collector runs unattended on a cron, against the device
   that is the source of truth for payroll. The safe design is a client that
   *cannot* express a destructive command, not one that merely refrains — so
   every command is checked against ``READ_ONLY_COMMANDS`` before it goes on
   the wire.
2. **No dependency** on the critical path of attendance capture.
3. The wire framing here is exact rather than heuristic — see ``_recv_packet``.

pyzk remains available as an explicit fallback (``--driver pyzk``,
``biometric/pyzk_driver.py``) for the case this client meets a firmware quirk
the specification did not describe. That wrapper calls only connect, disconnect
and the three reads, asserted by a test that parses it rather than trusting the
comment.

WHAT IT WILL NOT DO
-------------------
No ``CMD_DISABLEDEVICE``. Some ZK integrations disable the terminal for the
duration of a read, which locks the keypad and screen; doing that on a cron
during working hours would stop staff punching in. Reading while the device
stays live can in principle interleave with a punch, but the cost is at worst a
punch that arrives on the next cycle — against a guaranteed outage every cycle.
No clear, no write, no unlock.

TRANSPORT
---------
TCP only. UDP is in the protocol but fragments and silently truncates large
reads, and the device validation in Phase 12 confirmed TCP/4370 is the reachable
path. A UDP fallback that quietly returns a short attendance log would be worse
than no fallback at all.
"""
import logging
import socket
import struct
from datetime import datetime

from .device_probe import USHRT_MAX, ZK_TCP_MAGIC, build_packet, wrap_tcp

logger = logging.getLogger(__name__)

# --- session ---------------------------------------------------------------
CMD_CONNECT = 1000
CMD_EXIT = 1001
CMD_AUTH = 1102

# --- replies ---------------------------------------------------------------
CMD_ACK_OK = 2000
CMD_ACK_ERROR = 2001
CMD_ACK_DATA = 2002
CMD_ACK_UNAUTH = 2005

# --- data transfer ---------------------------------------------------------
CMD_PREPARE_DATA = 1500
CMD_DATA = 1501
CMD_FREE_DATA = 1502
CMD_DATA_WRRQ = 1503     # "give me <fct> via the buffer protocol"
CMD_READ_BUFFER = 1504

# --- reads -----------------------------------------------------------------
CMD_USERTEMP_RRQ = 9
CMD_ATTLOG_RRQ = 13
CMD_GET_TIME = 201
CMD_GET_FREE_SIZES = 50

FCT_ATTLOG = 1
FCT_USER = 5

# The whole safety story in one constant. A command not in here cannot be sent,
# which is what makes "read-only" a property of the client rather than a promise
# in a docstring. CMD_FREE_DATA releases the device's own read buffer after a
# transfer — it frees nothing of ours and destroys no records.
READ_ONLY_COMMANDS = frozenset({
    CMD_CONNECT, CMD_EXIT, CMD_AUTH,
    CMD_DATA_WRRQ, CMD_READ_BUFFER, CMD_FREE_DATA,
    CMD_USERTEMP_RRQ, CMD_ATTLOG_RRQ, CMD_GET_TIME, CMD_GET_FREE_SIZES,
})

DEFAULT_PORT = 4370
DEFAULT_TIMEOUT = 20
MAX_CHUNK = 0xFFC0
# A terminal holding several years of records still tops out around a few MB.
# 64 MB is far above any real device and far below "exhaust the host's memory
# because a firmware bug reported a nonsense size".
MAX_TRANSFER_BYTES = 64 * 1024 * 1024

USER_RECORD_SIZES = (28, 72)
ATT_RECORD_SIZES = (8, 16, 40)


class ZKError(Exception):
    """Any failure talking to the terminal."""


class ZKAuthError(ZKError):
    """The terminal has a comm key set and ours was missing or wrong."""


class ZKProtocolError(ZKError):
    """Something answered, but not a ZK terminal."""


# ---------------------------------------------------------------------------
# comm key
# ---------------------------------------------------------------------------
def make_commkey(key, session_id, ticks=50):
    """ZK's session-salted obfuscation of the comm key.

    Not a cryptographic construction — it is a scramble the firmware expects,
    reimplemented here to match. It is bound to the session id, so the value on
    the wire differs every connection; that stops a trivially replayed capture
    but is not authentication in any modern sense. Treat the LAN segment the
    terminal sits on as the actual security boundary.
    """
    key = int(key)
    reversed_bits = 0
    for i in range(32):
        reversed_bits = (reversed_bits << 1) | (1 if key & (1 << i) else 0)
    # Mask: bit-reversal plus the session id can exceed 32 bits, and struct
    # would raise rather than wrap the way the firmware does.
    packed = struct.pack("<I", (reversed_bits + int(session_id)) & 0xFFFFFFFF)

    b = struct.unpack("<4B", packed)
    packed = struct.pack("<4B", b[0] ^ ord("Z"), b[1] ^ ord("K"),
                         b[2] ^ ord("S"), b[3] ^ ord("O"))
    high, low = struct.unpack("<2H", packed)
    packed = struct.pack("<2H", low, high)

    tick = 0xFF & ticks
    b = struct.unpack("<4B", packed)
    # The third byte is the tick VALUE, not a byte XORed with it. That
    # asymmetry looks like a typo and is not: verified against a working
    # session with 192.168.77.201, where the device expected `61 7d 32 c9`
    # (0x32 == tick == 50) and rejected our `61 7d d6 c9`.
    return struct.pack("<4B", b[0] ^ tick, b[1] ^ tick, tick, b[3] ^ tick)


# ---------------------------------------------------------------------------
# record decoding
# ---------------------------------------------------------------------------
def decode_device_time(raw):
    """Unpack ZK's packed-integer clock into a **naive** datetime.

    Naive on purpose: the terminal keeps wall-clock local time with no zone, so
    the only honest thing to return is the wall clock. Attaching a zone is the
    caller's job, because only the caller knows (from
    ``BiometricDevice.device_timezone``) which zone that wall clock is in.

    The encoding packs the date into a fixed 31-day month and 12-month year, so
    day/month arithmetic must not be "improved" into calendar arithmetic.
    """
    value = struct.unpack("<I", raw)[0] if isinstance(raw, bytes) else int(raw)
    second, value = value % 60, value // 60
    minute, value = value % 60, value // 60
    hour, value = value % 24, value // 24
    day, value = value % 31 + 1, value // 31
    month, value = value % 12 + 1, value // 12
    return datetime(value + 2000, month, day, hour, minute, second)


def _text(raw, encoding="utf-8"):
    return raw.split(b"\x00")[0].decode(encoding, errors="ignore").strip()


def parse_users(payload, encoding="utf-8", count=None):
    """Decode a user-table transfer into roster dicts.

    Field names match ``ingest.ingest_roster``'s expected payload so nothing has
    to be translated in between — and specifically so ``employee_id`` carries
    the terminal's own user id, unaltered. That string is the identity the whole
    system hangs off; this function is the last place it could be corrupted and
    it does nothing to it but decode the bytes.

    ``count`` is the enrolled-user count from ``CMD_GET_FREE_SIZES``. It is what
    resolves the record width exactly; see ``_candidate_sizes``.
    """
    body = _body(payload)
    if body is None:
        return []   # a terminal with nobody enrolled, not an error
    return _decode_table(body, count, USER_RECORD_SIZES, "user",
                         lambda chunk, size: _decode_user(chunk, size, encoding),
                         _users_are_plausible)


def parse_attendance(payload, encoding="utf-8", count=None):
    """Decode an attendance transfer into punch dicts with naive timestamps."""
    body = _body(payload)
    if body is None:
        return []   # an empty attendance log is a fact, not a failure
    return _decode_table(body, count, ATT_RECORD_SIZES, "attendance",
                         lambda chunk, size: _decode_punch(chunk, size, encoding),
                         _punches_are_plausible)


def _body(payload):
    """Strip the 4-byte total. ``None`` means "the table is empty"."""
    if len(payload) < 4:
        return None
    return payload[4:] or None


def _decode_user(chunk, size, encoding):
    if size == 28:
        uid, privilege, _pw, name, card, group_id, _tz, user_id = struct.unpack(
            "<HB5s8sIxBhI", chunk)
        group_id, user_id = str(group_id), str(user_id)
    else:
        uid, privilege, _pw, name, card, group_id, user_id = struct.unpack(
            "<HB8s24sIx7sx24s", chunk)
        group_id, user_id = _text(group_id, encoding), _text(user_id, encoding)
    # Old firmware leaves user_id blank and only fills the internal uid. Falling
    # back keeps such a device usable; it never *overrides* a user_id the device
    # did supply.
    return {"employee_id": user_id or str(uid), "name": _text(name, encoding),
            "privilege": privilege, "card": card, "group_id": group_id, "uid": uid}


def _decode_punch(chunk, size, encoding):
    if size == 8:
        uid, status, raw_time, punch = struct.unpack("<HB4sB", chunk)
        user_id = str(uid)
    elif size == 16:
        uid, raw_time, status, punch, _res, _wc = struct.unpack("<I4sBB2sI", chunk)
        user_id = str(uid)
    else:
        _uid, user_id, status, raw_time, punch, _space = struct.unpack(
            "<H24sB4sB8s", chunk)
        user_id = _text(user_id, encoding)
    return {"employee_id": user_id, "timestamp": decode_device_time(raw_time),
            "punch": punch, "status": status}


# How much of a table must look sane for the stride to be accepted.
#
# Not 100%: a terminal that lost its clock can hold a few genuinely corrupt
# records, and rejecting the whole table over them would refuse a decode that is
# in fact correct — the window filter drops those rows a moment later anyway.
# The threshold works because the two cases are nowhere near each other: at the
# wrong stride essentially *every* record is garbage, not a handful.
PLAUSIBLE_FRACTION = 0.9


def _fraction_sane(records, is_sane):
    if not records:
        return 1.0
    return sum(1 for r in records if is_sane(r)) / len(records)


# A device user id is never 0 or blank — enrolment starts at 1. Padding decoded
# at the wrong stride produces exactly those, which is what makes this a usable
# signal rather than a style check.
def _users_are_plausible(users):
    return _fraction_sane(
        users, lambda u: u["employee_id"] not in ("", "0")) >= PLAUSIBLE_FRACTION


def _punches_are_plausible(records):
    # Year 2000 is what an all-zero timestamp field decodes to, so it is the
    # tell for a misaligned read rather than a date worth supporting.
    def sane(record):
        return (record["employee_id"] not in ("", "0")
                and 2001 <= record["timestamp"].year <= 2100)

    return _fraction_sane(records, sane) >= PLAUSIBLE_FRACTION


def _candidate_sizes(available, count, candidates):
    """Record widths worth trying, best first.

    Divisibility alone cannot identify the width — 8, 16 and 40 all divide any
    40-byte-record table, so "smallest divisor wins" silently decodes every
    modern table at the wrong stride and yields garbage that still looks like
    records. The record COUNT from ``CMD_GET_FREE_SIZES`` is what settles it, so
    that is tried first.

    When the count is unavailable (older firmware refuses the command) the
    fallback is largest-first plus a plausibility check by the caller — because
    a too-large stride under-reads and is caught, whereas a too-small one
    over-reads into padding and produces convincing nonsense.
    """
    ordered = []
    if count:
        derived = available // count if available % count == 0 else None
        if derived in candidates:
            ordered.append(derived)
    ordered += [s for s in sorted(candidates, reverse=True)
                if s not in ordered and available % s == 0]
    return ordered


def _decode_table(body, count, candidates, what, decode, plausible):
    tried = _candidate_sizes(len(body), count, candidates)
    if not tried:
        raise ZKProtocolError(
            f"{len(body)} bytes of {what} data is not a whole number of records "
            f"(expected a multiple of {' or '.join(map(str, candidates))}). "
            f"The firmware is using a record layout this client does not know; "
            f"decoding it would silently produce wrong data.")

    for size in tried:
        records = [decode(body[offset:offset + size], size)
                   for offset in range(0, len(body) - size + 1, size)]
        if plausible(records):
            return records
        logger.debug("Rejected a %d-byte %s stride: implausible records", size, what)

    raise ZKProtocolError(
        f"{len(body)} bytes of {what} data did not decode sensibly at any "
        f"known record width ({', '.join(map(str, tried))}). Returning it would "
        f"mean filing invented punches against invented employees, so this is "
        f"an error.")


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------
class ZKReadOnlyClient:
    """A TCP session with a ZK terminal that can only read.

    Use as a context manager so the session is always closed — the terminal has
    a small connection pool and leaked sessions eventually lock out the very
    cron that leaked them.
    """

    def __init__(self, host, port=DEFAULT_PORT, *, timeout=DEFAULT_TIMEOUT,
                 comm_key=0, encoding="utf-8"):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.comm_key = int(comm_key or 0)
        self.encoding = encoding
        self._sock = None
        self._session_id = 0
        self._reply_id = USHRT_MAX - 1
        self._sizes = None
        self.connected = False

    # -- context manager ----------------------------------------------------
    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.disconnect()
        return False

    # -- session ------------------------------------------------------------
    def connect(self):
        try:
            self._sock = socket.create_connection((self.host, self.port),
                                                  timeout=self.timeout)
        except OSError as exc:
            raise ZKError(f"cannot reach {self.host}:{self.port} — {exc}") from exc
        self._sock.settimeout(self.timeout)
        self._session_id = 0
        self._reply_id = USHRT_MAX - 1
        self._sizes = None

        command, session, _reply, _data = self._send(CMD_CONNECT)
        self._session_id = session

        if command == CMD_ACK_UNAUTH:
            # Always attempt the auth exchange, even with key 0.
            #
            # ``CMD_ACK_UNAUTH`` does not mean "you need a key I have not been
            # given" — it means "do the auth step". The terminal at
            # 192.168.77.201 answers it and then accepts ``make_commkey(0,
            # session)`` perfectly happily, which is exactly what every working
            # ZK client does. Refusing here because ``comm_key`` is falsy turned
            # a device with NO key set into an unreachable one, and the error
            # message confidently blamed a key that does not exist.
            command, _s, _r, _d = self._send(
                CMD_AUTH, make_commkey(self.comm_key, self._session_id))
            if command != CMD_ACK_OK:
                self.disconnect()
                raise ZKAuthError(
                    f"{self.host} rejected the comm key "
                    f"{'(none configured — tried 0)' if not self.comm_key else ''}. "
                    f"If the terminal has one set, read it from Comm > Security > "
                    f"COMM Key and put it in BIOMETRIC_DEVICE_COMM_KEY.")
        elif command != CMD_ACK_OK:
            self.disconnect()
            raise ZKError(
                f"{self.host} refused the connection (reply {command}).")

        self.connected = True
        return self

    def disconnect(self):
        if self._sock is None:
            return
        try:
            if self.connected:
                self._send(CMD_EXIT)
        except Exception:  # noqa: BLE001 — a failed goodbye must not mask the real error
            logger.debug("CMD_EXIT to %s failed; closing anyway", self.host)
        finally:
            try:
                self._sock.close()
            finally:
                self._sock = None
                self.connected = False

    # -- reads --------------------------------------------------------------
    def device_time(self):
        """The terminal's own clock, naive, in its local wall time."""
        command, _s, _r, data = self._send(CMD_GET_TIME)
        if command != CMD_ACK_OK or len(data) < 4:
            raise ZKError("the terminal did not report its clock")
        return decode_device_time(data[:4])

    def sizes(self):
        """Enrolled-user and stored-record counts.

        Cached for the session: both table reads need the counts to pin their
        record width, and re-asking a terminal that has a handful of connection
        slots is a round trip for an answer that cannot change mid-session.
        """
        if self._sizes is None:
            self._sizes = self._read_sizes()
        return self._sizes

    def _read_sizes(self):
        command, _s, _r, data = self._send(CMD_GET_FREE_SIZES)
        if command != CMD_ACK_OK or len(data) < 80:
            return {}
        fields = struct.unpack("<20i", data[:80])
        return {"users": fields[4], "fingers": fields[6], "records": fields[8],
                "cards": fields[12], "users_capacity": fields[15],
                "records_capacity": fields[16]}

    def users(self):
        # The count comes first because it is what pins the record width; see
        # _candidate_sizes. A device that refuses CMD_GET_FREE_SIZES falls back
        # to inference rather than failing.
        return parse_users(self._read(CMD_USERTEMP_RRQ, FCT_USER), self.encoding,
                           count=self.sizes().get("users"))

    def attendance(self):
        return parse_attendance(self._read(CMD_ATTLOG_RRQ, FCT_ATTLOG), self.encoding,
                                count=self.sizes().get("records"))

    # -- transport ----------------------------------------------------------
    def _send(self, command, data=b""):
        if command not in READ_ONLY_COMMANDS:
            # Defence against a future edit, not against the current code.
            raise ZKError(
                f"command {command} is not in READ_ONLY_COMMANDS. This client "
                f"is deliberately incapable of writing to the terminal.")
        if self._sock is None:
            raise ZKError("not connected")
        # build_packet transmits reply_id + 1 and checksums the pre-increment
        # value — the convention the firmware validates against. See its
        # docstring; getting this wrong makes the terminal ignore us silently.
        packet = build_packet(command, self._session_id, self._reply_id, data)
        try:
            self._sock.sendall(wrap_tcp(packet))
        except OSError as exc:
            raise ZKError(f"send to {self.host} failed: {exc}") from exc
        return self._recv_packet()

    def _recv_exact(self, count):
        buffer = bytearray()
        while len(buffer) < count:
            try:
                chunk = self._sock.recv(count - len(buffer))
            except socket.timeout as exc:
                raise ZKError(
                    f"{self.host} stopped responding after {len(buffer)} of "
                    f"{count} bytes") from exc
            except OSError as exc:
                raise ZKError(f"read from {self.host} failed: {exc}") from exc
            if not chunk:
                raise ZKError(
                    f"{self.host} closed the connection after {len(buffer)} of "
                    f"{count} bytes")
            buffer += chunk
        return bytes(buffer)

    def _recv_packet(self):
        """One framed reply: (command, session, reply_id, data).

        Reads the 8-byte TCP header, then *exactly* the length it declares.
        Guessing at buffer sizes — the usual approach in ZK client code — is how
        a large attendance transfer ends up silently truncated, and a truncated
        attendance log looks exactly like an employee who did not come to work.
        """
        head = self._recv_exact(8)
        magic, size = head[:4], struct.unpack("<I", head[4:8])[0]
        if magic != ZK_TCP_MAGIC:
            raise ZKProtocolError(
                f"reply from {self.host} is not ZK-framed (got {head.hex()}); "
                f"something other than a terminal is listening on port {self.port}")
        if size < 8 or size > MAX_TRANSFER_BYTES:
            raise ZKProtocolError(
                f"{self.host} declared an implausible packet size of {size} bytes")
        body = self._recv_exact(size)
        command, _checksum, session, reply = struct.unpack("<4H", body[:8])
        # Track the device's own counter rather than our guess at it: the
        # terminal is authoritative about where the conversation has got to.
        self._reply_id = reply
        return command, session, reply, body[8:]

    # -- data transfer ------------------------------------------------------
    def _read(self, command, fct):
        """Fetch a whole table, over whichever transfer protocol this firmware speaks."""
        request = struct.pack("<bhii", 1, command, fct, 0)
        reply, _s, _r, data = self._send(CMD_DATA_WRRQ, request)

        if reply == CMD_DATA:
            # Small table — the device skipped the buffer dance entirely.
            return data
        if reply == CMD_PREPARE_DATA:
            return self._drain_prepared(data)
        if reply != CMD_ACK_OK:
            # Older firmware has no buffer protocol; ask for the table directly.
            return self._read_direct(command)

        if len(data) < 5:
            raise ZKProtocolError(
                f"{self.host} acknowledged the read but did not say how much "
                f"data it has")
        size = struct.unpack("<I", data[1:5])[0]
        if size > MAX_TRANSFER_BYTES:
            raise ZKProtocolError(
                f"{self.host} offered {size} bytes, which is not a credible "
                f"table size")

        chunks, start = [], 0
        while start < size:
            length = min(MAX_CHUNK, size - start)
            chunks.append(self._read_chunk(start, length))
            start += length
        self._send(CMD_FREE_DATA)

        table = b"".join(chunks)
        # The same rule as _drain_prepared, applied to the buffered path: a
        # device that returns a short chunk must not yield a short table that
        # merely looks like a quiet fortnight.
        if len(table) != size:
            raise ZKError(
                f"{self.host} offered {size} bytes and returned {len(table)}. "
                f"A partial table is not usable — the missing punches would be "
                f"indistinguishable from employees who were not there.")
        return table

    def _read_direct(self, command):
        """The pre-buffer-protocol path: ask for the table, take what comes.

        Firmware old enough to lack ``CMD_DATA_WRRQ`` is still in service on
        plenty of terminals, and it is the same firmware least likely to be
        replaced — so this is a real path, not a courtesy.
        """
        reply, _s, _r, data = self._send(command)
        if reply == CMD_PREPARE_DATA:
            return self._drain_prepared(data)
        if reply in (CMD_DATA, CMD_ACK_DATA):
            return data
        raise ZKProtocolError(
            f"{self.host} refused a direct read of table {command} "
            f"(reply {reply}), and does not support the buffered protocol "
            f"either. This firmware is not supported.")

    def _read_chunk(self, start, length):
        reply, _s, _r, data = self._send(CMD_READ_BUFFER,
                                         struct.pack("<ii", start, length))
        if reply == CMD_DATA:
            return data
        if reply == CMD_PREPARE_DATA:
            return self._drain_prepared(data)
        raise ZKProtocolError(
            f"{self.host} refused to return bytes {start}..{start + length} "
            f"(reply {reply})")

    def _drain_prepared(self, header):
        """Collect a PREPARE_DATA transfer: N data packets, then an ack.

        Loops on the declared size rather than on "until an ack arrives", so a
        device that acks early cannot pass off a short read as a complete one.
        """
        if len(header) < 4:
            raise ZKProtocolError(f"{self.host} sent an empty data preamble")
        expected = struct.unpack("<I", header[:4])[0]
        if expected > MAX_TRANSFER_BYTES:
            raise ZKProtocolError(
                f"{self.host} announced {expected} bytes, which is not credible")

        buffer = bytearray()
        while len(buffer) < expected:
            command, _s, _r, payload = self._recv_packet()
            if command == CMD_DATA:
                buffer += payload
            elif command in (CMD_ACK_OK, CMD_ACK_DATA):
                break
            else:
                raise ZKProtocolError(
                    f"{self.host} interrupted the transfer with reply {command}")

        if len(buffer) < expected:
            raise ZKError(
                f"{self.host} sent {len(buffer)} of {expected} announced bytes. "
                f"A partial table is not usable: missing punches are "
                f"indistinguishable from absent employees, so this is an error "
                f"rather than a partial success.")

        # Consume the trailing ack if one is still queued, so the next command
        # does not read it as its own reply.
        if len(buffer) == expected:
            try:
                self._sock.settimeout(2)
                self._recv_packet()
            except Exception:  # noqa: BLE001 — no trailing ack is equally valid
                pass
            finally:
                self._sock.settimeout(self.timeout)
        return bytes(buffer[:expected])
