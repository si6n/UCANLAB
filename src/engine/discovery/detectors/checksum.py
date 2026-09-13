"""Checksum and CRC-8/16/32 detection in CAN message payloads.

Implements standard CRC-8 catalogue models (AUTOSAR, SAE-J1850, SMBus, Maxim, Hitag)
and 16/32-bit variants referenced in MASTER_PLAN §7 and docs/specs/signal_discovery_spec.md.
"""

from __future__ import annotations

import functools
import operator
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from src.engine.discovery.hypotheses import Evidence, Hypothesis


@dataclass(slots=True, frozen=True)
class CrcModel:
    """Parametric CRC algorithm specification (8/16/32-bit)."""

    name: str
    width: int
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
        width: int = 8,
        init: int = 0x00,
        xorout: int = 0x00,
        refin: bool = False,
        refout: bool = False,
    ) -> CrcModel:
        table = cls._generate_table(poly, width, refin)
        return cls(
            name=name,
            width=width,
            poly=poly,
            init=init,
            xorout=xorout,
            refin=refin,
            refout=refout,
            table=table,
        )

    @staticmethod
    def _reflect(val: int, bits: int) -> int:
        res = 0
        for i in range(bits):
            if (val >> i) & 1:
                res |= 1 << (bits - 1 - i)
        return res

    @classmethod
    def _generate_table(cls, poly: int, width: int, refin: bool) -> tuple[int, ...]:
        # REVIEW (reflected CRC): LSB-first table entries use the reflected
        # polynomial (MAXIM-DOW: 0x8C, the reflection of 0x31). Reflecting
        # BOTH the polynomial and each input byte double-inverts and yields
        # wrong checksums (MAXIM('123456789') = 0xA7 instead of catalog 0xA1).
        mask = (1 << width) - 1
        msb = 1 << (width - 1)
        reflected_poly = cls._reflect(poly, width) if refin else poly
        table: list[int] = []
        for byte in range(256):
            # MSB-first registers seed the byte into the high half; LSB-first
            # into the low half.
            crc = byte if refin else (byte << (width - 8))
            for _ in range(8):
                if refin:
                    if crc & 0x01:
                        crc = (crc >> 1) ^ reflected_poly
                    else:
                        crc >>= 1
                else:
                    crc <<= 1
                    if crc & (msb << 1):
                        crc ^= poly
            table.append(crc & mask)
        return tuple(table)

    def calculate(self, data: bytes | Sequence[int]) -> int:
        # REVIEW (reflected CRC): with a genuinely LSB-first table the input
        # byte must NOT be pre-reflected — the table lookup indexes the raw
        # byte and the accumulated register is already in the reflected
        # domain. Only the final result is reflected for refout
        # (when refin != refout).
        mask = (1 << self.width) - 1
        crc = self.init
        if self.refin:
            for byte in data:
                crc = (crc >> 8) ^ self.table[(crc ^ byte) & 0xFF]
                crc &= mask
        else:
            shift = self.width - 8
            for byte in data:
                crc = ((crc << 8) & mask) ^ self.table[((crc >> shift) ^ byte) & 0xFF]
        if self.refout != self.refin:
            crc = self._reflect(crc, self.width)
        return (crc ^ self.xorout) & mask


# Backwards-compatible alias: existing callers construct 8-bit models by name.
Crc8Model = CrcModel


