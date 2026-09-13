"""Unit Test Suite for SAE J1979 Mode 01 PID Knowledge Base & Conversion Engine."""

from __future__ import annotations

import pytest

from src.protocols.obd.models import ObdPidDefinition
from src.protocols.obd.pids import (
    BITMASK_PIDS,
    OBD_PID_REGISTRY,
    decode_support_bitmask,
    is_pid_supported_by_bitmask,
)


def test_bitmask_anchor_pids_constants() -> None:
    """Test standard 32-PID bitmask anchor definitions."""
    assert 0x00 in BITMASK_PIDS
    assert 0x20 in BITMASK_PIDS
    assert 0x40 in BITMASK_PIDS
    assert 0x60 in BITMASK_PIDS
    assert 0x80 in BITMASK_PIDS
    assert 0xA0 in BITMASK_PIDS
    assert 0xC0 in BITMASK_PIDS
    assert 0xE0 in BITMASK_PIDS


def test_decode_support_bitmask_pids_01_to_20() -> None:
    """Test bitmask decoding algorithm for PID 0x00 [0x01 - 0x20]."""
    # Bitmask 0xBE 0x3F 0xB8 0x13
    # Binary:
    # 1011 1110 0011 1111 1011 1000 0001 0011
    # Bits set (1-indexed from MSB):
    # 1 (0x01), 3 (0x03), 4 (0x04), 5 (0x05), 6 (0x06), 7 (0x07)
    # 11 (0x0B), 12 (0x0C), 13 (0x0D), 14 (0x0E), 15 (0x0F), 16 (0x10)
    # 17 (0x11), 19 (0x13), 20 (0x14), 21 (0x15)
    # 28 (0x1C), 31 (0x1F), 32 (0x20 - Next anchor)
    raw = bytes([0xBE, 0x3F, 0xB8, 0x13])
    supported = decode_support_bitmask(raw, base_pid=0x00)

    assert 0x01 in supported
    assert 0x02 not in supported
    assert 0x03 in supported
    assert 0x04 in supported
    assert 0x05 in supported
    assert 0x06 in supported
    assert 0x07 in supported
    assert 0x0C in supported
    assert 0x0D in supported
    assert 0x0E in supported
    assert 0x0F in supported
    assert 0x10 in supported
    assert 0x11 in supported
    assert 0x14 in supported
    assert 0x1C in supported
    assert 0x1F in supported
    assert 0x20 in supported  # Bit 0 set means PID 0x20 is available


def test_is_pid_supported_by_bitmask() -> None:
    """Test helper for single PID support verification."""
    raw = bytes([0x80, 0x00, 0x00, 0x01])  # Only PID 0x01 and PID 0x20 supported
    assert is_pid_supported_by_bitmask(raw, base_pid=0x00, target_pid=0x01) is True
    assert is_pid_supported_by_bitmask(raw, base_pid=0x00, target_pid=0x05) is False
    assert is_pid_supported_by_bitmask(raw, base_pid=0x00, target_pid=0x20) is True
    assert is_pid_supported_by_bitmask(raw, base_pid=0x00, target_pid=0x21) is False


def test_decode_support_bitmask_invalid_length() -> None:
    """Test ValueError raised on bitmask payload < 4 bytes."""
    with pytest.raises(ValueError, match="requires 4 bytes"):
        decode_support_bitmask(bytes([0x00, 0x01]), base_pid=0x00)


def test_obd_engine_rpm_pid_0c() -> None:
    """Test Engine RPM (PID 0x0C) conversion: ((A * 256) + B) / 4."""
    reg = OBD_PID_REGISTRY
    # 8000 raw -> 2000.0 rpm (0x1F 0x40 = 8000)
    res = reg.decode(0x0C, bytes([0x1F, 0x40]))
    assert res.pid == 0x0C
    assert res.name == "ENGINE_RPM"
    assert res.unit == "rpm"
    assert res.value == 2000.0
    assert res.is_valid is True

    # 0 rpm
    res_zero = reg.decode(0x0C, bytes([0x00, 0x00]))
    assert res_zero.value == 0.0

    # Max rpm: 0xFF 0xFC (65532) -> 16383.0 rpm
    res_max = reg.decode(0x0C, bytes([0xFF, 0xFC]))
    assert res_max.value == 16383.0


