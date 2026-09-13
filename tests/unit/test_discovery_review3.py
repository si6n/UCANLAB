"""REVIEW 3 regressions: Motorola start-bit mapping, FD DLC byte length, J1939 SA/DA matching."""

from __future__ import annotations

import cantools
from cantools.database.can.database import Database

from src.core.models.can_frame import CanFrame
from src.engine.decoder.dbc_decoder import DbcSignalDecoder
from src.engine.discovery.dbc_builder import DbcBuilder
from src.engine.discovery.hypotheses import Hypothesis, IdReport


def test_motorola_start_bit_roundtrip_decode() -> None:
    """REVIEW 3: exported Motorola signal must decode its true bit layout.

    Ground truth: a Motorola 16-bit signal whose MSB is byte0 bit7 and LSB
    byte1 bit0. int.to_bytes(2, "big") must round-trip through cantools.
    Hypothesis anchor (byte0 bit0, LSB0 bit 0) maps to DBC start 7.
    """
    hyp = Hypothesis(
        htype="SIGNAL",
        start_bit=0,           # LSB0 anchor: byte0 bit0 (series start)
        length=16,
        is_little_endian=False,
        is_signed=False,
        name="BE16Test",
        confidence=0.85,
    )
    report = IdReport(
        arbitration_id=0x100,
        frame_count=10,
        rate_hz=10.0,
        dlc=8,
        hypotheses=[hyp],
    )
    db = DbcBuilder.build_database({0x100: report})
    assert len(db.messages) == 1
    sig = db.messages[0].signals[0]
    assert sig.byte_order == "big_endian"
    # MSB at byte0 bit7 = DBC start bit 7 (verified against cantools semantics)
    assert sig.start == 7

    frame = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x100,
        data=(0x1234).to_bytes(2, "big") + b"\x00" * 6,
        is_extended=False,
    )
    decoder = DbcSignalDecoder(db)
    decoded = decoder.decode_frame(frame)
    assert decoded is not None
    assert decoded.signals["BE16Test"].value == 0x1234


def test_motorola_start_bit_bit_offset_conversion() -> None:
    """Non-zero Motorola anchors: anchor at byte1 bit0 (LSB0 bit 8) -> DBC start 15."""
    hyp = Hypothesis(
        htype="SIGNAL",
        start_bit=8,
        length=16,
        is_little_endian=False,
        name="BE16AtByte1",
        confidence=0.85,
    )
    report = IdReport(arbitration_id=0x101, frame_count=10, rate_hz=10.0, dlc=8, hypotheses=[hyp])
    db = DbcBuilder.build_database({0x101: report})
    assert db.messages[0].signals[0].start == 15


def test_fd_dlc_code_not_used_as_byte_length() -> None:
    """REVIEW 3: IdReport.dlc must be treated as payload BYTE length.

    A 16-byte CAN-FD payload has DLC code 10. The builder must never pass
    the DLC code to cantools Message(length=) — it must use the observed
    byte length, or every FD message gets truncated to the code's numeric
    value and trailing signals are dropped as over-spanning.
    """
    hyp = Hypothesis(
        htype="SIGNAL",
        start_bit=112,  # bits 112..127 = bytes 14..15, inside the 16-byte payload
        length=16,
        is_little_endian=True,
        name="FD16",
        confidence=0.85,
    )
    report = IdReport(
        arbitration_id=0x200,
        frame_count=10,
        rate_hz=10.0,
        dlc=16,  # byte length, NOT the FD DLC code 10
        hypotheses=[hyp],
    )
    db = DbcBuilder.build_database({0x200: report})
    msg = db.messages[0]
    assert msg.length == 16, "Message length must be the byte length (16), not the FD DLC code (10)"
    assert any(s.name == "FD16" for s in msg.signals), "signal inside 16 bytes must survive"

    # Round-trip: decode a 16-byte FD frame
    payload = bytes(14) + (0x0BEE).to_bytes(2, "little")
    frame = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x200,
        data=payload,
        is_extended=False,
        is_fd=True,
    )
    decoded = DbcSignalDecoder(db).decode_frame(frame)
    assert decoded is not None
    assert decoded.signals["FD16"].value == 0x0BEE


