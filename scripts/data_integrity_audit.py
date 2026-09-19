# -*- coding: utf-8 -*-
"""data/ bütünlük denetimi (UCANLAB) — offline, stdlib + proje kodu.

Amaç: `data/` altındaki bilgi tabanlarında sessiz veri kaybını yakalamak.
Bu aracın ilk koşumu 2026-09-19'da `f8bb892` commit'inin üç CSV ikizinin veri
satırlarını boşalttığını ortaya çıkardı (bkz. docs/audit/data_integrity_2026-09-19.md).

Denetimler:
    1. json_assets          : her JSON ayrışır mı, kayıt sayısı nedir
    2. metadata_totals      : metadata'daki toplam alanı gerçek sayıya eşit mi
    3. csv_twins            : boş satır oranı, sarkan (ragged) satır, JSON ile satır sayısı
    4. dbc_catalog          : varlık + sha256 + boyut + kategori sayaçları, katalog dışı dosyalar
    5. golden_cases         : üretim yükleyicisi (src.engine.ai.golden_cases) ile şema uyumu
    6. knowledge_procedures : ayrışır mı, dosya adı ile dtc alanı uyuyor mu, yetim kayıt var mı
    7. truncation_signature : tarihsel [:400] / [:403] kırpma imzası (sessiz veri kaybı)
    8. fault_matrix_titles  : FMI başlığı boş satırlar (CSV başlık kolonu boş kalır)

Kullanım:
    python scripts/data_integrity_audit.py            # rapor + docs/audit yaz
    python scripts/data_integrity_audit.py --no-write # yalnız stdout
Çıkış kodu: FAIL bulgu sayısı 0 ise 0, aksi halde 1.

Uydurma yok: her bulgu dosya yolu + ölçülen sayı ile birlikte raporlanır;
ölçülemeyen durum "belirsiz" olarak INFO/WARN işaretlenir.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DIAG_DIR = DATA_DIR / "diagnostics"
DBC_DIR = DATA_DIR / "dbc"
CASES_DIR = DATA_DIR / "golden_traces" / "cases"
PROC_DIR = DATA_DIR / "knowledge" / "dtc_procedures"
REPORT_DIR = ROOT / "docs" / "audit"

# Tarihsel sessiz veri kaybı imzası: bu uzunlukta biten metinler kırpılmıştır.
TRUNCATION_CAPS = (400, 403)

# Cümle sonu sayılan karakterler (kırpma imzası için).
_TERMINAL_CHARS = (".", "!", "?", ":", ")", "]", '"', "\u201d", "\u2019", "'")

# Kırpık adım imzası: metin "Step V" / "Adım V" ile bitiyorsa kuyruğu kesilmiştir
# (docs: "kırpık adım" olayı — bkz. Veri-Mimarisi §5.1).
_STEP_V_RE = re.compile(r"(Step V|Adım V)(\W*\w{0,3})?\.?$")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _finding(check: str, severity: str, detail: str, evidence: str) -> dict[str, str]:
    return {"check": check, "severity": severity, "detail": detail, "evidence": evidence}


# --------------------------------------------------------------------------
# 1 + 2 — JSON varlıkları ve metadata sayaçları
# --------------------------------------------------------------------------
def check_json_assets(findings: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in sorted(DATA_DIR.rglob("*.json")):
        try:
            payload = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            findings.append(_finding("json_assets", "FAIL", f"{path.name} ayrıştırılamadı: {exc}",
                                     str(path.relative_to(ROOT))))
            continue
        counts[str(path.relative_to(ROOT))] = len(payload) if isinstance(payload, (list, dict)) else 1
    return counts


def check_metadata_totals(findings: list[dict[str, str]]) -> dict[str, Any]:
    measured: dict[str, Any] = {}
    specs = [
        ("data/diagnostics/j1939_spn_fmi_database.json", "metadata", "total_spns", lambda d: len(d["spns"])),
        ("data/diagnostics/uds_did_database.json", "metadata", "total_dids", lambda d: len(d["dids"])),
        ("data/diagnostics/extended_pid_database.json", "metadata", "total_pids", lambda d: len(d["pids"])),
        ("data/diagnostics/obd_mode06_database.json", "metadata", "total_monitors", lambda d: len(d["monitors"])),
    ]
    for rel, meta_key, field, counter in specs:
        payload = _load_json(ROOT / rel)
        declared = payload.get(meta_key, {}).get(field)
        actual = counter(payload)
        measured[rel] = {"declared": declared, "actual": actual}
        if declared != actual:
            findings.append(_finding(
                "metadata_totals", "FAIL",
                f"{Path(rel).name}: {meta_key}.{field}={declared} ama gerçek={actual}",
                f"{rel} ({meta_key}.{field})",
            ))
    return measured


# --------------------------------------------------------------------------
# 3 — CSV ikizleri
# --------------------------------------------------------------------------
# (csv, json, beklenen satır kuralı) — kural: one_to_one | dids | pids | fault_matrix
_CSV_EXPECTATIONS: tuple[tuple[str, str, str], ...] = (
    ("dtc_database.csv", "dtc_database.json", "one_to_one"),
    ("uds_did_database.csv", "uds_did_database.json", "dids"),
    ("extended_pid_database.csv", "extended_pid_database.json", "pids"),
    ("j1939_spn_fmi_database.csv", "j1939_spn_fmi_database.json", "fault_matrix"),
    ("nhtsa_can_recalls_database.csv", "nhtsa_can_recalls_database.json", "one_to_one"),
)


def _csv_profile(path: Path) -> dict[str, Any]:
    filled = blank = ragged = 0
    widths: Counter[int] = Counter()
    header_len = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        header_len = len(header) if header else 0
        for row in reader:
            widths[len(row)] += 1
            if any(cell.strip() for cell in row):
                filled += 1
            else:
                blank += 1
            if header_len and len(row) != header_len:
                ragged += 1
    return {"filled": filled, "blank": blank, "ragged": ragged, "header_len": header_len,
            "widths": dict(widths)}


def _expected_rows(key: str) -> int | None:
    try:
        if key == "dids":
            return len(_load_json(DIAG_DIR / "uds_did_database.json")["dids"])
        if key == "pids":
            return len(_load_json(DIAG_DIR / "extended_pid_database.json")["pids"])
        if key == "fault_matrix":
            spns = _load_json(DIAG_DIR / "j1939_spn_fmi_database.json")["spns"]
            return sum(len(entry.get("fault_matrix") or {}) for entry in spns.values())
    except (OSError, KeyError, json.JSONDecodeError):
        return None
    return None


def check_csv_twins(findings: list[dict[str, str]]) -> dict[str, Any]:
    measured: dict[str, Any] = {}
    for csv_name, json_name, key in _CSV_EXPECTATIONS:
        csv_path = DIAG_DIR / csv_name
        json_path = DIAG_DIR / json_name
        if not csv_path.exists():
            findings.append(_finding("csv_twins", "FAIL", f"{csv_name} yok", str(csv_path.relative_to(ROOT))))
            continue
        profile = _csv_profile(csv_path)
        if key == "one_to_one":
            expected = len(_load_json(json_path)) if json_path.exists() else None
        else:
            expected = _expected_rows(key)
        measured[csv_name] = {**profile, "expected": expected, "md5": _md5(csv_path)}
        if profile["filled"] == 0:
            findings.append(_finding(
                "csv_twins", "FAIL",
                f"{csv_name}: {profile['blank']} satırın TAMAMI boş (başlık dışında veri yok)",
                f"{csv_path.relative_to(ROOT)} md5={measured[csv_name]['md5']}",
            ))
        elif profile["blank"] > 0:
            findings.append(_finding(
                "csv_twins", "WARN",
                f"{csv_name}: {profile['blank']} boş satır / {profile['filled']} dolu satır",
                str(csv_path.relative_to(ROOT)),
            ))
        if profile["ragged"] > 0:
            findings.append(_finding(
                "csv_twins", "FAIL",
                f"{csv_name}: {profile['ragged']} satırda kolon sayısı başlıktan ({profile['header_len']}) farklı",
                str(csv_path.relative_to(ROOT)),
            ))
        if expected is not None and profile["filled"] != expected:
            findings.append(_finding(
                "csv_twins", "FAIL",
                f"{csv_name}: {profile['filled']} dolu satır ama {json_name} {expected} kayıt bekliyor",
                f"{csv_path.relative_to(ROOT)} <-> {json_path.relative_to(ROOT)}",
            ))
    return measured
# --------------------------------------------------------------------------
# 4 — DBC kataloğu
# --------------------------------------------------------------------------
def check_dbc_catalog(findings: list[dict[str, str]]) -> dict[str, Any]:
    catalog = _load_json(DBC_DIR / "catalog.json")
    listed: set[str] = set()
    sha_bad = size_bad = missing = 0
    declared_files = declared_messages = declared_signals = 0
    for category, meta in catalog.get("categories", {}).items():
        if len(meta.get("files", [])) != meta.get("files_count"):
            findings.append(_finding(
                "dbc_catalog", "FAIL",
                f"{category}: files_count={meta.get('files_count')} ama liste {len(meta.get('files', []))} kayıt",
                "data/dbc/catalog.json",
            ))
        for entry in meta.get("files", []):
            listed.add(entry["filename"])
            declared_files += 1
            declared_messages += int(entry.get("messages_count") or 0)
            declared_signals += int(entry.get("signals_count") or 0)
            target = DBC_DIR / category / entry["filename"]
            if not target.exists():
                missing += 1
                findings.append(_finding("dbc_catalog", "FAIL",
                                         f"{category}/{entry['filename']} diskte yok",
                                         str(target.relative_to(ROOT))))
                continue
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if entry.get("sha256") and digest != entry["sha256"]:
                sha_bad += 1
                findings.append(_finding("dbc_catalog", "FAIL",
                                         f"{entry['filename']}: sha256 uyuşmuyor",
                                         f"bildirilen={entry['sha256'][:12]}… gerçek={digest[:12]}…"))
            if entry.get("size_bytes") and target.stat().st_size != entry["size_bytes"]:
                size_bad += 1
                findings.append(_finding("dbc_catalog", "FAIL",
                                         f"{entry['filename']}: boyut {target.stat().st_size} != {entry['size_bytes']}",
                                         str(target.relative_to(ROOT))))

    on_disk = {p.name for p in DBC_DIR.rglob("*.dbc")}
    unlisted = sorted(on_disk - listed)
    unlisted_singular = [name for name in unlisted if not name.startswith("_")]
    if unlisted_singular:
        findings.append(_finding(
            "dbc_catalog", "WARN",
            f"{len(unlisted_singular)} tekil DBC dosyası katalogda kayıtlı değil (bunlar dahil: "
            f"{', '.join(unlisted_singular[:6])})",
            "data/dbc/catalog.json",
        ))
    totals = [(catalog.get("total_files"), declared_files),
              (catalog.get("total_messages"), declared_messages),
              (catalog.get("total_signals"), declared_signals)]
    for declared, computed in totals:
        if declared != computed:
            findings.append(_finding("dbc_catalog", "FAIL",
                                     f"katalog toplamı {declared} != hesaplanan {computed}",
                                     "data/dbc/catalog.json"))
    return {
        "catalog_files": catalog.get("total_files"),
        "resolved": declared_files - missing,
        "missing": missing,
        "sha_mismatch": sha_bad,
        "size_mismatch": size_bad,
        "dbc_on_disk": len(on_disk),
        "unlisted": len(unlisted),
        "unlisted_singular": unlisted_singular,
    }
# --------------------------------------------------------------------------
# 5 — Golden-Traces vakaları (üretim yükleyicisi ile)
# --------------------------------------------------------------------------
def check_golden_cases(findings: list[dict[str, str]]) -> dict[str, Any]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from src.engine.ai.golden_cases import (
            GoldenCaseError,
            calibration_eligible_cases,
            load_all_cases,
        )
    except Exception as exc:  # import zinciri bozulursa denetim görünür kalsın
        findings.append(_finding("golden_cases", "WARN",
                                 f"yükleyici içe aktarılamadı, şema denetimi atlandı: {exc}",
                                 "src/engine/ai/golden_cases.py"))
        return {"loader": False, "cases": len([p for p in CASES_DIR.glob("*.json") if p.name != "schema.json"])}

    files = [p for p in CASES_DIR.glob("*.json") if p.name != "schema.json"]
    try:
        cases = load_all_cases(CASES_DIR)
        eligible = calibration_eligible_cases(CASES_DIR)
    except GoldenCaseError as exc:
        findings.append(_finding("golden_cases", "FAIL", f"vaka şema ihlali: {exc}",
                                 str(CASES_DIR.relative_to(ROOT))))
        return {"loader": True, "cases": len(files), "valid": 0}

    no_trace = [case.case_id for case in cases if not case.trace_ref]
    if len(cases) != len(files):
        findings.append(_finding("golden_cases", "FAIL",
                                 f"{len(files)} dosyadan {len(cases)} vaka yüklendi",
                                 str(CASES_DIR.relative_to(ROOT))))
    if cases and not eligible:
        findings.append(_finding("golden_cases", "WARN", "kalibrasyon uygun vaka yok (hepsi taslak)",
                                 str(CASES_DIR.relative_to(ROOT))))
    if no_trace:
        findings.append(_finding(
            "golden_cases", "INFO",
            f"{len(no_trace)}/{len(cases)} vakada trace_ref boş (canlı kayıt kanıtı yok)",
            f"örnek: {', '.join(no_trace[:3])}",
        ))
    return {
        "loader": True,
        "cases": len(cases),
        "valid": len(cases),
        "calibration_eligible": len(eligible),
        "drafts": [case.case_id for case in cases if case.is_draft],
        "domains": dict(Counter(case.domain.value for case in cases)),
        "without_trace_ref": len(no_trace),
    }
# --------------------------------------------------------------------------
# 6 — data/knowledge/dtc_procedures
# --------------------------------------------------------------------------
def check_knowledge_procedures(findings: list[dict[str, str]]) -> dict[str, Any]:
    dtc_db = _load_json(DIAG_DIR / "dtc_database.json")
    spn_ids = {entry.get("spn") for entry in _load_json(DIAG_DIR / "j1939_spn_fmi_database.json")["spns"].values()}
    parse_bad: list[str] = []
    orphan: list[str] = []
    name_mismatch: list[str] = []
    incomplete: list[str] = []
    files = sorted(PROC_DIR.glob("*.json"))
    for path in files:
        try:
            payload = _load_json(path)
        except (OSError, json.JSONDecodeError):
            parse_bad.append(path.name)
            continue
        stem = path.stem
        declared = str(payload.get("dtc") or "").replace(" ", "_")
        if declared != stem:
            name_mismatch.append(f"{stem}(dtc={payload.get('dtc')})")
        if stem.startswith("SPN_"):
            parts = stem.split("_")
            mapped = len(parts) == 2 and parts[1].isdigit() and int(parts[1]) in spn_ids
        else:
            mapped = stem in dtc_db
        if not mapped:
            orphan.append(stem)
        if not (payload.get("symptoms") and payload.get("measurement_steps")):
            incomplete.append(stem)
    buckets = (("ayrıştırılamayan", parse_bad, "FAIL"),
               ("yetim (DB'de kaydı yok)", orphan, "WARN"),
               ("dosya adı/dtc alanı uyuşmaz", name_mismatch, "FAIL"),
               ("symptoms/measurement_steps eksik", incomplete, "FAIL"))
    for label, bucket, severity in buckets:
        if bucket:
            findings.append(_finding("knowledge_procedures", severity,
                                     f"{len(bucket)} dosya: {label}",
                                     f"örnek: {', '.join(bucket[:5])} | {PROC_DIR.relative_to(ROOT)}"))
    return {"files": len(files), "parse_errors": len(parse_bad), "orphans": len(orphan),
            "name_mismatch": len(name_mismatch), "incomplete": len(incomplete)}


# --------------------------------------------------------------------------
# 7 — Kırpma imzası (sessiz veri kaybı)
# --------------------------------------------------------------------------
def _truncation_marker(text: str) -> str | None:
    """Kırpık metnin metinsel izi (tam 400/403 uzunluk tek başına kanıt değildir)."""
    stripped = text.rstrip()
    if not stripped:
        return None
    if stripped.endswith("&"):
        return "kesik HTML entity (&)"
    if stripped.count('"') % 2 == 1:
        return "kapanmamış tırnak"
    if _STEP_V_RE.search(stripped):
        return "Step V işareti"
    if stripped[-1] not in _TERMINAL_CHARS:
        return "cümle ortasında bitiyor"
    return None


def check_truncation_signature(findings: list[dict[str, str]]) -> dict[str, Any]:
    hits: list[str] = []
    flagged: list[str] = []
    j1939 = _load_json(DIAG_DIR / "j1939_spn_fmi_database.json")["spns"]
    for key, entry in j1939.items():
        buckets: list[tuple[str, object]] = [("steps", entry.get("steps")), ("causes", entry.get("causes"))]
        for proc in entry.get("procedures_full") or []:
            if isinstance(proc, dict):
                buckets.append(("procedures_full.steps", proc.get("steps")))
                buckets.append(("procedures_full.possible_causes", proc.get("possible_causes")))
        for bucket_name, value in buckets:
            if not isinstance(value, list):
                continue
            for text in value:
                if not isinstance(text, str) or len(text) not in TRUNCATION_CAPS:
                    continue
                marker = _truncation_marker(text)
                hits.append(f"{key}.{bucket_name}[{len(text)}]")
                if marker:
                    flagged.append(f"{key}.{bucket_name}[{len(text)}] -> {marker}")
    dtc = _load_json(DIAG_DIR / "dtc_database.json")
    for code, record in dtc.items():
        for bucket_name in ("steps", "causes", "symptoms"):
            value = record.get(bucket_name)
            if not isinstance(value, list):
                continue
            for text in value:
                if not isinstance(text, str) or len(text) not in TRUNCATION_CAPS:
                    continue
                marker = _truncation_marker(text)
                hits.append(f"{code}.{bucket_name}[{len(text)}]")
                if marker:
                    flagged.append(f"{code}.{bucket_name}[{len(text)}] -> {marker}")

    if flagged:
        findings.append(_finding(
            "truncation_signature", "FAIL",
            f"{len(flagged)} metin kırpık (uzunluk {TRUNCATION_CAPS} + metinsel iz); tam metin "
            "kurtarılmadan ÇEVİRİLMEZ/MERGE EDİLMEZ",
            f"örnek: {'; '.join(flagged[:5])}",
        ))
    ambiguous = len(hits) - len(flagged)
    if ambiguous:
        findings.append(_finding(
            "truncation_signature", "WARN",
            f"{ambiguous}/{len(hits)} aday tam {TRUNCATION_CAPS} uzunlukta ama metinsel kırpma izi yok "
            "(belirsiz — elle doğrulanmalı)",
            f"örnek: {', '.join([h for h in hits if h not in {f.split(' -> ')[0] for f in flagged}][:5])}",
        ))
    return {"cap_length_candidates": len(hits), "flagged": flagged, "caps": list(TRUNCATION_CAPS)}
# --------------------------------------------------------------------------
# 8 — fault_matrix başlık bütünlüğü
# --------------------------------------------------------------------------
def check_fault_matrix_titles(findings: list[dict[str, str]]) -> dict[str, Any]:
    spns = _load_json(DIAG_DIR / "j1939_spn_fmi_database.json")["spns"]
    missing: list[str] = []
    total = 0
    for key, entry in spns.items():
        for fmi, cell in (entry.get("fault_matrix") or {}).items():
            if not isinstance(cell, dict):
                continue
            total += 1
            if not (cell.get("fault_title") or "").strip():
                missing.append(f"{key}/FMI {fmi}")
    if missing:
        findings.append(_finding(
            "fault_matrix_titles", "WARN",
            f"{len(missing)}/{total} FMI satırında fault_title boş (CSV 'Başlık (TR)' kolonu boş kalır; "
            "uydurma yapılmaz, kaynaktan doğrulanmalı)",
            f"örnek: {', '.join(missing[:5])}",
        ))
    return {"fault_matrix_rows": total, "missing_title": len(missing), "samples": missing[:10]}


# --------------------------------------------------------------------------
# Kapsam metrikleri (INFO) + rapor
# --------------------------------------------------------------------------
def _coverage() -> dict[str, Any]:
    def nonempty(value: object) -> bool:
        if value is None:
            return False
        if isinstance(value, (list, dict, str)):
            return len(value) > 0
        return True

    spns = _load_json(DIAG_DIR / "j1939_spn_fmi_database.json")["spns"]
    dtc = _load_json(DIAG_DIR / "dtc_database.json")
    j1939_fields = ("title_tr", "description", "causes", "steps", "procedures_full", "fault_matrix")
    dtc_fields = ("causes", "steps", "symptoms", "measurement", "source", "evidence_url")
    return {
        "j1939_total": len(spns),
        "j1939": {f: sum(1 for v in spns.values() if nonempty(v.get(f))) for f in j1939_fields},
        "dtc_total": len(dtc),
        "dtc": {f: sum(1 for v in dtc.values() if nonempty(v.get(f))) for f in dtc_fields},
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = [
        f"# data/ Bütünlük Denetimi — {report['generated']}",
        "",
        "> `python scripts/data_integrity_audit.py` çıktısı. Offline, ağ yok; "
        "her bulgu dosya yolu ve ölçülen sayı ile birlikte verilir.",
        "",
        f"- Bulgu: **FAIL {report['severity_counts'].get('FAIL', 0)}** · "
        f"WARN {report['severity_counts'].get('WARN', 0)} · "
        f"INFO {report['severity_counts'].get('INFO', 0)}",
        "",
        "## Bulgular",
        "",
    ]
    if report["findings"]:
        lines += ["| Önem | Denetim | Bulgu | Kanıt |", "|---|---|---|---|"]
        for f in report["findings"]:
            lines.append(f"| {f['severity']} | {f['check']} | {f['detail']} | `{f['evidence']}` |")
    else:
        lines.append("Bulgu yok.")
    lines += ["", "## Kapsam (INFO — bilgi tabanı doluluk oranları)", "",
              "| Küme | Alan | Dolu | Toplam | Oran |", "|---|---|---|---|---|"]
    cov = report["coverage"]
    for field, value in cov["j1939"].items():
        lines.append(f"| J1939 | {field} | {value} | {cov['j1939_total']} | "
                     f"{100 * value / cov['j1939_total']:.1f}% |")
    for field, value in cov["dtc"].items():
        lines.append(f"| DTC | {field} | {value} | {cov['dtc_total']} | "
                     f"{100 * value / cov['dtc_total']:.1f}% |")
    lines += ["", "## Ölçümler", "", "```json",
              json.dumps(report["measurements"], ensure_ascii=False, indent=1), "```", ""]
    return "\n".join(lines)


def run(write: bool) -> int:
    findings: list[dict[str, str]] = []
    measurements: dict[str, Any] = {}
    counts = check_json_assets(findings)
    measurements["json_asset_count"] = len(counts)
    measurements["metadata_totals"] = check_metadata_totals(findings)
    measurements["csv_twins"] = check_csv_twins(findings)
    measurements["dbc_catalog"] = check_dbc_catalog(findings)
    measurements["golden_cases"] = check_golden_cases(findings)
    measurements["knowledge_procedures"] = check_knowledge_procedures(findings)
    measurements["truncation_signature"] = check_truncation_signature(findings)
    measurements["fault_matrix_titles"] = check_fault_matrix_titles(findings)

    severity_counts = dict(Counter(f["severity"] for f in findings))
    report = {
        "generated": date.today().isoformat(),
        "severity_counts": severity_counts,
        "findings": findings,
        "measurements": measurements,
        "coverage": _coverage(),
    }

    for finding in findings:
        print(f"[{finding['severity']}] {finding['check']}: {finding['detail']}  ({finding['evidence']})")
    tail = ", ".join(f"{k}={v}" for k, v in sorted(severity_counts.items())) or "bulgu yok"
    print(f"\nDENETIM SONUCU: {tail}  ({len(counts)} JSON varlığı tarandı)")

    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        stem = f"data_integrity_{report['generated']}"
        (REPORT_DIR / f"{stem}.md").write_text(_render_markdown(report), encoding="utf-8")
        (REPORT_DIR / f"{stem}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Rapor: docs/audit/{stem}.md")

    return 1 if severity_counts.get("FAIL") else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit data/ integrity (offline)")
    parser.add_argument("--no-write", action="store_true", help="Write nothing under docs/audit")
    args = parser.parse_args()
    return run(write=not args.no_write)


if __name__ == "__main__":
    raise SystemExit(main())