def test_obd_vehicle_speed_pid_0d() -> None:
    """Test Vehicle Speed (PID 0x0D) conversion: A km/h."""
    reg = OBD_PID_REGISTRY
    res = reg.decode(0x0D, bytes([120]))
    assert res.name == "VEHICLE_SPEED"
    assert res.unit == "km/h"
    assert res.value == 120
    assert res.is_valid is True


def test_obd_coolant_temperature_pid_05() -> None:
    """Test Engine Coolant Temp (PID 0x05) conversion: A - 40 °C."""
    reg = OBD_PID_REGISTRY
    # 90 °C -> raw = 130 (0x82)
    res = reg.decode(0x05, bytes([130]))
    assert res.unit == "°C"
    assert res.value == 90
    assert res.is_valid is True

    # Min: 0 -> -40 °C
    res_min = reg.decode(0x05, bytes([0]))
    assert res_min.value == -40

    # Max: 255 -> 215 °C
    res_max = reg.decode(0x05, bytes([255]))
    assert res_max.value == 215


def test_obd_engine_load_pid_04() -> None:
    """Test Calculated Engine Load (PID 0x04) conversion: A * 100 / 255 %."""
    reg = OBD_PID_REGISTRY
    res_half = reg.decode(0x04, bytes([128]))
    assert res_half.unit == "%"
    assert pytest.approx(res_half.value, 0.01) == 50.2

    res_full = reg.decode(0x04, bytes([255]))
    assert res_full.value == 100.0


def test_obd_fuel_trims_pids_06_to_09() -> None:
    """Test Fuel Trim conversions: (A - 128) * 100 / 128 %."""
    reg = OBD_PID_REGISTRY
    # 0.0% trim -> raw 128 (0x80)
    res_zero = reg.decode(0x06, bytes([128]))
    assert res_zero.value == 0.0

    # -100.0% trim -> raw 0
    res_min = reg.decode(0x07, bytes([0]))
    assert res_min.value == -100.0

    # +99.22% trim -> raw 255
    res_max = reg.decode(0x08, bytes([255]))
    assert pytest.approx(res_max.value, 0.01) == 99.22


def test_obd_maf_air_flow_pid_10() -> None:
    """Test MAF (PID 0x10) conversion: ((A * 256) + B) / 100 g/s."""
    reg = OBD_PID_REGISTRY
    # 25.50 g/s -> raw = 2550 (0x09 0xF6)
    res = reg.decode(0x10, bytes([0x09, 0xF6]))
    assert res.name == "MAF_AIR_FLOW_RATE"
    assert res.unit == "g/s"
    assert res.value == 25.50


def test_obd_fuel_pressures_pids_0a_22_23_59() -> None:
    """Test Low and High pressure fuel rail PIDs."""
    reg = OBD_PID_REGISTRY

    # PID 0x0A: Fuel Pressure Gauge (A * 3 kPa)
    res_0a = reg.decode(0x0A, bytes([100]))
    assert res_0a.value == 300

    # PID 0x22: Fuel Rail Pressure Manifold Relative (((A*256)+B)*0.079 kPa)
    res_22 = reg.decode(0x22, bytes([0x03, 0xE8]))  # 1000
    assert pytest.approx(res_22.value, 0.01) == 79.0

    # PID 0x23: Fuel Rail Gauge Pressure Direct Injection (((A*256)+B)*10 kPa)
    res_23 = reg.decode(0x23, bytes([0x03, 0xE8]))  # 1000 -> 10,000 kPa
    assert res_23.value == 10000

    # PID 0x59: Fuel Rail Absolute Pressure (((A*256)+B)*10 kPa)
    res_59 = reg.decode(0x59, bytes([0x07, 0xD0]))  # 2000 -> 20,000 kPa
    assert res_59.value == 20000


