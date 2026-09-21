"""Universal CAN-Bus Diagnostic & Telemetry Platform - Structured Logging System.

High-performance logging with nanosecond precision, context tagging, and JSON support.

P3-6: this module is named ``logging`` and shadows the stdlib module for the
idiom ``from src.core import logging``. Renaming the FILE (e.g. to
``structured_logging.py``) was evaluated and rejected for this batch: 53
import sites across ``src/{engine,safety,hal,protocols,security,ui,launcher}``
and ``tests/`` say ``from src.core.logging import ...``, i.e. the rename is not
possible without edits outside ``src/core/**`` (out of ownership). The shadow
is harmless in practice — the module does ``import logging`` at top level,
which resolves absolutely (PEP 328) to the stdlib module, never to itself.
"""

from __future__ import annotations

import json
import logging
import math
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


def _encode_non_finite(value: Any) -> Any:
    """Replace non-finite floats with a JSON-safe string marker (P1-5).

    ``json.dumps(..., allow_nan=False)`` raises on NaN/Infinity, so the values
    are normalized BEFORE serialization. The marker keeps the anomaly visible
    (a dropped field would hide a genuine sentinel/desync in a signal) while
    guaranteeing the emitted line parses as strict JSON.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return f"<non-finite:{value!r}>"
    return value


def _sanitize_extra_value(value: Any) -> Any:
    if isinstance(value, float):
        return _encode_non_finite(value)
    text = value if isinstance(value, str) else str(value)
    if len(text) > _MAX_EXTRA_VALUE_LEN:
        return text[:_MAX_EXTRA_VALUE_LEN] + "..."
    return value if isinstance(value, (int, float, bool)) or value is None else text


def _json_default(value: Any) -> Any:
    """Fallback serializer for non-JSON field types (P1-5).

    Replaces the bare ``default=str``: the fallback is still stringification,
    but non-finite floats are marked explicitly rather than silently rendered
    as ``'nan'`` (which is a valid JSON *string* and therefore hostile to any
    downstream numeric check).
    """
    encoded = _encode_non_finite(value)
    return encoded if encoded is not value else str(value)


class JsonFormatter(logging.Formatter):
    """Custom JSON formatter producing structured log entries with nanosecond timestamps."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp_ns": getattr(record, "timestamp_ns", time.time_ns()),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.msg is not None:
            log_data["message"] = _encode_non_finite(log_data["message"])
        log_data["timestamp_ns"] = _encode_non_finite(log_data["timestamp_ns"])

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

        # P1-5: `allow_nan=False` — the default (`True`) serializes float
        # NaN/Infinity as the bare tokens `NaN` / `Infinity`, which are NOT
        # valid JSON. A consumer (log shipper, evidence archive, cloud
        # uploader) parsing the line with a strict JSON reader fails on the
        # whole record. Non-finite values are normalized to a string marker by
        # `_encode_non_finite` so the record stays parseable and the anomaly
        # remains visible instead of being dropped.
        return json.dumps(
            log_data, default=_json_default, allow_nan=False, separators=(",", ":")
        )


def setup_logging(
    level: int = logging.INFO,
    json_output: bool = True,
) -> logging.Logger:
    """Configure the ``universal_can`` logger tree for the platform.

    Idempotent: repeated calls re-apply ``level`` (P1-5 — the handler level used
    to be set only on the very first call, so a later ``setup_logging(DEBUG)``
    changed the logger level while the handler kept dropping DEBUG records).

    Delivery contract (P1-5). A record written through ``universal_can.*`` must
    reach a handler exactly ONCE and must still reach the stdlib ROOT logger.
    Two paths can emit it: our own handler on the ``universal_can`` logger, and
    the handlers attached to root (which receive a copy via propagation — this
    is how ``caplog`` and a host application's own logging config observe
    library records). Installing our handler while root already has one would
    double-log.

    Resolution: ``propagate`` always stays **True** — a library must never sever
    its records from the host's root handlers. Instead we only install a handler
    of our own when the stdlib root has NONE; when root is already configured we
    rely on inheritance and merely re-level the existing handlers.
    """
    root_logger = logging.getLogger("universal_can")
    root_logger.setLevel(level)
    # Explicitly asserted on every call: propagation to root is part of the
    # contract above (regression guard against re-introducing `propagate=False`).
    root_logger.propagate = True

    # The stdlib ROOT logger (`""`) — the propagation destination, and where
    # `caplog` / host applications attach their handlers.
    stdlib_root = logging.getLogger()

    if stdlib_root.handlers:
        # Root is already configured (host app, pytest/caplog, basicConfig).
        # Rely on inheritance: no handler of our own => no duplicate emission,
        # and records still reach root exactly once.
        #
        # The level must be handled on BOTH loggers. A record's own level is
        # checked against `getEffectiveLevel()`, which walks UP the hierarchy —
        # so a root left at WARNING (exactly what `caplog`/`basicConfig` do)
        # would drop our DEBUG/INFO records *before* any handler runs, making
        # the `setLevel(level)` below unreachable for them. Only ever LOWER the
        # effective threshold; a stricter root (e.g. ERROR-only) is never
        # silently loosened into emitting more than the caller asked for.
        if stdlib_root.level == logging.NOTSET or stdlib_root.level > level:
            stdlib_root.setLevel(level)
        for existing in root_logger.handlers:
            existing.setLevel(level)
        return root_logger

    # Root has nothing, so this logger owns the only handler. Propagation stays
    # enabled; with no root handler installed there is nothing to duplicate into.
    if not root_logger.handlers:
        root_logger.addHandler(logging.StreamHandler(sys.stdout))

    handler = root_logger.handlers[0]
    # Re-applied on EVERY call, unconditionally.
    handler.setLevel(level)

    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Get a named sub-logger under the universal_can namespace.

    P3-4: guarantees the namespace has a configured handler. Previously a
    library consumer that never called ``setup_logging()`` explicitly got
    ``logging.lastResort`` behaviour (WARNING+ only, unformatted) — or, worse,
    the stdlib root handler — depending on import order.
    """
    root_logger = logging.getLogger("universal_can")
    if not root_logger.handlers:
        setup_logging()
    return logging.getLogger(f"universal_can.{name}")
