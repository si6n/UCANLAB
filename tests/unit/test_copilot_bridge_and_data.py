"""Bridge integration of the structured copilot + the data validation gate."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from src.engine.ai.diagnostic_copilot import AiDiagnosticCopilot
from src.ui.desktop_app import DesktopApiBridge, UniversalCanDesktopApp

ROOT = Path(__file__).resolve().parents[2]


def _app() -> UniversalCanDesktopApp:
    return UniversalCanDesktopApp(channel="vcan0", bitrate=250000)


def _dm1_frame(spn: int, fmi: int):
    from src.core.models.can_frame import CanFrame

    data = bytes([0x04, 0xFF, spn & 0xFF, (spn >> 8) & 0xFF, (((spn >> 16) & 0x7) << 5) | (fmi & 0x1F), 0x01, 0xFF, 0xFF])
    return CanFrame.create(channel_id="vcan0", arbitration_id=0x18FECA00, data=data, is_extended=True)


def test_copilot_class_exposes_structured_answer() -> None:
    answer = AiDiagnosticCopilot().answer("SPN 110 FMI 0", telemetry={"CoolantTemp": 112})
    d = answer.to_dict()
    assert d["urgency"]["level"] == "RED"
    assert d["causes"][0]["confidence"] == "high"


def test_bridge_method_is_in_the_risk_manifest_as_read_only() -> None:
    assert DesktopApiBridge.BRIDGE_RISK_MANIFEST["ask_copilot_structured"] == "read"


def test_bridge_structured_query_returns_six_sections() -> None:
    bridge = DesktopApiBridge(_app())
    res = bridge.ask_copilot_structured("motor ısınıyor, DPF lambası yandı")
    assert res["success"] is True
    answer = res["answer"]
    for key in ("summary", "urgency", "causes", "steps", "missing_data", "technical", "markdown"):
        assert key in answer
    assert "engine-overheating" in [s["id"] for s in answer["understood"]["symptoms"]]


@pytest.mark.parametrize("bad", [None, 42, "x" * 9000])
def test_bridge_structured_query_rejects_bad_input(bad: object) -> None:
    res = DesktopApiBridge(_app()).ask_copilot_structured(bad)  # type: ignore[arg-type]
    assert res["success"] is False and res["code"] == "INVALID_COPILOT_QUERY"


def test_bridge_passes_answers_to_the_copilot() -> None:
    res = DesktopApiBridge(_app()).ask_copilot_structured(
        "akü bitiyor", "tr", {"battery-drain-parasitic.q0": 320, "battery-drain-parasitic.q1": "hayır"})
    assert res["success"] is True
    checks = {c["key"]: c for c in res["answer"]["checks"]}
    assert checks["battery-drain-parasitic.q0"]["answer_text"] == "320 mA"
    assert res["answer"]["causes"][0]["title"] == "Gövde Kontrol Modülü (BCM)"


def test_bridge_accepts_an_empty_query_only_with_answers() -> None:
    bridge = DesktopApiBridge(_app())
    # answering the live-session answer's questions: no sentence typed
    assert bridge.ask_copilot_structured("", "tr", {"misfire-cylinder-1.q0": "evet"})["success"] is True
    assert bridge.ask_copilot_structured("", "tr")["code"] == "INVALID_COPILOT_QUERY"
    assert bridge.ask_copilot_structured("", "tr", {})["code"] == "INVALID_COPILOT_QUERY"


@pytest.mark.parametrize("bad", [
    ["yes"], {"../../etc": "yes"}, {"battery-drain-parasitic.q0": float("nan")},
    {"battery-drain-parasitic.q0": {"nested": 1}}, {"battery-drain-parasitic.q0": "x" * 40},
    {f"s{i}.q0": "yes" for i in range(41)},
])
def test_bridge_rejects_malformed_answers(bad: object) -> None:
    res = DesktopApiBridge(_app()).ask_copilot_structured("akü bitiyor", "tr", bad)  # type: ignore[arg-type]
    assert res["success"] is False and res["code"] == "INVALID_COPILOT_QUERY"


def test_analysis_payload_carries_structured_answer_for_live_dm1() -> None:
    app = _app()
    app._decode_j1939_signal(_dm1_frame(spn=100, fmi=1))
    analysis = app.get_diagnostic_analysis()
    assert analysis["success"] is True
    sa = analysis["structured_answer"]
    assert [s["spn"] for s in sa["understood"]["spns"]] == [100]
    assert sa["urgency"]["level"] == "RED"
    assert sa["causes"], "SPN 100 FMI 1 must produce ranked causes"
    # the existing keys are untouched (additive change)
    assert {"user_card", "report", "gate", "hypotheses"} <= set(analysis)


def test_structured_answer_failure_never_breaks_the_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app()

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("composer bug")

    monkeypatch.setattr(app.copilot, "answer", boom)
    analysis = app.get_diagnostic_analysis()
    assert analysis["success"] is True
    assert "structured_answer" not in analysis
    assert DesktopApiBridge(app).ask_copilot_structured("P0101")["success"] is False


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_copilot_data", ROOT / "scripts" / "validate_copilot_data.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_copilot_data"] = module
    spec.loader.exec_module(module)
    return module


def test_data_validation_gate_has_no_failures() -> None:
    rep = _load_validator().run(quiet=True)
    fails = [r for r in rep.rows if r[0] == "FAIL"]
    assert not fails, fails[:10]
    assert rep.metrics["dtc_records_schema_valid"] == rep.metrics["dtc_records_total"]
    assert rep.metrics["provenance_blocks_validated"] >= 80


def test_copilot_data_files_regenerate_identically(tmp_path: Path) -> None:
    """The generator scripts are the single source: running them must not change the files."""
    import subprocess

    targets = ["symptom_lexicon.json", "signal_measurement_map.json", "copilot_glossary.json", "symptom_checks.json",
               "subsystem_labels_en.json", "graph_title_i18n.json",
               "reasoning_rules.json", "operating_scenarios.json"]
    # Windows checkouts may carry CRLF (core.autocrlf); compare content, not line endings.
    def read(t: str) -> bytes:
        return (ROOT / "data" / "diagnostics" / t).read_bytes().replace(b"\r\n", b"\n")

    before = {t: read(t) for t in targets}
    for script in ("build_lexicon.py", "build_signal_map.py", "build_glossary.py", "build_symptom_checks.py",
                   "build_subsystem_labels.py", "build_graph_titles.py",
                   "build_reasoning_rules.py", "build_operating_scenarios.py"):
        subprocess.run([sys.executable, str(ROOT / "scripts" / "copilot_data" / script)], check=True,
                       capture_output=True, cwd=ROOT)
    after = {t: read(t) for t in targets}
    assert before == after
