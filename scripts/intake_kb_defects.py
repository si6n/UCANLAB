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
    python scripts/intake_kb_defects.py --stage-gaps --apply

``--stage-gaps`` writes one ``provenance_gap`` record per **distinct** source key
(26 keys, 8,918 occurrences), so the finding is reviewable source by source
instead of as an unreadable pile of identical rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse
from collections import Counter
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
                           "adlandırıyor. ÖNEMLİ: data/diagnostics/PROVENANCE.md bu hasadın bir kısmını "
                           "açıkça belgeliyor (dieselaptops, j1939hub, obd-codes) — yani konu "
                           "gizlenmiş değil, **lisansı çözülemiyor**. Bu hukuki hüküm değil, "
                           "ölçülmüş bir atıf/lisans çözülebilirliği eksiğidir; karar veri sahibinin."),
        "expression": ("no record.source, or (source without _source_license* and not a declared "
                       "licensed source value)"),
        **_target(SPN_DB),
        "affected_count": total,
        "examples": examples[:EXAMPLE_LIMIT],
    }


PROVENANCE_DOCS = (Path("data") / "PROVENANCE.md", Path("data") / "diagnostics" / "PROVENANCE.md")
SOURCE_FIELDS = ("_source_ref", "_source_ref_sitrak", "source", "evidence_url", "url", "_source",
                 "description_en_source", "causes_source")


def _provenance_corpus(root: Path) -> str:
    parts = []
    for relative in PROVENANCE_DOCS:
        path = root / relative
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8").lower())
    return "\n".join(parts)


def _is_documented(value: str, corpus: str) -> bool:
    """True when any meaningful token of ``value`` occurs in a provenance doc."""
    low = value.lower()
    host = urlparse(low if "//" in low else "//" + low).netloc or low
    tokens = [t for t in re.split(r"[^a-z0-9]+", host) if len(t) > 3]
    tokens += [t for t in re.split(r"[^a-z0-9]+", low) if len(t) > 3]
    return any(token in corpus for token in tokens)


GAPS_SUBDIR = "gaps"
GAP_KEY_LIMIT = 4


def measure_provenance_gaps(root: Path = ROOT) -> dict[str, dict[str, Any]]:
    """Per-source occurrence of values that no provenance document records.

    Keyed by a normalised source key (URL host, or the leading token of a free
    text value). One record per key is reviewable: the data owner can decide
    document / attribute / drop source by source, instead of staring at 8,918
    identical-shaped fields.
    """
    corpus = _provenance_corpus(root)
    gaps: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "data" / "diagnostics").rglob("*.json")):
        if "quarantine" in path.parts:
            continue
        try:
            blob = _load(path)
        except (OSError, ValueError):
            continue
        stack: list[Any] = [blob]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for field in SOURCE_FIELDS:
                    value = node.get(field)
                    if not isinstance(value, str) or not value.strip():
                        continue
                    if _is_documented(value, corpus):
                        continue
                    low = value.strip().lower()
                    host = urlparse(low if "//" in low else "//" + low).netloc or low
                    key = host or low.split()[0]
                    entry = gaps.setdefault(key, {
                        "occurrences": 0, "files": [], "sample_values": [],
                        "sample_record_keys": [], "fields": [],
                    })
                    entry["occurrences"] += 1
                    if path.name not in entry["files"]:
                        entry["files"].append(path.name)
                    if field not in entry["fields"]:
                        entry["fields"].append(field)
                    if len(entry["sample_values"]) < 3 and value.strip()[:80] not in entry["sample_values"]:
                        entry["sample_values"].append(value.strip()[:80])
                    for record_key in (node.get("code"), node.get("spn")):
                        if isinstance(record_key, (str, int)) and len(entry["sample_record_keys"]) < GAP_KEY_LIMIT:
                            rendered = str(record_key)
                            if rendered not in entry["sample_record_keys"]:
                                entry["sample_record_keys"].append(rendered)
                            break
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return gaps


