# -*- coding: utf-8 -*-
"""Upstream source discovery scan for the Copilot intake queue.

Answers one question, reproducibly: **which licence-clean upstream artefacts
exist, and which of them are already vendored in this repository?**

It downloads (build-time only, like ``tools/data_ingest/fetch_sources.py``) the
*same pinned commits* the repo already cites in ``data/PROVENANCE.md`` and
compares a per-file ``sha256`` inventory against the hashes recorded there.
Nothing under ``data/diagnostics`` or ``data/golden_traces`` is written: the
scan emits a report plus, on an explicit ``--apply``, new **intake** records
for artefacts that are not vendored yet.

Findings that motivated this tool (measured, 2026-10-02):

* OBDex @ ``bc58b0eb…`` — all 9 data files are already vendored and every hash
  matches ``data/PROVENANCE.md`` §2. Honest "no new data here" result, recorded
  rather than hidden.
* CANboat @ ``f7f088b4…`` — 11 artefacts vendored (including the 2.6 MB
  ``docs/canboat.json``), every hash re-verified, **0 mismatch**; while the
  pinned commit ships **85** J1939 PGN layout files (65,882 bytes) of which
  **84** are unknown to the repository. ``canboat_pgn_reference.json``
  documents the cause: "canboat.json J1939 ICERMEZ … J1939 DM1 ayri
  artefakttir".

Those 84 layouts are staged as ``pgn_layout`` intake records (Apache-2.0,
NOTICE preserved, per-record ``sha256``). Staging is not merging: nothing is
written to the knowledge base.

Usage::

    python scripts/intake_scan_sources.py --report docs/audit/source_scan.md
    python scripts/intake_scan_sources.py --json /tmp/scan.json
    python scripts/intake_scan_sources.py --stage                 # verify staged
    python scripts/intake_scan_sources.py --stage --apply         # write intake
    python scripts/intake_scan_sources.py --offline --tarballs-dir /tmp/tb

The YAML reader below is a deliberately restricted parser for canboat's
machine-generated ``database/j1939/pgns/*.yaml`` subset (block mappings, block
sequences, plain and block scalars). It replaces PyYAML so the scan yields
byte-identical records on every machine, and it **fails closed**: a line it
cannot account for raises instead of being skipped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INTAKE = ROOT / "data" / "intake"
PGN_SUBDIR = "pgn"

CANBOAT_REPO = "https://github.com/canboat/canboat"
WAL33D_REPO = "https://github.com/Wal33D/dtc-database"
SITRAK_REPO = "https://github.com/STAS63-bit/sitrak-error-codes"
OBDEX_REPO = "https://github.com/foerbsnavi/OBDex"
CANBOAT_COMMIT = "f7f088b49d58f5b4a0feb9b29c288b0ae18a7880"
OBDEX_COMMIT = "bc58b0eb7273226a1aabae98e956b70b8362bda1"
WAL33D_COMMIT = "04c43d72e7db7197658b6f72fe582c5076d9eee8"
SITRAK_COMMIT = "fdb0c0d9daf0643975b0ff62e0ff69ef9c07f742"

# sha256 values recorded in data/PROVENANCE.md §2 (the ingest evidence).
VENDORED_SHA256: dict[str, str] = {
    "obdex/data/generic/B0xxx_enriched.yaml": "73d317fb49b01a369ee29126bb0bb4f31c4775a5fe0a7e7fae1456aa980d3bc4",
    "obdex/data/generic/C0xxx_enriched.yaml": "1d60a394ff9cfde96b6e7a738420ac05dd48b85ebfe798001c38310aba7073e6",
    "obdex/data/generic/P0xxx_enriched.yaml": "a765cade770ffe756a5d4ea91c61fc128d5508f06f38552cd803dd726fecef63",
    "obdex/data/generic/P2xxx_enriched.yaml": "eda26317419f7e897c13eb254f74a01d905f180c7006174298d08d665b42dc4a",
    "obdex/data/generic/P3xxx_enriched.yaml": "bba19fe7dddb757866632ae939e4ace7dbbbe639816d3a46ce3a6936053708a3",
    "obdex/data/generic/U0xxx_enriched.yaml": "60aebde267b4bae53902e0abfeb7b489e314cd9e2ecdf0eb62384ea6b5a56afc",
    "obdex/data/generic/U3xxx_enriched.yaml": "f8a4b3723140b059216bad1d5fd450ce067c9c6bb8361f4120bbd422ecc769b7",
    "obdex/data/pids/mode01.yaml": "cc4c435fe5ce8af2ff5b9b084dd9a96c32c8257d2ac8116e819281cb06fa367a",
    "obdex/data/pids/mode09.yaml": "aaf0aa7041f8f1f81cfc0a7dfd1c3dab62dda68ca43c64d46357a44a04726233",
    "canboat/canboat.json": "b5a2c0c84b59af33caef583a372f9e763deb54ef4caf187825eff33d068735ba",
    "canboat/j1939_065226-activeTroubleCodes.yaml":
        "2c821042983bd5c751794dba388f1a8121c57ecacd5d0ab5a9ec95b03ec197c9",
    "wal33d/data/dtc_codes.db": "099a4ffd60398112a0540b0bbc93a5929e05e7f4e6d4988ca2a50858af01b743",
    "sitrak/error-codes.json": "69d726d69d5612eb890de0aa2579beef2220a2f2b371b8cbcd560e75f12840d3",
}

OBDEX_TARBALL = f"https://codeload.github.com/foerbsnavi/OBDex/tar.gz/{OBDEX_COMMIT}"
WAL33D_TARBALL = f"https://codeload.github.com/Wal33D/dtc-database/tar.gz/{WAL33D_COMMIT}"
SITRAK_TARBALL = f"https://codeload.github.com/STAS63-bit/sitrak-error-codes/tar.gz/{SITRAK_COMMIT}"
CANBOAT_TARBALL = f"https://codeload.github.com/canboat/canboat/tar.gz/{CANBOAT_COMMIT}"
OBDEX_PREFIX = "data/"
WAL33D_PREFIX = "data/source-data/"
SITRAK_JSON = "error-codes.json"
WAL33D_DB = "data/dtc_codes.db"
CANBOAT_PREFIX = "database/j1939/pgns/"
CANBOAT_JSON = "docs/canboat.json"

SCAN_DATE = "2026-10-02"
STAGE_NOTE = (
    "Verbatim field layout from the pinned CANboat commit; staged for review, NOT merged into "
    "data/diagnostics/canboat_pgn_reference.json. SPN numbers are copied from the upstream "
    "'description' text only — nothing is inferred."
)


# --------------------------------------------------------------------------- #
# restricted YAML reader (canboat database/j1939/pgns subset)
# --------------------------------------------------------------------------- #
_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+\.\d+$")
SPN_IN_TEXT_RE = re.compile(r"SPN\s*(\d+)")


def _scalar(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return None
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    if _INT_RE.match(text):
        return int(text)
    if _FLOAT_RE.match(text):
        return float(text)
    return text


def _significant_lines(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped in {"---", "..."}:
            continue
        lines.append((len(raw) - len(raw.lstrip()), stripped))
    return lines


def _block_scalar(header: str, lines: list[tuple[int, str]], index: int, indent: int) -> tuple[str, int]:
    """Read a ``|``/``|-``/``>``/``>-`` block scalar starting after the header."""
    chomp = "clip"
    style = header[0]
    if len(header) > 1:
        if header[1] == "-":
            chomp = "strip"
        elif header[1] == "+":
            chomp = "keep"
    parts: list[str] = []
    while index < len(lines) and lines[index][0] > indent:
        parts.append(lines[index][1])
        index += 1
    text = ("\n" if style == "|" else " ").join(parts)
    if chomp != "strip":
        text += "\n"
    return text, index


def _parse_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    """Parse one block at ``indent``; returns (value, next_index)."""
    if index >= len(lines):
        return None, index
    if lines[index][1].startswith("- "):
        items: list[Any] = []
        while index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("- "):
            body = lines[index][1][2:].strip()
            if ":" in body:
                # A mapping item: its first key sits on the dash line.
                block = [(indent + 2, body)]
                index += 1
                while index < len(lines) and lines[index][0] > indent:
                    block.append(lines[index])
                    index += 1
                value, _ = _parse_block(block, 0, indent + 2)
                items.append(value)
            else:
                items.append(_scalar(body))
                index += 1
        return items, index

    mapping: dict[str, Any] = {}
    while index < len(lines) and lines[index][0] == indent:
        content = lines[index][1]
        if content.startswith("- "):
            break
        if ":" not in content:
            raise ValueError(f"unparseable line: {content!r}")
        key, _, value = content.partition(":")
        key, value = key.strip(), value.strip()
        if value in {"|", "|-", "|+", ">", ">-", ">+"}:
            text, index = _block_scalar(value, lines, index + 1, indent)
            mapping[key] = text
            continue
        if value:
            # A plain scalar may continue on more-indented following lines.
            index += 1
            if index < len(lines) and lines[index][0] > indent and not lines[index][1].startswith("- "):
                parts = [value]
                while index < len(lines) and lines[index][0] > indent and not lines[index][1].startswith("- "):
                    parts.append(lines[index][1])
                    index += 1
                mapping[key] = " ".join(parts)
            else:
                mapping[key] = _scalar(value)
            continue
        if index + 1 >= len(lines):
            mapping[key] = None
            index += 1
            continue
        nxt_indent, nxt_content = lines[index + 1]
        if nxt_indent > indent:
            child, index = _parse_block(lines, index + 1, nxt_indent)
            mapping[key] = child
        elif nxt_indent == indent and nxt_content.startswith("- "):
            child, index = _parse_block(lines, index + 1, indent)
            mapping[key] = child
        else:
            mapping[key] = None
            index += 1
    return mapping, index


def parse_pgn_yaml(text: str) -> dict[str, Any]:
    """Parse one canboat J1939 PGN YAML file (strict subset, fail-closed)."""
    lines = _significant_lines(text)
    if not lines:
        raise ValueError("empty document")
    doc, consumed = _parse_block(lines, 0, lines[0][0])
    if consumed != len(lines):
        leftover = lines[consumed]
        raise ValueError(f"unconsumed line at indent {leftover[0]}: {leftover[1]!r}")
    if not isinstance(doc, dict):
        raise ValueError("top level must be a mapping")
    return doc


# --------------------------------------------------------------------------- #
# upstream inventory
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Artifact:
    repo: str
    commit: str
    rel_path: str
    bytes: int
    sha256: str
    state: str  # vendored | hash_mismatch | not_vendored


@dataclass(slots=True)
class ScanResult:
    started_at: str = SCAN_DATE
    sources: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0

    def metrics(self) -> dict[str, Any]:
        by_state: dict[str, int] = {}
        for art in self.artifacts:
            by_state[art["state"]] = by_state.get(art["state"], 0) + 1
        unvendored = [a for a in self.artifacts if a["state"] == "not_vendored"]
        return {
            "artifacts_scanned": len(self.artifacts),
            "by_state": by_state,
            "unvendored_files": len(unvendored),
            "unvendored_bytes": sum(a["bytes"] for a in unvendored),
            "j1939_pgn_layouts_unvendored": sum(
                1 for a in unvendored if a["rel_path"].startswith(CANBOAT_PREFIX)),
            "wal33d_per_make_lists_unvendored": sum(
                1 for a in unvendored if a["rel_path"].startswith(WAL33D_PREFIX)),
            "hash_mismatch": by_state.get("hash_mismatch", 0),
            "seconds": round(self.seconds, 2),
        }


def fetch_tarball(url: str, dest: Path) -> Path:
    """Download a pinned commit tarball. Network access lives here and nowhere else."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=180) as response:  # noqa: S310 - literal https codeload URL
        dest.write_bytes(response.read())
    return dest


