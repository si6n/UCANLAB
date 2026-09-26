#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Migrate dtc_database.json to include mandatory dtc_namespace and dtc_class fields.

AUTOSAR Dem (R21-11 / R25-11) & ISO 27145-3 (WWH-OBD) compliance:
- dtc_namespace (7 allowed values): SAE_J2012, SAE_J2012_4, ISO_14229_1, OBD, WWH_OBD, J1939, OEM
- dtc_class (5 allowed values): NoClass, A, B1, B2, C

Atomic write with backup.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.core.models.diagnostics import (  # noqa: E402
    DtcClass,
    DtcNamespace,
    classify_dtc_class,
    classify_dtc_namespace,
)

DTC_DB = REPO_ROOT / "data" / "diagnostics" / "dtc_database.json"


def migrate_database(path: Path = DTC_DB) -> int:
    if not path.is_file():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        print("ERROR: dtc_database.json must be a JSON object", file=sys.stderr)
        return 1

    updated = 0
    for code, record in data.items():
        if not isinstance(record, dict):
            continue
        if "dtc_namespace" not in record:
            record["dtc_namespace"] = classify_dtc_namespace(code).value
            updated += 1
        else:
            # Re-validate
            assert record["dtc_namespace"] in [ns.value for ns in DtcNamespace]

        if "dtc_class" not in record:
            record["dtc_class"] = classify_dtc_class(code, record.get("severity", "UNKNOWN")).value
            updated += 1
        else:
            # Re-validate
            assert record["dtc_class"] in [c.value for c in DtcClass]

    backup = path.with_suffix(".json.bak_schema_migration")
    backup.write_bytes(path.read_bytes())

    tmp = path.with_suffix(f".json.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)

    print(f"Migrated {len(data)} DTC records. Updated fields: {updated}. Backup: {backup.name}")
    return 0


if __name__ == "__main__":
    sys.exit(migrate_database())