def build_gap_records(root: Path = ROOT) -> list[dict[str, Any]]:
    """One ``provenance_gap`` intake record per undocumented source key."""
    gaps = measure_provenance_gaps(root)
    records: list[dict[str, Any]] = []
    for key in sorted(gaps):
        entry = gaps[key]
        payload_file = Path("data") / "diagnostics" / entry["files"][0]
        records.append({
            "schema_version": 1,
            "intake_id": "provgap-" + re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-"),
            "record_type": "provenance_gap",
            "submitted_at": MEASURED_AT,
            "submitter": {"type": "automated", "id": "scripts/intake_kb_defects.py", "role": "author"},
            "source": {
                "title": f"Kaynak izlenebilirligi boslugu: {key} hicbir provenance belgesinde gecmiyor",
                "path": f"{payload_file.as_posix()} (ic alanlarda gecen kaynak degeri)",
                "type": "internal_kb",
                "publisher": "UCanLab intake",
                "revision": None,
                "licence": "project-internal",
                "access_date": MEASURED_AT,
                "snapshot": {"archive_url": None, "sha256": None, "bytes": None, "pages_cited": []},
            },
            "confidence": "corroborated",
            "draft": True,
            "knowledge_base": None,
            "payload": {
                "source_key": key,
                "occurrences": entry["occurrences"],
                "files": entry["files"],
                "fields": entry["fields"],
                "sample_values": entry["sample_values"],
                "sample_record_keys": entry["sample_record_keys"],
                "documented_in": [],
                "licence_status": "unresolved",
            },
            "notes": ("Kanit kaydidir, kusur degil: veri bu kaynagi isaret ediyor ama repoda hicbir "
                      "provenance belgesi onu adlandirmiyor. Karar veri sahibinin: belgele (lisans + "
                      "erisim bilgisi), kayda oznelik ekle, ya da alani kaldir. validate_intake.py "
                      "anahtari yeniden olcer; kapaninca kayit arsivlenir."),
        })
    return records


def stage_gaps(root: Path = ROOT, intake_dir: Path = INTAKE, apply: bool = False,
               refresh: bool = False) -> tuple[int, list[str]]:
    """Write (or verify) one record per undocumented source key."""
    target = intake_dir / GAPS_SUBDIR
    if apply or refresh:
        target.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    written = 0
    for record in build_gap_records(root):
        rel = f"{GAPS_SUBDIR}/{record['intake_id']}.json"
        out = intake_dir / rel
        payload = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        if out.exists():
            if out.read_text(encoding="utf-8") != payload:
                if refresh:
                    out.write_text(payload, encoding="utf-8")
                    written += 1
                else:
                    problems.append(f"{rel}: differs from the current measurement (--refresh after review)")
            continue
        if apply:
            out.write_text(payload, encoding="utf-8")
        else:
            problems.append(f"{rel}: missing (run with --apply)")
        written += 1
    return written, problems


# Categories the repository policy rejects by *name* or by *kind*
# (data/PROVENANCE.md §5: "iATN ve herkese açık forumlar · … · lisanssız depolar
# (alperunlu/DTCparser, f-steff/…, digitalyacht/…, linux-can/can-utils)").
# A measured source that matches one of these is a policy breach; anything else is
# an attribution gap. Hard-coding "high" would accuse sources the policy never
# named, so the classification is measured instead.
POLICY_NAMED_MARKERS = ("iatn", "alperunlu", "dtcparser", "f-steff", "digitalyacht",
                        "linux-can", "can-utils", "wikipedia")
POLICY_CATEGORY_MARKERS = ("forum", "justanswer", "reddit", "stackexchange", "obscure")


