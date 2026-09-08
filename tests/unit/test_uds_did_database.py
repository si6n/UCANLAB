"""Unit Test Suite for ISO 14229 UDS DID Database & Decoders."""

from __future__ import annotations

import pytest

from src.protocols.obd.models import UdsDidDefinition
from src.protocols.uds.did_database import (
    UDS_DID_REGISTRY,
    DiagnosticSessionEnum,
    DpfRegenStatusEnum,
    IgnitionStatusEnum,
    SecurityAccessStateEnum,
)


def test_standard_uds_vin_did_f190() -> None:
    """Test standard 17-character VIN (DID 0xF190) decoding."""
    reg = UDS_DID_REGISTRY
    vin_bytes = b"1M8GDM9A_KP042788"
    res = reg.decode(0xF190, vin_bytes)
    assert res.did == 0xF190
    assert res.name == "VEHICLE_IDENTIFICATION_NUMBER"
    assert res.value == "1M8GDM9A_KP042788"
    assert res.unit == "string"
    assert res.is_valid is True


def test_standard_uds_ecu_part_numbers_dids() -> None:
    """Test OEM Spare Part, Software, Hardware, and System Name DIDs."""
    reg = UDS_DID_REGISTRY

    # 0xF187: Spare Part Number
    res_187 = reg.decode(0xF187, b"21854930P02\x00\x00")
    assert res_187.value == "21854930P02"

    # 0xF188: Software Number
    res_188 = reg.decode(0xF188, b"SW22948201AB")
    assert res_188.value == "SW22948201AB"

    # 0xF191: Hardware Number
    res_191 = reg.decode(0xF191, b"HW21482019AA")
    assert res_191.value == "HW21482019AA"

    # 0xF197: System Name
    res_197 = reg.decode(0xF197, b"OM471LA_EURO6")
    assert res_197.value == "OM471LA_EURO6"


def test_standard_uds_bcd_dates_dids() -> None:
    """Test BCD Manufacturing and Programming date DIDs."""
    reg = UDS_DID_REGISTRY

    # 0xF18B: ECU Manufacturing Date BCD 2024-05-18
    res_mfg = reg.decode(0xF18B, bytes([0x20, 0x24, 0x05, 0x18]))
    assert res_mfg.value == "2024-05-18"

    # 0xF199: Programming Date BCD 2025-11-04
    res_prog = reg.decode(0xF199, bytes([0x20, 0x25, 0x11, 0x04]))
    assert res_prog.value == "2025-11-04"


def test_standard_uds_active_session_did_f186() -> None:
    """Test Active Diagnostic Session DID (0xF186)."""
    reg = UDS_DID_REGISTRY

    res_default = reg.decode(0xF186, bytes([0x01]))
    assert res_default.value == DiagnosticSessionEnum.DEFAULT_SESSION

    res_prog = reg.decode(0xF186, bytes([0x02]))
    assert res_prog.value == DiagnosticSessionEnum.PROGRAMMING_SESSION

    res_ext = reg.decode(0xF186, bytes([0x03]))
    assert res_ext.value == DiagnosticSessionEnum.EXTENDED_DIAGNOSTIC_SESSION


def test_standard_uds_cvn_and_counters_dids() -> None:
    """Test CVN, Flash Counter, and Security Access DIDs."""
    reg = UDS_DID_REGISTRY

    # 0xF1A0: CVN #1 (Hex string)
    res_cvn = reg.decode(0xF1A0, bytes([0x9A, 0x4F, 0x11, 0xBC]))
    assert res_cvn.value == "9A4F11BC"

    # 0xF1A2: Flash Counter (uint16)
    res_flash = reg.decode(0xF1A2, bytes([0x00, 0x05]))
    assert res_flash.value == 5

    # 0xF1A5: Security Lock State
    res_lock = reg.decode(0xF1A5, bytes([0x00]))
    assert res_lock.value == SecurityAccessStateEnum.LOCKED

    res_unlocked = reg.decode(0xF1A5, bytes([0x01]))
    assert res_unlocked.value == SecurityAccessStateEnum.UNLOCKED_LEVEL_1


