"""Statistical bit-level and entropy analysis for CAN message payloads."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence


class BitStats:
    """Calculates Shannon entropy, bit flip frequency, and transition metrics."""

    @staticmethod
    def compute_shannon_entropy(byte_values: Sequence[int]) -> float:
        """Compute Shannon entropy in bits (0.0 to 8.0) for a sequence of 8-bit byte values."""
        if not byte_values:
            return 0.0

        counts = Counter(byte_values)
        total = len(byte_values)
        entropy = 0.0

        for count in counts.values():
            p = count / total
            if p > 0:
                entropy -= p * math.log2(p)

        return round(entropy, 4)

    @staticmethod
    def compute_flip_rates(payloads: Sequence[bytes], dlc: int) -> list[float]:
        """Compute the bit transition rate (0.0 to 1.0) for each of the (dlc * 8) bits."""
        if len(payloads) < 2 or dlc <= 0:
            return [0.0] * (dlc * 8)

        total_transitions = len(payloads) - 1
        num_bits = dlc * 8
        flip_counts = [0] * num_bits

        for i in range(total_transitions):
            p1 = payloads[i]
            p2 = payloads[i + 1]
            min_len = min(len(p1), len(p2), dlc)

            for byte_idx in range(min_len):
                diff = p1[byte_idx] ^ p2[byte_idx]
                if diff:
                    for bit in range(8):
                        if (diff >> bit) & 1:
                            flip_counts[byte_idx * 8 + bit] += 1

        return [round(c / total_transitions, 4) for c in flip_counts]

    @classmethod
    def classify_bits(cls, flip_rates: Sequence[float]) -> list[str]:
        """Classify each bit based on its transition profile."""
        classifications: list[str] = []
        for rate in flip_rates:
            if rate == 0.0:
                classifications.append("CONST")
            elif rate <= 0.20:
                classifications.append("INC")
            elif rate <= 0.70:
                classifications.append("TOGGLE")
            else:
                classifications.append("NOISY")
        return classifications
