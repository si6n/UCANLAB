"""T46-A regression tests — English DTC harvest merged into the local DB.

Task: kanban t_edda7bef (T46-A).

The merge (`scripts/merge_t45_dtc.py`) folds the T45-verified English scrape
(troublecodes.net 617 rows / obdhut 4515 rows) into `data/diagnostics/dtc_database.json`
as PARALLEL `*_en` fields, because the DB's own `symptoms` / `causes` / etc. are
Turkish and must never receive English scrape text.

Invariants locked here (from the task body — violation = task rejection):
  1. Record count is frozen (14,352). The merge never opens a new code.
  2. Idempotent: a second run produces byte-identical output.
  3. English payload lives ONLY under `_en`-suffixed fields; the Turkish
     `symptoms` field never gains pure-ASCII (English) text.
  4. No new `*_en` field is written without a matching `*_en_source` URL.

The tests exercise a synthetic minimal DB so they stay fast and independent of
the production file, plus one read-only assertion against the production DB.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "merge_t45_dtc.py"
PROD_DB_PATH = REPO_ROOT / "data" / "diagnostics" / "dtc_database.json"
TCN_INPUT = REPO_ROOT / "spn_gap_hunter" / "output" / "t45c_tcn_symptoms.json"
OBDHUT_INPUT = REPO_ROOT / "spn_gap_hunter" / "output" / "t45c_dtc_symptoms.json"

EN_FIELDS = ("symptoms_en", "causes_en", "description_en", "title_en")
EN_SOURCE_FIELDS = {
    "symptoms_en": "symptoms_en_source",
    "causes_en": "causes_en_source",
    "description_en": "description_en_source",
    "title_en": "title_en_source",
}

# Value the merge script stamps on every row it touched (idempotency anchor).
MERGE_MARKER_VALUE = "t46a"


def _load_merge_module() -> Any:
    """Import `scripts/merge_t45_dtc.py` without package-relative plumbing."""
    spec = importlib.util.spec_from_file_location("merge_t45_dtc", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["merge_t45_dtc"] = module
    spec.loader.exec_module(module)
    return module


merge_mod = _load_merge_module()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_raw(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    assert isinstance(data, list)
    return data


@pytest.fixture(scope="module")
def harvest_codes() -> set[str]:
    """Every DTC code the two T45 harvest files deliver (uppercased)."""
    codes: set[str] = set()
    for path in (TCN_INPUT, OBDHUT_INPUT):
        for row in _load_raw(path):
            codes.add(str(row["code"]).strip().upper())
    return codes


@pytest.fixture()
def synthetic_db(tmp_path: Path, harvest_codes: set[str]) -> Path:
    """A miniature DB containing the real harvest codes plus one untouched code.

    Entries are minimal but shape-valid (`title`/`subsystem`/`severity`/`steps`/
    `causes`), mirroring the production schema, so the merge script sees a
    realistic target without loading the 22 MB production file.
    """
    db: dict[str, Any] = {}
    for code in sorted(harvest_codes):
        db[code] = {
            "title": "Örnek arıza başlığı",
            "subsystem": "Örnek alt sistem",
            "severity": "MEDIUM",
            "causes": ["Türkçe neden"],
            "steps": [["Türkçe adım", "Bileşen", "Kolay (Görsel)"]],
            "symptoms": ["Motor çekişinde düşüklük"],
        }
    # A code that the harvest does NOT cover: the merge must leave it alone.
    db["P9999"] = {
        "title": "Kapsam dışı kod",
        "subsystem": "Test",
        "severity": "LOW",
        "causes": ["Türkçe neden"],
        "steps": [],
        "symptoms": ["Kapsam dışı semptom"],
    }
    path = tmp_path / "dtc_database.json"
    path.write_text(json.dumps(db, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def _merge(db_path: Path) -> dict[str, int]:
    return merge_mod.merge(db_path)


class TestRecordCountFrozen:
    def test_record_count_unchanged(self, synthetic_db: Path) -> None:
        before = len(json.loads(synthetic_db.read_text(encoding="utf-8")))
        stats = _merge(synthetic_db)
        after = len(json.loads(synthetic_db.read_text(encoding="utf-8")))
        assert after == before
        assert stats["db_records_before"] == stats["db_records_after"] == before
        assert stats["skipped_unknown_code"] == 0

    def test_unknown_codes_are_skipped_not_added(self, synthetic_db: Path) -> None:
        """A harvest code absent from the DB is counted and dropped."""
        db = json.loads(synthetic_db.read_text(encoding="utf-8"))
        known = next(iter(db))
        db = {known: db[known]}
        synthetic_db.write_text(json.dumps(db, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

        stats = _merge(synthetic_db)
        result = json.loads(synthetic_db.read_text(encoding="utf-8"))
        assert len(result) == 1
        assert stats["skipped_unknown_code"] > 0
        assert stats["db_records_after"] == 1


class TestIdempotent:
    def test_second_run_is_byte_identical(self, synthetic_db: Path) -> None:
        _merge(synthetic_db)
        first_digest = _sha256(synthetic_db)
        stats = _merge(synthetic_db)
        assert _sha256(synthetic_db) == first_digest
        # The second pass writes nothing new: every *_en field is already set.
        assert stats["fields_written"] == 0
        assert stats["records_enriched"] == 0


class TestEnglishFieldsAreParallel:
    def test_english_payload_lands_in_en_fields(self, synthetic_db: Path) -> None:
        stats = _merge(synthetic_db)
        db = json.loads(synthetic_db.read_text(encoding="utf-8"))

        assert stats["enriched_symptoms_en"] > 0
        assert stats["enriched_causes_en"] > 0
        assert stats["enriched_description_en"] > 0

        # troublecodes.net rows carry real symptoms -> symptoms_en populated.
        p0005 = db["P0005"]
        assert isinstance(p0005["symptoms_en"], str) and p0005["symptoms_en"]
        assert p0005["symptoms_en_source"].startswith("https://")
        assert p0005["causes_en_source"].startswith("https://")

    def test_turkish_symptoms_never_gain_english_text(self, synthetic_db: Path) -> None:
        """The Turkish `symptoms` list must stay non-ASCII after the merge.

        An English scrape leak would leave a pure-ASCII (or `_en`-shaped)
        value behind; both are rejected here.
        """
        _merge(synthetic_db)
        db = json.loads(synthetic_db.read_text(encoding="utf-8"))
        for code, rec in db.items():
            symptoms = rec.get("symptoms")
            if isinstance(symptoms, list) and symptoms:
                assert merge_mod.has_turkish_text(symptoms), (
                    f"{code}: Turkish symptoms field carries no non-ASCII char — "
                    "English text may have leaked in"
                )

    def test_turkish_fields_untouched(self, synthetic_db: Path) -> None:
        """No-overwrite rule: pre-filled Turkish fields survive verbatim."""
        _merge(synthetic_db)
        db = json.loads(synthetic_db.read_text(encoding="utf-8"))
        for rec in db.values():
            if "Türkçe neden" in rec["causes"]:
                assert rec["causes"] == ["Türkçe neden"]
            assert rec["title"].startswith("Örnek") or rec["title"] == "Kapsam dışı kod"

    def test_uncovered_code_untouched(self, synthetic_db: Path) -> None:
        _merge(synthetic_db)
        db = json.loads(synthetic_db.read_text(encoding="utf-8"))
        p9999 = db["P9999"]
        for field in EN_FIELDS:
            assert field not in p9999


class TestProvenance:
    def test_every_en_field_has_a_source_url(self, synthetic_db: Path) -> None:
        _merge(synthetic_db)
        db = json.loads(synthetic_db.read_text(encoding="utf-8"))
        for code, rec in db.items():
            for en_field, source_field in EN_SOURCE_FIELDS.items():
                if en_field in rec:
                    source = rec.get(source_field)
                    assert isinstance(source, str) and source.startswith("https://"), (
                        f"{code}: {en_field} present without a usable {source_field}"
                    )

    def test_record_without_source_is_skipped(self, tmp_path: Path) -> None:
        """A harvest row with no source URL is never merged (rule 3)."""
        db = {
            "P0005": {
                "title": "TR başlık",
                "subsystem": "TR",
                "severity": "MEDIUM",
                "causes": ["Türkçe neden"],
                "steps": [],
                "symptoms": ["Türkçe semptom"],
            }
        }
        path = tmp_path / "db_nosource.json"
        path.write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")

        rows = merge_mod._harvest_rows(_load_raw(TCN_INPUT))
        stats = {k: 0 for k in ("skipped_unknown_code", "skipped_no_source")}
        stats["skipped_unknown_code"] = 0
        stats["skipped_no_source"] = 0
        rows[0]["source_url"] = ""  # hostile/no-source row

        candidates = merge_mod._collect_candidates([rows[0]], db, stats)
        assert stats["skipped_no_source"] == 1
        assert candidates.get("P0005", {}) == {}

    def test_is_usable_source_rejects_blank(self) -> None:
        assert merge_mod._is_usable_source("https://obdhut.com/codes/p0005")
        assert not merge_mod._is_usable_source("")
        assert not merge_mod._is_usable_source("   ")
        assert not merge_mod._is_usable_source("not-a-url")
        assert not merge_mod._is_usable_source(None)


class TestProductionDatabase:
    """Read-only assertions against the production DB (no writes)."""

    def test_database_exists_with_expected_count(self) -> None:
        assert PROD_DB_PATH.exists(), "production DTC database is missing"
        with PROD_DB_PATH.open(encoding="utf-8") as fh:
            db = json.load(fh)
        assert len(db) == 14352

    def test_english_fields_are_parallel_and_sourced(self) -> None:
        with PROD_DB_PATH.open(encoding="utf-8") as fh:
            db = json.load(fh)
        seen_en = 0
        for code, rec in db.items():
            for en_field, source_field in EN_SOURCE_FIELDS.items():
                if en_field in rec:
                    seen_en += 1
                    assert isinstance(rec.get(source_field), str), (
                        f"{code}: {en_field} without {source_field}"
                    )
                    assert rec[source_field].startswith("https://")
        # The T46 merge must have enriched a substantial slice of the DB.
        assert seen_en > 0, "T46 merge has not been applied to the production DB"

    def test_turkish_symptoms_stay_turkish(self) -> None:
        """No 100% English symptoms list may appear on a T46-touched record."""
        with PROD_DB_PATH.open(encoding="utf-8") as fh:
            db = json.load(fh)
        for code, rec in db.items():
            if merge_mod.MERGE_MARKER not in rec:
                continue  # untouched row — its Turkish set was never English
            symptoms = rec.get("symptoms")
            if isinstance(symptoms, list) and symptoms:
                assert merge_mod.has_turkish_text(symptoms), (
                    f"{code}: T46-touched row has a pure-ASCII `symptoms` list — "
                    "the English scrape leaked into the Turkish field"
                )

    def test_merge_adds_only_en_and_source_keys(self, synthetic_db: Path) -> None:
        """Diff key sets before/after: only `_en` / `_en_source` / marker appear."""
        before = json.loads(synthetic_db.read_text(encoding="utf-8"))
        before_keys = {code: set(rec) for code, rec in before.items()}
        _merge(synthetic_db)
        after = json.loads(synthetic_db.read_text(encoding="utf-8"))

        allowed = set(EN_FIELDS) | set(EN_SOURCE_FIELDS.values()) | {merge_mod.MERGE_MARKER}
        added: set[str] = set()
        for code, rec in after.items():
            added |= set(rec) - before_keys.get(code, set())
        assert added <= allowed, f"merge added unexpected keys: {sorted(added - allowed)}"
        # The Turkish field names are never among the added keys.
        assert not (added & {"symptoms", "causes", "title", "description", "meaning"})
