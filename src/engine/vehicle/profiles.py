"""Vehicle catalog for the mechanic flow (Aşama 4).

The catalog lives in ``data/vehicle_profiles.json``. This module loads it and
enforces the product rule "no invented coverage" (MECHANIC_FLOW.md §0.3):

* ``enriched`` — the repo really holds maker data for it: at least one DBC file
  matched by ``dbc_globs`` exists, and/or ``oem_decoder`` names a registered
  J1939 OEM decoder. A claim that is not backed by files fails the load.
* ``standard`` — only the generic protocol (OBD-II / J1939 / NMEA 2000) applies;
  it must not reference maker data.
* ``unsupported`` — shown greyed out with the reason; cannot be selected.

A vehicle type carries what the connection wizard (Aşama 5) needs: protocol,
socket, bitrate candidates and the traffic it expects to hear passively.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.core.logging import get_logger

logger = get_logger("engine.vehicle.profiles")


def _data_root() -> Path:
    """``data/`` next to the sources, or inside the PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", sys.executable)).resolve() / "data"
        if bundled.is_dir():
            return bundled
    return Path(__file__).resolve().parents[3] / "data"


_DATA_ROOT = _data_root()
DEFAULT_CATALOG_PATH = _DATA_ROOT / "vehicle_profiles.json"
DEFAULT_DBC_ROOT = _DATA_ROOT / "dbc"

COVERAGE_LEVELS = ("enriched", "standard", "unsupported")


class CatalogError(ValueError):
    """The catalog file is malformed or claims coverage it cannot back."""


@dataclass(frozen=True)
class ExpectedTraffic:
    kind: str  # "j1939_pgn" | "n2k_pgn"
    pgn: int
    name_tr: str
    name_en: str


@dataclass(frozen=True)
class VehicleType:
    id: str
    label_tr: str
    label_en: str
    sub_tr: str
    sub_en: str
    protocol: str
    socket: str
    bitrate_candidates: tuple[int, ...]
    expected_traffic: tuple[ExpectedTraffic, ...]
    plug_tr: str
    plug_en: str
    passive_note_tr: str
    passive_note_en: str


@dataclass(frozen=True)
class VehicleProfile:
    id: str
    type: str
    make: str
    label_tr: str
    label_en: str
    coverage: str
    dbc_files: tuple[str, ...] = ()
    oem_decoder: str | None = None
    wmi: tuple[str, ...] = ()
    high_voltage: bool = False
    note_tr: str = ""
    note_en: str = ""

    @property
    def selectable(self) -> bool:
        return self.coverage != "unsupported"


