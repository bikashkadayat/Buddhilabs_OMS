"""JSON log formatting and per-request correlation IDs."""
import json
import logging
from contextvars import ContextVar

# Correlation id for the current request/task; "-" when outside a request.
_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def set_request_id(request_id):
    _request_id.set(request_id)


def get_request_id():
    return _request_id.get()


class RequestIDFilter(logging.Filter):
    """Injects the active correlation id onto every log record."""

    def filter(self, record):
        record.request_id = get_request_id()
        return True


class JSONFormatter(logging.Formatter):
    """Minimal structured JSON formatter (no third-party dependency)."""

    def format(self, record):
        payload = {
            "time": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)
