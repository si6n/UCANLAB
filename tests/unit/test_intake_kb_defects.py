# -*- coding: utf-8 -*-
"""Unit tests for the measured-defect register (``scripts/intake_kb_defects.py``).

The register only has value if it cannot rot: a staged finding must equal a
fresh measurement, a fix must close it, and a change in the data must surface
as drift instead of silently going stale. These tests pin all three.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.intake_kb_defects import (
    DETECTORS,
    DEFECT_SUBDIR,
    build_record,
    measure,
    stage,
)
from scripts.validate_intake import INTAKE_DIRNAME, ROOT, Report, run, validate_envelope

DEFECTS_DIR = ROOT / INTAKE_DIRNAME / DEFECT_SUBDIR


def test_every_detector_finds_something_and_documents_its_target() -> None:
    findings = measure(ROOT)
    assert {f["code"] for f in findings} == set(DETECTORS)
    for finding in findings:
        assert finding["affected_count"] > 0, f"{finding['code']}: nothing to fix?"
        target = ROOT / finding["target_file"]
        assert target.is_file(), finding["target_file"]
        assert len(finding["target_sha256"]) == 64
        assert finding["summary"] and finding["why_it_matters"]
        assert finding["examples"], f"{finding['code']} must carry verbatim examples"
        assert finding["severity"] in {"high", "medium", "low"}


def test_measure_is_deterministic() -> None:
    assert measure(ROOT) == measure(ROOT)


def test_staged_records_equal_a_fresh_measurement() -> None:
    """The staged count must be exactly what the detector says today."""
    _written, problems = stage(ROOT, ROOT / INTAKE_DIRNAME, apply=False)
    assert not problems, problems


def test_staged_register_covers_every_detector() -> None:
    staged = {json.loads(p.read_text(encoding="utf-8"))["payload"]["defect_code"]
              for p in DEFECTS_DIR.glob("*.json")}
    assert staged == set(DETECTORS), "a detector without a staged record would vanish from the queue"


def test_defect_record_content_is_self_describing() -> None:
    for path in DEFECTS_DIR.glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["record_type"] == "kb_defect"
        assert record["draft"] is True and record["knowledge_base"] is None
        assert record["source"]["licence"] == "project-internal"
        payload = record["payload"]
        assert payload["affected_count"] > 0
        assert payload["examples"]
        assert payload["detector_expression"]


def _doctored_root(tmp_path: Path, fix_names: bool = False, fix_units: int = 0) -> Path:
    """A minimal repo root whose SPN DB differs from the real one."""
    root = tmp_path / "root"
    diag = root / "data" / "diagnostics"
    diag.mkdir(parents=True)
    source = ROOT / "data" / "diagnostics" / "j1939_spn_fmi_database.json"
    blob = json.loads(source.read_text(encoding="utf-8"))
    if fix_names:
        for record in blob["spns"].values():
            if isinstance(record.get("name"), str) and record["name"].startswith("SPN "):
                record["name"] = "Cleaned Parameter Name"
    fixed = 0
    if fix_units:
        for record in blob["spns"].values():
            if fixed >= fix_units:
                break
            if str(record.get("unit") or "").strip() == "":
                record["unit"] = "rpm"
                fixed += 1
    (diag / "j1939_spn_fmi_database.json").write_text(json.dumps(blob, ensure_ascii=False), encoding="utf-8")
    (diag / "dtc_database.json").write_text(json.dumps({"P0101": {"title_tr": "x"}}), encoding="utf-8")
    return root


def test_gate_reports_drift_when_the_measured_data_changes(tmp_path: Path) -> None:
    """Fixing the target file must show up as drift, not as a stale 'open'."""
    root = _doctored_root(tmp_path, fix_units=3)
    try:
        rep = run(root=root, intake_dir=ROOT / INTAKE_DIRNAME, quiet=True)
        drift = [d for _lv, check, d in rep.rows if check == "defect_drift"]
        stale = [d for _lv, check, d in rep.rows if check == "defect_stale"]
        assert drift, "a changed measurement must be reported as drift"
        assert stale, "a changed target file hash must be reported as stale"
        assert any("spn_unit_placeholder" in d for d in drift), drift
        # A detector whose target file is absent must degrade, never crash.
        missing_target = [d for _lv, check, d in rep.rows if check == "defect_target_missing"]
        assert missing_target, "the gate must report a missing measurement target"
        assert rep.count("FAIL") == 0, [d for lv, _c, d in rep.rows if lv == "FAIL"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_gate_marks_a_closed_defect(tmp_path: Path) -> None:
    """When the detector hits zero, the record is closed — not an error."""
    root = _doctored_root(tmp_path, fix_names=True)
    try:
        rep = run(root=root, intake_dir=ROOT / INTAKE_DIRNAME, quiet=True)
        closed = [d for _lv, check, d in rep.rows if check == "defect_closed"]
        assert any("spn_name_is_fmi_sentence" in d for d in closed), closed
        assert rep.count("FAIL") == 0, [d for lv, _c, d in rep.rows if lv == "FAIL"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_build_record_keeps_the_measurement_verbatim() -> None:
    finding = DETECTORS["spn_name_is_fmi_sentence"](ROOT)
    record = build_record(finding)
    payload = record["payload"]
    assert payload["defect_code"] == finding["code"]
    assert payload["affected_count"] == finding["affected_count"]
    assert payload["target_sha256"] == finding["target_sha256"]
    assert payload["examples"] == finding["examples"]
    assert record["source"]["snapshot"]["sha256"] == finding["target_sha256"]


def test_validator_rejects_a_malformed_defect_record() -> None:
    record = json.loads(next(DEFECTS_DIR.glob("kbdefect-spn-*.json")).read_text(encoding="utf-8"))
    record["payload"]["severity"] = "catastrophic"
    rep = Report()
    validate_envelope(record, "defects/x.json", rep)
    assert any("severity" in d for lv, _c, d in rep.rows if lv == "FAIL")

    record["payload"]["severity"] = "high"
    record["payload"]["affected_count"] = -1
    rep = Report()
    validate_envelope(record, "defects/x.json", rep)
    assert any("affected_count" in d for lv, _c, d in rep.rows if lv == "FAIL")


def test_defect_template_is_valid() -> None:
    template = ROOT / INTAKE_DIRNAME / "_templates" / "kb_defect.template.json"
    rep = Report()
    validate_envelope(json.loads(template.read_text(encoding="utf-8")), "_templates/kb_defect", rep)
    assert rep.count("FAIL") == 0, [d for lv, _c, d in rep.rows if lv == "FAIL"]


def test_gate_never_writes_the_measured_files() -> None:
    targets = [ROOT / "data" / "diagnostics" / "j1939_spn_fmi_database.json",
               ROOT / "data" / "diagnostics" / "dtc_database.json"]
    before = {p: p.read_bytes() for p in targets}
    run(root=ROOT, quiet=True)
    for path, blob in before.items():
        assert path.read_bytes() == blob, f"{path} must stay read-only"


def test_provenance_matcher_recognises_documented_sources() -> None:
    """A traceability detector is only useful if it does not cry wolf."""
    from scripts.intake_kb_defects import _is_documented, _provenance_corpus

    corpus = _provenance_corpus(ROOT)
    assert corpus, "provenance documents must be readable"
    for value in ("https://github.com/foerbsnavi/OBDex/blob/bc58b0eb",
                  "sitrak_ccby4", "obdex_p0xxx", "troublecodes.net",
                  "https://www.j1939hub.com/spn/190", "internal://copilot/notes"):
        assert _is_documented(value, corpus), value
    for value in ("obd2.com", "openlaborproject.com"):
        assert not _is_documented(value, corpus), value


def test_traceability_detector_reports_the_big_files() -> None:
    finding = DETECTORS["kb_source_value_not_in_provenance_doc"](ROOT)
    assert finding["severity"] == "high"
    assert finding["affected_count"] > 0
    # examples are per source key and the summary must name the worst file:
    # an unreadable aggregate is not a reviewable finding.
    assert all({"source_key", "occurrences", "files"} <= set(e) for e in finding["examples"])
    assert "dtc_database.json" in finding["summary"]
    assert finding["examples"][0]["source_key"] == "obd2.com"


def test_j1939_licence_detector_separates_the_two_classes() -> None:
    finding = DETECTORS["j1939_source_without_licence"](ROOT)
    classes = {e.get("class") for e in finding["examples"]}
    assert classes == {"no_source", "unlicensed_src"}
    assert finding["affected_count"] > 0
    # The wording must not claim the harvest is undocumented: the diagnostics
    # provenance log documents part of it. That distinction is load-bearing.
    assert "lisansı çözülemiyor" in finding["why_it_matters"]


# --------------------------------------------------------------------------- #
# per-source provenance gaps
# --------------------------------------------------------------------------- #
GAPS_DIR = ROOT / "data" / "intake" / "gaps"


def test_gap_records_cover_every_undocumented_source_key() -> None:
    from scripts.intake_kb_defects import GAPS_SUBDIR, build_gap_records, measure_provenance_gaps

    measured = measure_provenance_gaps(ROOT)
    assert measured, "the traceability gap must not be silently empty"
    staged = {json.loads(p.read_text(encoding="utf-8"))["payload"]["source_key"]
              for p in (ROOT / "data" / "intake" / GAPS_SUBDIR).glob("*.json")}
    assert staged == set(measured), "a source key without a staged record would be invisible"
    assert len(build_gap_records(ROOT)) == len(measured)


def test_gap_records_are_shape_valid() -> None:
    from scripts.validate_intake import validate_envelope as _validate

    for path in sorted(GAPS_DIR.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["record_type"] == "provenance_gap"
        assert record["draft"] is True and record["knowledge_base"] is None
        payload = record["payload"]
        assert payload["occurrences"] > 0
        assert payload["files"] and payload["source_key"]
        assert payload["licence_status"] == "unresolved"
        rep = Report()
        _validate(record, f"gaps/{path.name}", rep)
        assert rep.count("FAIL") == 0, [d for lv, _c, d in rep.rows if lv == "FAIL"]


def test_gap_record_rejects_a_resolved_licence() -> None:
    """A closed gap must be archived, not kept with a fake 'resolved' status."""
    from scripts.validate_intake import validate_envelope as _validate

    record = json.loads(next(GAPS_DIR.glob("*.json")).read_text(encoding="utf-8"))
    record["payload"]["licence_status"] = "MIT"
    rep = Report()
    _validate(record, "gaps/x.json", rep)
    assert any("licence_status" in d for lv, _c, d in rep.rows if lv == "FAIL")


def test_gate_re_measures_gap_occurrences() -> None:
    rep = run(root=ROOT, quiet=True)
    assert rep.count("FAIL") == 0, [d for lv, _c, d in rep.rows if lv == "FAIL"]
    staged = len(list(GAPS_DIR.glob("*.json")))
    assert rep.metrics.get("records_provenance_gap") == staged
    assert rep.metrics.get("provenance_gap_occurrences", 0) > 0
    assert sum(1 for _lv, check, _d in rep.rows if check == "provenance_open") == staged


# --------------------------------------------------------------------------- #
# product-capability detectors (what the copilot can actually answer)
# --------------------------------------------------------------------------- #
def test_severity_detector_measures_a_systematic_gap() -> None:
    finding = DETECTORS["dtc_severity_unknown_and_unclassed"](ROOT)
    assert finding["severity"] == "medium"
    assert finding["affected_count"] > 0
    assert "UNKNOWN" in finding["summary"] and "NoClass" in finding["summary"]
    # the gap must be systematic: both placeholders move together
    assert all(e["dtc_namespace"] in {"SAE_J2012", "OEM"} for e in finding["examples"])


def test_symptom_detector_measures_matchability() -> None:
    finding = DETECTORS["dtc_missing_symptoms"](ROOT)
    assert finding["severity"] == "low"
    assert finding["affected_count"] > 0
    assert all("code" in e for e in finding["examples"])


def test_every_detector_is_registered_and_runs() -> None:
    from scripts.intake_kb_defects import measure

    codes = {f["code"] for f in measure(ROOT)}
    assert codes == set(DETECTORS), "a detector missing from the registry never reaches the gate"
    assert len(codes) >= 10


# --------------------------------------------------------------------------- #
# capability detectors (graph coverage, evidence signals, thresholds)
# --------------------------------------------------------------------------- #
def test_graph_coverage_detector_measures_by_reference_not_node_count() -> None:
    finding = DETECTORS["root_cause_graph_dtc_coverage"](ROOT)
    assert finding["severity"] == "medium"
    assert finding["affected_count"] > 0
    assert "/" in finding["summary"] and "%" in finding["summary"]
    # every DTC reference the graph makes must exist (no dangling refs)
    assert finding.get("spn_references_checked", 0) > 0
    assert all({"code"} <= set(e) for e in finding["examples"])


def test_evidence_signal_detector_counts_empty_nodes() -> None:
    finding = DETECTORS["cause_node_without_evidence_signal"](ROOT)
    assert finding["severity"] == "low"
    assert finding["affected_count"] > 0
    assert all({"id", "title"} <= set(e) for e in finding["examples"])


def test_threshold_detector_lists_signals_without_thresholds() -> None:
    finding = DETECTORS["measurement_signal_without_threshold"](ROOT)
    assert finding["severity"] == "medium"
    assert finding["affected_count"] > 0
    assert all({"canonical", "unit"} <= set(e) for e in finding["examples"])


def test_no_detector_reports_a_dangling_cross_reference() -> None:
    """Cross-references the repo can walk must all resolve — that is a hard gate."""
    import json as _json

    graph = _json.loads((ROOT / "data" / "diagnostics" / "root_cause_graph.json").read_text(encoding="utf-8"))
    codes = set(_json.loads((ROOT / "data" / "diagnostics" / "dtc_database.json").read_text(encoding="utf-8")))
    spns = set(_json.loads((ROOT / "data" / "diagnostics" / "j1939_spn_fmi_database.json")
                           .read_text(encoding="utf-8"))["spns"])
    import re as _re

    for node in graph["nodes"]:
        for ref in node.get("expected_dtcs") or []:
            text = str(ref).strip()
            if _re.fullmatch(r"[PBCU][0-9A-F]{4}", text):
                assert text in codes, text
            match = _re.fullmatch(r"SPN\s+(\d+)", text)
            if match:
                assert f"SPN_{match.group(1)}" in spns, text


# --------------------------------------------------------------------------- #
# quarantine self-consistency gate
# --------------------------------------------------------------------------- #
def test_quarantine_invariants_hold_on_the_real_data() -> None:
    from scripts.validate_intake import check_quarantine_invariants

    rep = Report()
    check_quarantine_invariants(ROOT, rep)
    assert rep.count("FAIL") == 0, [d for lv, _c, d in rep.rows if lv == "FAIL"]
    assert rep.metrics.get("quarantine_invariants_checked", 0) > 0


def _mini_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A tiny stand-in for the repo: real quarantine audits, synthetic DB/graph."""
    import shutil

    src = ROOT / "data" / "diagnostics"
    diag = tmp_path / "data" / "diagnostics"
    quarantine = diag / "quarantine"
    quarantine.mkdir(parents=True)
    shutil.copy(src / "j1939_spn_fmi_database.json", diag / "j1939_spn_fmi_database.json")
    for name in ("t2_4_sitrak_shell_rows.json", "dtcdocs_llm_blocks.json"):
        shutil.copy(src / "quarantine" / name, quarantine / name)
    return tmp_path, diag


