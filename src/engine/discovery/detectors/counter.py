"""Rolling counter detection in CAN message payloads."""

from __future__ import annotations

from collections.abc import Sequence

from src.engine.discovery.hypotheses import Evidence, Hypothesis


class CounterDetector:
    """Detects monotonic rolling counters with moduli 4, 8, 16, 64, 256 across byte/nibble boundaries."""

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
        tested_spans: set[tuple[int, int]] = set()

        # Priority 1: Check 8-bit byte-aligned candidates
        for byte_idx in range(dlc):
            start_bit = byte_idx * 8
            cls._evaluate_field(payloads, start_bit=start_bit, length=8, modulus=256, hypotheses=hypotheses)
            tested_spans.add((start_bit, 8))

        # Priority 2: Check 4-bit nibble candidates
        for byte_idx in range(dlc):
            # Low nibble (bits 0..3)
            start_bit_low = byte_idx * 8
            cls._evaluate_field(payloads, start_bit=start_bit_low, length=4, modulus=16, hypotheses=hypotheses)
            tested_spans.add((start_bit_low, 4))

            # High nibble (bits 4..7)
            start_bit_high = byte_idx * 8 + 4
            cls._evaluate_field(payloads, start_bit=start_bit_high, length=4, modulus=16, hypotheses=hypotheses)
            tested_spans.add((start_bit_high, 4))

        # Priority 3: Check 2-bit, 3-bit, 6-bit candidates if byte isn't already claimed
        for byte_idx in range(dlc):
            for length, mod in ((2, 4), (3, 8), (6, 64)):
                for bit_offset in range(0, 8 - length + 1, max(1, length)):
                    start_bit = byte_idx * 8 + bit_offset
                    if (start_bit, length) not in tested_spans:
                        cls._evaluate_field(
                            payloads,
                            start_bit=start_bit,
                            length=length,
                            modulus=mod,
                            hypotheses=hypotheses,
                        )
                        tested_spans.add((start_bit, length))

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
    ) -> None:
        """Evaluate if a bit slice acts as a modulo-N rolling counter."""
        values: list[int] = []
        for p in payloads:
            val = cls._extract_bits(p, start_bit, length)
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

            hypotheses.append(
                Hypothesis(
                    htype="COUNTER",
                    start_bit=start_bit,
                    length=length,
                    params={
                        "modulus": modulus,
                        "byte_index": byte_idx,
                        "bit_in_byte": bit_in_byte,
                        "valid_steps": valid_steps,
                        "total_transitions": total_transitions,
                    },
                    confidence=confidence,
                    evidence=[
                        Evidence(
                            kind="monotonicity",
                            value=step_ratio,
                            detail=(
                                f"Bit {start_bit} ({length}-bit) increments modulo {modulus} "
                                f"in {valid_steps}/{active_transitions} active transitions ({step_ratio:.1%})."
                            ),
                        )
                    ],
                    name=f"COUNTER_B{byte_idx}_b{bit_in_byte}_l{length}_M{modulus}",
                )
            )

    @staticmethod
    def _extract_bits(payload: bytes, start_bit: int, length: int) -> int | None:
        """Extract a little-endian bitfield from payload bytes."""
        byte_idx = start_bit // 8
        bit_idx = start_bit % 8

        if byte_idx >= len(payload):
            return None

        # Handle simple single-byte bit slices
        if bit_idx + length <= 8:
            mask = (1 << length) - 1
            return (payload[byte_idx] >> bit_idx) & mask

        # Handle multi-byte bit slices (up to 16 bits)
        if byte_idx + 1 < len(payload) and bit_idx + length <= 16:
            val = payload[byte_idx] | (payload[byte_idx + 1] << 8)
            mask = (1 << length) - 1
            return (val >> bit_idx) & mask

        return None
