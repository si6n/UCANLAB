"""Universal CAN-Bus Diagnostic & Telemetry Platform - E2E Safety Tx Packager.

Handles outbound frame safety sealing: increments rolling counter, injects sequence metadata,
computes CRC-8 / checksum bytes, and generates ASIL-compliant CanFrame structures for TxPort dispatch.
"""

from __future__ import annotations

import threading

from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame, dlc_to_length, length_to_dlc
from src.safety.e2e.profiles import (
    E2EProfileConfig,
    compute_checksum,
    inject_counter,
    inject_crc,
)

logger = get_logger("safety.e2e.packager")


class E2ESafetyPackager:
    """Thread-safe stateful frame packager that applies E2E protection to outgoing CAN frames."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._counters: dict[tuple[str, int], int] = {}

    def _resolve_counter(
        self,
        stream_key: tuple[str, int],
        profile: E2EProfileConfig,
        counter: int | None,
    ) -> int:
        """Resolve the counter to seal with (rewind-rejecting, jump-audited).

        External `counter` is KEPT for backward compat (validator gap/resync
        harness crafts SOME_LOST/WRONG_SEQUENCE vectors through it). Forward
        jumps are honored but audit-logged (the validator still classifies
        them SOME_LOST/WRONG_SEQUENCE). Rewinds/repeats (normalized <= current
        and not the immediate monotonic successor, incl. modulo wrap) raise
        ValueError + audit instead of silently resynchronizing the stream.
        Production callers must use counter=None (auto-increment, monotonic
        by construction). The packager is NOT wired into any production TX
        path (wiring gate).
        """
        if counter is None:
            current_val = self._counters.get(stream_key, -1)
            counter_to_use = (current_val + 1) % profile.counter_modulo
            self._counters[stream_key] = counter_to_use
            return counter_to_use
        if not isinstance(counter, int) or isinstance(counter, bool):
            raise ValueError(f"E2E counter must be int, got {type(counter).__name__}")
        normalized = counter % profile.counter_modulo
        if stream_key in self._counters:
            current = self._counters[stream_key]
            expected = (current + 1) % profile.counter_modulo
            if normalized != expected:
                if normalized <= current and not (
                    current == profile.counter_modulo - 1 and normalized == 0
                ):
                    # Repeat or rewind (wrap successor already handled via
                    # expected==0 above, so reaching here with normalized==0
                    # and current==modulo-1 cannot happen; guard anyway).
                    logger.warning(
                        "E2E counter rewind/repeat rejected",
                        extra={
                            "stream": stream_key,
                            "expected_next": expected,
                            "current": current,
                            "requested": normalized,
                        },
                    )
                    raise ValueError(
                        f"E2E counter rewind/repeat rejected for {stream_key}: "
                        f"current {current}, requested {normalized} (expected {expected})"
                    )
                # Forward jump: honor for validator classification, but audit.
                logger.warning(
                    "E2E explicit counter jumps forward",
                    extra={
                        "stream": stream_key,
                        "expected_next": expected,
                        "current": current,
                        "requested": normalized,
                    },
                )
        self._counters[stream_key] = normalized
        return normalized

    def package(
        self,
        frame: CanFrame,
        profile: E2EProfileConfig,
        counter: int | None = None,
        timestamp_ns: int | None = None,
    ) -> CanFrame:
        """Package and seal an outgoing CanFrame with rolling counter and computed CRC."""
        stream_key = (frame.channel_id, frame.arbitration_id)
        # R2-G6: preserve the original frame timestamp (monotonic domain);
        # wall-clock stamping used to reorder telemetry on NTP jumps.
        ts = timestamp_ns if timestamp_ns is not None else frame.timestamp_ns

        with self._lock:
            counter_to_use = self._resolve_counter(stream_key, profile, counter)

            payload = bytearray(frame.data)

            # Ensure payload has sufficient length to hold counter and CRC offsets
            # R2-G6: pad with the canonical 0xCC (can_frame.pad_payload), not 0x00.
            min_len = max(profile.crc_byte_offset, profile.counter_byte_offset) + 1
            if len(payload) < min_len:
                payload.extend(b"\xCC" * (min_len - len(payload)))

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
            counter_to_use = self._resolve_counter(stream_key, profile, counter)

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
        """Explicitly set the counter value for a given stream (audited, monotonic).

        Rewinds are rejected fail-closed: lowering the stored counter raises
        ValueError + audit instead of silently reopening a replay window.
        Privileged re-sync must go through reset() + forward seal.
        """
        if not isinstance(counter, int) or isinstance(counter, bool):
            raise ValueError(f"E2E counter must be int, got {type(counter).__name__}")
        with self._lock:
            key = (channel_id, arbitration_id)
            current = self._counters.get(key)
            if current is not None and counter < current:
                logger.warning(
                    "E2E set_counter rewind rejected",
                    extra={"stream": key, "current": current, "requested": counter},
                )
                raise ValueError(
                    f"E2E set_counter rewind rejected for {key}: "
                    f"current {current}, requested {counter}"
                )
            self._counters[key] = counter
            logger.warning(
                "E2E counter explicitly set",
                extra={"stream": key, "value": counter},
            )

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
