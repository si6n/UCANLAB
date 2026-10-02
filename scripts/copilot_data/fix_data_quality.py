# -*- coding: utf-8 -*-
"""Copilot-upgrade data-quality fixes (idempotent, --check to preview).

Every change is DERIVED from data already in the repository; nothing is
invented. Each fix is listed in docs/audit/copilot_data_quality_2026-10-02.md.

1. root_cause_graph.json — 39 curated seed nodes had no ``system`` field (the
   T2-1 derivation only set it on DB-derived nodes). The system id is derived
   from the node's first expected code: the DTC/SPN record's own ``subsystem``
   label mapped through system_taxonomy.json ``source_labels`` (exact label).
   Nodes whose label is not in the taxonomy are left without a system.
2. j1939_spn_fmi_database.json — 12 generic FMI definitions (13, 20–30) had no
   ``description_tr``. A Turkish rendering of the entry's OWN SAE J1939-73
   ``name`` is added with ``description_tr_source`` naming that origin. For
   22–30 the standard reserves the code; the TR text says exactly that.
3. hv_safety_thresholds.json — provenance blocks failed provenance_schema.json:
   no ``agent`` and two non-enum ``source.type`` values. The agent is recorded
   honestly as ``legacy-unrecorded`` (the importer was not recorded) and the
   types are mapped to their schema equivalents
   (oem_technical_service_bulletin -> service_bulletin,
   oem_service_manual -> oem_manual). No value/verbatim text is touched.
4. dtc_database.json — 303 records stored ``procedures_full`` as ONE string
   ("1. Foo 2. Bar ...") although the schema (and the copilot's reader) expect
   a list of steps, so those procedures were silently ignored. Each string is
   split on its own sequential "N. " markers (all 303 split into a gap-free
   1..n sequence — verified before writing) and the markers are removed. The
   step text itself is unchanged.

Usage: python scripts/copilot_data/fix_data_quality.py [--check]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "diagnostics"

FMI_TR = {
    "13": "Kalibrasyon dışı (kalibrasyon/ayar değeri geçersiz)",
    "20": "Veri yukarı yönde kaymış (sensör sapması - yüksek)",
    "21": "Veri aşağı yönde kaymış (sensör sapması - düşük)",
    **{str(n): "SAE J1939-73 tarafından ayrılmış FMI (standart bir anlamı tanımlanmamış)" for n in range(22, 31)},
}
TYPE_MAP = {"oem_technical_service_bulletin": "service_bulletin", "oem_service_manual": "oem_manual"}


def _fold(text: str) -> str:
    t = str(text).translate(str.maketrans("ıİşŞğĞüÜöÖçÇ", "iissgguuoocc")).lower()
    return " ".join(re.sub(r"[^0-9a-z]+", " ", t).split())


def fix_graph(check: bool) -> int:
    path = D / "root_cause_graph.json"
    graph = json.loads(path.read_text(encoding="utf-8"))
    dtc = json.loads((D / "dtc_database.json").read_text(encoding="utf-8"))
    spns = json.loads((D / "j1939_spn_fmi_database.json").read_text(encoding="utf-8"))["spns"]
    tax = json.loads((D / "system_taxonomy.json").read_text(encoding="utf-8"))
    label_to_id = {_fold(lab): s["id"] for s in tax["canonical_systems"] for lab in s.get("source_labels", [])}
    changed = 0
    for node in graph["nodes"]:
        if node.get("system"):
            continue
        for code in node["expected_dtcs"]:
            m = re.match(r"^SPN\s*(\d+)", code)
            rec = spns.get(f"SPN_{m.group(1)}") if m else dtc.get(code.split()[0])
            label = (rec or {}).get("subsystem")
            sid = label_to_id.get(_fold(label)) if label else None
            if sid:
                node["system"] = sid
                changed += 1
                break
    print(f"[graph] system eklendi: {changed}")
    if changed and not check:
        path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


def fix_fmi(check: bool) -> int:
    path = D / "j1939_spn_fmi_database.json"
    db = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for key, text in FMI_TR.items():
        entry = db["fmi_definitions"].get(key)
        if isinstance(entry, dict) and not entry.get("description_tr"):
            entry["description_tr"] = text
            entry["description_tr_source"] = (
                f"Curator Turkish rendering of this entry's own SAE J1939-73 name ('{entry.get('name')}'), "
                "copilot-upgrade 2026-10-02"
            )
            changed += 1
    print(f"[fmi] description_tr eklendi: {changed}")
    if changed and not check:
        path.write_text(json.dumps(db, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return changed


def fix_hv(check: bool) -> int:
    path = D / "hv_safety_thresholds.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for rec in data["thresholds"].values():
        prov = rec.get("provenance") or {}
        if "agent" not in prov:
            prov["agent"] = {"type": "system", "id": "legacy-unrecorded", "role": "importer"}
            changed += 1
        src = prov.get("source") or {}
        if src.get("type") in TYPE_MAP:
            src["type"] = TYPE_MAP[src["type"]]
            changed += 1
    print(f"[hv] provenance alanı düzeltildi: {changed}")
    if changed and not check:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


_STEP_SPLIT = re.compile(r"(?:^|\s)(?=\d{1,2}\.\s)")


def fix_procedures(check: bool) -> int:
    path = D / "dtc_database.json"
    db = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for code, rec in db.items():
        raw = rec.get("procedures_full") if isinstance(rec, dict) else None
        if not isinstance(raw, str):
            continue
        parts = [p.strip() for p in _STEP_SPLIT.split(raw) if p.strip()]
        nums = [int(m.group(1)) for p in parts if (m := re.match(r"(\d+)\.\s", p))]
        if nums != list(range(1, len(parts) + 1)):
            print(f"[procedures] {code}: numbering not sequential, left unchanged")
            continue
        rec["procedures_full"] = [re.sub(r"^\d+\.\s+", "", p) for p in parts]
        changed += 1
    print(f"[procedures] string -> list: {changed}")
    if changed and not check:
        path.write_text(json.dumps(db, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    fix_graph(args.check)
    fix_fmi(args.check)
    fix_hv(args.check)
    fix_procedures(args.check)
    return 0


if __name__ == "__main__":
    sys.exit(main())
