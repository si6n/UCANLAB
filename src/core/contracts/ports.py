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
        inbound_triggered: bool = False,
    ) -> None:
        """Transmit a CAN frame asynchronously onto the bus.

        Args:
            frame: Canonical CanFrame to transmit.
            is_critical_command: Whether this frame executes a critical automotive command.
            user_confirmed: Dual operator confirmation capability flag.
            budget_category: Dedicated rate-limit lane name.
            inbound_triggered: Frame is a protocol RESPONSE to inbound bus
                traffic (J1939 TP CTS/ACK, ISO-TP FC) — never E-Stop
                escalated on rate overload (P11 / G-11).

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
        inbound_triggered: bool = False,
    ) -> None:
        """Transmit a CAN frame synchronously (blocking) onto the bus.

        Args:
            frame: Canonical CanFrame to transmit.
            is_critical_command: Whether this frame executes a critical automotive command.
            user_confirmed: Dual operator confirmation capability flag.
            budget_category: Dedicated rate-limit lane name.
            inbound_triggered: See `send` (P11 / G-11).

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
        # P2-4: the canonical state is INTEGER nanoseconds. The previous
        # `int(self._monotonic_sec * 1e9)` round-tripped through a float, so a
        # perfectly representable counter (e.g. the watchdog lease limit of
        # 800_000_000 ns) came back off by a few nanoseconds — enough to flip a
        # `<=` lease-expiry comparison. Start at exactly 1000.0 s = 1e12 ns.
        self._monotonic_ns: int = int(round(float(start_monotonic_sec) * 1_000_000_000))

    @property
    def _monotonic_sec(self) -> float:
        """Backward-compatible seconds view of the integer nanosecond state."""
        return self._monotonic_ns / 1_000_000_000

    def advance(self, delta_sec: float) -> None:
        """Advance the virtual monotonic clock by delta_sec (must be non-negative)."""
        delta = float(delta_sec)
        if delta < 0:
            raise ValueError(f"VirtualClock.advance requires non-negative delta, got {delta_sec!r}")
        self._monotonic_ns += int(round(delta * 1_000_000_000))

    def advance_ns(self, delta_ns: int) -> None:
        """Advance the virtual monotonic clock by an exact integer nanosecond count."""
        delta = int(delta_ns)
        if delta < 0:
            raise ValueError(f"VirtualClock.advance_ns requires non-negative delta, got {delta_ns!r}")
        self._monotonic_ns += delta

    def set(self, monotonic_sec: float) -> None:
        """Set the virtual monotonic clock to an absolute value (monotonic, never backwards)."""
        monotonic_sec = float(monotonic_sec)
        target = int(round(monotonic_sec * 1_000_000_000))
        if target < self._monotonic_ns:
            raise ValueError(
                f"VirtualClock.set cannot move backwards ({self._monotonic_ns} -> {target}) ns"
            )
        self._monotonic_ns = target

    def set_ns(self, monotonic_ns: int) -> None:
        """Set the virtual monotonic clock to an exact integer nanosecond value."""
        target = int(monotonic_ns)
        if target < self._monotonic_ns:
            raise ValueError(
                f"VirtualClock.set_ns cannot move backwards ({self._monotonic_ns} -> {target})"
            )
        self._monotonic_ns = target

    def now_monotonic(self) -> float:
        """Return virtual monotonic time in fractional seconds."""
        return self._monotonic_ns / 1_000_000_000

    def now_monotonic_ns(self) -> int:
        """Return virtual monotonic time in nanoseconds (exact, no float math)."""
        return self._monotonic_ns

    def now_wall_ns(self) -> int:
        """Return real wall-clock time in nanoseconds (not virtualised)."""
        return time.time_ns()


class InMemorySecretProvider:
    """In-memory dictionary backed secret provider for configuration and test mocking."""

    def __init__(
        self,
        secrets: dict[str, bytes] | None = None,
        allow_whitelist_superset: bool = False,
    ) -> None:
        # P2-3: `dict(secrets)` was a SHALLOW copy — a `bytearray`/`memoryview`
        # value stayed shared with the caller, so mutating the caller's buffer
        # silently rewrote the "vault" secret (an HMAC key change with no audit
        # trail, and `probe.py`-style rollback confusion). Coerce every value to
        # immutable `bytes` on ingest.
        self._secrets: dict[str, bytes] = {
            str(k): bytes(v) for k, v in (secrets or {}).items()
        }
        # P11 (G-11): audit flag for the intentionally broad J1939 response
        # masks — the concrete implementation must acknowledge the override.
        # Kept on the secret store: it is part of the SecretProvider port's
        # constructor contract, so consumers read it off this instance.
        self.allow_whitelist_superset = bool(allow_whitelist_superset)

    def set_secret(self, key_name: str, secret: bytes) -> None:
        """Store or update a secret in the provider (byte-coerced, P2-3)."""
        self._secrets[str(key_name)] = bytes(secret)

    def get_secret(self, key_name: str) -> bytes:
        """Retrieve a secret by name. Raises KeyError if not found.

        P2-3: returns a fresh copy, so a caller cannot mutate stored key
        material in place through the handle it received.
        """
        if key_name not in self._secrets:
            raise KeyError(f"Secret '{key_name}' not found")
        return bytes(self._secrets[key_name])


