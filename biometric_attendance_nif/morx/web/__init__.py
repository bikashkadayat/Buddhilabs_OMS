"""Read-only attendance dashboard.

Separate from the collector on purpose: it opens the same `data/` output but
never writes to it and never touches a device, so it is safe to run against a
live deployment. Standard library only — the dashboard must not add a
dependency to a service whose whole point is running unattended on a small box.
"""

from __future__ import annotations

from .server import serve

__all__ = ["serve"]
