"""SAE J1939-73 freeze frames (DM4) and scaled test results (DM7 -> DM30) for heavy-duty vehicles.

The heavy-duty counterpart of OBD-II Mode 02 (freeze frame) and Mode 06
(on-board monitor results):

* **DM4** (PGN 65229, Freeze Frame Parameters) is read with a Request
  (PGN 59904). Each freeze frame is::

      length, SPN/FMI/CM/OC (4 bytes, DM1 layout),
      engine torque mode (SPN 899), boost pressure (SPN 102, 2 kPa/bit),
      engine speed (SPN 190, 0.125 rpm/bit, 2 bytes), engine load (SPN 92, 1 %/bit),
      coolant temperature (SPN 110, 1 °C/bit, -40 offset),
      vehicle speed (SPN 84, 1/256 km/h per bit, 2 bytes), manufacturer bytes …

  ``length`` counts the bytes after itself, so a frame with only the required
  parameters has length 12.

* **DM7** (PGN 58112) asks for test results. Only one form is built here:
  test identifier 247 with FMI 31, which per J1939-73 asks the ECU to *report*
  the results of the tests it already ran for one SPN. Test identifiers 1–245
  *command* a test to run and are never built (the read-only policy refuses
  them too).

* **DM30** (PGN 41984, Scaled Test Results) is the answer: 12 bytes per test::

      TID, SPN/FMI (3 bytes, DM1 layout), SLOT (2 bytes),
      test value, test limit maximum, test limit minimum (2 bytes each, little-endian)

  The verdict needs no scaling: value and limits share one SLOT, so comparing
  the raw numbers is exact. A raw value in the J1939-71 indicator range
  (0xFB00 and up) is "not available": a test value there means the test has not
  completed (the record is skipped), a limit there means that side has no limit.
  SLOT scaling is not applied (no verified table ships with the tool), so values
  are shown raw rather than guessed.

DM8 (PGN 65232), the older non-scaled test result message, is superseded by
DM30 for HD-OBD and is not requested.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.protocols.j1939.diagnostics import PGN_DM4

PGN_DM7: int = 58112  # 0xE300 (Command Non-Continuously Monitored Test)
PGN_DM8: int = 65232  # 0xFED0 (Test Results, superseded by DM30; not requested)
PGN_DM30: int = 41984  # 0xA400 (Scaled Test Results)

#: DM7 test identifier that asks for the stored results of every test for one SPN.
DM7_TID_REPORT_ALL: int = 247
#: FMI value that goes with ``DM7_TID_REPORT_ALL`` ("all failure modes").
DM7_FMI_ALL: int = 31

_NA_1BYTE = 0xFB  # J1939-71: 0xFB..0xFF are indicators / not available
_NA_2BYTE = 0xFB00

# DM4 required parameters: (offset after the 4 DTC bytes, size, SPN, canonical copilot signal, scale, offset)
_DM4_FIELDS: tuple[tuple[int, int, int, str, float, float], ...] = (
    (1, 1, 102, "BoostPressure", 2.0, 0.0),
    (2, 2, 190, "EngineSpeed", 0.125, 0.0),
    (4, 1, 92, "EngineLoad", 1.0, 0.0),
    (5, 1, 110, "CoolantTemp", 1.0, -40.0),
    (6, 2, 84, "VehicleSpeed", 1.0 / 256.0, 0.0),
)
_DM4_REQUIRED_LEN = 12


def _spn_fmi(b0: int, b1: int, b2: int) -> tuple[int, int]:
    """SPN / FMI from the 3-byte DM1 layout (conversion method 0)."""
    return b0 | (b1 << 8) | ((b2 & 0xE0) << 11), b2 & 0x1F


@dataclass(frozen=True, slots=True)
class FreezeFrame:
    spn: int
    fmi: int
    occurrence_count: int
    readings: dict[str, float]
    manufacturer_bytes: int

    @property
    def dtc(self) -> str:
        return f"SPN {self.spn} FMI {self.fmi}"

    def as_dict(self) -> dict[str, object]:
        return {"dtc": self.dtc, "spn": self.spn, "fmi": self.fmi, "occurrence_count": self.occurrence_count,
                "readings": dict(self.readings)}


@dataclass(frozen=True, slots=True)
class ScaledTestResult:
    spn: int
    fmi: int
    tid: int
    slot: int
    raw_value: int
    raw_max: int | None
    raw_min: int | None

    @property
    def passed(self) -> bool:
        lo_ok = self.raw_min is None or self.raw_value >= self.raw_min
        hi_ok = self.raw_max is None or self.raw_value <= self.raw_max
        return lo_ok and hi_ok

    def as_dict(self) -> dict[str, object]:
        return {"spn": self.spn, "fmi": self.fmi, "tid": self.tid, "slot": self.slot,
                "value": float(self.raw_value),
                "min": None if self.raw_min is None else float(self.raw_min),
                "max": None if self.raw_max is None else float(self.raw_max),
                "unit": "raw", "scaled": False, "passed": self.passed}


def decode_dm4(payload: bytes) -> list[FreezeFrame]:
    """A complete (reassembled) DM4 payload -> its freeze frames.

    A frame whose length byte is shorter than the required parameters, or that
    runs past the payload, ends decoding: the rest cannot be framed reliably.
    An all-zero DTC (no freeze frame stored) is skipped. Parameters in the
    not-available range are left out, never filled in.
    """
    frames: list[FreezeFrame] = []
    i = 0
    while i < len(payload):
        length = payload[i]
        if length == 0xFF or length < _DM4_REQUIRED_LEN or i + 1 + length > len(payload):
            break
        body = payload[i + 1:i + 1 + length]
        i += 1 + length
        if body[:4] in (b"\x00\x00\x00\x00", b"\xff\xff\xff\xff"):
            continue
        if body[3] >> 7:
            continue  # conversion method 1 layout is not implemented (see DiagnosticTroubleCode)
        spn, fmi = _spn_fmi(body[0], body[1], body[2])
        params = body[4:]
        readings: dict[str, float] = {}
        for off, size, _spn, name, scale, offset in _DM4_FIELDS:
            raw = int.from_bytes(params[off:off + size], "little")
            if raw >= (_NA_1BYTE if size == 1 else _NA_2BYTE):
                continue
            readings[name] = round(raw * scale + offset, 3)
        frames.append(FreezeFrame(spn, fmi, body[3] & 0x7F, readings, length - _DM4_REQUIRED_LEN))
    return frames


def decode_dm30(payload: bytes) -> list[ScaledTestResult]:
    """A complete DM30 payload -> its test results (tests that have not completed are skipped)."""
    out: list[ScaledTestResult] = []
    for i in range(0, len(payload) - len(payload) % 12, 12):
        rec = payload[i:i + 12]
        spn, fmi = _spn_fmi(rec[1], rec[2], rec[3])
        slot = int.from_bytes(rec[4:6], "little")
        value, hi, lo = (int.from_bytes(rec[j:j + 2], "little") for j in (6, 8, 10))
        if value >= _NA_2BYTE or spn == 0:
            continue
        out.append(ScaledTestResult(spn, fmi, rec[0], slot, value,
                                    None if hi >= _NA_2BYTE else hi, None if lo >= _NA_2BYTE else lo))
    return out


def dm7_report_payload(spn: int) -> bytes:
    """DM7 asking for the stored results of every test for ``spn`` (TID 247, FMI 31)."""
    if not 0 < spn <= 0x7FFFF:
        raise ValueError(f"SPN out of range: {spn}")
    b2 = ((spn >> 16) & 0x07) << 5 | DM7_FMI_ALL
    return bytes([DM7_TID_REPORT_ALL, spn & 0xFF, (spn >> 8) & 0xFF, b2, 0xFF, 0xFF, 0xFF, 0xFF])


def dm7_arbitration_id(target_address: int, source_address: int = 0xF9) -> int:
    """Priority 6, PF 0xE3 (DM7), destination = target, source = tool."""
    return 0x18E30000 | ((target_address & 0xFF) << 8) | (source_address & 0xFF)


__all__ = [
    "DM7_FMI_ALL", "DM7_TID_REPORT_ALL", "PGN_DM4", "PGN_DM7", "PGN_DM8", "PGN_DM30",
    "FreezeFrame", "ScaledTestResult", "decode_dm30", "decode_dm4", "dm7_arbitration_id", "dm7_report_payload",
]
