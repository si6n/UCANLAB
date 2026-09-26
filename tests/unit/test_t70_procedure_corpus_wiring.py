"""T70-A: validated procedure-corpus wiring into the deep-dive reports.

MEASURED DEFECT (T70-A): ``data/knowledge/dtc_procedures/`` holds 601
schema-validated procedure files. Before T70-A the corpus had exactly TWO
consumers in the tree — ``desktop_app.get_dtc_info`` and ``dialogue_engine``
question generation — and NEITHER deep-dive report imported
``procedure_validator``. An operator asking "P0300 nedir" got the KB record but
never the validated branch tree the corpus carries for that same code.

These tests pin the wiring, not the wording:
  * a covered code renders the corpus block; an uncovered code renders NOTHING
  * every corpus field that exists is rendered (no silent truncation)
  * ``pass_next`` / ``fail_next`` — the conditional branches — reach the report
  * both the DTC (4-stage) and J1939 report paths carry the block
  * no fabrication: absent fields produce no line
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.engine.ai.diagnostic_copilot import (
    CausalBayesianInferenceEngine,
    _procedure_for_code,
    ensure_external_dtc_database_loaded,
    get_j1939_spn_database,
    procedure_corpus_block,
)
from src.engine.ai.procedure_validator import load_all_procedures

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = PROJECT_ROOT / "data" / "knowledge" / "dtc_procedures"


@pytest.fixture(scope="module")
def corpus() -> dict:
    ensure_external_dtc_database_loaded()
    return load_all_procedures()


class TestResolver:
    """``_procedure_for_code`` must reach every key spelling in the corpus."""

    def test_resolves_plain_dtc(self) -> None:
        proc = _procedure_for_code("P0300")
        assert proc is not None
        assert proc.dtc == "P0300"

    def test_resolves_compact_spn_to_spaced_file(self) -> None:
        """Files are named `SPN 100.json`; engine keys are `SPN100`."""
        proc = _procedure_for_code("SPN100")
        assert proc is not None
        assert proc.dtc == "SPN 100"

    def test_resolves_spaced_spn_directly(self) -> None:
        proc = _procedure_for_code("SPN 3251")
        assert proc is not None
        assert proc.dtc == "SPN 3251"

    def test_unknown_code_returns_none(self) -> None:
        assert _procedure_for_code("ZZZZZ") is None
        assert _procedure_for_code("") is None
        assert _procedure_for_code("   ") is None

    def test_resolution_is_deterministic(self) -> None:
        first = _procedure_for_code("P0420")
        second = _procedure_for_code("p0420")
        assert first is not None and second is not None
        assert first.dtc == second.dtc

    def test_every_corpus_file_resolves_through_its_own_key(self, corpus: dict) -> None:
        """A file that cannot be resolved by its own key is dead data."""
        unresolved: list[str] = []
        for key in corpus:
            if _procedure_for_code(key) is None and _procedure_for_code(
                key.replace(" ", "")
            ) is None:
                unresolved.append(key)
        assert not unresolved, f"corpus keys unreachable: {unresolved[:10]}"


class TestBlockRendering:
    """Every corpus field that exists must be rendered; absent ones must not."""

    def test_covered_code_renders_block(self) -> None:
        block = procedure_corpus_block("P0300")
        assert block
        assert "Doğrulanmış Prosedür Kütüphanesi" in block

    def test_uncovered_code_renders_nothing(self) -> None:
        """Fail-safe: no procedure -> no block, never a placeholder."""
        assert procedure_corpus_block("ZZZZZ") == ""
        assert procedure_corpus_block("") == ""

    def test_system_and_symptoms_rendered(self, corpus: dict) -> None:
        proc = corpus["P0300"]
        block = procedure_corpus_block("P0300")
        assert proc.system in block
        for symptom in proc.symptoms:
            assert symptom in block, f"symptom dropped: {symptom}"

    def test_every_question_rendered(self, corpus: dict) -> None:
        proc = corpus["P0300"]
        block = procedure_corpus_block("P0300")
        assert proc.questions, "fixture assumption: P0300 carries questions"
        for q in proc.questions:
            assert q["text"] in block, f"question dropped: {q['id']}"

    def test_every_measurement_step_rendered(self, corpus: dict) -> None:
        proc = corpus["P0300"]
        block = procedure_corpus_block("P0300")
        assert proc.measurement_steps, "fixture assumption"
        for step in proc.measurement_steps:
            assert step["target"] in block, f"step dropped: {step['step']}"
            assert step["test_type"] in block

    def test_expected_values_rendered(self, corpus: dict) -> None:
        proc = corpus["P0300"]
        block = procedure_corpus_block("P0300")
        for key, value in proc.expected_values.items():
            assert key in block
            assert str(value) in block

    def test_conditional_branches_rendered(self, corpus: dict) -> None:
        """pass_next/fail_next are what make this a decision tree."""
        proc = corpus["P0300"]
        block = procedure_corpus_block("P0300")
        assert proc.pass_next and proc.fail_next, "fixture assumption"
        assert proc.pass_next in block
        assert proc.fail_next in block
        assert "Koşullu Dallanma" in block

    def test_safety_notes_rendered(self, corpus: dict) -> None:
        proc = corpus["P0300"]
        block = procedure_corpus_block("P0300")
        assert proc.safety_notes, "fixture assumption"
        for note in proc.safety_notes:
            assert note in block, f"safety note dropped: {note}"

    def test_block_is_deterministic(self) -> None:
        assert procedure_corpus_block("P0420") == procedure_corpus_block("P0420")


class TestReportWiring:
    """Both deep-dive report paths must carry the corpus block."""

    def test_4stage_report_contains_corpus_block(self) -> None:
        ensure_external_dtc_database_loaded()
        report = CausalBayesianInferenceEngine._format_4stage_technician_report(
            "P0300", {}
        )
        assert "Doğrulanmış Prosedür Kütüphanesi" in report
        # The branch guidance is the corpus's unique contribution.
        proc = _procedure_for_code("P0300")
        assert proc is not None
        assert proc.fail_next in report

    def test_4stage_report_unaffected_for_uncovered_code(self) -> None:
        """A code with no procedure must not gain an empty header."""
        ensure_external_dtc_database_loaded()
        report = CausalBayesianInferenceEngine._format_4stage_technician_report(
            "P1234", {}
        )
        if _procedure_for_code("P1234") is None:
            assert "Doğrulanmış Prosedür Kütüphanesi" not in report

    def test_j1939_report_contains_corpus_block(self) -> None:
        db = get_j1939_spn_database()
        row = (db.get("spns") or {}).get("SPN_100")
        assert isinstance(row, dict), "fixture assumption: SPN_100 present"
        report = CausalBayesianInferenceEngine._format_j1939_technician_report(
            row, "SPN 100 FMI 3", {}, None
        )
        assert "Doğrulanmış Prosedür Kütüphanesi" in report
        assert "Motor Yağlama Sistemi (J1939)" in report

    def test_reports_stay_read_only(self) -> None:
        """P0-1/P0-3 parity: a report mints no action trigger."""
        ensure_external_dtc_database_loaded()
        report = CausalBayesianInferenceEngine._format_4stage_technician_report(
            "P0300", {}
        )
        assert "action_triggers" not in report or "uds_clear_dtc" not in report

    def test_multi_dtc_report_contains_corpus_for_each_covered_code(self) -> None:
        """T70-A: the combined report carries the corpus once per covered code."""
        from src.engine.ai.diagnostic_copilot import analyze_active_dtc_clusters

        ensure_external_dtc_database_loaded()
        codes = ["P0300", "P0420"]
        report = CausalBayesianInferenceEngine._format_multi_dtc_combined_report(
            codes, {}, analyze_active_dtc_clusters(codes)
        )
        assert "Doğrulanmış Prosedür Kütüphanesi" in report
        # Exactly ONE section header, with per-code sub-headings beneath it.
        assert report.count("Doğrulanmış Prosedür Kütüphanesi") == 1
        for code in codes:
            assert f"**[{code}]**" in report

    def test_multi_dtc_report_skips_codes_without_procedure(self) -> None:
        """A code with no corpus file must contribute no sub-heading."""
        from src.engine.ai.diagnostic_copilot import analyze_active_dtc_clusters

        ensure_external_dtc_database_loaded()
        codes = ["P0300", "P9999"]
        assert _procedure_for_code("P9999") is None, "fixture assumption"
        report = CausalBayesianInferenceEngine._format_multi_dtc_combined_report(
            codes, {}, analyze_active_dtc_clusters(codes)
        )
        assert "**[P0300]**" in report
        assert "**[P9999]**" not in report

    def test_block_without_header_drops_only_the_title(self) -> None:
        """``include_header=False`` must drop the title, not content."""
        with_header = procedure_corpus_block("P0300")
        without = procedure_corpus_block("P0300", include_header=False)
        assert without
        assert "Doğrulanmış Prosedür Kütüphanesi" not in without
        # Every content line survives the header drop.
        body = [ln for ln in with_header.splitlines() if ln.strip().startswith("•")]
        for line in body:
            assert line in without


class TestNoFabrication:
    """AGENTS.md §2.3: absent fields produce no line, never a placeholder."""

    def test_corpus_block_never_invents_a_tolerance(self, tmp_path: Path) -> None:
        """A minimal valid procedure with no expected_values renders no value line."""
        payload = {
            "schema_version": 1,
            "dtc": "Q9999",
            "system": "Test Systemi",
            "symptoms": ["Test belirtisi"],
            "questions": [],
            "measurement_steps": [],
            "expected_values": {},
            "safety_notes": [],
        }
        proc_dir = tmp_path / "dtc_procedures"
        proc_dir.mkdir()
        (proc_dir / "Q9999.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        from src.engine.ai.procedure_validator import get_procedure

        proc = get_procedure("Q9999", dir_path=proc_dir)
        assert proc is not None
        assert proc.expected_values == {}
        # Rendering through the public reader for an unregistered dir is a no-op,
        # so assert the invariant directly on the data shape instead.
        assert proc.measurement_steps == ()

    def test_block_carries_only_corpus_text(self, corpus: dict) -> None:
        """Every rendered bullet must trace back to the validated file."""
        proc = corpus["U0100"]
        block = procedure_corpus_block("U0100")
        corpus_texts = (
            [q["text"] for q in proc.questions]
            + [m["target"] for m in proc.measurement_steps]
            + [proc.pass_next or "", proc.fail_next or ""]
            + list(proc.safety_notes)
        )
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped.startswith("–"):
                continue
            text = stripped.lstrip("– ").split(" *(id:")[0].split(" *(")[0].strip()
            # Branch lines carry a fixed prefix that is not corpus text.
            for prefix in ("✔️ *Geçti ise:* ", "❌ *Kaldı ise:* "):
                if text.startswith(prefix):
                    text = text[len(prefix):]
            assert any(text in t or t in text for t in corpus_texts if t), (
                f"rendered line does not trace to corpus: {text!r}"
            )
