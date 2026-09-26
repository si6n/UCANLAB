# -*- coding: utf-8 -*-
"""ISOBUS (ISO 11783-11) DDI Version Pinning & Diff Registry.

Complies with:
- ISO 11783-11 (Agricultural vehicles — Controller area network — Part 11: Data Dictionary)
- Konsolide Keşif Raporu §A.2 (Madde 3), §H.3 (Aksiyon 36):
  "isobus.net DDI sürüm sabitleme + diff + atıf hattı (2026050501 / 2025092601 / 2026090801).
   isobus.net terms-of-use yayımlamıyor; ISO'nun kendi metni 'No reproduction on networking
   permitted without license from ISO' diyor. Scrape edip MIT olarak lisanslamak telif ihlalidir."
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.errors import ProtocolError

ROOT_DIR = Path(__file__).resolve().parents[3]
DDI_MANIFEST_PATH = ROOT_DIR / "data" / "knowledge" / "isobus_ddi" / "isobus_ddi_versions.json"


@dataclass(slots=True, frozen=True)
class DdiEntity:
    ddi: int
    hex_id: str
    name: str
    unit: str
    resolution: float
    added_in: str


class IsobusDdiRegistry:
    """AEF ISOBUS Data Dictionary version-pinned registry."""

    PINNED_RELEASES: tuple[str, ...] = ("2025092601", "2026050501", "2026090801")

    def __init__(self, manifest_path: Path | str | None = None) -> None:
        self._path = Path(manifest_path) if manifest_path is not None else DDI_MANIFEST_PATH
        if not self._path.exists():
            raise FileNotFoundError(f"ISOBUS DDI manifest not found: {self._path}")
        self._data: dict[str, Any] = json.loads(self._path.read_text(encoding="utf-8"))
        self._validate_manifest()

    def _validate_manifest(self) -> None:
        if "pinned_releases" not in self._data:
            raise ProtocolError("Missing 'pinned_releases' in ISOBUS DDI manifest", code="MANIFEST_INVALID")
        for rel in self.PINNED_RELEASES:
            if rel not in self._data["pinned_releases"]:
                raise ProtocolError(f"Missing mandatory pinned release {rel}", code="MANIFEST_INVALID")

    @property
    def citation(self) -> str:
        return self._data.get("citation", "isobus.net / ISO 11783-11")

    @property
    def licensing_notice(self) -> str:
        return self._data.get("licensing_notice", "")

    def get_release_info(self, release_id: str) -> dict[str, Any]:
        releases = self._data.get("pinned_releases", {})
        if release_id not in releases:
            raise KeyError(f"Unknown ISOBUS release {release_id}. Pinned: {self.PINNED_RELEASES}")
        return releases[release_id]

    def lookup_ddi(self, ddi: int) -> DdiEntity | None:
        hex_key = f"{ddi:04X}"
        items = self._data.get("core_ddi_dictionary", {})
        info = items.get(hex_key)
        if not info:
            return None
        return DdiEntity(
            ddi=info["ddi"],
            hex_id=info["hex"],
            name=info["name"],
            unit=info["unit"],
            resolution=float(info["resolution"]),
            added_in=info["added_in"],
        )

    def diff_releases(self, old_release: str, new_release: str) -> dict[str, Any]:
        """Compute structural diff between two pinned releases."""
        old_info = self.get_release_info(old_release)
        new_info = self.get_release_info(new_release)

        # Count delta
        delta_count = new_info.get("ddi_count", 0) - old_info.get("ddi_count", 0)

        # List known DDI additions between the two releases
        added_entities = []
        for _ddi_key, info in self._data.get("core_ddi_dictionary", {}).items():
            if info.get("added_in") == new_release:
                added_entities.append(info)

        return {
            "from_release": old_release,
            "to_release": new_release,
            "ddi_count_delta": delta_count,
            "added_core_entities": added_entities,
            "citation": self.citation,
            "licensing_notice": self.licensing_notice,
        }