def test_extended_battery_voltage_did_0100_and_f010() -> None:
    """Test Terminal 30 Battery Voltage (DIDs 0x0100 & 0xF010)."""
    reg = UDS_DID_REGISTRY

    # 12.50 V -> raw = 1250 (0x04 0xE2)
    res_100 = reg.decode(0x0100, bytes([0x04, 0xE2]))
    assert res_100.unit == "V"
    assert res_100.value == 12.50

    # 24.00 V (Commercial Vehicle 24V bus) -> raw = 2400 (0x09 0x60)
    res_f010 = reg.decode(0xF010, bytes([0x09, 0x60]))
    assert res_f010.value == 24.00


def test_extended_engine_speed_did_0103() -> None:
    """Test high-resolution Engine Crankshaft Speed (DID 0x0103): ((A*256)+B)/4 rpm."""
    reg = UDS_DID_REGISTRY
    # 2000.0 rpm -> raw = 8000 (0x1F 0x40)
    res = reg.decode(0x0103, bytes([0x1F, 0x40]))
    assert res.unit == "rpm"
    assert res.value == 2000.0


def test_extended_steering_wheel_angle_did_0106() -> None:
    """Test signed 16-bit Steering Wheel Angle (DID 0x0106)."""
    reg = UDS_DID_REGISTRY

    # +15.5 deg -> raw = 155 (0x00 0x9B)
    res_pos = reg.decode(0x0106, bytes([0x00, 0x9B]))
    assert res_pos.value == 15.5

    # -25.0 deg -> raw = -250 (0xFF 0x06)
    res_neg = reg.decode(0x0106, bytes([0xFF, 0x06]))
    assert res_neg.value == -25.0


def test_extended_aftertreatment_dids_0200_to_0212() -> None:
    """Test DPF and AdBlue / DEF telemetry DIDs."""
    reg = UDS_DID_REGISTRY

    # 0x0200: DPF Soot Mass (0.1 g/bit) -> 45.0 g = 450 (0x01 0xC2)
    res_soot = reg.decode(0x0200, bytes([0x01, 0xC2]))
    assert res_soot.unit == "g"
    assert res_soot.value == 45.0

    # 0x0201: DPF Regen Status
    res_regen = reg.decode(0x0201, bytes([0x03]))
    assert res_regen.value == DpfRegenStatusEnum.ACTIVE_HIGH

    # 0x0210: AdBlue Dosing Rate (0.01 g/s) -> 1.75 g/s = 175 (0x00 0xAF)
    res_dose = reg.decode(0x0210, bytes([0x00, 0xAF]))
    assert res_dose.unit == "g/s"
    assert res_dose.value == 1.75

    # 0x0211: AdBlue Tank Level (%) -> 80% = 204 (0xCC)
    res_tank = reg.decode(0x0211, bytes([0xCC]))
    assert res_tank.value == 80.0

    # 0x0212: Urea Concentration (%) -> 32.50% (ISO 22241 standard) = 3250 (0x0C 0xB2)
    res_conc = reg.decode(0x0212, bytes([0x0C, 0xB2]))
    assert res_conc.unit == "%"
    assert res_conc.value == 32.50


def test_extended_cylinder_balancing_offsets_0300_to_0305() -> None:
    """Test Cylinder 1..6 Injector Balancing Trim Offsets (signed 0.01 mg/stroke)."""
    reg = UDS_DID_REGISTRY

    # Cylinder 1: +1.25 mg/stroke = +125 (0x00 0x7D)
    res_cyl1 = reg.decode(0x0300, bytes([0x00, 0x7D]))
    assert res_cyl1.name == "INJECTOR_1_BALANCING_TRIM_OFFSET"
    assert res_cyl1.unit == "mg/stroke"
    assert res_cyl1.value == 1.25

    # Cylinder 6: -1.50 mg/stroke = -150 (0xFF 0x6A)
    res_cyl6 = reg.decode(0x0305, bytes([0xFF, 0x6A]))
    assert res_cyl6.name == "INJECTOR_6_BALANCING_TRIM_OFFSET"
    assert res_cyl6.value == -1.50


