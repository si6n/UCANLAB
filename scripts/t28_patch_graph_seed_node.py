"""T2-8 — merge the seed nodes into the SHIPPED graph (idempotent upsert + ``.bak``).

WHY A PATCH AND NOT A REGENERATION
---------------------------------
``scripts/derive_root_cause_graph.py`` regenerates ``root_cause_graph.json`` from
the two databases AND collapses the per-cause curated seed nodes into coarser
DB-title nodes (its own module header documents that). Re-running it to add ONE
node would replace the 8.974-node graph — a coverage regression far larger than
the single case being fixed, and it is not this task's mandate.

So the merge is done HERE, additively:

  * a seed node whose ``id`` is NOT in the graph is appended;
  * a seed node whose ``id`` IS in the graph and differs REPLACES that entry
    (the seed is the curated authority — same precedence the derivation script
    uses);
  * a seed node identical to the graph entry is left alone.

Nothing is deleted and every other node is byte-preserved. Idempotent: running
twice writes the same bytes. A ``.bak_t28`` of the graph is taken on first run.

The merged node's ``source_ref`` is RE-VERIFIED with the same
``_build_seed_resolver`` the derivation gate uses, so this script cannot smuggle
in a fabricated node even if the seed file were corrupted.
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
GRAPH = DIAG / "root_cause_graph.json"

#: Only the seed nodes this task is responsible for are merged. Merging the whole
#: 39-node seed set would change many unrelated hypotheses and is not measured
#: here.
T28_SEED_IDS: tuple[str, ...] = ("engine-overrev",)


def main() -> int:
    resolver = _build_seed_resolver()
    seed_payload = json.loads(SEED.read_text(encoding="utf-8"))
    by_id = {n["id"]: n for n in seed_payload["nodes"]}

    missing = [nid for nid in T28_SEED_IDS if nid not in by_id]
    if missing:
        print(f"ABORT: seed node(s) absent from {SEED.name}: {missing}")
        return 1

    incoming: list[dict] = []
    for nid in T28_SEED_IDS:
        node = by_id[nid]
        ref = str(node.get("source_ref", ""))
        if resolver(ref) is None:
            print(f"ABORT: seed source_ref does not resolve (no fabrication): {nid} -> {ref!r}")
            return 1
        incoming.append({k: (list(v) if isinstance(v, list) else v) for k, v in node.items()})

    backup = GRAPH.with_suffix(GRAPH.suffix + ".bak_t28")
    if not backup.exists():
        shutil.copy2(GRAPH, backup)
        print(f"backup written: {backup.name}")
    else:
        print(f"backup already present (kept): {backup.name}")

    payload = json.loads(GRAPH.read_text(encoding="utf-8"))
    nodes: list[dict] = payload["nodes"]
    before = len(nodes)
    changes: list[str] = []
    for node in incoming:
        node_id = str(node["id"])
        matches = [(i, n) for i, n in enumerate(nodes) if n.get("id") == node_id]
        if not matches:
            nodes.append(node)
            changes.append(f"+ inserted {node_id}")
            continue
        idx, current = matches[0]
        if current == node:
            changes.append(f"= unchanged {node_id}")
            continue
        nodes[idx] = node
        changes.append(f"~ replaced {node_id}")

    payload["nodes"] = nodes
    GRAPH.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for line in changes:
        print(f"  {line}")
    print(f"graph nodes: {before} -> {len(nodes)}  ({GRAPH.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
