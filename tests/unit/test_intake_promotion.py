"""The intake records promoted by scripts/promote_intake.py are in the knowledge base and gone from intake."""

from __future__ import annotations

from pathlib import Path

from src.engine.ai.knowledge_base import KnowledgeBase

KB = KnowledgeBase()
INTAKE = Path(__file__).resolve().parents[2] / "data" / "intake"


def test_promoted_spns_resolve_with_their_canboat_source() -> None:
    look = KB.spn(1032)
    assert look.found and look.record["name"] == "Total ECU Distance" and look.record["unit"] == "km"
    assert look.record["_source_license"] == "Apache-2.0"
    assert not (INTAKE / "spn_ref" / "canboat-spn-01032.json").exists()


def test_promoted_j1939_pgn_layout_is_found() -> None:
    look = KB.pgn(65262)
    assert look.found
    assert not (INTAKE / "pgn").glob("*065262*") or not list((INTAKE / "pgn").glob("*065262*"))


def test_j1939_parameter_names_resolve_to_canonical_signals() -> None:
    assert KB.canonical_signal("Engine RPM") == "EngineSpeed"
    assert KB.canonical_signal("Intake_Manifold_Temp") == "IntakeAirTemp"
    assert KB.canonical_signal("Engine Coolant Temp") == "CoolantTemp"


def test_retired_intake_files_left_no_manifest_rows() -> None:
    manifest = (INTAKE / "MANIFEST.md").read_text(encoding="utf-8")
    assert "canboat-spn-01032" not in manifest and "wal33d-divergence-ford" not in manifest