def test_obd_timing_advance_pid_0e() -> None:
    """Test Timing Advance (PID 0x0E): (A / 2) - 64 ° BTDC."""
    reg = OBD_PID_REGISTRY
    # 0.0 ° BTDC -> raw = 128 (0x80)
    res_zero = reg.decode(0x0E, bytes([128]))
    assert res_zero.value == 0.0

    # -64.0 ° BTDC -> raw = 0
    res_min = reg.decode(0x0E, bytes([0]))
    assert res_min.value == -64.0

    # +63.5 ° BTDC -> raw = 255
    res_max = reg.decode(0x0E, bytes([255]))
    assert res_max.value == 63.5


def test_obd_oxygen_sensors_and_wideband() -> None:
    """Test Narrowband and Wideband O2 sensor decoders."""
    reg = OBD_PID_REGISTRY

    # Narrowband O2S1 (PID 0x14): V = A / 200, Trim = (B - 128) * 100 / 128
    res_14 = reg.decode(0x14, bytes([200, 128]))
    assert res_14.value == {"voltage_v": 1.0, "short_term_trim_percent": 0.0}

    # Wideband O2S1 Voltage (PID 0x24): Lambda = ((A*256)+B)/32768, V = ((C*256)+D)/8192
    # Lambda = 1.0 (32768 = 0x8000), Voltage = 2.5V (20480 = 0x5000)
    res_24 = reg.decode(0x24, bytes([0x80, 0x00, 0x50, 0x00]))
    assert res_24.value == {"equivalence_ratio": 1.0, "voltage_v": 2.5}

    # Wideband O2S1 Current (PID 0x34): Lambda = ((A*256)+B)/32768, Current = ((C*256)+D)/256 - 128 mA
    # Lambda = 1.0 (0x8000), Current = 0.0 mA (32768 = 0x8000)
    res_34 = reg.decode(0x34, bytes([0x80, 0x00, 0x80, 0x00]))
    assert res_34.value == {"equivalence_ratio": 1.0, "current_ma": 0.0}


def test_obd_catalyst_temperatures_pids_3c_to_3f() -> None:
    """Test Catalyst Temp conversions: (((A * 256) + B) / 10) - 40 °C."""
    reg = OBD_PID_REGISTRY
    # 600.0 °C -> raw = (600 + 40) * 10 = 6400 (0x19 0x00)
    res = reg.decode(0x3C, bytes([0x19, 0x00]))
    assert res.value == 600.0
    assert res.unit == "°C"


def test_obd_def_sensor_data_pid_9a() -> None:
    """Test AdBlue / DEF tank level & concentration (PID 0x9A)."""
    reg = OBD_PID_REGISTRY
    # Tank Level = 204 (80%), Urea Conc = 200 (10.0%)
    res = reg.decode(0x9A, bytes([204, 200, 0x00, 0x00]))
    assert res.name == "DEF_SENSOR_DATA"
    assert res.value["tank_level_percent"] == 80.0
    assert res.value["urea_concentration_percent"] == 10.0


def test_obd_odometer_pid_a6() -> None:
    """Test total vehicle odometer (PID 0xA6) 32-bit conversion."""
    reg = OBD_PID_REGISTRY
    # 1,234,567 raw -> 123,456.7 km
    raw_int = 1234567
    raw_bytes = raw_int.to_bytes(4, byteorder="big")
    res = reg.decode(0xA6, raw_bytes)
    assert res.name == "ODOMETER"
    assert res.unit == "km"
    assert res.value == 123456.7


def test_obd_monitor_status_pid_01() -> None:
    """Test PID 0x01 Monitor Status flags."""
    reg = OBD_PID_REGISTRY
    # Byte A: 0x83 (MIL On, 3 DTCs)
    # Byte B: 0x07 (Misfire, Fuel, Components all available)
    raw = bytes([0x83, 0x07, 0x00, 0x00])
    res = reg.decode(0x01, raw)
    assert res.value["mil_on"] is True
    assert res.value["dtc_count"] == 3
    assert res.value["misfire_available"] is True
    assert res.value["fuel_system_available"] is True


