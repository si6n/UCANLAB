"""Promote the merge-ready part of data/intake into the knowledge base, then remove it from intake.

What is promoted (the rest stays in intake, waiting for evidence or a decision):

1. **SPN records**: ``spn_reference`` entries absent from
   ``j1939_spn_fmi_database.json`` that carry a parameter name, a unit and two
   independent sources (canboat YAML + vendored canboat DBC). Name, unit,
   resolution, bit length and PGN are copied verbatim; nothing else is
   written. An existing SPN key is never touched.
2. **J1939 PGN layouts**: ``pgn_layout`` entries where every field has a name
   and an SPN or a bit length, that are not already in
   ``canboat_pgn_reference.json``, have one layout per PGN (transport/ack
   variants stay out) and carry no bulk "Data" field. Written to its ``j1939``
   block next to DM1; the NMEA 2000 catalogue is not touched.
3. **Signal names**: J1939 parameter names (upstream text and DBC signal name)
   of an SPN that a canonical copilot signal already measures become aliases
   of that signal in ``signal_measurement_map.json``. The identity is the SPN
   number itself; a name already resolvable is skipped.

Also removed from intake: the ``oem_divergence`` records, already promoted to
``dtc_oem_meanings.json`` (commit a68a2d3, sha256 per source kept in its
metadata). Every removed record's MANIFEST row goes with it.

Licence: canboat Apache-2.0 (data/licenses/NOTICE.canboat), Wal33D MIT.
Build-time tool. Idempotent: a second run finds nothing to promote.

    python scripts/promote_intake.py
"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTAKE = ROOT / "data" / "intake"
DIAG = ROOT / "data" / "diagnostics"
SPN_DB = DIAG / "j1939_spn_fmi_database.json"
PGN_REF = DIAG / "canboat_pgn_reference.json"
SIGNAL_MAP = DIAG / "signal_measurement_map.json"
SIGNAL_ALIASES = DIAG / "signal_aliases.json"
MANIFEST = INTAKE / "MANIFEST.md"
BULK_BITS = 512
TODAY = "2026-10-03"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, data: dict, indent: int | None) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8")


def _indent_of(path: Path) -> int | None:
    head = path.read_text(encoding="utf-8")[:200]
    m = re.search(r"\n( +)\"", head)
    return len(m.group(1)) if m else None


def _fold(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(text).lower())


def _records(sub: str) -> list[tuple[Path, dict]]:
    return [(p, _load(p)) for p in sorted((INTAKE / sub).glob("*.json"))]


def promote_spns(removed: list[Path]) -> int:
    db = _load(SPN_DB)
    spns = db["spns"]
    added = 0
    for path, rec in _records("spn_ref"):
        p = rec["payload"]
        ready = p.get("kb_state") == "absent" and p.get("names_en") and p.get("units") and len(p.get("sources") or []) >= 2
        key = f"SPN_{p['spn']}"
        if not ready or key in spns:
            continue
        entry = {"spn": p["spn"], "name": p["names_en"][0], "unit": p["units"][0]}
        if p.get("resolutions"):
            entry["resolution"] = p["resolutions"][0]
        if p.get("bit_lengths"):
            entry["bit_length"] = p["bit_lengths"][0]
        if p.get("evidence_pgns"):
            entry["associated_pgn"] = p["evidence_pgns"][0]
        entry.update({
            "_source_license": "Apache-2.0",
            "_source_ref": rec["source"]["path"],
            "_source_evidence": [{"file": s["source_file"], "sha256": s["sha256"]} for s in p["sources"]],
            "_promoted_from": f"data/intake/spn_ref/{path.name}",
        })
        spns[key] = entry
        removed.append(path)
        added += 1
    if added:
        db["metadata"]["total_spns"] = len(spns)
        db["metadata"]["last_updated"] = TODAY
        _dump(SPN_DB, db, _indent_of(SPN_DB))
    return added


def _ready_layout(p: dict) -> bool:
    fields = p.get("fields") or []
    return bool(fields) and all(isinstance(f, dict) and str(f.get("name") or "").strip()
                                and (f.get("spn") or f.get("bits")) for f in fields)


def promote_pgns(removed: list[Path]) -> int:
    ref = _load(PGN_REF)
    have = set(ref.get("pgns_by_number") or {}) | set(ref.get("j1939") or {})
    records = _records("pgn")
    per_pgn = collections.Counter(r["payload"]["pgn"] for _p, r in records)
    added = 0
    for path, rec in records:
        p = rec["payload"]
        pgn = str(p["pgn"])
        if (not _ready_layout(p) or pgn in have or per_pgn[p["pgn"]] > 1
                or any((f.get("bits") or 0) >= BULK_BITS for f in p["fields"])):
            continue
        fields = []
        for f in p["fields"]:
            out = {"id": f.get("field_id"), "name": f.get("name")}
            out.update({k: f[k] for k in ("spn", "bits", "unit", "resolution", "description") if f.get(k) is not None})
            fields.append(out)
        entry = {"pgn": p["pgn"], "id": p.get("pgn_id"), "description": p.get("description"), "type": p.get("pgn_type")}
        entry.update({k: p[k] for k in ("priority", "interval_ms") if p.get(k) is not None})
        entry.update({
            "fields": fields,
            "_source_license": "Apache-2.0",
            "_source_ref": rec["source"]["path"],
            "_source_sha256": rec["source"]["snapshot"]["sha256"],
            "_promoted_from": f"data/intake/pgn/{path.name}",
        })
        ref.setdefault("j1939", {})[pgn] = entry
        removed.append(path)
        added += 1
    if added:
        ref["metadata"]["j1939_layouts"] = len(ref["j1939"])
        _dump(PGN_REF, ref, _indent_of(PGN_REF))
    return added


def promote_signal_names(removed: list[Path]) -> int:
    smap = _load(SIGNAL_MAP)
    aliases = _load(SIGNAL_ALIASES)
    known: set[str] = set()
    for rec in aliases.get("aliases") or []:
        known.update(_fold(f) for f in [rec.get("canonical", "")] + list(rec.get("source_forms") or [])
                     + list(rec.get("node_side") or []))
    by_spn = {}
    for sig in smap["signals"]:
        known.update(_fold(f) for f in [sig["canonical"]] + list(sig.get("aliases") or []))
        spn = (sig.get("j1939") or {}).get("spn")
        if isinstance(spn, int):
            by_spn[spn] = sig
    added = 0
    for path, rec in _records("spn_ref"):
        p = rec["payload"]
        sig = by_spn.get(p["spn"])
        if sig is None:
            continue
        new = []
        for name in list(p.get("names_en") or []) + list(p.get("dbc_signals") or []):
            if _fold(name) and _fold(name) not in known:
                known.add(_fold(name))
                new.append(name)
        if new:
            sig.setdefault("aliases", []).extend(new)
            sig.setdefault("provenance", []).append({
                "provenance_id": f"prv-sigmap-{sig['canonical'].lower()}-j1939-names",
                "target": {"record_id": sig["canonical"], "field": "aliases"},
                "activity": "promote_from_intake",
                "agent": {"type": "automated", "id": "scripts/promote_intake.py", "role": "generator"},
                "source": {"title": f"canboat J1939 parameter name of SPN {p['spn']} (YAML + vendored DBC)",
                           "path": rec["source"]["path"], "type": "standard", "licence": "Apache-2.0"},
                "transform": {"rule_id": "same-spn-name-alias", "values": new},
                "confidence": rec.get("confidence", "single_source"),
            })
            added += len(new)
        removed.append(path)  # the SPN's names are now resolvable or already were
    if added:
        _dump(SIGNAL_MAP, smap, _indent_of(SIGNAL_MAP))
    return added


def drop_promoted_oem(removed: list[Path]) -> int:
    meanings = _load(DIAG / "dtc_oem_meanings.json")
    promoted = {s["intake_id"] for s in meanings["metadata"]["sources"]}
    n = 0
    for path, rec in _records("oem"):
        if rec["intake_id"] in promoted:
            removed.append(path)
            n += 1
    return n


def drop_manifest_rows(removed: list[Path]) -> int:
    ids = {p.stem for p in removed}
    lines = MANIFEST.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [ln for ln in lines if not (ln.startswith("| ") and ln.split("|")[1].strip() in ids)]
    MANIFEST.write_text("".join(kept), encoding="utf-8")
    return len(lines) - len(kept)


def main() -> None:
    removed: list[Path] = []
    spns = promote_spns(removed)
    pgns = promote_pgns(removed)
    names = promote_signal_names(removed)
    oem = drop_promoted_oem(removed)
    removed = sorted(set(removed))
    rows = drop_manifest_rows(removed)
    for path in removed:
        path.unlink()
    print(f"SPN records added: {spns}; J1939 PGN layouts added: {pgns}; signal aliases added: {names}; "
          f"OEM divergence records retired: {oem}; intake files removed: {len(removed)}; MANIFEST rows removed: {rows}")


if __name__ == "__main__":
    main()
