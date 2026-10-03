"""SAE J1939 vehicle and ECU identification messages, read with a Request (PGN 59904).

* **VI** (PGN 65260, Vehicle Identification): the VIN as ASCII, terminated by
  ``*``.
* **DM19** (PGN 54016, Calibration Information): 20 bytes per calibration::

      CVN (4 bytes, little-endian), Calibration ID (16 bytes ASCII)

  Unused Calibration ID bytes are padding (0x00 / 0xFF) and are stripped.
* **SOFT** (PGN 65242, Software Identification): byte 0 is the number of
  fields, then the fields as ASCII, each terminated by ``*``.
* **CI** (PGN 65259, Component Identification): ``make*model*serial*unit*``
  as ASCII.

All four are identification only: they change nothing on the ECU. Decoding is
strict: a field that is not clean printable ASCII is dropped, never repaired
into something that looks like an identifier.
"""

from __future__ import annotations

from dataclasses import dataclass

PGN_VI: int = 65260  # 0xFEEC
PGN_DM19: int = 54016  # 0xD300
PGN_SOFT: int = 65242  # 0xFEDA
PGN_CI: int = 65259  # 0xFEEB

_PADDING = b"\x00\xff "


def _ascii(raw: bytes) -> str | None:
    cleaned = raw.strip(_PADDING)
    if not cleaned or any(b < 0x20 or b > 0x7E for b in cleaned):
        return None
    return cleaned.decode("ascii")


def decode_vi(payload: bytes) -> str | None:
    """The VIN text of a VI message (format is checked by the caller), else None."""
    return _ascii(bytes(payload).split(b"*")[0])


@dataclass(frozen=True)
class Calibration:
    cal_id: str
    cvn: str  # 8 hex digits, as service tools print it

    def as_dict(self) -> dict[str, str]:
        return {"cal_id": self.cal_id, "cvn": self.cvn}


def decode_dm19(payload: bytes) -> list[Calibration]:
    """Every complete 20-byte calibration record with a readable Calibration ID."""
    data = bytes(payload)
    out: list[Calibration] = []
    for i in range(0, len(data) - 19, 20):
        cal_id = _ascii(data[i + 4:i + 20])
        if cal_id is None:
            continue
        out.append(Calibration(cal_id, f"{int.from_bytes(data[i:i + 4], 'little'):08X}"))
    return out


def decode_soft(payload: bytes) -> list[str]:
    """Software identification fields of a SOFT message."""
    data = bytes(payload)
    if not data:
        return []
    count = data[0]
    fields = [f for raw in data[1:].split(b"*")[:count] if (f := _ascii(raw)) is not None]
    return fields


def decode_ci(payload: bytes) -> dict[str, str]:
    """Component identification: the fields that are present of make / model / serial / unit."""
    parts = bytes(payload).split(b"*")
    out: dict[str, str] = {}
    for key, raw in zip(("make", "model", "serial", "unit"), parts):
        value = _ascii(raw)
        if value is not None:
            out[key] = value
    return out
