"""F-05: purge scraped web-page artifacts from the DTC database source JSON.

Five records (P2032, P2033, P2077, P2078, P2079) had an entire scraped web
page — JavaScript, JSON-LD schema.org graph, ad code, nav menu, FAQ block —
stored in their `causes` field instead of a diagnostic cause. The
QuarantineGatekeeper now blocks these at load time, but the source data must
also be repaired so every consumer (not just the gated load path) sees clean
prose.

Writes atomically (temp file + replace) with a .bak alongside. Deterministic:
same input -> same output, key order preserved.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine.ai.harvest_validator import CODE_ARTIFACT_PATTERN

TARGET = Path(__file__).resolve().parents[1] / "data" / "diagnostics" / "dtc_database.json"
TEXT_FIELDS = ("symptoms", "causes")


def main() -> int:
    data = json.loads(TARGET.read_text(encoding="utf-8"))
    print(f"loaded {len(data)} records from {TARGET}")

    repaired = 0
    dropped_values = 0
    emptied = []
    for code, info in data.items():
        if not isinstance(info, dict):
            continue
        changed = False
        for field in TEXT_FIELDS:
            value = info.get(field)
            if isinstance(value, str):
                if CODE_ARTIFACT_PATTERN.search(value):
                    info[field] = []
                    dropped_values += 1
                    changed = True
            elif isinstance(value, list):
                kept = []
                for item in value:
                    if CODE_ARTIFACT_PATTERN.search(str(item)):
                        dropped_values += 1
                        changed = True
                        continue
                    kept.append(item)
                if kept != value:
                    info[field] = kept
        if changed:
            repaired += 1
            # A record whose symptoms AND causes are both now empty cannot pass
            # the quarantine gate (zero valid symptom/cause). Flag it so the
            # caller knows to supply real prose rather than silently shipping a
            # record that will be dropped at load.
            if not info.get("symptoms") and not info.get("causes"):
                emptied.append(code)

    print(f"\nrepaired {repaired} records, dropped {dropped_values} artifact values")
    if emptied:
        print(f"WARNING: {len(emptied)} record(s) now have empty symptoms AND causes: {emptied}")
        print("         these must be re-authored from a real source (no fabrication).")
    if repaired == 0:
        print("nothing to do — source DB already clean")
        return 0

    backup = TARGET.with_suffix(".json.bak")
    if not backup.exists():
        shutil.copy2(TARGET, backup)
        print(f"backup written: {backup}")

    tmp = TARGET.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(TARGET)
    print(f"written: {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