def extract(tarball: Path, into: Path) -> Path:
    """Extract regular files only (path traversal is rejected explicitly)."""
    into.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:gz") as tf:
        for member in sorted(tf.getmembers(), key=lambda m: m.name):
            parts = Path(member.name).parts
            if not member.isfile() or member.name.startswith("/") or ".." in parts:
                continue
            dest = into / member.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            handle = tf.extractfile(member)
            if handle is None:
                continue
            with handle, dest.open("wb") as out:
                shutil.copyfileobj(handle, out)
    roots = [p for p in sorted(into.iterdir()) if p.is_dir()]
    if len(roots) != 1:
        raise ValueError(f"expected one top-level directory in {tarball.name}, got {roots}")
    return roots[0]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def aggregate_sha256(paths: list[Path]) -> str:
    """Order-stable digest over a file set (sha256 over concatenated per-file digests)."""
    agg = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.name):
        agg.update(hashlib.sha256(path.read_bytes()).digest())
    return agg.hexdigest()


def _vendored_key(repo_key: str, rel_path: str) -> str | None:
    """Map an upstream path onto the keys used in data/PROVENANCE.md §2."""
    if repo_key == "obdex":
        candidate = f"obdex/{rel_path}"
        return candidate if candidate in VENDORED_SHA256 else None
    if repo_key == "sitrak":
        return "sitrak/error-codes.json" if rel_path == SITRAK_JSON else None
    if repo_key == "wal33d":
        if rel_path == WAL33D_DB:
            return "wal33d/data/dtc_codes.db"
        return None
    if rel_path == CANBOAT_JSON:
        return "canboat/canboat.json"
    if rel_path.startswith(CANBOAT_PREFIX):
        candidate = "canboat/j1939_" + Path(rel_path).name
        return candidate if candidate in VENDORED_SHA256 else None
    return None


