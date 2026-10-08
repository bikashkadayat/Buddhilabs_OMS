"""Domain objects.

The point of this module is to stop `pyzk` objects from leaking into the rest
of the code. Everything past the device layer speaks Employee/AttendanceRecord,
so swapping the driver (or faking it in tests) touches only device.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Device-reported punch codes. Firmware varies, so unknown codes are kept as-is
# rather than dropped — never lose a record just because we can't label it.
PUNCH_LABELS = {
    0: "check_in",
    1: "check_out",
    2: "break_out",
    3: "break_in",
    4: "overtime_in",
    5: "overtime_out",
}


@dataclass(frozen=True)
class Employee:
    """A user enrolled on a device."""

    employee_id: str
    name: str
    privilege: int = 0
    card: int = 0
    group_id: str = ""
    device: str = ""

    @classmethod
    def from_zk(cls, user: Any, device: str) -> Employee:
        return cls(
            employee_id=str(user.user_id).strip(),
            name=(user.name or "").strip(),
            privilege=int(getattr(user, "privilege", 0) or 0),
            card=int(getattr(user, "card", 0) or 0),
            group_id=str(getattr(user, "group_id", "") or ""),
            device=device,
        )


@dataclass(frozen=True)
class AttendanceRecord:
    """A single punch."""

    employee_id: str
    name: str
    timestamp: datetime
    status: int
    punch: int
    source: str  # HISTORY (backlog pull) or LIVE (streamed)
    device: str

    @classmethod
    def from_zk(cls, log: Any, name: str, source: str, device: str) -> AttendanceRecord:
        return cls(
            employee_id=str(log.user_id).strip(),
            name=name,
            timestamp=log.timestamp,
            status=int(log.status or 0),
            punch=int(log.punch or 0),
            source=source,
            device=device,
        )

    @property
    def punch_label(self) -> str:
        return PUNCH_LABELS.get(self.punch, f"unknown_{self.punch}")

    @property
    def dedup_key(self) -> tuple[str, str, str, int]:
        """Identity of a punch.

        A device replays its whole backlog on every `get_attendance()`, and the
        same punch can also arrive live. Deduping on
        (device, employee, timestamp, punch) is what makes re-running the
        collector safe — omit `source`, since the same event seen via HISTORY
        and via LIVE must collapse to one row.
        """
        return (self.device, self.employee_id, self.timestamp.isoformat(sep=" "), self.punch)
