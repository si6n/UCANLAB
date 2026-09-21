"""Rolling counter detection in CAN message payloads."""

from __future__ import annotations

from collections.abc import Sequence

from src.engine.discovery.hypotheses import Evidence, Hypothesis


class CounterDetector:
    """Detects monotonic rolling counters with moduli 4..65536 across byte/nibble boundaries.

    Supports Little-Endian (Intel) and Motorola (Big-Endian) byte-aligned 16-bit
    candidates plus bit-packed (non-byte-aligned) 12-bit candidates.
    """

    CANDIDATE_WIDTHS = (
        (4, 16),   # 4-bit nibbles (mod 16)
        (8, 256),  # 8-bit bytes (mod 256)
        (2, 4),    # 2-bit counters (mod 4)
        (3, 8),    # 3-bit counters (mod 8)
        (6, 64),   # 6-bit counters (mod 64)
    )

    @classmethod
    def detect(cls, payloads: Sequence[bytes], dlc: int) -> list[Hypothesis]:
        """Scan payload streams for rolling counter patterns."""
        if len(payloads) < 10 or dlc <= 0:
            return []

        hypotheses: list[Hypothesis] = []
        tested_spans: set[tuple[int, int, str]] = set()

        # Priority 1: Check 8-bit byte-aligned candidates
        for byte_idx in range(dlc):
            start_bit = byte_idx * 8
            cls._evaluate_field(payloads, start_bit=start_bit, length=8, modulus=256, hypotheses=hypotheses)
            tested_spans.add((start_bit, 8, "little"))

        # Priority 1b: 16-bit byte-aligned candidates, both byte orders.
        # Motorola (Big-Endian): MSB in byte_idx, LSB in byte_idx+1 — the
        # classic big-endian rolling counter layout (e.g. some Bosch/Continental ECUs).
        for byte_idx in range(dlc - 1):
            start_bit = byte_idx * 8
            cls._evaluate_field(payloads, start_bit=start_bit, length=16, modulus=65536, hypotheses=hypotheses)
            tested_spans.add((start_bit, 16, "little"))
            cls._evaluate_field(
                payloads, start_bit=start_bit, length=16, modulus=65536, hypotheses=hypotheses, byte_order="big"
            )
            tested_spans.add((start_bit, 16, "big"))

        # Priority 2: Check 4-bit nibble candidates
        for byte_idx in range(dlc):
            # Low nibble (bits 0..3)
            start_bit_low = byte_idx * 8
            cls._evaluate_field(payloads, start_bit=start_bit_low, length=4, modulus=16, hypotheses=hypotheses)
            tested_spans.add((start_bit_low, 4, "little"))

            # High nibble (bits 4..7)
            start_bit_high = byte_idx * 8 + 4
            cls._evaluate_field(payloads, start_bit=start_bit_high, length=4, modulus=16, hypotheses=hypotheses)
            tested_spans.add((start_bit_high, 4, "little"))

        # Priority 3: Check 2-bit, 3-bit, 6-bit candidates if byte isn't already claimed
        for byte_idx in range(dlc):
            for length, mod in ((2, 4), (3, 8), (6, 64)):
                for bit_offset in range(0, 8 - length + 1, max(1, length)):
                    start_bit = byte_idx * 8 + bit_offset
                    if (start_bit, length, "little") not in tested_spans:
                        cls._evaluate_field(
                            payloads,
                            start_bit=start_bit,
                            length=length,
                            modulus=mod,
                            hypotheses=hypotheses,
                        )
                        tested_spans.add((start_bit, length, "little"))

        # Priority 4: bit-packed 12-bit candidates (Intel) crossing nibble/byte
        # boundaries — offsets 0 and 4 within each byte pair.
        # ponytail: packed Motorola candidates are skipped because DbcBuilder
        # maps big-endian start bits byte-aligned only; add when builder
        # learns DBC Motorola nibble numbering.
        if dlc >= 2:
            for byte_idx in range(dlc - 1):
                for bit_offset in (0, 4):
                    start_bit = byte_idx * 8 + bit_offset
                    if (start_bit, 12, "little") in tested_spans:
                        continue
                    cls._evaluate_field(
                        payloads, start_bit=start_bit, length=12, modulus=4096, hypotheses=hypotheses
                    )
                    tested_spans.add((start_bit, 12, "little"))

        # Sort hypotheses by confidence descending
        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        return hypotheses

    @classmethod
    def _evaluate_field(
        cls,
        payloads: Sequence[bytes],
        start_bit: int,
        length: int,
        modulus: int,
        hypotheses: list[Hypothesis],
        byte_order: str = "little",
    ) -> None:
        """Evaluate if a bit slice acts as a modulo-N rolling counter.

        `start_bit` is the hypothesis anchor: for little-endian it is the LSB
        position; for big-endian byte-aligned candidates it is the MSB byte
        anchor (extraction starts at bit `start_bit + 7`).
        """
        extract_start = start_bit if byte_order != "big" else start_bit + 7

        values: list[int] = []
        for p in payloads:
            val = cls._extract_bits(p, extract_start, length, byte_order)
            if val is not None:
                values.append(val)

        if len(values) < 10:
            return

        valid_steps = 0
        static_repeats = 0
        total_transitions = len(values) - 1

        for i in range(total_transitions):
            v1 = values[i]
            v2 = values[i + 1]

            if v2 == (v1 + 1) % modulus:
                valid_steps += 1
            elif v2 == v1:
                static_repeats += 1

        active_transitions = total_transitions - static_repeats
        if active_transitions < 5:
            return

        step_ratio = valid_steps / active_transitions
        overall_compliance = (valid_steps + static_repeats) / total_transitions

        # We require at least 90% step compliance and active counter progression
        if step_ratio >= 0.90 and valid_steps >= 5:
            confidence = round(step_ratio * 0.8 + overall_compliance * 0.2, 4)
            byte_idx = start_bit // 8
            bit_in_byte = start_bit % 8
            endian_label = "Big-Endian (Motorola)" if byte_order == "big" else "Little-Endian (Intel)"
            name_suffix = "_BE" if byte_order == "big" else ""

            hypotheses.append(
                Hypothesis(
                    htype="COUNTER",
                    start_bit=start_bit,
                    length=length,
                    is_little_endian=byte_order != "big",
                    params={
                        "modulus": modulus,
                        "byte_index": byte_idx,
                        "bit_in_byte": bit_in_byte,
                        "byte_order": byte_order,
                        "valid_steps": valid_steps,
                        "total_transitions": total_transitions,
                    },
                    confidence=confidence,
                    evidence=[
                        Evidence(
                            kind="monotonicity",
                            value=step_ratio,
                            detail=(
                                f"Bit {start_bit} ({length}-bit, {endian_label}) increments modulo {modulus} "
                                f"in {valid_steps}/{active_transitions} active transitions ({step_ratio:.1%})."
                            ),
                        )
                    ],
                    name=f"COUNTER_B{byte_idx}_b{bit_in_byte}_l{length}_M{modulus}{name_suffix}",
                )
            )

    @staticmethod
    def _extract_bits(
        payload: bytes, start_bit: int, length: int, byte_order: str = "little"
    ) -> int | None:
        """Extract a bitfield from payload bytes.

        little: LSB0 continuous numbering across bytes (Intel).
        big:    Motorola-contiguous order — the bit at `start_bit` is the MSB,
                proceeding down within the byte then to bit 7 of the next byte.
                Only correct for byte-aligned candidates (start_bit % 8 == 7).
        """
        if byte_order == "big":
            val = 0
            pos = start_bit
            for _ in range(length):
                byte_idx, bit_idx = divmod(pos, 8)
                if byte_idx >= len(payload):
                    return None
                val = (val << 1) | ((payload[byte_idx] >> bit_idx) & 1)
                # Motorola-contiguous: bit 0 of byte k -> bit 7 of byte k+1
                pos = pos - 1 if bit_idx != 0 else pos + 15
            return val

        val = 0
        shift = 0
        pos = start_bit
        for _ in range(length):
            byte_idx, bit_idx = divmod(pos, 8)
            if byte_idx >= len(payload):
                return None
            val |= ((payload[byte_idx] >> bit_idx) & 1) << shift
            shift += 1
            pos += 1
        return val