def _scan_one(repo_key: str, commit: str, path: Path, tree: Path) -> Artifact:
    rel = path.relative_to(tree).as_posix()
    digest = sha256_file(path)
    key = _vendored_key(repo_key, rel)
    if key is None:
        state = "not_vendored"
    elif VENDORED_SHA256[key] == digest:
        state = "vendored"
    else:
        state = "hash_mismatch"
    return Artifact(repo_key, commit, rel, path.stat().st_size, digest, state)


def scan_repo_tree(repo_key: str, commit: str, tree: Path, prefixes: tuple[str, ...]) -> list[Artifact]:
    """Inventory ``prefixes`` (files and/or directories) of one pinned upstream tree."""
    artifacts: list[Artifact] = []
    for prefix in prefixes:
        base = tree / prefix
        if base.is_file():
            artifacts.append(_scan_one(repo_key, commit, base, tree))
            continue
        if not base.is_dir():
            raise ValueError(f"{repo_key}: {prefix} not found in the pinned tree")
        for path in sorted(base.rglob("*")):
            if path.is_file():
                artifacts.append(_scan_one(repo_key, commit, path, tree))
    return artifacts


def run_scan(workdir: Path, tarballs_dir: Path | None = None,
             offline: bool = False) -> tuple[ScanResult, dict[str, Path]]:
    t0 = time.perf_counter()
    obdex_tb = (tarballs_dir / "obdex.tar.gz") if tarballs_dir else workdir / "obdex.tar.gz"
    canboat_tb = (tarballs_dir / "canboat.tar.gz") if tarballs_dir else workdir / "canboat.tar.gz"
    wal33d_tb = (tarballs_dir / "wal33d.tar.gz") if tarballs_dir else workdir / "wal33d.tar.gz"
    sitrak_tb = (tarballs_dir / "sitrak.tar.gz") if tarballs_dir else workdir / "sitrak.tar.gz"
    if not offline:
        fetch_tarball(OBDEX_TARBALL, obdex_tb)
        fetch_tarball(CANBOAT_TARBALL, canboat_tb)
        fetch_tarball(WAL33D_TARBALL, wal33d_tb)
        fetch_tarball(SITRAK_TARBALL, sitrak_tb)
    for tarball in (obdex_tb, canboat_tb, wal33d_tb, sitrak_tb):
        if not tarball.is_file():
            raise FileNotFoundError(
                f"{tarball} missing — run online or pass --tarballs-dir with both tarballs")
    obdex_tree = extract(obdex_tb, workdir / "obdex")
    canboat_tree = extract(canboat_tb, workdir / "canboat")
    wal33d_tree = extract(wal33d_tb, workdir / "wal33d")
    sitrak_tree = extract(sitrak_tb, workdir / "sitrak")

    result = ScanResult()
    result.sources = [
        {"repo": "foerbsnavi/OBDex", "commit": OBDEX_COMMIT, "url": OBDEX_TARBALL,
         "licence": "CC0-1.0", "attribution_required": False},
        {"repo": "canboat/canboat", "commit": CANBOAT_COMMIT, "url": CANBOAT_TARBALL,
         "licence": "Apache-2.0", "attribution_required": True,
         "notice": "Kees Verruijt, CANboat — data/licenses/NOTICE.canboat"},
        {"repo": "Wal33D/dtc-database", "commit": WAL33D_COMMIT, "url": WAL33D_TARBALL,
         "licence": "MIT", "attribution_required": True,
         "notice": "Copyright (c) Wal33D — data/licenses/ATTRIBUTION.obdex-and-dtcdb.md"},
        {"repo": "STAS63-bit/sitrak-error-codes", "commit": SITRAK_COMMIT, "url": SITRAK_TARBALL,
         "licence": "CC-BY-4.0", "attribution_required": True,
         "notice": "МегаДата / megadata.pro — data/licenses/ATTRIBUTION.sitrak.md"},
    ]
    artifacts = scan_repo_tree("obdex", OBDEX_COMMIT, obdex_tree, (OBDEX_PREFIX,))
    # docs/canboat.json is a single 2.6 MB file: hashed, not parsed.
    artifacts += scan_repo_tree("canboat", CANBOAT_COMMIT, canboat_tree, (CANBOAT_PREFIX, CANBOAT_JSON))
    # The compiled DB is hashed (3.2 MB); the 37 per-manufacturer source lists
    # under data/source-data/ are the *primary* per-make evidence the DB collapsed.
    artifacts += scan_repo_tree("wal33d", WAL33D_COMMIT, wal33d_tree, (WAL33D_DB, WAL33D_PREFIX))
    # SITRAK ships one JSON catalogue; scanned to prove there is no leftover.
    artifacts += scan_repo_tree("sitrak", SITRAK_COMMIT, sitrak_tree, (SITRAK_JSON,))
    result.artifacts = [asdict(a) for a in artifacts]
    result.seconds = time.perf_counter() - t0
    return result, {"obdex": obdex_tree, "canboat": canboat_tree,
                    "wal33d": wal33d_tree, "sitrak": sitrak_tree}


