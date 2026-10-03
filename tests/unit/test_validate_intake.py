# -*- coding: utf-8 -*-
"""Unit tests for the ``data/intake/`` staging gate (``scripts/validate_intake.py``).

The intake area is a raw queue that is NOT bound to the knowledge base, so the
properties under test are the guard rails:

- the repository's own intake skeleton validates clean;
- every shipped template is a *valid* record (templates are what people copy);
- provenance/licence, VIN/PII, draft-case and exact-schema rules fail closed;
- MANIFEST <-> file <-> hash drift is detected;
- a trace that is too large for git must be recorded by hash + location only;
- the conflict report is read-only: it flags "already known" / "looks new" and
  never touches ``data/diagnostics`` or ``data/golden_traces``.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.validate_intake import (
    INTAKE_DIRNAME,
    ROOT,
    Report,
    load_records,
    run,
    split_oem_note,
    trace_meta_fields,
    validate_envelope,
)

TEMPLATES = ROOT / INTAKE_DIRNAME / "_templates"
KB_FILES = (
    ROOT / "data" / "diagnostics" / "dtc_database.json",
    ROOT / "data" / "diagnostics" / "dtc_database_oem_layer.json",
    ROOT / "data" / "diagnostics" / "j1939_spn_fmi_database.json",
    ROOT / "data" / "golden_traces" / "cases" / "schema.json",
)

BASE_DTC: dict[str, Any] = {
    "schema_version": 1,
    "intake_id": "dtc-p0301-workshop",
    "record_type": "dtc",
    "submitted_at": "2026-10-02",
    "submitter": {"type": "curator", "id": "workshop-01", "role": "author"},
    "source": {
        "title": "Workshop fault-code reference",
        "path": "https://example.invalid/manual",
        "type": "oem_manual",
        "publisher": "Example Motorservice",
        "revision": "2024-03",
        "licence": "proprietary",
        "access_date": "2026-10-01",
        "snapshot": None,
    },
    "confidence": "single_source",
    "draft": True,
    "knowledge_base": None,
    "payload": {
        "code": "P0301",
        "system": "Engine",
        "title_en": "Cylinder 1 misfire detected",
        "title_tr": "Silindir 1 atesleme hatasi",
        "description": "Crankshaft-speed deviation on cylinder 1.",
        "severity": "MEDIUM",
        "known_symptoms": ["rough idle"],
        "possible_causes": ["coil open circuit"],
        "status": "ACTIVE",
        "freeze_frame_ref": None,
        "oem_ref": None,
        "vin_masked": "WBA*****X2M",
    },
    "notes": None,
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _kb_state() -> dict[str, str]:
    return {str(p.relative_to(ROOT)): _digest(p) for p in KB_FILES}


def _manifest_for(intake: Path, rows: list[tuple[str, str, str]]) -> str:
    """Build a MANIFEST.md whose hashes match the files actually on disk."""
    lines = [
        "# MANIFEST", "",
        "## Git'e giren kayitlar", "",
        "| intake_id | path | kind | bytes | sha256 | licence | source |",
        "|---|---|---|---|---|---|---|",
    ]
    for intake_id, rel, kind in rows:
        path = intake / rel
        raw = path.read_bytes()
        if path.suffix == ".md":
            meta, _body = split_oem_note(raw.decode("utf-8"))
            record = meta or {}
        else:
            record = json.loads(raw.decode("utf-8"))
        licence = record.get("source", {}).get("licence", "project-internal")
        source = record.get("source", {}).get("path", "internal://workshop")
        lines.append(
            f"| {intake_id} | `data/intake/{rel}` | {kind} | {len(raw)} | "
            f"`{hashlib.sha256(raw).hexdigest()}` | {licence} | `{source}` |"
        )
    lines += ["", "## Git'e girmeyen trace'ler (yalnız hash + konum)", "",
              "| intake_id | format | bytes | sha256 | location |", "|---|---|---|---|---|"]
    return "\n".join(lines) + "\n"


def _make_intake(tmp_path: Path, files: dict[str, Any], external_rows: str = "") -> Path:
    """Materialise a minimal intake tree (dirs + files + matching MANIFEST)."""
    intake = tmp_path / "data" / "intake"
    rows: list[tuple[str, str, str]] = []
    for rel, content in files.items():
        path = intake / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
            meta, _body = split_oem_note(content)
            record_id = (meta or {}).get("intake_id", Path(rel).stem)
            kind = (meta or {}).get("record_type", "oem_note")
        else:
            path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            record_id = content.get("intake_id", "unknown")
            kind = content.get("record_type", "trace_frames")
        rows.append((record_id, rel, kind))
    (intake / "MANIFEST.md").write_text(_manifest_for(intake, rows) + external_rows, encoding="utf-8")
    return intake


def _fails(rep: Report) -> str:
    return " | ".join(f"{check}: {detail}" for level, check, detail in rep.rows if level == "FAIL")


def test_repository_intake_skeleton_is_clean() -> None:
    rep = run(root=ROOT, quiet=True)
    assert rep.count("FAIL") == 0, _fails(rep)
    assert rep.metrics["intake_files"] >= 1
    scan = Report()
    records, _frames = load_records(ROOT / INTAKE_DIRNAME, scan)
    assert scan.count("FAIL") == 0, _fails(scan)
    # Nothing staged under data/intake may ever be calibration-eligible.
    assert all(record.draft for record in records if record.record_type == "case")


@pytest.mark.parametrize("name", sorted(p.name for p in TEMPLATES.iterdir()))
def test_every_template_is_a_valid_record(name: str) -> None:
    """Templates are what contributors copy: they must pass their own gate."""
    path = TEMPLATES / name
    rep = Report()
    if "frames" in name:
        # A frame file is referenced by its meta sidecar, not by an envelope.
        blob = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(blob.get("frames"), list) and blob["frames"]
        for frame in blob["frames"]:
            assert set(frame) == {"t", "channel", "id", "dlc", "data"}
            assert len(frame["data"]) == frame["dlc"] * 2
        return
    if name.endswith(".md"):
        meta, body = split_oem_note(path.read_text(encoding="utf-8"))
        assert meta is not None, f"{name}: meta block missing"
        assert body.strip(), f"{name}: body is empty"
    else:
        meta = json.loads(path.read_text(encoding="utf-8"))
    record = validate_envelope(meta, f"_templates/{name}", rep)
    assert rep.count("FAIL") == 0, _fails(rep)
    assert record is not None
    if record.record_type == "trace":
        fields = trace_meta_fields(TEMPLATES / record.payload["frame_file"])
        assert fields["frame_file_sha256"] == record.payload["frame_file_sha256"], \
            f"{name}: stale sha256 — regenerate with --print-trace-meta"


def test_case_template_is_draft() -> None:
    meta = json.loads((TEMPLATES / "case.template.json").read_text(encoding="utf-8"))
    assert meta["draft"] is True
    assert "verified" not in meta
    assert meta["payload"]["actual_fault"] is None


def _mutate(**changes: Any) -> dict[str, Any]:
    record = copy.deepcopy(BASE_DTC)
    record.update(changes)
    return record


def test_raw_vin_is_rejected() -> None:
    record = _mutate(notes="VIN WBA5A10B9N0C12345 logged at the bench")
    rep = Report()
    validate_envelope(record, "dtc/x.json", rep)
    assert "raw_vin" in _fails(rep)


def test_email_and_phone_are_rejected() -> None:
    rep = Report()
    validate_envelope(_mutate(notes="ask tech-07@example.invalid"), "dtc/x.json", rep)
    assert "email" in _fails(rep)
    rep = Report()
    validate_envelope(_mutate(notes="callback 0212 555 12 34"), "dtc/x.json", rep)
    assert "phone" in _fails(rep)


def test_unmasked_vin_field_is_rejected() -> None:
    record = _mutate()
    record["payload"]["vin_masked"] = "WBA5A1"
    rep = Report()
    validate_envelope(record, "dtc/x.json", rep)
    assert "vin_masked" in _fails(rep)


@pytest.mark.parametrize("licence", ["unknown", "TBD", "", "Beerware"])
def test_ambiguous_or_unlisted_licence_is_rejected(licence: str) -> None:
    record = _mutate()
    record["source"]["licence"] = licence
    rep = Report()
    validate_envelope(record, "dtc/x.json", rep)
    assert "licence" in _fails(rep)


def test_record_without_source_is_rejected() -> None:
    record = _mutate()
    del record["source"]
    rep = Report()
    assert validate_envelope(record, "dtc/x.json", rep) is None
    assert "schema" in _fails(rep)


def test_unknown_field_fails_closed() -> None:
    record = _mutate(extra_field="surprise")
    rep = Report()
    assert validate_envelope(record, "dtc/x.json", rep) is None
    assert "unknown field" in _fails(rep)


def test_operator_verified_confidence_is_not_an_intake_state() -> None:
    rep = Report()
    validate_envelope(_mutate(confidence="operator_verified"), "dtc/x.json", rep)
    assert "confidence" in _fails(rep)


def test_known_knowledge_base_binding_is_rejected() -> None:
    rep = Report()
    validate_envelope(_mutate(knowledge_base="dtc_database.json#P0301"), "dtc/x.json", rep)
    assert "binding" in _fails(rep)


def test_case_cannot_claim_to_be_verified(tmp_path: Path) -> None:
    case = {
        "schema_version": 1,
        "intake_id": "case-p0171-x",
        "record_type": "case",
        "submitted_at": "2026-10-02",
        "submitter": {"type": "human", "id": "tech-07"},
        "source": {"title": "Repair order", "path": "internal://ro/1", "type": "internal_kb",
                   "publisher": "UCanLab", "revision": None, "licence": "project-internal",
                   "access_date": "2026-10-02", "snapshot": None},
        "confidence": "unverified",
        "draft": False,
        "knowledge_base": None,
        "payload": {"case_id": "case-p0171-x", "domain": "PASSENGER", "make": None, "model": None,
                    "year": None, "symptom": "stumble at idle", "dtcs": [], "signals_of_interest": [],
                    "actual_fault": None, "repair": None, "verification": None, "trace_refs": [],
                    "vin_masked": None},
        "notes": None,
    }
    intake = _make_intake(tmp_path, {"cases/case-p0171-x.json": case})
    # The test used to stop here and asserted nothing, so it could never fail.
    assert "draft" in _fails(run(root=ROOT, intake_dir=intake, quiet=True))


def test_manifest_hash_drift_is_detected(tmp_path: Path) -> None:
    intake = _make_intake(tmp_path, {"dtc/dtc-p0301-workshop.json": BASE_DTC})
    assert run(root=ROOT, intake_dir=intake, quiet=True).count("FAIL") == 0
    path = intake / "dtc" / "dtc-p0301-workshop.json"
    path.write_text(path.read_text(encoding="utf-8").replace("MEDIUM", "HIGH"), encoding="utf-8")
    rep = run(root=ROOT, intake_dir=intake, quiet=True)
    assert "MANIFEST sha256" in _fails(rep)


def test_record_without_manifest_row_is_rejected(tmp_path: Path) -> None:
    intake = _make_intake(tmp_path, {"dtc/dtc-p0301-workshop.json": BASE_DTC})
    (intake / "MANIFEST.md").write_text(
        "# MANIFEST\n\n## Git'e giren kayitlar\n\n"
        "| intake_id | path | kind | bytes | sha256 | licence | source |\n|---|---|---|---|---|---|---|\n",
        encoding="utf-8")
    rep = run(root=ROOT, intake_dir=intake, quiet=True)
    assert "no MANIFEST row" in _fails(rep)


def test_duplicate_intake_id_is_rejected(tmp_path: Path) -> None:
    second = copy.deepcopy(BASE_DTC)
    second["intake_id"] = BASE_DTC["intake_id"]
    intake = _make_intake(tmp_path, {"dtc/dtc-p0301-workshop.json": BASE_DTC, "dtc/copy.json": second})
    rep = run(root=ROOT, intake_dir=intake, quiet=True)
    assert "already used by" in _fails(rep) or "is reused" in _fails(rep)


def test_record_type_must_match_its_directory(tmp_path: Path) -> None:
    spn = {
        "schema_version": 1, "intake_id": "spn-102-16", "record_type": "spn_fmi",
        "submitted_at": "2026-10-02", "submitter": {"type": "curator", "id": "w1"},
        "source": {"title": "Manual", "path": "internal://m", "type": "oem_manual", "publisher": "X",
                   "revision": None, "licence": "proprietary", "access_date": "2026-10-01",
                   "snapshot": None},
        "confidence": "unverified", "draft": True, "knowledge_base": None,
        "payload": {"spn": 102, "fmi": 16, "system": "Engine (J1939)", "description": None,
                    "typical_causes": [], "pgn": None, "oem_ref": None, "vin_masked": None},
        "notes": None,
    }
    intake = _make_intake(tmp_path, {"dtc/spn-102-16.json": spn})
    rep = run(root=ROOT, intake_dir=intake, quiet=True)
    assert "does not belong in dtc/" in _fails(rep)


def test_fmi_range_is_enforced() -> None:
    spn = {
        "schema_version": 1, "intake_id": "spn-102-99", "record_type": "spn_fmi",
        "submitted_at": "2026-10-02", "submitter": {"type": "curator", "id": "w1"},
        "source": {"title": "Manual", "path": "internal://m", "type": "oem_manual", "publisher": "X",
                   "revision": None, "licence": "proprietary", "access_date": "2026-10-01",
                   "snapshot": None},
        "confidence": "unverified", "draft": True, "knowledge_base": None,
        "payload": {"spn": 102, "fmi": 99, "system": None, "description": None,
                    "typical_causes": [], "pgn": None, "oem_ref": None, "vin_masked": None},
        "notes": None,
    }
    rep = Report()
    validate_envelope(spn, "spn_fmi/x.json", rep)
    assert "fmi must be an integer in [0, 31]" in _fails(rep)


def _trace_meta(frame_name: str, frame_path: Path, in_git: bool, location: str | None,
                size_override: int | None = None) -> dict[str, Any]:
    fields = trace_meta_fields(frame_path)
    fields["in_git"] = in_git
    fields["external_location"] = location
    if size_override is not None:
        fields["frame_file_bytes"] = size_override
    return {
        "schema_version": 1, "intake_id": f"trace-{frame_name.split('.')[0]}", "record_type": "trace",
        "submitted_at": "2026-10-02", "submitter": {"type": "curator", "id": "w1"},
        "source": {"title": "Bench capture", "path": "internal://bench/1", "type": "internal_kb",
                   "publisher": "UCanLab", "revision": None, "licence": "project-internal",
                   "access_date": "2026-10-02", "snapshot": None},
        "confidence": "unverified", "draft": True, "knowledge_base": None, "payload": fields,
        "notes": None,
    }


FRAMES = {
    "format": "can_frames_json",
    "frames": [
        {"t": 0.0, "channel": "can0", "id": "0x7E0", "dlc": 2, "data": "0209"},
        {"t": 0.01, "channel": "can0", "id": "0x7E8", "dlc": 2, "data": "0300"},
    ],
}


def _trace_intake(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    staging = tmp_path / "stage"
    staging.mkdir()
    frame_path = staging / "bench-01.json"
    frame_path.write_text(json.dumps(FRAMES, indent=2) + "\n", encoding="utf-8")
    meta = _trace_meta(frame_path.name, frame_path, in_git=True, location=None)
    intake = tmp_path / "data" / "intake"
    (intake / "traces").mkdir(parents=True)
    (intake / "traces" / "bench-01.json").write_text(frame_path.read_text(encoding="utf-8"), encoding="utf-8")
    (intake / "traces" / "bench-01.meta.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (intake / "MANIFEST.md").write_text(_manifest_for(intake, [
        ("trace-bench-01", "traces/bench-01.meta.json", "trace"),
        ("trace-bench-01", "traces/bench-01.json", "trace_frames"),
    ]), encoding="utf-8")
    return intake, meta


def test_trace_meta_hash_is_verified(tmp_path: Path) -> None:
    intake, _meta = _trace_intake(tmp_path)
    clean = run(root=ROOT, intake_dir=intake, quiet=True)
    assert clean.count("FAIL") == 0, _fails(clean)
    frame = intake / "traces" / "bench-01.json"
    frame.write_text(frame.read_text(encoding="utf-8").replace('"dlc": 2', '"dlc": 3'), encoding="utf-8")
    rep = run(root=ROOT, intake_dir=intake, quiet=True)
    assert "frame_file_sha256 does not match" in _fails(rep)


def test_frame_file_without_sidecar_is_rejected(tmp_path: Path) -> None:
    intake, _meta = _trace_intake(tmp_path)
    (intake / "traces" / "bench-01.meta.json").unlink()
    rep = run(root=ROOT, intake_dir=intake, quiet=True)
    assert "meta.json sidecar" in _fails(rep)


def test_oversized_trace_may_not_live_in_git(tmp_path: Path) -> None:
    intake, _meta = _trace_intake(tmp_path)
    rep = run(root=ROOT, intake_dir=intake, quiet=True, max_inline_trace_bytes=16)
    assert "inline ceiling" in _fails(rep)
    # The same capture declared external passes the size gate but needs a row.
    external_meta = _trace_meta("bench-01.json", intake / "traces" / "bench-01.json",
                                in_git=False, location="s3://ucanlab-intake/bench-01.json")
    digest = external_meta["payload"]["frame_file_sha256"]
    size = external_meta["payload"]["frame_file_bytes"]
    (intake / "traces" / "bench-01.json").unlink()
    meta_path = intake / "traces" / "bench-01.meta.json"
    meta_path.write_text(json.dumps(external_meta, indent=2) + "\n", encoding="utf-8")
    row = f"| trace-bench-01 | can_frames_json | {size} | `{digest}` | `s3://ucanlab-intake/bench-01.json` |\n"
    (intake / "MANIFEST.md").write_text(
        _manifest_for(intake, [("trace-bench-01", "traces/bench-01.meta.json", "trace")]) + row,
        encoding="utf-8")
    rep = run(root=ROOT, intake_dir=intake, quiet=True, max_inline_trace_bytes=16)
    assert rep.count("FAIL") == 0, _fails(rep)
    assert any(check == "trace_external" for _lv, check, _d in rep.rows)
    # A row with the wrong hash is still a failure.
    (intake / "MANIFEST.md").write_text(
        _manifest_for(intake, [("trace-bench-01", "traces/bench-01.meta.json", "trace")])
        + f"| trace-bench-01 | can_frames_json | {size} | `{'0' * 64}` | `s3://ucanlab-intake/bench-01.json` |\n",
        encoding="utf-8")
    rep = run(root=ROOT, intake_dir=intake, quiet=True, max_inline_trace_bytes=16)
    assert "MANIFEST sha256" in _fails(rep)


def test_external_trace_without_location_is_rejected(tmp_path: Path) -> None:
    intake, _meta = _trace_intake(tmp_path)
    meta = _trace_meta("bench-01.json", intake / "traces" / "bench-01.json", in_git=False, location=None)
    (intake / "traces" / "bench-01.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (intake / "traces" / "bench-01.json").unlink()
    rep = run(root=ROOT, intake_dir=intake, quiet=True)
    assert "requires external_location" in _fails(rep)


def test_conflict_report_flags_known_and_new_without_writing(tmp_path: Path) -> None:
    known = copy.deepcopy(BASE_DTC)  # P0301 is in the merged databases
    known["intake_id"] = "dtc-p0301-known"
    new = copy.deepcopy(BASE_DTC)
    new["intake_id"] = "dtc-p0zz9-new"
    new["payload"]["code"] = "P30F7"
    intake = _make_intake(tmp_path, {"dtc/known.json": known, "dtc/new.json": new})
    before = _kb_state()
    rep = run(root=ROOT, intake_dir=intake, quiet=True)
    assert rep.count("FAIL") == 0, _fails(rep)
    overlaps = [d for lv, ck, d in rep.rows if ck == "kb_overlap"]
    news = [d for lv, ck, d in rep.rows if ck == "kb_new"]
    assert any("P0301" in d for d in overlaps)
    assert any("P30F7" in d for d in news)
    assert _kb_state() == before, "the validator must never write to the knowledge base"


def test_oem_note_requires_meta_block_and_clean_body(tmp_path: Path) -> None:
    note = {
        "schema_version": 1, "intake_id": "oem-p0401-note", "record_type": "oem_note",
        "submitted_at": "2026-10-02", "submitter": {"type": "curator", "id": "w1"},
        "source": {"title": "Bulletin index", "path": "https://example.invalid/tsb", "type": "service_bulletin",
                   "publisher": "Example Motorservice", "revision": "2025-01", "licence": "proprietary",
                   "access_date": "2026-10-02", "snapshot": None},
        "confidence": "single_source", "draft": True, "knowledge_base": None,
        "payload": {"make": "Example Motors", "model": None, "year": None, "oem_code": "EA01-21-501A",
                    "system": "Exhaust", "evidence_refs": [], "related_dtcs": ["P0401"], "vin_masked": None},
        "notes": None,
    }
    body = "# Note\n\nCustomer mail: owner@example.invalid asked for a quote.\n"
    text = "<!-- intake-meta\n" + json.dumps(note, indent=2) + "\n-->\n" + body
    intake = _make_intake(tmp_path, {"oem_notes/oem-p0401-note.md": text})
    rep = run(root=ROOT, intake_dir=intake, quiet=True)
    assert "email" in _fails(rep)
    text = text.replace("Customer mail: owner@example.invalid asked for a quote.", "EGR duty stays at the stop.")
    (intake / "oem_notes" / "oem-p0401-note.md").write_text(text, encoding="utf-8")
    (intake / "MANIFEST.md").write_text(
        _manifest_for(intake, [("oem-p0401-note", "oem_notes/oem-p0401-note.md", "oem_note")]), encoding="utf-8")
    rep = run(root=ROOT, intake_dir=intake, quiet=True)
    assert rep.count("FAIL") == 0, _fails(rep)


def test_raw_container_in_traces_is_rejected(tmp_path: Path) -> None:
    intake = _make_intake(tmp_path, {"dtc/dtc-p0301-workshop.json": BASE_DTC})
    (intake / "traces").mkdir(parents=True, exist_ok=True)
    (intake / "traces" / "capture.blf").write_bytes(b"LOGG\x00\x01binary container")
    rep = run(root=ROOT, intake_dir=intake, quiet=True)
    assert "must not be committed" in _fails(rep)


def test_stray_backup_file_is_rejected(tmp_path: Path) -> None:
    intake = _make_intake(tmp_path, {"dtc/dtc-p0301-workshop.json": BASE_DTC})
    (intake / "dtc" / "dtc-p0301-workshop.json.bak").write_text("{}", encoding="utf-8")
    rep = run(root=ROOT, intake_dir=intake, quiet=True)
    assert "stray artefact" in _fails(rep)


def test_agriculture_case_is_flagged_as_not_promotable(tmp_path: Path) -> None:
    case = {
        "schema_version": 1, "intake_id": "case-agri-x", "record_type": "case",
        "submitted_at": "2026-10-02", "submitter": {"type": "human", "id": "t1"},
        "source": {"title": "Repair order", "path": "internal://ro/2", "type": "internal_kb",
                   "publisher": "UCanLab", "revision": None, "licence": "project-internal",
                   "access_date": "2026-10-02", "snapshot": None},
        "confidence": "unverified", "draft": True, "knowledge_base": None,
        "payload": {"case_id": "case-agri-x", "domain": "AGRICULTURE", "make": None, "model": None,
                    "year": None, "symptom": "engine stalls", "dtcs": [], "signals_of_interest": [],
                    "actual_fault": None, "repair": None, "verification": None, "trace_refs": [],
                    "vin_masked": None},
        "notes": None,
    }
    intake = _make_intake(tmp_path, {"cases/case-agri-x.json": case})
    rep = run(root=ROOT, intake_dir=intake, quiet=True)
    assert rep.count("FAIL") == 0, _fails(rep)
    assert any(check == "promotion_blocked" for _lv, check, _d in rep.rows)


def test_intake_records_are_invisible_to_the_copilot_loaders() -> None:
    """Intake is a queue, not a knowledge source: no loader may read it."""
    copilot = (ROOT / "src" / "engine" / "ai" / "diagnostic_copilot.py").read_text(encoding="utf-8")
    golden = (ROOT / "src" / "engine" / "ai" / "golden_cases.py").read_text(encoding="utf-8")
    assert "data/intake" not in copilot
    assert "data/intake" not in golden


def test_load_records_skips_templates_and_placeholders(tmp_path: Path) -> None:
    intake = _make_intake(tmp_path, {"dtc/dtc-p0301-workshop.json": BASE_DTC})
    (intake / "_templates").mkdir(parents=True, exist_ok=True)
    (intake / "_templates" / "x.json").write_text("{}", encoding="utf-8")
    (intake / "dtc" / ".gitkeep").write_text("", encoding="utf-8")
    rep = Report()
    records, _frames = load_records(intake, rep)
    assert [r.intake_id for r in records] == ["dtc-p0301-workshop"]
    assert rep.count("FAIL") == 0, _fails(rep)
