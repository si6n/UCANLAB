"""RP1210Bus — AbstractBus adapter for the TMC RP1210 client.

Wire formats marshalled by the adapter (H-C-001 fix):

* 29-bit extended IDs (J1939 and ISO 15765-4 on RP1210): 4-byte
  little-endian 29-bit CAN identifier, 1 byte DLC, then the payload.
* 11-bit classic IDs: 2-byte little-endian header (bits 0-3: DLC,
  bits 4-15: 11-bit CAN identifier) followed by the payload.

Reception selects the layout from the negotiated client protocol: the
"J1939" protocol stack always delivers 29-bit frames in the 4+1 form,
while classic CAN protocols use the compact 2-byte header.
"""

from __future__ import annotations

import time
from typing import ClassVar

from src.core.errors import HardwareError
from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame
from src.hal.base import AbstractBus, BusMetrics, BusState
from src.hal.rp1210.client import RP1210Client

logger = get_logger("hal.rp1210.bus")

# 29-bit extended identifier mask (J1939 / ISO 15765-4 addressing)
_EXT_ID_MASK: int = 0x1FFFFFFF


def default_rp1210_dll_name() -> str:
    """Select the RP1210 DLL matching the running process bitness (H-C-004).

    A 64-bit interpreter cannot load the 32-bit RP121032.DLL (and vice
    versa); the vendor ships both entry points per the TMC RP1210 spec.
    """
    import struct

    return "RP121064.DLL" if struct.calcsize("P") * 8 == 64 else "RP121032.DLL"


