"""OBD-II Mode 02 (freeze frame) and Mode 06 (monitor results with the ECU's own limits)."""

from __future__ import annotations

import asyncio

import pytest

from src.engine.ai.copilot_answer import answer_query
from src.engine.diagnosis.obd_reader import SimulatedObdEcu, read_obd_fault_codes
from src.engine.diagnosis.scan import ScanRequest, ScanRunner, SimulatorScanBackend
from src.protocols.obd.mode06 import decode_mode06_response, decode_test_record


# ---------------------------------------------------------------- decoding
def test_test_record_verdict_uses_the_ecu_limits() -> None:
    t = decode_test_record(bytes([0xA2, 0x0B, 0x24, 0x00, 0x29, 0x00, 0x00, 0x00, 0x14]))
    assert (t.mid, t.tid, t.value, t.min, t.max, t.unit, t.passed) == (0xA2, 0x0B, 41.0, 0.0, 20.0, "counts", False)


def test_signed_uasid_compares_as_signed() -> None:
    # UASID 0x83 (signed, 0.01): value -0.05 within -0.10 .. +0.10
    t = decode_test_record(bytes([0x01, 0x10, 0x83, 0xFF, 0xFB, 0xFF, 0xF6, 0x00, 0x0A]))
    assert t.passed and (t.value, t.min, t.max) == (-0.05, -0.1, 0.1)


def test_unknown_uasid_is_shown_raw_but_still_judged() -> None:
    t = decode_test_record(bytes([0x21, 0x01, 0x7E, 0x00, 0x64, 0x00, 0x00, 0x00, 0x32]))
    assert (t.scaled, t.unit, t.passed) == (False, "raw", False)


def test_supported_mid_bitmask_and_multi_record_answers() -> None:
    mids, tests = decode_mode06_response(bytes([0x46, 0x00, 0x80, 0x00, 0x00, 0x01]))
    assert mids == [0x01, 0x20] and tests == []
    two = bytes([0x46]) + bytes([0x01, 0x01, 0x0A, 0x0E, 0x10, 0x0B, 0xB8, 0x0F, 0xA0]) * 2
    assert len(decode_mode06_response(two)[1]) == 2
    with pytest.raises(ValueError):
        decode_mode06_response(bytes([0x41, 0x00]))


# ---------------------------------------------------------------- reader
def _read() -> object:
    ecu = SimulatedObdEcu()
    return ecu, asyncio.run(read_obd_fault_codes(ecu, ecu.subscribe, channel_id="sim_obd", timeout_s=0.3))


def test_reader_gets_freeze_frame_and_monitors_through_the_read_only_policy() -> None:
    ecu, out = _read()
    assert out.status == "ok"
    assert out.freeze_frame["dtc"] == "P0301"
    assert out.freeze_frame["readings"]["EngineSpeed"] == 780.0 and out.freeze_frame["readings"]["CoolantTemp"] == 91.0
    failed = [m for m in out.monitors if not m["passed"]]
    assert [(m["mid"], m["value"], m["max"]) for m in failed] == [(0xA2, 41.0, 20.0)]
    services = {bytes(f.data)[1] for f in ecu.sent if bytes(f.data)[0] != 0x30}
    assert services <= {0x02, 0x03, 0x06, 0x07, 0x09, 0x0A}, "only read services went out"


def test_reader_without_snapshot_sends_no_mode_02_or_06() -> None:
    ecu = SimulatedObdEcu()
    out = asyncio.run(read_obd_fault_codes(ecu, ecu.subscribe, channel_id="sim_obd", timeout_s=0.3, snapshot=False))
    assert out.freeze_frame is None and out.monitors == []
    assert {bytes(f.data)[1] for f in ecu.sent if bytes(f.data)[0] != 0x30} == {0x03, 0x07, 0x0A}


def test_scan_runner_keeps_the_last_obd_read_and_clears_it_on_a_new_scan() -> None:
    runner = ScanRunner(lambda req: SimulatorScanBackend(req.vehicle_type, "ok", lambda s: {}, frame_sleep=False))
    runner.start(ScanRequest(vehicle_label="Sim", vehicle_type="car", simulator=True, allow_read=True,
                             listen_seconds=0.1))
    runner.wait()
    assert runner.last_obd is not None and runner.last_obd.freeze_frame["dtc"] == "P0301"
    runner.start(ScanRequest(vehicle_label="Sim", vehicle_type="truck", simulator=True, listen_seconds=0.1))
    runner.wait()
    assert runner.last_obd is None


# ---------------------------------------------------------------- copilot
def _answer(**kw: object) -> dict:
    _ecu, out = _read()
    return answer_query("", dtcs=[c.code for c in out.codes], freeze_frame=out.freeze_frame,
                        monitors=out.monitors, language="tr", **kw).to_dict()


def test_freeze_frame_gives_the_fault_moment_state() -> None:
    d = _answer()
    ff = d["technical"]["freeze_frame"]
    assert ff["dtc"] == "P0301" and ff["state"] == "rölantide, sıcak motor"
    assert "Arıza anı (P0301, freeze frame): rölantide, sıcak motor" in d["summary"]
    assert "(rölantide, sıcak motor)" in d["steps"][-1]["text"], "repair is verified in the fault-moment state"


def test_failed_ecu_test_is_a_candidate_and_supports_the_code_causes() -> None:
    d = _answer()
    kinds = {c["kind"] for c in d["causes"]}
    assert "monitor" in kinds
    top = d["causes"][0]
    assert any("ECU testi başarısız" in e["text"] and "41 counts" in e["text"] for e in top["support"])
    assert "1 başarısız" in d["summary"] and "1 sınırda" in d["summary"]
    assert any(r["ref"] == "obd_mode06#0xA2" for r in d["urgency"]["reasons"])


def test_freeze_frame_fuel_trims_confirm_the_lean_code() -> None:
    d = _answer()
    lean = next(c for c in d["causes"] if c["title"].startswith("Bank 1 fakir"))
    assert any("arıza anında (freeze frame)" in e["text"] and "23.44" in e["text"] for e in lean["support"])


def test_english_answer_names_monitors_in_english() -> None:
    d = _answer()
    en = answer_query("", dtcs=["P0301"], monitors=[m for m in _read()[1].monitors], language="en").to_dict()
    assert any(c["title"].startswith("ECU test failed: Misfire Monitor Cylinder 1") or
               c["title"].startswith("ECU test failed:") for c in en["causes"])
    assert d  # Turkish answer built above


def test_malformed_monitor_records_are_dropped_not_repaired() -> None:
    d = answer_query("", dtcs=["P0301"], monitors=[{"mid": "x"}, {"mid": 0xA2, "tid": 1, "value": float("nan"),
                                                                  "min": 0, "max": 1, "passed": False}],
                     language="tr").to_dict()
    assert "monitors" not in d["technical"]