def test_uds_did_unknown_fallback() -> None:
    """Test dynamic fallback decoding for unlisted proprietary DIDs."""
    reg = UDS_DID_REGISTRY
    res = reg.decode(0xABCD, bytes([0x11, 0x22, 0x33, 0x44]))
    assert res.did == 0xABCD
    assert res.name == "UNKNOWN_DID_0xABCD"
    assert res.value == "11223344"
    assert res.is_valid is True


def test_uds_did_insufficient_length_error() -> None:
    """Test error handling when response payload has fewer bytes than required."""
    reg = UDS_DID_REGISTRY
    # 0xF190 (VIN) requires 17 bytes, provide 5
    res = reg.decode(0xF190, b"12345")
    assert res.is_valid is False
    assert res.value is None
    assert "requires at least 17 bytes" in (res.error_message or "")


def test_standard_uds_software_and_fingerprints_dids() -> None:
    """Test Boot/App SW and Data ID and Fingerprint DIDs (0xF180 - 0xF185)."""
    reg = UDS_DID_REGISTRY

    # 0xF180: Boot Software ID
    res_boot = reg.decode(0xF180, b"BOOT_v04.12.00")
    assert res_boot.value == "BOOT_v04.12.00"

    # 0xF181: Application Software ID
    res_app = reg.decode(0xF181, b"SW_APPL_482910")
    assert res_app.value == "SW_APPL_482910"

    # 0xF182: Application Data ID
    res_data = reg.decode(0xF182, b"CAL_DATA_ENG_01")
    assert res_data.value == "CAL_DATA_ENG_01"

    # 0xF184: App SW Fingerprint (BCD Date 2024-05-18 + Tester ID 0x12 0x34)
    res_fp = reg.decode(0xF184, bytes([0x20, 0x24, 0x05, 0x18, 0x12, 0x34]))
    assert res_fp.value["date"] == "2024-05-18"
    assert res_fp.value["tester_id"] == "1234"


def test_standard_uds_supplier_and_approval_dids() -> None:
    """Test Supplier HW/SW and Exhaust Type Approval DIDs (0xF192 - 0xF198)."""
    reg = UDS_DID_REGISTRY

    # 0xF18A: System Supplier Identifier
    res_supp = reg.decode(0xF18A, b"BOSCH_EDC17")
    assert res_supp.value == "BOSCH_EDC17"

    # 0xF192: Supplier HW Number
    res_hw = reg.decode(0xF192, b"0281020349")
    assert res_hw.value == "0281020349"

    # 0xF194: Supplier SW Number
    res_sw = reg.decode(0xF194, b"1037528190")
    assert res_sw.value == "1037528190"

    # 0xF196: Exhaust Approval
    res_appr = reg.decode(0xF196, b"e1*2007/46*0412*02")
    assert res_appr.value == "e1*2007/46*0412*02"

    # 0xF198: Repair Shop Code / Tester Serial Number
    res_tester = reg.decode(0xF198, b"DEALER_TRUCK_042")
    assert res_tester.value == "DEALER_TRUCK_042"


def test_extended_telemetry_dids_0101_0104_0105_0107_0202() -> None:
    """Test Ignition, Pedal, Brake, Speed, and DPF Differential Pressure DIDs."""
    reg = UDS_DID_REGISTRY

    # 0x0101: Ignition Status (Key On = 2)
    res_ign = reg.decode(0x0101, bytes([0x02]))
    assert res_ign.value == IgnitionStatusEnum.IGNITION_ON

    # 0x0104: Accelerator Pedal Position (((A*256)+B)/100 %) -> 65.50% = 6550 (0x19 0x96)
    res_pedal = reg.decode(0x0104, bytes([0x19, 0x96]))
    assert res_pedal.value == 65.50

    # 0x0105: Brake Pressure (((A*256)+B)/10 bar) -> 85.0 bar = 850 (0x03 0x52)
    res_brake = reg.decode(0x0105, bytes([0x03, 0x52]))
    assert res_brake.value == 85.0

    # 0x0107: Vehicle Speed (((A*256)+B)/100 km/h) -> 89.50 km/h = 8950 (0x22 0xF6)
    res_spd = reg.decode(0x0107, bytes([0x22, 0xF6]))
    assert res_spd.value == 89.50

    # 0x0202: DPF Differential Pressure (((A*256)+B)/100 kPa) -> 12.34 kPa = 1234 (0x04 0xD2)
    res_dpf_dp = reg.decode(0x0202, bytes([0x04, 0xD2]))
    assert res_dpf_dp.value == 12.34


