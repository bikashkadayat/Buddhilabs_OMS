"""Parsers for the ZKTeco iClock / PUSH (ADMS) protocol.

The terminal at 192.168.77.201 is a ZKTeco **ZLM60** (embedded Linux, MIPS,
kernel 3.10.14 — read from its own telnet banner). That platform ships in PUSH
mode: instead of answering SDK calls on port 4370, the device *dials out* to a
configured server over plain HTTP and posts what it has. Port 4370 stays open
but unserviced, which is exactly the behaviour observed — a correctly framed
``CMD_CONNECT`` is accepted and then never answered.

WHY THIS IS THE BETTER PATH ANYWAY
----------------------------------
The requirement is that a fingerprint reaches the OMS automatically, with no
manual sync, surviving restarts and network outages. PUSH gives that directly:

* **Real time.** The device posts a punch seconds after the finger lands,
  rather than up to a polling interval later.
* **Outage-proof without any code from us.** The device keeps its own buffer
  and re-posts until the server answers ``OK``. A server restart, a network
  drop or a power cut is absorbed by the terminal's own retry loop.
* **No inbound reachability needed.** The server never has to open a socket to
  the device, so device-side firewalls and NAT stop mattering.

THE ONE RULE THAT MATTERS
-------------------------
``OK`` is the device's signal to **delete its copy**. So the server must reply
OK only once the punches are durably committed. Anything else — unknown serial,
malformed body, a database error — must return non-OK so the terminal keeps
the records and tries again. Cheerfully acknowledging data we did not store is
the one bug in this protocol that silently destroys attendance.

IDENTITY
--------
``PIN`` in an ATTLOG line is the terminal's own user id. It travels into
``AttendancePunch.employee_device_id`` unaltered — no renumbering, no mapping
table, no OMS-side id generation.
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ATTLOG columns, in the order the firmware emits them. Only the first four
# are dependable across firmware revisions; the rest are optional.
#   PIN  DateTime  Status  Verify  WorkCode  Reserved1  Reserved2
ATTLOG_MIN_FIELDS = 2

TIMESTAMP_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M")


class PushParseError(Exception):
    """The body was not in a form this parser recognises."""


def _int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_timestamp(raw):
    """Device-local wall clock, returned **naive**.

    Naive is not an oversight. The terminal has no idea what zone it is in, so
    the only honest reading is the wall clock; the caller attaches
    ``BiometricDevice.device_timezone``, which is the only place that knows.
    """
    text = (raw or "").strip()
    for fmt in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise PushParseError(f"unrecognised timestamp {raw!r}")


def parse_attlog(body):
    """Tab-separated attendance records -> punch dicts.

    Returns ``(records, errors)``. A malformed line is collected rather than
    raised on: one bad row must not cost the other 199 in the same POST, and
    the caller needs the error list to decide whether the batch as a whole can
    be acknowledged.
    """
    records, errors = [], []
    for number, line in enumerate(body.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        fields, offset = _split_line(line)
        if fields is None:
            errors.append(f"line {number}: expected at least a PIN and a timestamp")
            continue

        pin = fields[0].strip()
        if not pin:
            errors.append(f"line {number}: blank PIN")
            continue
        try:
            timestamp = parse_timestamp(fields[1])
        except PushParseError as exc:
            errors.append(f"line {number}: {exc}")
            continue

        # `offset` is where the remaining columns start. On tab-separated lines
        # that is 2; on whitespace-separated ones the timestamp consumed two
        # tokens, so it is 3.
        records.append({
            "employee_id": pin,          # the device's own id, untouched
            "timestamp": timestamp,
            "punch": _int(fields[offset]) if len(fields) > offset else 0,
            "status": _int(fields[offset + 1]) if len(fields) > offset + 1 else 0,
        })
    return records, errors


def _split_line(line):
    """Split an ATTLOG line into columns. Returns ``(fields, value_offset)``.

    Tabs are the documented separator, but some firmware emits spaces — and
    that case is not simply "split on whitespace", because the timestamp itself
    contains a space. ``2026-08-06 09:15:04`` becomes two tokens, so a naive
    whitespace split shifts every subsequent column by one and quietly reads the
    punch type out of the seconds field.
    """
    fields = line.split("\t")
    if len(fields) >= ATTLOG_MIN_FIELDS:
        return fields, 2

    fields = line.split()
    if len(fields) >= 3:
        # Rejoin the date and time into a single timestamp column.
        return [fields[0], f"{fields[1]} {fields[2]}", *fields[3:]], 2
    if len(fields) == ATTLOG_MIN_FIELDS:
        return fields, 2
    return None, 0


def parse_operlog(body):
    """OPERLOG payload -> (users, other_line_count).

    The device reports enrolment changes here as ``USER PIN=..`` lines mixed in
    with operator-audit lines (``OPLOG``), fingerprint templates (``FP``) and
    others. Only USER lines are of interest; the rest are counted so the
    acknowledgement can be honest about how much was accepted.
    """
    users, others = [], 0
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.upper().startswith("USER "):
            others += 1
            continue

        attributes = {}
        for chunk in line[5:].split("\t"):
            if "=" in chunk:
                key, _, value = chunk.partition("=")
                attributes[key.strip().lower()] = value.strip()

        pin = attributes.get("pin", "").strip()
        if not pin:
            others += 1
            continue
        users.append({
            "employee_id": pin,
            "name": attributes.get("name", ""),
            "privilege": _int(attributes.get("pri"), 0),
            "card": _int(attributes.get("card"), 0),
            "group_id": attributes.get("grp", "") or "",
        })
    return users, others


def handshake_response(serial, *, stamp="0", op_stamp="0", timezone_offset="5.75",
                       delay=10, error_delay=30, realtime=True):
    """The config block a terminal expects from its first ``GET /iclock/cdata``.

    Field notes, because the names are not self-explanatory and getting them
    wrong produces a device that silently never sends anything:

    * ``Stamp`` / ``OpStamp`` — the device's resume cursors. Echoing what it
      sent (or 0 on a first contact) makes it replay from there. Dedup makes a
      replay free, so erring toward re-sending is the safe direction.
    * ``Delay`` — seconds between ``getrequest`` polls.
    * ``ErrorDelay`` — seconds to wait after a failed call before retrying.
    * ``TransFlag`` — a bitmask of which tables to transmit. This value enables
      attendance, operation log and user data; a zero here is the classic cause
      of "the device is online but nothing arrives".
    * ``Realtime=1`` — post each punch as it happens rather than in batches.
      This is the setting that makes "no manual sync" literally true.
    """
    return "\n".join([
        f"GET OPTION FROM: {serial}",
        f"Stamp={stamp}",
        f"OpStamp={op_stamp}",
        f"ErrorDelay={error_delay}",
        f"Delay={delay}",
        "TransTimes=00:00;14:05",
        "TransInterval=1",
        "TransFlag=1111000000",
        f"TimeZone={timezone_offset}",
        f"Realtime={1 if realtime else 0}",
        "Encrypt=0",
    ]) + "\n"


def timezone_offset_hours(zone, moment):
    """The device's TimeZone field: UTC offset in hours, as the firmware wants it.

    Asia/Kathmandu is +05:45, i.e. 5.75 — a non-integer offset, which is
    precisely why this is computed rather than hardcoded to an int.
    """
    offset = moment.astimezone(zone).utcoffset()
    if offset is None:
        return "0"
    hours = offset.total_seconds() / 3600
    return f"{hours:g}"
