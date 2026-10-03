"""J1939 DM4 (freeze frame) and DM7 -> DM30 (scaled test results) for heavy-duty vehicles."""

from __future__ import annotations

import asyncio
import time

from src.core.models.can_frame import CanFrame
from src.engine.ai.copilot_answer import answer_query
from src.engine.diagnosis.j1939_reader import SimulatedJ1939Ecu, read_j1939_snapshot, spns_from_codes
from src.engine.diagnosis.scan import ScanRequest, ScanRunner, SimulatorScanBackend
from src.protocols.j1939.dm_results import decode_dm4, decode_dm30, dm7_report_payload
from src.safety.read_only_policy import ReadOnlyPolicy


# ---------------------------------------------------------------- decoding
def test_dm4_decodes_the_required_parameters() -> None:
    (ff,) = decode_dm4(SimulatedJ1939Ecu.FREEZE_FRAME)
    assert ff.dtc == "SPN 3251 FMI 0" and ff.occurrence_count == 2
    assert ff.readings == {"BoostPressure": 160.0, "EngineSpeed": 1450.0, "EngineLoad": 82.0,
                           "CoolantTemp": 88.0, "VehicleSpeed": 64.0}


def test_dm4_leaves_not_available_parameters_out_and_stops_on_a_bad_length() -> None:
    frame = bytes([12, 0xB3, 0x0C, 0x00, 0x01, 0x00, 0xFF, 0xFF, 0xFF, 82, 0xFE, 0xFF, 0xFF])
    (ff,) = decode_dm4(frame + bytes([40, 1, 2]))  # second frame claims 40 bytes but has 2
    assert ff.readings == {"EngineLoad": 82.0}
    assert decode_dm4(bytes([12, 0, 0, 0, 0] + [0] * 8)) == [], "an all-zero DTC is no freeze frame"


def test_dm30_verdict_uses_the_ecu_limits_and_allows_one_sided_limits() -> None:
    fail, ok = decode_dm30(SimulatedJ1939Ecu.DM30_TESTS[3251])
    assert (fail.spn, fail.fmi, fail.tid, fail.raw_value, fail.raw_max, fail.raw_min, fail.passed) == \
        (3251, 0, 10, 520, 400, None, False)
    assert (ok.raw_value, ok.raw_min, ok.raw_max, ok.passed) == (35, 20, 60, True)
    not_done = bytes([12, 0xB3, 0x0C, 0x00, 0x01, 0x00, 0x00, 0xFB, 0x90, 0x01, 0xFF, 0xFF])
    assert decode_dm30(not_done) == [], "a test that has not completed has no result"


def test_dm7_payload_only_asks_for_stored_results() -> None:
    assert dm7_report_payload(3251) == bytes([247, 0xB3, 0x0C, 0x1F, 0xFF, 0xFF, 0xFF, 0xFF])


# ---------------------------------------------------------------- policy
def _frame(arbitration_id: int, data: bytes) -> CanFrame:
    return CanFrame.create(channel_id="t", arbitration_id=arbitration_id, data=data, is_extended=True,
                           direction="tx")


def test_j1939_policy_allows_only_read_requests() -> None:
    policy = ReadOnlyPolicy(expires_ns=time.monotonic_ns() + 10**10, j1939=True)
    now = time.monotonic_ns()
    dm4 = _frame(0x18EA00F9, bytes([0xCD, 0xFE, 0x00]) + b"\xff" * 5)
    dm11 = _frame(0x18EA00F9, bytes([0xD3, 0xFE, 0x00]) + b"\xff" * 5)
    dm7_report = _frame(0x18E300F9, dm7_report_payload(3251))
    dm7_start = _frame(0x18E300F9, bytes([5, 0xB3, 0x0C, 0x1F, 0xFF, 0xFF, 0xFF, 0xFF]))
    cts = _frame(0x1CEC00F9, bytes([0x11, 2, 1, 0xFF, 0xFF, 0x00, 0xA4, 0x00]))
    rts = _frame(0x1CEC00F9, bytes([0x10, 24, 0, 4, 0xFF, 0x00, 0xA4, 0x00]))
    tsc1 = _frame(0x0C0000F9, b"\x00" * 8)
    assert policy.violation(dm4, now) is None
    assert policy.violation(dm7_report, now) is None
    assert policy.violation(cts, now) is None
    assert policy.violation(dm11, now) == "READ_ONLY_SERVICE"
    assert policy.violation(dm7_start, now) == "READ_ONLY_SERVICE", "a DM7 that starts a test is refused"
    assert policy.violation(rts, now) == "READ_ONLY_PAYLOAD", "the tool never sends data over TP"
    assert policy.violation(tsc1, now) == "READ_ONLY_ID"
    assert ReadOnlyPolicy(expires_ns=now + 10**10).violation(dm4, now) == "READ_ONLY_ID", \
        "a passenger-car read session still refuses every J1939 frame"


