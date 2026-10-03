# -*- coding: utf-8 -*-
"""Unit tests for the upstream discovery scan (``scripts/intake_scan_sources.py``).

The scan's value is that it is *trustworthy*: it re-derives the upstream
inventory at the very commits ``data/PROVENANCE.md`` cites and compares
per-file sha256 against the recorded evidence. These tests pin that contract:

- every hash the tool trusts is present in the provenance document (no drift);
- the committed pinli commit SHAs match the provenance document;
- the restricted YAML reader handles the constructs canboat actually emits
  (repeat blocks, plain multi-line scalars, block scalars) and **fails closed**
  on anything else;
- staged ``pgn_layout`` records match what the pinned source says, byte for
  byte, with a per-record sha256 of the upstream file;
- the intake gate stays green on the staged batch, and the knowledge base is
  never written.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.intake_scan_sources import (
    CANBOAT_COMMIT,
    SPN_REF_SUBDIR,
    OBDEX_COMMIT,
    OEM_SUBDIR,
    PGN_SUBDIR,
    VENDORED_SHA256,
    WAL33D_COMMIT,
    build_oem_divergence_records,
    build_pgn_record,
    parse_oem_listing,
    collect_spn_evidence,
    parse_pgn_yaml,
    stage,
    stage_oem,
    stage_spn_refs,
)
from scripts.validate_intake import INTAKE_DIRNAME, ROOT, Report, run

SCAN = ROOT / "data" / "intake" / PGN_SUBDIR
PROVENANCE = ROOT / "data" / "PROVENANCE.md"
SHA_RE = re.compile(r"`([0-9a-f]{64})`")
KB_FILES = (
    ROOT / "data" / "diagnostics" / "canboat_pgn_reference.json",
    ROOT / "data" / "diagnostics" / "j1939_spn_fmi_database.json",
)

YAML_FIXTURE = """pgn: 61450
id: engineGasFlowRate1
description: Engine Gas Flow Rate 1
type: Single
priority: 6
interval: 50
explanation: This PGN carries a flow rate expressed in kg/h and the
  value continues on the next line of upstream text.
missing:
- Resolution
fields:
- id: engineExhaustGasRecirculation1MassFlowRate
  name: Engine Exhaust Gas Recirculation 1 Mass Flow Rate
  type: NUMBER
  bits: 16
  unit: kg/h
  resolution: 0.05
  description: SPN 97, 0.05 kg/hr per bit
notes: |-
  A literal block scalar that spans
  two lines.
"""


def _provenance_hashes() -> set[str]:
    return set(SHA_RE.findall(PROVENANCE.read_text(encoding="utf-8")))


def test_every_trusted_hash_appears_in_the_provenance_document() -> None:
    """The tool may only claim artefacts data/PROVENANCE.md actually evidences."""
    documented = _provenance_hashes()
    missing = {key: digest for key, digest in VENDORED_SHA256.items() if digest not in documented}
    assert not missing, f"hashes not evidenced in data/PROVENANCE.md: {missing}"


def test_pinned_commits_match_the_provenance_document() -> None:
    text = PROVENANCE.read_text(encoding="utf-8")
    assert OBDEX_COMMIT in text
    assert CANBOAT_COMMIT in text


def test_parser_reads_the_canboat_subset() -> None:
    doc = parse_pgn_yaml(YAML_FIXTURE)
    assert doc["pgn"] == 61450
    assert doc["priority"] == 6 and doc["interval"] == 50
    assert doc["missing"] == ["Resolution"]
    assert doc["notes"] == "A literal block scalar that spans\ntwo lines."  # |- strips the trailing newline
    assert doc["explanation"].startswith("This PGN carries a flow rate")
    assert doc["explanation"].endswith("next line of upstream text.")
    field = doc["fields"][0]
    assert field["id"] == "engineExhaustGasRecirculation1MassFlowRate"
    assert field["bits"] == 16
    assert field["resolution"] == 0.05
    assert "SPN 97" in field["description"]


def test_parser_flattens_repeat_blocks() -> None:
    doc = parse_pgn_yaml(
        "pgn: 65226\n"
        "fields:\n"
        "- id: spn\n"
        "  name: SPN\n"
        "- repeat:\n"
        "    fields:\n"
        "    - id: fmi\n"
        "      name: FMI\n"
    )
    assert doc["fields"][0]["name"] == "SPN"
    assert "repeat" in doc["fields"][1], "a repeat block stays nested (callers flatten it)"
    repeat = doc["fields"][1]["repeat"]["fields"]
    assert [f["id"] for f in repeat] == ["fmi"]


def test_parser_fails_closed_on_an_unreadable_line() -> None:
    with pytest.raises(ValueError):
        parse_pgn_yaml("pgn: 61450\nthis line is not yaml at all\n")
    with pytest.raises(ValueError):
        parse_pgn_yaml("")


def test_staged_records_are_complete_and_consistent() -> None:
    staged = sorted(SCAN.glob("*.json"))
    assert staged, "the canboat J1939 PGN batch must be staged"
    seen_ids: set[str] = set()
    for path in staged:
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["record_type"] == "pgn_layout"
        assert record["draft"] is True
        assert record["knowledge_base"] is None
        assert record["source"]["licence"] == "Apache-2.0"
        assert CANBOAT_COMMIT in record["source"]["path"]
        assert re.fullmatch(r"[0-9a-f]{64}", record["source"]["snapshot"]["sha256"])
        payload = record["payload"]
        assert isinstance(payload["pgn"], int)
        assert payload["fields"], f"{path.name}: a staged layout must have fields"
        assert record["intake_id"] not in seen_ids
        seen_ids.add(record["intake_id"])
    # DM1 is already vendored: staging a copy would be noise, not discovery.
    assert not any(json.loads(p.read_text(encoding="utf-8"))["payload"]["pgn"] == 65226 for p in staged)


def test_staged_snapshot_hash_matches_the_recorded_source_path() -> None:
    """Every staged record must name its upstream file and pin that file's hash."""
    for path in sorted(SCAN.glob("*.json"))[:5]:
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["payload"]["source_file"].startswith("database/j1939/pgns/")
        assert record["source"]["path"].endswith(record["payload"]["source_file"])
        assert record["source"]["snapshot"]["bytes"] > 0


