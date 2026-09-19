"""Unit tests for AI Engine Roadmap FAZ 2 (Coverage Factory & Symptom Mapping)."""


import pytest

from src.engine.ai.golden_cases import load_all_cases
from src.engine.ai.procedure_validator import (
    ProcedureSchemaError,
    load_all_procedures,
    validate_procedure_payload,
)
from src.engine.ai.symptom_mapper import (
    map_symptoms_to_systems,
)


def test_valid_procedure_payload() -> None:
    payload = {
        "schema_version": 1,
        "dtc": "P0101",
        "system": "Air Intake",
        "symptoms": ["loss of power"],
        "questions": [{"id": "Q1", "kind": "yes_no", "text": "Check filter"}],
        "measurement_steps": [{"step": 1, "target": "MAF", "unit": "V"}],
        "expected_values": {"voltage_v": 12.0},
        "safety_notes": ["Wear gloves"],
    }
    proc = validate_procedure_payload(payload)
    assert proc.dtc == "P0101"
    assert proc.schema_version == 1
    assert "voltage_v" in proc.expected_values


def test_procedure_physical_bounds_fail_closed() -> None:
    # Plausible voltage is <= 48V. 120V must fail closed!
    bad_payload = {
        "schema_version": 1,
        "dtc": "P0101",
        "system": "Air Intake",
        "symptoms": ["loss of power"],
        "questions": [{"id": "Q1", "kind": "yes_no", "text": "Check filter"}],
        "measurement_steps": [],
        "expected_values": {"voltage_v": 120.0},
        "safety_notes": [],
    }
    with pytest.raises(ProcedureSchemaError, match="Physical value out of plausible bounds"):
        validate_procedure_payload(bad_payload)


def test_load_all_procedures_from_disk() -> None:
    procs = load_all_procedures()
    assert len(procs) >= 3
    assert "P0101" in procs
    assert "P0300" in procs
    assert "SPN 100" in procs


def test_symptom_mapper_crank_no_start() -> None:
    res = map_symptoms_to_systems("Araç marş basıyor ama çalışmıyor")
    assert "crank-no-start" in res.matched_symptoms
    assert any("Marş" in sub or "Yakıt" in sub for sub in res.suspected_subsystems)
    assert len(res.candidate_dtcs) > 0
    assert len(res.initial_questions) > 0


def test_symptom_mapper_black_smoke() -> None:
    res = map_symptoms_to_systems("Egzozdan yoğun siyah duman atıyor")
    assert "black-smoke" in res.matched_symptoms
    assert any("Hava" in sub or "Turbo" in sub for sub in res.suspected_subsystems)


def test_symptom_mapper_extended_profiles() -> None:
    # DPF
    res_dpf = map_symptoms_to_systems("Partikül filtresi tıkandı, rejenerasyon yapmıyor")
    assert "dpf-regeneration-failed" in res_dpf.matched_symptoms
    assert "P2002" in res_dpf.candidate_dtcs

    # AdBlue / SCR
    res_def = map_symptoms_to_systems("AdBlue bitti uyarısı veriyor, motor çalışmayacak")
    assert "def-scr-adblue-warning" in res_def.matched_symptoms
    assert "SPN 1761" in res_def.candidate_dtcs

    # Transmission
    res_trans = map_symptoms_to_systems("Şanzıman vites kaydırıyor, vuruntu yapıyor")
    assert "transmission-slip-limp" in res_trans.matched_symptoms
    assert "P0700" in res_trans.candidate_dtcs

    # CAN Bus
    res_can = map_symptoms_to_systems("Gösterge çalışmıyor, CAN bus iletişim yok")
    assert "can-bus-communication-loss" in res_can.matched_symptoms
    assert "U0100" in res_can.candidate_dtcs


def test_symptom_mapper_empty_query() -> None:
    res = map_symptoms_to_systems("")
    assert len(res.matched_symptoms) == 0
    assert len(res.candidate_dtcs) == 0


def test_golden_cases_count_meets_milestone() -> None:
    """FAZ 2 milestone: Golden case library expanded to 30+ verified cases."""
    cases = load_all_cases()
    assert len(cases) >= 30


def test_get_dtc_info_enriches_procedure() -> None:
    from unittest.mock import MagicMock

    from src.ui.desktop_app import DesktopApiBridge

    bridge = DesktopApiBridge(MagicMock())
    info = bridge.get_dtc_info("P0101")
    assert "procedure" in info
    assert info["procedure"]["dtc"] == "P0101"
    assert "steps" in info
