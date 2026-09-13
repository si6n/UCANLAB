"""Universal CAN-Bus Diagnostic & Telemetry Platform - Formal Exception Taxonomy.

Implements protocol exceptions for ISO 15765-2 (DoCAN) and SAE J1939-21 Transport Protocols,
inheriting from PlatformError and TransportError for complete backward compatibility.
"""

from __future__ import annotations

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
        elapsed_ms: float = 1000.0,
        limit_ms: float = 1000.0,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        d: dict[str, Any] = {
            "timeout_type": timeout_type,
            "elapsed_ms": elapsed_ms,
            "limit_ms": limit_ms,
        }
        if details:
            d.update(details)
        super().__init__(message, code=f"ISOTP_TIMEOUT_{timeout_type}", details=d, cause=cause)
        self.timeout_type: str = timeout_type
        self.elapsed_ms: float = elapsed_ms
        self.limit_ms: float = limit_ms


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
        if details:
            d.update(details)
        super().__init__(message, code="ISOTP_FLOW_CONTROL_ERROR", details=d, cause=cause)
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
        if details:
            d.update(details)
        super().__init__(message, code="ISOTP_BUFFER_OVERFLOW", details=d, cause=cause)
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
            message = expected_sn_or_msg
            exp_sn = expected_sn if expected_sn is not None else 0
            act_sn = actual_sn
        else:
            exp_sn = expected_sn if expected_sn is not None else int(expected_sn_or_msg)
            act_sn = actual_sn
            message = f"ISO-TP Sequence Number mismatch: expected {exp_sn}, got {act_sn}"

        d: dict[str, Any] = {"expected_sn": exp_sn, "actual_sn": act_sn}
        if details:
            d.update(details)
        super().__init__(message, code="ISOTP_SEQUENCE_ERROR", details=d, cause=cause)
        self.expected_sn: int = exp_sn
        self.actual_sn: int = act_sn


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
        if raw_data is not None and len(raw_data) > 8:
            raw_hex: str | None = raw_data[:8].hex() + "..."
        else:
            raw_hex = raw_data.hex() if raw_data is not None else None
        d: dict[str, Any] = {
            "pci_type": pci_type,
            "raw_data_hex": raw_hex,
        }
        if details:
            d.update(details)
        super().__init__(message, code="ISOTP_INVALID_PDU", details=d, cause=cause)
        self.pci_type: int | None = pci_type
        self.raw_data: bytes | None = raw_data


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
        if details:
            d.update(details)
        super().__init__(message, code="J1939_TP_ABORT", details=d, cause=cause)
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
        if details:
            d.update(details)
        super().__init__(message, code="J1939_SESSION_COLLISION", details=d, cause=cause)
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
        if details:
            d.update(details)
        super().__init__(message, code="J1939_SEQUENCE_ERROR", details=d, cause=cause)
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
        elapsed_ms: float = 750.0,
        limit_ms: float = 750.0,
        sa: int | None = None,
        da: int | None = None,
        target_pgn: int | None = None,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        d: dict[str, Any] = {
            "timeout_type": timeout_type,
            "elapsed_ms": elapsed_ms,
            "limit_ms": limit_ms,
            "sa": sa,
            "da": da,
            "target_pgn": target_pgn,
        }
        if details:
            d.update(details)
        super().__init__(message, code=f"J1939_TIMEOUT_{timeout_type}", details=d, cause=cause)
        self.timeout_type: str = timeout_type
        self.elapsed_ms: float = elapsed_ms
        self.limit_ms: float = limit_ms
        self.sa: int | None = sa
        self.da: int | None = da
        self.target_pgn: int | None = target_pgn