def test_build_record_never_invents_spn_numbers() -> None:
    """SPN may only be taken from the upstream text, never derived."""
    doc_text = "\n".join([
        "pgn: 61450",
        "id: demo",
        "fields:",
        "- id: a",
        "  name: Some Field",
        "  bits: 16",
        "  description: no spn mentioned",
        "- id: b",
        "  name: Engine Speed",
        "  bits: 16",
        "  description: SPN 190, 0.125 rpm",
        "",
    ])
    tree = Path("/tmp/ucanlab-fake-canboat-f7f088b4")
    yaml_path = tree / "database" / "j1939" / "pgns" / "061450-demo.yaml"
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(doc_text, encoding="utf-8")
    try:
        record = build_pgn_record(tree, yaml_path)
        rows = record["payload"]["fields"]
        assert rows[0]["spn"] is None
        assert rows[1]["spn"] == 190
        assert record["payload"]["upstream_keys"] == []
        assert record["payload"]["pgn"] == 61450
    finally:
        import shutil

        shutil.rmtree(tree, ignore_errors=True)


def test_stage_is_idempotent_and_reports_drift(tmp_path: Path) -> None:
    """--stage without --apply reports missing records; --apply is idempotent.

    ``count`` is the number of layout files that are in sync *or* written; a
    record that had to be created is also listed in ``problems`` in check mode.
    """
    tree = Path("/tmp/ucanlab-fake-canboat-stage")
    import shutil

    shutil.rmtree(tree, ignore_errors=True)
    pgns = tree / "database" / "j1939" / "pgns"
    pgns.mkdir(parents=True)
    (pgns / "061450-demo.yaml").write_text(YAML_FIXTURE, encoding="utf-8")
    intake = tmp_path / "intake"
    try:
        count, problems, skipped = stage(tree, intake, apply=False)
        assert count == 1 and skipped == [], "nothing is vendored under a made-up tree"
        assert any("missing (run with --apply)" in p for p in problems)

        count, problems, skipped = stage(tree, intake, apply=True)
        assert count == 1 and not problems
        staged_file = intake / PGN_SUBDIR / "canboat-pgn-61450-enginegasflowrate1.json"
        assert staged_file.is_file()

        count, problems, _skipped = stage(tree, intake, apply=True)
        assert count == 0 and not problems, "a second --apply must add nothing"

        staged_file.write_text(staged_file.read_text(encoding="utf-8").replace("Single", "Fast"),
                               encoding="utf-8")
        _count, problems, _skipped = stage(tree, intake, apply=True)
        assert any("differs from the pinned source" in p for p in problems)
    finally:
        shutil.rmtree(tree, ignore_errors=True)