def classify_source(key: str, policy: str) -> str:
    """``policy_breach`` when the policy names or forbids this kind of source."""
    low = key.lower()
    if any(marker in low for marker in POLICY_CATEGORY_MARKERS):
        return "policy_breach"
    if any(marker in low for marker in POLICY_NAMED_MARKERS):
        return "policy_breach" if marker in policy or marker in POLICY_NAMED_MARKERS else "unattested"
    return "unattested"


def detect_kb_source_value_not_in_provenance_doc(root: Path = ROOT) -> dict[str, Any]:
    """Shipped records point at sources that neither provenance document records.

    This is a *traceability* measurement, not a legal claim: the data asserts a
    provenance value and no repository document mentions it, so the chain cannot
    be walked. Severity follows the documented policy — a measured source that
    the rejected list names or forbids by kind is a breach, everything else is an
    attribution gap.
    """
    gaps = measure_provenance_gaps(root)
    policy_path = root / Path("data") / "PROVENANCE.md"
    policy = policy_path.read_text(encoding="utf-8").lower() if policy_path.is_file() else ""
    per_file: dict[str, int] = {}
    total = 0
    for entry in gaps.values():
        total += entry["occurrences"]
        for name in entry["files"]:
            per_file[name] = per_file.get(name, 0) + entry["occurrences"]
    ranked = sorted(per_file.items(), key=lambda kv: -kv[1])
    classified = {key: classify_source(key, policy) for key in gaps}
    breaches = sorted(k for k, v in classified.items() if v == "policy_breach")
    severity = "high" if breaches else "medium"
    sample_examples: list[dict[str, Any]] = []
    for key in sorted(gaps, key=lambda k: -gaps[k]["occurrences"])[:EXAMPLE_LIMIT]:
        entry = gaps[key]
        sample_examples.append({"source_key": key, "occurrences": entry["occurrences"],
                                "files": entry["files"], "policy_class": classified[key],
                                "sample_value": (entry["sample_values"] or [""])[0]})
    return {
        "code": "kb_source_value_not_in_provenance_doc",
        "severity": severity,
        "summary": (f"{total} kaynak alanı değeri hiçbir provenance belgesinde geçmiyor "
                    f"({', '.join(f'{name}: {count}' for name, count in ranked[:3])})"
                    + (f"; politika §5'in yasakladığı türden: {', '.join(breaches[:4])}" if breaches
                       else "; politika §5 bu kaynakları adıyla yasaklamıyor (atıf eksiği)")),
        "why_it_matters": (
            "Veri, kökeni repo'da hiç yazılmamış bir kaynağı işaret ediyor: "
            "data/PROVENANCE.md (13 satırlık hash kanıtı) ve data/diagnostics/PROVENANCE.md "
            "(hasat günlüğü) bu değerleri içermiyor. Böyle bir alan kanıt zinciri "
            "yürütülemeyen bir iddiadır. Çözüm ikisinden biri: kaynağı belgele ya da alanı "
            "kaldır. En büyük dosya dtc_database.json. Öncelik, ölçülen kaynağın politika §5'te "
            "yasaklı türden olup olmadığına göre verilir — politika adıyla yasaklamadığı bir "
            "kaynak 'high' ile suçlanmaz."),
        "expression": "token(source-ish field) not found in data/PROVENANCE.md + data/diagnostics/PROVENANCE.md",
        **_target(DTC_DB),
        "affected_count": total,
        "examples": sample_examples,
    }


