"""Signal segmentation, multi-byte boundary detection, endianness and signedness analysis."""

from __future__ import annotations

from collections.abc import Sequence

from src.engine.discovery.hypotheses import Evidence, Hypothesis


class SignalSegmenter:
    """Groups unassigned active bits into physical signal candidates (1-byte, 2-byte, 4-byte)."""

    @classmethod
    def segment(
        cls,
        payloads: Sequence[bytes],
        dlc: int,
        occupied_spans: Sequence[tuple[int, int]] | None = None,
    ) -> list[Hypothesis]:
        """Segment remaining payload bytes into candidate physical signals."""
        if len(payloads) < 5 or dlc <= 0:
            return []

        occupied: set[int] = set()
        if occupied_spans:
            for start, length in occupied_spans:
                for b in range(start, start + length):
                    occupied.add(b)

        hypotheses: list[Hypothesis] = []
        byte_available = [True] * dlc
        for byte_idx in range(dlc):
            # Mark byte unavailable if any of its bits are occupied
            if any((byte_idx * 8 + bit) in occupied for bit in range(8)):
                byte_available[byte_idx] = False

        # Phase 1: Search for 2-byte (16-bit) candidate signals
        byte_idx = 0
        while byte_idx < dlc - 1:
            if byte_available[byte_idx] and byte_available[byte_idx + 1]:
                sig16 = cls._evaluate_16bit_signal(payloads, byte_idx)
                if sig16 is not None:
                    hypotheses.append(sig16)
                    byte_available[byte_idx] = False
                    byte_available[byte_idx + 1] = False
                    byte_idx += 2
                    continue
            byte_idx += 1

        # Phase 1b: bit-packed 12-bit candidates at nibble offsets 0/4 of
        # remaining adjacent byte pairs (Intel LSB0 numbering). These may
        # overlap 16-bit SIGNAL spans — the DBC builder resolves overlaps
        # by confidence (16-bit 0.85 > 12-bit 0.70); here we only avoid
        # counter/checksum-occupied bits.
        # ponytail: packed Motorola candidates skipped — DbcBuilder maps
        # big-endian start bits byte-aligned only; add when it learns DBC
        # Motorola nibble numbering.
        for byte_idx in range(dlc - 1):
            for bit_offset in (0, 4):
                start_bit = byte_idx * 8 + bit_offset
                span = range(start_bit, start_bit + 12)
                if any(b in occupied for b in span):
                    continue
                sig12 = cls._evaluate_packed_12bit_signal(payloads, byte_idx, bit_offset)
                if sig12 is not None:
                    hypotheses.append(sig12)

        # Phase 2: For any remaining available single bytes, emit 8-bit signal candidate
        for b_idx in range(dlc):
            if byte_available[b_idx]:
                sig8 = cls._evaluate_8bit_signal(payloads, b_idx)
                if sig8 is not None:
                    hypotheses.append(sig8)

        return hypotheses

    @staticmethod
    def _extract_bits_lsb0(payload: bytes, start_bit: int, length: int) -> int | None:
        """Extract an Intel (LSB0) bitfield that may cross byte boundaries."""
        val = 0
        for i in range(length):
            byte_idx, bit_idx = divmod(start_bit + i, 8)
            if byte_idx >= len(payload):
                return None
            val |= ((payload[byte_idx] >> bit_idx) & 1) << i
        return val

    @classmethod
    def _evaluate_packed_12bit_signal(
        cls, payloads: Sequence[bytes], byte_idx: int, bit_offset: int
    ) -> Hypothesis | None:
        """Evaluate a bit-packed 12-bit signal at bits byte_idx*8+bit_offset .. +11."""
        start_bit = byte_idx * 8 + bit_offset
        values: list[int] = []
        for p in payloads:
            v = cls._extract_bits_lsb0(p, start_bit, 12)
            if v is not None:
                values.append(v)

        if len(values) < 5:
            return None

        min_v = float(min(values))
        max_v = float(max(values))
        if max_v == min_v:
            return None  # constant packed field — 8-bit/16-bit phases already report the bytes

        return Hypothesis(
            htype="SIGNAL",
            start_bit=start_bit,
            length=12,
            is_little_endian=True,
            is_signed=False,
            min_value=min_v,
            max_value=max_v,
            confidence=0.70,
            params={"byte_order": "little", "bit_offset": bit_offset},
            evidence=[
                Evidence(
                    kind="entropy",
                    value=float(len(set(values))),
                    detail=(
                        f"Bit-packed 12-bit candidate at bits {start_bit}..{start_bit + 11} "
                        f"(Intel); range [{min_v:.0f}, {max_v:.0f}]."
                    ),
                )
            ],
            name=f"SIG_B{byte_idx}_o{bit_offset}_12B",
        )

    @classmethod
    def _evaluate_16bit_signal(cls, payloads: Sequence[bytes], byte_idx: int) -> Hypothesis | None:
        """Evaluate a 16-bit physical signal spanning byte_idx and byte_idx+1."""
        raw_b0 = [p[byte_idx] for p in payloads if len(p) > byte_idx + 1]
        raw_b1 = [p[byte_idx + 1] for p in payloads if len(p) > byte_idx + 1]

        if len(raw_b0) < 5:
            return None

        # Check activity: if both bytes are completely constant, it might be padding or constant
        b0_unique = len(set(raw_b0))
        b1_unique = len(set(raw_b1))
        if b0_unique == 1 and b1_unique == 1:
            # Constant 16-bit field
            return Hypothesis(
                htype="CONSTANT",
                start_bit=byte_idx * 8,
                length=16,
                is_little_endian=True,
                params={"constant_value": raw_b0[0] | (raw_b1[0] << 8)},
                confidence=0.95,
                evidence=[Evidence(kind="constancy", value=1.0, detail="Field has zero variance")],
                name=f"CONST_B{byte_idx}_16B",
            )

        # Endianness determination:
        # REVIEW 3 (endian evidence): the unique-count heuristic alone
        # declared Big-Endian whenever the MSB byte repeated, even for
        # plain LE counters. Decide with series monotonicity — for an
        # ascending raw series the LSB byte changes every step in the
        # correct byte order; the wrong order looks shuffled.
        le_vals = [(p[byte_idx] | (p[byte_idx + 1] << 8)) for p in payloads if len(p) > byte_idx + 1]
        be_vals = [(p[byte_idx] << 8) | p[byte_idx + 1] for p in payloads if len(p) > byte_idx + 1]

        def _monotonic_ratio(vals: list[int]) -> float:
            if len(vals) < 2:
                return 0.0
            inc = sum(1 for i in range(1, len(vals)) if vals[i] >= vals[i - 1])
            return inc / (len(vals) - 1)

        # Prefer the byte order whose series is smoother (higher
        # non-decreasing ratio); tie-break on unique-count as before.
        le_mono, be_mono = _monotonic_ratio(le_vals), _monotonic_ratio(be_vals)
        if abs(le_mono - be_mono) >= 0.05:
            is_le = le_mono > be_mono
        else:
            is_le = b0_unique >= b1_unique

        # REVIEW 3 (signedness): a 16-bit field whose LE/BE series spends
        # its whole life below 0x8000 but whose high byte carries both
        # 0x00-ish and 0xFF-ish runs plausibly encodes a negative physical
        # value (two's complement). Flag it as a signed candidate so the
        # operator reviews it instead of silently exporting unsigned.
        is_signed = False
        if is_le:
            values = le_vals
        else:
            values = be_vals
        highs = [v >> 8 for v in values]
        if min(values) < 0x8000 and set(highs) & {0xFF} and max(values) - min(values) > 0x8000:
            is_signed = True
        min_v = float(min(values))
        max_v = float(max(values))

        # Check for dynamic variability
        confidence = 0.85 if (max_v - min_v) > 0 else 0.50
        endian_str = "Little-Endian (Intel)" if is_le else "Big-Endian (Motorola)"

        return Hypothesis(
            htype="SIGNAL",
            start_bit=byte_idx * 8,
            length=16,
            is_little_endian=is_le,
            is_signed=is_signed,
            min_value=min_v,
            max_value=max_v,
            confidence=confidence,
            evidence=[
                Evidence(
                    kind="entropy",
                    value=float(max(b0_unique, b1_unique)),
                    detail=(
                        f"16-bit candidate at bytes [{byte_idx}, {byte_idx+1}] in {endian_str}"
                        + (" (signed candidate: two's-complement range detected)" if is_signed else "")
                        + f"; range [{min_v:.0f}, {max_v:.0f}]."
                    ),
                )
            ],
            name=f"SIG_B{byte_idx}_16B" + ("S" if is_signed else ""),
        )

    @classmethod
    def _evaluate_8bit_signal(cls, payloads: Sequence[bytes], byte_idx: int) -> Hypothesis | None:
        """Evaluate an 8-bit physical signal at byte_idx."""
        raw_bytes = [p[byte_idx] for p in payloads if len(p) > byte_idx]
        if not raw_bytes:
            return None

        unique_count = len(set(raw_bytes))
        min_v = float(min(raw_bytes))
        max_v = float(max(raw_bytes))

        if unique_count == 1:
            return Hypothesis(
                htype="CONSTANT",
                start_bit=byte_idx * 8,
                length=8,
                params={"constant_value": raw_bytes[0]},
                confidence=0.95,
                evidence=[Evidence(kind="constancy", value=1.0, detail=f"Byte {byte_idx} is constant 0x{raw_bytes[0]:02X}")],
                name=f"CONST_B{byte_idx}",
            )

        return Hypothesis(
            htype="SIGNAL",
            start_bit=byte_idx * 8,
            length=8,
            is_little_endian=True,
            is_signed=False,
            min_value=min_v,
            max_value=max_v,
            confidence=0.75,
            evidence=[
                Evidence(
                    kind="entropy",
                    value=float(unique_count),
                    detail=f"8-bit candidate at byte {byte_idx}; {unique_count} distinct values in range [{min_v:.0f}, {max_v:.0f}].",
                )
            ],
            name=f"SIG_B{byte_idx}_8B",
        )
