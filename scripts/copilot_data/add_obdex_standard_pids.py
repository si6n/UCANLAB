# -*- coding: utf-8 -*-
"""Add the 18 OBDex Mode 01 standard PIDs that the T2-4 merge skipped.

T2-4 (data/PROVENANCE.md §3.2) merged OBDex ``data/pids/mode01.yaml`` but
SKIPPED every PID whose hex number already existed in the database — and the
"existing" rows were Toyota-specific Torque CSV rows (``EN Engine Speed``,
``ICE Actual RPM``…). The result: the most important standard SAE J1979 PIDs
(04 load, 05 coolant, 0C rpm, 0D speed, 0F IAT, 10 MAF, 11 TPS, …) had NO
generic, manufacturer-neutral record, so the copilot could only cite a Toyota
row for "how do I read coolant temperature".

This script adds exactly those rows, in the same row format as the 114 OBDex
rows T2-4 already merged, from the SAME pinned artefact:

    https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/pids/mode01.yaml
    sha256 cc4c435fe5ce8af2ff5b9b084dd9a96c32c8257d2ac8116e819281cb06fa367a  (CC0-1.0)

The sha256 is checked before anything is written (fail-closed). Existing rows
are never modified. Idempotent: a second run adds nothing.

Usage:
    curl -o /tmp/mode01.yaml <url above>
    python scripts/copilot_data/add_obdex_standard_pids.py /tmp/mode01.yaml [--check]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "data" / "diagnostics" / "extended_pid_database.json"
PINNED_SHA256 = "cc4c435fe5ce8af2ff5b9b084dd9a96c32c8257d2ac8116e819281cb06fa367a"
SOURCE_REF = "https://github.com/foerbsnavi/OBDex/blob/bc58b0eb7273226a1aabae98e956b70b8362bda1"


def _key(row: dict) -> tuple[str, str]:
    pid = row.get("pid")
    pid_hex = f"{pid:02X}" if isinstance(pid, int) else str(pid).upper().zfill(2)
    return str(row.get("service", "")).zfill(2), pid_hex


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("yaml_path")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    raw = Path(args.yaml_path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PINNED_SHA256:
        print(f"DUR: sha256 {digest} != pinned {PINNED_SHA256}")
        return 1
    source = yaml.safe_load(raw.decode("utf-8"))
    db = json.loads(TARGET.read_text(encoding="utf-8"))
    have = {_key(p) for p in db["pids"] if p.get("data_source") == "obdex"}
    added = []
    for rec in source:
        service = str(rec.get("mode", "01")).zfill(2)
        pid_hex = str(rec["pid"]).upper().zfill(2)
        if service != "01" or (service, pid_hex) in have:
            continue
        rng = rec.get("range")
        added.append({
            "pid": int(pid_hex, 16), "pid_hex": pid_hex, "name": rec["name"]["en"], "description": "",
            "formula": rec.get("formula", "") or "", "unit": rec.get("unit", "") or "", "ecu_header": "",
            "service": service, "manufacturer": "", "vehicle_model": "", "ecu_module": "",
            "category": "standard", "data_source": "obdex", "source_repo": SOURCE_REF,
            "confidence": "standard", "min_value": None, "max_value": None, "bytes": rec.get("bytes"),
            "range": rng, "name_de": rec["name"].get("de", ""),
            "_source_license": "CC0-1.0", "_source_ref": SOURCE_REF,
        })
    print(f"eklenecek: {len(added)} -> {[a['pid_hex'] for a in added]}")
    if args.check or not added:
        return 0
    db["pids"].extend(added)
    db["metadata"]["total_pids"] = len(db["pids"])
    db["metadata"]["obdex_layer"]["pids"] = sum(1 for p in db["pids"] if p.get("data_source") == "obdex")
    db["metadata"]["obdex_layer"]["copilot_upgrade_note"] = (
        "18 standard Mode 01 PIDs skipped by T2-4 (hex collided with Toyota torque_csv rows) added from the "
        "same pinned artefact (sha256 verified) by scripts/copilot_data/add_obdex_standard_pids.py."
    )
    TARGET.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"yazildi: total_pids={db['metadata']['total_pids']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
