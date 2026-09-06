"""Thread-Safe Multiplexed Bus Adapter enforcing Centralized Safety and Single RX Ownership.

Matches NO-GO Remediation Plan (v1.0 Release Blockers).
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar

from src.hal.base import AbstractBus

if TYPE_CHECKING:
    from src.core.models.can_frame import CanFrame
    from src.engine.router import FrameRouter
    from src.safety.gateway import TxSafetyGateway


class SafeMultiplexedBus(AbstractBus):
    """Adapter that routes physical TX through TxSafetyGateway and RX through FrameRouter.

    Resolves K-01: Removes physical TX capability from application layer.
    Resolves K-02: Prevents Frame Stealing by acting as an asynchronous Queue subscriber.

    B1 (REVIEW): the adapter must NOT capture the physical bus instance at
    construction time. `_reconnect_bus` swaps `app.bus` for a NEW driver
    instance and disconnects the old one; a client holding a multiplexed bus
    built around the old instance would then read stale `is_connected` /
    `channel_id` metadata (TX still works — it delegates to the gateway,
    whose `bus` is rebound; RX still works — it comes from the router). The
    bus is therefore resolved dynamically through a provider callable on
    every attribute access, so the adapter always reflects the live driver.
    """

    def __init__(
        self,
        physical_bus: AbstractBus | None = None,
        gateway: TxSafetyGateway | None = None,
        router: FrameRouter | None = None,
        *,
        bus_provider: Callable[[], AbstractBus] | None = None,
    ) -> None:
        if bus_provider is None and physical_bus is None:
            raise ValueError("SafeMultiplexedBus requires either physical_bus or bus_provider")
        if gateway is None or router is None:
            raise ValueError("SafeMultiplexedBus requires gateway and router")
        if bus_provider is not None:
            self._bus_provider = bus_provider
        else:
            assert physical_bus is not None  # narrowed above
            live_bus = physical_bus
            self._bus_provider = lambda: live_bus
        self.gateway = gateway
        self.router = router
        self._initialized = False
        # Resolve the live bus for the base-class fields; channel_id/bitrate
        # stay mutable properties below so they track reconnects.
        super().__init__(
            channel_id=self._bus_provider().channel_id,
            bitrate=self._bus_provider().bitrate,
            is_fd=self._bus_provider().is_fd,
        )
        self._initialized = True

        # Subscribe to FrameRouter for RX without stealing frames from hardware
        sub_id, rx_queue = self.router.subscribe(use_queue=True)
        self.sub_id: int | None = sub_id
        self.rx_queue: queue.Queue[CanFrame] | None = rx_queue
        if self.rx_queue is None:
            raise RuntimeError("SafeMultiplexedBus failed to obtain an RX queue from FrameRouter")

    @property
    def _live_bus(self) -> AbstractBus:
        """Current physical driver instance (re-resolved on every access)."""
        return self._bus_provider()

    @property
    def channel_id(self) -> str:
        """Live channel identity from the current physical bus."""
        return self._bus_provider().channel_id

    @channel_id.setter
    def channel_id(self, value: str) -> None:
        # The base-class constructor assigns this once before the provider is
        # meaningful; afterwards it is strictly derived from the live bus.
        if not self._initialized:
            self.__dict__["channel_id"] = value

    @property
    def bitrate(self) -> int:
        return self._bus_provider().bitrate

    @bitrate.setter
    def bitrate(self, value: int) -> None:
        if not self._initialized:
            self.__dict__["bitrate"] = value

    @property
    def is_fd(self) -> bool:
        return self._bus_provider().is_fd

    @is_fd.setter
    def is_fd(self, value: bool) -> None:
        if not self._initialized:
            self.__dict__["is_fd"] = value

    @property
    def is_connected(self) -> bool:
        """Live connection state reflected directly from the underlying physical bus."""
        return bool(self._bus_provider() and self._bus_provider().is_connected)

    @is_connected.setter
    def is_connected(self, value: bool) -> None:
        """Read-only delegation: connection state is strictly driven by the physical bus
        driver lifecycle (connect/disconnect), not by external property assignment."""
        pass

    def connect(self) -> None:
        """Physical bus connection is managed externally (e.g. by main UI)."""
        if not self._live_bus.is_connected:
            self._live_bus.connect()
        if self.sub_id is None:
            self.sub_id, self.rx_queue = self.router.subscribe(use_queue=True)

    def disconnect(self) -> None:
        """Unsubscribe from router upon teardown."""
        if self.sub_id is not None:
            self.router.unsubscribe(self.sub_id)
            self.sub_id = None
        self.rx_queue = None

    def send(
        self,
        frame: CanFrame,
        *,
        is_critical_command: bool = False,
        user_confirmed: bool = False,
    ) -> None:
        """Enforce CORE_SAFETY_FLOOR on every transmission."""
        self.gateway.validate_and_transmit(
            frame,
            is_critical_command=is_critical_command,
            user_confirmed=user_confirmed,
        )

    def send_sync(
        self,
        frame: CanFrame,
        *,
        is_critical_command: bool = False,
        user_confirmed: bool = False,
        budget_category: str = "default",
    ) -> None:
        """Synchronously transmit frame conforming to TxPort protocol."""
        self.gateway.validate_and_transmit(
            frame,
            is_critical_command=is_critical_command,
            user_confirmed=user_confirmed,
            budget_category=budget_category,
        )

    async def send_async(
        self,
        frame: CanFrame,
        *,
        is_critical_command: bool = False,
        user_confirmed: bool = False,
        budget_category: str = "default",
    ) -> None:
        """Asynchronously transmit frame conforming to TxPort protocol."""
        await self.gateway.send(
            frame,
            is_critical_command=is_critical_command,
            user_confirmed=user_confirmed,
            budget_category=budget_category,
        )

    DEFAULT_RECV_TIMEOUT_S: ClassVar[float] = 1.0

    def recv(self, timeout_s: float | None = None) -> CanFrame | None:
        """Asynchronously read from the dedicated subscription queue, avoiding hardware race conditions.

        A `None` timeout falls back to the bounded default (F-23) — the queue
        never blocks forever, so callers stay responsive.
        """
        if self.rx_queue is None:
            return None

        effective_timeout = timeout_s if timeout_s is not None else self.DEFAULT_RECV_TIMEOUT_S
        try:
            # Block until frame available or timeout
            return self.rx_queue.get(timeout=effective_timeout)
        except queue.Empty:
            return None
