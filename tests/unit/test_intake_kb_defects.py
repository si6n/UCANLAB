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
