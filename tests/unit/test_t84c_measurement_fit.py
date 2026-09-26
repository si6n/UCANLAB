"""T84c: the measurement-tolerance fit guard.

MEASURED FINDING (2026-09-26, gap-analysis agent; verified and extended by
me): the `measurement` field carries 41 distinct values across 9,300 records;
five template values cover 9,262 (99.6%), and NONE carries a provenance key —
unlike every other harvested field. The stated tolerance fits the record's own
subject only rarely:

    value (records)                          title names its system
    --------------------------------------   ----------------------
    "Sensör besleme 5.0V ±0.1V ..." (6,437)          1.2%
    "Hava yastığı devre direnci ..." (1,286)         5.5%
    "Tekerlek hız sensör direnci ..." (652)         19.9%
    "CAN High 2.5-3.5V ..." (537)                   34.6%
    "Standart VAG OEM toleransları" (350)          100.0%

So for P0AD9 (Hybrid Battery Positive Contactor) the report printed "Sensör
besleme 5.0V ±0.1V, Şasi direnci < 1.0 Ω" — an HV contactor is not a 5V
sensor circuit. `Tuzaklar-ve-Dersler` §60 calls a fabricated tolerance the
most dangerous fabrication: a technician measures against it and replaces a
healthy part.

The guard withholds a tolerance whose stated system the record does not
mention; stage 2 then renders the explicit gap T67-A established. The value
stays in the DB — it is simply not asserted for a record it does not describe.
"""

import json

import pytest

from src.engine.ai.diagnostic_copilot import CausalBayesianInferenceEngine

DTC_DB = "data/diagnostics/dtc_database.json"
E = CausalBayesianInferenceEngine

V_5V = ("Standart OEM elektriksel toleranslar: Sensör besleme 5.0V ±0.1V, "
        "Şasi direnci < 1.0 Ω.")
V_AIR = ("Hava yastığı / ön gergi devre direnci: 1.8 - 3.4 Ω "
         "(Güvenlik limitleri dahilinde).")
V_CAN = ("CAN High: 2.5V - 3.5V, CAN Low: 1.5V - 2.5V, "
         "Terminasyon Direnci: 60 Ω (±2 Ω).")
V_WS = ("Tekerlek hız sensör direnci: 1.0 - 2.5 kΩ veya aktif hall-effect "
        "7mA / 14mA akım darbeleri.")


@pytest.fixture(scope="module")
def db():
    with open(DTC_DB, encoding="utf-8") as fh:
        return json.load(fh)


class TestWithheldTolerances:
    @pytest.mark.parametrize("code,value", [
        # HV contactor: a 5V sensor supply does not describe it
        ("P0AD9", V_5V),
        # HV battery voltage sense
        ("P0B59", V_5V),
        # a misfire is not a 5V reference circuit
        ("P0301", V_5V),
    ])
    def test_mismatched_tolerance_is_withheld(self, db, code, value):
        assert not E._measurement_fits_record(value, db[code]), code

    def test_the_5v_template_is_withheld_from_the_vast_majority(self, db):
        keep = drop = 0
        for e in db.values():
            if not isinstance(e, dict) or str(e.get("measurement") or "") != V_5V:
                continue
            if E._measurement_fits_record(V_5V, e):
                keep += 1
            else:
                drop += 1
        assert drop > 5000, f"only {drop} withheld — the guard stopped working"
        assert keep > 50, "the guard is withholding everything"


class TestKeptTolerances:
    @pytest.mark.parametrize("code,value", [
        ("P0658", V_5V),     # "Actuator supply voltage" -> 5V reference
        ("C0050", V_WS),     # wheel speed circuit + wheel tolerance
        ("U0000", V_CAN),    # a network code + CAN voltages
    ])
    def test_matching_tolerance_is_kept(self, db, code, value):
        assert E._measurement_fits_record(value, db[code]), code

    def test_can_template_survives_on_can_codes(self, db):
        """CAN voltages describe CAN codes; the few withheld lack the wording."""
        drop = sum(1 for e in db.values()
                   if isinstance(e, dict)
                   and str(e.get("measurement") or "") == V_CAN
                   and not E._measurement_fits_record(V_CAN, e))
        assert drop <= 5, f"{drop} CAN codes lost their CAN tolerance"


class TestSafetyProperties:
    def test_empty_measurement_is_not_a_fit(self):
        assert not E._measurement_fits_record("", {})

    def test_unspecific_tolerance_is_kept(self):
        """A tolerance naming no system asserts nothing specific."""
        assert E._measurement_fits_record(
            "Standart OEM toleransları dahilindedir.",
            {"title": "Cylinder #1 Misfire"})

    def test_placeholder_title_does_not_defeat_the_guard(self, db):
        """A placeholder title must not cause a false withhold when the
        record's other layers name the system."""
        got = E._measurement_fits_record(V_AIR, db["B0070"])
        assert got, "B0070's seatbelt pretensioner must keep the airbag loop"


class TestRendererIntegration:
    @pytest.fixture(scope="class", autouse=True)
    def _loaded(self):
        from src.engine.ai.diagnostic_copilot import (
            ensure_external_dtc_database_loaded,
        )
        ensure_external_dtc_database_loaded()

    def test_stage2_renders_a_gap_when_the_tolerance_is_withheld(self):
        """The withheld path must be the explicit gap, never blank or invented."""
        report = E._format_4stage_technician_report("P0AD9", {})
        assert "Aşama 2" in report
        # the fabricated 5V tolerance must not appear for this HV code
        assert "5.0V ±0.1V" not in report

    def test_stage2_still_prints_a_matching_tolerance(self):
        report = E._format_4stage_technician_report("U0000", {})
        assert "Aşama 2" in report
        assert "CAN High" in report
