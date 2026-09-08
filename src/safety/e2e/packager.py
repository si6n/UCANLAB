"""Universal CAN-Bus Diagnostic & Telemetry Platform - E2E Safety Tx Packager.

Handles outbound frame safety sealing: increments rolling counter, injects sequence metadata,
computes CRC-8 / checksum bytes, and generates ASIL-compliant CanFrame structures for TxPort dispatch.
"""

from __future__ import annotations

import threading
import time

from src.core.models.can_frame import CanFrame, dlc_to_length, length_to_dlc
from src.safety.e2e.profiles import (
    E2EProfileConfig,
    compute_checksum,
    inject_counter,
    inject_crc,
)


class E2ESafetyPackager:
    """Thread-safe stateful frame packager that applies E2E protection to outgoing CAN frames."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._counters: dict[tuple[str, int], int] = {}

    def package(
        self,
        frame: CanFrame,
        profile: E2EProfileConfig,
        counter: int | None = None,
        timestamp_ns: int | None = None,
    ) -> CanFrame:
        """Package and seal an outgoing CanFrame with rolling counter and computed CRC."""
        stream_key = (frame.channel_id, frame.arbitration_id)
        ts = timestamp_ns if timestamp_ns is not None else time.time_ns()

        with self._lock:
            if counter is None:
                current_val = self._counters.get(stream_key, -1)
                counter_to_use = (current_val + 1) % profile.counter_modulo
                self._counters[stream_key] = counter_to_use
            else:
                counter_to_use = counter % profile.counter_modulo
                self._counters[stream_key] = counter_to_use

            payload = bytearray(frame.data)

            # Ensure payload has sufficient length to hold counter and CRC offsets
            min_len = max(profile.crc_byte_offset, profile.counter_byte_offset) + 1
            if len(payload) < min_len:
                payload.extend(b"\x00" * (min_len - len(payload)))

            # Inject rolling counter
            inject_counter(payload, counter_to_use, profile)

            # Determine final payload length & DLC before checksum calculation
            final_len = len(payload)
            effective_dlc = length_to_dlc(final_len) if final_len > dlc_to_length(frame.dlc) else frame.dlc

            # Compute CRC over payload containing updated counter and accurate DLC
            crc_val = compute_checksum(
                data=payload,
                config=profile,
                arbitration_id=frame.arbitration_id,
                dlc=effective_dlc,
            )

            # Inject computed CRC
            inject_crc(payload, crc_val, profile)
            sealed_data = bytes(payload)

            return CanFrame.create(
                channel_id=frame.channel_id,
                arbitration_id=frame.arbitration_id,
                data=sealed_data,
                is_extended=frame.is_extended,
                is_fd=frame.is_fd,
                brs=frame.brs,
                dlc=effective_dlc,
                direction="tx",
                timestamp_ns=ts,
                source=frame.source,
            )

    def package_payload(
        self,
        data: bytes,
        profile: E2EProfileConfig,
        arbitration_id: int = 0,
        channel_id: str = "default",
        dlc: int | None = None,
        counter: int | None = None,
    ) -> tuple[bytes, int, int]:
        """Package a raw byte buffer, returning (sealed_data, counter_used, computed_crc)."""
        stream_key = (channel_id, arbitration_id)
        with self._lock:
            if counter is None:
                current_val = self._counters.get(stream_key, -1)
                counter_to_use = (current_val + 1) % profile.counter_modulo
                self._counters[stream_key] = counter_to_use
            else:
                counter_to_use = counter % profile.counter_modulo
                self._counters[stream_key] = counter_to_use

            payload = bytearray(data)
            min_len = max(profile.crc_byte_offset, profile.counter_byte_offset) + 1
            if len(payload) < min_len:
                payload.extend(b"\x00" * (min_len - len(payload)))

            inject_counter(payload, counter_to_use, profile)

            # M-3 (P1-10): the DLC must be a DLC CODE (0..15), not a byte
            # count — the RX side passes the real frame DLC, so a >15-byte
            # payload here desynchronized the two CRC computations for every
            # profile with include_dlc_in_crc=True.
            effective_dlc = dlc if dlc is not None else length_to_dlc(len(payload))
            crc_val = compute_checksum(
                data=payload,
                config=profile,
                arbitration_id=arbitration_id,
                dlc=effective_dlc,
            )
            inject_crc(payload, crc_val, profile)

            return bytes(payload), counter_to_use, crc_val

    def get_counter(self, channel_id: str, arbitration_id: int) -> int | None:
        """Get the last counter value used for a given stream."""
        with self._lock:
            return self._counters.get((channel_id, arbitration_id))

    def set_counter(self, channel_id: str, arbitration_id: int, counter: int) -> None:
        """Explicitly set the counter value for a given stream."""
        with self._lock:
            self._counters[(channel_id, arbitration_id)] = counter

    def reset(self, channel_id: str | None = None, arbitration_id: int | None = None) -> None:
        """Reset sequence counters."""
        with self._lock:
            if channel_id is None and arbitration_id is None:
                self._counters.clear()
            elif channel_id is not None and arbitration_id is not None:
                self._counters.pop((channel_id, arbitration_id), None)
            elif channel_id is not None:
                keys_to_remove = [k for k in self._counters if k[0] == channel_id]
                for k in keys_to_remove:
                    self._counters.pop(k, None)
            elif arbitration_id is not None:
                keys_to_remove = [k for k in self._counters if k[1] == arbitration_id]
                for k in keys_to_remove:
                    self._counters.pop(k, None)
