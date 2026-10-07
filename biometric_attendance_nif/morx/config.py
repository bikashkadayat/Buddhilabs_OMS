"""Configuration.

Devices are never hardcoded. Two ways to declare them:

  1. One device  -> MORX_DEVICE_HOST=... in the environment / .env
  2. Many devices -> MORX_DEVICES_FILE=config/devices.json

Anything not set falls back to a documented default, so the collector runs
against a brand-new terminal by changing one variable.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class ConfigError(Exception):
    """Configuration is missing or malformed — not recoverable at runtime."""


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key).lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{key} must be a boolean, got {raw!r}")


@dataclass(frozen=True)
class DeviceConfig:
    """Everything needed to reach one terminal.

    The knobs below exist because ZK clones disagree with each other: some only
    speak UDP, some sit behind a firewall that eats ICMP, some ship non-UTF-8
    firmware. Defaults suit a modern TCP device.
    """

    host: str
    port: int = 4370
    name: str = ""
    password: int = 0
    timeout: int = 10
    force_udp: bool = False
    omit_ping: bool = False
    encoding: str = "UTF-8"

    def __post_init__(self) -> None:
        if not self.host:
            raise ConfigError("device host is required")
        if not 0 < self.port < 65536:
            raise ConfigError(f"{self.label}: port must be 1-65535, got {self.port}")
        if self.timeout <= 0:
            raise ConfigError(f"{self.label}: timeout must be positive, got {self.timeout}")
        if not self.name:
            # frozen dataclass — bypass __setattr__ to fill in the derived default.
            object.__setattr__(self, "name", self.host)

    @property
    def label(self) -> str:
        return self.name or self.host

    @classmethod
    def from_dict(cls, raw: dict, defaults: dict | None = None) -> DeviceConfig:
        merged = {**(defaults or {}), **raw}
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        unknown = set(merged) - known
        if unknown:
            raise ConfigError(f"unknown device option(s): {', '.join(sorted(unknown))}")
        if "host" not in merged:
            raise ConfigError(f"device entry is missing 'host': {raw}")
        return cls(
            host=str(merged["host"]).strip(),
            port=int(merged.get("port", 4370)),
            name=str(merged.get("name", "")).strip(),
            password=int(merged.get("password", 0)),
            timeout=int(merged.get("timeout", 10)),
            force_udp=bool(merged.get("force_udp", False)),
            omit_ping=bool(merged.get("omit_ping", False)),
            encoding=str(merged.get("encoding", "UTF-8")),
        )


@dataclass(frozen=True)
class Settings:
    """Process-wide settings plus the device inventory."""

    devices: list[DeviceConfig] = field(default_factory=list)

    storage_backend: str = "csv"
    data_dir: Path = Path("data")
    sqlite_path: Path = Path("data/attendance.db")

    sync_history: bool = True
    live_poll_interval: int = 10
    heartbeat_interval: float = 300.0
    reconnect_min_delay: float = 2.0
    reconnect_max_delay: float = 60.0

    log_level: str = "INFO"
    log_format: str = "text"
    log_file: Path | None = None

    def __post_init__(self) -> None:
        if not self.devices:
            raise ConfigError(
                "No devices configured. Set MORX_DEVICE_HOST=<ip> in your .env, "
                "or point MORX_DEVICES_FILE at a JSON inventory "
                "(see config/devices.example.json)."
            )
        if self.storage_backend not in ("csv", "sqlite"):
            raise ConfigError(
                f"MORX_STORAGE_BACKEND must be 'csv' or 'sqlite', got {self.storage_backend!r}"
            )
        if self.reconnect_min_delay > self.reconnect_max_delay:
            raise ConfigError("MORX_RECONNECT_MIN_DELAY cannot exceed MORX_RECONNECT_MAX_DELAY")

        names = [d.label for d in self.devices]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            # Device name is part of every dedup key and CSV row; collisions would
            # silently merge two terminals' data.
            raise ConfigError(f"duplicate device name(s): {', '.join(sorted(duplicates))}")

    @classmethod
    def load(cls) -> Settings:
        return cls(
            devices=_load_devices(),
            storage_backend=_env("MORX_STORAGE_BACKEND", "csv").lower(),
            data_dir=Path(_env("MORX_DATA_DIR", "data")),
            sqlite_path=Path(_env("MORX_SQLITE_PATH", "data/attendance.db")),
            sync_history=_env_bool("MORX_SYNC_HISTORY", True),
            live_poll_interval=_env_int("MORX_LIVE_POLL_INTERVAL", 10),
            heartbeat_interval=_env_float("MORX_HEARTBEAT_INTERVAL", 300),
            reconnect_min_delay=_env_float("MORX_RECONNECT_MIN_DELAY", 2),
            reconnect_max_delay=_env_float("MORX_RECONNECT_MAX_DELAY", 60),
            log_level=_env("MORX_LOG_LEVEL", "INFO").upper(),
            log_format=_env("MORX_LOG_FORMAT", "text").lower(),
            log_file=Path(_env("MORX_LOG_FILE")) if _env("MORX_LOG_FILE") else None,
        )


def _load_devices() -> list[DeviceConfig]:
    inventory = _env("MORX_DEVICES_FILE")
    if inventory:
        return _load_devices_from_file(Path(inventory))

    host = _env("MORX_DEVICE_HOST")
    if not host:
        return []  # Settings.__post_init__ raises with the actionable message.

    return [
        DeviceConfig(
            host=host,
            port=_env_int("MORX_DEVICE_PORT", 4370),
            name=_env("MORX_DEVICE_NAME"),
            password=_env_int("MORX_DEVICE_PASSWORD", 0),
            timeout=_env_int("MORX_DEVICE_TIMEOUT", 10),
            force_udp=_env_bool("MORX_DEVICE_FORCE_UDP", False),
            omit_ping=_env_bool("MORX_DEVICE_OMIT_PING", False),
            encoding=_env("MORX_DEVICE_ENCODING", "UTF-8"),
        )
    ]


def _load_devices_from_file(path: Path) -> list[DeviceConfig]:
    if not path.is_file():
        raise ConfigError(f"MORX_DEVICES_FILE points at a missing file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc

    if isinstance(payload, list):
        entries, defaults = payload, {}
    elif isinstance(payload, dict):
        entries = payload.get("devices", [])
        defaults = payload.get("defaults", {})
    else:
        raise ConfigError(f"{path} must contain a list or an object with a 'devices' key")

    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{path} contains no devices")

    return [DeviceConfig.from_dict(entry, defaults) for entry in entries]