# --------------------------------------------------------------------------- #
# staging: canboat J1939 PGN layouts -> intake records
# --------------------------------------------------------------------------- #
def _iter_field_rows(fields: Any) -> list[dict[str, Any]]:
    """Flatten one canboat ``fields:`` list (recursing into ``repeat:`` blocks)."""
    rows: list[dict[str, Any]] = []
    if not isinstance(fields, list):
        return rows
    for item in fields:
        if not isinstance(item, dict):
            continue
        if "repeat" in item:
            repeat = item["repeat"]
            nested = repeat.get("fields") if isinstance(repeat, dict) else None
            rows.extend(_iter_field_rows(nested))
            continue
        name = item.get("name")
        if not isinstance(name, str):
            continue
        description = item.get("description") if isinstance(item.get("description"), str) else None
        match = SPN_IN_TEXT_RE.search(f"{name} {description or ''}")
        rows.append({
            "field_id": item.get("id") if isinstance(item.get("id"), str) else None,
            "name": name,
            "spn": int(match.group(1)) if match else None,
            "bits": item.get("bits") if isinstance(item.get("bits"), int) else None,
            "unit": item.get("unit") if isinstance(item.get("unit"), str) else None,
            "resolution": item.get("resolution") if isinstance(item.get("resolution"), (int, float)) else None,
            "description": description,
        })
    return rows


def build_pgn_record(tree: Path, path: Path) -> dict[str, Any]:
    """Build one ``pgn_layout`` intake record from a canboat J1939 PGN file."""
    doc = parse_pgn_yaml(path.read_text(encoding="utf-8"))
    pgn = doc.get("pgn")
    if not isinstance(pgn, int):
        raise ValueError(f"{path.name}: missing integer 'pgn'")
    ident = doc.get("id") if isinstance(doc.get("id"), str) else path.stem
    known = {"pgn", "id", "description", "type", "priority", "interval", "fields"}
    return {
        "schema_version": 1,
        "intake_id": f"canboat-pgn-{pgn:05d}-{ident.lower()}",
        "record_type": "pgn_layout",
        "submitted_at": SCAN_DATE,
        "submitter": {"type": "automated", "id": "scripts/intake_scan_sources.py", "role": "author"},
        "source": {
            "title": f"CANboat J1939 PGN {pgn} ({ident}) field layout",
            "path": f"{CANBOAT_REPO}/blob/{CANBOAT_COMMIT}/{path.relative_to(tree).as_posix()}",
            "type": "standard",
            "publisher": "CANboat (Kees Verruijt); layout per SAE J1939-71",
            "revision": CANBOAT_COMMIT[:12],
            "licence": "Apache-2.0",
            "access_date": SCAN_DATE,
            "snapshot": {
                "archive_url": None,
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "pages_cited": [],
            },
        },
        "confidence": "single_source",
        "draft": True,
        "knowledge_base": None,
        "payload": {
            "pgn": pgn,
            "pgn_id": ident,
            "description": doc.get("description") if isinstance(doc.get("description"), str) else None,
            "pgn_type": doc.get("type") if isinstance(doc.get("type"), str) else None,
            "priority": doc.get("priority") if isinstance(doc.get("priority"), int) else None,
            "interval_ms": doc.get("interval") if isinstance(doc.get("interval"), int) else None,
            "fields": _iter_field_rows(doc.get("fields")),
            "upstream_keys": sorted(k for k in doc if k not in known),
            "source_file": path.relative_to(tree).as_posix(),
        },
        "notes": STAGE_NOTE,
    }


