"""Strictly read-only reachability probe for a ZK biometric terminal.

Phase 12. Answers one question before go-live: *is the terminal actually there,
and does it speak the protocol the collector expects?*

WHY THIS DOES NOT USE pyzk
--------------------------
An earlier version of this note claimed ``pyzk.ZK.connect()`` calls
``disable_device()``. **That is not true** — verified by reading the installed
library: neither ``connect()``, ``get_users()`` nor ``get_attendance()`` touches
it. The habit belongs to pyzk's *examples*, which commonly disable a terminal
around a read, and disabling one during working hours does lock its keypad and
stop staff punching in. But the library does not require it, and the claim was
wrong.

The real reasons stand on their own:

* **No dependency on the path that probes a device before go-live.** An audit
  tool should not need a package installed to answer "is it there?".
* **Read-only by construction.** pyzk can clear the attendance log, delete users
  and unlock the door. This module can send exactly two commands —
  ``CMD_CONNECT`` and ``CMD_EXIT`` — because those are the only two it knows.

pyzk is still available as an explicit fallback driver for the *collector*; see
``biometric/pyzk_driver.py``.

WHAT A RESULT MEANS
-------------------
``tcp_open`` alone proves very little: firewalls, SYN proxies and NAT helpers
all accept connections on behalf of hosts that are not there. The verdict is
therefore based on the *protocol reply*, and a control probe against a
deliberately unused address in the same subnet is run alongside it so a
gateway answering for everything is detected rather than believed.
"""
import socket
import struct

CMD_CONNECT = 1000
CMD_EXIT = 1001
CMD_ACK_OK = 2000
CMD_ACK_ERROR = 2001
CMD_ACK_DATA = 2002
CMD_ACK_UNAUTH = 2005

REPLY_NAMES = {
    CMD_ACK_OK: "CMD_ACK_OK — connection accepted",
    CMD_ACK_ERROR: "CMD_ACK_ERROR — device refused the command",
    CMD_ACK_DATA: "CMD_ACK_DATA",
    CMD_ACK_UNAUTH: "CMD_ACK_UNAUTH — a comm key is configured on the device",
}

ZK_TCP_MAGIC = b"\x50\x50\x82\x7d"
USHRT_MAX = 65535
DEFAULT_PORT = 4370
DEFAULT_TIMEOUT = 6


# ---------------------------------------------------------------------------
# wire format
# ---------------------------------------------------------------------------
def _checksum(payload):
    """ZK's 16-bit ones-complement checksum."""
    length, total, index = len(payload), 0, 0
    while length > 1:
        total += struct.unpack("H", payload[index:index + 2])[0]
        index += 2
        length -= 2
        if total > USHRT_MAX:
            total -= USHRT_MAX
    if length:
        total += payload[index]
    while total > USHRT_MAX:
        total -= USHRT_MAX
    total = ~total
    while total < 0:
        total += USHRT_MAX
    return struct.pack("H", total)


def build_packet(command, session_id=0, reply_id=0, data=b""):
    """One ZK command packet.

    ``reply_id`` is the **pre-increment** counter. The checksum is computed over
    a header carrying that value, and the header actually transmitted carries
    ``reply_id + 1``.

    That looks wrong, and read as arithmetic it is: the checksum does not match
    the bytes it accompanies. But it is what the firmware expects, and it is not
    a guess — it was established by capturing a working session against the
    terminal at 192.168.77.201 and diffing it against ours:

        working  5050827d08000000 e803 17fc 0000 0000
        ours     5050827d08000000 e803 16fc 0000 0000
                                       ^^^^ checksum, off by exactly one

    Every other byte was identical, and the device answered the first and
    ignored the second. The off-by-one is the difference between computing the
    checksum over ``reply_id`` (65534, the initial value) and over the
    transmitted ``reply_id + 1`` (0, after wrapping).

    So the device validates the checksum against the pre-increment header. Any
    client that computes a "correct" checksum over the bytes it sends is
    silently ignored — which presents as a dead device, not a protocol error,
    and is exactly how this cost two wrong diagnoses.
    """
    header = struct.pack("<4H", command, 0, session_id, reply_id)
    checksum = _checksum(header + data)
    transmitted_reply = (reply_id + 1) % USHRT_MAX
    return struct.pack("<2H", command, 0)[:2] + checksum + struct.pack(
        "<2H", session_id, transmitted_reply) + data


