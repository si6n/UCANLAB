"""Universal CAN-Bus Diagnostic & Telemetry Platform - Core Error Hierarchy.

Normative error classes matching MASTER_PLAN.md Section 11.2.

P2-7: this module does NOT emit RFC 7807 "problem details". `to_dict()` is an
internal, flat envelope (`code` / `message` / `timestamp_ns` / `details` /
`cause`); the RFC 7807 members (`type`, `title`, `status`, `detail`,
`instance`) are absent. The previous docstring claimed compliance that the
code never implemented — a consumer reading it could emit a payload that no
RFC 7807 parser accepts. Any future REST boundary must build its own problem
document from `to_dict()`.
"""

from __future__ import annotations

import os
import time
from typing import Any

_MAX_RAW_HEX_CHARS: int = 16  # 8 bytes of payload


def _redact_details(details: dict[str, Any]) -> dict[str, Any]:
    """Return a redacted copy safe for external responses/logs."""
    redacted: dict[str, Any] = {}
    for key, value in details.items():
        if key == "candidates" and isinstance(value, (list, tuple)):
            redacted[key] = [os.path.basename(str(c)) for c in value]
        elif key in ("raw_data_hex", "raw_data") and isinstance(value, str) and len(value) > _MAX_RAW_HEX_CHARS:
            redacted[key] = value[:_MAX_RAW_HEX_CHARS] + "..."
        elif key in ("raw_data_hex", "raw_data") and isinstance(value, (bytes, bytearray)) and len(value) > 8:
            redacted[key] = bytes(value[:8]).hex() + "..."
        else:
            redacted[key] = value
    return redacted


class PlatformError(Exception):
    """Base exception for all Universal CAN Platform errors.

    Subclasses declare their default machine-readable ``code`` as a
    ``default_code`` class attribute (P3-1) — the six identical ``__init__``
    bodies that differed only in that string are gone.
    """

    default_code: str = "PLATFORM_ERROR"

    def __init__(
        self,
        message: str,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code if code is not None else self.default_code
        # P1-2: never retain the caller's dict by reference. The caller keeps a
        # handle on it, so an aliased `details` could be mutated after the
        # error was raised and after it was serialized into a log/evidence
        # record — retroactively rewriting the recorded error context.
        self.details: dict[str, Any] = dict(details) if details else {}
        self.cause = cause
        # M-09: also bind Python's built-in exception chaining so the root
        # cause surfaces in tracebacks ("The above exception was the direct
        # cause...") instead of being visible only via to_dict().
        if cause is not None:
            self.__cause__ = cause
        self.timestamp_ns = time.time_ns()

    @property
    def chained_cause(self) -> BaseException | None:
        """The effective root cause: explicit ``cause=`` wins, else ``__cause__``.

        P1-2: ``raise ... from exc`` sets ``__cause__`` on the instance WITHOUT
        going through ``cause=``, so the exception carried a populated
        ``__cause__`` that ``to_dict``/``cause`` ignored. Falling back to
        ``self.__cause__`` makes both entry points equivalent.
        """
        return self.cause if self.cause is not None else self.__cause__

    def to_dict(self, include_cause: bool = True) -> dict[str, Any]:
        """Serialize error to standardized dictionary representation.

        REVIEW redaction: external responses must not carry the internal
        cause chain — pass ``include_cause=False`` for external payloads.
        Default True preserves backward compatibility (existing tests
        assert ``d["cause"]``); internal ``self.cause``/``__cause__``
        are always retained.
        """
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "timestamp_ns": self.timestamp_ns,
            "details": _redact_details(self.details),
        }
        chained = self.chained_cause
        if include_cause and chained is not None:
            result["cause"] = str(chained)
        return result

    def to_dict_internal(self) -> dict[str, Any]:
        """Full internal serialization including the cause chain (logs only)."""
        result = self.to_dict(include_cause=True)
        return result


class HardwareError(PlatformError):
    """Hardware, transceiver, driver or DLL level failures (Bus-off, disconnect, USB error)."""

    default_code = "HARDWARE_ERROR"


class TransportError(PlatformError):
    """Transport protocol failures (J1939 BAM/CMDT timeout, out-of-order frames, ISO-TP abort)."""

    default_code = "TRANSPORT_ERROR"


class ProtocolError(PlatformError):
    """Protocol decoding/encoding, DBC signal extraction or sentinel parsing errors."""

    default_code = "PROTOCOL_ERROR"


class SafetyError(PlatformError):
    """Active test preconditions failure or TX Gateway safety violation."""

    default_code = "SAFETY_ERROR"


class LicenseError(PlatformError):
    """Licensing, token verification, HWID validation or clock tampering errors."""

    default_code = "LICENSE_ERROR"


class SecurityError(PlatformError):
    """Cryptographic, signature, anti-tamper or envelope decryption errors."""

    default_code = "SECURITY_ERROR"
