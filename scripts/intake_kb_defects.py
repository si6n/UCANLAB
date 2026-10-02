# -*- coding: utf-8 -*-
"""Measured data-quality defects in the vendored knowledge base — intake register.

The intake queue is also where *findings about our own data* belong: a defect
that is only described in prose disappears, while a defect that is **measured
by a detector and staged with its result** can be re-checked on every run.

Each detector is a pure function over the vendored JSON files. It returns a
finding: a stable ``code``, a severity, the target file with its ``sha256``, the
exact detector expression, the measured ``affected_count`` and a few verbatim
examples. ``scripts/validate_intake.py`` re-runs every detector on each
execution and compares the result with the staged record:

* count unchanged → ``open`` (the defect is still there, by measurement)
* count zero      → ``closed`` (fix landed, the record can be archived)
* count differs   → ``drift`` (data or detector changed: investigate)
* detector with no staged record → reported as an **unstaged** finding

Nothing here writes to ``data/diagnostics`` or ``data/golden_traces``: this
tool only reads them and writes ``data/intake/defects/``.

Measurements (2026-10-02, ``j1939_spn_fmi_database.json``, 4.291 SPN):

===========================  ======  ==================================================
code                         count   what it means
===========================  ======  ==================================================
spn_name_is_fmi_sentence          23  ``name`` holds a whole "SPN n FMI m — …" diagnosis
spn_name_embeds_spn_fmi_token    171  ``name`` ends in a truncated "… Preliminary FMI"
spn_unit_placeholder            4.202  ``unit`` is "None"/"-"/""/"Standart J1939" (98 %)
spn_without_fault_matrix          64  SPN known, no FMI rows at all
dtc_missing_title_tr           13.971  English-only DTC titles (known gap, not a bug)
===========================  ======  ==================================================

Usage::

    python scripts/intake_kb_defects.py                  # measure and report
    python scripts/intake_kb_defects.py --json /tmp/kb_defects.json
    python scripts/intake_kb_defects.py --stage           # verify staged records
    python scripts/intake_kb_defects.py --stage --apply   # stage them
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
INTAKE = ROOT / "data" / "intake"
DEFECT_SUBDIR = "defects"

MEASURED_AT = "2026-10-02"
SPN_DB = Path("data") / "diagnostics" / "j1939_spn_fmi_database.json"
DTC_DB = Path("data") / "diagnostics" / "dtc_database.json"
ALIAS_DB = Path("data") / "diagnostics" / "signal_aliases.json"
MEASUREMENT_MAP = Path("data") / "diagnostics" / "signal_measurement_map.json"
# Evidence for the alias-gap detector: the staged SPN references, each carrying
# the upstream parameter name and the hash of the file it was read from.
SPN_REF_DIR = Path("data") / "intake" / "spn_ref"

# Expressions are part of the finding: a defect without its detector is not
# reproducible, and "the regex changed" must show up as drift.
FMI_SENTENCE_RE = re.compile(r"^SPN\s*\d+\s*FMI\s*\d+", re.IGNORECASE)
SPN_FMI_TOKEN_RE = re.compile(r"\b(?:SPN\s*\d+|FMI)\b", re.IGNORECASE)
PLACEHOLDER_UNITS = frozenset({"", "-", "none", "n/a", "na", "standart j1939", "standart", "j1939"})

# How many verbatim examples each finding carries (enough to act on, not a dump).
EXAMPLE_LIMIT = 5


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _target(relative: Path) -> dict[str, Any]:
    path = ROOT / relative
    return {"target_file": relative.as_posix(), "target_sha256": sha256_file(path),
            "target_bytes": path.stat().st_size}


# --------------------------------------------------------------------------- #
# detectors
# --------------------------------------------------------------------------- #
def detect_spn_name_is_fmi_sentence(root: Path = ROOT) -> dict[str, Any]:
    """``name`` contains a whole diagnosis sentence instead of a parameter name."""
    blob = _load(root / SPN_DB)
    hits = []
    for key, record in sorted(blob["spns"].items()):
        name = str((record or {}).get("name") or "")
        if FMI_SENTENCE_RE.search(name):
            hits.append({"key": key, "current_name": name,
                         "current_title_tr": (record or {}).get("title_tr")})
    return {
        "code": "spn_name_is_fmi_sentence",
        "severity": "high",
        "summary": "SPN kaydının name alanı parametre adı değil, 'SPN n FMI m — …' tanı cümlesi",
        "why_it_matters": ("Copilot 'SPN 112 nedir?' sorusuna bir arıza cümlesi yanıt verir; "
                           "parametre adı ile arıza metni aynı alanda karışmıştır."),
        "expression": FMI_SENTENCE_RE.pattern,
        **_target(SPN_DB),
        "affected_count": len(hits),
        "examples": hits[:EXAMPLE_LIMIT],
    }


def detect_spn_name_embeds_spn_fmi_token(root: Path = ROOT) -> dict[str, Any]:
    """``name`` embeds an SPN/FMI token — usually a truncated upstream phrase."""
    blob = _load(root / SPN_DB)
    hits = []
    for key, record in sorted(blob["spns"].items()):
        name = str((record or {}).get("name") or "")
        if SPN_FMI_TOKEN_RE.search(name) and not FMI_SENTENCE_RE.search(name):
            hits.append({"key": key, "current_name": name})
    return {
        "code": "spn_name_embeds_spn_fmi_token",
        "severity": "medium",
        "summary": "SPN name alanında 'SPN n' / 'FMI' belirteci var (kırpılmış upstream cümlesi)",
        "why_it_matters": ("name alanı bir etiket olmalı; kırpılmış cümleler arama ve "
                           "eşleştirme kalitesini düşürür."),
        "expression": f"{SPN_FMI_TOKEN_RE.pattern} and not {FMI_SENTENCE_RE.pattern}",
        **_target(SPN_DB),
        "affected_count": len(hits),
        "examples": hits[:EXAMPLE_LIMIT],
    }


def detect_spn_unit_placeholder(root: Path = ROOT) -> dict[str, Any]:
    """``unit`` holds a placeholder instead of a unit of measurement."""
    blob = _load(root / SPN_DB)
    hits = []
    total = len(blob["spns"])
    for key, record in sorted(blob["spns"].items()):
        unit = str((record or {}).get("unit") or "").strip()
        if unit.lower() in PLACEHOLDER_UNITS:
            hits.append({"key": key, "current_unit": unit,
                         "name": (record or {}).get("name")})
    return {
        "code": "spn_unit_placeholder",
        "severity": "medium",
        "summary": f"SPN unit alanı yer tutucu değer ({len(hits)}/{total}); gerçek birim kayıp",
        "why_it_matters": ("Birim olmadan sinyal aralığı/eşik karşılaştırması yapılamaz; "
                           "copilot 'kPa cinsinden' diye bir şey söyleyemez."),
        "expression": "unit.lower() in {'-', '', 'none', 'n/a', 'standart j1939', …}",
        **_target(SPN_DB),
        "affected_count": len(hits),
        "examples": hits[:EXAMPLE_LIMIT],
    }


def detect_spn_without_fault_matrix(root: Path = ROOT) -> dict[str, Any]:
    """SPN exists but has no FMI rows: the parameter cannot be diagnosed."""
    blob = _load(root / SPN_DB)
    hits = []
    for key, record in sorted(blob["spns"].items()):
        if not ((record or {}).get("fault_matrix") or {}):
            hits.append({"key": key, "name": (record or {}).get("name"),
                         "spn": (record or {}).get("spn")})
    return {
        "code": "spn_without_fault_matrix",
        "severity": "medium",
        "summary": "SPN kaydı var ama hiç FMI satırı yok — tanı kombinasyonu üretilemez",
        "why_it_matters": ("Copilot bu parametre için 'hangi FMI ile ne olur?' sorusuna "
                           "cevap üretemez; vaka ile eşleştirme de zayıflar."),
        "expression": "not record.get('fault_matrix')",
        **_target(SPN_DB),
        "affected_count": len(hits),
        "examples": hits[:EXAMPLE_LIMIT],
    }


def detect_dtc_missing_title_tr(root: Path = ROOT) -> dict[str, Any]:
    """DTC records without a Turkish title (known coverage gap, kept visible)."""
    blob = _load(root / DTC_DB)
    hits = []
    for code, record in sorted(blob.items()):
        if not str((record or {}).get("title_tr") or "").strip():
            hits.append({"key": code, "title": (record or {}).get("title")})
    total = len(blob)
    return {
        "code": "dtc_missing_title_tr",
        "severity": "low",
        "summary": f"DTC kayıtlarında Türkçe başlık yok ({len(hits)}/{total}) — bilinen kapsam eksiği",
        "why_it_matters": ("Türkçe arayüzde başlık İngilizce kalıyor; bilgi eksikliği değil "
                           "çeviri kapsamı eksiği, bu yüzden severity düşük ve kayıtta 'known'."),
        "expression": "not record.get('title_tr', '').strip()",
        "target_kind": "known_coverage_gap",
        **_target(DTC_DB),
        "affected_count": len(hits),
        "examples": hits[:EXAMPLE_LIMIT],
    }


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    return _NON_ALNUM.sub("", text.lower())


def _alias_vocabulary(root: Path) -> set[str]:
    """Every form the copilot can resolve: canonicals, aliases, source forms, phrases."""
    pool: set[str] = set()
    measurement = root / MEASUREMENT_MAP
    if measurement.is_file():
        for row in (_load(measurement).get("signals") or []):
            pool.add(_norm(str(row.get("canonical") or "")))
            for key in ("aliases", "phrases_tr", "phrases_en"):
                pool.update(_norm(str(v)) for v in row.get(key) or [])
    aliases = root / ALIAS_DB
    if aliases.is_file():
        for row in (_load(aliases).get("aliases") or []):
            pool.add(_norm(str(row.get("canonical") or "")))
            for key in ("source_forms", "node_side"):
                pool.update(_norm(str(v)) for v in row.get(key) or [])
    pool.discard("")
    return pool


def detect_spn_parameter_name_not_in_alias_map(root: Path = ROOT) -> dict[str, Any]:
    """J1939 parameter names the copilot cannot resolve to a canonical signal.

    Measured against the evidence staged in ``data/intake/spn_ref`` (upstream
    field names + their source hashes), against the two vocabulary files.
    """
    pool = _alias_vocabulary(root)
    hits: list[dict[str, Any]] = []
    seen = 0
    for path in sorted((root / SPN_REF_DIR).glob("*.json")):
        record = _load(path)
        payload = record.get("payload") or {}
        for name in payload.get("names_en") or []:
            seen += 1
            key = _norm(str(name))
            resolvable = key in pool or any(len(p) > 7 and (p in key or key in p) for p in pool)
            if not resolvable:
                hits.append({
                    "spn": payload.get("spn"),
                    "name": name,
                    "evidence_pgns": payload.get("evidence_pgns"),
                    "source": (payload.get("sources") or [{}])[0].get("source_file"),
                })
    return {
        "code": "spn_parameter_name_not_in_alias_map",
        "severity": "medium",
        "summary": (f"canboat kanıtındaki {seen} J1939 parametre adından {len(hits)} tanesi "
                    f"copilot sözlüğünde karşılık bulamıyor"),
        "why_it_matters": ("Kullanıcı 'Engine Intercooler Temp' gibi gerçek bir J1939 sinyalini "
                           "sorduğunda sinyal çözümlemesi başarısız olur; sorgu kanala düşer ve "
                           "yanlış/alakasız cevap riski doğar. Çözüm: signal_aliases.json "
                           "(source_forms) + signal_measurement_map.json (aliases) genişletmesi."),
        "expression": "normalize(name) not in vocabulary (signal_aliases + signal_measurement_map)",
        **_target(ALIAS_DB),
        "affected_count": len(hits),
        "examples": hits[:EXAMPLE_LIMIT],
    }


# Sources whose licence the file itself declares. Everything else in the merged
# J1939 database is licence-unresolved — the repository policy
# (data/PROVENANCE.md §1/§5) requires a resolvable licence per source.
LICENSED_SOURCE_MARKERS = ("ccby4", "cc by 4.0", "apache", "mit", "public domain")


def _declared_licence(record: dict[str, Any]) -> str:
    for field in ("_source_license", "_source_license_sitrak", "license"):
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def detect_j1939_source_without_licence(root: Path = ROOT) -> dict[str, Any]:
    """SPN records whose origin cannot be resolved to a licensed source.

    Two measured classes:
      * ``no_source``      — the record carries no ``source`` at all, so nobody
                             can say where the text came from (e.g. SPN 190).
      * ``unlicensed_src`` — a ``source`` is named but no licence accompanies
                             it and the value is not the one declared-licensed
                             source (SITRAK, CC BY 4.0).

    This is an attribution/licence-resolvability measurement, **not** a legal
    conclusion: the data owner decides attribution or removal.
    """
    path = root / SPN_DB
    blob = _load(path)
    spns = blob["spns"]
    sources = list((blob.get("metadata") or {}).get("sources") or [])
    licensed_sources = [s for s in sources
                        if any(marker in str(s).lower() for marker in LICENSED_SOURCE_MARKERS)]
    by_class: dict[str, int] = {"no_source": 0, "unlicensed_src": 0}
    examples: list[dict[str, Any]] = []
    for key, record in sorted(spns.items()):
        source = str((record or {}).get("source") or "").strip()
        licence = _declared_licence(record)
        if not source:
            by_class["no_source"] += 1
            if len([e for e in examples if e["class"] == "no_source"]) < 3:
                examples.append({"key": key, "class": "no_source", "name": (record or {}).get("name")})
            continue
        lowered = source.lower()
        if licence or any(marker in lowered for marker in LICENSED_SOURCE_MARKERS):
            continue
        by_class["unlicensed_src"] += 1
        if len([e for e in examples if e["class"] == "unlicensed_src"]) < 3:
            examples.append({"key": key, "class": "unlicensed_src", "source": source,
                             "name": (record or {}).get("name")})
    total = sum(by_class.values())
    return {
        "code": "j1939_source_without_licence",
        "severity": "high",
        "summary": (f"{total}/{len(spns)} SPN kaydının kaynağı lisansa bağlanamıyor "
                    f"(kaynak alanı yok: {by_class['no_source']}, lisanssız kaynak: "
                    f"{by_class['unlicensed_src']}); metadata'daki {len(sources)} kaynaktan "
                    f"yalnız {len(licensed_sources)} tanesi lisansını kendisi belirtiyor"),
        "why_it_matters": ("data/PROVENANCE.md §1 her kaynak için çözülebilir lisans ister "
                           "(belirsizse reddedilir) ve §5 ticari/forum kazımasını reddeder. "
                           "metadata.sources içindeki kaynakların çoğu repair.diesellaptops / "
                           "4roadservice / justanswer / j1939hub / dtcdocs gibi lisansı belirtilmemiş "
                           "ticari-manual sitelerden toplama; attribution bloğu 19 kaynağın 1'ini "
                           "adlandırıyor. Bu hukuki risk değil, kanıtlanmış bir atıf/lisans "
                           "çözülebilirliği eksiği — karar veri sahibinin."),
        "expression": ("no record.source, or (source without _source_license* and not a declared "
                       "licensed source value)"),
        **_target(SPN_DB),
        "affected_count": total,
        "examples": examples[:EXAMPLE_LIMIT],
    }


Detector = Callable[[Path], dict[str, Any]]
DETECTORS: dict[str, Detector] = {
    "j1939_source_without_licence": detect_j1939_source_without_licence,
    "spn_parameter_name_not_in_alias_map": detect_spn_parameter_name_not_in_alias_map,
    "spn_name_is_fmi_sentence": detect_spn_name_is_fmi_sentence,
    "spn_name_embeds_spn_fmi_token": detect_spn_name_embeds_spn_fmi_token,
    "spn_unit_placeholder": detect_spn_unit_placeholder,
    "spn_without_fault_matrix": detect_spn_without_fault_matrix,
    "dtc_missing_title_tr": detect_dtc_missing_title_tr,
}


def measure(root: Path = ROOT) -> list[dict[str, Any]]:
    """Run every detector; returns the findings in a stable order."""
    return [DETECTORS[name](root) for name in sorted(DETECTORS)]


# --------------------------------------------------------------------------- #
# staging
# --------------------------------------------------------------------------- #
def build_record(finding: dict[str, Any]) -> dict[str, Any]:
    code = finding["code"]
    return {
        "schema_version": 1,
        "intake_id": f"kbdefect-{code.replace('_', '-')}",
        "record_type": "kb_defect",
        "submitted_at": MEASURED_AT,
        "submitter": {"type": "automated", "id": "scripts/intake_kb_defects.py", "role": "author"},
        "source": {
            "title": f"Kendi verimizden ölçüm: {finding['summary']}",
            "path": f"{finding['target_file']} (repo içi ölçüm; sha256 kayıt altındadır)",
            "type": "internal_kb",
            "publisher": "UCanLab intake",
            "revision": None,
            "licence": "project-internal",
            "access_date": MEASURED_AT,
            "snapshot": {
                "archive_url": None,
                "sha256": finding["target_sha256"],
                "bytes": finding["target_bytes"],
                "pages_cited": [],
            },
        },
        "confidence": "corroborated",
        "draft": True,
        "knowledge_base": None,
        "payload": {
            "defect_code": code,
            "severity": finding["severity"],
            "summary": finding["summary"],
            "why_it_matters": finding["why_it_matters"],
            "target_file": finding["target_file"],
            "target_sha256": finding["target_sha256"],
            "detector_expression": finding["expression"],
            "affected_count": finding["affected_count"],
            "examples": finding["examples"],
        },
        "notes": ("Düzeltme intake'ten yapılmaz: bu kayıt yalnız ölçümü ve tespit ifadesini "
                  "saklar. validate_intake.py her çalıştırmada dedektörü yeniden çalıştırır; "
                  "sayı değişirse WARN (drift), sıfırlanırsa kapanmış sayılır."),
    }


def stage(root: Path = ROOT, intake_dir: Path = INTAKE, apply: bool = False) -> tuple[int, list[str]]:
    target = intake_dir / DEFECT_SUBDIR
    if apply:
        target.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    written = 0
    for finding in measure(root):
        record = build_record(finding)
        rel = f"{DEFECT_SUBDIR}/{record['intake_id']}.json"
        out = intake_dir / rel
        payload = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        if out.exists():
            if out.read_text(encoding="utf-8") != payload:
                problems.append(f"{rel}: staged result differs from the current measurement "
                                f"(detector ya da veri değişti — kaydı gözden geçir)")
            continue
        if apply:
            out.write_text(payload, encoding="utf-8")
        else:
            problems.append(f"{rel}: missing (run with --apply)")
        written += 1
    return written, problems


# --------------------------------------------------------------------------- #
# report / CLI
# --------------------------------------------------------------------------- #
def render(findings: list[dict[str, Any]]) -> str:
    lines = ["# KB veri kalitesi ölçümü", "",
             "| code | severity | affected | target |", "|---|---|---|---|"]
    for f in findings:
        lines.append(f"| `{f['code']}` | {f['severity']} | **{f['affected_count']}** | "
                     f"`{f['target_file']}` |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--report")
    ap.add_argument("--stage", action="store_true", help="verify staged defect records")
    ap.add_argument("--apply", action="store_true", help="with --stage: write them")
    args = ap.parse_args()
    root = Path(args.root).resolve()

    findings = measure(root)
    for f in findings:
        print(f"[{f['severity']:6s}] {f['code']:32s} affected={f['affected_count']}")
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(findings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(findings), encoding="utf-8")
    if args.stage:
        written, problems = stage(root, INTAKE, apply=args.apply)
        for problem in problems:
            print(f"[!] {problem}")
        print(f"[*] kb_defect records: {written} ({'written' if args.apply else 'verify only'})")
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    raise SystemExit(main())