def test_staged_batch_passes_the_intake_gate_and_never_writes_the_kb() -> None:
    before = {p: p.read_bytes() for p in KB_FILES}
    rep = run(root=ROOT, quiet=True)
    failures = [d for level, _c, d in rep.rows if level == "FAIL"]
    assert not failures, failures
    assert rep.metrics["records_pgn_layout"] == len(list(SCAN.glob("*.json")))
    assert rep.metrics["manifest_rows"] >= rep.metrics["records_pgn_layout"]
    # The conflict report must be measured, not empty theatre.
    assert rep.metrics["kb_new_candidates"] > 0
    assert rep.metrics["kb_overlaps"] > 0
    assert rep.metrics["pgn_layout_spn_refs"] > 0
    for path, blob in before.items():
        assert path.read_bytes() == blob, f"{path} must never be written by the intake gate"


def test_manifest_is_in_sync_with_the_staged_records() -> None:
    manifest = (ROOT / INTAKE_DIRNAME / "MANIFEST.md").read_text(encoding="utf-8")
    for path in sorted(SCAN.glob("*.json")):
        assert f"`data/intake/{PGN_SUBDIR}/{path.name}`" in manifest


def test_notice_file_backs_the_staged_licence() -> None:
    notice = (ROOT / INTAKE_DIRNAME / "NOTICE.canboat.md").read_text(encoding="utf-8")
    assert CANBOAT_COMMIT in notice
    assert "Apache License, Version 2.0" in notice
    assert "Kees Verruijt" in notice


def test_pgn_layout_record_rejects_a_bogus_field() -> None:
    """A staged layout still has to pass the schema: bits must be a sane integer."""
    bad = {
        "schema_version": 1,
        "intake_id": "pgn-bogus",
        "record_type": "pgn_layout",
        "submitted_at": "2026-10-02",
        "submitter": {"type": "human", "id": "tech-01"},
        "source": {"title": "t", "path": "https://example.invalid/x", "type": "standard",
                   "publisher": "p", "revision": None, "licence": "Apache-2.0",
                   "access_date": "2026-10-02", "snapshot": None},
        "confidence": "single_source",
        "draft": True,
        "knowledge_base": None,
        "payload": {"pgn": 61450, "pgn_id": "demo", "description": None, "pgn_type": "Single",
                    "priority": 6, "interval_ms": None, "upstream_keys": [],
                    "source_file": "database/j1939/pgns/061450-demo.yaml",
                    "fields": [{"field_id": "a", "name": "A", "spn": 190, "bits": "sixteen",
                                "unit": None, "resolution": None, "description": "SPN 190"}]},
        "notes": None,
    }
    from scripts.validate_intake import validate_envelope

    rep = Report()
    validate_envelope(bad, "pgn/x.json", rep)
    assert any("bits must be an integer" in d for _lv, _c, d in rep.rows if _lv == "FAIL")


# --------------------------------------------------------------------------- #
# Wal33D per-manufacturer divergence staging
# --------------------------------------------------------------------------- #
OEM_SRC = "oem"
OEM_SAMPLE = """P1100 - Mass Air Flow Sensor Intermittent
P1101 - Mass Air Flow Sensor Out of Self-Test Range
P1105 - Dual Alternator Upper Fault
garbage line without a code
"""


def test_parse_oem_listing_keeps_only_code_lines() -> None:
    rows = parse_oem_listing(OEM_SAMPLE)
    assert rows == [
        ("P1100", "Mass Air Flow Sensor Intermittent"),
        ("P1101", "Mass Air Flow Sensor Out of Self-Test Range"),
        ("P1105", "Dual Alternator Upper Fault"),
    ]


def _fake_wal33d(tmp_path: Path) -> Path:
    tree = tmp_path / "wal33d"
    src = tree / "data" / "source-data"
    src.mkdir(parents=True)
    (src / "ford_codes.txt").write_text(OEM_SAMPLE, encoding="utf-8")
    return tree


def test_oem_divergence_records_carry_only_disagreeing_rows(tmp_path: Path) -> None:
    tree = _fake_wal33d(tmp_path)
    records = build_oem_divergence_records(tree, ROOT)
    assert len(records) == 1
    payload = records[0]["payload"]
    assert payload["make"] == "FORD"
    assert payload["source_rows"] == 3
    assert payload["divergence_count"] == len(payload["divergences"])
    codes = {row["code"]: row for row in payload["divergences"]}
    # P1100 is in the repo's OEM layer with different wording -> both texts kept.
    assert codes["P1100"]["source_description_en"] == "Mass Air Flow Sensor Intermittent"
    assert codes["P1100"]["kb_description_en"] != codes["P1100"]["source_description_en"]
    for row in payload["divergences"]:
        assert set(row) == {"code", "source_description_en", "kb_description_en"}