def test_obd_freeze_dtc_pid_02() -> None:
    """Test Freeze Frame DTC decoding (PID 0x02)."""
    reg = OBD_PID_REGISTRY
    # P0123: Powertrain (00), 01 23
    res_p0123 = reg.decode(0x02, bytes([0x01, 0x23]))
    assert res_p0123.value == "P0123"

    # None
    res_none = reg.decode(0x02, bytes([0x00, 0x00]))
    assert res_none.value == "None"


def test_obd_standards_and_fuel_type_pids() -> None:
    """Test PID 0x1C standards conformance and PID 0x51 fuel type enums."""
    reg = OBD_PID_REGISTRY

    res_std = reg.decode(0x1C, bytes([0x01]))
    assert "CARB" in res_std.value

    res_fuel = reg.decode(0x51, bytes([0x04]))
    assert res_fuel.value == "Diesel"


def test_obd_gear_ratio_pid_a4() -> None:
    """Test Transmission Actual Gear and Ratio (PID 0xA4)."""
    reg = OBD_PID_REGISTRY
    # Gear 3 (125 + 3 = 128), Ratio 1.250 (1250 = 0x04 0xE2)
    res = reg.decode(0xA4, bytes([128, 0x04, 0xE2, 0x00]))
    assert res.value["gear"] == 3
    assert res.value["gear_ratio"] == 1.25


def test_obd_unknown_pid_fallback() -> None:
    """Test dynamic decoding fallback for unlisted PIDs."""
    reg = OBD_PID_REGISTRY
    res = reg.decode(0xFE, bytes([0x12, 0x34]))
    assert res.pid == 0xFE
    assert res.name == "UNKNOWN_PID_0xFE"
    assert res.value == "1234"
    # REVIEW hardening: unlisted identifiers are fail-closed invalid.
    assert res.is_valid is False


def test_obd_insufficient_length_error() -> None:
    """Test graceful handling when raw payload is shorter than required bytes."""
    reg = OBD_PID_REGISTRY
    # PID 0x0C (Engine RPM) requires 2 bytes, provide 1
    res = reg.decode(0x0C, bytes([0x1F]))
    assert res.is_valid is False
    assert res.value is None
    assert "requires at least 2 bytes" in (res.error_message or "")


def test_obd_all_bitmask_anchors() -> None:
    """Test decoding support across all 8 bitmask blocks (0x00 to 0xE0)."""
    reg = OBD_PID_REGISTRY
    # Test each anchor block
    anchors = [0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xE0]
    for anchor in anchors:
        raw = bytes([0x80, 0x00, 0x00, 0x01])  # First PID in block and 32nd PID (next anchor)
        res = reg.decode(anchor, raw)
        assert res.is_valid is True
        assert isinstance(res.value, list)
        assert (anchor + 1) in res.value
        assert (anchor + 32) in res.value
        assert (anchor + 2) not in res.value


def test_obd_voltage_and_load_pids_42_to_44() -> None:
    """Test Control Module Voltage (0x42), Absolute Load (0x43), and Commanded Lambda (0x44)."""
    reg = OBD_PID_REGISTRY

    # 0x42: Voltage ((A*256)+B)/1000 V -> 12.600 V = 12600 (0x31 0x38)
    res_v = reg.decode(0x42, bytes([0x31, 0x38]))
    assert res_v.unit == "V"
    assert res_v.value == 12.600

    # 0x43: Absolute Load (((A*256)+B)*100)/255 % -> 255 raw = 100.0%
    res_load = reg.decode(0x43, bytes([0x00, 0xFF]))
    assert res_load.value == 100.0

    # 0x44: Commanded Fuel-Air Equivalence Ratio ((A*256)+B)/32768 -> 1.0000 (0x80 0x00)
    res_lambda = reg.decode(0x44, bytes([0x80, 0x00]))
    assert res_lambda.value == 1.0