def stage(canboat_tree: Path, intake_dir: Path, apply: bool) -> tuple[int, list[str], list[str]]:
    """Write (or verify) one intake record per **un-vendored** J1939 PGN layout.

    Files ``data/PROVENANCE.md`` already records (the DM1 layout,
    065226-activeTroubleCodes.yaml) are skipped on purpose: staging a copy of
    something the repository already ships would be noise, not discovery.
    """
    src = canboat_tree / "database" / "j1939" / "pgns"
    target = intake_dir / PGN_SUBDIR
    if apply:
        target.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    problems: list[str] = []
    skipped: list[str] = []
    for path in sorted(src.glob("*.yaml")):
        if _vendored_key("canboat", path.relative_to(canboat_tree).as_posix()) is not None:
            skipped.append(path.name)
            continue
        record = build_pgn_record(canboat_tree, path)
        rel = f"{PGN_SUBDIR}/{record['intake_id']}.json"
        out = intake_dir / rel
        payload = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        if out.exists():
            if out.read_text(encoding="utf-8") != payload:
                problems.append(f"{rel}: staged record differs from the pinned source (re-stage or investigate)")
            continue
        if apply:
            out.write_text(payload, encoding="utf-8")
        else:
            problems.append(f"{rel}: missing (run with --apply)")
        written.append(rel)
    return len(written), problems, skipped


# --------------------------------------------------------------------------- #
# staging: Wal33D per-manufacturer source lists -> OEM divergence records
# --------------------------------------------------------------------------- #
DTC_LINE_RE = re.compile(r"^([PBCU][0-9A-Fa-f]{4})\s*-\s*(\S.*?)\s*$")
OEM_SUBDIR = "oem"
DIVERGENCE_NOTE = (
    "Staged from the pinned Wal33D/dtc-database source list. The per-manufacturer "
    "description is upstream evidence; kb_description_en is what the merged OEM layer "
    "currently stores for the same code. Nothing here is written to data/diagnostics/."
)


def parse_oem_listing(text: str) -> list[tuple[str, str]]:
    """Parse a ``CODE - Description`` source list; unparsable lines are dropped."""
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = DTC_LINE_RE.match(stripped)
        if match:
            rows.append((match.group(1).upper(), match.group(2)))
    return rows


