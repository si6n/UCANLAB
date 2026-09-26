"""python-can Hardware Driver Wrapper (PEAK, Kvaser, Vector, GS_USB, Virtual).

Supports Listen-Only bitrate scanning and lossless CanFrame bi-directional translation.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from typing import Any, ClassVar

import can

from src.core.errors import HardwareError, TransportError
from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame, dlc_to_length, length_to_dlc
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
        # HAL-21 (part 1): an ALIASED canonical key (e.g. passing `bitrate=`
        # twice via `**{"bitrate": ...}` or the common `fd=`/`interface=`
        # spellings) is bound to the named parameter by Python itself, so it
        # never appears in `kwargs` and the collision check below cannot see
        # it. Python permits this silent override for ALL calls (not just
        # dict-expansion ones), so it is rejected here or a caller could
        # retune the already-validated bus configuration by accident.
        aliases = {
            "bitrate": bitrate,
            "fd": is_fd,
            "interface": interface,
            "channel": channel,
        }
        for key, value in aliases.items():
            if key in kwargs:
                raise ValueError(
                    "extra python-can kwargs must not override canonical bus "
                    f"configuration; colliding keys: ['{key}']"
                )
            _ = value  # documented pairing: key -> canonical parameter
        super().__init__(channel_id=f"{interface}_{channel}", bitrate=bitrate, is_fd=is_fd)
        self.interface = interface
        self.channel = channel
        self.data_bitrate = data_bitrate
        self.listen_only = listen_only
        self.extra_kwargs = kwargs
        # HAL-21: reject backend kwargs that would collide with the canonical
        # configuration we already validated and log. Accepting them silently
        # made `interface=`/`bitrate=`/`fd=` overridable behind the operator's
        # back (e.g. a "listen-only" scan silently retuned to a live baud).
        canonical_keys = {
            "interface",
            "channel",
            "bitrate",
            "fd",
            "state",
            "data_bitrate",
        }
        collisions = sorted(canonical_keys.intersection(kwargs))
        if collisions:
            raise ValueError(
                "extra python-can kwargs must not override canonical bus configuration; "
                f"colliding keys: {collisions}"
            )
        self._bus: can.BusABC | None = None
        # H-H-001: send()/recv()/disconnect() race guard — a send in flight
        # while another thread tears the driver down would hit a freed handle.
        self._lifecycle_lock = threading.Lock()
        self._active_sends = 0
        self._active_recvs = 0
        self._send_cond = threading.Condition(self._lifecycle_lock)
        self._drain_cond = self._send_cond
        # M-30 (P2-21): consecutive-error window — resets on any successful
        # RX so a recovered bus leaves BUS_OFF instead of staying latched.
        self._consecutive_error_frames = 0
        # HAL-06: last fail-closed hardware verification verdict. Set by
        # `assert_bitrate_applied` and readable by the composition root so the
        # UI can refuse to label an unverified session as "250 kbit/s".
        self.hardware_unverified_code: str | None = None

    def _bitrate_verdict(self, bus: can.BusABC | None, connected: bool) -> tuple[bool, str | None]:
        """HAL-06: lock-free bitrate verification core.

        Split out of `assert_bitrate_applied` so `connect()` — which already
        holds the NON-re-entrant `_lifecycle_lock` — can obtain the verdict
        without self-deadlocking. Returns `(verified, unverified_code)`.

        The pure-Python `virtual` backend has no transceiver, so there is no
        rate to diverge from and the software configuration is authoritative;
        it is exempt (mirroring the identical exemption in `connect`/`set_listen_only`).
        """
        if self.interface in ("virtual",):
            return True, None
        if bus is None or not connected:
            return False, "HARDWARE_BITRATE_UNVERIFIED"
        reported = getattr(bus, "bitrate", None)
        if reported is None:
            # Some backends (socketcan, vendor DLLs) expose no rate property.
            reported = getattr(getattr(bus, "_channel_info", None), "bitrate", None)
        if isinstance(reported, int) and not isinstance(reported, bool) and reported == self.bitrate:
            return True, None
        logger.warning(
            "HARDWARE_BITRATE_UNVERIFIED: backend does not report the requested bitrate",
            extra={"requested": self.bitrate, "reported": reported, "interface": self.interface},
        )
        return False, "HARDWARE_BITRATE_UNVERIFIED"

    def assert_bitrate_applied(self) -> bool:
        """HAL-06 (fail-closed): prove the requested bitrate reached hardware.

        The python-can backends in this adapter accept `bitrate=` and pass it
        to the vendor driver, but a vendor DLL that ignores it (or uses an
        adapter-INI default) leaves the transceiver on ITS baud while the
        session claims `self.bitrate`. Reading the rate back is the only proof
        this HAL can obtain, and it is NOT available on every backend
        (`can.BusABC` does not expose a bitrate property).

        Contract: return True only when the backend's reported bitrate is
        readable and equals the requested one. Otherwise set
        `hardware_unverified_code = "HARDWARE_BITRATE_UNVERIFIED"` and return
        False — the caller MUST treat that as fail-closed (no telemetry
        attribution, no TX arming) rather than assuming the requested rate.
        """
        with self._lifecycle_lock:
            verified, code = self._bitrate_verdict(self._bus, self.is_connected)
        self.hardware_unverified_code = code
        return verified

    def _hw_listen_only_confirmed(self) -> bool | None:
        """Independent hardware read of PCAN_LISTEN_ONLY (fix 1).

        python-can's `PcanBus.state` getter returns the flag its setter just
        wrote — reading it back is CIRCULAR and never proves the hardware
        `SetValue(PCAN_LISTEN_ONLY, ...)` command landed. When the driver
        exposes `m_objPCANBasic`/`m_PcanHandle`, ask the adapter directly.
        Returns True/False on a successful hardware read, None when no
        independent read is available (non-PCAN backend, test stub, or the
        read itself failed) so callers can warn instead of pretending.
        """
        api = getattr(self._bus, "m_objPCANBasic", None)
        handle = getattr(self._bus, "m_PcanHandle", None)
        if api is None or handle is None or not callable(getattr(api, "GetValue", None)):
            return None
        try:
            from can.interfaces.pcan.basic import PCAN_LISTEN_ONLY

            err, value = api.GetValue(handle, PCAN_LISTEN_ONLY)
            if int(err) != 0:
                return None
            return bool(value)
        except Exception:  # noqa: BLE001 — hardware read unavailable: fall back
            return None

    def connect(self) -> None:
        """Initialize physical transceiver connection via python-can.

        H5: runs under the lifecycle lock and is idempotent — a second
        connect() returns instead of leaking the first open handle (a
        "busy" PCAN/Kvaser channel).
        REVIEW 2.2: when listen_only is requested, the backend's actual
        state is VERIFIED after opening; backends that silently ignore the
        PASSIVE kwarg (slcan, some serial adapters) would ACK onto a live
        vehicle bus during bitrate scans — fail closed instead.
        """
        with self._lifecycle_lock:
            if self._bus is not None:
                logger.debug("connect() called while already connected — ignoring")
                return
            try:
                # HAL-21: `**self.extra_kwargs` is spread FIRST so a caller's
                # stray `extra_kwargs={"bitrate": 1000000}` can never silently
                # override the canonical interface/channel/bitrate/fd values.
                # It used to be spread LAST, winning over the constructor
                # arguments and retuning the already-confirmed bus config.
                bus_kwargs: dict[str, Any] = {
                    **self.extra_kwargs,
                    "interface": self.interface,
                    "channel": self.channel,
                    "bitrate": self.bitrate,
                    "fd": self.is_fd,
                }

                if self.is_fd and self.data_bitrate:
                    bus_kwargs["data_bitrate"] = self.data_bitrate

                if self.listen_only:
                    # python-can expects the BusState enum member; BusState is a
                    # plain Enum (not a str-Enum), so the string "PASSIVE" fails
                    # the driver's membership check and raises ValueError.
                    bus_kwargs["state"] = can.BusState.PASSIVE

                self._bus = can.Bus(**bus_kwargs)

                # REVIEW 2.2: prove the backend honoured listen-only.
                # The pure-Python virtual bus never ACKs onto hardware, so
                # it is exempt from the fail-closed verification; physical
                # backends must prove PASSIVE state or refuse to open.
                # FIX 1: the python-can `state` read-back is SOFTWARE-FLAG-ONLY
                # (the getter returns what the setter wrote), so it can never
                # prove a hardware PCAN_LISTEN_ONLY command landed. On PCAN an
                # independent hardware read is attempted: a hardware "OFF"
                # fails closed, an unavailable read is warned about explicitly
                # (anti-ACK guarantee is software-enforced). The existing
                # PASSIVE fail-closed check below is kept either way.
                if self.listen_only and self.interface not in ("virtual",):
                    actual_state = getattr(self._bus, "state", None)
                    hw_listen_only = self._hw_listen_only_confirmed()
                    if actual_state != can.BusState.PASSIVE or hw_listen_only is False:
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
                    if hw_listen_only is None:
                        logger.warning(
                            "Listen-only verified from python-can state ONLY (software flag) "
                            "— hardware listen-only could not be independently verified; "
                            "anti-ACK guarantee is software-enforced",
                            extra={"interface": self.interface, "channel": str(self.channel)},
                        )

                self.is_connected = True
                self.metrics.state = BusState.PASSIVE if self.listen_only else BusState.ACTIVE
                # HAL-06: record the fail-closed hardware bitrate verdict. A
                # physical backend that cannot report the requested rate is
                # flagged HARDWARE_BITRATE_UNVERIFIED so no telemetry is
                # attributed to an assumed baud. Deliberately computed INLINE
                # from the already-snapshotted handle: `connect()` holds
                # `_lifecycle_lock`, which is a plain (NON-re-entrant)
                # `threading.Lock`, so calling the lock-taking
                # `assert_bitrate_applied()` here would self-deadlock.
                _, self.hardware_unverified_code = self._bitrate_verdict(
                    self._bus, self.is_connected
                )
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
                # HAL-01 (residual): the old condition was
                # `actual_state is not None and actual_state != target`, so a
                # backend that reports NO state at all (getattr -> None) fell
                # through and the call returned True — a phantom "mode
                # applied" for a transceiver whose state is unknowable. An
                # unknown state now fails CLOSED, exactly like a mismatching
                # one. The `interface == "virtual"` exemption below is what
                # keeps a software-only backend (no transceiver to verify)
                # from being rejected.
                if actual_state != target:
                    logger.error(
                        "Backend did not confirm bus state change",
                        extra={"target": target, "actual": actual_state},
                    )
                    return False
                # FIX 1: the read-back above is CIRCULAR on PCAN (the getter
                # returns what the setter just wrote). Ask the hardware
                # directly when the driver exposes the PCANBasic API: a
                # mismatch fails closed, an unavailable read is warned about
                # (software-flag-only verification).
                hw_listen_only = self._hw_listen_only_confirmed()
                if hw_listen_only is not None and hw_listen_only != listen_only:
                    logger.error(
                        "Hardware PCAN_LISTEN_ONLY does not match requested mode",
                        extra={"requested_listen_only": listen_only, "hw_listen_only": hw_listen_only},
                    )
                    return False
                if hw_listen_only is None and getattr(self, "interface", None) != "virtual":
                    logger.warning(
                        "Listen-only change verified from python-can state ONLY (software "
                        "flag) — hardware listen-only could not be independently verified",
                        extra={"requested_listen_only": listen_only},
                    )
            except (NotImplementedError, AttributeError):
                # S1-P1-4 (fail-closed): the backend cannot express hardware
                # transceiver state (e.g. a serial/slcan adapter whose state
                # property is read-only or absent). The OLD code swallowed this
                # and still flipped `self.listen_only` + returned True — a
                # PHANTOM SUCCESS that reported "disarmed" while the hardware
                # kept ACTIVE (still ACK-ing on the vehicle bus) and "armed"
                # while the hardware stayed PASSIVE (dead-but-armed protocol).
                #
                # The pure-Python virtual backend is the ONE legitimate
                # exemption: it has no transceiver, so there is no hardware
                # state to diverge from and the software flag is authoritative.
                # This mirrors the identical `interface == "virtual"` exemption
                # already applied in `connect()` when proving PASSIVE.
                if getattr(self, "interface", None) == "virtual":
                    self.listen_only = listen_only
                    self.metrics.state = BusState.PASSIVE if listen_only else BusState.ACTIVE
                    return True
                logger.error(
                    "Backend cannot mutate listen-only state; refusing to report unverified mode change",
                    extra={"target": str(target), "requested_listen_only": listen_only},
                )
                return False
            except Exception as exc:  # noqa: BLE001 — backend refusal
                logger.error(
                    "Backend refused bus state change",
                    extra={"target": target, "error": str(exc)},
                )
                return False
            self.listen_only = listen_only
            self.metrics.state = BusState.PASSIVE if listen_only else BusState.ACTIVE
            return True

    def flush_tx_buffer(self) -> None:
        """R2-H1: E-Stop abort hook — drain the backend TX queue if exposed.

        Delegates to the python-can backend's `flush_tx_buffer` when the
        backend provides one (e.g. socketcan); otherwise a no-op so the
        gateway's `_resolve_driver_flush` finds a registered (harmless) hook
        instead of silently running without abort coverage.
        """
        # HAL-14: the handle snapshot must be read under `_lifecycle_lock`
        # like every other accessor. Reading `self._bus` unlocked let a
        # concurrent `disconnect()` null the attribute between the read and
        # the call, so the flush ran against a torn/closed handle and the
        # E-Stop abort hook silently did nothing.
        with self._lifecycle_lock:
            bus = self._bus
        flush = getattr(bus, "flush_tx_buffer", None)
        if callable(flush):
            try:
                flush()
            except Exception:
                logger.warning("Backend flush_tx_buffer failed (best-effort)", exc_info=True)

    def disconnect(self) -> None:
        """Shutdown CAN bus and release transceiver handles."""
        with self._lifecycle_lock:
            self.is_connected = False
            # H-8 (P1-4): wait for in-flight sends and recvs with a TOTAL monotonic
            # deadline, not per-iteration timeouts — the old
            # `while > 0: wait(0.06)` loop spun forever if a vendor send
            # ignored its timeout and never decremented _active_sends.
            drain_deadline = time.monotonic() + self.DISCONNECT_DRAIN_TIMEOUT_S
            while self._active_sends > 0 or self._active_recvs > 0:
                remaining = drain_deadline - time.monotonic()
                if remaining <= 0:
                    logger.error(
                        "In-flight operations did not drain within deadline; forcing shutdown",
                        extra={"stranded_sends": self._active_sends, "stranded_recvs": self._active_recvs},
                    )
                    self._active_sends = 0
                    self._active_recvs = 0
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
        except (can.CanError, ValueError, OSError, RuntimeError) as exc:
            # FIX 7: the vendor C layer also surfaces OSError/RuntimeError on a
            # wedged/removed handle (recv already catches them) — one driver
            # hiccup must wrap as TransportError, not escape raw.
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
                self._active_sends = max(0, self._active_sends - 1)
                if self._active_sends == 0 and self._active_recvs == 0:
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

        REVIEW 2.3: the vendor C layer can raise OSError/RuntimeError
        (not just can.CanError) on a wedged/removed handle — catch them so
        one driver hiccup never kills the RX loop.
        H3: remote frames carry data=b'' with DLC>0, which violates the
        CanFrame invariant — filtered like error frames.
        H7: bus state is probed; ERROR/BUS_OFF updates metrics + supervisor.
        HAL-31 (fix 6): timeout_s is validated per the AbstractBus contract
        before any snapshot is taken (negative/NaN/bool/out-of-range rejected).
        """
        # HAL-31 contract (copied from RP1210Bus.recv): None = block
        # indefinitely, 0 = non-blocking, 0 < x <= 60 = timed; everything else
        # (negative, NaN, bool, non-numeric, > 60) fails closed with ValueError.
        if timeout_s is not None and (
            not isinstance(timeout_s, (int, float))
            or isinstance(timeout_s, bool)
            or not (0 <= timeout_s <= 60)
        ):
            raise ValueError(f"timeout_s must be None, 0, or in range (0, 60], got {timeout_s!r}")

        with self._lifecycle_lock:
            if not self.is_connected or self._bus is None:
                raise HardwareError("Cannot receive: CAN bus is not connected")
            bus_snapshot = self._bus
            self._active_recvs += 1

        try:
            msg = bus_snapshot.recv(timeout=timeout_s)
        except (can.CanError, OSError, RuntimeError, AttributeError) as exc:
            # FIX: a teardown racing this recv closes the handle — that is not
            # a hardware fault, so return None instead of HARDWARE_READ_ERROR.
            with self._lifecycle_lock:
                if not self.is_connected:
                    return None
            self.metrics.error_frames += 1
            raise HardwareError(
                f"Hardware frame read error: {str(exc)[:500]}",
                code="HARDWARE_READ_ERROR",
                cause=exc,
            ) from exc
        finally:
            with self._lifecycle_lock:
                self._active_recvs = max(0, self._active_recvs - 1)
                if self._active_sends == 0 and self._active_recvs == 0:
                    self._send_cond.notify_all()

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
        # HAL-24: `if msg.timestamp` treats a legitimate 0.0 hardware timestamp
        # (first frame of a capture, or a driver with no clock) as absent and
        # substituted the wall clock, producing a timestamp that jumped
        # forward-by-decades relative to the capture anchor. An explicit
        # `is not None` test keeps the driver-supplied epoch intact.
        ts_ns = int(msg.timestamp * 1_000_000_000) if msg.timestamp is not None else time.time_ns()

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
            # HAL-02 (cosmetic): `msg.dlc` is the raw DLC CODE, not a byte
            # count — for FD frames DLC 9..15 must be mapped through
            # `dlc_to_length` (12/16/20/24/32/48/64) before padding, otherwise
            # the frame is handed on one byte short of its own declared DLC.
            # NOTE: this is a cosmetic capture-fidelity fix, NOT the "silent
            # corruption" the review claimed — `CanFrame` deliberately permits
            # a short FD payload (`0 < len(data) <= capacity`), so no data was
            # ever lost; only the trailing zero padding was missing.
            dlc = msg.dlc if msg.dlc is not None else length_to_dlc(len(msg.data))
            expected_len = dlc_to_length(dlc)
            rx_data = bytes(msg.data)
            if len(rx_data) < expected_len:
                rx_data = rx_data + bytes([0x00] * (expected_len - len(rx_data)))
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
        min_valid_frames: int = 10,
        _bus_factory: Callable[..., Any] | None = None,
    ) -> int | None:
        """Scan CAN line in Listen-Only mode to auto-detect valid bitrate without ACK disturbance.

        HAL-19: a WRONG baud rate on a live bus still yields occasional
        decodable-looking frames (bit-stuffing and error frames alias into
        well-formed IDs). The old implementation returned on the FIRST frame
        it could parse, which latched an incorrect rate onto the session and
        produced confidently wrong telemetry (AGENTS.md §2.3). A candidate is
        now only accepted when it delivers `min_valid_frames` consecutive
        non-error frames, with ZERO error frames observed and the controller
        not latched into BUS_OFF/ERROR.

        `_bus_factory` is a test seam: it replaces `cls(...)` so the HAL-19
        acceptance policy can be exercised without a real transceiver. It is
        private and must never be supplied by production callers.
        """
        factory = _bus_factory or cls
        for rate in candidates:
            bus = None
            try:
                bus = factory(interface=interface, channel=channel, bitrate=rate, listen_only=True)
                bus.connect()
                valid = 0
                deadline = time.monotonic() + listen_timeout_s
                while valid < min_valid_frames:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    frame = bus.recv(timeout_s=min(remaining, 0.05))
                    if frame is None:
                        continue
                    valid += 1
                metrics = bus.get_metrics()
                bus_off = metrics.state in (BusState.BUS_OFF, BusState.ERROR)
                if valid >= min_valid_frames and metrics.error_frames == 0 and not bus_off:
                    logger.info(
                        "Auto-detected active bitrate",
                        extra={"bitrate": rate, "valid_frames": valid},
                    )
                    return rate
                logger.debug(
                    "Bitrate candidate rejected (insufficient clean frames)",
                    extra={
                        "bitrate": rate,
                        "valid_frames": valid,
                        "required": min_valid_frames,
                        "error_frames": metrics.error_frames,
                        "state": str(metrics.state),
                    },
                )
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