def detect_dtc_severity_unknown_and_unclassed(root: Path = ROOT) -> dict[str, Any]:
    """Codes the classing and severity passes skipped (severity UNKNOWN + NoClass).

    Both fields are left at their placeholder for the same records, which makes
    the gap systematic rather than random: for these codes the copilot cannot
    triage severity and cannot group them by class.
    """
    blob = _load(root / DTC_DB)
    hits = []
    namespaces: dict[str, int] = {}
    for code, record in sorted(blob.items()):
        record = record or {}
        if str(record.get("severity")) == "UNKNOWN" and str(record.get("dtc_class")) == "NoClass":
            hits.append({"code": code, "title": record.get("title"),
                         "dtc_namespace": record.get("dtc_namespace"),
                         "subsystem": record.get("subsystem")})
            key = str(record.get("dtc_namespace"))
            namespaces[key] = namespaces.get(key, 0) + 1
    total = len(blob)
    return {
        "code": "dtc_severity_unknown_and_unclassed",
        "severity": "medium",
        "summary": (f"{len(hits)}/{total} DTC kodu sınıflandırma ve şiddet geçişinde atlanmış "
                    f"(severity=UNKNOWN, dtc_class=NoClass) — "
                    f"{', '.join(f'{k}: {v}' for k, v in sorted(namespaces.items()))}"),
        "why_it_matters": ("Copilot bu kodlarda şiddet sıralaması yapamaz (tümü UNKNOWN) ve "
                           "sınıf gruplamasına katamaz. Aynı iki alan aynı kayıtlarda boş olduğu "
                           "için eksiklik rastgele değil, iki geçişin ortak atladığı bir küme."),
        "expression": "severity == 'UNKNOWN' and dtc_class == 'NoClass'",
        **_target(DTC_DB),
        "affected_count": len(hits),
        "examples": hits[:EXAMPLE_LIMIT],
    }


def detect_dtc_missing_symptoms(root: Path = ROOT) -> dict[str, Any]:
    """Codes with no symptom text at all: nothing for the matcher to match on."""
    blob = _load(root / DTC_DB)
    missing = 0
    hits = []
    for code, record in sorted(blob.items()):
        record = record or {}
        symptoms = record.get("symptoms")
        empty = (not symptoms) if isinstance(symptoms, list) else not str(symptoms or "").strip()
        if empty:
            missing += 1
            if len(hits) < EXAMPLE_LIMIT:
                hits.append({"code": code, "title": record.get("title"),
                             "subsystem": record.get("subsystem")})
    total = len(blob)
    return {
        "code": "dtc_missing_symptoms",
        "severity": "low",
        "summary": f"{missing}/{total} DTC kodunda semptom metni yok (semptom eşleştirici için boş)",
        "why_it_matters": ("Semptom→DTC eşleşmesi semptom metnine dayanır; kodun %90'ında metin "
                           "olmadığı için kullanıcı 'aracım titriyor' dediğinde bu kodlar eşleşemez. "
                           "Bu bilgi eksiğidir, hata değil."),
        "expression": "not record.get('symptoms')",
        **_target(DTC_DB),
        "affected_count": missing,
        "examples": hits,
    }


ROOT_CAUSE_GRAPH = Path("data") / "diagnostics" / "root_cause_graph.json"
MEASUREMENT_MAP_FILE = Path("data") / "diagnostics" / "signal_measurement_map.json"
TELEMETRY_FILE = Path("data") / "diagnostics" / "telemetry_thresholds.json"
DTC_CODE_FULL_RE = re.compile(r"^[PBCU][0-9A-F]{4}$")


