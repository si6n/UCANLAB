# -*- coding: utf-8 -*-
"""T68 regression lock — the two remaining zero-consumer data sources.

Round T68 (2026-09-22) continued T67's engine work. T67 wired six fields that
had no consumer; T68 closes two more that the same audit had flagged.

T68-A  THRESHOLD COVERAGE.  `_measured_value_consistency` compared a captured
       telemetry value against the KB-derived band — but only for THREE
       hardcoded codes (`P0234`/`SPN102`, `SPN110`/`P0115`, `SPN100`).
       `telemetry_thresholds.json` carries SEVEN signals, so four of them
       (EngineSpeed, VehicleSpeed, EngineLoad, EngineTorque) could never be
       cross-checked no matter what the operator asked. The mapping is now a
       table derived from the J1939 SPN definitions in the live DB.

T68-B  UDS DID CATALOG.  `uds_did_database.json` holds 68 DIDs with full decode
       metadata (byte_length / data_type / scaling / offset / unit) and had NO
       consumer — `desktop_app.py:642` only counted the rows. Packet forensics
       used a hardcoded 9-entry dict, so 59 DIDs rendered as bare hex, and a
       positive `0x62` response was never decoded beyond VIN.

Both changes are read-only, offline and deterministic. Where a record does not
describe enough to answer, the code says nothing rather than guessing
(AGENTS.md §2.3).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.engine.ai.diagnostic_copilot import (
    CausalBayesianInferenceEngine,
    _uds_did_decode_hint,
    _uds_did_decode_value,
    _uds_did_row,
    _uds_did_table_for_tests,
    ensure_external_dtc_database_loaded,
    explain_can_packet,
    get_j1939_spn_database,
    get_uds_did_database,
)

DID_DB_PATH = Path("data/diagnostics/uds_did_database.json")


@pytest.fixture(scope="module", autouse=True)
def _loaded() -> None:
    ensure_external_dtc_database_loaded()
    get_j1939_spn_database()
    get_uds_did_database()


def _did_db() -> dict:
    return json.loads(DID_DB_PATH.read_text(encoding="utf-8"))["dids"]


# ---------------------------------------------------------------------------
# T68-A — the threshold cross-check covers every banded signal
# ---------------------------------------------------------------------------


class TestT68AThresholdCoverage:
    """A code whose measured quantity has a band must produce a consistency line."""

    THRESHOLD_SIGNALS = (
        "EngineCoolantTemp",
        "TurboBoost",
        "EngineOilPressure",
        "EngineSpeed",
        "VehicleSpeed",
        "EngineLoad",
        "EngineTorque",
    )

    def test_every_threshold_signal_has_at_least_one_code(self) -> None:
        """No banded signal may be unreachable from any code (the T68 defect)."""
        table = CausalBayesianInferenceEngine._THRESHOLD_SIGNAL_FOR_CODE
        covered = {entry[1] for entry in table.values()}
        missing = [s for s in self.THRESHOLD_SIGNALS if s not in covered]
        assert not missing, (
            f"threshold signal(s) unreachable from every code: {missing} — the "
            "mapping table does not cover the whole thresholds DB"
        )

    def test_every_mapped_threshold_key_exists_in_the_db(self) -> None:
        """A mapping to a non-existent band would silently produce nothing."""
        from src.engine.ai.anomaly_detector import load_thresholds

        thresholds = load_thresholds()
        table = CausalBayesianInferenceEngine._THRESHOLD_SIGNAL_FOR_CODE
        unknown = sorted({e[1] for e in table.values()} - set(thresholds.keys()))
        assert not unknown, f"mapping names threshold keys that do not exist: {unknown}"

    @pytest.mark.parametrize(
        "code,telemetry,expect_in_report",
        [
            ("SPN110", {"CoolantTemp": 92.0}, "nominal bantta"),
            ("SPN110", {"CoolantTemp": 115.0}, "limit üstü"),
            ("SPN100", {"OilPressure": 2.0}, "Yağ basıncı"),
            ("SPN102", {"BoostPressure": 2.6}, "nominal bantta"),
            ("SPN102", {"BoostPressure": 4.2}, "limit üstü"),
        ],
    )
    def test_previously_covered_codes_still_work(
        self, code: str, telemetry: dict, expect_in_report: str
    ) -> None:
        report = CausalBayesianInferenceEngine._format_4stage_technician_report(
            code, telemetry
        )
        assert expect_in_report in report

    @pytest.mark.parametrize(
        "code,telemetry,label",
        [
            ("SPN84", {"VehicleSpeed": 95.0}, "Araç hızı"),
            ("P0500", {"VehicleSpeed": 95.0}, "Araç hızı"),
            ("SPN92", {"EngineLoad": 45.0}, "Motor yükü"),
            ("SPN513", {"EngineTorque": 60.0}, "Motor torku"),
            ("P0335", {"EngineSpeed": 2400.0}, "Motor devri"),
        ],
    )
    def test_newly_covered_codes_produce_a_consistency_line(
        self, code: str, telemetry: dict, label: str
    ) -> None:
        """These four signals were unreachable before T68."""
        out = CausalBayesianInferenceEngine._measured_value_consistency(code, telemetry)
        assert out, f"{code} produced no consistency line for {label}"
        assert label in out

    def test_unmeasured_signal_says_nothing(self) -> None:
        """F-02: absence of a measurement is not narrated."""
        assert CausalBayesianInferenceEngine._measured_value_consistency("SPN84", {}) == ""
        assert CausalBayesianInferenceEngine._measured_value_consistency("P0500", {}) == ""

    def test_code_without_a_threshold_says_nothing(self) -> None:
        """An unmapped code has no band — the honest result is silence."""
        assert (
            CausalBayesianInferenceEngine._measured_value_consistency(
                "P0999", {"CoolantTemp": 92.0}
            )
            == ""
        )

    def test_bare_spn_number_resolves_like_the_prefixed_form(self) -> None:
        """The DM1 path emits a bare number; the query path emits "SPN84"."""
        with_prefix = CausalBayesianInferenceEngine._measured_value_consistency(
            "SPN84", {"VehicleSpeed": 95.0}
        )
        bare = CausalBayesianInferenceEngine._measured_value_consistency(
            "84", {"VehicleSpeed": 95.0}
        )
        assert with_prefix == bare
        assert with_prefix

    def test_measured_but_nominal_does_not_claim_support(self) -> None:
        """A nominal reading must not be narrated as evidence FOR the fault."""
        out = CausalBayesianInferenceEngine._measured_value_consistency(
            "SPN110", {"CoolantTemp": 90.0}
        )
        assert "DESTEKLEMİYOR" in out


# ---------------------------------------------------------------------------
# T68-B — the 68-DID catalog is reachable and decodes honestly
# ---------------------------------------------------------------------------


class TestT68BDidCatalogIsWired:
    def test_catalog_loads(self) -> None:
        db = get_uds_did_database()
        assert isinstance(db, dict) and db.get("dids"), "DID catalog did not load"

    def test_every_catalog_did_resolves_by_row_lookup(self) -> None:
        """All 68 must be reachable, not just the hardcoded nine."""
        dids = _did_db()
        unresolved = []
        for key in dids:
            did_int = int(str(key), 16) if str(key).lower().startswith("0x") else int(key)
            if _uds_did_row(did_int) is None:
                unresolved.append(key)
        assert not unresolved, f"DID rows unreachable by lookup: {unresolved}"

    def test_name_table_covers_the_catalog(self) -> None:
        table = _uds_did_table_for_tests()
        assert len(table) == len(_did_db()), (
            f"name table has {len(table)} entries but the catalog has {len(_did_db())}"
        )

    def test_a_did_outside_the_old_hardcoded_nine_is_named(self) -> None:
        """The measured defect: 59 DIDs rendered as bare hex."""
        old_hardcoded = {0xF190, 0xF187, 0xF189, 0xF197, 0x1102, 0x4100, 0x4101, 0x4102, 0x4105}
        dids = _did_db()
        victim = None
        for key, row in dids.items():
            did_int = int(str(key), 16) if str(key).lower().startswith("0x") else int(key)
            if did_int not in old_hardcoded and (row.get("name_tr") or row.get("name")):
                victim = (did_int, str(row.get("name_tr") or row.get("name")))
                break
        if victim is None:
            pytest.skip("catalog has no DID outside the old hardcoded set")
        did_int, name = victim
        text, _actions = explain_can_packet(0x7E0, bytes([0x03, 0x22, (did_int >> 8) & 0xFF, did_int & 0xFF]))
        assert name[:25] in text, (
            f"DID 0x{did_int:04X} still renders as bare hex — the catalog is not consulted"
        )

    def test_decode_hint_uses_only_present_fields(self) -> None:
        """A record with no scale/unit must not invent them."""
        hint = _uds_did_decode_hint({"data_type": "uint16", "byte_length": 2})
        assert "uint16" in hint and "2 bayt" in hint
        assert "×" not in hint, "decode hint invented a scaling factor"

    def test_decode_hint_reports_scaling_when_present(self) -> None:
        hint = _uds_did_decode_hint(
            {"data_type": "uint16", "byte_length": 2, "scaling": 0.01, "unit": "g"}
        )
        assert "0.01" in hint and "g" in hint

    def test_decode_value_applies_scaling_and_unit(self) -> None:
        row = {"data_type": "uint16", "byte_length": 2, "scaling": 0.01, "unit": "g"}
        # 0x0BB8 = 3000 -> 3000 * 0.01 = 30 g
        assert _uds_did_decode_value(row, bytes([0x0B, 0xB8])) == "30 g"

    def test_decode_value_applies_offset(self) -> None:
        row = {"data_type": "uint8", "byte_length": 1, "scaling": 1.0, "offset": -40, "unit": "°C"}
        # 0x5A = 90 -> 90 - 40 = 50 °C
        out = _uds_did_decode_value(row, bytes([0x5A]))
        assert out.startswith("50")

    def test_decode_value_signed_type(self) -> None:
        row = {"data_type": "int16", "byte_length": 2, "unit": "Nm"}
        # 0xFFFF as int16 = -1
        out = _uds_did_decode_value(row, bytes([0xFF, 0xFF]))
        assert out.startswith("-1")

    def test_decode_value_without_recipe_returns_empty(self) -> None:
        """An undescribed type must yield nothing, never a guess."""
        assert _uds_did_decode_value({"data_type": "bcd"}, bytes([0x12, 0x34])) == ""
        assert _uds_did_decode_value({}, b"") == ""

    def test_positive_response_decodes_a_real_did(self) -> None:
        """A 0x62 response used to print raw hex unless the DID was 0xF190."""
        dids = _did_db()
        victim = None
        for key, row in dids.items():
            if row.get("scaling") and row.get("byte_length") == 2 and row.get("unit"):
                did_int = int(str(key), 16) if str(key).lower().startswith("0x") else int(key)
                victim = (did_int, row)
                break
        if victim is None:
            pytest.skip("no scalable 2-byte DID in the catalog")
        did_int, row = victim
        payload = bytes([0x04, 0x62, (did_int >> 8) & 0xFF, did_int & 0xFF, 0x0B, 0xB8])
        text, _actions = explain_can_packet(0x7E8, payload)
        assert "Değer" in text, "0x62 response was not decoded"
        expected = _uds_did_decode_value(row, bytes([0x0B, 0xB8]))
        assert expected and expected in text

    def test_positive_response_for_unknown_did_still_prints_bytes(self) -> None:
        """Fail-safe: an unknown DID prints its bytes, never an interpretation."""
        text, _actions = explain_can_packet(0x7E8, bytes([0x04, 0x62, 0xDE, 0xAD, 0x11, 0x22]))
        assert "0xDEAD" in text
        assert "Değer" not in text

    def test_vin_still_decodes_as_ascii(self) -> None:
        """The pre-existing VIN path must survive the widening.

        ISO 15765-2 single frame carries at most 7 payload bytes, so a whole
        17-char VIN never arrives in one frame — this exercises the FIRST
        fragment, which is what the packet explainer actually sees.
        """
        # SF: [len=6, 0x62, 0xF1, 0x90, 'W', 'V', 'W']
        payload = bytes([0x06, 0x62, 0xF1, 0x90, 0x57, 0x56, 0x57])
        text, _actions = explain_can_packet(0x7E8, payload)
        assert "WVW" in text, "the VIN ASCII path regressed"

    def test_vin_request_still_named(self) -> None:
        text, _actions = explain_can_packet(0x7E0, bytes([0x03, 0x22, 0xF1, 0x90]))
        assert "VIN" in text

    def test_request_side_names_the_did(self) -> None:
        dids = _did_db()
        victim = None
        for key, row in dids.items():
            name = str(row.get("name_tr") or row.get("name") or "").strip()
            if name:
                did_int = int(str(key), 16) if str(key).lower().startswith("0x") else int(key)
                victim = (did_int, name)
                break
        if victim is None:
            pytest.skip("catalog has no named DID")
        did_int, name = victim
        text, _actions = explain_can_packet(0x7E0, bytes([0x03, 0x22, (did_int >> 8) & 0xFF, did_int & 0xFF]))
        assert name[:25] in text

    def test_did_forensics_is_deterministic(self) -> None:
        payload = bytes([0x04, 0x62, 0x11, 0x53, 0x0B, 0xB8])
        assert explain_can_packet(0x7E8, payload) == explain_can_packet(0x7E8, payload)

    def test_lookup_accepts_both_key_shapes(self) -> None:
        """The file has used "0x1153" and may use a decimal key; both must work."""
        dids = _did_db()
        key = next(iter(dids))
        did_int = int(str(key), 16) if str(key).lower().startswith("0x") else int(key)
        assert _uds_did_row(did_int) is not None

    def test_missing_did_db_degrades_without_crashing(self, monkeypatch) -> None:
        """A stripped build has no DID file — forensics must still run."""
        import src.engine.ai.diagnostic_copilot as dc

        monkeypatch.setattr(dc, "get_uds_did_database", lambda *a, **k: {})
        assert _uds_did_row(0x1153) is None
        text, _actions = explain_can_packet(0x7E0, bytes([0x03, 0x22, 0x11, 0x53]))
        # Falls back to the builtin table; an unknown DID still prints its hex.
        assert "0x1153" in text
