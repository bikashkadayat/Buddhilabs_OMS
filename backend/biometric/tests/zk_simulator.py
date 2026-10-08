"""A minimal ZK terminal, in a thread, for testing the collector's wire code.

The real terminal at 192.168.77.201 accepts TCP but does not complete a
handshake from a developer machine, so the collector's protocol layer cannot be
exercised against it from here. Shipping several hundred lines of wire code with
no execution behind it would be indefensible, so this simulator exists to run
it: it speaks the same framing, the same command codes and the same record
layouts, and it can be told to behave like each of the firmware variants the
client claims to support.

It is a **test double, not a reference implementation** — it implements only the
commands the client sends, and it is deliberately strict about them so a client
bug shows up as a failure here rather than as a mystery in production.
"""
import socket
import struct
import threading

from biometric.device_probe import build_packet, wrap_tcp
from biometric.zk_client import (
    CMD_ACK_ERROR,
    CMD_ACK_OK,
    CMD_ACK_UNAUTH,
    CMD_ATTLOG_RRQ,
    CMD_AUTH,
    CMD_CONNECT,
    CMD_DATA,
    CMD_DATA_WRRQ,
    CMD_EXIT,
    CMD_FREE_DATA,
    CMD_GET_FREE_SIZES,
    CMD_GET_TIME,
    CMD_GET_VERSION,
    CMD_OPTIONS_RRQ,
    CMD_PREPARE_DATA,
    CMD_READ_BUFFER,
    CMD_USERTEMP_RRQ,
    FCT_USER,
    ZK_TCP_MAGIC,
    make_commkey,
)

SESSION_ID = 0x1234


def encode_device_time(dt):
    """Inverse of ``zk_client.decode_device_time`` — the packed-integer clock."""
    days = ((dt.year - 2000) * 12 + (dt.month - 1)) * 31 + (dt.day - 1)
    return ((days * 24 + dt.hour) * 60 + dt.minute) * 60 + dt.second


def pack_user(uid, user_id, name, privilege=0, card=0, group_id="1"):
    return struct.pack(
        "<HB8s24sIx7sx24s", uid, privilege, b"", name.encode()[:24],
        card, group_id.encode()[:7], str(user_id).encode()[:24])


def pack_user_small(uid, user_id, name, privilege=0, card=0):
    """The 28-byte layout used by older firmware."""
    return struct.pack("<HB5s8sIxBhI", uid, privilege, b"", name.encode()[:8],
                       card, 1, 0, int(user_id))


def pack_attendance(user_id, dt, punch=0, status=1, uid=0):
    return struct.pack("<H24sB4sB8s", uid, str(user_id).encode()[:24], status,
                       struct.pack("<I", encode_device_time(dt)), punch, b"")


def pack_attendance_16(user_id, dt, punch=0, status=1):
    """The compact layout: an integer user id, no name field."""
    return struct.pack("<I4sBB2sI", int(user_id),
                       struct.pack("<I", encode_device_time(dt)),
                       status, punch, b"", 0)


def pack_attendance_8(user_id, dt, punch=0, status=1):
    """The oldest layout. Ambiguous by length alone — 8 divides everything."""
    return struct.pack("<HB4sB", int(user_id), status,
                       struct.pack("<I", encode_device_time(dt)), punch)


def table(records):
    """A transfer body: 4-byte total, then the fixed-width records."""
    body = b"".join(records)
    return struct.pack("<I", len(body)) + body