# ---------------------------------------------------------------- reader
def _read(spns: tuple[int, ...] = (3251,)) -> tuple[SimulatedJ1939Ecu, object]:
    ecu = SimulatedJ1939Ecu()
    return ecu, asyncio.run(read_j1939_snapshot(ecu, ecu.subscribe, channel_id="sim_j1939", spns=spns,
                                                timeout_s=0.3))


def test_reader_gets_dm4_and_dm30_over_the_transport_protocol() -> None:
    ecu, out = _read()
    assert out.status == "ok"
    assert out.freeze_frame["dtc"] == "SPN 3251 FMI 0" and out.freeze_frame["readings"]["EngineSpeed"] == 1450.0
    assert [(m["spn"], m["tid"], m["value"], m["max"], m["passed"]) for m in out.monitors] == \
        [(3251, 10, 520.0, 400.0, False), (3251, 11, 35.0, 60.0, True)]
    pfs = {(f.arbitration_id >> 16) & 0xFF for f in ecu.sent}
    assert pfs <= {0xEA, 0xE3, 0xEC}, "only requests, DM7 report and TP receiver frames went out"


def test_reader_asks_dm30_for_unknown_spns_and_survives_the_nack() -> None:
    _ecu, out = _read(spns=(110,))
    assert out.status == "ok" and {m["spn"] for m in out.monitors} == {3251}, "the freeze frame's SPN is asked too"


def test_spns_come_from_dm1_event_codes() -> None:
    assert spns_from_codes(["SPN 3251 FMI 0", "P0301", "SPN 3251 FMI 16", "SPN 110 FMI 0"]) == [3251, 110]


def test_truck_scan_with_read_permission_keeps_the_j1939_snapshot() -> None:
    runner = ScanRunner(lambda req: SimulatorScanBackend(req.vehicle_type, "ok", lambda s: {}, frame_sleep=False))
    runner.start(ScanRequest(vehicle_label="Sim", vehicle_type="truck", simulator=True, allow_read=True,
                             listen_seconds=0.1))
    runner.wait()
    assert runner.last_obd is not None and runner.last_obd.freeze_frame["dtc"] == "SPN 3251 FMI 0"


# ---------------------------------------------------------------- copilot
def _answer(language: str = "tr") -> dict:
    _ecu, out = _read()
    return answer_query("", dm1=[{"spn": 3251, "fmi": 0}], freeze_frame=out.freeze_frame,
                        monitors=out.monitors, language=language).to_dict()


def test_j1939_freeze_frame_gives_the_fault_moment_state() -> None:
    d = _answer()
    ff = d["technical"]["freeze_frame"]
    assert ff["dtc"] == "SPN 3251 FMI 0" and ff["state"].startswith("yük altında")
    assert "Arıza anı (SPN 3251 FMI 0, freeze frame)" in d["summary"]


def test_failed_dm30_test_is_a_cited_candidate() -> None:
    d = _answer()
    mon = next(c for c in d["causes"] if c["kind"] == "monitor")
    assert mon["title"].startswith("ECU testi başarısız: DPF")
    assert any("J1939 DM30" in e["text"] and "≤ 400" in e["text"] for e in mon["support"])
    assert "ECU testleri (J1939 DM30): 1 başarısız" in d["summary"]
    assert any(r["ref"] == "j1939_spn_fmi#SPN_3251" for r in d["urgency"]["reasons"])
    assert any(s["refs"] == ["template:dm30_verify"] for s in d["steps"])
    rows = d["technical"]["monitors"]
    assert rows[0]["min"] is None and rows[0]["protocol"] == "J1939 DM30"


def test_english_dm30_answer() -> None:
    d = _answer("en")
    assert any(c["title"].startswith("ECU test failed: Aftertreatment 1 Diesel Particulate Filter")
               for c in d["causes"])


def test_malformed_dm30_records_are_dropped() -> None:
    d = answer_query("", dm1=[{"spn": 3251, "fmi": 0}], monitors=[
        {"spn": 3251, "fmi": 0, "tid": 1, "value": 5, "min": None, "max": None, "passed": True},
        {"spn": 0, "fmi": 0, "tid": 1, "value": 5, "max": 4, "passed": False},
        {"spn": 3251, "fmi": 40, "tid": 1, "value": 5, "max": 4, "passed": False},
    ], language="tr").to_dict()
    assert "monitors" not in d["technical"]
