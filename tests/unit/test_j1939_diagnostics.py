"""Unit tests for SAE J1939-73 Diagnostic Services (DM1, DM2, DM3, DM4, DM5, DM6, DM11, FMI 0-31)."""

from src.protocols.j1939.diagnostics import (
    J1939DiagnosticService,
    LampStatus,
)


def test_j1939_single_dtc_dm1_parsing() -> None:
    # DM1 Payload (8 bytes):
    # Byte 0: 0x40 (MIL=0b01 ON, RedStop=0b00 OFF, Amber=0b00 OFF, Protect=0b00 OFF)
    # Byte 1: 0xFF (Flash states not available)
    # Byte 2..5: SPN 100 (Engine Oil Pressure), FMI 1 (Low Most Severe), OC 3, CM 0
    # SPN 100 = 0x000064 -> Byte 0: 0x64, Byte 1: 0x00, Byte 2: ((SPN >> 11) << 5) | FMI = (0 << 5) | 1 = 0x01
    # Byte 3: (CM << 7) | OC = (0 << 7) | 3 = 0x03
    # Byte 6..7: 0xFF 0xFF
    dm1_raw = b"\x40\xff\x64\x00\x01\x03\xff\xff"

    msg = J1939DiagnosticService.parse_dm1_or_dm2(
        data=dm1_raw,
        pgn=65226,
        source_address=0,
        timestamp_ns=1000,
    )

    assert msg.source_address == 0
    assert msg.malfunction_indicator_lamp == LampStatus.ON
    assert msg.red_stop_lamp == LampStatus.OFF
    assert len(msg.dtcs) == 1

    dtc = msg.dtcs[0]
    assert dtc.spn == 100
    assert dtc.fmi == 1
    assert dtc.occurrence_count == 3
    assert dtc.is_critical is True
    assert "Below Normal" in dtc.fmi_description_en
    assert "Kritik" in dtc.fmi_description_tr


def test_j1939_multi_dtc_parsing() -> None:
    # 2 DTCs in payload (10 bytes)
    # Lamps: Amber Warning ON (Byte 0 = 0x04)
    # DTC 1: SPN 190 (Engine Speed), FMI 0 (High Most Severe), OC 5 -> 0xBE, 0x00, 0x00, 0x05
    # DTC 2: SPN 110 (Coolant Temp), FMI 3 (Voltage High), OC 1 -> 0x6E, 0x00, 0x03, 0x01
    dm_raw = b"\x04\xff\xbe\x00\x00\x05\x6e\x00\x03\x01"

    msg = J1939DiagnosticService.parse_dm1_or_dm2(data=dm_raw, pgn=65226, source_address=0)
    assert msg.amber_warning_lamp == LampStatus.ON
    assert len(msg.dtcs) == 2

    dtc1 = msg.dtcs[0]
    assert dtc1.spn == 190
    assert dtc1.fmi == 0
    assert dtc1.occurrence_count == 5

    dtc2 = msg.dtcs[1]
    assert dtc2.spn == 110
    assert dtc2.fmi == 3
    assert dtc2.occurrence_count == 1


def test_clear_diagnostic_requests() -> None:
    dm11_req = J1939DiagnosticService.create_dm11_clear_active_request()
    assert dm11_req == b"\xd3\xfe\x00"

    dm3_req = J1939DiagnosticService.create_dm3_clear_previously_active_request()
    assert dm3_req == b"\xcc\xfe\x00"


def test_clear_diagnostic_frames_are_valid_can_frames() -> None:
    """Positive: DM11/DM3 frame constructors build transmittable extended frames (P1 NameError regression)."""
    dm11 = J1939DiagnosticService.create_dm11_frame()
    assert dm11.arbitration_id == 0x18EA00F9
    assert dm11.data == b"\xd3\xfe\x00"
    assert dm11.is_extended is True
    assert dm11.direction == "tx"

    dm3 = J1939DiagnosticService.create_dm3_frame()
    assert dm3.arbitration_id == 0x18EA00F9
    assert dm3.data == b"\xcc\xfe\x00"


def test_dm4_dm5_dm6_request_frames() -> None:
    """Positive: DM4/DM5/DM6 request frames use the canonical 0x18EA layout with correct PGN payloads."""
    dm4 = J1939DiagnosticService.create_dm4_request_frame()
    assert dm4.arbitration_id == 0x18EA00F9
    assert dm4.data == b"\xcd\xfe\x00"  # PGN 65229 little-endian

    dm5 = J1939DiagnosticService.create_dm5_request_frame()
    assert dm5.data == b"\xce\xfe\x00"  # PGN 65230

    dm6 = J1939DiagnosticService.create_dm6_request_frame()
    assert dm6.data == b"\xcf\xfe\x00"  # PGN 65231


def test_create_request_frame_rejects_out_of_range_pgn() -> None:
    """Boundary/negative: PGN above 0x1FFFF must raise, never build a bogus frame."""
    import pytest

    with pytest.raises(ValueError):
        J1939DiagnosticService.create_request_frame(0x20000)


def test_parse_dm5_readiness() -> None:
    """Positive: DM5 readiness bytes decode to rank/support/completion fields."""
    readiness = J1939DiagnosticService.parse_dm5_readiness(
        data=b"\x00\x00\x0F\x0F",
        source_address=0,
        timestamp_ns=42,
    )
    assert readiness.active_rank == 0
    assert readiness.previously_active_rank == 0
    assert readiness.supported_systems == 0x0F
    assert readiness.completed_systems == 0x0F
    assert readiness.timestamp_ns == 42


def test_parse_dm5_readiness_rejects_short_payload() -> None:
    """Boundary/negative: DM5 payload shorter than 4 bytes raises ValueError."""
    import pytest

    with pytest.raises(ValueError):
        J1939DiagnosticService.parse_dm5_readiness(data=b"\x00\x00")


def test_fmi_table_is_complete_0_to_31() -> None:
    """Boundary: the FMI table covers every value 0..31 (MASTER_PLAN Task 2.3 DoD)."""
    from src.protocols.j1939.diagnostics import FMI_DESCRIPTIONS

    missing = [fmi for fmi in range(32) if fmi not in FMI_DESCRIPTIONS]
    assert missing == []
    for entry in FMI_DESCRIPTIONS.values():
        assert isinstance(entry, tuple) and len(entry) == 2