class InMemoryTxPort:
    """In-memory transmission port recording frames for test verification.

    L-18 (P3-16): signature-aligned with the TxPort protocol — the old
    bare `send(frame)` raised TypeError the moment a protocol engine passed
    the protocol-mandated safety kwargs (is_critical_command / user_confirmed
    / budget_category). The test recorder ignores the flags and records
    everything, exactly as before. P11 (G-11) adds `inbound_triggered` to
    both entry points so the protocol contract stays satisfied.

    *** TEST DOUBLE — NEVER A PRODUCTION TX PATH (P2-10) ***

    This class performs NO policy evaluation: it does not consult the
    whitelist, the rate limiter, the speed interlock, the E2E packager or the
    watchdog — it simply appends the frame. Wiring it into a live bus is a
    direct violation of AGENTS.md §2.1 ("ALL outbound CAN transmissions MUST
    pass through `TxSafetyGateway`"). The constructor therefore requires an
    explicit ``unsafe_test_double=True`` acknowledgement; the guard is
    deliberately redundant (it also refuses a ``False``/missing flag rather
    than defaulting to permissive) so no silent default can create a
    production-reachable TX path.
    """

    #: Marker read by audit tooling / tests proving this is a double.
    is_test_double: bool = True

    def __init__(self, *, unsafe_test_double: bool = True) -> None:
        if unsafe_test_double is not True:
            raise RuntimeError(
                "InMemoryTxPort is a TEST DOUBLE and must never be used on a "
                "production TX path (AGENTS.md §2.1: all TX goes through "
                "TxSafetyGateway). Pass unsafe_test_double=True to acknowledge "
                "this is a test/audit context."
            )
        self.sent_frames: list[CanFrame] = []

    async def send(
        self,
        frame: CanFrame,
        *,
        is_critical_command: bool = False,
        user_confirmed: bool = False,
        budget_category: str = "default",
        inbound_triggered: bool = False,
    ) -> None:
        """Record frame asynchronously.

        No whitelist / rate-limit / E2E / interlock evaluation happens here —
        see the class docstring (test double only, P2-10).
        """
        self.sent_frames.append(frame)

    def send_sync(
        self,
        frame: CanFrame,
        *,
        is_critical_command: bool = False,
        user_confirmed: bool = False,
        budget_category: str = "default",
        inbound_triggered: bool = False,
    ) -> None:
        """Record frame synchronously (test double only — see the class docstring)."""
        self.sent_frames.append(frame)

    def clear(self) -> None:
        """Clear recorded frame history."""
        self.sent_frames.clear()


class QueueRxSubscription:
    """Asyncio queue-backed RX subscription implementation.

    P3-5 (why this lives in core/contracts): it is the *reference*
    implementation of the `RxSubscription` port that core's protocol state
    machines are tested against, and it depends on nothing beyond
    ``asyncio.Queue``. Moving it to a test-utilities module under ``src/core``
    or ``tests/`` was evaluated and rejected: 8 test modules across
    ``tests/{unit,e2e}`` (plus ``test_obf_poller``-style pollers) import it
    from ``src.core.contracts.ports``, and ``src/hal``/``src/protocols`` reach
    it through the contracts package. The move would require editing files
    outside ``src/core/**``. It is documented as a double precisely because of
    this (see P1-4): it must not be presented as a production RX path.
    """

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
        # P1-4: no bare `except Exception: return None` here any more. Swallowing
        # every exception turned a programming error (or a broken queue) into
        # the same value as "no frame arrived" — a fail-open that a caller could
        # not distinguish from an idle bus. Only the two expected "nothing to
        # receive" signals are converted to None; everything else propagates.
        if timeout_s is None:
            return await self._queue.get()
        if timeout_s <= 0:
            try:
                return self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return None
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout_s)
        except (asyncio.TimeoutError, TimeoutError):
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
