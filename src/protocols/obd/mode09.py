"""SAE J1979 Mode 09 (request vehicle information) decoding.

A positive answer on CAN is ``[0x49, InfoType, NODI, data…]`` where NODI is the
number of data items:

* InfoType 0x02, VIN: one item of 17 ASCII bytes (some ECUs pad in front with
  0x00);
* InfoType 0x04, Calibration ID: NODI items of 16 ASCII bytes, padded with 0x00;
* InfoType 0x06, CVN: NODI items of 4 bytes, shown as 8 hex digits;
* InfoType 0x0A, ECU name: one item of 20 ASCII bytes (acronym, separator,
  name).

Mode 09 only reports identification; it changes nothing on the ECU. Decoding is
strict: an item that is not clean printable ASCII is dropped.
"""

from __future__ import annotations

INFOTYPE_VIN = 0x02
INFOTYPE_CAL_ID = 0x04
INFOTYPE_CVN = 0x06
INFOTYPE_ECU_NAME = 0x0A


def _items(payload: bytes, infotype: int, size: int) -> list[bytes]:
    data = bytes(payload)
    if len(data) < 3 or data[0] != 0x49 or data[1] != infotype:
        raise ValueError(f"not a Mode 09 InfoType 0x{infotype:02X} answer")
    body = data[3:]
    count = min(data[2], len(body) // size)
    return [body[i * size:(i + 1) * size] for i in range(count)]


def _ascii(raw: bytes) -> str | None:
    cleaned = raw.strip(b"\x00\xff ")
    if not cleaned or any(b < 0x20 or b > 0x7E for b in cleaned):
        return None
    return cleaned.decode("ascii")


def decode_vin(payload: bytes) -> str | None:
    """VIN text (format is checked by the caller), else None."""
    data = bytes(payload)
    if len(data) < 3 or data[0] != 0x49 or data[1] != INFOTYPE_VIN:
        raise ValueError("not a Mode 09 VIN answer")
    return _ascii(data[3:])


def decode_cal_ids(payload: bytes) -> list[str]:
    return [c for raw in _items(payload, INFOTYPE_CAL_ID, 16) if (c := _ascii(raw)) is not None]


def decode_cvns(payload: bytes) -> list[str]:
    return [raw.hex().upper() for raw in _items(payload, INFOTYPE_CVN, 4)]


def decode_ecu_name(payload: bytes) -> str | None:
    items = _items(payload, INFOTYPE_ECU_NAME, 20)
    if not items:
        return None
    parts = [p for raw in items[0].split(b"\x00") if (p := _ascii(raw)) is not None]
    return "-".join(p.strip("-") for p in parts) or None