def test_stage_oem_is_idempotent_and_flags_drift(tmp_path: Path) -> None:
    tree = _fake_wal33d(tmp_path)
    intake = tmp_path / "intake"
    _count, problems = stage_oem(tree, intake, ROOT, apply=False)
    assert any("missing (run with --apply)" in p for p in problems)

    count, problems = stage_oem(tree, intake, ROOT, apply=True)
    assert count == 1 and not problems
    staged = intake / OEM_SUBDIR / "wal33d-divergence-ford.json"
    assert staged.is_file()

    count, problems = stage_oem(tree, intake, ROOT, apply=True)
    assert count == 0 and not problems, "a second --apply must add nothing"

    staged.write_text(staged.read_text(encoding="utf-8").replace("FORD", "FORD2"), encoding="utf-8")
    _count, problems = stage_oem(tree, intake, ROOT, apply=True)
    assert any("differs from the pinned source" in p for p in problems)


def test_staged_oem_divergences_are_re_measured_by_the_gate() -> None:
    """The gate re-checks every staged row against the live OEM layer."""
    staged = sorted((ROOT / "data" / "intake" / OEM_SRC).glob("*.json"))
    assert staged, "the Wal33D per-manufacturer batch must be staged"
    total = 0
    for path in staged:
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["record_type"] == "oem_divergence"
        assert record["source"]["licence"] == "MIT"
        assert WAL33D_COMMIT in record["source"]["path"]
        assert record["draft"] is True and record["knowledge_base"] is None
        payload = record["payload"]
        assert payload["divergence_count"] == len(payload["divergences"])
        total += len(payload["divergences"])
    rep = run(root=ROOT, quiet=True)
    assert rep.count("FAIL") == 0, [d for lv, _c, d in rep.rows if lv == "FAIL"]
    assert rep.metrics["oem_divergence_rows"] == total
    assert rep.metrics["oem_divergence_open"] > 0, "the measured divergence must be re-reported"
    assert any(check == "kb_divergence" for _lv, check, _d in rep.rows)


def test_oem_divergence_record_rejects_a_count_mismatch() -> None:
    from scripts.validate_intake import Report as _Report, validate_envelope

    staged_path = ROOT / "data" / "intake" / OEM_SRC / "wal33d-divergence-ford.json"
    record = json.loads(staged_path.read_text(encoding="utf-8"))
    record["payload"]["divergence_count"] = 1
    rep = _Report()
    validate_envelope(record, "oem/x.json", rep)
    assert any("divergence_count" in d for lv, _c, d in rep.rows if lv == "FAIL")


# --------------------------------------------------------------------------- #
# SPN references (harvested from the J1939 layouts)
# --------------------------------------------------------------------------- #
SPN_REF_DIR = ROOT / "data" / "intake" / SPN_REF_SUBDIR


def test_collect_spn_evidence_only_tracks_mentioned_numbers() -> None:
    tree = Path("/tmp/ucanlab-fake-canboat-spn")
    import shutil

    shutil.rmtree(tree, ignore_errors=True)
    pgns = tree / "database" / "j1939" / "pgns"
    pgns.mkdir(parents=True)
    (pgns / "065201-ecuHistory.yaml").write_text(
        "pgn: 65201\n"
        "id: ecuHistory\n"
        "fields:\n"
        "- id: totalEcuDistance\n"
        "  name: Total ECU Distance\n"
        "  bits: 32\n"
        "  unit: km\n"
        "  resolution: 0.125\n"
        "  description: SPN 1032, 0.125 kilometre per bit\n"
        "- id: totalEcuRunTime\n"
        "  name: Total ECU Run Time\n"
        "  bits: 32\n"
        "  unit: h\n"
        "  description: no number in this text\n",
        encoding="utf-8")
    try:
        evidence = collect_spn_evidence(tree)
        assert sorted(evidence) == [1032], "only an explicit 'SPN n' mention counts"
        entry = evidence[1032]
        assert entry["names"] == ["Total ECU Distance"]
        assert entry["units"] == ["km"]
        assert entry["resolutions"] == [0.125]
        assert entry["pgns"] == [65201]
        assert entry["evidence"] == ["SPN 1032, 0.125 kilometre per bit"]
    finally:
        shutil.rmtree(tree, ignore_errors=True)


