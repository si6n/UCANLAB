"""T2-1 step 1 — extract the CURATED SEED nodes from the original graph.

WHY THIS EXISTS
---------------
``scripts/derive_root_cause_graph.py`` regenerates the graph from the two
databases. Run alone, it DELETED hand-curated expert knowledge that the DBs do
not carry (``diagnostic_copilot.py EXPERT_KNOWLEDGE_BASE`` keys, scenario rules,
SAE/J2012 documented related-code groups) — that regression broke 10 shipped
unit tests. T2-1 must deepen the graph, never amputate it.

The original 53-node graph mixed three kinds of content
(``scripts/_probe_t21g.py`` + ``_probe_t21i.py`` measure each):

  PASS B — REAL EXPERT KNOWLEDGE. Title describes a mechanism that is not a
      restatement of a golden ``actual_fault``, and/or the node is asserted by
      the shipped test suite (which is the product's behavioural contract).
      PRESERVED VERBATIM.
  PASS A — OVERFIT-TO-GOLDEN. Node title was written FROM a golden case's
      ``actual_fault`` text while ``expected_dtcs`` names exactly that golden
      code. ``evaluate_calibration`` word-matches the title against
      ``actual_fault``, so these nodes satisfy the metric BY CONSTRUCTION: the
      published 44.4 % baseline is therefore an overfit measurement, not a
      capability measurement. NOT preserved — the derived nodes cover the same
      codes from real ``causes[i]`` evidence instead.
  PASS C — DB TITLE ECHOES. One node per DB ``title``. Superseded by the
      derived nodes, which are finer-grained (one per specific ``causes[i]``).

THE RULE (deliberately conservative — knowledge loss is worse than a stale node)
-------------------------------------------------------------------------------
  KEEP  if the node's ``source_ref`` cites the expert KB / scenario /
        documented related-code group, **or** its ``id`` is asserted by
        ``tests/unit/test_hypothesis_engine.py``.
  DROP  only a node that is BOTH non-KB-sourced **and** unasserted by any test.

That is: an overfit-looking node survives unless it is also unreferenced. The
cost of keeping one stale node is one extra row in the hypothesis table; the cost
of dropping a real expert node is silently losing knowledge and breaking tests.

Writes ONLY ``data/diagnostics/root_cause_graph_seed.json``. Deterministic.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.engine.ai.golden_cases import load_all_cases  # noqa: E402

DIAG = ROOT / "data" / "diagnostics"
SOURCE = DIAG / "root_cause_graph.json.t21_before"
TARGET = DIAG / "root_cause_graph_seed.json"
TEST_SRC = ROOT / "tests" / "unit" / "test_hypothesis_engine.py"

_KB_MARKERS = (
    "EXPERT_KNOWLEDGE_BASE",
    "scenario",
    "_RELATED_CODE_GROUPS",
    "UCANLAB-EXPERT-KB",
)


def is_knowledge_sourced(node: dict) -> bool:
    """True when the node cites the expert KB / scenario / documented code group."""
    ref = node.get("source_ref", "")
    return any(marker in ref for marker in _KB_MARKERS)


def is_title_echo(node: dict, cases: dict) -> bool:
    """True when the node title restates a golden case's ``actual_fault``."""
    for code in node.get("expected_dtcs", []):
        case = cases.get(code)
        if not case or not case.actual_fault:
            continue
        actual = case.actual_fault.lower()
        if any(w in actual for w in node["title"].lower().split() if len(w) > 4):
            return True
    return False


def asserted_ids() -> set[str]:
    """Node ids the shipped test suite asserts on (the behavioural contract)."""
    text = TEST_SRC.read_text(encoding="utf-8")
    return set(re.findall(r'"([a-z0-9]+(?:-[a-z0-9]+)+)"', text))


def main() -> int:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    cases = {c.dtcs[0]["code"]: c for c in load_all_cases() if c.verified and not c.is_draft and c.actual_fault}
    asserted = asserted_ids()

    seeded: list[dict] = []
    dropped: list[dict] = []
    for node in payload["nodes"]:
        kb = is_knowledge_sourced(node)
        tested = node["id"] in asserted
        echo = is_title_echo(node, cases)
        if kb or tested:
            seeded.append(
                {
                    "id": node["id"],
                    "title": node["title"],
                    "evidence_signals": list(node["evidence_signals"]),
                    "expected_dtcs": list(node["expected_dtcs"]),
                    "contradicting_signals": list(node["contradicting_signals"]),
                    "source_ref": node["source_ref"],
                }
            )
        else:
            dropped.append({"id": node["id"], "title_echo": echo, "kb": kb})

    out = {
        "schema_version": 1,
        "_derivation_rule": (
            "Elle dogrulanmis uzman KB dugumleri: kaynak diagnostic_copilot.py "
            "EXPERT_KNOWLEDGE_BASE / senaryo kurallari / _RELATED_CODE_GROUPS, VE "
            "tests/unit/test_hypothesis_engine.py'nin dogruladigi dugum kimlikleri "
            "(urunun davranis sozlesmesi). Yalnizca HEM KB kaynagi olmayan HEM de hicbir "
            "test tarafindan dogrulanmayan dugumler disarida birakildi - bunlar golden "
            "actual_fault metnini tekrarlayan overfit dugumleriydi (olcum: "
            "scripts/_probe_t21g.py + _probe_t21i.py). Kural: bilgi kaybi, fazladan bir "
            "dugumden daha pahalidir. scripts/derive_root_cause_graph.py bu seed'i "
            "turetilen grafla BIRLESTIRIR (seed kimlik cakismasinda kazanir)."
        ),
        "nodes": seeded,
    }
    TARGET.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"seed nodes written : {len(seeded)}  -> {TARGET}")
    print(f"dropped (non-KB AND untested): {len(dropped)}")
    for d in dropped:
        print(f"   {d['id']:36s} title_echo={d['title_echo']}")
    missing_asserted = sorted(asserted - {n["id"] for n in seeded})
    print(f"test-asserted ids still missing: {len(missing_asserted)} {missing_asserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
