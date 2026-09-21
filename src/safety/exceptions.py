"""Safety Layer Exception Taxonomy.

Defines specialized functional safety exceptions inheriting from SafetyError.
Matches MASTER_PLAN.md Section 7 and ISO 26262 ASIL-B/D Fault Containment.
"""

from __future__ import annotations

from typing import Any, ClassVar

from src.core.errors import SafetyError


class WhitelistFailClosedError(SafetyError):
    """Raised when frame transmission is attempted against an empty or unconfigured whitelist."""

    def __init__(
        self,
        message: str = "Transmission blocked: Dynamic whitelist is empty or unconfigured (Fail-Closed)",
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message, code="WHITELIST_FAIL_CLOSED", details=details, cause=cause)


class WhitelistViolationError(SafetyError):
    """Raised when frame arbitration ID is not found in the authorized whitelist."""

    def __init__(
        self,
        message: str = "Transmission blocked: Frame ID not in whitelist",
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message, code="WHITELIST_VIOLATION", details=details, cause=cause)


class SpeedInterlockError(SafetyError):
    """Raised when a critical transmission is attempted while vehicle is in motion."""

    def __init__(
        self,
        message: str = "Safety Interlock: Critical command blocked while vehicle is moving",
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message, code="SPEED_INTERLOCK_ACTIVE", details=details, cause=cause)


class SpeedDataStaleError(SafetyError):
    """Raised when a critical transmission is attempted with stale or missing speed telemetry."""

    def __init__(
        self,
        message: str = "Safety Interlock: Critical command blocked due to stale vehicle speed telemetry",
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message, code="SPEED_DATA_STALE", details=details, cause=cause)


class DualConfirmationRequiredError(SafetyError):
    """Raised when a critical diagnostic command lacks explicit user confirmation."""

    def __init__(
        self,
        message: str = "Critical command rejected: Operator dual-confirmation missing",
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message, code="CONFIRMATION_REQUIRED", details=details, cause=cause)


class FrameSanityError(SafetyError):
    """Raised when frame attributes violate CAN or CAN-FD sanity constraints."""

    def __init__(
        self,
        message: str = "Transmission rejected: Invalid frame sanity check",
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message, code="INVALID_FRAME_SANITY", details=details, cause=cause)


class RateLimitExceededError(SafetyError):
    """Raised when transmission rate exceeds the permitted budget.

    S-12: the message is built from the REAL gateway caps
    (``TxSafetyGateway.MAX_TX_RATE_PER_SEC`` per lane and
    ``TxSafetyGateway.MAX_TOTAL_TX_PER_SEC`` aggregate) instead of the
    hardcoded "(100 msg/s)" text, which contradicted the aggregate envelope.
    ``RATE_LIMIT_LANE_MSG_PER_SEC`` / ``RATE_LIMIT_TOTAL_MSG_PER_SEC`` are
    authoritative here; keep them equal to the gateway constants.
    """

    #: Per-lane ceiling (mirrors ``TxSafetyGateway.MAX_TX_RATE_PER_SEC``).
    RATE_LIMIT_LANE_MSG_PER_SEC: ClassVar[int] = 100
    #: Aggregate ceiling across every lane (mirrors
    #: ``TxSafetyGateway.MAX_TOTAL_TX_PER_SEC``).
    RATE_LIMIT_TOTAL_MSG_PER_SEC: ClassVar[int] = 400

    @classmethod
    def default_message(cls) -> str:
        """S-12: canonical rate-limit text derived from the real caps."""
        return (
            "Transmission rate limit exceeded "
            f"({cls.RATE_LIMIT_LANE_MSG_PER_SEC} msg/s per lane, "
            f"{cls.RATE_LIMIT_TOTAL_MSG_PER_SEC} msg/s total)"
        )

    def __init__(
        self,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        if message is None:
            message = self.default_message()
        super().__init__(message, code="RATE_LIMIT_EXCEEDED", details=details, cause=cause)