class FakeZKDevice:
    """A terminal that answers on 127.0.0.1.

    ``transfer_mode`` selects which of the three data-transfer protocols the
    client has to cope with:

    * ``"buffer"``  — CMD_DATA_WRRQ + chunked CMD_READ_BUFFER (modern firmware)
    * ``"prepare"`` — CMD_DATA_WRRQ answered with PREPARE_DATA + data packets
    * ``"direct"``  — CMD_DATA_WRRQ refused; the table is fetched by its own
                      command (pre-buffer-protocol firmware)
    """

    def __init__(self, users=b"", attendance=b"", *, comm_key=0,
                 transfer_mode="buffer", clock=None, sizes=None,
                 chunk_bytes=None, garbage=False, announce_extra=0,
                 short_chunk=0, firmware=None, options=None):
        # Device identity for CMD_GET_VERSION / CMD_OPTIONS_RRQ. Unset means
        # the firmware refuses, which is what older terminals do.
        self.firmware = firmware
        self.options = options or {}
        self.users = users
        self.attendance = attendance
        self.comm_key = comm_key
        self.transfer_mode = transfer_mode
        self.clock = clock
        self.sizes = sizes or {"users": 0, "records": 0}
        self.chunk_bytes = chunk_bytes
        self.garbage = garbage
        # Announce more bytes than are actually sent — a firmware bug, or a
        # transfer cut short by the network. The client must treat it as a
        # failure, never as a shorter-than-expected success.
        self.announce_extra = announce_extra
        # Return fewer bytes than a CMD_READ_BUFFER asked for — the buffered
        # path's version of a truncated transfer.
        self.short_chunk = short_chunk

        self.commands = []          # every command code received, in order
        self.authenticated = comm_key == 0
        self._buffer = b""          # the table staged for CMD_READ_BUFFER

        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self.host, self.port = self._server.getsockname()
        self._thread = None
        self._stop = threading.Event()

    # -- lifecycle ----------------------------------------------------------
    def __enter__(self):
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        try:
            self._server.close()
        except OSError:
            pass
        if self._thread:
            self._thread.join(timeout=5)
        return False

    # -- serving ------------------------------------------------------------
    def _serve(self):
        # Poll rather than block: closing a listening socket from another
        # thread does not reliably wake a blocked accept() on Linux, so a long
        # timeout here costs every protocol test that many seconds at teardown.
        self._server.settimeout(0.05)
        while not self._stop.is_set():
            try:
                conn, _addr = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                try:
                    self._session(conn)
                except OSError:
                    pass

    def _session(self, conn):
        conn.settimeout(5)
        while not self._stop.is_set():
            packet = self._read_packet(conn)
            if packet is None:
                return
            command, _session, reply, data = packet
            self.commands.append(command)
            if not self._dispatch(conn, command, reply, data):
                return

    def _read_packet(self, conn):
        head = self._read_exact(conn, 8)
        if head is None:
            return None
        assert head[:4] == ZK_TCP_MAGIC, "the client sent a badly framed packet"
        size = struct.unpack("<I", head[4:8])[0]
        body = self._read_exact(conn, size)
        if body is None:
            return None
        command, _cks, session, reply = struct.unpack("<4H", body[:8])
        return command, session, reply, body[8:]

    @staticmethod
    def _read_exact(conn, count):
        buffer = b""
        while len(buffer) < count:
            try:
                chunk = conn.recv(count - len(buffer))
            except (OSError, socket.timeout):
                return None
            if not chunk:
                return None
            buffer += chunk
        return buffer

    def _reply(self, conn, command, reply_id, data=b""):
        if self.garbage:
            conn.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
            return
        conn.sendall(wrap_tcp(build_packet(command, SESSION_ID, reply_id, data)))

    # -- commands -----------------------------------------------------------
    def _dispatch(self, conn, command, reply, data):
        if command == CMD_CONNECT:
            self._reply(conn, CMD_ACK_UNAUTH if self.comm_key else CMD_ACK_OK, reply)
            return True

        if command == CMD_AUTH:
            ok = data == make_commkey(self.comm_key, SESSION_ID)
            self.authenticated = ok
            self._reply(conn, CMD_ACK_OK if ok else CMD_ACK_ERROR, reply)
            return True

        if not self.authenticated:
            self._reply(conn, CMD_ACK_UNAUTH, reply)
            return True

        if command == CMD_EXIT:
            self._reply(conn, CMD_ACK_OK, reply)
            return False

        if command == CMD_GET_TIME:
            payload = struct.pack("<I", encode_device_time(self.clock)) if self.clock else b""
            self._reply(conn, CMD_ACK_OK if self.clock else CMD_ACK_ERROR, reply, payload)
            return True

        if command == CMD_GET_FREE_SIZES:
            fields = [0] * 20
            fields[4] = self.sizes.get("users", 0)
            fields[8] = self.sizes.get("records", 0)
            self._reply(conn, CMD_ACK_OK, reply, struct.pack("<20i", *fields))
            return True

        if command == CMD_GET_VERSION:
            payload = (self.firmware.encode() + b"\x00") if self.firmware else b""
            self._reply(conn, CMD_ACK_OK if self.firmware else CMD_ACK_ERROR, reply, payload)
            return True

        if command == CMD_OPTIONS_RRQ:
            name = data.split(b"\x00")[0].decode()
            if name in self.options:
                self._reply(conn, CMD_ACK_OK, reply,
                            f"{name}={self.options[name]}".encode() + b"\x00")
            else:
                self._reply(conn, CMD_ACK_ERROR, reply)
            return True

        if command == CMD_DATA_WRRQ:
            return self._serve_wrrq(conn, reply, data)

        if command == CMD_READ_BUFFER:
            start, length = struct.unpack("<ii", data[:8])
            if self.short_chunk:
                length = max(length - self.short_chunk, 0)
            self._reply(conn, CMD_DATA, reply, self._buffer[start:start + length])
            return True

        if command == CMD_FREE_DATA:
            self._buffer = b""
            self._reply(conn, CMD_ACK_OK, reply)
            return True

        if command in (CMD_USERTEMP_RRQ, CMD_ATTLOG_RRQ):
            table_bytes = self.users if command == CMD_USERTEMP_RRQ else self.attendance
            self._send_prepared(conn, reply, table_bytes)
            return True

        self._reply(conn, CMD_ACK_ERROR, reply)
        return True

    def _serve_wrrq(self, conn, reply, data):
        _flag, table_command, fct, _ext = struct.unpack("<bhii", data[:11])
        table_bytes = self.users if fct == FCT_USER else self.attendance

        if self.transfer_mode == "direct":
            # Firmware that has never heard of the buffer protocol.
            self._reply(conn, CMD_ACK_ERROR, reply)
            return True
        if self.transfer_mode == "prepare":
            self._send_prepared(conn, reply, table_bytes)
            return True

        self._buffer = table_bytes
        self._reply(conn, CMD_ACK_OK, reply,
                    b"\x00" + struct.pack("<I", len(table_bytes)))
        return True

    def _send_prepared(self, conn, reply, table_bytes):
        """PREPARE_DATA, then N data packets, then the ack."""
        self._reply(conn, CMD_PREPARE_DATA, reply,
                    struct.pack("<I", len(table_bytes) + self.announce_extra))
        step = self.chunk_bytes or max(len(table_bytes), 1)
        for start in range(0, len(table_bytes), step):
            self._reply(conn, CMD_DATA, reply, table_bytes[start:start + step])
        self._reply(conn, CMD_ACK_OK, reply)
