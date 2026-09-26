"""t66_adapter.py — convert T66 scan outputs into merge-ready records (T63 schema).

The merge tool (`scripts/t63_merge.py`) expects per-record:
  - code / key / spn / fmi
  - evidence (>= 20 chars, verbatim)
  - the fields it may fill, in the shape it understands

Language contract (locked by tests/unit/test_t46_merge.py):
  English harvest text NEVER goes into Turkish fields.
  Field shapes measured in the production DB (2026-09-22):
  - DTC `symptoms_en`  : LIST of strings   (+ `symptoms_en_source` URL)
  - DTC `causes_en`    : STRING (sentences joined with " | ")
  - DTC `description_en`: STRING
  - DTC `title_en`     : STRING
  - DTC English steps  -> `procedures_steps` (merge maps it to `procedures_full`,
    which is a list of step strings)
  J1939 English procedure bundles -> `procedures_full_append` (append-only by source_url).

Usage:
    python scripts/t66_adapter.py --kind dtc --input output/scan_t66_obdfyi.json \
        --out output/mr_t66_obdfyi.json [--evidence-min 20]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent

ADAPTER_VERSION = "t66_adapter/1.1-provenance"


def _provenance(rec: dict, evidence: str) -> dict:
    """I-18: exact evidence/hash + source URL/ID + timestamp + version + status.

    quality / verification_status is ALWAYS "unverified" here — adapter output
    is never authoritative; only t66_verify can upgrade it downstream.
    """
    retrieved = rec.get("retrieved_at") or rec.get("retrieval_timestamp")
    if not isinstance(retrieved, str) or not retrieved.strip():
        retrieved = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    return {
        "evidence": evidence,
        "evidence_sha256": hashlib.sha256(evidence.encode("utf-8")).hexdigest(),
        "source_url": rec.get("url"),
        "source_id": rec.get("source_file") or rec.get("content_signature") or rec.get("source"),
        "retrieved_at": retrieved,
        "adapter_version": ADAPTER_VERSION,
        "verification_status": "unverified",
    }


def _field(rec: dict, name: str) -> object:
    """Read ``name`` accepting BOTH harvest naming conventions (T69-F).

    Scans from different rounds spell the same payload differently: the T66/T69
    harvests wrote ``steps`` / ``causes`` / ``symptoms``, while the T69-F
    troubleshootmyvehicle corpus writes ``steps_en`` / ``causes_en`` /
    ``symptoms_en`` (its own language contract — the text IS English, so the
    suffix is explicit). Both must reach the same adapter output, otherwise a
    whole corpus silently adapts to zero records.
    """
    if name in rec:
        return rec[name]
    return rec.get(f"{name}_en")


def pick_evidence(rec: dict) -> str | None:
    """Longest verbatim string available in the record, for the evidence gate."""
    cands: list[str] = []
    for f in ("steps", "causes", "symptoms", "description", "title"):
        v = _field(rec, f)
        if isinstance(v, list):
            cands.extend(str(x) for x in v if isinstance(x, str))
        elif isinstance(v, str):
            cands.append(v)
    ev = rec.get("evidence")
    if isinstance(ev, str):
        cands.append(ev)
    cands = [c.strip() for c in cands if isinstance(c, str) and len(c.strip()) >= 40]
    return max(cands, key=len) if cands else None


def adapt_dtc(rec: dict, evidence_min: int) -> dict | None:
    code = str(rec.get("code") or "").strip().upper()
    if not code:
        return None
    out: dict = {
        "source": rec.get("source"),
        "url": rec.get("url"),
        "code": code,
        "key": code,
    }
    # English payload -> *_en fields (T46 contract). Never the Turkish ones.
    # Shapes mirror the production DB: symptoms_en is a LIST, the rest are STRINGS.
    if isinstance(_field(rec, "symptoms"), list) and _field(rec, "symptoms"):
        out["symptoms_en"] = [str(x).strip() for x in _field(rec, "symptoms") if str(x).strip()]
        out["symptoms_en_source"] = rec.get("url")
    if isinstance(_field(rec, "causes"), list) and _field(rec, "causes"):
        causes = [str(x).strip() for x in _field(rec, "causes") if str(x).strip()]
        if causes:
            out["causes_en"] = " | ".join(causes)
            out["causes_en_source"] = rec.get("url")
    # T70-F: these two MUST go through `_field` like symptoms/causes/steps do.
    # Measured on `output/scan_t70_open_sources.json`: all 28,817 records spell
    # the payload `description_en` / `title_en` (21,856 and 13,622 non-empty)
    # and ZERO carry a plain `description` / `title`, so a direct `rec.get(...)`
    # here silently adapted 19,794 records to causes-only and dropped every
    # title and description on the floor. Same defect class as the T69-F
    # `steps`/`steps_en` gap.
    _desc = _field(rec, "description")
    if isinstance(_desc, str) and _desc.strip():
        out["description_en"] = _desc.strip()
        out["description_en_source"] = rec.get("url")
    _ttl = _field(rec, "title")
    if isinstance(_ttl, str) and _ttl.strip():
        out["title_en"] = _ttl.strip()
        out["title_en_source"] = rec.get("url")
    # English steps -> procedures_steps (merge persists as procedures_full).
    if isinstance(_field(rec, "steps"), list) and _field(rec, "steps"):
        out["procedures_steps"] = [str(x).strip() for x in _field(rec, "steps") if str(x).strip()]
        # T69: `procedures_full` on the DTC side is a flat list[str] with ONE
        # source URL per record. Fill-if-empty therefore cannot express "this
        # record already has a 7-step obd2.com summary and we now have a
        # 77-step test procedure from a second site" — the richer harvest was
        # silently dropped. The bundle below carries the append path the J1939
        # side has had since T66, deduped by source_url, so a DTC can hold the
        # procedures of several independent sources without overwriting any.
        out["procedures_full_append"] = {
            "source_url": rec.get("url"),
            "source_site": rec.get("source"),
            "steps": [str(x).strip() for x in _field(rec, "steps") if str(x).strip()],
            "causes": [
                str(x).strip() for x in (_field(rec, "causes") or []) if str(x).strip()
            ],
            "symptoms": [
                str(x).strip() for x in (_field(rec, "symptoms") or []) if str(x).strip()
            ],
            "quality": "unverified",
            "method": "brightdata" if rec.get("_via_brightdata") else "free",
        }
    ev = pick_evidence(rec)
    if not ev or len(ev) < evidence_min:
        return None
    out["evidence"] = ev
    out["provenance"] = _provenance(rec, ev)
    return out


def adapt_j1939(rec: dict, evidence_min: int) -> dict | None:
    spn = rec.get("spn")
    if spn is None:
        return None
    fmi = rec.get("fmi")
    key = rec.get("key") or (f"SPN_{int(spn)}_FMI_{int(fmi)}" if fmi is not None else f"SPN_{int(spn)}")
    out: dict = {
        "source": rec.get("source"),
        "url": rec.get("url"),
        "key": key,
        "spn": int(spn),
        "fmi": int(fmi) if fmi is not None else None,
    }
    bundle = rec.get("procedures_full_append")
    if not isinstance(bundle, dict):
        bundle = {}
    steps = rec.get("steps") or bundle.get("steps") or []
    causes = rec.get("causes") or bundle.get("causes") or []
    symptoms = rec.get("symptoms") or bundle.get("symptoms") or []
    if not (steps or causes or symptoms):
        return None
    # Bundle shape mirrors the DB's dominant schema (6074 existing bundles:
    # overview + source_url + fmi + causes + steps + symptoms). The copilot
    # renders `overview`/`causes`/`steps`/`symptoms` for non-Eaton bundles.
    overview = str(_field(rec, "description") or bundle.get("summary") or "").strip()
    out["procedures_full_append"] = {
        "overview": overview[:600],
        "source_url": rec.get("url"),
        "source_site": rec.get("source"),
        "fmi": str(int(fmi)) if fmi is not None else None,
        "severity": rec.get("severity"),
        "causes": [str(x).strip() for x in causes if str(x).strip()],
        "steps": [str(x).strip() for x in steps if str(x).strip()],
        "symptoms": [str(x).strip() for x in symptoms if str(x).strip()],
        "quality": "unverified",  # kanıt kalitesi gerçek kanaldan türetilmiyor — uydurma "high" yok
        "method": "free",
    }
    if isinstance(symptoms, list) and symptoms:
        out["symptoms"] = [str(x).strip() for x in symptoms if str(x).strip()]
    if isinstance(steps, list) and steps:
        out["diagnostic_steps"] = [str(x).strip() for x in steps if str(x).strip()]
    ev = pick_evidence(rec)
    if not ev or len(ev) < evidence_min:
        return None
    out["evidence"] = ev
    out["provenance"] = _provenance(rec, ev)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["dtc", "j1939", "auto"], default="auto")
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--evidence-min", type=int, default=20)
    args = ap.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    records = data.get("records") if isinstance(data, dict) else data
    meta = data.get("_meta", {}) if isinstance(data, dict) else {}

    kind = args.kind
    if kind == "auto":
        # Her iki dal da aynıydı (ölü if/else): tek seçim.
        kind = "j1939" if any(r.get("spn") is not None for r in records[:50]) else "dtc"

    fn = adapt_j1939 if kind == "j1939" else adapt_dtc
    out_records = []
    dropped_no_evidence = 0
    for rec in records:
        if rec.get("placeholder") is True:
            continue
        adapted = fn(rec, args.evidence_min)
        if adapted is None:
            dropped_no_evidence += 1
            continue
        out_records.append(adapted)

    payload = {
        "_meta": {
            "ajan": "orchestrator",
            "tur": "T66",
            "adapter": "t66_adapter",
            "kind": kind,
            "input": args.input,
            "input_records": len(records),
            "adapted_records": len(out_records),
            "dropped_no_evidence": dropped_no_evidence,
            "source_meta": meta,
        },
        "target_db": "dtc" if kind == "dtc" else "j1939",
        "records": out_records,
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"kind": kind, "in": len(records), "out": len(out_records),
                      "dropped": dropped_no_evidence}, ensure_ascii=False))
    return 0 if out_records else 1  # 0 kayıt uyarıldı = hata


if __name__ == "__main__":
    raise SystemExit(main())