def test_overspanning_signal_dropped_not_fatal() -> None:
    """A candidate spanning past the observed payload is dropped, export survives."""
    hyp = Hypothesis(
        htype="SIGNAL",
        start_bit=56,   # byte 7 — 16 bits would need byte 8
        length=16,
        is_little_endian=True,
        name="Overflow16",
        confidence=0.85,
    )
    ok_hyp = Hypothesis(
        htype="SIGNAL",
        start_bit=0,
        length=8,
        is_little_endian=True,
        name="OK8",
        confidence=0.75,
    )
    report = IdReport(
        arbitration_id=0x300,
        frame_count=10,
        rate_hz=10.0,
        dlc=8,
        hypotheses=[hyp, ok_hyp],
    )
    db = DbcBuilder.build_database({0x300: report})
    names = {s.name for s in db.messages[0].signals}
    assert "OK8" in names
    assert "Overflow16" not in names


def _make_dbc_with_two_sa_messages() -> Database:
    """Two OEM DBC messages sharing PGN 65265 (CCVS1, PF=254) with different SA.

    Vector DBC marks 29-bit ids with bit31 set (0x80000000 | raw id).
    """
    dbc_text = """VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 2566844672 CCVS1_EngineSA0: 8 ECU
 SG_ WheelSpeed : 0|16@1+ (0.1,0) [0|250] "km/h" Vector__XXX

BO_ 2566844914 CCVS1_RetarderSA242: 8 ECU
 SG_ WheelSpeed : 0|16@1+ (0.01,0) [0|250] "km/h" Vector__XXX
"""
    return cantools.database.load_string(dbc_text)


def test_j1939_sa_discriminates_same_pgn() -> None:
    """REVIEW 3: first-match PGN lookup decoded all CCVS1 frames with one SA's scaling.

    SA=0 ECU publishes 0x18FEF100 (engine CCVS1, 0.1 scale); SA=242 retarder
    publishes 0x18FEF1F2 (0.01 scale). The decoder must pick by SA.
    """
    db = _make_dbc_with_two_sa_messages()
    decoder = DbcSignalDecoder(db)

    frame_engine = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x18FEF100,  # PGN 65265, SA 0
        data=(1000).to_bytes(2, "little") + b"\x00" * 6,
        is_extended=True,
    )
    decoded_engine = decoder.decode_frame(frame_engine)
    assert decoded_engine is not None
    assert decoded_engine.message_name == "CCVS1_EngineSA0"
    assert decoded_engine.signals["WheelSpeed"].value == 100.0  # 1000 * 0.1

    frame_retarder = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x18FEF1F2,  # PGN 65265, SA 242
        data=(1000).to_bytes(2, "little") + b"\x00" * 6,
        is_extended=True,
    )
    decoded_retarder = decoder.decode_frame(frame_retarder)
    assert decoded_retarder is not None
    assert decoded_retarder.message_name == "CCVS1_RetarderSA242"
    assert decoded_retarder.signals["WheelSpeed"].value == 10.0  # 1000 * 0.01


def test_j1939_wildcard_sa_fallback() -> None:
    """A DBC message published with SA=255 (global) matches any SA after exact SA misses."""
    dbc_text = """VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 2566844927 CCVS1_Global: 8 ECU
 SG_ WheelSpeed : 0|16@1+ (0.1,0) [0|250] "km/h" Vector__XXX
"""
    decoder = DbcSignalDecoder.from_dbc_string(dbc_text)
    frame = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x18FEF105,  # PGN 65265, SA 5 (not 255)
        data=(1000).to_bytes(2, "little") + b"\x00" * 6,
        is_extended=True,
    )
    decoded = decoder.decode_frame(frame)
    assert decoded is not None
    assert decoded.message_name == "CCVS1_Global"
