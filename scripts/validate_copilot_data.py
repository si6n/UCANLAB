# -*- coding: utf-8 -*-
"""Validate every knowledge source the offline copilot reads (CI gate).

FAIL findings make the exit code 1; WARN/INFO findings are reported only.

Checks
------
* every JSON file under data/diagnostics, data/knowledge, data/golden_traces parses;
* provenance blocks of copilot-owned files and hv_safety_thresholds.json
  validate against data/diagnostics/provenance_schema.json;
* DTC records validate against dtc_record_schema.json once the runtime's
  legacy-provenance synthesis is applied (counted; legacy gaps are WARN);
* cross-references: lexicon -> canonical symptoms, signal map -> J1939 SPN /
  OBD PID / threshold keys (names must match the databases), graph codes ->
  DTC / SPN / OEM records, canonical symptom candidate codes -> records;
* duplicates and internal consistency (J1939 keys, graph ids, metadata totals,
  duplicate standard PID rows, NHTSA ids);
* CSV twins are in sync with their JSON sources (line endings normalised);
* no stray backup/artefact files (``*.bak*``, ``*.t21_before``, ``*.tmp-*``) in data/.

Usage::

    python scripts/validate_copilot_data.py            # report + exit code
    python scripts/validate_copilot_data.py --report docs/audit/out.md
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data"
D = DATA / "diagnostics"

COPILOT_FILES = ("symptom_lexicon.json", "signal_measurement_map.json", "copilot_glossary.json")
SPECIAL_CODE_RE = re.compile(r"^(N2K_|CAN_)")


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []
        self.metrics: dict[str, Any] = {}

    def add(self, level: str, check: str, detail: str) -> None:
        self.rows.append((level, check, detail))

    def count(self, level: str) -> int:
        return sum(1 for lv, _, _ in self.rows if lv == level)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _fold(text: Any) -> str:
    t = str(text).translate(str.maketrans("ıİşŞğĞüÜöÖçÇ", "iissgguuoocc")).lower()
    return " ".join(re.sub(r"[^0-9a-z]+", " ", t).split())


def check_json_parse(rep: Report) -> None:
    n = 0
    for base in (D, DATA / "knowledge", DATA / "golden_traces"):
        for path in sorted(base.rglob("*.json")):
            n += 1
            try:
                _load(path)
            except (OSError, ValueError) as exc:
                rep.add("FAIL", "json_parse", f"{path.relative_to(ROOT)}: {exc}")
    rep.metrics["json_files_parsed"] = n


def _prov_validator() -> Any:
    import jsonschema

    return jsonschema.Draft202012Validator(_load(D / "provenance_schema.json"))


def check_provenance(rep: Report) -> None:
    validator = _prov_validator()
    checked = 0

    def validate(blocks: Any, where: str) -> None:
        nonlocal checked
        if not isinstance(blocks, list) or not blocks:
            rep.add("FAIL", "provenance", f"{where}: provenance missing")
            return
        for block in blocks:
            checked += 1
            errors = [e.message for e in validator.iter_errors(block)]
            if errors:
                rep.add("FAIL", "provenance", f"{where}: {errors[0]}")

    lex = _load(D / "symptom_lexicon.json")
    for e in lex.get("entries", []):
        validate(e.get("provenance"), f"symptom_lexicon[{e.get('symptom_id')}]")
    validate(lex.get("safety_terms", {}).get("provenance"), "symptom_lexicon.safety_terms")
    for s in _load(D / "signal_measurement_map.json").get("signals", []):
        validate(s.get("provenance"), f"signal_measurement_map[{s.get('canonical')}]")
    for term, rec in _load(D / "copilot_glossary.json").get("terms", {}).items():
        validate(rec.get("provenance"), f"copilot_glossary[{term}]")
    for key, rec in _load(D / "hv_safety_thresholds.json").get("thresholds", {}).items():
        validate([rec.get("provenance")], f"hv_safety_thresholds[{key}]")
    rep.metrics["provenance_blocks_validated"] = checked


def check_dtc_schema(rep: Report, dtc: dict[str, Any]) -> None:
    import jsonschema
    from referencing import Registry, Resource

    from src.core.models.diagnostics import synthesize_provenance_from_legacy

    prov_schema = _load(D / "provenance_schema.json")
    registry = Registry().with_resource(prov_schema["$id"], Resource.from_contents(prov_schema))
    validator = jsonschema.Draft202012Validator(_load(D / "dtc_record_schema.json"), registry=registry)
    known = set(validator.schema["properties"])
    ok = legacy_gap = 0
    errors: Counter[str] = Counter()
    for code, rec in dtc.items():
        candidate = {k: v for k, v in rec.items() if k in known}
        if "provenance" not in candidate:
            synth = synthesize_provenance_from_legacy(code, rec)
            if synth:
                candidate["provenance"] = synth
        errs = list(validator.iter_errors(candidate))
        if not errs:
            ok += 1
        else:
            first = errs[0]
            if first.validator == "dependentRequired":
                legacy_gap += 1
            errors[f"{first.validator}: {first.message[:80]}"] += 1
    rep.metrics["dtc_records_schema_valid"] = ok
    rep.metrics["dtc_records_total"] = len(dtc)
    if legacy_gap:
        rep.add("WARN", "dtc_schema", f"{legacy_gap} DTC records carry content but no resolvable source (no provenance can be synthesised)")
    for msg, n in errors.most_common(5):
        rep.add("WARN" if "dependentRequired" in msg else "FAIL", "dtc_schema", f"{n}x {msg}")


def check_cross_refs(rep: Report, dtc: dict[str, Any], j1939: dict[str, Any]) -> None:
    spns = j1939["spns"]
    oem = _load(D / "dtc_database_oem_layer.json")
    symptoms = _load(D / "canonical_symptoms.json")["symptoms"]
    thresholds = _load(D / "telemetry_thresholds.json")["signals"]
    pids = _load(D / "extended_pid_database.json")["pids"]

    def code_exists(code: str) -> bool:
        m = re.match(r"^SPN\s*(\d+)", code)
        if m:
            return f"SPN_{m.group(1)}" in spns
        base = code.split()[0]
        return base in dtc or base in oem["codes"] or base in oem["oem_index"] or bool(SPECIAL_CODE_RE.match(base))

    for e in _load(D / "symptom_lexicon.json")["entries"]:
        if e["symptom_id"] not in symptoms:
            rep.add("FAIL", "lexicon_ref", f"unknown symptom_id {e['symptom_id']}")
    for s in _load(D / "signal_measurement_map.json")["signals"]:
        j = s.get("j1939")
        if j:
            rec = spns.get(f"SPN_{j['spn']}")
            if rec is None or rec.get("name") != j.get("name") or rec.get("associated_pgn") != j.get("pgn"):
                rep.add("FAIL", "signal_map_ref", f"{s['canonical']}: SPN {j['spn']} does not match the J1939 DB")
        o = s.get("obd")
        if o:
            rows = [p for p in pids if str(p.get("service", "")).zfill(2) == o["service"]
                    and (f"{p['pid']:02X}" if isinstance(p.get("pid"), int) else str(p.get("pid", "")).upper().zfill(2)) == o["pid"]
                    and p.get("name") == o["name"]]
            if not rows:
                rep.add("FAIL", "signal_map_ref", f"{s['canonical']}: OBD {o['service']}/{o['pid']} '{o['name']}' not in PID DB")
        if s.get("threshold_key") and s["threshold_key"] not in thresholds:
            rep.add("FAIL", "signal_map_ref", f"{s['canonical']}: unknown threshold {s['threshold_key']}")
    broken = [(sid, c) for sid, rec in symptoms.items() for c in rec.get("candidate_dtcs", []) if not code_exists(c)]
    rep.metrics["symptom_candidate_refs_broken"] = len(broken)
    for sid, c in broken:
        rep.add("WARN", "symptom_ref", f"canonical_symptoms[{sid}] candidate {c} has no DTC/SPN/OEM record")
    graph = _load(D / "root_cause_graph.json")["nodes"]
    missing = sorted({c for n in graph for c in n["expected_dtcs"] if not code_exists(c)})
    for c in missing:
        rep.add("FAIL", "graph_ref", f"graph code {c} has no record")
    ids = Counter(n["id"] for n in graph)
    for nid, n in ids.items():
        if n > 1:
            rep.add("FAIL", "graph_dup", f"duplicate node id {nid}")
    no_system = sum(1 for n in graph if not n.get("system"))
    rep.metrics["graph_nodes"] = len(graph)
    rep.metrics["graph_nodes_without_system"] = no_system
    if no_system:
        rep.add("WARN", "graph_system", f"{no_system} graph nodes have no system id")
    from src.engine.ai.hypothesis_engine import load_root_cause_graph

    try:
        load_root_cause_graph(D / "root_cause_graph.json")
    except Exception as exc:  # noqa: BLE001
        rep.add("FAIL", "graph_schema", str(exc))


def check_consistency(rep: Report, dtc: dict[str, Any], j1939: dict[str, Any]) -> None:
    upper = Counter(k.upper() for k in dtc)
    for k, n in upper.items():
        if n > 1:
            rep.add("FAIL", "dtc_dup", f"{k} appears {n}x (case variants)")
    bad = [k for k, v in dtc.items() if not re.fullmatch(r"[PBCU][0-9A-F]{4}", k)]
    if bad:
        rep.add("WARN", "dtc_key", f"{len(bad)} non-standard DTC keys (e.g. {bad[:3]})")
    for k, v in j1939["spns"].items():
        if f"SPN_{v.get('spn')}" != k:
            rep.add("FAIL", "spn_key", f"{k} carries spn={v.get('spn')}")
    for fmi, rec in j1939["fmi_definitions"].items():
        if not rec.get("description_tr"):
            rep.add("WARN", "fmi_tr", f"FMI {fmi} has no Turkish description")
    if j1939["metadata"].get("total_spns") != len(j1939["spns"]):
        rep.add("FAIL", "metadata", f"j1939 total_spns {j1939['metadata'].get('total_spns')} != {len(j1939['spns'])}")
    pid_db = _load(D / "extended_pid_database.json")
    if pid_db["metadata"].get("total_pids") != len(pid_db["pids"]):
        rep.add("FAIL", "metadata", "extended_pid total_pids != len(pids)")
    std = Counter((str(p.get("service")).zfill(2), p.get("pid_hex")) for p in pid_db["pids"] if p.get("data_source") == "obdex")
    for key, n in std.items():
        if n > 1:
            rep.add("FAIL", "pid_dup", f"standard PID {key} appears {n}x")
    rj = _load(D / "nhtsa_can_recalls_database.json")
    with (D / "nhtsa_can_recalls_database.csv").open(encoding="utf-8-sig", newline="") as fh:
        rc = [r["campaign_number"] for r in csv.DictReader(fh)]
    if set(rc) != set(rj) or len(rc) != len(rj):
        rep.add("FAIL", "csv_twin", f"nhtsa recalls csv ({len(rc)}) != json ({len(rj)})")
    title_tr = sum(1 for v in dtc.values() if str(v.get("title_tr") or "").strip())
    rep.metrics["dtc_title_tr_filled"] = title_tr
    rep.metrics["dtc_by_letter"] = dict(Counter(k[0] for k in dtc))


def check_csv_twins(rep: Report) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import rebuild_csv_exports as r

    jobs = [
        ("dtc_database.csv", r.DTC_CSV, r._read_existing_header(r.DTC_CSV), r._build_dtc_rows),
        ("uds_did_database.csv", r.UDS_CSV, r._read_existing_header(r.UDS_CSV), r._build_uds_rows),
        ("j1939_spn_fmi_database.csv", r.J1939_CSV, r.J1939_HEADER, r._build_j1939_rows),
        ("obd_mode06_database.csv", r.MODE06_CSV, r._read_existing_header(r.MODE06_CSV), r._build_mode06_rows),
        ("canonical_symptoms.csv", r.CANONICAL_SYMPTOMS_CSV, r._read_existing_header(r.CANONICAL_SYMPTOMS_CSV),
         r._build_canonical_symptoms_rows),
    ]
    for name, path, header, build in jobs:
        expected = r._render(header, build(), r._has_bom(path)).replace(b"\r\n", b"\n")
        actual = path.read_bytes().replace(b"\r\n", b"\n")
        if expected != actual:
            rep.add("FAIL", "csv_twin", f"{name} is out of sync with its JSON (run scripts/rebuild_csv_exports.py)")
    # extended PID twin: same row rule as tools/data_ingest/rebuild_extended_pid_csv.py
    pid_db = _load(D / "extended_pid_database.json")
    with (D / "extended_pid_database.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(io.StringIO(fh.read())))
    if len(rows) - 1 != len(pid_db["pids"]):
        rep.add("FAIL", "csv_twin", f"extended_pid_database.csv rows {len(rows) - 1} != json {len(pid_db['pids'])}")
    rep.metrics["csv_twins_checked"] = len(jobs) + 2


def check_artefacts(rep: Report) -> None:
    pattern = re.compile(r"(\.bak|\.t21_before$|\.tmp-|\.orig$|~$)")
    for path in sorted(DATA.rglob("*")):
        if path.is_file() and pattern.search(path.name):
            rep.add("FAIL", "artefact", f"stray artefact {path.relative_to(ROOT)}")


def render(rep: Report, seconds: float) -> str:
    lines = [
        "# Copilot veri doğrulama raporu",
        "",
        f"- Süre: {seconds:.1f} s · FAIL **{rep.count('FAIL')}** · WARN {rep.count('WARN')} · INFO {rep.count('INFO')}",
        "",
        "## Ölçümler",
        "",
    ]
    lines += [f"- `{k}`: {v}" for k, v in rep.metrics.items()]
    lines += ["", "## Bulgular", "", "| Seviye | Kontrol | Ayrıntı |", "|---|---|---|"]
    lines += [f"| {lv} | {ck} | {dt} |" for lv, ck, dt in rep.rows]
    return "\n".join(lines) + "\n"


def run(report_path: str | None = None, quiet: bool = False) -> Report:
    t0 = time.perf_counter()
    rep = Report()
    check_json_parse(rep)
    dtc = _load(D / "dtc_database.json")
    j1939 = _load(D / "j1939_spn_fmi_database.json")
    check_provenance(rep)
    check_dtc_schema(rep, dtc)
    check_cross_refs(rep, dtc, j1939)
    check_consistency(rep, dtc, j1939)
    check_csv_twins(rep)
    check_artefacts(rep)
    text = render(rep, time.perf_counter() - t0)
    if report_path:
        Path(report_path).write_text(text, encoding="utf-8")
    if not quiet:
        for lv, ck, dt in rep.rows:
            if lv == "FAIL":
                print(f"[FAIL] {ck}: {dt}")
        print(f"FAIL={rep.count('FAIL')} WARN={rep.count('WARN')} metrics={json.dumps(rep.metrics, ensure_ascii=False)}")
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", help="write a markdown report to this path")
    args = ap.parse_args()
    rep = run(args.report)
    return 1 if rep.count("FAIL") else 0


if __name__ == "__main__":
    raise SystemExit(main())
