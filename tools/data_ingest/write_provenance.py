#!/usr/bin/env python3
"""Render tools/data_ingest/PROVENANCE.md from provenance.json + verification.json.

The markdown is GENERATED so the sha256 values come straight from the recorded
evidence, never hand-copied (Tuzaklar §16 — leave reproducible evidence for
every number).
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
prov = json.loads((HERE / "provenance.json").read_text(encoding="utf-8"))
ver = json.loads((HERE / "verification.json").read_text(encoding="utf-8"))

src = prov["sources"]
arts = prov["artifacts"]
checks = ver["checks"]


def fmt(n: int) -> str:
    return f"{n:,}"


lines: list[str] = []
A = lines.append

A("# Vendored Data Provenance — tools/data_ingest/staging/")
A("")
A(f"Generated: `{prov['generated_at']}`  |  by `tools/data_ingest/fetch_sources.py`")
A("")
A("> **Build-time vendor data only.** The product is fully offline; runtime reads")
A("> these files from disk. No LLM, no cloud call, no network dependency ships.")
A("> Nothing here is merged into `data/` — that is Tur-2's job.")
A("")
A("## Verified licenses (read from the actual LICENSE file, not the README)")
A("")
A("| Source | Repo | Pinned commit | License (SPDX) | Evidence |")
A("|---|---|---|---|---|")
ev = {
    "obdex": "`LICENSE-DATA` (709 B) — CC0 1.0 Universal dedication, data files",
    "canboat": "`LICENSE` (614 B) — Apache License 2.0, Kees Verruijt",
    "dtcdb": "`LICENSE` (1,078 B) — MIT, Copyright (c) 2024 Wal33D",
    "sitrak": "`LICENSE` (881 B) — CC BY 4.0 (Russian-language deed)",
}
for k, s in src.items():
    A(f"| `{s['repo']}` | {k} | `{s['commit_sha'][:12]}` | **{s['license_spdx']}** | {ev.get(k, '')} |")
A("")
A("### SITRAK resolution (the one the recon could not verify)")
A("")
A("The recon flagged `STAS63-bit/sitrak-error-codes` as suspicious because the GitHub")
A("API reports `NOASSERTION`. **A real `LICENSE` file does exist** (881 bytes, sha")
A("`ffc973284ba440ae28b5eba7c22352a5582c7adf` at the repo root). Its text declares:")
A("")
A("> Creative Commons Attribution 4.0 International (CC BY 4.0)")
A("> База кодов ошибок SITRAK / SITRAK Error Codes Database — © МегаДата (megadata.pro)")
A("> Commercial use, copying, modification and distribution permitted **with**")
A("> attribution: name the source МегаДата / megadata.pro and keep the link.")
A("")
A("`NOASSERTION` is a **GitHub classifier artifact**: the license is a Russian-language")
A("deed, which GitHub's SPDX matcher cannot map. **Verdict: REDISTRIBUTABLE** under")
A("CC BY 4.0, *provided the attribution below ships in the product's about/licenses UI.*")
A("")
A("> **Required attribution (CC BY 4.0 obligation):**")
A(f"> `{src['sitrak'].get('attribution_text')}`")
A("")
A("## Artifacts")
A("")
A("| Artifact | Source URL | Bytes | sha256 | License |")
A("|---|---|---|---|---|")
for a in sorted(arts, key=lambda x: (x["kind"] != "data", x["artifact"])):
    if a["kind"] != "data":
        continue
    A(
        f"| `{a['staged_path']}` | [link]({a['source_url']}) | {fmt(a['bytes'])} "
        f"| `{a['sha256']}` | {a.get('license')} |"
    )
A("")
A("License texts preserved under `tools/data_ingest/licenses/`:")
A("")
A("| File | Bytes | sha256 |")
A("|---|---|---|")
for a in sorted(arts, key=lambda x: x["artifact"]):
    if a["kind"] == "license":
        A(f"| `{a['artifact']}` | {fmt(a['bytes'])} | `{a['sha256']}` |")
A("")
A(
    f"**Total data bytes staged: {fmt(sum(a['bytes'] for a in arts if a['kind'] == 'data'))}** "
    f"across {sum(1 for a in arts if a['kind'] == 'data')} data artifacts."
)
A("")
A("## Independent verification (records actually decode)")
A("")
A(f"`tools/data_ingest/verify_records.py` — **{ver['pass']} pass / {ver['fail']} fail**.")
A("Raw evidence: `tools/data_ingest/verification.json`.")
A("")
A("| Check | Result | Key numbers |")
A("|---|---|---|")
d = checks.get("dtcdb.sqlite", {})
if d.get("status") == "PASS":
    A(
        f"| Wal33D `dtc_codes.db` (stdlib `sqlite3`) | PASS | tables "
        f"{d['tables']}; `dtc_definitions` = **{fmt(d['row_counts']['dtc_definitions'])}** rows, "
        f"**{fmt(d['distinct_codes'])}** distinct codes; P0420 probe returned "
        f"{d['p0420_probe_rows']} rows |"
    )
c = checks.get("canboat.json", {})
if c.get("status") == "PASS":
    A(
        f"| canboat `canboat.json` (JSON parse) | PASS | **{fmt(c['total_pgns'])}** PGNs; "
        f"declared license `{c['declared_license']}`; Version {c['version']}, "
        f"SchemaVersion {c['schema_version']}; PGN range "
        f"{c['pgn_number_range'][0]}–{c['pgn_number_range'][1]} |"
    )
s = checks.get("sitrak.json", {})
if s.get("status") == "PASS":
    A(
        f"| SITRAK `error-codes.json` | PASS | **{fmt(s['records'])}** records, all with "
        f"SPN+FMI; {fmt(s['description_ru'])} RU / {fmt(s['description_en'])} EN; "
        f"**{s['distinct_systems']}** distinct systems |"
    )
o = checks.get("obdex.yaml", {})
if o.get("status") == "PASS":
    pids = o["pid_counts"]
    A(
        f"| OBDex generic YAML (PyYAML) | PASS | **{fmt(o['total_codes'])}** codes "
        f"across {len(o['codes_per_file'])} files; PIDs mode01={pids['mode01.yaml']} + "
        f"mode09={pids['mode09.yaml']} = **{sum(pids.values())}** |"
    )
m = checks.get("canboat.dm1", {})
if m.get("status") == "PASS":
    A(
        f"| canboat J1939 DM1 YAML | PASS | `pgn: 65226`, `id: activeTroubleCodes`; "
        f"lamp fields: {', '.join(str(x) for x in m['lamp_fields'])} |"
    )
A("")
A("## ⚠️ CORRECTIONS to the recon report (verified divergences)")
A("")
A("These were found by probing the real endpoints, and they change what Tur 2 can")
A("assume. Both recon claims below are **wrong** as of this fetch.")
A("")
A("### 1. OBDex does NOT publish pre-built JSON over GitHub Pages")
A("")
A("The recon's ingest commands fetch `https://foerbsnavi.github.io/obdex/generic.min.json`")
A("(`generic.min.json`, `pids/mode01.json`, `pids/mode09.json`, `meta.json`). **All of")
A("these 404.** The Pages site does not exist —")
A("`https://foerbsnavi.github.io/obdex/` returns **HTTP 404 \"There isn't a GitHub Pages")
A('site here"**, and the repository has **no `dist/` directory** and **no `meta.json`**.')

A("**What actually exists** (and is staged): the authored sources under `data/` —")
A("`data/generic/{P0,P2,P3,B0,C0,U0,U3}xxx_enriched.yaml` plus `data/pids/mode0{1,9}.yaml`.")
A("Tur 2 must convert YAML→JSON during merge (PyYAML is already available in this")
A("environment). **We did not fabricate a converted JSON artifact here.**")
A("")
A("### 2. `docs/canboat.json` contains NO J1939 — it is NMEA-2000 only")
A("")
A('The recon states canboat "is two of the four required families in one repo" and')
A("implies the generated `canboat.json` carries J1939/DM1. Verified against the")
A("actual file:")
A("")
A(
    f"- `{fmt(c['total_pgns'])}` PGNs total, number range **{c['pgn_number_range'][0]}–{c['pgn_number_range'][1]}** (NMEA-2000 space)."
)
A(f"- PGN **65226 (DM1) present? {c['contains_dm1_pgn_65226']}**")
A(f"- PGNs with an `SPN`/`FMI` field: **{c['pgns_with_spn_or_fmi_field']}**")
A(f"- PGNs typed J1939: **{c['pgns_typed_j1939']}**")
A("")
A("**J1939 exists only in the `database/j1939/pgns/*.yaml` sources**, of which we staged")
A("the DM1 file (`j1939_065226-activeTroubleCodes.yaml`). Tur 2 must not plan a J1939")
A("merge against `canboat.json`; it needs the YAML sources (or additional ones fetched")
A("the same way) for the J1939 side.")
A("")
A("### 3. Schema note: DM1 YAML and canboat.json use different key casing")
A("")
A("- J1939 YAML sources: **lowercase** (`pgn`, `fields`, `id`, `name`, `bitLength`).")
A("- Generated `canboat.json`: **PascalCase** (`PGN`, `Fields`, `Id`, `Name`, `BitLength`).")
A("")
A("A parser written for one will silently read nothing from the other — the exact")
A("class of bug in Tuzaklar §11 (a gap is a real state, not something to fill")
A("with an invented default).")
A("")
A("## Rejected sources (not vendored)")
A("")
A("| Source | Reason |")
A("|---|---|")
for r in prov["rejected"]:
    A(f"| {r['name']} | {r['reason']} |")
A("")
A("## Reproduce")
A("")
A("```bash")
A("python tools/data_ingest/fetch_sources.py      # download into staging/ + record provenance")
A("python tools/data_ingest/verify_records.py     # independent decode checks")
A("python tools/data_ingest/write_provenance.py   # regenerate this file")
A("```")
A("")
A("> ⚠️ Never relocate these scripts under `src/engine/ai/` — the AI package forbids")
A("> `urllib`/`http`/`socket`/`requests` and `tests/safety/test_ai_tx_isolation.py`")
A("> must stay green.")

(HERE / "PROVENANCE.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
print(f"wrote {HERE / 'PROVENANCE.md'}  ({len(lines)} lines)")