def wrap_tcp(packet):
    """Prefix the 8-byte TCP framing header.

    The second short is 0x7D82, **not** 0x827D. Packed little-endian the pair
    lands on the wire as ``50 50 82 7d`` — which is ``ZK_TCP_MAGIC``, the value
    this module already expects on the way *in*. Writing the magic as it reads
    on the wire produces a byte-swapped header, the terminal drops the packet
    without replying, and the symptom is a probe that reports "port open but not
    speaking ZK" — i.e. it looks like a device fault rather than a client bug.
    """
    return struct.pack("<HHI", 0x5050, 0x7D82, len(packet)) + packet


# ---------------------------------------------------------------------------
# probes
# ---------------------------------------------------------------------------
def probe_tcp(host, port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT):
    result = {"transport": "tcp", "tcp_open": False, "protocol_reply": None,
              "reply_command": None, "session_id": None, "error": None}
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    result["tcp_open"] = True
    try:
        sock.settimeout(timeout)
        sock.send(wrap_tcp(build_packet(CMD_CONNECT)))
        raw = sock.recv(1024)
        if not raw:
            result["error"] = "device closed the connection without replying"
        elif raw[:4] != ZK_TCP_MAGIC:
            result["error"] = (f"reply is not ZK-framed (got {raw[:8].hex()}); "
                               f"something other than a terminal is listening")
        else:
            command, _cks, session, reply = struct.unpack("<4H", raw[8:16])
            result.update(protocol_reply=REPLY_NAMES.get(command, f"unknown ({command})"),
                          reply_command=command, session_id=session)
            # Close the session politely; a terminal has a small connection pool
            # and leaking sessions would eventually lock out the real collector.
            sock.send(wrap_tcp(build_packet(CMD_EXIT, session, reply + 1)))
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        sock.close()
    return result


def probe_udp(host, port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT):
    result = {"transport": "udp", "protocol_reply": None, "reply_command": None,
              "session_id": None, "error": None}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(build_packet(CMD_CONNECT), (host, port))
        raw, _addr = sock.recvfrom(1024)
        command, _cks, session, reply = struct.unpack("<4H", raw[:8])
        result.update(protocol_reply=REPLY_NAMES.get(command, f"unknown ({command})"),
                      reply_command=command, session_id=session)
        sock.sendto(build_packet(CMD_EXIT, session, reply + 1), (host, port))
    except socket.timeout:
        result["error"] = "no reply (device may be TCP-only, or UDP is filtered)"
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        sock.close()
    return result


def probe_control(host, port=DEFAULT_PORT, timeout=3):
    """Same TCP check against a deliberately unused neighbour address.

    If this reports open too, the path is answering for addresses that do not
    exist and NOTHING about the real probe can be trusted. Without this control
    a friendly firewall reads as a healthy terminal.
    """
    parts = host.split(".")
    if len(parts) != 4:
        return {"skipped": "host is not an IPv4 address"}
    last = int(parts[3])
    parts[3] = str(last - 1 if last > 1 else last + 1)
    control_host = ".".join(parts)
    try:
        sock = socket.create_connection((control_host, port), timeout=timeout)
        sock.close()
        return {"host": control_host, "tcp_open": True,
                "warning": "A NON-EXISTENT address also accepted a connection. "
                           "Something on the path answers for everything; the "
                           "main probe proves nothing."}
    except Exception:  # noqa: BLE001 -- refusal is the expected, healthy result
        return {"host": control_host, "tcp_open": False,
                "note": "Control address correctly refused — the path is honest."}


def verdict(tcp, udp, control):
    """One sentence an operator can act on."""
    if control.get("tcp_open"):
        return ("INCONCLUSIVE — a non-existent address on the same subnet also "
                "accepted a connection, so reachability cannot be trusted.")
    if tcp.get("reply_command") == CMD_ACK_OK or udp.get("reply_command") == CMD_ACK_OK:
        return "READY — the terminal answered the ZK handshake."
    if tcp.get("reply_command") == CMD_ACK_UNAUTH or udp.get("reply_command") == CMD_ACK_UNAUTH:
        return ("REACHABLE, AUTH REQUIRED — a comm key is set on the terminal. "
                "The collector must be configured with it.")
    if tcp.get("tcp_open"):
        return ("REACHABLE BUT NOT SPEAKING ZK — port 4370 accepts connections "
                "but the handshake was refused. Likely causes: a comm key, a "
                "firmware in a different protocol mode, or a firewall proxying "
                "the port. Confirm with the collector host, which is the only "
                "machine that has to be able to reach it.")
    return "UNREACHABLE — no TCP connection to port 4370."


def full_probe(host, port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT):
    tcp = probe_tcp(host, port, timeout)
    udp = probe_udp(host, port, timeout)
    control = probe_control(host, port)
    return {"host": host, "port": port, "tcp": tcp, "udp": udp,
            "control": control, "verdict": verdict(tcp, udp, control)}