def detect_root_cause_graph_dtc_coverage(root: Path = ROOT) -> dict[str, Any]:
    """DTC codes that no cause node references: no chain for the copilot to walk.

    The graph is cause-keyed (nodes point at ``expected_dtcs``), so coverage has to
    be measured by walking the references — not by counting nodes.
    """
    nodes = _load(root / ROOT_CAUSE_GRAPH).get("nodes") or []
    dtc_codes = set(_load(root / DTC_DB))
    referenced: set[str] = set()
    spn_refs: set[int] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        for ref in node.get("expected_dtcs") or []:
            text = str(ref).strip()
            if DTC_CODE_FULL_RE.match(text):
                referenced.add(text)
            match = re.fullmatch(r"SPN\s+(\d+)", text)
            if match:
                spn_refs.add(int(match.group(1)))
    uncovered = sorted(dtc_codes - referenced)
    return {
        "code": "root_cause_graph_dtc_coverage",
        "severity": "medium",
        "summary": (f"{len(referenced)}/{len(dtc_codes)} DTC kodu bir neden düğümünde "
                    f"referans veriyor (%{round(100 * len(referenced) / max(len(dtc_codes), 1), 1)}); "
                    f"{len(uncovered)} kod için neden zinciri yok ({len(nodes)} düğüm)"),
        "why_it_matters": ("Kök neden zinciri yalnız referans verilen kodlarda kurulabilir. "
                           "Kapsanmayan kodlarda copilot yalnız metin yanıtlar; 'neden olur' "
                           "sorusunda zincir kurulamaz. Ölçüm referans taramasıyla yapılır "
                           "(düğüm sayısı kapsam değildir) ve referansların tamamı DB'de "
                           "karşılık bulur — yani eksiklik üretim değil, kapsam eksiğidir."),
        "expression": "DTC codes absent from every node's expected_dtcs",
        **_target(DTC_DB),
        "affected_count": len(uncovered),
        "examples": [{"code": code} for code in uncovered[:EXAMPLE_LIMIT]],
        "spn_references_checked": len(spn_refs),
    }


def detect_cause_node_without_evidence_signal(root: Path = ROOT) -> dict[str, Any]:
    """Cause nodes that name no signal: the copilot has nothing to check against."""
    nodes = _load(root / ROOT_CAUSE_GRAPH).get("nodes") or []
    empty = [n for n in nodes if isinstance(n, dict) and not (n.get("evidence_signals") or [])]
    return {
        "code": "cause_node_without_evidence_signal",
        "severity": "low",
        "summary": f"{len(empty)}/{len(nodes)} neden düğümü hiç sinyal adı taşımıyor "
                   f"(evidence_signals boş)",
        "why_it_matters": ("Bir nedeni doğrulamak için sinyal gerekir; sinyal adı olmayan "
                           "düğüm yalnız metin düzeyinde kalır, canlı değerle karşılaştırılamaz."),
        "expression": "not node.get('evidence_signals')",
        **_target(ROOT_CAUSE_GRAPH),
        "affected_count": len(empty),
        "examples": [{"id": n.get("id"), "title": n.get("title"),
                      "expected_dtcs": (n.get("expected_dtcs") or [])[:3]} for n in empty[:EXAMPLE_LIMIT]],
    }


def detect_measurement_signal_without_threshold(root: Path = ROOT) -> dict[str, Any]:
    """Signals the copilot knows but cannot judge: no telemetry threshold."""
    signals = _load(root / MEASUREMENT_MAP_FILE).get("signals") or []
    thresholds = set((_load(root / TELEMETRY_FILE).get("signals") or {}))
    without = [s for s in signals if not s.get("threshold_key") or s["threshold_key"] not in thresholds]
    return {
        "code": "measurement_signal_without_threshold",
        "severity": "medium",
        "summary": f"{len(without)}/{len(signals)} ölçüm sinyali için eşik tanımı yok "
                   f"(telemetry_thresholds.json'da {len(thresholds)} sinyal var)",
        "why_it_matters": ("Sinyal çözülüyor ama 'iyi mi kötü mü' sorusu cevaplanamıyor: eşik "
                           "olmadan canlı değer değerlendirilemez. İsim uyumu ayrıca kontrol "
                           "edilir: eşik anahtarı ölçüm sözlüğündeki kanonik adla eşleşmeli."),
        "expression": "not signal.get('threshold_key') or threshold_key not in telemetry_thresholds",
        **_target(MEASUREMENT_MAP_FILE),
        "affected_count": len(without),
        "examples": [{"canonical": s.get("canonical"), "unit": s.get("unit"),
                      "aliases": (s.get("aliases") or [])[:3]} for s in without[:EXAMPLE_LIMIT]],
    }


