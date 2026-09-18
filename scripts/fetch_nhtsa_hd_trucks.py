"""Fetch heavy-duty truck complaints from the official NHTSA API (staging only).

Source: https://api.nhtsa.gov — free, no key, public government data.
No scraping, no credentials, no third-party ToS involved.

Writes data/diagnostics/nhtsa_hd_trucks_STAGING.json — NEVER the live
nhtsa_can_complaints_database.json. A human reviews the staging file and
merges it explicitly (separate, approved step).

Schema mirrors the live DB: {source, vehicles[], complaints[]} with
complaint rows {odi_number, components, summary, date_of_incident}.
Only CAN-relevant component families are kept (electrical / powertrain /
engine / brakes / ADAS); pure body/interior rows are dropped.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STAGING_PATH = REPO_ROOT / "data" / "diagnostics" / "nhtsa_hd_trucks_STAGING.json"

BASE_URL = "https://api.nhtsa.gov/complaints/complaintsByVehicle"
USER_AGENT = "UCANLAB-data-tool/1.0 (NHTSA public API; contact: destek@universalcancloud.com)"

# (make aliases tried in order, model, years)
TARGETS: list[tuple[tuple[str, ...], str, tuple[int, ...]]] = [
    (("freightliner",), "Cascadia", (2020, 2021, 2022, 2023)),
    (("volvo truck", "volvo"), "VN", (2020, 2021, 2022, 2023)),
    (("peterbilt",), "579", (2020, 2021, 2022, 2023)),
    (("kenworth",), "T680", (2020, 2021, 2022, 2023)),
    (("mack",), "Anthem", (2020, 2021, 2022, 2023)),
    (("international", "navistar"), "LT", (2020, 2021, 2022, 2023)),
]

CAN_COMPONENTS = frozenset({
    "ELECTRICAL SYSTEM",
    "ENGINE",
    "POWER TRAIN",
    "VEHICLE SPEED CONTROL",
    "SERVICE BRAKES",
    "AIR BRAKES",
    "ELECTRONIC STABILITY CONTROL",
    "FORWARD COLLISION AVOIDANCE",
    "LANE DEPARTURE",
    "BACK OVER PREVENTION",
    "FUEL/PROPULSION SYSTEM",
    "HYBRID PROPULSION SYSTEM",
    "PARKING BRAKE",
    "TRACTION CONTROL SYSTEM",
    "CRUISE CONTROL",
})


def _fetch(make: str, model: str, year: int) -> dict:
    query = urllib.parse.urlencode({"make": make, "model": model, "modelYear": str(year)})
    req = urllib.request.Request(f"{BASE_URL}?{query}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _is_can_related(components: str) -> bool:
    parts = {p.strip().upper() for p in (components or "").split(",")}
    return not parts.isdisjoint(CAN_COMPONENTS)


def main() -> int:
    vehicles: list[dict] = []
    complaints: list[dict] = []
    seen_odi: set[int] = set()
    for makes, model, years in TARGETS:
        for year in years:
            payload: dict | None = None
            used_make = ""
            for make in makes:
                try:
                    payload = _fetch(make, model, year)
                    used_make = make
                    break
                except Exception as exc:  # noqa: BLE001 — try next alias
                    print(f"  retry {make}/{model}/{year}: {exc}")
                    continue
            if not payload:
                print(f"SKIP {model} {year}: no alias answered")
                continue
            results = payload.get("results") or []
            kept = []
            for row in results:
                try:
                    odi = int(row.get("odiNumber", 0))
                except (TypeError, ValueError):
                    continue
                if odi in seen_odi:
                    continue
                comps = str(row.get("components", ""))
                if not _is_can_related(comps):
                    continue
                seen_odi.add(odi)
                kept.append({
                    "odi_number": odi,
                    "components": comps,
                    "summary": str(row.get("summary", ""))[:2000],
                    "date_of_incident": str(row.get("dateOfIncident", "")),
                })
            vehicles.append({
                "make": used_make.upper(),
                "model": model,
                "year": str(year),
                "raw_count": len(results),
                "can_related": kept,
            })
            complaints.extend(kept)
            print(f"OK {used_make}/{model}/{year}: raw={len(results)} kept={len(kept)}")
            time.sleep(1.0)  # be polite to the public API
    staging = {
        "source": "api.nhtsa.gov complaints (heavy-duty staging, NOT merged)",
        "_staging_note": "Human review required before merging into nhtsa_can_complaints_database.json",
        "vehicles": vehicles,
        "complaints": complaints,
    }
    STAGING_PATH.write_text(json.dumps(staging, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"STAGING: {len(vehicles)} vehicles, {len(complaints)} complaints -> {STAGING_PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
