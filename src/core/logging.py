"""Universal CAN-Bus Diagnostic & Telemetry Platform - Structured Logging System.

High-performance logging with nanosecond precision, context tagging, and JSON support.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

_RESERVED_LOG_ATTRS: frozenset[str] = frozenset({
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "message",
    "timestamp_ns",
    "extra",
})


_PROTECTED_LOG_KEYS: frozenset[str] = frozenset(
    {"timestamp_ns", "level", "logger", "message", "exception"}
)
_MAX_EXTRA_VALUE_LEN: int = 512


def _sanitize_extra_value(value: Any) -> Any:
    text = value if isinstance(value, str) else str(value)
    if len(text) > _MAX_EXTRA_VALUE_LEN:
        return text[:_MAX_EXTRA_VALUE_LEN] + "..."
    return value if isinstance(value, (int, float, bool)) or value is None else text


class JsonFormatter(logging.Formatter):
    """Custom JSON formatter producing structured log entries with nanosecond timestamps."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp_ns": getattr(record, "timestamp_ns", time.time_ns()),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include custom extra fields passed via logger.log(..., extra={...})
        for key, val in record.__dict__.items():
            if key not in _RESERVED_LOG_ATTRS and not key.startswith("_"):
                if key in _PROTECTED_LOG_KEYS:
                    continue
                log_data[key] = _sanitize_extra_value(val)

        if hasattr(record, "extra") and isinstance(record.extra, dict):
            for key, val in record.extra.items():
                if key in _PROTECTED_LOG_KEYS:
                    continue
                log_data[key] = _sanitize_extra_value(val)

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, default=str)


def setup_logging(
    level: int = logging.INFO,
    json_output: bool = True,
) -> logging.Logger:
    """Configure the root logger for the platform."""
    root_logger = logging.getLogger("universal_can")
    root_logger.setLevel(level)

    # Avoid duplicate handlers
    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)

        if json_output:
            handler.setFormatter(JsonFormatter())
        else:
            formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
            handler.setFormatter(formatter)

        root_logger.addHandler(handler)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Get a named sub-logger under the universal_can namespace."""
    return logging.getLogger(f"universal_can.{name}")