@dataclass(frozen=True)
class VehicleCatalog:
    types: tuple[VehicleType, ...]
    profiles: tuple[VehicleProfile, ...]
    _by_id: dict[str, VehicleProfile] = field(default_factory=dict, compare=False, repr=False)

    def type_by_id(self, type_id: str) -> VehicleType | None:
        return next((t for t in self.types if t.id == type_id), None)

    def profile(self, profile_id: str) -> VehicleProfile | None:
        return self._by_id.get(profile_id)

    def profiles_for(self, type_id: str) -> list[VehicleProfile]:
        return [p for p in self.profiles if p.type == type_id]

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe view for the UI bridge."""
        return {
            "types": [
                {
                    "id": t.id, "label_tr": t.label_tr, "label_en": t.label_en, "sub_tr": t.sub_tr,
                    "sub_en": t.sub_en, "protocol": t.protocol, "socket": t.socket,
                    "bitrate_candidates": list(t.bitrate_candidates),
                    "plug_tr": t.plug_tr, "plug_en": t.plug_en,
                    "passive_note_tr": t.passive_note_tr, "passive_note_en": t.passive_note_en,
                }
                for t in self.types
            ],
            "profiles": [profile_dict(p) for p in self.profiles],
        }


def profile_dict(p: VehicleProfile) -> dict[str, Any]:
    return {
        "id": p.id, "type": p.type, "make": p.make, "label_tr": p.label_tr, "label_en": p.label_en,
        "coverage": p.coverage, "selectable": p.selectable, "high_voltage": p.high_voltage,
        "has_oem_decoder": p.oem_decoder is not None, "dbc_file_count": len(p.dbc_files),
        "note_tr": p.note_tr, "note_en": p.note_en,
    }


def _known_oem_decoders() -> frozenset[str]:
    from src.protocols.j1939.oem.registry import OEM_NAME_MANUFACTURER_CODES

    return frozenset(OEM_NAME_MANUFACTURER_CODES)


def _str(raw: dict[str, Any], key: str, where: str, *, required: bool = True) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str) or (required and not value.strip()):
        raise CatalogError(f"{where}: '{key}' must be a non-empty string")
    return value


def load_catalog(path: Path | None = None, dbc_root: Path | None = None) -> VehicleCatalog:
    """Load and validate the catalog. Raises CatalogError on any dishonest entry."""
    catalog_path = path or DEFAULT_CATALOG_PATH
    root = dbc_root or DEFAULT_DBC_ROOT
    try:
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"cannot read vehicle catalog: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise CatalogError("vehicle catalog must be an object with schema_version 1")

    types: list[VehicleType] = []
    for t in raw.get("vehicle_types") or []:
        where = f"vehicle_type {t.get('id')!r}"
        bitrates = t.get("bitrate_candidates") or []
        if not bitrates or not all(isinstance(b, int) and b > 0 for b in bitrates):
            raise CatalogError(f"{where}: bitrate_candidates must be positive integers")
        traffic = tuple(
            ExpectedTraffic(kind=_str(e, "kind", where), pgn=int(e["pgn"]),
                            name_tr=_str(e, "name_tr", where), name_en=_str(e, "name_en", where))
            for e in t.get("expected_traffic") or []
        )
        types.append(VehicleType(
            id=_str(t, "id", where), label_tr=_str(t, "label_tr", where), label_en=_str(t, "label_en", where),
            sub_tr=_str(t, "sub_tr", where), sub_en=_str(t, "sub_en", where), protocol=_str(t, "protocol", where),
            socket=_str(t, "socket", where), bitrate_candidates=tuple(bitrates), expected_traffic=traffic,
            plug_tr=_str(t, "plug_tr", where), plug_en=_str(t, "plug_en", where),
            passive_note_tr=_str(t, "passive_note_tr", where), passive_note_en=_str(t, "passive_note_en", where),
        ))
    type_ids = {t.id for t in types}
    if len(type_ids) != len(types) or not types:
        raise CatalogError("vehicle type ids must be unique and non-empty")

    known_decoders = _known_oem_decoders()
    profiles: list[VehicleProfile] = []
    for p in raw.get("profiles") or []:
        where = f"profile {p.get('id')!r}"
        coverage = _str(p, "coverage", where)
        if coverage not in COVERAGE_LEVELS:
            raise CatalogError(f"{where}: unknown coverage {coverage!r}")
        ptype = _str(p, "type", where)
        if ptype not in type_ids:
            raise CatalogError(f"{where}: unknown vehicle type {ptype!r}")

        globs = p.get("dbc_globs") or []
        files = sorted({str(f.relative_to(root)) for g in globs for f in root.glob(g) if f.is_file()})
        if globs and not files:
            raise CatalogError(f"{where}: dbc_globs {globs} match no file under {root}")
        decoder = p.get("oem_decoder")
        if decoder is not None and decoder not in known_decoders:
            raise CatalogError(f"{where}: oem_decoder {decoder!r} is not a registered J1939 OEM decoder")
        if coverage == "enriched" and not files and decoder is None:
            raise CatalogError(f"{where}: 'enriched' needs maker data (dbc_globs or oem_decoder)")
        if coverage != "enriched" and (files or decoder is not None):
            raise CatalogError(f"{where}: only 'enriched' profiles may reference maker data")
        if coverage == "unsupported" and not str(p.get("note_tr") or "").strip():
            raise CatalogError(f"{where}: 'unsupported' needs a note explaining why")

        wmi = tuple(str(w).upper() for w in p.get("wmi") or [])
        if any(not (2 <= len(w) <= 3) or not w.isalnum() for w in wmi):
            raise CatalogError(f"{where}: wmi prefixes must be 2-3 alphanumeric characters")

        profiles.append(VehicleProfile(
            id=_str(p, "id", where), type=ptype, make=_str(p, "make", where),
            label_tr=_str(p, "label_tr", where), label_en=_str(p, "label_en", where), coverage=coverage,
            dbc_files=tuple(files), oem_decoder=decoder, wmi=wmi, high_voltage=bool(p.get("high_voltage", False)),
            note_tr=str(p.get("note_tr") or ""), note_en=str(p.get("note_en") or ""),
        ))

    by_id = {p.id: p for p in profiles}
    if len(by_id) != len(profiles):
        raise CatalogError("profile ids must be unique")
    for t in types:
        if not any(p.type == t.id and p.selectable for p in profiles):
            raise CatalogError(f"vehicle type {t.id!r} has no selectable profile")
    return VehicleCatalog(types=tuple(types), profiles=tuple(profiles), _by_id=by_id)


@lru_cache(maxsize=1)
def default_catalog() -> VehicleCatalog:
    return load_catalog()
