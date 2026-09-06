"""Universal CAN-Bus Diagnostic & Telemetry Platform - Hardware Port & Provider Contracts.

Decouples transport protocol state machines (ISO-TP, J1939, UDS) from concrete HAL drivers,
operating system monotonic clocks, and cryptographic key stores.
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol, runtime_checkable

from src.core.models.can_frame import CanFrame


@runtime_checkable
class TxPort(Protocol):
    """Abstract CAN frame transmission port.

    Provides both asynchronous (cooperative non-blocking) and synchronous (blocking)
    transmission methods to allow protocol engines to send frames onto the CAN bus.
    """

    async def send(
        self,
        frame: CanFrame,
        *,
        is_critical_command: bool = False,
        user_confirmed: bool = False,
        budget_category: str = "default",
    ) -> None:
        """Transmit a CAN frame asynchronously onto the bus.

        Args:
            frame: Canonical CanFrame to transmit.
            is_critical_command: Whether this frame executes a critical automotive command.
            user_confirmed: Dual operator confirmation capability flag.
            budget_category: Dedicated rate-limit lane name.

        Raises:
            PlatformError: If transmission fails or bus is in fault state.
        """
        ...

    def send_sync(
        self,
        frame: CanFrame,
        *,
        is_critical_command: bool = False,
        user_confirmed: bool = False,
        budget_category: str = "default",
    ) -> None:
        """Transmit a CAN frame synchronously (blocking) onto the bus.

        Args:
            frame: Canonical CanFrame to transmit.
            is_critical_command: Whether this frame executes a critical automotive command.
            user_confirmed: Dual operator confirmation capability flag.
            budget_category: Dedicated rate-limit lane name.

        Raises:
            PlatformError: If transmission fails or bus is in fault state.
        """
        ...


@runtime_checkable
class RxSubscription(Protocol):
    """Abstract CAN frame subscription receiver.

    Allows protocol engines and subscribers to asynchronously consume incoming CAN frames
    with timeout support and lifecycle teardown.
    """

    async def recv(self, timeout_s: float | None = None) -> CanFrame | None:
        """Receive the next incoming CAN frame.

        Args:
            timeout_s: Maximum duration in seconds to wait for a frame.
                       If None, wait indefinitely until a frame is received or cancelled.

        Returns:
            CanFrame if received before timeout, or None if the timeout expired.
        """
        ...

    def unsubscribe(self) -> None:
        """Cancel and release the subscription and any associated resources."""
        ...


@runtime_checkable
class ClockProvider(Protocol):
    """High-resolution monotonic time provider.

    Supplies high-precision monotonic timestamps in seconds and nanoseconds
    for protocol state machine timers (N_As, N_Bs, N_Cr, T1..T4, STmin), plus
    a wall-clock reading for license/HWM style absolute-time comparisons.
    """

    def now_monotonic(self) -> float:
        """Return current monotonic time in seconds."""
        ...

    def now_monotonic_ns(self) -> int:
        """Return current monotonic time in nanoseconds."""
        ...

    def now_wall_ns(self) -> int:
        """Return current wall-clock (real) time in nanoseconds since epoch."""
        ...


@runtime_checkable
class SecretProvider(Protocol):
    """Cryptographic secret lookup provider.

    Supplies binary keys and shared secrets for security seed/key exchanges,
    HMAC verification, and encryption without leaking credentials into state machines.
    """

    def get_secret(self, key_name: str) -> bytes:
        """Retrieve binary secret for key_name.

        Args:
            key_name: Logical key identifier string.

        Returns:
            Binary secret bytes.

        Raises:
            KeyError: If the requested key_name does not exist.
        """
        ...


# Concrete Default Implementations & Test Utilities


class SystemClockProvider:
    """Default system clock provider using standard library clocks."""

    def now_monotonic(self) -> float:
        """Return monotonic time in fractional seconds."""
        return time.monotonic()

    def now_monotonic_ns(self) -> int:
        """Return monotonic time in nanoseconds."""
        return time.monotonic_ns()

    def now_wall_ns(self) -> int:
        """Return wall-clock time in nanoseconds since the epoch."""
        return time.time_ns()


class VirtualClock:
    """Deterministic, manually advanced monotonic clock for timeout tests.

    docs/ai_context/05 §4: tests must never sleep to exercise timeout
    behaviour — they advance this clock instead. Only monotonic time is
    virtualised (safety/lease math is monotonic by invariant); wall time
    falls back to the real system clock because file/license comparisons
    legitimately need it.
    """

    def __init__(self, start_monotonic_sec: float = 1000.0) -> None:
        self._monotonic_sec = float(start_monotonic_sec)

    def advance(self, delta_sec: float) -> None:
        """Advance the virtual monotonic clock by delta_sec (may be negative for rollback tests)."""
        self._monotonic_sec += float(delta_sec)

    def set(self, monotonic_sec: float) -> None:
        """Set the virtual monotonic clock to an absolute value."""
        self._monotonic_sec = float(monotonic_sec)

    def now_monotonic(self) -> float:
        """Return virtual monotonic time in fractional seconds."""
        return self._monotonic_sec

    def now_monotonic_ns(self) -> int:
        """Return virtual monotonic time in nanoseconds."""
        return int(self._monotonic_sec * 1e9)

    def now_wall_ns(self) -> int:
        """Return real wall-clock time in nanoseconds (not virtualised)."""
        return time.time_ns()


class InMemorySecretProvider:
    """In-memory dictionary backed secret provider for configuration and test mocking."""

    def __init__(self, secrets: dict[str, bytes] | None = None) -> None:
        self._secrets: dict[str, bytes] = dict(secrets) if secrets is not None else {}

    def set_secret(self, key_name: str, secret: bytes) -> None:
        """Store or update a secret in the provider."""
        self._secrets[key_name] = secret

    def get_secret(self, key_name: str) -> bytes:
        """Retrieve a secret by name. Raises KeyError if not found."""
        if key_name not in self._secrets:
            raise KeyError(f"Secret '{key_name}' not found")
        return self._secrets[key_name]


class InMemoryTxPort:
    """In-memory transmission port recording frames for test verification."""

    def __init__(self) -> None:
        self.sent_frames: list[CanFrame] = []

    async def send(self, frame: CanFrame) -> None:
        """Record frame asynchronously."""
        self.sent_frames.append(frame)

    def send_sync(self, frame: CanFrame) -> None:
        """Record frame synchronously."""
        self.sent_frames.append(frame)

    def clear(self) -> None:
        """Clear recorded frame history."""
        self.sent_frames.clear()


class QueueRxSubscription:
    """Asyncio queue-backed subscription implementation."""

    def __init__(self, queue: asyncio.Queue[CanFrame] | None = None) -> None:
        self._queue: asyncio.Queue[CanFrame] = queue if queue is not None else asyncio.Queue()
        self._unsubscribed: bool = False

    @property
    def is_unsubscribed(self) -> bool:
        """Return True if subscription is cancelled."""
        return self._unsubscribed

    async def recv(self, timeout_s: float | None = None) -> CanFrame | None:
        """Receive next frame from queue with optional timeout."""
        if self._unsubscribed:
            return None
        if timeout_s is None:
            try:
                return await self._queue.get()
            except asyncio.CancelledError:
                raise
            except Exception:
                return None
        if timeout_s <= 0:
            try:
                return self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return None
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout_s)
        except (asyncio.TimeoutError, TimeoutError):
            return None
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    def unsubscribe(self) -> None:
        """Mark subscription as unsubscribed."""
        self._unsubscribed = True

    def put_nowait(self, frame: CanFrame) -> None:
        """Enqueue frame synchronously."""
        self._queue.put_nowait(frame)

    async def put(self, frame: CanFrame) -> None:
        """Enqueue frame asynchronously."""
        await self._queue.put(frame)
