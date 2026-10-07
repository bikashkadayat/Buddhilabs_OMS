"""Logging configuration.

Text format for humans, JSON for log shippers. Everything goes to stderr so
systemd/journald and `docker logs` pick it up without extra plumbing; a file
sink is optional and additive.
"""

from __future__ import annotations

import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

TEXT_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-24s | %(message)s"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Anything attached via logger.info(..., extra={...}) rides along.
        for key, value in record.__dict__.items():
            if key.startswith("ctx_"):
                payload[key[4:]] = value
        return json.dumps(payload, default=str)


def configure(level: str = "INFO", fmt: str = "text", log_file: Path | None = None) -> None:
    formatter: logging.Formatter = (
        JsonFormatter() if fmt == "json" else logging.Formatter(TEXT_FORMAT)
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))
    root.handlers.clear()

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        rotating = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        rotating.setFormatter(formatter)
        root.addHandler(rotating)

    # pyzk is chatty about socket internals at DEBUG; keep it out of our stream.
    logging.getLogger("zk").setLevel(logging.WARNING)
