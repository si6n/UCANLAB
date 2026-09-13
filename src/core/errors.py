"""Universal CAN-Bus Diagnostic & Telemetry Platform - Core Error Hierarchy.

Normative error classes matching MASTER_PLAN.md Section 11.2 and RFC 7807 problem details.
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
    """Base exception for all Universal CAN Platform errors."""

    def __init__(
        self,
        message: str,
        code: str = "PLATFORM_ERROR",
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}
        self.cause = cause
        # M-09: also bind Python's built-in exception chaining so the root
        # cause surfaces in tracebacks ("The above exception was the direct
        # cause...") instead of being visible only via to_dict().
        if cause is not None:
            self.__cause__ = cause
        self.timestamp_ns = time.time_ns()

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
        if include_cause and self.cause:
            result["cause"] = str(self.cause)
        return result

    def to_dict_internal(self) -> dict[str, Any]:
        """Full internal serialization including the cause chain (logs only)."""
        result = self.to_dict(include_cause=True)
        return result


class HardwareError(PlatformError):
    """Hardware, transceiver, driver or DLL level failures (Bus-off, disconnect, USB error)."""

    def __init__(
        self,
        message: str,
        code: str = "HARDWARE_ERROR",
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, cause=cause)


class TransportError(PlatformError):
    """Transport protocol failures (J1939 BAM/CMDT timeout, out-of-order frames, ISO-TP abort)."""

    def __init__(
        self,
        message: str,
        code: str = "TRANSPORT_ERROR",
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, cause=cause)


class ProtocolError(PlatformError):
    """Protocol decoding/encoding, DBC signal extraction or sentinel parsing errors."""

    def __init__(
        self,
        message: str,
        code: str = "PROTOCOL_ERROR",
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, cause=cause)


class SafetyError(PlatformError):
    """Active test preconditions failure or TX Gateway safety violation."""

    def __init__(
        self,
        message: str,
        code: str = "SAFETY_ERROR",
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, cause=cause)


class LicenseError(PlatformError):
    """Licensing, token verification, HWID validation or clock tampering errors."""

    def __init__(
        self,
        message: str,
        code: str = "LICENSE_ERROR",
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, cause=cause)


class SecurityError(PlatformError):
    """Cryptographic, signature, anti-tamper or envelope decryption errors."""

    def __init__(
        self,
        message: str,
        code: str = "SECURITY_ERROR",
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, cause=cause)