def _oem_layer_codes(repo_root: Path) -> dict[str, str]:
    """``{code: stored description}`` from the merged OEM layer (read-only)."""
    layer = repo_root / "data" / "diagnostics" / "dtc_database_oem_layer.json"
    if not layer.is_file():
        return {}
    blob = json.loads(layer.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for code, record in (blob.get("codes") or {}).items():
        description = ((record or {}).get("description") or {})
        out[code] = str(description.get("en") or "").strip()
    return out


def build_oem_divergence_records(wal33d_tree: Path, repo_root: Path) -> list[dict[str, Any]]:
    """One record per manufacturer list, carrying only rows the KB disagrees with.

    The merged OEM layer stores **one** description per code (last write wins), so
    the per-manufacturer wording that upstream actually publishes is lost. These
    records restore that evidence for review; they are not a merge.
    """
    stored = _oem_layer_codes(repo_root)
    source_dir = wal33d_tree / WAL33D_PREFIX
    records: list[dict[str, Any]] = []
    for path in sorted(source_dir.glob("*_codes.txt")):
        make = path.stem.replace("_codes", "").upper()
        rows = parse_oem_listing(path.read_text(encoding="utf-8", errors="replace"))
        divergences: list[dict[str, Any]] = []
        for code, description in rows:
            current = stored.get(code)
            if current is None:
                divergences.append({"code": code, "source_description_en": description,
                                    "kb_description_en": None})
            elif current.strip().lower() != description.strip().lower():
                divergences.append({"code": code, "source_description_en": description,
                                    "kb_description_en": current})
        rel_source = path.relative_to(wal33d_tree).as_posix()
        records.append({
            "schema_version": 1,
            "intake_id": f"wal33d-divergence-{make.lower()}",
            "record_type": "oem_divergence",
            "submitted_at": SCAN_DATE,
            "submitter": {"type": "automated", "id": "scripts/intake_scan_sources.py", "role": "author"},
            "source": {
                "title": f"Wal33D dtc-database source list for {make} ({path.name})",
                "path": f"{WAL33D_REPO}/blob/{WAL33D_COMMIT}/{rel_source}",
                "type": "web",
                "publisher": "Wal33D (dtc-database)",
                "revision": WAL33D_COMMIT[:12],
                "licence": "MIT",
                "access_date": SCAN_DATE,
                "snapshot": {
                    "archive_url": None,
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                    "pages_cited": [],
                },
            },
            "confidence": "single_source",
            "draft": True,
            "knowledge_base": None,
            "payload": {
                "make": make,
                "source_file": rel_source,
                "source_rows": len(rows),
                "divergence_count": len(divergences),
                "divergences": divergences,
            },
            "notes": DIVERGENCE_NOTE,
        })
    return records


def stage_oem(wal33d_tree: Path, intake_dir: Path, repo_root: Path, apply: bool,
              ) -> tuple[int, list[str]]:
    """Write (or verify) one ``oem_divergence`` record per manufacturer list."""
    target = intake_dir / OEM_SUBDIR
    if apply:
        target.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    problems: list[str] = []
    for record in build_oem_divergence_records(wal33d_tree, repo_root):
        rel = f"{OEM_SUBDIR}/{record['intake_id']}.json"
        out = intake_dir / rel
        payload = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        if out.exists():
            if out.read_text(encoding="utf-8") != payload:
                problems.append(f"{rel}: staged record differs from the pinned source")
            continue
        if apply:
            out.write_text(payload, encoding="utf-8")
        else:
            problems.append(f"{rel}: missing (run with --apply)")
        written.append(rel)
    return len(written), problems


# --------------------------------------------------------------------------- #
# staging: SPN references harvested from the canboat J1939 layouts
# --------------------------------------------------------------------------- #
SPN_REF_SUBDIR = "spn_ref"
SPN_REF_NOTE = (
    "SPN referansi ve parametre metni IKI ayri upstream temsilinden birebir "
    "toplandi: canboat'in pinli J1939 PGN alan duzenleri (YAML) ve repoda vendor "
    "edilmis data/dbc/heavy_duty/j1939_canboat.dbc (CM_ SG_ yorumlari, sha256 "
    "data/dbc/manifest.json ile sabitli). Iki temsil ayni cumleyi soyleyen iki "
    "bicimdir; hicbir deger TURETILMEMISTIR. "
    "data/diagnostics/j1939_spn_fmi_database.json bu kayitlari OKUMAZ - terfi "
    "karari intake README'sindeki Adim 3a'ya bagli."
)


def collect_spn_evidence(canboat_tree: Path) -> dict[int, dict[str, Any]]:
    """Aggregate every SPN mentioned in the canboat J1939 PGN layouts.

    Only text that literally mentions ``SPN <n>`` counts: the number is never
    derived from the field name, the PGN or the bit layout.
    """
    evidence: dict[int, dict[str, Any]] = {}
    src = canboat_tree / "database" / "j1939" / "pgns"
    for path in sorted(src.glob("*.yaml")):
        doc = parse_pgn_yaml(path.read_text(encoding="utf-8"))
        pgn = doc.get("pgn")
        for row in _iter_field_rows(doc.get("fields")):
            spn = row.get("spn")
            if not isinstance(spn, int):
                continue
            entry = evidence.setdefault(spn, {
                "names": [], "units": [], "resolutions": [], "bit_lengths": [],
                "evidence": [], "files": [],
            })
            if row["name"] not in entry["names"]:
                entry["names"].append(row["name"])
            if row.get("unit") and row["unit"] not in entry["units"]:
                entry["units"].append(row["unit"])
            if row.get("resolution") is not None and row["resolution"] not in entry["resolutions"]:
                entry["resolutions"].append(row["resolution"])
            if row.get("bits") and row["bits"] not in entry["bit_lengths"]:
                entry["bit_lengths"].append(row["bits"])
            if row.get("description") and row["description"] not in entry["evidence"]:
                entry["evidence"].append(row["description"])
            rel = path.relative_to(canboat_tree).as_posix()
            if rel not in entry["files"]:
                entry["files"].append(rel)
            if isinstance(pgn, int):
                entry.setdefault("pgns", [])
                if pgn not in entry["pgns"]:
                    entry["pgns"].append(pgn)
    return evidence


def _kb_spn_db(repo_root: Path) -> dict[str, Any]:
    """The live J1939 SPN database (read-only: intake never writes the KB)."""
    path = repo_root / "data" / "diagnostics" / "j1939_spn_fmi_database.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("spns", {})


DBC_SPN_RE = re.compile(r'CM_\s+SG_\s+(\d+)\s+(\S+)\s+"(SPN\s+(\d+)[^"]*)"')


def _pgn_of_can_id(can_id: int) -> int:
    """J1939/NMEA-2000 PGN from a DBC message id (PDU1 destination byte excluded)."""
    if can_id <= 0x7FF:
        return can_id
    pgn = (can_id >> 8) & 0x3FFFF
    if ((can_id >> 16) & 0xFF) < 240:  # PDU1
        pgn &= 0x3FF00
    return pgn


def collect_dbc_spn_evidence(dbc_path: Path) -> dict[int, dict[str, Any]]:
    """SPN references carried by a vendored DBC's ``CM_ SG_`` comments.

    canboat's DBC comments look like ``"SPN 190; canboat type: NUMBER"``: the
    number is read verbatim and the signal name is kept verbatim, so this is a
    second *independent representation* of the same upstream statement (the YAML
    layouts say it in prose). Two representations agreeing is what lets a staged
    SPN reference claim ``corroborated`` instead of ``single_source``.
    """
    evidence: dict[int, dict[str, Any]] = {}
    text = dbc_path.read_text(encoding="utf-8", errors="replace")
    for match in DBC_SPN_RE.finditer(text):
        can_id, signal, comment, spn = match.group(1), match.group(2), match.group(3), int(match.group(4))
        entry = evidence.setdefault(spn, {"signals": [], "comments": [], "pgns": []})
        if signal not in entry["signals"]:
            entry["signals"].append(signal)
        if comment not in entry["comments"]:
            entry["comments"].append(comment)
        pgn = _pgn_of_can_id(int(can_id))
        if pgn not in entry["pgns"]:
            entry["pgns"].append(pgn)
    return evidence


def dbc_file_for_spns(repo_root: Path) -> Path | None:
    """The vendored canboat heavy-duty DBC, verified against data/dbc/manifest.json."""
    manifest_path = repo_root / "data" / "dbc" / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    entries: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("filename"), str):
                entries.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(manifest)
    for entry in entries:
        if entry["filename"] != "j1939_canboat.dbc":
            continue
        candidate = repo_root / "data" / "dbc" / "heavy_duty" / entry["filename"]
        if candidate.is_file() and sha256_file(candidate) == entry.get("sha256"):
            return candidate
    return None


