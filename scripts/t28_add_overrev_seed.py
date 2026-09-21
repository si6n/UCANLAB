"""T2-8 — ADD the SPN 190 over-rev seed node (idempotent, upsert + ``.bak``).

WHY
---
Measured (``scripts/_t28_probe_measure.py``): ``case-spn190-verified`` is a MISS
with top-1 ``crank-sync-loss``. Its own code has been resolved to the WRONG
mechanism:

    golden dtcs[0].code  = "SPN 190"                       (FMI lives only in prose)
    golden actual_fault  = "FMI 0 (Aşırı Devir): Motor devri >2450 RPM…"
    graph, SPN 190       = crank-sync-loss ONLY
        source_ref = EXPERT_KNOWLEDGE_BASE['SPN190'].causes[1]
        FMI 2 (Faz Senkronizasyon Kaybı)  -> the phase-sync mechanism

The over-rev mechanism the golden case documents IS present in the same
``EXPERT_KNOWLEDGE_BASE`` record, at ``causes[0]``:

    "FMI 0 (Aşırı Devir): Motor devri >2450 RPM (Yokuş aşağı vites hatası)."

and the shipped J1939 DB independently confirms it as the FMI 0 fault title:

    j1939_spn_fmi_database.json['spns']['SPN_190']['fault_matrix']['0']
        = "Motor Devri (Krank Hızı) - Veri geçerli fakat normal çalışma
           aralığının üzerinde"

So this is NOT a fabricated mechanism: the node is a direct transcription of a
real expert-KB entry and a real DB fault title. ``scripts/derive_root_cause_graph.py``
already VERIFIES that reference (``_build_seed_resolver`` accepts
``diagnostic_copilot.py EXPERT_KNOWLEDGE_BASE['SPN190'].causes[0]`` — checked,
and an invented key is rejected).

WHAT
----
Upsert ONE node into ``data/diagnostics/root_cause_graph_seed.json``:

    id                 : engine-overrev
    expected_dtcs      : ["SPN 190 FMI 0"]        <- QUALIFIED
    evidence_signals   : ["EngineSpeed"]          (real: over-rev happens on the
                                                   same sensor SPN 190 rides on)
    contradicting      : []
    source_ref         : diagnostic_copilot.py EXPERT_KNOWLEDGE_BASE['SPN190'].causes[0]

DELIBERATELY NOT DONE (the honest limit):
  * The graph is NOT regenerated. ``derive_root_cause_graph.py`` would collapse
    the per-cause seed nodes (its own header documents this) — that would be a
    regression of 8.974 nodes to fix one case. ``scripts/patch_graph_seed_node.py``
    merges just this node into the shipped graph, idempotently.
  * The FMI is taken from the expert-KB ENTRY (``causes[0]`` -> FMI 0), not
    parsed out of the golden case's prose. The prose is user-verified evidence
    about WHAT happened; the KB entry is the authoritative statement of WHICH
    mechanism carries FMI 0.

Idempotent: re-running rewrites the same bytes. A ``.bak`` is taken first.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.derive_root_cause_graph import _build_seed_resolver  # noqa: E402

DIAG = ROOT / "data" / "diagnostics"
SEED = DIAG / "root_cause_graph_seed.json"

NODE: dict[str, object] = {
    "id": "engine-overrev",
    "title": "Aşırı devir — motor devri normal çalışma aralığının üzerinde (yokuş aşağı vites hatası)",
    "evidence_signals": ["EngineSpeed"],
    "expected_dtcs": ["SPN 190 FMI 0"],
    "contradicting_signals": [],
    "source_ref": "diagnostic_copilot.py EXPERT_KNOWLEDGE_BASE['SPN190'].causes[0]",
}


def main() -> int:
    resolver = _build_seed_resolver()
    if resolver(str(NODE["source_ref"])) is None:
        print(f"ABORT: source_ref does not resolve (no fabrication): {NODE['source_ref']!r}")
        return 1

    if not SEED.is_file():
        print(f"ABORT: seed file missing: {SEED}")
        return 1
    backup = SEED.with_suffix(SEED.suffix + ".bak_t28")
    if not backup.exists():
        shutil.copy2(SEED, backup)
        print(f"backup written: {backup.name}")
    else:
        print(f"backup already present (kept): {backup.name}")

    payload = json.loads(SEED.read_text(encoding="utf-8"))
    nodes: list[dict] = payload["nodes"]
    node_id = str(NODE["id"])
    existing = [n for n in nodes if n.get("id") == node_id]
    canonical = {k: (list(v) if isinstance(v, list) else v) for k, v in NODE.items()}

    if existing:
        if existing[0] == canonical:
            print(f"no change: {node_id} already present and identical")
        else:
            idx = nodes.index(existing[0])
            nodes[idx] = canonical
            print(f"updated: {node_id} (in place, index {idx})")
    else:
        nodes.append(canonical)
        print(f"inserted: {node_id} (nodes {len(nodes) - 1} -> {len(nodes)})")

    payload["nodes"] = nodes
    SEED.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"seed nodes now: {len(nodes)} -> {SEED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
