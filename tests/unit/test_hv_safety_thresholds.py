# -*- coding: utf-8 -*-
"""Test suite for High-Voltage Safety Thresholds Database — Aksiyon 31 / Faz 6.1.

Validates:
- JSON loading and schema conformity.
- Inviolate values: 100 Ω/V, 7 MΩ, 300 s (5 min), 6200 kΩ, 20 mA, 12 V ±0.6 V.
- Mandatory provenance fields (authority, clause, url, provenance_id, confidence, verbatim).
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

DIAG_DIR = Path(__file__).resolve().parents[2] / "data" / "diagnostics"
HV_THRESHOLDS_PATH = DIAG_DIR / "hv_safety_thresholds.json"


def test_hv_safety_thresholds_file_exists() -> None:
    assert HV_THRESHOLDS_PATH.exists(), f"Eksik dosya: {HV_THRESHOLDS_PATH}"


def test_hv_safety_thresholds_content() -> None:
    data = json.loads(HV_THRESHOLDS_PATH.read_text(encoding="utf-8"))
    assert "thresholds" in data
    thresholds = data["thresholds"]

    # Aksiyon 31 zorunlu 5 eşik + GB/T eşiği
    expected_keys = [
        "min_isolation_resistance_dc",
        "isolation_resistance_testbed_threshold",
        "thermal_runaway_warning_advance",
        "gm_field_normal_isolation",
        "tesla_hvil_nominal_current",
        "gbt_control_pilot_pullup",
    ]

    for key in expected_keys:
        assert key in thresholds, f"Eksik eşik anahtarı: {key}"
        t = thresholds[key]
        assert "value" in t
        assert "unit" in t
        assert "authority" in t and len(t["authority"]) > 0
        assert "clause" in t and len(t["clause"]) > 0
        assert "url" in t and t["url"].startswith("http")

        # Provenance doğrulaması (§F.2)
        assert "provenance" in t
        prv = t["provenance"]
        assert "provenance_id" in prv
        assert "target" in prv
        assert "source" in prv
        assert prv["source"].get("path", "").startswith("http")
        assert prv.get("confidence") in ("operator_verified", "corroborated", "single_source")
        assert "verbatim" in prv and len(prv["verbatim"]) > 0

    # Değer kontrolleri
    assert thresholds["min_isolation_resistance_dc"]["value"] == 100.0
    assert thresholds["min_isolation_resistance_dc"]["unit"] == "Ω/V"

    assert thresholds["isolation_resistance_testbed_threshold"]["value"] == 7.0
    assert thresholds["isolation_resistance_testbed_threshold"]["unit"] == "MΩ"

    assert thresholds["thermal_runaway_warning_advance"]["value"] == 300.0
    assert thresholds["thermal_runaway_warning_advance"]["unit"] == "s"

    assert thresholds["gm_field_normal_isolation"]["value"] == 6200.0
    assert thresholds["gm_field_normal_isolation"]["unit"] == "kΩ"

    assert thresholds["tesla_hvil_nominal_current"]["value"] == 20.0
    assert thresholds["tesla_hvil_nominal_current"]["unit"] == "mA"

    assert thresholds["gbt_control_pilot_pullup"]["value"] == 12.0
    assert thresholds["gbt_control_pilot_pullup"]["tolerance"] == 0.6
    assert thresholds["gbt_control_pilot_pullup"]["unit"] == "V"