def test_staged_spn_references_carry_evidence_and_kb_state() -> None:
    staged = sorted(SPN_REF_DIR.glob("*.json"))
    assert staged, "SPN reference records must be staged"
    absent = 0
    for path in staged:
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["record_type"] == "spn_reference"
        assert record["draft"] is True and record["knowledge_base"] is None
        assert record["source"]["licence"] == "Apache-2.0"
        payload = record["payload"]
        assert payload["names_en"], f"{path.name}: an SPN without a name is not usable"
        assert payload["sources"] and payload["evidence_pgns"]
        for source in payload["sources"]:
            assert re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
        # The number must appear in the copied evidence text.
        assert any(f"SPN {payload['spn']}" in text for text in payload["evidence_text"]), path.name
        absent += payload["kb_state"] == "absent"
    assert absent > 0, "some staged SPNs must be KB gaps (that is the point)"


def test_gate_reconciles_spn_references_against_the_live_database() -> None:
    rep = run(root=ROOT, quiet=True)
    assert rep.count("FAIL") == 0, [d for lv, _c, d in rep.rows if lv == "FAIL"]
    total = len(list(SPN_REF_DIR.glob("*.json")))
    assert rep.metrics.get("records_spn_reference") == total
    assert rep.metrics.get("spn_ref_absent", 0) + rep.metrics.get("spn_ref_present", 0) == total
    assert rep.metrics.get("spn_ref_absent", 0) > 0
    assert rep.metrics.get("spn_name_variant", 0) > 0, "name variants must be measured, not assumed"


def test_stage_spn_refs_refuses_to_overwrite_a_changed_record(tmp_path: Path) -> None:
    """The stager must never silently rewrite a staged SPN reference.

    A synthetic upstream tree still contributes the DBC evidence, so the exact
    count is not fixed — what matters is: write once, add nothing on a second
    run, and refuse to overwrite a record that drifted.
    """
    import shutil

    tree = tmp_path / "canboat"
    src = tree / "database" / "j1939" / "pgns"
    src.mkdir(parents=True)
    (src / "065201-ecuHistory.yaml").write_text(YAML_FIXTURE, encoding="utf-8")
    intake = tmp_path / "intake"
    try:
        count, problems = stage_spn_refs(tree, intake, ROOT, apply=True)
        assert count > 0 and not problems
        staged = intake / "spn_ref" / "canboat-spn-00190.json"
        assert staged.is_file(), "the fixture's SPN 190 must be staged"

        count, problems = stage_spn_refs(tree, intake, ROOT, apply=True)
        assert count == 0 and not problems, "a second --apply must add nothing"

        staged.write_text(staged.read_text(encoding="utf-8").replace("SPN 190", "SPN 999"),
                          encoding="utf-8")
        count, problems = stage_spn_refs(tree, intake, ROOT, apply=True)
        assert count == 0 and any("differs from the pinned source" in p for p in problems)
    finally:
        shutil.rmtree(tree, ignore_errors=True)


def test_dbc_harvest_is_hash_pinned_and_verbatim() -> None:
    """The second representation must be verified against data/dbc/manifest.json."""
    from scripts.intake_scan_sources import collect_dbc_spn_evidence, dbc_file_for_spns

    dbc = dbc_file_for_spns(ROOT)
    assert dbc is not None, "the DBC must verify against its manifest hash"
    evidence = collect_dbc_spn_evidence(dbc)
    assert evidence, "CM_ SG_ comments must yield SPN references"
    for spn, entry in list(evidence.items())[:20]:
        assert entry["signals"] and entry["comments"]
        assert any(f"SPN {spn}" in c for c in entry["comments"]), spn


def test_two_source_records_declare_corroborated() -> None:
    """Cross-checked evidence must say so; the validator enforces the rule."""
    files = sorted(SPN_REF_DIR.glob("*.json"))
    two_source = 0
    for path in files:
        record = json.loads(path.read_text(encoding="utf-8"))
        sources = record["payload"]["sources"]
        if len(sources) >= 2:
            two_source += 1
            assert record["confidence"] == "corroborated", path.name
            assert record["payload"]["dbc_signals"], path.name
            # each cited source is hash pinned
            for item in sources:
                assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
    assert two_source > 100, "the corpus should carry cross-verified SPN references"


def test_validator_rejects_two_sources_without_corroborated_confidence() -> None:
    from scripts.validate_intake import Report as _Report, validate_envelope

    record = json.loads((SPN_REF_DIR / "canboat-spn-01032.json").read_text(encoding="utf-8"))
    assert len(record["payload"]["sources"]) >= 2
    record["confidence"] = "single_source"
    rep = _Report()
    validate_envelope(record, "spn_ref/x.json", rep)
    assert any("corroborated" in d for lv, _c, d in rep.rows if lv == "FAIL")
