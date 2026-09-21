#!/usr/bin/env python3
"""T1-3 — Independently VERIFY the staged artifacts actually decode.

This is a *separate* script from the fetcher on purpose: the fetcher's own
success message is not evidence (Tuzaklar §1 — "ajanın kendi kontrolü doğrulama
değildir"). Here we re-open each artifact with an independent stdlib reader and
count real records.

Writes tools/data_ingest/verification.json and prints a summary.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

# Keep stdout UTF-8 so the Turkish/em-dash headers do not mojibake on Windows
# consoles (Tuzaklar §20 — encoding defects are real, not cosmetic).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

HERE = Path(__file__).resolve().parent
STAGING = HERE / "staging"
OUT = HERE / "verification.json"

report: dict[str, dict] = {}
failures: list[str] = []


def ok(key: str, **fields) -> None:
    report[key] = {"status": "PASS", **fields}
    print(f"  PASS  {key}")


def bad(key: str, reason: str, **fields) -> None:
    report[key] = {"status": "FAIL", "reason": reason, **fields}
    failures.append(f"{key}: {reason}")
    print(f"  FAIL  {key}  {reason}")


# --------------------------------------------------------------------------
# 1. Wal33D dtc_codes.db (MIT) — real SQLite via stdlib
# --------------------------------------------------------------------------
print("\n[1] dtc_codes.db (SQLite, stdlib)")
db = STAGING / "dtcdb" / "dtc_codes.db"
try:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cur = con.cursor()
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    counts = {}
    for t in tables:
        counts[t] = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]

    # Find the code table and prove we can read actual DTC rows.
    code_table = next((t for t in tables if "code" in t.lower()), tables[0])
    cols = [r[1] for r in cur.execute(f'PRAGMA table_info("{code_table}")')]
    sample = cur.execute(f'SELECT * FROM "{code_table}" LIMIT 3').fetchall()
    distinct_codes = None
    code_col = next((c for c in cols if c.lower() in ("code", "dtc", "dtc_code")), None)
    if code_col:
        distinct_codes = cur.execute(f'SELECT COUNT(DISTINCT "{code_col}") FROM "{code_table}"').fetchone()[0]
        # Prove a known real DTC decodes.
        hit = cur.execute(f'SELECT * FROM "{code_table}" WHERE "{code_col}" LIKE "P0420%" LIMIT 2').fetchall()
    else:
        hit = []
    con.close()

    if counts.get(code_table, 0) > 0 and sample:
        ok(
            "dtcdb.sqlite",
            file="tools/data_ingest/staging/dtcdb/dtc_codes.db",
            tables=tables,
            row_counts=counts,
            code_table=code_table,
            code_column=code_col,
            distinct_codes=distinct_codes,
            p0420_probe_rows=len(hit),
            sample_row=[list(map(str, r))[:5] for r in sample],
        )
    else:
        bad("dtcdb.sqlite", "table empty or unreadable", tables=tables)
except Exception as exc:  # noqa: BLE001
    bad("dtcdb.sqlite", f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# 2. canboat.json (Apache-2.0) — JSON parse + PGN counts
# --------------------------------------------------------------------------
print("\n[2] canboat.json (JSON, stdlib)")
cj = STAGING / "canboat" / "canboat.json"
try:
    doc = json.loads(cj.read_text(encoding="utf-8"))
    pgns = doc.get("PGNs", [])
    lic = doc.get("License")
    ver = doc.get("Version")
    schema = doc.get("SchemaVersion")
    n = len(pgns)
    if not pgns:
        bad("canboat.json", "no PGNs parsed", declared_license=lic)
    else:
        # FINDING (T1-3): docs/canboat.json is NMEA-2000-ONLY. It contains NO
        # J1939 content at all — no PGN 65226 (DM1), no SPN/FMI field, no J1939
        # type. The recon report implied this file carried both families; it
        # does not. J1939 lives solely in database/j1939/pgns/*.yaml (staged
        # separately as canboat.dm1). Recorded here so Tur-2 does not plan a
        # J1939 merge against this file.
        lownums = [p.get("PGN") for p in pgns if isinstance(p.get("PGN"), int)]
        has_dm1 = any(x == 65226 for x in lownums)
        spn_fmi = sum(
            1 for p in pgns if any(str(f.get("Name", "")).upper() in ("SPN", "FMI") for f in p.get("Fields", []))
        )
        j1939_typed = sum(1 for p in pgns if "j1939" in f"{p.get('Type', '')}{p.get('Id', '')}".lower())
        ok(
            "canboat.json",
            file="tools/data_ingest/staging/canboat/canboat.json",
            total_pgns=n,
            declared_license=lic,
            version=ver,
            schema_version=schema,
            pgn_number_range=[min(lownums), max(lownums)],
            contains_dm1_pgn_65226=has_dm1,
            pgns_with_spn_or_fmi_field=spn_fmi,
            pgns_typed_j1939=j1939_typed,
            coverage_note=(
                "NMEA-2000 ONLY. No J1939/DM1 in this generated file — "
                "J1939 is vendored separately as j1939_065226-activeTroubleCodes.yaml"
            ),
        )
except Exception as exc:  # noqa: BLE001
    bad("canboat.json", f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# 3. SITRAK error-codes.json (CC BY 4.0) — JSON parse + record counts
# --------------------------------------------------------------------------
print("\n[3] sitrak_error-codes.json (JSON, stdlib)")
sj = STAGING / "sitrak" / "sitrak_error-codes.json"
try:
    arr = json.loads(sj.read_text(encoding="utf-8"))
    if not isinstance(arr, list):
        arr = arr.get("codes") or arr.get("data") or []
    n = len(arr)
    keys = sorted(arr[0].keys()) if arr else []
    has_spn_fmi = sum(1 for r in arr if "spn" in r and "fmi" in r)
    ru = sum(1 for r in arr if r.get("description_ru"))
    en = sum(1 for r in arr if r.get("description_en"))
    systems = len({r.get("system") for r in arr if r.get("system")})
    if n > 0 and has_spn_fmi:
        ok(
            "sitrak.json",
            file="tools/data_ingest/staging/sitrak/sitrak_error-codes.json",
            records=n,
            fields=keys,
            with_spn_and_fmi=has_spn_fmi,
            description_ru=ru,
            description_en=en,
            distinct_systems=systems,
            sample=arr[0],
        )
    else:
        bad("sitrak.json", f"no records or SPN/FMI missing (n={n})")
except Exception as exc:  # noqa: BLE001
    bad("sitrak.json", f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# 4. OBDex YAML (CC0) — parse with PyYAML if present, else count entries
# --------------------------------------------------------------------------
print("\n[4] OBDex YAML (CC0) — parse + code counts")
gen = STAGING / "obdex" / "data" / "generic"
try:
    import yaml  # type: ignore

    have_yaml = True
except ImportError:
    have_yaml = False

if not have_yaml:
    bad("obdex.yaml", "PyYAML not importable; cannot independently parse")
else:
    total_codes = 0
    per_file: dict[str, int] = {}
    sample_code = None
    for y in sorted(gen.glob("*_enriched.yaml")):
        try:
            doc = yaml.safe_load(y.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            bad(f"obdex.{y.name}", f"{type(exc).__name__}: {exc}")
            continue
        # Accept either a top-level list or a dict wrapping one.
        rows = doc if isinstance(doc, list) else (doc.get("codes") if isinstance(doc, dict) else None)
        if rows is None and isinstance(doc, dict):
            # Some dumps key by code -> normalise to its values.
            vals = [v for v in doc.values() if isinstance(v, dict)]
            rows = vals or list(doc.values())
        n = len(rows) if rows else 0
        per_file[y.name] = n
        total_codes += n
        if sample_code is None and rows:
            first = rows[0]
            sample_code = first if isinstance(first, dict) else {"code": str(first)}

    # PID files
    pid_counts = {}
    for p in ("mode01.yaml", "mode09.yaml"):
        pf = STAGING / "obdex" / "data" / "pids" / p
        doc = yaml.safe_load(pf.read_text(encoding="utf-8"))
        rows = doc if isinstance(doc, list) else list(doc.values()) if isinstance(doc, dict) else []
        pid_counts[p] = len(rows)

    if total_codes > 0:
        ok(
            "obdex.yaml",
            dir="tools/data_ingest/staging/obdex/data/generic",
            codes_per_file=per_file,
            total_codes=total_codes,
            pid_counts=pid_counts,
            sample_record=sample_code,
        )
    else:
        bad("obdex.yaml", "parsed zero codes", codes_per_file=per_file)


# --------------------------------------------------------------------------
# 5. J1939 DM1 YAML — confirm the exact SPN/FMI bit layout
# --------------------------------------------------------------------------
print("\n[5] j1939 DM1 YAML — SPN/FMI bit layout")
dm1f = STAGING / "canboat" / "j1939_065226-activeTroubleCodes.yaml"
if not have_yaml:
    bad("canboat.dm1", "PyYAML not importable")
else:
    try:
        # NOTE: the J1939 YAML sources use a LOWERCASE schema
        # (pgn/fields/id), unlike the generated canboat.json (PGN/Fields).
        doc = yaml.safe_load(dm1f.read_text(encoding="utf-8"))
        pgn_no = doc.get("pgn")
        fields = doc.get("fields", [])
        names = [f.get("name") for f in fields]
        if pgn_no == 65226 and names:
            ok(
                "canboat.dm1",
                pgn=pgn_no,
                id=doc.get("id"),
                description=doc.get("description"),
                field_names=names,
                lamp_fields=[n for n in names if "Lamp" in str(n)],
                schema_note="lowercase keys (pgn/fields/id) — differs from canboat.json",
            )
        else:
            bad("canboat.dm1", f"DM1 PGN/fields not as expected (pgn={pgn_no})", field_names=names)
    except Exception as exc:  # noqa: BLE001
        bad("canboat.dm1", f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------
npass = sum(1 for v in report.values() if v["status"] == "PASS")
nfail = sum(1 for v in report.values() if v["status"] == "FAIL")
print(f"\n{'=' * 60}\nVERIFICATION: {npass} pass / {nfail} fail")
if failures:
    for f in failures:
        print(f"  - {f}")

OUT.write_text(
    json.dumps({"checks": report, "pass": npass, "fail": nfail}, indent=2, ensure_ascii=False),
    encoding="utf-8",
    newline="",
)
print(f"wrote {OUT}")
sys.exit(1 if nfail else 0)
