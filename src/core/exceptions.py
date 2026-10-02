"""Universal CAN-Bus Diagnostic & Telemetry Platform - Formal Exception Taxonomy.

Implements protocol exceptions for ISO 15765-2 (DoCAN) and SAE J1939-21 Transport Protocols,
inheriting from PlatformError and TransportError for complete backward compatibility.
"""

from __future__ import annotations

import re
from typing import Any

from src.core.errors import (
    HardwareError,
    LicenseError,
    PlatformError,
    ProtocolError,
    SafetyError,
    SecurityError,
    TransportError,
)

__all__ = [
    "HardwareError",
    "IsoTpBufferOverflowError",
    "IsoTpError",
    "IsoTpFlowControlError",
    "IsoTpInvalidPduError",
    "IsoTpSequenceError",
    "IsoTpTimeoutError",
    "J1939SequenceError",
    "J1939SessionCollisionError",
    "J1939TpAbortError",
    "J1939TpError",
    "J1939TpTimeoutError",
    "LicenseError",
    "PlatformError",
    "ProtocolError",
    "SafetyError",
    "SecurityError",
    "TransportError",
]

#: ISO 15765-2 (DoCAN) timing parameters: N_As/N_Ar (sender), N_Bs/N_Br
#: (receiver), N_Cs/N_Cr (consecutive frame).
ISOTP_TIMERS: frozenset[str] = frozenset({"N_As", "N_Ar", "N_Bs", "N_Br", "N_Cs", "N_Cr"})

#: SAE J1939-21 transport timing parameters T1..T4.
J1939_TP_TIMERS: frozenset[str] = frozenset({"T1", "T2", "T3", "T4"})

#: P2-5: placeholder default. These are NOT measurements — a caller that does
#: not pass an observed elapsed time must not have "1000.0 ms" (or "750.0 ms")
#: presented downstream as if it had been measured. `None` means "not
#: observed" and serializes to JSON null.
_UNOBSERVED_MS: None = None

#: Recognises the message `IsoTpSequenceError` derives for the numeric form, so
#: a `pickle`/`copy` round trip (which reduces to `cls(*self.args)` == a single
#: message string) can recover `expected_sn`/`actual_sn` instead of tripping the
#: P2-6 "no explicit expected_sn" guard.
_DERIVED_SN_MESSAGE_RE = re.compile(
    r"ISO-TP Sequence Number mismatch: expected (?P<exp>-?\d+), got (?P<act>-?\d+)"
)


def _merge_details(structured: dict[str, Any], details: dict[str, Any] | None) -> dict[str, Any]:
    """P1-1: merge caller-supplied details WITHOUT letting them overwrite structured fields.

    ``details`` is free-form caller data (log/telemetry correlation). The
    structured keys built by each exception carry the *audited* values
    (``timeout_type``, ``expected_sn``, ``raw_data_hex``, ...). Previously
    ``if details: d.update(details)`` let a caller silently overwrite them, so
    the reported error could contradict the exception's own attributes. Caller
    keys that collide with a structured key are dropped; the structured value
    always wins.
    """
    merged = dict(structured)
    if details:
        for key, value in details.items():
            if key not in merged:
                merged[key] = value
    return merged


# ============================================================================
# ISO 15765-2 (DoCAN) Exception Hierarchy
# ============================================================================


class IsoTpError(TransportError):
    """Base exception for all ISO 15765-2 DoCAN transport layer errors."""

    def __init__(
        self,
        message: str = "ISO-TP transport error",
        code: str = "ISOTP_ERROR",
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, cause=cause)