def test_quarantine_gate_catches_a_reintroduced_seed_node(tmp_path: Path) -> None:
    """A node the audit recorded as 'not_recovered' must not reappear in the graph."""
    from scripts.validate_intake import check_quarantine_invariants

    root, diag = _mini_repo(tmp_path)
    (diag / "root_cause_graph.json").write_text(
        json.dumps({"nodes": [{"id": "leaked-node"}]}), encoding="utf-8")
    (diag / "quarantine" / "t21_seed_audit.json").write_text(
        json.dumps({"nodes": [{"id": "leaked-node", "verdict": "not_recovered"},
                              {"id": "missing-node", "verdict": "already_in_graph"}]}),
        encoding="utf-8")
    rep = Report()
    check_quarantine_invariants(root, rep)
    failures = " | ".join(d for lv, _c, d in rep.rows if lv == "FAIL")
    assert "not_recovered" in failures, failures
    assert "missing-node" in failures, failures


def test_quarantine_gate_catches_a_reintroduced_shell_row(tmp_path: Path) -> None:
    from scripts.validate_intake import check_quarantine_invariants

    root, diag = _mini_repo(tmp_path)
    (diag / "root_cause_graph.json").write_text(json.dumps({"nodes": []}), encoding="utf-8")
    shell = json.loads((diag / "quarantine" / "t2_4_sitrak_shell_rows.json").read_text(encoding="utf-8"))
    key = next(iter(shell["records"]))
    blob = json.loads((diag / "j1939_spn_fmi_database.json").read_text(encoding="utf-8"))
    blob["spns"][key] = {"spn": 1, "name": "leaked"}
    (diag / "j1939_spn_fmi_database.json").write_text(json.dumps(blob), encoding="utf-8")
    rep = Report()
    check_quarantine_invariants(root, rep)
    assert any("kabuk satırı" in d for lv, _c, d in rep.rows if lv == "FAIL")


