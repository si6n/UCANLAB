"""Compare what the vehicle says about itself with what the mechanic selected.

Two passive-or-already-available sources, no guessing:

* **VIN** (J1939 PGN 65260 broadcast, or an OBD-II Mode 09 read the user
  allowed): its first characters (WMI) are matched against the ``wmi`` prefixes
  in the vehicle catalog. An unknown WMI yields "unknown", never a guess.
* **J1939 address claims** (PGN 60928): each node's NAME carries an SAE
  manufacturer code. Names come from the canboat table vendored in
  ``data/dbc/heavy_duty/j1939_canboat.dbc``; OEM attribution uses the same
  ``OEM_NAME_MANUFACTURER_CODES`` map as the J1939 OEM decoders.

The result tells the UI whether to warn ("You chose Scania, the vehicle reports
Volvo") and which profile to suggest. The VIN is never logged in full.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from src.core.logging import get_logger
from src.engine.vehicle.profiles import DEFAULT_DBC_ROOT, VehicleCatalog, VehicleProfile

logger = get_logger("engine.vehicle.identity")

_CANBOAT_DBC = DEFAULT_DBC_ROOT / "heavy_duty" / "j1939_canboat.dbc"
# ISO 3779: 17 characters, letters I, O and Q are never used.
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def normalize_vin(vin: str | None) -> str | None:
    """Upper-cased VIN when it is well-formed, else None."""
    if not isinstance(vin, str):
        return None
    cleaned = vin.strip().upper()
    return cleaned if _VIN_RE.match(cleaned) else None


def mask_vin(vin: str) -> str:
    """WMI + last 4 only, for logs and screens that do not need the full VIN."""
    return f"{vin[:3]}{'*' * 10}{vin[-4:]}" if len(vin) == 17 else "***"


@lru_cache(maxsize=1)
def j1939_manufacturer_names(dbc_path: Path = _CANBOAT_DBC) -> dict[int, str]:
    """SAE J1939 NAME manufacturer code -> name, from the vendored canboat DBC."""
    try:
        text = dbc_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        logger.warning("J1939 manufacturer table unavailable", extra={"path": str(dbc_path)})
        return {}
    match = re.search(r"VAL_ \d+ Manufacturer_Code (.*?);", text, re.S)
    if not match:
        return {}
    return {int(code): name for code, name in re.findall(r'(\d+) "([^"]*)"', match.group(1))}


def oem_for_manufacturer_code(code: int) -> str | None:
    from src.protocols.j1939.oem.registry import OEM_NAME_MANUFACTURER_CODES

    for oem, codes in OEM_NAME_MANUFACTURER_CODES.items():
        if code in codes:
            return oem
    return None


@dataclass(frozen=True)
class IdentityResult:
    status: str  # "match" | "mismatch" | "unknown"
    source: str | None = None  # "vin" | "j1939_name" | None
    detected_tr: str = ""
    detected_en: str = ""
    suggested_profile_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status, "source": self.source, "detected_tr": self.detected_tr,
            "detected_en": self.detected_en, "suggested_profile_id": self.suggested_profile_id,
        }


def profiles_for_vin(catalog: VehicleCatalog, vin: str) -> list[VehicleProfile]:
    """Profiles whose WMI prefix matches, longest prefix first."""
    hits = [(len(w), p) for p in catalog.profiles for w in p.wmi if vin.startswith(w)]
    hits.sort(key=lambda h: -h[0])
    best = hits[0][0] if hits else 0
    return [p for n, p in hits if n == best]


def compare_identity(
    catalog: VehicleCatalog,
    selected: VehicleProfile,
    *,
    vin: str | None = None,
    j1939_manufacturer_codes: Iterable[int] = (),
) -> IdentityResult:
    """Decide match / mismatch / unknown for the selected profile.

    Generic profiles ("all makes") can never mismatch. A VIN match on any of
    several profiles sharing a WMI (e.g. Mercedes cars and trucks) counts as a
    match when the selected profile is among them.
    """
    if selected.coverage == "standard" and not selected.wmi:
        return IdentityResult(status="unknown")

    normalized = normalize_vin(vin)
    if normalized:
        candidates = profiles_for_vin(catalog, normalized)
        same_type = [p for p in candidates if p.type == selected.type] or candidates
        if candidates:
            if selected in candidates:
                return IdentityResult(status="match", source="vin",
                                      detected_tr=selected.label_tr, detected_en=selected.label_en)
            suggestion = same_type[0]
            logger.info("VIN does not match the selected vehicle",
                        extra={"vin": mask_vin(normalized), "selected": selected.id, "suggested": suggestion.id})
            return IdentityResult(status="mismatch", source="vin", detected_tr=suggestion.label_tr,
                                  detected_en=suggestion.label_en, suggested_profile_id=suggestion.id)

    codes = tuple(int(c) for c in j1939_manufacturer_codes)
    oems = {oem for code in codes if (oem := oem_for_manufacturer_code(code))}
    if oems and selected.oem_decoder is not None:
        if selected.oem_decoder in oems:
            return IdentityResult(status="match", source="j1939_name",
                                  detected_tr=selected.label_tr, detected_en=selected.label_en)
        names = j1939_manufacturer_names()
        other = sorted(oems)[0]
        other_profile = next((p for p in catalog.profiles_for(selected.type) if p.oem_decoder == other), None)
        label = next((names[c] for c in codes if oem_for_manufacturer_code(c) == other and c in names), other)
        return IdentityResult(status="mismatch", source="j1939_name", detected_tr=label, detected_en=label,
                              suggested_profile_id=other_profile.id if other_profile else None)
    return IdentityResult(status="unknown")