class IsoTpTimeoutError(IsoTpError):
    """ISO-TP protocol timing constraint violation (N_As, N_Ar, N_Bs, N_Br, N_Cs, N_Cr)."""

    def __init__(
        self,
        message: str = "ISO-TP timeout exceeded",
        timeout_type: str = "N_Bs",
        elapsed_ms: float | None = _UNOBSERVED_MS,
        limit_ms: float | None = _UNOBSERVED_MS,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        # P2-5: `timeout_type` is interpolated into the machine-readable `code`,
        # so an unvalidated value ("N_Bs/../x", "", or a free-form string) both
        # corrupts the code and defeats any code-prefix based routing. Only the
        # six ISO 15765-2 timers are accepted.
        if timeout_type not in ISOTP_TIMERS:
            raise ValueError(
                f"Unknown ISO-TP timer {timeout_type!r}; expected one of {sorted(ISOTP_TIMERS)}"
            )
        d: dict[str, Any] = {
            "timeout_type": timeout_type,
            "elapsed_ms": elapsed_ms,
            "limit_ms": limit_ms,
        }
        super().__init__(message, code=f"ISOTP_TIMEOUT_{timeout_type}", details=_merge_details(d, details), cause=cause)
        self.timeout_type: str = timeout_type
        self.elapsed_ms: float | None = elapsed_ms
        self.limit_ms: float | None = limit_ms


class IsoTpFlowControlError(IsoTpError):
    """ISO-TP Flow Control protocol error (WFTmax exceeded, invalid FlowStatus, malformed FC)."""

    def __init__(
        self,
        message: str = "ISO-TP Flow Control error",
        flow_status: int | None = None,
        wft_count: int | None = None,
        reason: str = "FLOW_CONTROL_ERROR",
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        d: dict[str, Any] = {
            "flow_status": flow_status,
            "wft_count": wft_count,
            "reason": reason,
        }
        super().__init__(
            message, code="ISOTP_FLOW_CONTROL_ERROR", details=_merge_details(d, details), cause=cause
        )
        self.flow_status: int | None = flow_status
        self.wft_count: int | None = wft_count
        self.reason: str = reason


class IsoTpBufferOverflowError(IsoTpError):
    """ISO-TP Buffer Overflow error (FS=OVERFLOW received or FF_DL exceeds RX capacity)."""

    def __init__(
        self,
        message: str = "ISO-TP buffer overflow",
        requested_length: int = 0,
        max_buffer_size: int | None = None,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        d: dict[str, Any] = {
            "requested_length": requested_length,
            "max_buffer_size": max_buffer_size,
        }
        super().__init__(
            message, code="ISOTP_BUFFER_OVERFLOW", details=_merge_details(d, details), cause=cause
        )
        self.requested_length: int = requested_length
        self.max_buffer_size: int | None = max_buffer_size


class IsoTpSequenceError(IsoTpError):
    """ISO-TP Consecutive Frame sequence number mismatch."""

    def __init__(
        self,
        expected_sn_or_msg: int | str = 0,
        actual_sn: int = 0,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
        *,
        expected_sn: int | None = None,
    ) -> None:
        if isinstance(expected_sn_or_msg, str):
            # P2-6: the string branch is the "custom message" form. Defaulting
            # `expected_sn` to 0 silently reported the WRONG expected sequence
            # number — sequence 0 is a legal CF index, so the defect is
            # indistinguishable from a real report. Fail closed instead.
            #
            # Regression found while adding this: a bare `Exception` reduces to
            # `self.args`, so `pickle`/`copy` recreate the instance via
            # `cls(*self.args)` — i.e. `(message,)` alone — landing in THIS
            # branch with no `expected_sn` and breaking multiprocessing IPC.
            # The derived form ("... expected N, got M") is therefore
            # recognised and the sequence numbers recovered from it, so the
            # fail-closed guard does not cost round-trip fidelity.
            if expected_sn is None:
                recovered = _DERIVED_SN_MESSAGE_RE.fullmatch(expected_sn_or_msg)
                if recovered is None:
                    raise TypeError(
                        "IsoTpSequenceError(custom_message, ...) requires an explicit "
                        "expected_sn= — the expected sequence number cannot be "
                        "defaulted (or use the numeric form "
                        "IsoTpSequenceError(expected_sn, actual_sn))"
                    )
                exp_sn = int(recovered.group("exp"))
                act_sn = int(recovered.group("act"))
                message = expected_sn_or_msg
                d: dict[str, Any] = {"expected_sn": exp_sn, "actual_sn": act_sn}
                super().__init__(
                    message, code="ISOTP_SEQUENCE_ERROR", details=_merge_details(d, details), cause=cause
                )
                self.expected_sn = exp_sn
                self.actual_sn = act_sn
                return
            message = expected_sn_or_msg
            exp_sn = expected_sn
            act_sn = actual_sn
        else:
            exp_sn = expected_sn if expected_sn is not None else int(expected_sn_or_msg)
            act_sn = actual_sn
            message = f"ISO-TP Sequence Number mismatch: expected {exp_sn}, got {act_sn}"

        d = {"expected_sn": exp_sn, "actual_sn": act_sn}
        super().__init__(
            message, code="ISOTP_SEQUENCE_ERROR", details=_merge_details(d, details), cause=cause
        )
        self.expected_sn = exp_sn
        self.actual_sn = act_sn


class IsoTpInvalidPduError(IsoTpError):
    """ISO-TP malformed PDU header or invalid length specification."""

    def __init__(
        self,
        message: str = "Invalid ISO-TP PDU",
        pci_type: int | None = None,
        raw_data: bytes | None = None,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        # P1-1: `raw_data_hex` is truncated to 8 bytes for the serialized form,
        # but the raw PDU was then retained in full on `self.raw_data` — i.e.
        # the redaction was cosmetic and the complete (possibly VIN/proprietary
        # payload bearing) PDU stayed reachable through the exception object.
        # Retain at most the 8 bytes the redactor already discloses.
        bounded_raw = raw_data[:8] if raw_data is not None and len(raw_data) > 8 else raw_data
        raw_hex: str | None = None
        if raw_data is not None:
            raw_hex = raw_data[:8].hex() + ("..." if len(raw_data) > 8 else "")
        d: dict[str, Any] = {
            "pci_type": pci_type,
            "raw_data_hex": raw_hex,
        }
        super().__init__(
            message, code="ISOTP_INVALID_PDU", details=_merge_details(d, details), cause=cause
        )
        self.pci_type: int | None = pci_type
        self.raw_data: bytes | None = bounded_raw


# ============================================================================
# SAE J1939-21 Transport Protocol Exception Hierarchy
# ============================================================================


class J1939TpError(TransportError):
    """Base exception for all SAE J1939 Transport Protocol failures."""

    def __init__(
        self,
        message: str = "SAE J1939 Transport Protocol error",
        code: str = "J1939_TP_ERROR",
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details, cause=cause)


class J1939TpAbortError(J1939TpError):
    """Raised when a J1939 connection is explicitly aborted via TP.Conn_Abort."""

    def __init__(
        self,
        message: str = "SAE J1939 connection aborted",
        reason: int = 255,
        target_pgn: int = 0,
        sa: int = 0,
        da: int = 0,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        d: dict[str, Any] = {
            "reason": reason,
            "target_pgn": target_pgn,
            "sa": sa,
            "da": da,
        }
        super().__init__(message, code="J1939_TP_ABORT", details=_merge_details(d, details), cause=cause)
        self.reason: int = reason
        self.target_pgn: int = target_pgn
        self.sa: int = sa
        self.da: int = da


class J1939SessionCollisionError(J1939TpError):
    """Raised when an RTS arrives for an active (SA, DA) session."""

    def __init__(
        self,
        message: str = "SAE J1939 session collision on (SA, DA)",
        sa: int = 0,
        da: int = 0,
        old_pgn: int = 0,
        new_pgn: int = 0,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        d: dict[str, Any] = {
            "sa": sa,
            "da": da,
            "old_pgn": old_pgn,
            "new_pgn": new_pgn,
        }
        super().__init__(
            message, code="J1939_SESSION_COLLISION", details=_merge_details(d, details), cause=cause
        )
        self.sa: int = sa
        self.da: int = da
        self.old_pgn: int = old_pgn
        self.new_pgn: int = new_pgn


class J1939SequenceError(J1939TpError):
    """Raised when an out-of-order TP.DT sequence number arrives."""

    def __init__(
        self,
        message: str = "SAE J1939 out-of-order sequence number received",
        expected_seq: int = 1,
        received_seq: int = 1,
        sa: int = 0,
        da: int = 0,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        d: dict[str, Any] = {
            "expected_seq": expected_seq,
            "received_seq": received_seq,
            "sa": sa,
            "da": da,
        }
        super().__init__(
            message, code="J1939_SEQUENCE_ERROR", details=_merge_details(d, details), cause=cause
        )
        self.expected_seq: int = expected_seq
        self.received_seq: int = received_seq
        self.sa: int = sa
        self.da: int = da


class J1939TpTimeoutError(J1939TpError):
    """Raised when J1939 timing constraints (T1, T2, T3, T4) are violated."""

    def __init__(
        self,
        message: str = "SAE J1939 Transport Protocol timeout",
        timeout_type: str = "T1",
        elapsed_ms: float | None = _UNOBSERVED_MS,
        limit_ms: float | None = _UNOBSERVED_MS,
        sa: int | None = None,
        da: int | None = None,
        target_pgn: int | None = None,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        # P2-5: same unsanitized-`code` hazard as IsoTpTimeoutError — only the
        # four SAE J1939-21 timers may reach the code suffix.
        if timeout_type not in J1939_TP_TIMERS:
            raise ValueError(
                f"Unknown SAE J1939 timer {timeout_type!r}; expected one of {sorted(J1939_TP_TIMERS)}"
            )
        d: dict[str, Any] = {
            "timeout_type": timeout_type,
            "elapsed_ms": elapsed_ms,
            "limit_ms": limit_ms,
            "sa": sa,
            "da": da,
            "target_pgn": target_pgn,
        }
        super().__init__(
            message, code=f"J1939_TIMEOUT_{timeout_type}", details=_merge_details(d, details), cause=cause
        )
        self.timeout_type: str = timeout_type
        self.elapsed_ms: float | None = elapsed_ms
        self.limit_ms: float | None = limit_ms
        self.sa: int | None = sa
        self.da: int | None = da
        self.target_pgn: int | None = target_pgn