UDS_DID_DB = Path("data") / "diagnostics" / "uds_did_database.json"
# data/PROVENANCE.md §5: "UDS DID / Mode 06 veri tabloları (açık yeniden-dağıtılabilir
# kaynak YOK — uydurulmaz)". The policy text is matched, not hardcoded as fact: if
# someone rewrites the policy, the finding degrades from "policy breach" to
# "attribution gap" instead of quietly accusing them.
UDS_POLICY_MARKER = "UDS DID / Mode 06"


def detect_uds_oem_did_without_source(root: Path = ROOT) -> dict[str, Any]:
    """OEM-specific DIDs that carry no ``source`` at all.

    The universal ISO 14229 rows do cite a licence-accurate source
    (python-udsoncan, MIT) for their identity. The OEM rows carry byte length,
    scaling, offset and unit — and no source for any of it.
    """
    blob = _load(root / UDS_DID_DB)
    dids = blob.get("dids") or {}
    without: list[dict[str, Any]] = []
    with_source = 0
    by_oem: dict[str, int] = {}
    for did, record in sorted(dids.items()):
        record = record or {}
        source = str(record.get("source") or record.get("_source_ref") or "").strip()
        if source:
            with_source += 1
            continue
        oem = str(record.get("oem") or "(belirtilmemiş)")
        by_oem[oem] = by_oem.get(oem, 0) + 1
        if len(without) < EXAMPLE_LIMIT:
            without.append({"did": did, "name": record.get("name"), "oem": record.get("oem"),
                            "byte_length": record.get("byte_length"), "unit": record.get("unit")})
    policy_path = root / Path("data") / "PROVENANCE.md"
    policy = policy_path.read_text(encoding="utf-8") if policy_path.is_file() else ""
    breach = UDS_POLICY_MARKER in policy
    severity = "high" if breach else "medium"
    return {
        "code": "uds_oem_did_without_source",
        "severity": severity,
        "summary": (f"{len(dids) - with_source}/{len(dids)} UDS DID kaydında kaynak yok "
                    f"(yalnız {with_source} kayıt kaynak belirtiyor); üretici dağılımı: "
                    f"{', '.join(f'{k}: {v}' for k, v in sorted(by_oem.items(), key=lambda kv: -kv[1])[:6])}"),
        "why_it_matters": (
            ("data/PROVENANCE.md §5 bu tablo için açık yeniden-dağıtılabilir kaynak olmadığını ve "
             "'uydurulmaz' ilkesini yazıyor; buna rağmen üreticiye özgü satırlar byte_length/scaling/"
             "offset/unit değerlerini kaynaksız taşıyor. Doğrulanmış kısım: 32 ISO 14229 satırının "
             "kimliği (DID + ad) python-udsoncan (MIT, Pier-Yves Lessard) udsoncan/common/dids.py "
             "sabitlerinden geliyor — kütüphane YALNIZCA numara ve sabit adı veriyor, ölçek/birim/"
             "bayt uzunluğu vermiyor; yani kaynak dizesi kapsamından biraz geniş atıf yapıyor.")
            if breach else
            ("Politika metni değişmiş görünüyor; yine de üreticiye özgü DID satırlarının "
             "ölçek/birim öznitelikleri kaynaksız.")),
        "expression": "no record.source / _source_ref",
        **_target(UDS_DID_DB),
        "affected_count": len(dids) - with_source,
        "examples": without,
    }


