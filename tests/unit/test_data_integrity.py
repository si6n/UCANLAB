"""data/ bütünlük bekçisi (regresyon kapısı) — sessiz veri kaybına karşı CI testi.

Neden var:
    `f8bb892` commit'i (2026-09-14) `dtc_database.csv` (14.166), `j1939_spn_fmi_database.csv`
    (3.710) ve `uds_did_database.csv` (68) dosyalarının VERİ satırlarını boşalttı —
    yalnız başlık kaldı. Hiçbir test CSV içeriğini doğrulamadığı için bu sessiz veri
    kaybı depoya girdi. Bu modül o sınıf hatayı CI'da durdurur.

Kanıt/rapor: docs/audit/data_integrity_2026-09-19.md
    python scripts/data_integrity_audit.py     (tam denetim)
    python scripts/rebuild_csv_exports.py      (CSV ikizlerini JSON'dan yeniden üretir)

Kapsam:
    1. CSV ikizlerinde boş/tutanaksız (ragged) satır yok
    2. CSV satır sayısı JSON kayıt sayısına eşit
    3. metadata toplamları gerçek kayıtlarla uyuşuyor
    4. DBC kataloğu: varlık + sha256 + boyut
    5. Golden-Traces: üretim yükleyicisinden geçiyor
    6. knowledge/dtc_procedures: yetimsiz, ad/alan tutarlı
    7. Kırpma imzası ve FMI başlık boşluğu baseline'ı AŞMIYOR (ratchet)
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
DIAG_DIR = DATA_DIR / "diagnostics"
DBC_DIR = DATA_DIR / "dbc"
PROC_DIR = DATA_DIR / "knowledge" / "dtc_procedures"
CASES_DIR = DATA_DIR / "golden_traces" / "cases"

# Bu sayılar 2026-09-19 denetiminde ölçüldü; kırpık ve boş başlıklar temizlendi (ratchet = 0).
TRUNCATION_BASELINE = 0          # Kırpık metin kalmadı (SPN_3364, SPN_629, SPN_1231 onarıldı)
FAULT_TITLE_BASELINE = 0         # fault_matrix satırlarında boş fault_title kalmadı (18 satır onarıldı)

# CSV ikizi -> (JSON, beklenen satır kuralı)
CSV_PAIRS: dict[str, tuple[str, str]] = {
    "dtc_database.csv": ("dtc_database.json", "one_to_one"),
    "uds_did_database.csv": ("uds_did_database.json", "dids"),
    "extended_pid_database.csv": ("extended_pid_database.json", "pids"),
    "j1939_spn_fmi_database.csv": ("j1939_spn_fmi_database.json", "fault_matrix"),
    "nhtsa_can_recalls_database.csv": ("nhtsa_can_recalls_database.json", "one_to_one"),
    "obd_mode06_database.csv": ("obd_mode06_database.json", "mode06_tests"),
    "canonical_symptoms.csv": ("canonical_symptoms.json", "symptoms"),
}

# j1939 CSV düzeni = scripts/expand_j1939_spn_database.py'nin ürettiği FMI-başına düzen
# (kanıt: git f80eb36:data/diagnostics/j1939_spn_fmi_database.csv başlığı).
J1939_CSV_COLUMNS = 8


@lru_cache(maxsize=8)
def _load(name: str) -> Any:
    return json.loads((DIAG_DIR / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=8)
def _csv_rows(name: str) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    with (DIAG_DIR / name).open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    return tuple(rows[0]), tuple(tuple(row) for row in rows[1:])


def _expected_rows(json_name: str, rule: str) -> int:
    if rule == "dids":
        return len(_load("uds_did_database.json")["dids"])
    if rule == "pids":
        return len(_load("extended_pid_database.json")["pids"])
    if rule == "fault_matrix":
        spns = _load("j1939_spn_fmi_database.json")["spns"]
        return sum(len(entry.get("fault_matrix") or {}) for entry in spns.values())
    if rule == "mode06_tests":
        data = _load("obd_mode06_database.json")
        monitors = data.get("monitors", {})
        c2 = data.get("class2_monitors", {})
        m_tests = sum(len(m.get("tests") or []) for m in monitors.values())
        c2_tests = sum(len(m.get("tests") or []) for m in c2.values()) if isinstance(c2, dict) else 0
        return m_tests + c2_tests
    if rule == "symptoms":
        return len(_load("canonical_symptoms.json").get("symptoms", {}))
    return len(_load(json_name))
@pytest.mark.parametrize("csv_name", sorted(CSV_PAIRS))
def test_csv_twin_has_no_hollow_rows(csv_name: str) -> None:
    """F8BB892 gerilemesi: başlık dışında hiç veri satırı olmaması."""
    _, data_rows = _csv_rows(csv_name)
    filled = [row for row in data_rows if any(cell.strip() for cell in row)]
    blank = len(data_rows) - len(filled)
    assert data_rows, f"{csv_name}: hiç veri satırı yok"
    assert blank == 0, f"{csv_name}: {blank} boş satır (sessiz veri kaybı)"


@pytest.mark.parametrize("csv_name", sorted(CSV_PAIRS))
def test_csv_twin_row_count_matches_json(csv_name: str) -> None:
    """CSV, JSON bilgi tabanının ikizi olmalı — satır sayısı JSON kaydına eşit."""
    json_name, rule = CSV_PAIRS[csv_name]
    _, data_rows = _csv_rows(csv_name)
    expected = _expected_rows(json_name, rule)
    assert len(data_rows) == expected, (
        f"{csv_name}: {len(data_rows)} satır ama {json_name} {expected} kayıt bekliyor "
        "(python scripts/rebuild_csv_exports.py ile yeniden üretin)"
    )


@pytest.mark.parametrize("csv_name", sorted(CSV_PAIRS))
def test_csv_twin_rows_are_not_ragged(csv_name: str) -> None:
    """Her satırın kolon sayısı başlıkla aynı olmalı (8-in-9 kolon sınıfı hata)."""
    header, data_rows = _csv_rows(csv_name)
    widths = Counter(len(row) for row in data_rows)
    assert set(widths) == {len(header)}, f"{csv_name}: kolon genişlikleri {dict(widths)}, başlık {len(header)}"


def test_j1939_csv_keeps_generator_layout() -> None:
    """j1939 CSV, in-repo üreticinin (expand_j1939_spn_database.py) kolon düzeninde kalmalı."""
    header, data_rows = _csv_rows("j1939_spn_fmi_database.csv")
    assert len(header) == J1939_CSV_COLUMNS
    assert header[0] == "SPN" and header[1] == "FMI"
    assert all(len(row) == J1939_CSV_COLUMNS for row in data_rows)


def test_metadata_totals_match_records() -> None:
    j1939 = _load("j1939_spn_fmi_database.json")
    uds = _load("uds_did_database.json")
    pids = _load("extended_pid_database.json")
    mode06 = _load("obd_mode06_database.json")
    assert j1939["metadata"]["total_spns"] == len(j1939["spns"])
    assert uds["metadata"]["total_dids"] == len(uds["dids"])
    assert pids["metadata"]["total_pids"] == len(pids["pids"])
    assert mode06["metadata"]["total_monitors"] == len(mode06["monitors"])
def test_dbc_catalog_entries_resolve_with_matching_hash() -> None:
    catalog = json.loads((DBC_DIR / "catalog.json").read_text(encoding="utf-8"))
    checked = 0
    for category, meta in catalog["categories"].items():
        assert len(meta["files"]) == meta["files_count"], f"{category}: files_count tutarsız"
        for entry in meta["files"]:
            target = DBC_DIR / category / entry["filename"]
            assert target.exists(), f"katalog kaydı diskte yok: {target}"
            assert target.stat().st_size == entry["size_bytes"], f"{entry['filename']}: boyut değişti"
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            assert digest == entry["sha256"], f"{entry['filename']}: sha256 değişti"
            checked += 1
    assert checked == catalog["total_files"] >= 70

    # Aksiyon 23: bidirectional disk <-> catalog check
    disk_files = {p.relative_to(DBC_DIR).as_posix() for p in DBC_DIR.rglob("*.dbc")}
    catalog_files = {
        f"{category}/{entry['filename']}"
        for category, meta in catalog["categories"].items()
        for entry in meta["files"]
    }
    assert disk_files == catalog_files, f"DBC katalog↔disk iki yönlü uyumsuzluk: disk-fark={disk_files - catalog_files}, katalog-fark={catalog_files - disk_files}"


def test_golden_cases_load_with_production_validator() -> None:
    from src.engine.ai.golden_cases import calibration_eligible_cases, load_all_cases

    files = [p for p in CASES_DIR.glob("*.json") if p.name != "schema.json"]
    cases = load_all_cases(CASES_DIR)   # şema ihlali -> GoldenCaseError
    eligible = calibration_eligible_cases(CASES_DIR)
    assert len(cases) == len(files) >= 50
    assert len(eligible) >= len(cases) - 1, "en fazla 1 taslak vaka olmalı"
    for case in cases:
        if case.verified:
            assert case.verified_date, f"{case.case_id}: verified=true ama verified_date yok"
            assert case.actual_fault and case.actual_fault.strip()


def test_knowledge_procedures_are_orphan_free_and_consistent() -> None:
    dtc_db = _load("dtc_database.json")
    spn_ids = {entry.get("spn") for entry in _load("j1939_spn_fmi_database.json")["spns"].values()}
    files = sorted(PROC_DIR.glob("*.json"))
    assert files, "data/knowledge/dtc_procedures boş"
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload.get("schema_version") == 1, f"{path.name}: schema_version"
        assert payload.get("symptoms") and payload.get("measurement_steps"), f"{path.name}: içerik eksik"
        assert str(payload.get("dtc") or "").replace(" ", "_") == path.stem, f"{path.name}: dtc alanı uyuşmuyor"
        stem = path.stem
        if stem.startswith("SPN_"):
            parts = stem.split("_")
            assert len(parts) == 2 and int(parts[1]) in spn_ids, f"{path.name}: yetim SPN prosedürü"
        else:
            assert stem in dtc_db, f"{path.name}: yetim DTC prosedürü"


def test_dtc_database_mandatory_autosar_fields() -> None:
    """Aksiyon 12 / Faz 4.2: dtc_namespace ve dtc_class zorunlu alan doğrulaması."""
    from src.core.models.diagnostics import DtcClass, DtcNamespace

    dtc_db = _load("dtc_database.json")
    allowed_ns = {ns.value for ns in DtcNamespace}
    allowed_cls = {c.value for c in DtcClass}
    assert len(dtc_db) >= 14000
    for code, rec in dtc_db.items():
        assert "dtc_namespace" in rec, f"{code}: dtc_namespace alanı eksik"
        assert "dtc_class" in rec, f"{code}: dtc_class alanı eksik"
        assert rec["dtc_namespace"] in allowed_ns, f"{code}: geçersiz namespace {rec['dtc_namespace']}"
        assert rec["dtc_class"] in allowed_cls, f"{code}: geçersiz class {rec['dtc_class']}"


def test_dtc_provenance_schema_integrity() -> None:
    """Aksiyon 14 / Faz 5.1: provenance_schema.json ve dtc_record_schema.json doğrulaması."""
    import jsonschema

    prov_path = DIAG_DIR / "provenance_schema.json"
    dtc_path = DIAG_DIR / "dtc_record_schema.json"
    assert prov_path.exists(), "provenance_schema.json yok"
    assert dtc_path.exists(), "dtc_record_schema.json yok"

    prov_schema = json.loads(prov_path.read_text(encoding="utf-8"))
    dtc_schema = json.loads(dtc_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(prov_schema)
    jsonschema.Draft202012Validator.check_schema(dtc_schema)

    assert "dependentRequired" in dtc_schema
    for f in ("title", "severity", "causes", "steps", "symptoms"):
        assert f in dtc_schema["dependentRequired"], f"{f} dependentRequired içinde yok"
        assert "provenance" in dtc_schema["dependentRequired"][f]


def test_truncation_and_fault_title_gaps_do_not_grow() -> None:
    """Ratchet: kırpık metin ve boş FMI başlığı sayısı baseline'ı aşamaz."""
    import sys

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.data_integrity_audit import check_fault_matrix_titles, check_truncation_signature

    findings: list[dict[str, str]] = []
    truncation = check_truncation_signature(findings)
    titles = check_fault_matrix_titles(findings)
    flagged = len(truncation["flagged"])
    assert flagged <= TRUNCATION_BASELINE, (
        f"yeni kırpık metin: {flagged} > baseline {TRUNCATION_BASELINE} -> {truncation['flagged'][:5]}"
    )
    assert titles["missing_title"] <= FAULT_TITLE_BASELINE, (
        f"boş FMI başlığı arttı: {titles['missing_title']} > {FAULT_TITLE_BASELINE}"
    )