def build_spn_reference_records(canboat_tree: Path, repo_root: Path) -> list[dict[str, Any]]:
    """One record per SPN seen in the layouts, with the KB's current state."""
    evidence = collect_spn_evidence(canboat_tree)
    spns = _kb_spn_db(repo_root)
    files_hash: dict[str, tuple[str, int]] = {}
    src = canboat_tree / "database" / "j1939" / "pgns"
    # Second, independent in-repo representation of the same upstream statement.
    dbc_path = dbc_file_for_spns(repo_root)
    dbc_evidence = collect_dbc_spn_evidence(dbc_path) if dbc_path else {}
    dbc_rel = "data/dbc/heavy_duty/j1939_canboat.dbc"
    dbc_hash = sha256_file(dbc_path) if dbc_path else None
    dbc_bytes = dbc_path.stat().st_size if dbc_path else None
    records: list[dict[str, Any]] = []
    for spn in sorted(set(evidence) | set(dbc_evidence)):
        entry = evidence.get(spn) or {"names": [], "units": [], "resolutions": [], "bit_lengths": [],
                                       "evidence": [], "files": [], "pgns": []}
        dbc = dbc_evidence.get(spn)
        if dbc and not entry["files"]:
            # DBC-only reference: real upstream text, staged from the vendored DBC.
            entry["names"] = list(dbc["signals"])
            entry["evidence"] = list(dbc["comments"])
        cited = []
        for rel in entry["files"]:
            if rel not in files_hash:
                files_hash[rel] = (sha256_file(src / Path(rel).name), (src / Path(rel).name).stat().st_size)
            cited.append({"source_file": rel, "sha256": files_hash[rel][0], "bytes": files_hash[rel][1]})
        if dbc and dbc_hash:
            cited.append({"source_file": dbc_rel, "sha256": dbc_hash, "bytes": dbc_bytes})
        current = spns.get(f"SPN_{spn}") or {}
        if entry["files"]:
            source_path = f"{CANBOAT_REPO}/blob/{CANBOAT_COMMIT}/{entry['files'][0]}"
            source_title = f"CANboat J1939 PGN alan düzenlerinden SPN {spn} referansı"
            source_publisher = "CANboat (Kees Verruijt); layout per SAE J1939-71"
            source_revision = CANBOAT_COMMIT[:12]
        else:
            source_path = dbc_rel
            source_title = f"Vendor DBC yorumundan SPN {spn} referansı"
            source_publisher = "CANboat (Kees Verruijt) via data/dbc/manifest.json"
            source_revision = None
        records.append({
            "schema_version": 1,
            "intake_id": f"canboat-spn-{spn:05d}",
            "record_type": "spn_reference",
            "submitted_at": SCAN_DATE,
            "submitter": {"type": "automated", "id": "scripts/intake_scan_sources.py", "role": "author"},
            "source": {
                "title": source_title,
                "path": source_path,
                "type": "standard",
                "publisher": source_publisher,
                "revision": source_revision,
                "licence": "Apache-2.0",
                "access_date": SCAN_DATE,
                "snapshot": {"archive_url": None, "sha256": None, "bytes": None, "pages_cited": []},
            },
            # Two independent representations of one statement -> cross-checked.
            "confidence": "corroborated" if len(cited) >= 2 else "single_source",
            "draft": True,
            "knowledge_base": None,
            "payload": {
                "spn": spn,
                "dbc_signals": (dbc or {}).get("signals", []),
                "names_en": entry["names"],
                "units": entry["units"],
                "resolutions": entry["resolutions"],
                "bit_lengths": entry["bit_lengths"],
                "evidence_pgns": sorted(set(entry.get("pgns", [])) | set((dbc or {}).get("pgns", []))),
                "evidence_text": entry["evidence"],
                "sources": cited,
                "kb_state": "present" if current else "absent",
                "kb_name": current.get("name"),
                "kb_unit": current.get("unit"),
            },
            "notes": SPN_REF_NOTE,
        })
    return records