def test_quarantine_gate_catches_a_reintroduced_llm_block(tmp_path: Path) -> None:
    """A quarantined LLM block's sha256 must not reappear in the shipped DB text."""
    from scripts.validate_intake import check_quarantine_invariants

    root, diag = _mini_repo(tmp_path)
    (diag / "root_cause_graph.json").write_text(json.dumps({"nodes": []}), encoding="utf-8")
    blocks = json.loads((diag / "quarantine" / "dtcdocs_llm_blocks.json").read_text(encoding="utf-8"))
    digest = blocks["records"][0]["block_sha256"]
    blob = json.loads((diag / "j1939_spn_fmi_database.json").read_text(encoding="utf-8"))
    blob["spns"]["SPN_999999"] = {"spn": 999999, "name": "leaked", "evidence_url": digest}
    (diag / "j1939_spn_fmi_database.json").write_text(json.dumps(blob), encoding="utf-8")
    rep = Report()
    check_quarantine_invariants(root, rep)
    assert any("blok özeti" in d for lv, _c, d in rep.rows if lv == "FAIL")


# --------------------------------------------------------------------------- #
# promotion readiness (what can be merged this week)
# --------------------------------------------------------------------------- #
def test_readiness_split_is_consistent() -> None:
    """ready + waiting must equal the queue for every promotable family."""
    rep = run(root=ROOT, quiet=True)
    assert rep.count("FAIL") == 0, [d for lv, _c, d in rep.rows if lv == "FAIL"]
    m = rep.metrics
    assert m["promotable_spn_reference"] + m["waiting_evidence_spn_reference"] > 0
    assert m["promotable_pgn_layout"] + m["waiting_evidence_pgn_layout"] > 0
    assert m["promotable_oem_divergence"] > 0
    assert m["decision_records"] > 0
    assert any(check == "readiness" for _lv, check, _d in rep.rows)