def test_obd_temperatures_and_timing_pids_46_5c_5d() -> None:
    """Test Ambient Temp (0x46), Oil Temp (0x5C), and Injection Timing (0x5D)."""
    reg = OBD_PID_REGISTRY

    # 0x46: Ambient Air Temp (A - 40 °C) -> 25 °C = 65 (0x41)
    res_amb = reg.decode(0x46, bytes([65]))
    assert res_amb.value == 25

    # 0x5C: Engine Oil Temp (A - 40 °C) -> 95 °C = 135 (0x87)
    res_oil = reg.decode(0x5C, bytes([135]))
    assert res_oil.value == 95

    # 0x5D: Fuel Injection Timing (((A*256)+B) - 26880)/128 ° -> 0.0° = 26880 (0x69 0x00)
    res_timing = reg.decode(0x5D, bytes([0x69, 0x00]))
    assert res_timing.value == 0.0

    # +10.0° BTDC -> 26880 + (10 * 128) = 28160 (0x6E 0x00)
    res_timing_pos = reg.decode(0x5D, bytes([0x6E, 0x00]))
    assert res_timing_pos.value == 10.0


def test_obd_torque_pids_61_62_8e() -> None:
    """Test Torque PIDs: Driver Demand (0x61), Actual (0x62), Friction (0x8E)."""
    reg = OBD_PID_REGISTRY

    # 0x61: Demand Torque (A - 125 %) -> 50% = 175
    res_dem = reg.decode(0x61, bytes([175]))
    assert res_dem.value == 50

    # 0x62: Actual Torque (A - 125 %) -> 0% = 125
    res_act = reg.decode(0x62, bytes([125]))
    assert res_act.value == 0

    # 0x8E: Friction Torque (A - 125 %) -> -10% = 115
    res_fric = reg.decode(0x8E, bytes([115]))
    assert res_fric.value == -10


def test_obd_exhaust_and_dpf_pids_7a_87_9d() -> None:
    """Test DPF Differential Pressure (0x7A), Soot Mass (0x87), Exhaust Flow (0x9D)."""
    reg = OBD_PID_REGISTRY

    # 0x7A: DPF Delta P (((A*256)+B)/100) - 327.68 kPa -> 0.0 kPa = 32768 (0x80 0x00)
    res_dp = reg.decode(0x7A, bytes([0x00, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00]))
    assert res_dp.value == 0.0

    # 0x87: DPF Soot Mass ((A*256)+B)*0.1 g -> 25.0 g = 250 (0x00 0xFA)
    res_soot = reg.decode(0x87, bytes([0x00, 0x00, 0xFA, 0x00, 0x00]))
    assert res_soot.value == 25.0

    # 0x9D: Exhaust Flow ((A*256)+B)/5 kg/h -> 200.0 kg/h = 1000 (0x03 0xE8)
    res_flow = reg.decode(0x9D, bytes([0x03, 0xE8, 0x00, 0x00]))
    assert res_flow.value == 200.0


def test_obd_pid_range_limits_validation() -> None:
    """Test min_value and max_value boundary checks."""
    reg = OBD_PID_REGISTRY

    # Throttle Position (0x11): valid range 0.0 to 100.0
    res_valid = reg.decode(0x11, bytes([128]))
    assert res_valid.is_valid is True
    assert res_valid.error_message is None

    # Custom definition with strict limits
    custom_pid = ObdPidDefinition(
        pid=0xF0,
        name="CUSTOM_TEST_PID",
        description="Custom Test PID",
        bytes_length=1,
        unit="bar",
        min_value=10.0,
        max_value=50.0,
        decoder=lambda b: float(b[0]),
    )
    reg.register(custom_pid)

    # Within range
    res_in = reg.decode(0xF0, bytes([25]))
    assert res_in.is_valid is True
    assert res_in.value == 25.0

    # Below min
    res_low = reg.decode(0xF0, bytes([5]))
    assert res_low.is_valid is False
    assert "below minimum 10.0" in (res_low.error_message or "")

    # Above max
    res_high = reg.decode(0xF0, bytes([60]))
    assert res_high.is_valid is False
    assert "above maximum 50.0" in (res_high.error_message or "")
