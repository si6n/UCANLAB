"""Abstract Base CAN Interface (HAL) and Metrics Model."""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from types import TracebackType
from typing import Self

from src.core.models.can_frame import CanFrame


class BusState(str, enum.Enum):
    """Bus operational state enum."""

    ACTIVE = "active"
    PASSIVE = "passive"
    BUS_OFF = "bus_off"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    STOPPED = "stopped"

    def __str__(self) -> str:
        return self.value


@dataclass(slots=True)
class BusMetrics:
    """Real-time bus operational metrics and statistics."""

    channel_id: str
    rx_frames: int = 0
    tx_frames: int = 0
    error_frames: int = 0
    # Remote Transmission Request frames are protocol-legal Classic CAN
    # traffic (ISO 11898-1), not hardware errors — count them separately so a
    # busy RTR bus can never inflate error_frames toward the BUS_OFF
    # threshold (B5/REVIEW: 128+ dropped RTRs used to trip a fake bus-off).
    rtr_frames: int = 0
    dropped_frames: int = 0
    bus_load_percent: float = 0.0
    bitrate: int = 250000
    data_bitrate: int | None = None
    state: BusState = BusState.ACTIVE


class AbstractBus(ABC):
    """Abstract Base Class for all CAN hardware and virtual interfaces."""

    def __init__(self, channel_id: str, bitrate: int = 250000, is_fd: bool = False) -> None:
        self.channel_id = channel_id
        self.bitrate = bitrate
        self.is_fd = is_fd
        self.is_connected = False
        self.metrics = BusMetrics(channel_id=channel_id, bitrate=bitrate)

    @abstractmethod
    def connect(self) -> None:
        """Initialize and connect to the physical or virtual CAN channel."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Close connection and release hardware resources."""
        ...

    @abstractmethod
    def send(self, frame: CanFrame) -> None:
        """Canonical hardware transmission routine.

        Concrete drivers implement THIS method — there is no second TX entry
        point. Upper application and protocol layers MUST NOT call it
        directly; transmissions must be routed through TxPort /
        TxSafetyGateway (D8: the old send/_send_raw mutual delegation could
        recurse infinitely for drivers that only overrode send()).
        """
        ...

    def privileged_send(self, frame: CanFrame) -> None:
        """Explicit privileged TX entry point for the safety gateway.

        The gateway validates a frame through its full 6-stage policy and then
        dispatches via THIS method — an auditable, public port instead of the
        former duck-typed reach into the driver's private `_send_raw`.
        Default behaviour is the canonical `send`; drivers needing
        gateway-exempt low-level access override this deliberately.
        """
        self.send(frame)

    def _send_raw(self, frame: CanFrame) -> None:
        """Legacy shim for drivers that historically implemented only `_send_raw`.

        Bridges to the canonical `send()`. Recursion-safe by construction:
        `send` is abstract and must be implemented by every concrete driver,
        so this shim can never dispatch back into itself.
        """
        self.send(frame)

    @abstractmethod
    def recv(self, timeout_s: float | None = 0.1) -> CanFrame | None:
        """Receive a single CanFrame within timeout. Returns None on timeout."""
        ...

    def get_metrics(self) -> BusMetrics:
        """Return current bus performance metrics."""
        return self.metrics

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.disconnect()