def stage_spn_refs(canboat_tree: Path, intake_dir: Path, repo_root: Path, apply: bool,
                   ) -> tuple[int, list[str]]:
    target = intake_dir / SPN_REF_SUBDIR
    if apply:
        target.mkdir(parents=True, exist_ok=True)
    written = 0
    problems: list[str] = []
    for record in build_spn_reference_records(canboat_tree, repo_root):
        rel = f"{SPN_REF_SUBDIR}/{record['intake_id']}.json"
        out = intake_dir / rel
        payload = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        if out.exists():
            if out.read_text(encoding="utf-8") != payload:
                problems.append(f"{rel}: staged record differs from the pinned source")
            continue
        if apply:
            out.write_text(payload, encoding="utf-8")
        else:
            problems.append(f"{rel}: missing (run with --apply)")
        written += 1
    return written, problems

# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def render(result: ScanResult) -> str:
    metrics = result.metrics()
    lines = [
        "# Upstream kaynak taraması — intake keşfi",
        "",
        f"- Tarih: {result.started_at} · süre: {metrics['seconds']} s",
        f"- Tarama edilen artefakt: **{metrics['artifacts_scanned']}** · "
        f"vendor edilmemiş: **{metrics['unvendored_files']}** ({metrics['unvendored_bytes']} bayt) · "
        f"hash uyuşmazlığı: **{metrics['hash_mismatch']}**",
        "",
        "## Kaynaklar (pinli commit)",
        "",
        "| Repo | Commit | Lisans | Atıf |",
        "|---|---|---|---|",
    ]
    for src in result.sources:
        lines.append(f"| `{src['repo']}` | `{src['commit'][:12]}` | {src['licence']} | "
                     f"{'zorunlu' if src['attribution_required'] else 'yok'} |")
    lines += ["", "## Vendor edilmemiş artefaktlar", "", "| Repo | Yol | Bayt | sha256 (ilk 16) |",
              "|---|---|---|---|"]
    for art in result.artifacts:
        if art["state"] == "not_vendored":
            lines.append(f"| {art['repo']} | `{art['rel_path']}` | {art['bytes']} | `{art['sha256'][:16]}…` |")
    lines += ["", "## Doğrulanmış (hash birebir eşleşti)", "", "| Repo | Yol | Bayt | Durum |",
              "|---|---|---|---|"]
    for art in result.artifacts:
        if art["state"] != "not_vendored":
            lines.append(f"| {art['repo']} | `{art['rel_path']}` | {art['bytes']} | {art['state']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("--report", help="write the markdown scan report to this path")
    ap.add_argument("--json", dest="json_out", help="write the scan result as JSON")
    ap.add_argument("--tarballs-dir", help="reuse downloaded tarballs from this directory")
    ap.add_argument("--offline", action="store_true", help="never touch the network")
    ap.add_argument("--stage", action="store_true",
                    help="stage the un-vendored J1939 PGN layouts (verify without --apply)")
    ap.add_argument("--stage-spn", action="store_true",
                    help="stage SPN references harvested from the J1939 layouts (needs --apply)")
    ap.add_argument("--stage-oem", action="store_true",
                    help="stage Wal33D per-manufacturer description divergences (verify without --apply)")
    ap.add_argument("--apply", action="store_true", help="with --stage: write the records")
    args = ap.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="intake-scan-"))
    try:
        result, trees = run_scan(
            workdir,
            tarballs_dir=Path(args.tarballs_dir) if args.tarballs_dir else None,
            offline=args.offline,
        )
        metrics = result.metrics()
        print(f"[*] artefacts={metrics['artifacts_scanned']} "
              f"not_vendored={metrics['unvendored_files']} "
              f"j1939_pgn_layouts={metrics['j1939_pgn_layouts_unvendored']} "
              f"wal33d_lists={metrics['wal33d_per_make_lists_unvendored']} "
              f"hash_mismatch={metrics['hash_mismatch']}")
        if args.json_out:
            out = Path(args.json_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"metrics": metrics, "artifacts": result.artifacts,
                                       "sources": result.sources}, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
        if args.report:
            out = Path(args.report)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render(result), encoding="utf-8")

        if args.stage_spn:
            count, problems = stage_spn_refs(trees["canboat"], INTAKE, ROOT, apply=args.apply)
            for problem in problems:
                print(f"[!] {problem}")
            print(f"[*] spn_reference records: {count} ({'written' if args.apply else 'verify only'})")
            return 1 if problems else 0
        if args.stage_oem:
            count, problems = stage_oem(trees["wal33d"], INTAKE, ROOT, apply=args.apply)
            for problem in problems:
                print(f"[!] {problem}")
            print(f"[*] oem_divergence records: {count} ({'written' if args.apply else 'verify only'})")
            return 1 if problems else 0
        if args.stage:
            count, problems, skipped = stage(trees["canboat"], INTAKE, apply=args.apply)
            for problem in problems:
                print(f"[!] {problem}")
            if skipped:
                print(f"[*] already vendored, not staged: {', '.join(skipped)}")
            print(f"[*] pgn_layout records: {count} ({'written' if args.apply else 'verify only'})")
            return 1 if problems else 0
        return 1 if metrics["hash_mismatch"] else 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    raise SystemExit(main())
