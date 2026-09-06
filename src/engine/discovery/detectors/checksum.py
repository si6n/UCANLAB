"""Checksum and CRC-8 detection in CAN message payloads.

Implements standard CRC-8 catalogue models (AUTOSAR, SAE-J1850, SMBus, Maxim, Hitag)
and arithmetic/XOR checksums referenced in MASTER_PLAN §7 and docs/specs/signal_discovery_spec.md.
"""

from __future__ import annotations

import functools
import operator
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import ClassVar

from src.engine.discovery.hypotheses import Evidence, Hypothesis


@dataclass(slots=True, frozen=True)
class Crc8Model:
    """Parametric CRC-8 algorithm specification."""

    name: str
    poly: int
    init: int
    xorout: int
    refin: bool
    refout: bool
    table: tuple[int, ...]

    @classmethod
    def create(
        cls,
        name: str,
        poly: int,
        init: int = 0x00,
        xorout: int = 0x00,
        refin: bool = False,
        refout: bool = False,
    ) -> Crc8Model:
        table = cls._generate_table(poly, refin)
        return cls(
            name=name,
            poly=poly,
            init=init,
            xorout=xorout,
            refin=refin,
            refout=refout,
            table=table,
        )

    @staticmethod
    def _reflect(val: int, bits: int = 8) -> int:
        res = 0
        for i in range(bits):
            if (val >> i) & 1:
                res |= 1 << (bits - 1 - i)
        return res

    @classmethod
    def _generate_table(cls, poly: int, refin: bool) -> tuple[int, ...]:
        table: list[int] = []
        for byte in range(256):
            crc = cls._reflect(byte, 8) if refin else byte
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ poly) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
            table.append(crc)
        return tuple(table)

    def calculate(self, data: bytes | Sequence[int]) -> int:
        crc = self.init
        for byte in data:
            if self.refin:
                byte = self._reflect(byte, 8)
            crc = self.table[(crc ^ byte) & 0xFF]
        if self.refout != self.refin:
            crc = self._reflect(crc, 8)
        return (crc ^ self.xorout) & 0xFF


class ChecksumDetector:
    """Evaluates payload checksum candidates against mathematical checksum and CRC-8 models."""

    CRC8_MODELS: ClassVar[tuple[Crc8Model, ...]] = (
        Crc8Model.create("CRC-8/AUTOSAR", poly=0x2F, init=0xFF, xorout=0xFF),
        Crc8Model.create("CRC-8/SAE-J1850", poly=0x1D, init=0xFF, xorout=0xFF),
        Crc8Model.create("CRC-8/SMBUS", poly=0x07, init=0x00, xorout=0x00),
        Crc8Model.create("CRC-8/MAXIM-DOW", poly=0x31, init=0x00, xorout=0x00, refin=True, refout=True),
        Crc8Model.create("CRC-8/HITAG", poly=0x1D, init=0xFF, xorout=0x00),
        Crc8Model.create("CRC-8/GSM-A", poly=0x1D, init=0x00, xorout=0x00),
    )

    SIMPLE_ALGORITHMS: ClassVar[tuple[tuple[str, Callable[[bytes], int]], ...]] = (
        ("XOR-8", lambda data: functools.reduce(operator.xor, data, 0)),
        ("SUM-8", lambda data: sum(data) & 0xFF),
        ("ONES_COMP_SUM-8", lambda data: (0xFF - (sum(data) & 0xFF)) & 0xFF),
        ("NIBBLE_SUM-8", lambda data: (sum((b & 0x0F) + (b >> 4) for b in data)) & 0xFF),
    )

    @classmethod
    def detect(cls, payloads: Sequence[bytes], dlc: int) -> list[Hypothesis]:
        """Detect checksums / CRCs across candidate byte positions."""
        if len(payloads) < 10 or dlc < 2:
            return []

        hypotheses: list[Hypothesis] = []

        # Check candidate checksum positions: last byte, first byte, second-to-last, second
        pos = [dlc - 1, 0]
        if dlc >= 3:
            pos.extend([dlc - 2, 1])
        candidate_positions = list(dict.fromkeys(pos))

        for target_byte in candidate_positions:
            covered_indices = [i for i in range(dlc) if i != target_byte]

            # 1. Test Simple Checksums
            for name, algo in cls.SIMPLE_ALGORITHMS:
                cls._evaluate_checksum(
                    payloads=payloads,
                    target_byte=target_byte,
                    covered_indices=covered_indices,
                    name=name,
                    calculator=algo,
                    params={"algorithm": name, "covered_bytes": covered_indices},
                    hypotheses=hypotheses,
                )

            # 2. Test CRC-8 Models
            for crc_model in cls.CRC8_MODELS:
                cls._evaluate_checksum(
                    payloads=payloads,
                    target_byte=target_byte,
                    covered_indices=covered_indices,
                    name=crc_model.name,
                    calculator=crc_model.calculate,
                    params={
                        "algorithm": crc_model.name,
                        "poly": hex(crc_model.poly),
                        "init": hex(crc_model.init),
                        "xorout": hex(crc_model.xorout),
                        "covered_bytes": covered_indices,
                    },
                    hypotheses=hypotheses,
                )

        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        return hypotheses

    @classmethod
    def _evaluate_checksum(
        cls,
        payloads: Sequence[bytes],
        target_byte: int,
        covered_indices: list[int],
        name: str,
        calculator: Callable[[bytes], int],
        params: dict,
        hypotheses: list[Hypothesis],
    ) -> None:
        """Evaluate a checksum algorithm against target byte across all payloads."""
        matches = 0
        total = 0

        for p in payloads:
            if len(p) <= max(target_byte, max(covered_indices, default=0)):
                continue
            actual = p[target_byte]
            covered_data = bytes([p[i] for i in covered_indices])
            expected = calculator(covered_data) & 0xFF

            if actual == expected:
                matches += 1
            total += 1

        if total < 10:
            return

        match_ratio = matches / total

        # Match ratio >= 90% indicates a strong checksum/CRC candidate
        if match_ratio >= 0.90:
            start_bit = target_byte * 8
            hypotheses.append(
                Hypothesis(
                    htype="CHECKSUM",
                    start_bit=start_bit,
                    length=8,
                    params=params,
                    confidence=round(match_ratio, 4),
                    evidence=[
                        Evidence(
                            kind="crc_match_ratio",
                            value=match_ratio,
                            detail=(
                                f"Byte {target_byte} matches {name} over bytes {covered_indices} "
                                f"in {matches}/{total} frames ({match_ratio:.1%})."
                            ),
                        )
                    ],
                    name=f"CHECKSUM_B{target_byte}_{name.replace('/', '_').replace('-', '_')}",
                )
            )