def test_every_staged_oem_divergence_has_rows() -> None:
    """An empty divergence list is a non-finding and must not sit in the queue."""
    for path in (ROOT / "data" / "intake" / "oem").glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))["payload"]
        assert payload["divergences"], f"{path.name} has no divergence to review"
        assert payload["divergence_count"] == len(payload["divergences"])


# --------------------------------------------------------------------------- #
# UDS DID attribution (policy-driven severity)
# --------------------------------------------------------------------------- #
def test_uds_did_detector_counts_sourceless_oem_rows() -> None:
    finding = DETECTORS["uds_oem_did_without_source"](ROOT)
    assert finding["affected_count"] > 0
    assert finding["severity"] in {"high", "medium"}
    # severity is policy driven: data/PROVENANCE.md §5 names this table
    policy = (ROOT / "data" / "PROVENANCE.md").read_text(encoding="utf-8")
    expected = "high" if "UDS DID / Mode 06" in policy else "medium"
    assert finding["severity"] == expected, "severity must follow the documented policy"
    for example in finding["examples"]:
        assert {"did", "oem", "name"} <= set(example)


def test_uds_did_examples_are_verbatim_sourceless_rows() -> None:
    finding = DETECTORS["uds_oem_did_without_source"](ROOT)
    dids = json.loads((ROOT / "data" / "diagnostics" / "uds_did_database.json")
                      .read_text(encoding="utf-8"))["dids"]
    for example in finding["examples"]:
        record = dids[example["did"]]
        assert record.get("name") == example["name"]
        assert not str(record.get("source") or "").strip(), "example must be a sourceless row"


def test_traceability_severity_follows_the_policy_classification() -> None:
    """A source the policy never named must not be reported as a policy breach."""
    from scripts.intake_kb_defects import measure_provenance_gaps, classify_source

    policy = (ROOT / "data" / "PROVENANCE.md").read_text(encoding="utf-8").lower()
    gaps = measure_provenance_gaps(ROOT)
    classes = {key: classify_source(key, policy) for key in gaps}
    assert "unattested" in classes.values(), "most measured sources are simply undocumented"
    # justanswer is a public forum, which §5 rejects by kind
    assert classes.get("justanswer") == "policy_breach"
    # a plain commercial DTC site is undocumented, not forbidden
    assert classes.get("obd2.com") == "unattested"

    finding = DETECTORS["kb_source_value_not_in_provenance_doc"](ROOT)
    breaches = {k for k, v in classes.items() if v == "policy_breach"}
    expected = "high" if breaches else "medium"
    assert finding["severity"] == expected
    assert all("policy_class" in e for e in finding["examples"])