def test_uds_did_custom_registration_and_range_check() -> None:
    """Test custom DID registration and boundary limit checking."""
    reg = UDS_DID_REGISTRY

    custom_did = UdsDidDefinition(
        did=0x9999,
        name="CUSTOM_PRESSURE_DID",
        description="Custom Test Pressure",
        length=2,
        unit="psi",
        min_value=0.0,
        max_value=150.0,
        scaling=0.1,
        offset=0.0,
    )
    reg.register(custom_did)

    # Valid: 500 raw -> 50.0 psi
    res_valid = reg.decode(0x9999, bytes([0x01, 0xF4]))
    assert res_valid.is_valid is True
    assert res_valid.value == 50.0

    # Above max: 2000 raw -> 200.0 psi (max is 150.0)
    res_invalid = reg.decode(0x9999, bytes([0x07, 0xD0]))
    assert res_invalid.is_valid is False
    assert "above maximum 150.0" in (res_invalid.error_message or "")


def test_parse_response_rejects_bare_sid_below_0x40() -> None:
    """Y-08 regression: a response echoing a bare SID (< 0x40) is malformed
    per ISO 14229 and must fail closed, never be labelled positive."""
    from src.protocols.uds.services import UdsServiceBuilder

    with pytest.raises(ValueError, match="SID"):
        UdsServiceBuilder.parse_response(b"\x10\x00")  # bare echo, no +0x40

    with pytest.raises(ValueError, match="SID"):
        UdsServiceBuilder.parse_response(b"\x00")


def test_parse_response_accepts_canonical_positive_echo() -> None:
    """Y-08: the canonical SID+0x40 echo still parses as positive."""
    from src.protocols.uds.services import UdsServiceBuilder

    resp = UdsServiceBuilder.parse_response(b"\x50\x03\x00\x32")
    assert resp.is_positive is True
    assert resp.service_id == 0x10


# ============================================================================
# L-7b (micro): numeric DID decode requires well-defined integer widths
# ============================================================================


def test_numeric_did_with_unsupported_width_fails_closed() -> None:
    """L-7b: a length-less numeric DID with a 3-byte payload must be
    rejected — the old generic `int.from_bytes` fabricated meaningless
    values (e.g. 66011) instead of failing."""
    from src.protocols.obd.models import UdsDidDefinition

    did_def = UdsDidDefinition(
        did=0xF999,
        name="OddWidthDid",
        description="Test DID with no declared length",
        length=None,
        unit="-",
        data_format="numeric",
    )
    # 3-byte payload: not 1/2/4 — unsupported for inferred numeric decode.
    with pytest.raises(ValueError, match="1/2/4-byte payload"):
        did_def.decode(bytes([0x01, 0x02, 0x03]))


def test_numeric_did_with_declared_exotic_width_still_decodes() -> None:
    """L-7b: explicit-length numeric DIDs keep the generic big-endian decode —
    the width was declared by the DID author, not inferred from the wire."""
    from src.protocols.obd.models import UdsDidDefinition

    did_def = UdsDidDefinition(
        did=0xF998,
        name="Declared3ByteDid",
        description="Test DID with an explicit 3-byte length",
        length=3,
        unit="-",
        data_format="numeric",
    )
    assert did_def.decode(bytes([0x01, 0x02, 0x03])) == float(0x010203)