class ChecksumDetector:
    """Evaluates payload checksum candidates against mathematical checksum and CRC models."""

    CRC8_MODELS: ClassVar[tuple[CrcModel, ...]] = (
        CrcModel.create("CRC-8/AUTOSAR", poly=0x2F, init=0xFF, xorout=0xFF),
        CrcModel.create("CRC-8/SAE-J1850", poly=0x1D, init=0xFF, xorout=0xFF),
        CrcModel.create("CRC-8/SMBUS", poly=0x07),
        CrcModel.create("CRC-8/MAXIM-DOW", poly=0x31, refin=True, refout=True),
        CrcModel.create("CRC-8/HITAG", poly=0x1D, init=0xFF),
        CrcModel.create("CRC-8/GSM-A", poly=0x1D),
    )

    # check="123456789": CCITT-FALSE=0x29B1, KERMIT=0x447F, XMODEM=0x31C3,
    # IBM(ARC)=0xBB3D, MODBUS=0x4B37, CRC-32/ISO-HDLC=0xCBF43926
    CRC16_MODELS: ClassVar[tuple[CrcModel, ...]] = (
        CrcModel.create("CRC-16/CCITT-FALSE", poly=0x1021, width=16, init=0xFFFF),
        CrcModel.create("CRC-16/XMODEM", poly=0x1021, width=16),
        CrcModel.create("CRC-16/KERMIT", poly=0x1021, width=16, refin=True, refout=True),
        CrcModel.create("CRC-16/IBM-ARC", poly=0x8005, width=16, refin=True, refout=True),
        CrcModel.create("CRC-16/MODBUS", poly=0x8005, width=16, init=0xFFFF, refin=True, refout=True),
    )

    CRC32_MODELS: ClassVar[tuple[CrcModel, ...]] = (
        # CRC-32/ISO-HDLC (PKZIP, Ethernet FCS); reflected poly of 0x04C11DB7
        CrcModel.create(
            "CRC-32/ISO-HDLC", poly=0x04C11DB7, width=32, init=0xFFFFFFFF, xorout=0xFFFFFFFF, refin=True, refout=True
        ),
    )

    SIMPLE_ALGORITHMS: ClassVar[tuple[tuple[str, Callable[[bytes], int]], ...]] = (
        ("XOR-8", lambda data: functools.reduce(operator.xor, data, 0)),
        ("SUM-8", lambda data: sum(data) & 0xFF),
        ("ONES_COMP_SUM-8", lambda data: (0xFF - (sum(data) & 0xFF)) & 0xFF),
        ("NIBBLE_SUM-8", lambda data: (sum((b & 0x0F) + (b >> 4) for b in data)) & 0xFF),
    )

    @classmethod
    def detect(cls, payloads: Sequence[bytes], dlc: int) -> list[Hypothesis]:
        """Detect checksums / CRCs across candidate byte positions.

        CRC-16 candidates are tested at the trailing byte pair (MSB-first
        and LSB-first storage); CRC-32 at the trailing four bytes.
        """
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
                        "width": 8,
                        "poly": hex(crc_model.poly),
                        "init": hex(crc_model.init),
                        "xorout": hex(crc_model.xorout),
                        "covered_bytes": covered_indices,
                    },
                    hypotheses=hypotheses,
                )

        # 3. CRC-16 at the trailing byte pair (both storage orders)
        if dlc >= 4:
            covered = list(range(dlc - 2))
            for crc_model in cls.CRC16_MODELS:
                for byte_order in ("big", "little"):
                    cls._evaluate_multibyte_checksum(
                        payloads,
                        start=dlc - 2,
                        width=2,
                        byte_order=byte_order,
                        model=crc_model,
                        covered_indices=covered,
                        hypotheses=hypotheses,
                    )

        # 4. CRC-32 at the trailing four bytes (both storage orders)
        if dlc >= 6:
            covered = list(range(dlc - 4))
            for crc_model in cls.CRC32_MODELS:
                for byte_order in ("big", "little"):
                    cls._evaluate_multibyte_checksum(
                        payloads,
                        start=dlc - 4,
                        width=4,
                        byte_order=byte_order,
                        model=crc_model,
                        covered_indices=covered,
                        hypotheses=hypotheses,
                    )

        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        return hypotheses

    @classmethod
    def _evaluate_multibyte_checksum(
        cls,
        payloads: Sequence[bytes],
        start: int,
        width: int,
        byte_order: str,
        model: CrcModel,
        covered_indices: list[int],
        hypotheses: list[Hypothesis],
    ) -> None:
        """Evaluate a 16/32-bit CRC stored at bytes start..start+width-1 over covered_indices."""
        matches = 0
        total = 0

        for p in payloads:
            if len(p) < start + width:
                continue
            crc_bytes = p[start : start + width]
            actual = int.from_bytes(crc_bytes, "big" if byte_order == "big" else "little")
            covered_data = bytes([p[i] for i in covered_indices])
            expected = model.calculate(covered_data)

            if actual == expected:
                matches += 1
            total += 1

        if total < 10:
            return

        match_ratio = matches / total
        if match_ratio >= 0.90:
            order_label = "MSB-first" if byte_order == "big" else "LSB-first"
            length = width * 8
            hypotheses.append(
                Hypothesis(
                    htype="CHECKSUM",
                    start_bit=start * 8,
                    length=length,
                    params={
                        "algorithm": model.name,
                        "width": model.width,
                        "poly": hex(model.poly),
                        "init": hex(model.init),
                        "xorout": hex(model.xorout),
                        "byte_order": byte_order,
                        "covered_bytes": covered_indices,
                    },
                    confidence=round(match_ratio, 4),
                    evidence=[
                        Evidence(
                            kind="crc_match_ratio",
                            value=match_ratio,
                            detail=(
                                f"Bytes {start}..{start + width - 1} ({order_label}) match {model.name} "
                                f"over bytes {covered_indices} in {matches}/{total} frames ({match_ratio:.1%})."
                            ),
                        )
                    ],
                    name=f"CHECKSUM_B{start}_{model.name.replace('/', '_').replace('-', '_')}_{order_label}",
                )
            )

    @classmethod
    def _evaluate_checksum(
        cls,
        payloads: Sequence[bytes],
        target_byte: int,
        covered_indices: list[int],
        name: str,
        calculator: Callable[[bytes], int],
        params: dict[str, Any],
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
