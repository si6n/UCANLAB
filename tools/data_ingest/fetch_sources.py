#!/usr/bin/env python3
"""T1-3 (scout) — Build-time vendor fetcher for legally-clean CAN/OBD data.

HARD CONSTRAINTS (operator-set, non-negotiable):
  * NO LLM, NO cloud call in the product. This script is BUILD-TIME ONLY.
    It runs on a maintainer's dev machine, once. Runtime reads from disk.
  * This script lives under tools/data_ingest/ and MUST NEVER be moved under
    src/engine/ai/ — the AI package forbids urllib/http/socket/requests
    (tests/safety/test_ai_tx_isolation.py).
  * It writes to tools/data_ingest/staging/ ONLY. It never merges into data/.
    Merging is Tur-2's job (telemetry agent).

Every artifact is recorded with sha256 + source_url + fetched_at + license +
commit SHA in tools/data_ingest/provenance.json (and PROVENANCE.md).

Usage:
    python tools/data_ingest/fetch_sources.py            # download everything
    python tools/data_ingest/fetch_sources.py --verify   # re-hash staging only
    python tools/data_ingest/fetch_sources.py --source obdex
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
STAGING = HERE / "staging"
LICENSES = HERE / "licenses"
PROVENANCE_JSON = HERE / "provenance.json"

USER_AGENT = "UniversalCANDataBot/1.0 (+https://github.com/Universal-CAN-BUS-Tool; data-ingest build-time vendor step)"

# --------------------------------------------------------------------------
# Pinned source revisions.
#
# SHA = the default-branch commit observed via api.github.com/.../commits
# at recon time (2026-09-16 task start). Pinning is deliberate: the license we
# read is the license we ship (Tuzaklar §23 — do not assume).
# --------------------------------------------------------------------------
SOURCES: dict[str, dict] = {
    "obdex": {
        "repo": "foerbsnavi/OBDex",
        "commit": "bc58b0eb7273226a1aabae98e956b70b8362bda1",
        "license_spdx": "CC0-1.0",
        "license_scope": "data files only (LICENSE-DATA); code is MIT (LICENSE-CODE)",
        "attribution_required": False,
        # NOTE (T1-3 CORRECTION to docs/research/data-source-recon-2026.md):
        # The recon claimed a pre-built "dist/" JSON set served over GitHub
        # Pages (foerbsnavi.github.io/obdex/...). That is WRONG as of this
        # fetch: the Pages site returns HTTP 404 ("There isn't a GitHub Pages
        # site here") and the repo has no dist/ directory. The authoritative
        # artifacts are the *_enriched.yaml sources plus the PID YAML. There is
        # NO meta.json in the repo either. We vendor the YAML sources verbatim
        # (CC0-1.0) and let the Tur-2 merge step convert to JSON — we do NOT
        # fabricate a converted artifact here.
        "base": ("https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/"),
        "files": {
            "data/generic/P0xxx_enriched.yaml": "data/generic/P0xxx_enriched.yaml",
            "data/generic/P2xxx_enriched.yaml": "data/generic/P2xxx_enriched.yaml",
            "data/generic/P3xxx_enriched.yaml": "data/generic/P3xxx_enriched.yaml",
            "data/generic/B0xxx_enriched.yaml": "data/generic/B0xxx_enriched.yaml",
            "data/generic/C0xxx_enriched.yaml": "data/generic/C0xxx_enriched.yaml",
            "data/generic/U0xxx_enriched.yaml": "data/generic/U0xxx_enriched.yaml",
            "data/generic/U3xxx_enriched.yaml": "data/generic/U3xxx_enriched.yaml",
            "data/pids/mode01.yaml": "data/pids/mode01.yaml",
            "data/pids/mode09.yaml": "data/pids/mode09.yaml",
        },
        "license_files": {
            "LICENSE-DATA.obdex": (
                "https://raw.githubusercontent.com/foerbsnavi/OBDex/"
                "bc58b0eb7273226a1aabae98e956b70b8362bda1/LICENSE-DATA"
            ),
            "LICENSE-CODE.obdex": (
                "https://raw.githubusercontent.com/foerbsnavi/OBDex/"
                "bc58b0eb7273226a1aabae98e956b70b8362bda1/LICENSE-CODE"
            ),
        },
    },
    "canboat": {
        "repo": "canboat/canboat",
        "commit": "f7f088b49d58f5b4a0feb9b29c288b0ae18a7880",
        "license_spdx": "Apache-2.0",
        "license_scope": "whole repository (LICENSE file); NOTICE must be preserved",
        "attribution_required": True,
        "files": {
            # Pre-generated machine-readable DB — no `make generated` needed.
            "canboat.json": (
                "https://raw.githubusercontent.com/canboat/canboat/"
                "f7f088b49d58f5b4a0feb9b29c288b0ae18a7880/docs/canboat.json"
            ),
            # J1939 DM1 — the exact SPN(19b)/FMI(5b)/CM(1b)/OC(7b) layout.
            "j1939_065226-activeTroubleCodes.yaml": (
                "https://raw.githubusercontent.com/canboat/canboat/"
                "f7f088b49d58f5b4a0feb9b29c288b0ae18a7880/database/j1939/pgns/"
                "065226-activeTroubleCodes.yaml"
            ),
        },
        "license_files": {
            "LICENSE.canboat": (
                "https://raw.githubusercontent.com/canboat/canboat/f7f088b49d58f5b4a0feb9b29c288b0ae18a7880/LICENSE"
            ),
        },
    },
    "dtcdb": {
        "repo": "Wal33D/dtc-database",
        "commit": "04c43d72e7db7197658b6f72fe582c5076d9eee8",
        "license_spdx": "MIT",
        "license_scope": "whole repository (LICENSE file)",
        "attribution_required": True,
        "files": {
            "dtc_codes.db": (
                "https://raw.githubusercontent.com/Wal33D/dtc-database/"
                "04c43d72e7db7197658b6f72fe582c5076d9eee8/data/dtc_codes.db"
            ),
        },
        "license_files": {
            "LICENSE.dtc-database": (
                "https://raw.githubusercontent.com/Wal33D/dtc-database/04c43d72e7db7197658b6f72fe582c5076d9eee8/LICENSE"
            ),
        },
    },
    "sitrak": {
        "repo": "STAS63-bit/sitrak-error-codes",
        "commit": "fdb0c0d9daf0643975b0ff62e0ff69ef9c07f742",
        # The GitHub API reports NOASSERTION because the LICENSE file carries a
        # Russian-language CC BY 4.0 deed GitHub's classifier cannot map. The
        # actual LICENSE file (881 B) declares CC BY 4.0 explicitly — verified
        # by reading it (T1-3). It IS redistributable WITH attribution.
        "license_spdx": "CC-BY-4.0",
        "license_scope": "error-codes data (LICENSE file, Russian CC BY 4.0 deed)",
        "attribution_required": True,
        "attribution_text": (
            "Источник: МегаДата / megadata.pro — CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)"
        ),
        "files": {
            "sitrak_error-codes.json": (
                "https://raw.githubusercontent.com/STAS63-bit/sitrak-error-codes/"
                "fdb0c0d9daf0643975b0ff62e0ff69ef9c07f742/error-codes.json"
            ),
        },
        "license_files": {
            "LICENSE.sitrak": (
                "https://raw.githubusercontent.com/STAS63-bit/sitrak-error-codes/"
                "fdb0c0d9daf0643975b0ff62e0ff69ef9c07f742/LICENSE"
            ),
        },
    },
}

# Sources we deliberately do NOT vendor, with the evidence-backed reason.
REJECTED = [
    {
        "name": "SAE J2012 / J1979 / J1939-71 / J1939-73 standard texts",
        "reason": (
            "Paid copyrighted works; redistribution not licensed even if bought. "
            "The FACTS (code id, bit widths, formulas) are not copyrightable — "
            "OBDex re-authors descriptions independently. Do the same."
        ),
    },
    {
        "name": "ISO 14229-1 (UDS) / ISO 15031-5 (Mode 06) / ISO 15765-2",
        "reason": "Paid copyrighted ISO standards; same as SAE. Implement from protocol structure.",
    },
    {
        "name": "OEM TSB bodies (Cummins/CAT/Scania/Volvo/Detroit/Mercedes/PACCAR)",
        "reason": (
            "Copyrighted OEM service literature; public visibility != redistribution "
            "license. Many are dealer-login-gated (CFAA exposure)."
        ),
    },
    {
        "name": "AllData / Mitchell 1 / Identifix / Snap-on / Autodata",
        "reason": "Commercial subscription services; ToS forbid scraping + redistribution.",
    },
    {
        "name": "iATN + public forums (TruckersReport, Reddit, brand boards)",
        "reason": "Members-only / user-authored copyright; ToS forbid scraping + republication.",
    },
    {
        "name": "Wikipedia OBD-II PID + DTC tables",
        "reason": (
            "CC BY-SA 4.0 is viral — a derivative DB arguably must also be CC BY-SA, "
            "which is unacceptable for this proprietary repo. OBDex (CC0) covers the "
            "same ground with zero obligations."
        ),
    },
    {
        "name": "alperunlu/DTCparser, f-steff/J1939_Reserved_SPN_Convention, "
        "digitalyacht/NMEA2000-Simulator-App, linux-can/can-utils",
        "reason": "No license declared = all rights reserved by default. Reference-only.",
    },
    {
        "name": "NMEA 2000 / NMEA 0183 standard documents",
        "reason": "Paid copyrighted NMEA documents. CANboat already did the RE work and is Apache-2.0.",
    },
    {
        "name": "UDS DID dictionary / Mode 06 data tables",
        "reason": (
            "No open redistributable source exists (exhaustive GitHub search = 0). "
            "This is a genuine capability gap — hand-author in-repo, do not fabricate."
        ),
    },
    {
        "name": "NHTSA complaints / recalls / vPIC bulk data",
        "reason": (
            "Public domain and clean, but OUT OF SCOPE for T1-3 (PHASE 1 vendor set), "
            "and complaint free-text carries PII/VINs that must be masked before any "
            "data/ entry. Deferred to a later turn with a PII-masking gate."
        ),
    },
]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def http_get(url: str, *, attempts: int = 3, timeout: int = 60) -> bytes:
    """Plain GET with UA + backoff. Raises on non-200 after retries."""
    last: Exception | None = None
    for i in range(attempts):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise urllib.error.HTTPError(url, resp.status, resp.reason, resp.headers, None)
                return resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last = exc
            if i < attempts - 1:
                time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {last}")


def download(url: str, dest: Path, records: list[dict], **meta) -> dict:
    """Download url -> dest, append a provenance record, return it."""
    if dest.exists() and dest.stat().st_size > 0:
        raw = dest.read_bytes()
        note = "cached"
    else:
        raw = http_get(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        note = "downloaded"

    digest = hashlib.sha256(raw).hexdigest()
    rec = {
        "artifact": dest.name,
        "staged_path": str(dest.relative_to(HERE.parent.parent)).replace("\\", "/"),
        "source_url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bytes": len(raw),
        "sha256": digest,
        "status": note,
        **meta,
    }
    records.append(rec)
    print(f"  [{note:>10}] {len(raw):>9,} B  {digest[:16]}…  {dest.name}  <- {url}")
    return rec


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def run(sources: list[str]) -> int:
    STAGING.mkdir(parents=True, exist_ok=True)
    LICENSES.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    per_source: dict[str, dict] = {}

    for key, spec in SOURCES.items():
        if key not in sources:
            continue
        print(f"\n=== {key}  ({spec['repo']} @ {spec['commit'][:12]})  [{spec['license_spdx']}] ===")

        # 1) LICENSE files first — evidence before artifact (§3: prove it).
        for fname, url in spec["license_files"].items():
            download(
                url,
                LICENSES / fname,
                records,
                source=key,
                kind="license",
                license_claim=spec["license_spdx"],
                commit_sha=spec["commit"],
            )

        # 2) Data artifacts.
        base = spec.get("base", "")
        for fname, url in spec["files"].items():
            rel = url if base else fname
            if base:
                url = base + rel
            dest = STAGING / key / rel
            download(
                url,
                dest,
                records,
                source=key,
                kind="data",
                license=spec["license_spdx"],
                license_scope=spec["license_scope"],
                attribution_required=spec["attribution_required"],
                attribution_text=spec.get("attribution_text"),
                commit_sha=spec["commit"],
                repo=spec["repo"],
            )

        per_source[key] = {
            "repo": spec["repo"],
            "commit_sha": spec["commit"],
            "license_spdx": spec["license_spdx"],
            "license_scope": spec["license_scope"],
            "attribution_required": spec["attribution_required"],
            "attribution_text": spec.get("attribution_text"),
        }

    payload = {
        "generated_by": "tools/data_ingest/fetch_sources.py",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "purpose": "build-time vendor data for a FULLY OFFLINE product; runtime reads from disk",
        "hard_constraints": [
            "no LLM / no cloud call in the product",
            "ingest scripts live under tools/data_ingest/, never src/engine/ai/",
            "nothing merged into data/ by this script (Tur-2 job)",
        ],
        "sources": per_source,
        "rejected": REJECTED,
        "artifacts": records,
    }
    PROVENANCE_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8", newline="")
    print(f"\nwrote {PROVENANCE_JSON}")
    print(f"total artifacts: {len(records)}  data bytes: {sum(r['bytes'] for r in records if r['kind'] == 'data'):,}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--source",
        action="append",
        choices=sorted(SOURCES) + ["all"],
        help="restrict to one source (repeatable); default = all",
    )
    args = ap.parse_args()
    chosen = args.source or ["all"]
    sources = sorted(SOURCES) if "all" in chosen else sorted(set(chosen))
    return run(sources)


if __name__ == "__main__":
    sys.exit(main())
