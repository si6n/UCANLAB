"""python-can Hardware Driver Wrapper (PEAK, Kvaser, Vector, GS_USB, Virtual).

Supports Listen-Only bitrate scanning and lossless CanFrame bi-directional translation.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from typing import Any, ClassVar

import can

from src.core.errors import HardwareError, TransportError
from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame, length_to_dlc
from src.hal.base import AbstractBus, BusState

logger = get_logger("hal.drivers")


class PythonCanBus(AbstractBus):
    """Universal python-can abstraction for industrial CAN transceivers."""

    def __init__(
        self,
        interface: str = "virtual",
        channel: str | int = "0",
        bitrate: int = 250000,
        data_bitrate: int | None = None,
        is_fd: bool = False,
        listen_only: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(channel_id=f"{interface}_{channel}", bitrate=bitrate, is_fd=is_fd)
        self.interface = interface
        self.channel = channel
        self.data_bitrate = data_bitrate
        self.listen_only = listen_only
        self.extra_kwargs = kwargs
        self._bus: can.BusABC | None = None
        # H-H-001: send()/recv()/disconnect() race guard — a send in flight
        # while another thread tears the driver down would hit a freed handle.
        self._lifecycle_lock = threading.Lock()
        self._active_sends = 0
        self._send_cond = threading.Condition(self._lifecycle_lock)
        # M-30 (P2-21): consecutive-error window — resets on any successful
        # RX so a recovered bus leaves BUS_OFF instead of staying latched.
        self._consecutive_error_frames = 0

    def connect(self) -> None:
        """Initialize physical transceiver connection via python-can.

        H5: runs under the lifecycle lock and is idempotent — a second
        connect() returns instead of leaking the first open handle (a
        "busy" PCAN/Kvaser channel).
        REVIEW.md 2.2: when listen_only is requested, the backend's actual
        state is VERIFIED after opening; backends that silently ignore the
        PASSIVE kwarg (slcan, some serial adapters) would ACK onto a live
        vehicle bus during bitrate scans — fail closed instead.
        """
        with self._lifecycle_lock:
            if self._bus is not None:
                logger.debug("connect() called while already connected — ignoring")
                return
            try:
                bus_kwargs: dict[str, Any] = {
                    "interface": self.interface,
                    "channel": self.channel,
                    "bitrate": self.bitrate,
                    "fd": self.is_fd,
                    **self.extra_kwargs,
                }

                if self.is_fd and self.data_bitrate:
                    bus_kwargs["data_bitrate"] = self.data_bitrate

                if self.listen_only:
                    # python-can expects the BusState enum member; BusState is a
                    # plain Enum (not a str-Enum), so the string "PASSIVE" fails
                    # the driver's membership check and raises ValueError.
                    bus_kwargs["state"] = can.BusState.PASSIVE

                self._bus = can.Bus(**bus_kwargs)

                # REVIEW.md 2.2: prove the backend honoured listen-only.
                # The pure-Python virtual bus never ACKs onto hardware, so
                # it is exempt from the fail-closed verification; physical
                # backends must prove PASSIVE state or refuse to open.
                if self.listen_only and self.interface not in ("virtual",):
                    actual_state = getattr(self._bus, "state", None)
                    if actual_state != can.BusState.PASSIVE:
                        try:
                            self._bus.shutdown()
                        except Exception:  # noqa: BLE001 — best-effort cleanup
                            pass
                        self._bus = None
                        raise HardwareError(
                            f"Interface '{self.interface}' does not support Listen-Only "
                            "(PASSIVE) mode — refusing an active connection that could "
                            "disturb a live vehicle bus",
                            code="HARDWARE_LISTEN_ONLY_UNSUPPORTED",
                        )

                self.is_connected = True
                self.metrics.state = BusState.PASSIVE if self.listen_only else BusState.ACTIVE
                logger.info(
                    "Connected CAN hardware interface",
                    extra={"interface": self.interface, "channel": str(self.channel), "bitrate": self.bitrate},
                )
            except HardwareError:
                self.is_connected = False
                self.metrics.state = BusState.DISCONNECTED
                raise
            except Exception as exc:  # noqa: BLE001
                # REVIEW2 #4: python-can backends raise more than
                # CanError/OSError/ValueError — optional-dependency
                # ImportError/ModuleNotFoundError, wrong-kwarg
                # AttributeError/TypeError, backend-specific exceptions. The
                # old narrow guard skipped the HardwareError wrap AND left
                # is_connected/metrics.state stale ( callers saw a phantom
                # ACTIVE bus). Wrap every failure and always clean state.
                self.is_connected = False
                self._bus = None
                self.metrics.state = BusState.DISCONNECTED
                raise HardwareError(
                    f"Failed to connect to CAN interface '{self.interface}:{self.channel}': "
                    f"{type(exc).__name__}: {str(exc)[:400]}",
                    code="HARDWARE_CONNECT_FAILED",
                    details={"interface": self.interface, "channel": str(self.channel), "bitrate": self.bitrate},
                    cause=exc,
                ) from exc

    # H-8 (P1-4): bounded drain — a wedged vendor send must never hold the
    # lifecycle lock (and thus every subsequent send/connect/disconnect)
    # hostage forever.
    DISCONNECT_DRAIN_TIMEOUT_S: ClassVar[float] = 2.0

    def set_listen_only(self, listen_only: bool) -> bool:
        """Flip the backend bus state between PASSIVE and ACTIVE (verified).

        REVIEW (arm_tx atomicity): arming TX used to flip only the
        supervisor while the driver stayed PASSIVE — the first send then
        raised HardwareError. The desktop arming path now calls this and
        only proceeds when the driver confirmed the requested mode.

        Returns True when the backend reports the requested state. A
        backend whose state cannot be read back after the assignment is
        treated as unverified (fail-closed: False).
        """
        with self._lifecycle_lock:
            if not self.is_connected or self._bus is None:
                return False
            target = can.BusState.PASSIVE if listen_only else can.BusState.ACTIVE
            try:
                self._bus.state = target
                actual_state = getattr(self._bus, "state", None)
                if actual_state is not None and actual_state != target:
                    logger.error(
                        "Backend did not confirm bus state change",
                        extra={"target": target, "actual": actual_state},
                    )
                    return False
            except (NotImplementedError, AttributeError):
                # Software-simulated bus (virtual / loopback) does not expose hardware transceiver state
                pass
            except Exception as exc:  # noqa: BLE001 — backend refusal
                logger.error(
                    "Backend refused bus state change",
                    extra={"target": target, "error": str(exc)},
                )
                return False
            self.listen_only = listen_only
            self.metrics.state = BusState.PASSIVE if listen_only else BusState.ACTIVE
            return True

    def disconnect(self) -> None:
        """Shutdown CAN bus and release transceiver handles."""
        with self._lifecycle_lock:
            self.is_connected = False
            # H-8 (P1-4): wait for in-flight sends with a TOTAL monotonic
            # deadline, not per-iteration timeouts — the old
            # `while > 0: wait(0.06)` loop spun forever if a vendor send
            # ignored its timeout and never decremented _active_sends.
            drain_deadline = time.monotonic() + self.DISCONNECT_DRAIN_TIMEOUT_S
            while self._active_sends > 0:
                remaining = drain_deadline - time.monotonic()
                if remaining <= 0:
                    logger.error(
                        "In-flight sends did not drain within deadline; forcing shutdown",
                        extra={"stranded_sends": self._active_sends},
                    )
                    # Forcing the counter to zero releases the drain loop;
                    # the vendor shutdown() below still runs best-effort.
                    self._active_sends = 0
                    break
                self._send_cond.wait(timeout=min(0.06, remaining))

            if self._bus is not None:
                try:
                    self._bus.shutdown()
                except (can.CanError, OSError) as exc:
                    logger.warning("Error during CAN bus shutdown", extra={"error": str(exc)[:500]})
                finally:
                    self._bus = None
                    self.metrics.state = BusState.DISCONNECTED

    def send(self, frame: CanFrame) -> None:
        """Transmit CanFrame on physical bus.

        H6: the handle snapshot is taken under the lock, and active send count
        tracked so disconnect() cleanly drains in-flight sends before shutdown().
        """
        with self._lifecycle_lock:
            if not self.is_connected or self._bus is None:
                raise HardwareError("Cannot send: CAN bus is not connected")

            if self.listen_only:
                raise HardwareError("Cannot send: CAN bus is opened in Listen-Only (passive) mode")

            bus_snapshot = self._bus
            self._active_sends += 1

        try:
            msg = can.Message(
                arbitration_id=frame.arbitration_id,
                is_extended_id=frame.is_extended,
                data=frame.padded_data,
                is_fd=frame.is_fd,
                bitrate_switch=frame.brs,
                error_state_indicator=frame.esi,
                check=True,
            )
            # Timeout guards a wedged vendor driver; supported by socketcan &
            # most native backends.
            bus_snapshot.send(msg, timeout=0.05)
            self.metrics.tx_frames += 1
        except (can.CanError, ValueError) as exc:
            self.metrics.error_frames += 1
            raise TransportError(
                f"Hardware frame construction/transmission failed: {str(exc)[:500]}",
                code="TRANSPORT_FRAME_INVALID",
                cause=exc,
            ) from exc
        except TypeError as exc:
            self.metrics.error_frames += 1
            raise TransportError(
                f"Hardware frame rejected by driver: {str(exc)[:500]}",
                code="TRANSPORT_FRAME_INVALID",
                cause=exc,
            ) from exc
        finally:
            with self._lifecycle_lock:
                self._active_sends -= 1
                if self._active_sends == 0:
                    self._send_cond.notify_all()

    # H7: consecutive error frames before the driver latches BUS_OFF metrics.
    # The latch is OBSERVED by the composition root (desktop telemetry loop
    # polls metrics.state each tick) and reported to TxSafetyGateway
    # .notify_bus_off, which turns it into an E-Stop + supervisor FAULT + TX
    # fence bump while TX is permitted. The driver itself never imports the
    # safety layer (HAL stays dependency-free).
    ERROR_FRAMES_BUS_OFF_THRESHOLD: ClassVar[int] = 128

    def recv(self, timeout_s: float | None = 0.1) -> CanFrame | None:
        """Receive single CAN frame with timeout.

        The blocking recv runs OUTSIDE the lifecycle lock (it may wait the
        full timeout); only the handle snapshot is taken under the lock so
        a concurrent disconnect cannot free the bus mid-call.

        REVIEW.md 2.3: the vendor C layer can raise OSError/RuntimeError
        (not just can.CanError) on a wedged/removed handle — catch them so
        one driver hiccup never kills the RX loop.
        H3: remote frames carry data=b'' with DLC>0, which violates the
        CanFrame invariant — filtered like error frames.
        H7: bus state is probed; ERROR/BUS_OFF updates metrics + supervisor.
        """
        with self._lifecycle_lock:
            if not self.is_connected or self._bus is None:
                raise HardwareError("Cannot receive: CAN bus is not connected")
            bus_snapshot = self._bus

        try:
            msg = bus_snapshot.recv(timeout=timeout_s)
        except (can.CanError, OSError, RuntimeError, AttributeError) as exc:
            self.metrics.error_frames += 1
            raise HardwareError(
                f"Hardware frame read error: {str(exc)[:500]}",
                code="HARDWARE_READ_ERROR",
                cause=exc,
            ) from exc

        if msg is None:
            return None

        if msg.is_error_frame:
            self.metrics.error_frames += 1
            # H7: sustained error frames indicate bus-off conditions
            # M-30 (P2-21): count CONSECUTIVE errors — a physical bus that
            # recovers must not stay BUS_OFF latched for the process
            # lifetime on the strength of old errors.
            self._consecutive_error_frames += 1
            if self._consecutive_error_frames >= self.ERROR_FRAMES_BUS_OFF_THRESHOLD:
                self.metrics.state = BusState.BUS_OFF
            return None

        # H3: remote request frames — no payload, cannot satisfy the DLC
        # invariant, so they must be dropped; but they are legal Classic CAN
        # traffic (ISO 11898-1), NOT hardware errors. Count them in their own
        # metric so a healthy bus full of RTR polls never inflates
        # error_frames toward ERROR_FRAMES_BUS_OFF_THRESHOLD (B5).
        if msg.is_remote_frame:
            self.metrics.rtr_frames += 1
            return None

        # M-30 (P2-21): a successfully received frame proves the bus
        # recovered — reset the consecutive-error window and leave
        # BUS_OFF-latched state (the event was logged; the condition is gone).
        if self._consecutive_error_frames > 0:
            self._consecutive_error_frames = 0
            if self.metrics.state == BusState.BUS_OFF:
                logger.info("Bus recovered from BUS_OFF — resuming normal reception")
                self.metrics.state = BusState.PASSIVE if self.listen_only else BusState.ACTIVE
        self.metrics.rx_frames += 1
        ts_ns = int(msg.timestamp * 1_000_000_000) if msg.timestamp else time.time_ns()

        # H7: reflect the controller state when the backend exposes it
        state = getattr(bus_snapshot, "state", None)
        if state == can.BusState.ERROR:
            self.metrics.state = BusState.BUS_OFF

        try:
            # REVIEW 2-M1: python-can returns ONLY the bytes received — many
            # classic frames arrive with fewer bytes than the DLC declares
            # (drivers do not pad). The CanFrame DLC invariant (CORE-C-001)
            # requires exact length, so raw short payloads were rejected as
            # "malformed" and silently dropped, killing real-world RX traffic.
            # Pad to the declared DLC length (ISO 11898-1 wire padding).
            dlc = msg.dlc if msg.dlc is not None else length_to_dlc(len(msg.data))
            rx_data = bytes(msg.data)
            if len(rx_data) < dlc:
                rx_data = rx_data + bytes([0x00] * (dlc - len(rx_data)))
            return CanFrame(
                channel_id=self.channel_id,
                arbitration_id=msg.arbitration_id,
                dlc=dlc,
                data=rx_data,
                is_extended=msg.is_extended_id,
                is_fd=msg.is_fd,
                brs=msg.bitrate_switch,
                esi=msg.error_state_indicator,
                direction="rx",
                timestamp_ns=ts_ns,
                source="physical",
            )
        except ValueError as exc:
            # Malformed on-wire frame (e.g. DLC/data mismatch from a flaky
            # driver) — count and skip instead of killing the RX loop.
            self.metrics.error_frames += 1
            logger.debug("Malformed RX frame dropped", extra={"error": str(exc)[:500]})
            return None

    @classmethod
    def scan_bitrate(
        cls,
        interface: str,
        channel: str | int,
        candidates: Sequence[int] = (250000, 500000, 125000, 1000000),
        listen_timeout_s: float = 0.5,
    ) -> int | None:
        """Scan CAN line in Listen-Only mode to auto-detect valid bitrate without ACK disturbance."""
        for rate in candidates:
            bus = None
            try:
                bus = cls(interface=interface, channel=channel, bitrate=rate, listen_only=True)
                bus.connect()
                frame = bus.recv(timeout_s=listen_timeout_s)
                if frame is not None:
                    logger.info("Auto-detected active bitrate", extra={"bitrate": rate})
                    return rate
            except (can.CanError, OSError, HardwareError) as exc:
                logger.debug("Bitrate candidate failed", extra={"bitrate": rate, "error": str(exc)[:500]})
            finally:
                # F-22: always release the bus, including on exception paths
                if bus is not None:
                    try:
                        bus.disconnect()
                    except (can.CanError, OSError) as exc:
                        logger.debug(
                            "Disconnect after bitrate probe failed",
                            extra={"bitrate": rate, "error": str(exc)[:500]},
                        )
        return None