class RP1210Bus(AbstractBus):
    """AbstractBus implementation over a TMC RP1210 vendor adapter (K4-a)."""

    DEFAULT_TX_BUFFER: ClassVar[int] = 8000
    DEFAULT_RX_BUFFER: ClassVar[int] = 8000
    MAX_CLASSIC_PAYLOAD: ClassVar[int] = 8
    # Protocols that are strictly 29-bit only (SAE J1939 standard).
    _STRICT_29BIT_PROTOCOLS: ClassVar[frozenset[str]] = frozenset(
        {"j1939", "j1939t"}
    )
    # Protocols whose RP1210 stacks deliver 29-bit frames in the
    # 4-byte-ID + 1-byte-DLC layout (case-insensitive).
    _EXTENDED_ID_PROTOCOLS: ClassVar[frozenset[str]] = frozenset(
        {"j1939", "j1939t", "iso15765", "iso_tp"}
    )

    def __init__(
        self,
        device_id: int = 1,
        protocol: str = "J1939",
        bitrate: int = 250000,
        dll_name: str | None = None,
        client: RP1210Client | None = None,
        listen_only: bool = True,
    ) -> None:
        super().__init__(channel_id=f"rp1210_dev{device_id}", bitrate=bitrate, is_fd=False)
        self.device_id = device_id
        self.protocol = protocol
        # P0-3 (REVIEW C-4): listen_only semantics for the RP1210 backend.
        # The RP1210 API has no standardized listen-only connect flag; the
        # only universally reliable passive guarantee this adapter can give
        # is refusing to transmit at all. `listen_only=True` (the default)
        # therefore opens the client session for RX but hard-blocks send().
        # An operator who needs TX must explicitly construct/connect with
        # listen_only=False after arming the gateway path — mirroring the
        # pcan/kvaser PASSIVE verification (HARDWARE_LISTEN_ONLY_UNSUPPORTED).
        self.listen_only = listen_only
        # Client may be injected for testing (a pre-mocked RP1210Client);
        # otherwise the real vendor DLL is loaded from System32 (D7 order).
        # H-C-004: the DLL entry point must match the process bitness.
        effective_dll = dll_name or default_rp1210_dll_name()
        self._client = client or RP1210Client(
            dll_name=effective_dll, device_id=device_id, protocol=protocol
        )
        self.metrics = BusMetrics(channel_id=self.channel_id, bitrate=bitrate)
        self.metrics.state = BusState.DISCONNECTED

    @property
    def _uses_extended_id_layout(self) -> bool:
        """True when the negotiated protocol speaks the 29-bit 4+1 layout."""
        return self.protocol.strip().lower() in self._EXTENDED_ID_PROTOCOLS

    # ------------------------------------------------------------------
    # AbstractBus contract
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Establish the RP1210 client session and mark the bus ACTIVE."""
        try:
            self._client.connect(
                tx_buffer_size=self.DEFAULT_TX_BUFFER, rx_buffer_size=self.DEFAULT_RX_BUFFER
            )
        except HardwareError:
            raise
        except (OSError, RuntimeError) as exc:
            raise HardwareError(
                f"RP1210 connect failed: {exc}",
                code="HARDWARE_CONNECT_FAILED",
                details={"device_id": self.device_id, "protocol": self.protocol},
                cause=exc,
            ) from exc
        self.is_connected = True
        # P0-3: mirror the pcan/kvaser state model — a listen_only session is
        # PASSIVE (RX-only), an explicitly armed TX session is ACTIVE.
        self.metrics.state = BusState.PASSIVE if self.listen_only else BusState.ACTIVE
        logger.info(
            "RP1210 bus connected",
            extra={
                "device_id": self.device_id,
                "protocol": self.protocol,
                "bitrate": self.bitrate,
                "listen_only": self.listen_only,
            },
        )

    def disconnect(self) -> None:
        """Gracefully close the RP1210 session (idempotent)."""
        try:
            self._client.disconnect()
        except (OSError, RuntimeError) as exc:
            logger.warning("RP1210 disconnect failed", extra={"error": str(exc)})
        finally:
            self.is_connected = False
            self.metrics.state = BusState.DISCONNECTED

    def send(self, frame: CanFrame) -> None:
        """Transmit a frame via the RP1210 adapter (canonical TX, D8).

        Wire layout (H-C-001):
          - extended (29-bit) IDs: ``<id:LE32><dlc:1><payload>``
          - classic (11-bit) IDs:  ``<(id<<4 | dlc):LE16><payload>``
        """
        if not self.is_connected:
            raise HardwareError("Cannot send: RP1210 bus is not connected")
        # P0-3 (REVIEW C-4): listen-only sessions hard-block TX. The RP1210
        # API has no vendor-portable listen-only connect mode, so refusing
        # send() is the only fail-closed guarantee that the adapter never
        # produces ACKs / transmissions against a live vehicle bus unless
        # the operator explicitly opened a TX session (listen_only=False).
        if self.listen_only:
            raise HardwareError(
                "Cannot send: RP1210 bus is opened in Listen-Only (passive) mode",
                code="HARDWARE_LISTEN_ONLY_TX_BLOCKED",
            )
        if frame.is_fd:
            raise HardwareError(
                "RP1210 classic CAN adapters do not support CAN-FD frames",
                code="HARDWARE_FRAME_REJECTED",
            )
        # L-4 (FABLE): oversized frames are REJECTED, never silently
        # truncated — a cropped frame would corrupt the message on the wire.
        if len(frame.data) > self.MAX_CLASSIC_PAYLOAD:
            raise HardwareError(
                f"Classic CAN frame payload exceeds {self.MAX_CLASSIC_PAYLOAD} bytes "
                f"(got {len(frame.data)}) — frame rejected",
                code="HARDWARE_FRAME_REJECTED",
            )
        data = bytes(frame.data)

        if frame.is_extended:
            wire = (frame.arbitration_id & _EXT_ID_MASK).to_bytes(4, "little") + bytes([len(data)]) + data
        elif self.protocol.strip().lower() in self._STRICT_29BIT_PROTOCOLS:
            # Strictly 29-bit J1939 stack rejects 11-bit frames (J1939-21 conformance)
            raise HardwareError(
                "11-bit frame sent on a 29-bit (J1939) protocol stack",
                code="HARDWARE_FRAME_REJECTED",
            )
        else:
            header = (frame.arbitration_id & 0x7FF) << 4 | (len(data) & 0x0F)
            wire = header.to_bytes(2, "little") + data

        self._client.send_message(wire)
        self.metrics.tx_frames += 1

    def recv(self, timeout_s: float | None = 0.1) -> CanFrame | None:
        """Poll one frame from the RP1210 RX queue within the timeout.

        H-H-004: adaptive backoff — the first empty polls stay at 1 ms
        latency for burst traffic, then relax toward 10 ms so an idle bus
        no longer burns a full CPU core per channel in busy-polling.
        """
        if not self.is_connected:
            raise HardwareError("Cannot receive: RP1210 bus is not connected")

        deadline = time.monotonic() + (timeout_s if timeout_s is not None else 0.1)
        poll_interval = 0.001
        while True:
            try:
                raw = self._client.read_message(block=False)
            except HardwareError as exc:
                # Transient RX errors are logged and polling continues until deadline (HIGH-2):
                # one malformed vendor packet must not kill the ingest loop or truncate timeout.
                logger.warning("RP1210 read error; retrying until deadline", extra={"error": str(exc)})
                if time.monotonic() >= deadline:
                    return None
                time.sleep(poll_interval)
                poll_interval = min(poll_interval * 2.0, 0.010)
                continue

            if raw is not None:
                # P0-3/H-2: derive the admission threshold from the layout
                # that will actually be used. iso15765/iso_tp speak the
                # 29-bit 4+1 layout, so a 2-4 byte vendor packet under those
                # protocols must be dropped as a runt — the old
                # strict-29bit-only threshold let it fall through to the
                # 11-bit decoder and fabricated telemetry.
                min_len = 5 if self._uses_extended_id_layout else 2
                if len(raw) >= min_len:
                    try:
                        frame = self._decode_rp1210_packet(raw)
                    except ValueError as exc:
                        # H-1: a malformed DLC (9..15 on non-FD) or otherwise
                        # invalid CanFrame input raises ValueError out of the
                        # decoder — count and continue polling instead of
                        # killing the whole RX session with an unhandled error.
                        logger.warning(
                            "RP1210 packet rejected by frame decoder; dropped",
                            extra={"error": str(exc), "length": len(raw)},
                        )
                        self.metrics.dropped_frames += 1
                        frame = None
                    if frame is not None:
                        self.metrics.rx_frames += 1
                        return frame
                else:
                    logger.warning(
                        "RP1210 packet shorter than minimum header (runt packet); dropped",
                        extra={"length": len(raw), "min_required": min_len},
                    )
                    self.metrics.dropped_frames += 1

            if time.monotonic() >= deadline:
                return None
            time.sleep(poll_interval)
            # Exponential-ish backoff capped at 10 ms while idle
            poll_interval = min(poll_interval * 2.0, 0.010)

    # ------------------------------------------------------------------
    # RP1210 wire format marshalling
    # ------------------------------------------------------------------

    def _decode_rp1210_packet(self, raw: bytes) -> CanFrame | None:
        """Decode an RP1210 packet into a CanFrame (layout by protocol)."""
        is_strict_29bit = self.protocol.strip().lower() in self._STRICT_29BIT_PROTOCOLS

        # 29-bit extended layout: 4-byte LE identifier + 1-byte DLC.
        if is_strict_29bit or (self._uses_extended_id_layout and len(raw) >= 5):
            if len(raw) < 5:
                logger.warning(
                    "RP1210 extended packet shorter than 4+1 header; dropped",
                    extra={"length": len(raw)},
                )
                self.metrics.dropped_frames += 1
                return None
            arb_id = int.from_bytes(raw[0:4], "little") & _EXT_ID_MASK
            dlc = raw[4] & 0x0F
            payload = raw[5 : 5 + dlc]

            if len(payload) < dlc:
                logger.warning(
                    "RP1210 packet shorter than declared DLC; dropped",
                    extra={"declared_dlc": dlc, "actual": len(payload)},
                )
                self.metrics.dropped_frames += 1
                return None

            # H-1: classic CAN carries only DLC 0..8. A wire byte of 9..15
            # (FD-style DLC codes) would construct a non-FD CanFrame whose
            # DLC invariant raises ValueError — reject here and count the
            # drop instead of letting the error escape into the RX loop.
            if dlc > 8:
                logger.warning(
                    "RP1210 extended packet declares classic-invalid DLC; dropped",
                    extra={"declared_dlc": dlc},
                )
                self.metrics.dropped_frames += 1
                return None

            return CanFrame(
                channel_id=self.channel_id,
                arbitration_id=arb_id,
                dlc=dlc,
                data=bytes(payload),
                is_extended=True,
                is_fd=False,
                direction="rx",
                timestamp_ns=time.time_ns(),
            )

        # Classic 11-bit layout: 2-byte header (DLC low nibble, ID in bits 4-15).
        if len(raw) < 2:
            logger.warning("RP1210 packet shorter than 2-byte header; dropped", extra={"length": len(raw)})
            self.metrics.dropped_frames += 1
            return None

        header = int.from_bytes(raw[:2], "little")
        dlc = header & 0x0F
        arb_id = (header >> 4) & 0x7FF
        payload = raw[2 : 2 + dlc]

        if len(payload) < dlc:
            logger.warning(
                "RP1210 packet shorter than declared DLC; dropped",
                extra={"declared_dlc": dlc, "actual": len(payload)},
            )
            self.metrics.dropped_frames += 1
            return None

        # H-1: same classic-DLC bound on the 11-bit layout.
        if dlc > 8:
            logger.warning(
                "RP1210 classic packet declares classic-invalid DLC; dropped",
                extra={"declared_dlc": dlc},
            )
            self.metrics.dropped_frames += 1
            return None

        return CanFrame(
            channel_id=self.channel_id,
            arbitration_id=arb_id,
            dlc=dlc,
            data=bytes(payload),
            is_extended=False,
            is_fd=False,
            direction="rx",
            timestamp_ns=time.time_ns(),
        )