Detector = Callable[[Path], dict[str, Any]]
DETECTORS: dict[str, Detector] = {
    "cause_node_without_evidence_signal": detect_cause_node_without_evidence_signal,
    "dtc_missing_symptoms": detect_dtc_missing_symptoms,
    "measurement_signal_without_threshold": detect_measurement_signal_without_threshold,
    "root_cause_graph_dtc_coverage": detect_root_cause_graph_dtc_coverage,
    "uds_oem_did_without_source": detect_uds_oem_did_without_source,
    "dtc_severity_unknown_and_unclassed": detect_dtc_severity_unknown_and_unclassed,
    "kb_source_value_not_in_provenance_doc": detect_kb_source_value_not_in_provenance_doc,
    "j1939_source_without_licence": detect_j1939_source_without_licence,
    "spn_parameter_name_not_in_alias_map": detect_spn_parameter_name_not_in_alias_map,
    "spn_name_is_fmi_sentence": detect_spn_name_is_fmi_sentence,
    "spn_name_embeds_spn_fmi_token": detect_spn_name_embeds_spn_fmi_token,
    "spn_unit_placeholder": detect_spn_unit_placeholder,
    "spn_without_fault_matrix": detect_spn_without_fault_matrix,
    "dtc_missing_title_tr": detect_dtc_missing_title_tr,
}


# Detectors re-read a few MB of vendored data. The gate runs them on every
# invocation and the test suite runs the gate many times, so results are cached
# per (root signature). The signature is the identity of every input file the
# detectors touch, so a changed file can never serve a stale answer.
_CACHE: dict[str, tuple[tuple[Any, ...], list[dict[str, Any]]]] = {}


def _signature(root: Path) -> tuple[Any, ...]:
    parts: list[Any] = []
    for directory in (root / "data" / "diagnostics", root / "data" / "intake" / "spn_ref"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.json")):
            try:
                stat = path.stat()
            except OSError:
                continue
            parts.append((path.relative_to(root).as_posix(), stat.st_mtime_ns, stat.st_size))
    for relative in PROVENANCE_DOCS:
        path = root / relative
        if path.is_file():
            stat = path.stat()
            parts.append((relative, stat.st_mtime_ns, stat.st_size))
    return tuple(parts)


def measure(root: Path = ROOT, use_cache: bool = True) -> list[dict[str, Any]]:
    """Run every detector; returns the findings in a stable order."""
    key = str(root)
    signature = _signature(root)
    if use_cache and _CACHE.get(key, (None, None))[0] == signature:
        return _CACHE[key][1]
    findings = [DETECTORS[name](root) for name in sorted(DETECTORS)]
    _CACHE[key] = (signature, findings)
    return findings


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


def stage(root: Path = ROOT, intake_dir: Path = INTAKE, apply: bool = False,
          refresh: bool = False) -> tuple[int, list[str]]:
    """Write (or verify) one record per detector finding.

    By default an existing record that no longer matches the measurement is a
    **problem**, never a silent overwrite: a changed number means a reviewer has
    to look. ``refresh`` is the explicit, auditable way to accept the new value.
    """
    target = intake_dir / DEFECT_SUBDIR
    if apply or refresh:
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
                if refresh:
                    out.write_text(payload, encoding="utf-8")
                    written += 1
                else:
                    problems.append(f"{rel}: staged result differs from the current measurement "
                                    f"(dedektör ya da veri değişti — kaydı gözden geçir, "
                                    f"sonra --refresh)")
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
    ap.add_argument("--stage-gaps", action="store_true",
                    help="stage one provenance-gap record per undocumented source key")
    ap.add_argument("--apply", action="store_true", help="with --stage: write them")
    ap.add_argument("--refresh", action="store_true",
                    help="accept a changed measurement for existing records (explicit rewrite)")
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
    if args.stage_gaps:
        written, problems = stage_gaps(root, INTAKE, apply=args.apply, refresh=args.refresh)
        for problem in problems:
            print(f"[!] {problem}")
        print(f"[*] provenance_gap records: {written} "
              f"({'written' if args.apply or args.refresh else 'verify only'})")
        return 1 if problems else 0
    if args.stage:
        written, problems = stage(root, INTAKE, apply=args.apply, refresh=args.refresh)
        for problem in problems:
            print(f"[!] {problem}")
        print(f"[*] kb_defect records: {written} ({'written' if args.apply else 'verify only'})")
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    raise SystemExit(main())
