"""Vehicle/ECU identification: OBD Mode 09 and J1939 VI / DM19 / SOFT / CI, and what the copilot does with it."""

from __future__ import annotations

import asyncio
import time

from src.core.models.can_frame import CanFrame
from src.engine.ai.copilot_answer import answer_query
from src.engine.ai.query_understanding import parse_query
from src.engine.diagnosis.j1939_reader import SimulatedJ1939Ecu, read_j1939_snapshot
from src.engine.diagnosis.obd_reader import SimulatedObdEcu, read_obd_fault_codes
from src.protocols.j1939.identification import decode_ci, decode_dm19, decode_soft, decode_vi
from src.protocols.obd import mode09
from src.safety.read_only_policy import ReadOnlyPolicy

VW_VIN = "WVWZZZ1KZS1M00001"


# ---------------------------------------------------------------- decoding
def test_j1939_identification_decoders() -> None:
    assert decode_vi(b"YV2XSM0A0S1000001*\xff\xff") == "YV2XSM0A0S1000001"
    (cal,) = decode_dm19(bytes([0x4D, 0x3C, 0x2B, 0x1A]) + b"CAL-1".ljust(16, b"\x00"))
    assert (cal.cal_id, cal.cvn) == ("CAL-1", "1A2B3C4D")
    assert decode_dm19(bytes(4) + bytes([0x80]) * 16) == [], "a non-ASCII calibration ID is dropped, not repaired"
    assert decode_soft(b"\x02A-1*B-2*") == ["A-1", "B-2"]
    assert decode_ci(b"MAKE*MODEL**UNIT*") == {"make": "MAKE", "model": "MODEL", "unit": "UNIT"}


def test_mode09_decoders() -> None:
    assert mode09.decode_vin(bytes([0x49, 0x02, 0x01]) + VW_VIN.encode()) == VW_VIN
    assert mode09.decode_cal_ids(bytes([0x49, 0x04, 0x02]) + b"A1".ljust(16, b"\x00") + b"B2".ljust(16, b"\x00")) \
        == ["A1", "B2"]
    assert mode09.decode_cvns(bytes([0x49, 0x06, 0x01, 0x1A, 0x2B, 0x3C, 0x4D])) == ["1A2B3C4D"]
    assert mode09.decode_ecu_name(bytes([0x49, 0x0A, 0x01]) + b"ECM\x00-EngineControl".ljust(20, b"\x00")) \
        == "ECM-EngineControl"


# ---------------------------------------------------------------- policy
def test_j1939_identification_requests_are_read_only() -> None:
    now = time.monotonic_ns()
    policy = ReadOnlyPolicy(expires_ns=now + 10**10, j1939=True)
    for pgn in (65260, 54016, 65242, 65259):
        frame = CanFrame.create(channel_id="t", arbitration_id=0x18EA00F9,
                                data=bytes([pgn & 0xFF, (pgn >> 8) & 0xFF, pgn >> 16]) + b"\xff" * 5,
                                is_extended=True, direction="tx")
        assert policy.violation(frame, now) is None, pgn


# ---------------------------------------------------------------- readers
def test_obd_reader_gets_mode09_identity() -> None:
    ecu = SimulatedObdEcu()
    out = asyncio.run(read_obd_fault_codes(ecu, ecu.subscribe, channel_id="sim_obd", timeout_s=0.3))
    assert out.identity == {"vin": VW_VIN, "calibrations": [{"cal_id": "SIMCAL-0001", "cvn": "1A2B3C4D"}],
                            "ecu_name": "ECM-EngineControl", "ecu": "Motor kontrol ünitesi",
                            "protocol": "OBD Mode 09"}


def test_j1939_reader_gets_vi_dm19_soft_ci() -> None:
    ecu = SimulatedJ1939Ecu()
    out = asyncio.run(read_j1939_snapshot(ecu, ecu.subscribe, channel_id="sim_j1939", spns=(3251,), timeout_s=0.3))
    ident = out.identity
    assert ident["vin"] == "YV2XSM0A0S1000001" and ident["protocol"] == "J1939"
    assert ident["calibrations"] == [{"cal_id": "SIMCAL-HD-0001", "cvn": "1A2B3C4D"}]
    assert ident["software"] == ["SIM-SW-1.0"] and ident["component"]["model"] == "ENGINE-ECU"
    assert {(f.arbitration_id >> 16) & 0xFF for f in ecu.sent} <= {0xEA, 0xE3, 0xEC}


# ---------------------------------------------------------------- copilot
def test_vin_gives_the_make_when_none_was_selected() -> None:
    pq = parse_query("motor tekliyor", identity={"vin": VW_VIN})
    assert pq.vehicle_make == "volkswagen" and pq.make_source == "vin"
    assert pq.identity.vin_make == "Volkswagen Group" and pq.identity.masked_vin == "WVW**********0001"


def test_selected_make_wins_and_a_contradicting_vin_is_flagged() -> None:
    d = answer_query("motor tekliyor", vehicle_make="Toyota", identity={"vin": VW_VIN}, language="tr").to_dict()
    assert d["understood"]["vehicle_make"] == "Toyota"
    assert "uyuşmuyor" in d["summary"] and "Volkswagen Group" in d["summary"]


def test_shared_wmi_brand_group_gives_no_lookup_make() -> None:
    pq = parse_query("", identity={"vin": "KNAXX000000000001"})
    assert pq.identity.vin_make == "Hyundai-Kia" and pq.identity.vin_make_key == "" and pq.vehicle_make is None


def test_malformed_vin_is_ignored_but_calibration_stays() -> None:
    pq = parse_query("", identity={"vin": "WVWIOQ", "calibrations": [{"cal_id": "CAL-1", "cvn": "1A2B3C4D"}]})
    assert pq.identity.vin == "" and pq.identity.calibrations == (("CAL-1", "1A2B3C4D"),)
    assert parse_query("", identity={"vin": "not a vin"}).identity is None


def test_answer_shows_identity_and_a_calibration_step() -> None:
    d = answer_query("", dtcs=["P0301"], language="tr", identity={
        "vin": VW_VIN, "calibrations": [{"cal_id": "SIMCAL-0001", "cvn": "1A2B3C4D"}], "protocol": "OBD Mode 09"},
    ).to_dict()
    assert d["technical"]["identity"]["vin"] == "WVW**********0001"
    assert VW_VIN not in str(d), "the full VIN never appears in the answer"
    assert "Araç kimliği: VIN WVW**********0001 (Volkswagen Group, WMI); kalibrasyon SIMCAL-0001." in d["summary"]
    assert any(s["refs"] == ["template:calibration_check"] for s in d["steps"])
