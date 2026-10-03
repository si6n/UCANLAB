"""SAE J1979 Mode $06 (on-board monitoring test results) decoding for ISO 15765-4 (CAN).

Mode 06 is the one standard place where the engine ECU publishes its own
limits: for every monitor (catalyst, O2 sensors, misfire per cylinder, EVAP,
EGR …) it reports the measured test value together with the minimum and
maximum it accepts. The pass/fail verdict is the ECU's own.

CAN response layout (SAE J1979 / ISO 15765-4)::

    0x46, then for every test a 9-byte record:
    OBDMID, TID, UASID, VALUE_HI, VALUE_LO, MIN_HI, MIN_LO, MAX_HI, MAX_LO

A request for a "supported MIDs" identifier (0x00, 0x20, 0x40 …) is answered
with ``0x46, MID, 4 bitmask bytes`` instead.

The verdict needs no scaling: value, minimum and maximum share one UASID, so
comparing the raw numbers (signed for UASIDs 0x80 and up) is exact. Scaling is
only for display; a UASID outside the table below is shown as a raw number
rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass

# UASID -> (scale, unit, offset) for the common unit/scaling identifiers of
# SAE J1979 (Appendix E). Unlisted identifiers stay raw ("scaled" is False).
UASID_SCALING: dict[int, tuple[float, str, float]] = {
    0x01: (1.0, "", 0.0), 0x02: (0.1, "", 0.0), 0x03: (0.01, "", 0.0), 0x04: (0.001, "", 0.0),
    0x05: (0.0000305, "", 0.0), 0x06: (0.000305, "", 0.0),
    0x07: (0.25, "rpm", 0.0), 0x08: (0.01, "km/h", 0.0), 0x09: (1.0, "km/h", 0.0),
    0x0A: (0.122, "mV", 0.0), 0x0B: (0.001, "V", 0.0), 0x0C: (0.01, "V", 0.0),
    0x0D: (0.00390625, "mA", 0.0), 0x0E: (0.001, "A", 0.0), 0x0F: (0.01, "A", 0.0),
    0x10: (1.0, "ms", 0.0), 0x11: (100.0, "ms", 0.0), 0x12: (1.0, "s", 0.0),
    0x13: (1.0, "mΩ", 0.0), 0x14: (1.0, "Ω", 0.0), 0x15: (1.0, "kΩ", 0.0),
    0x16: (0.1, "°C", -40.0), 0x17: (0.01, "kPa", 0.0), 0x1A: (1.0, "kPa", 0.0), 0x1B: (10.0, "kPa", 0.0),
    0x1C: (0.01, "°", 0.0), 0x1D: (0.5, "°", 0.0), 0x24: (1.0, "counts", 0.0),
    0x27: (0.01, "g/s", 0.0), 0x28: (1.0, "g/s", 0.0), 0x2F: (0.01, "%", 0.0),
    # signed identifiers (two's complement raw values)
    0x81: (1.0, "", 0.0), 0x82: (0.1, "", 0.0), 0x83: (0.01, "", 0.0), 0x84: (0.001, "", 0.0),
    0x8A: (0.122, "mV", 0.0), 0x8B: (0.001, "V", 0.0), 0x8C: (0.01, "V", 0.0),
    0x8D: (0.00390625, "mA", 0.0), 0x8E: (0.001, "A", 0.0), 0x90: (1.0, "ms", 0.0), 0xAF: (0.01, "%", 0.0),
}


@dataclass(frozen=True, slots=True)
class Mode06Test:
    mid: int
    tid: int
    uasid: int
    raw_value: int
    raw_min: int
    raw_max: int
    passed: bool
    value: float
    min: float
    max: float
    unit: str
    scaled: bool

    def as_dict(self) -> dict[str, object]:
        return {"mid": self.mid, "tid": self.tid, "uasid": self.uasid, "value": self.value, "min": self.min,
                "max": self.max, "unit": self.unit, "scaled": self.scaled, "passed": self.passed}


def _signed16(raw: int) -> int:
    return raw - 0x10000 if raw & 0x8000 else raw


def decode_test_record(record: bytes) -> Mode06Test:
    """One 9-byte record: OBDMID, TID, UASID, value, min, max (big-endian 16-bit)."""
    if len(record) != 9:
        raise ValueError("a Mode 06 test record is 9 bytes")
    mid, tid, uasid = record[0], record[1], record[2]
    raw = [int.from_bytes(record[i:i + 2], "big") for i in (3, 5, 7)]
    if uasid >= 0x80:
        raw = [_signed16(x) for x in raw]
    value, lo, hi = raw
    scale, unit, offset = UASID_SCALING.get(uasid, (1.0, "", 0.0))
    scaled = uasid in UASID_SCALING

    def sc(x: int) -> float:
        return round(x * scale + offset, 6)

    return Mode06Test(mid, tid, uasid, value, lo, hi, lo <= value <= hi, sc(value), sc(lo), sc(hi),
                      unit if scaled else "raw", scaled)


def decode_mode06_response(payload: bytes) -> tuple[list[int], list[Mode06Test]]:
    """A complete (reassembled) Mode 06 positive response -> (supported MIDs, test records).

    ``payload`` starts with the 0x46 service byte. A supported-MID answer
    returns the MID list and no tests; a test answer returns the records.
    """
    if not payload or payload[0] != 0x46 or len(payload) < 2:
        raise ValueError("not a Mode 06 positive response")
    mid = payload[1]
    body = payload[2:]
    if mid % 0x20 == 0 and len(body) == 4:
        bits = int.from_bytes(body, "big")
        return [mid + i + 1 for i in range(32) if bits & (1 << (31 - i))], []
    data = payload[1:]
    tests = [decode_test_record(data[i:i + 9]) for i in range(0, len(data) - len(data) % 9, 9)]
    return [], tests
