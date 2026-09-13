"""SAE J1979 / ISO 15031-5 OBD-II Mode 01 PID Knowledge Base & Physical Conversion Engine.

Provides complete registry of SAE J1979 Mode 01 PIDs (0x00 to 0xFF), bitmask decoders,
physical scaling formulas, unit conversions, and validation ranges.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from typing import Any

from src.protocols.obd.models import ObdPidDefinition, ObdPidResult

# Bitmask Anchor PIDs (32 PIDs per block)
BITMASK_PIDS: frozenset[int] = frozenset({0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xE0})

# OBD Standards Conformance Map (PID 0x1C)
OBD_STANDARDS_MAP: dict[int, str] = {
    0x01: "OBD-II (CARB)",
    0x02: "OBD (EPA)",
    0x03: "OBD and OBD-II",
    0x04: "OBD-I",
    0x05: "Not OBD compliant",
    0x06: "EOBD (Europe)",
    0x07: "EOBD and OBD-II",
    0x08: "EOBD and OBD",
    0x09: "OBD-I and EOBD",
    0x0A: "JOBD (Japan)",
    0x0B: "JOBD and OBD-II",
    0x0C: "JOBD and EOBD",
    0x0D: "JOBD, EOBD, and OBD-II",
    0x11: "EMD (Engine Manufacturer Diagnostics)",
    0x12: "HD-OBD-C (Heavy Duty OBD - California)",
    0x13: "HD-OBD (Heavy Duty OBD)",
    0x14: "WWH-OBD (World-Wide Harmonized OBD)",
    0x17: "HD EOBD-I",
    0x18: "HD EOBD-I With NOx Control",
    0x19: "HD EOBD-II",
    0x1A: "HD EOBD-II With NOx Control",
    0x1C: "Brazil OBD Phase 1",
    0x1D: "Brazil OBD Phase 2",
    0x1E: "Korean OBD",
    0x1F: "India OBD I",
    0x20: "India OBD II",
}

# Fuel Type Map (PID 0x51)
FUEL_TYPES_MAP: dict[int, str] = {
    0x00: "Not available",
    0x01: "Gasoline",
    0x02: "Methanol",
    0x03: "Ethanol",
    0x04: "Diesel",
    0x05: "LPG",
    0x06: "CNG",
    0x07: "Propane",
    0x08: "Electric",
    0x09: "Bifuel (Gasoline)",
    0x0A: "Bifuel (Methanol)",
    0x0B: "Bifuel (Ethanol)",
    0x0C: "Bifuel (LPG)",
    0x0D: "Bifuel (CNG)",
    0x0E: "Bifuel (Propane)",
    0x0F: "Bifuel (Battery)",
    0x10: "Bifuel (Electric)",
    0x11: "Hybrid (Gasoline)",
    0x12: "Hybrid (Ethanol)",
    0x13: "Hybrid (Diesel)",
    0x14: "Hybrid (Electric)",
    0x15: "Hybrid (Mixed)",
    0x16: "Hybrid (Regenerative)",
}


def decode_support_bitmask(data: bytes, base_pid: int) -> list[int]:
    """Decode a 4-byte PID support bitmask into a list of supported integer PIDs.

    In SAE J1979:
    Bit 31 (MSB of byte A) corresponds to base_pid + 1.
    Bit 0 (LSB of byte D) corresponds to base_pid + 32 (next bitmask anchor).
    """
    if len(data) < 4:
        raise ValueError(f"Support bitmask requires 4 bytes, got {len(data)}")

    bitmask = (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]
    supported = []
    for offset in range(1, 33):
        pid = base_pid + offset
        bit_pos = 32 - offset
        if (bitmask >> bit_pos) & 1:
            supported.append(pid)
    return supported


def is_pid_supported_by_bitmask(bitmask_bytes: bytes, base_pid: int, target_pid: int) -> bool:
    """Check if target_pid is supported given the 4-byte bitmask starting at base_pid."""
    if not (base_pid < target_pid <= base_pid + 32):
        return False
    supported = decode_support_bitmask(bitmask_bytes, base_pid)
    return target_pid in supported


# Custom Decoders for Complex PIDs


def _decode_bitmask(base_pid: int) -> Callable[[bytes], list[int]]:
    return lambda b: decode_support_bitmask(b, base_pid)


def _decode_pid_01(data: bytes) -> dict[str, Any]:
    """PID 0x01: Monitor status since DTCs cleared."""
    a, b, c, d = data[0], data[1], data[2], data[3]
    mil_on = bool(a & 0x80)
    dtc_count = a & 0x7F
    is_diesel = bool(b & 0x08)

    monitors = {
        "mil_on": mil_on,
        "dtc_count": dtc_count,
        "is_diesel": is_diesel,
        "misfire_available": bool(b & 0x01),
        "misfire_complete": not bool(b & 0x10),
        "fuel_system_available": bool(b & 0x02),
        "fuel_system_complete": not bool(b & 0x20),
        "components_available": bool(b & 0x04),
        "components_complete": not bool(b & 0x40),
        "catalyst_available": bool(c & 0x01),
        "catalyst_complete": not bool(d & 0x01),
        "heated_catalyst_available": bool(c & 0x02),
        "heated_catalyst_complete": not bool(d & 0x02),
        "evap_available": bool(c & 0x04),
        "evap_complete": not bool(d & 0x04),
        "secondary_air_available": bool(c & 0x08),
        "secondary_air_complete": not bool(d & 0x08),
        "ac_refrigerant_available": bool(c & 0x10),
        "ac_refrigerant_complete": not bool(d & 0x10),
        "o2_sensor_available": bool(c & 0x20),
        "o2_sensor_complete": not bool(d & 0x20),
        "o2_sensor_heater_available": bool(c & 0x40),
        "o2_sensor_heater_complete": not bool(d & 0x40),
        "egr_available": bool(c & 0x80),
        "egr_complete": not bool(d & 0x80),
    }
    return monitors


def _decode_dtc(data: bytes) -> str:
    """PID 0x02: Freeze DTC decoder (2 bytes)."""
    b1, b2 = data[0], data[1]
    if b1 == 0 and b2 == 0:
        return "None"
    sys_type_code = (b1 >> 6) & 0x03
    sys_types = ("P", "C", "B", "U")
    prefix = sys_types[sys_type_code]
    d1 = (b1 >> 4) & 0x03
    d2 = b1 & 0x0F
    d3 = (b2 >> 4) & 0x0F
    d4 = b2 & 0x0F
    return f"{prefix}{d1}{d2:X}{d3:X}{d4:X}"


def _decode_fuel_system_status(data: bytes) -> dict[str, Any]:
    """PID 0x03: Fuel System Status (2 bytes)."""
    status_map = {
        0x00: "Engine off",
        0x01: "Open loop (insufficient engine temp)",
        0x02: "Closed loop (using O2 sensor feedback)",
        0x04: "Open loop (engine load / deceleration)",
        0x08: "Open loop (system failure)",
        0x10: "Closed loop with fault",
    }
    b1 = data[0]
    b2 = data[1] if len(data) > 1 else 0
    return {
        "bank1_status_code": b1,
        "bank1_status": status_map.get(b1, f"Unknown (0x{b1:02X})"),
        "bank2_status_code": b2,
        "bank2_status": status_map.get(b2, f"Unknown (0x{b2:02X})") if b2 else "Not equipped",
    }


def _decode_o2_sensor_v_trim(data: bytes) -> dict[str, float]:
    """PIDs 0x14..0x1B: Narrowband O2 sensor Voltage (V) & Short Term Trim (%)."""
    v = round(data[0] / 200.0, 3)
    if data[1] == 0xFF:
        trim = 0.0  # Unused / not participating
    else:
        trim = round((data[1] - 128) * 100.0 / 128.0, 2)
    return {"voltage_v": v, "short_term_trim_percent": trim}


def _decode_wideband_lambda_voltage(data: bytes) -> dict[str, float]:
    """PIDs 0x24..0x2B: Wideband O2 Equivalence Ratio & Voltage."""
    raw_equiv = (data[0] << 8) | data[1]
    raw_v = (data[2] << 8) | data[3]
    equiv_ratio = round(raw_equiv / 32768.0, 4)
    voltage = round(raw_v / 8192.0, 4)
    return {"equivalence_ratio": equiv_ratio, "voltage_v": voltage}


def _decode_wideband_lambda_current(data: bytes) -> dict[str, float]:
    """PIDs 0x34..0x3B: Wideband O2 Equivalence Ratio & Pumping Current."""
    raw_equiv = (data[0] << 8) | data[1]
    raw_i = (data[2] << 8) | data[3]
    equiv_ratio = round(raw_equiv / 32768.0, 4)
    current_ma = round((raw_i / 256.0) - 128.0, 3)
    return {"equivalence_ratio": equiv_ratio, "current_ma": current_ma}


def _decode_gear_and_ratio(data: bytes) -> dict[str, Any]:
    """PID 0xA4: Transmission Actual Gear and Ratio."""
    gear = data[0] - 125  # Negative = Reverse, 0 = Neutral, Positive = Forward
    ratio = round(((data[1] << 8) | data[2]) / 1000.0, 3)
    return {"gear": gear, "gear_ratio": ratio}


def _decode_def_sensor_data(data: bytes) -> dict[str, float]:
    """PID 0x9A: Diesel Exhaust Fluid (AdBlue) Tank Level and Urea Concentration."""
    level = round(data[0] * 100.0 / 255.0, 1)
    conc = round(data[1] * 0.05, 2)
    return {"tank_level_percent": level, "urea_concentration_percent": conc}


def _decode_signed_16(data: bytes, scale: float = 1.0, offset: float = 0.0) -> float:
    """Helper to decode signed 16-bit big-endian integer."""
    val = struct.unpack(">h", data[:2])[0]
    return (val * scale) + offset


class ObdPidRegistry:
    """Master registry of SAE J1979 Mode 01 Parameter Identifiers (PIDs)."""

    def __init__(self) -> None:
        self._pids: dict[int, ObdPidDefinition] = {}
        self._register_default_pids()

    def register(self, definition: ObdPidDefinition) -> None:
        """Register or update a PID definition."""
        self._pids[definition.pid] = definition

    def get(self, pid: int) -> ObdPidDefinition | None:
        """Retrieve PID definition by integer code."""
        return self._pids.get(pid)

    def all_pids(self) -> dict[int, ObdPidDefinition]:
        """Return shallow copy of all registered PID definitions."""
        return dict(self._pids)

    def decode(self, pid: int, raw_bytes: bytes) -> ObdPidResult:
        """Decode raw response bytes for given PID into a validated ObdPidResult."""
        from src.protocols.obd.models import DECODE_FAILURE_CODE, MAX_RAW_BYTES

        # REVIEW hardening: raw-payload ceiling BEFORE any decode or .hex().
        truncated = len(raw_bytes) > MAX_RAW_BYTES
        bounded = bytes(raw_bytes[:MAX_RAW_BYTES]) if truncated else bytes(raw_bytes)

        definition = self.get(pid)
        if definition is None:
            # REVIEW hardening: unknown PIDs are NOT valid telemetry.
            return ObdPidResult(
                pid=pid,
                name=f"UNKNOWN_PID_0x{pid:02X}",
                raw_bytes=bounded,
                value=bounded.hex(),
                unit="unknown",
                is_valid=False,
                error_message="unknown PID (unlisted identifier)",
                truncated=truncated,
            )

        try:
            val = definition.decode(bounded)
            # Range check for numeric values
            is_valid = True
            err_msg = None
            if isinstance(val, (int, float)):
                if definition.min_value is not None and val < definition.min_value:
                    is_valid = False
                    err_msg = f"Value {val} below minimum {definition.min_value}"
                elif definition.max_value is not None and val > definition.max_value:
                    is_valid = False
                    err_msg = f"Value {val} above maximum {definition.max_value}"
            if truncated:
                is_valid = False
                err_msg = (err_msg + "; " if err_msg else "") + "payload cut at 256B ceiling"

            return ObdPidResult(
                pid=pid,
                name=definition.name,
                raw_bytes=bounded,
                value=val,
                unit=definition.unit,
                is_valid=is_valid,
                error_message=err_msg,
                truncated=truncated,
            )
        except Exception:
            # REVIEW hardening: fixed machine-readable code, no str(exc).
            # The expected length is definition metadata (not exception
            # text), so it is safe to include for diagnostics.
            expected_len = getattr(definition, "bytes_length", None)
            detail = f": requires at least {expected_len} bytes" if expected_len else ""
            return ObdPidResult(
                pid=pid,
                name=definition.name,
                raw_bytes=bounded,
                value=None,
                unit=definition.unit,
                is_valid=False,
                error_message=f"{DECODE_FAILURE_CODE}{detail}",
                truncated=truncated,
            )

    def get_supported_pids_from_bitmask(self, bitmask_bytes: bytes, base_pid: int) -> list[int]:
        """Decode bitmask response and return supported PID list."""
        return decode_support_bitmask(bitmask_bytes, base_pid)

    def _register_default_pids(self) -> None:
        """Initialize all standard SAE J1979 Mode 01 PIDs (0x00 to 0xFF)."""
        # 1. Bitmask Anchor PIDs
        for anchor in (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xE0):
            self.register(
                ObdPidDefinition(
                    pid=anchor,
                    name=f"PIDS_SUPPORTED_0x{anchor:02X}",
                    description=f"Supported PIDs [0x{anchor+1:02X} - 0x{anchor+0x20:02X}]",
                    bytes_length=4,
                    unit="bitmap",
                    is_bitmask=True,
                    decoder=_decode_bitmask(anchor),
                    category="support",
                )
            )

        # 2. Block 0x01 - 0x20
        self.register(
            ObdPidDefinition(
                pid=0x01,
                name="MONITOR_STATUS",
                description="Monitor Status Since DTCs Cleared",
                bytes_length=4,
                unit="bitmap",
                decoder=_decode_pid_01,
                category="status",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x02,
                name="FREEZE_DTC",
                description="Freeze Frame DTC",
                bytes_length=2,
                unit="code",
                decoder=_decode_dtc,
                category="status",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x03,
                name="FUEL_SYSTEM_STATUS",
                description="Fuel System Status",
                bytes_length=2,
                unit="enum",
                decoder=_decode_fuel_system_status,
                category="fuel",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x04,
                name="CALCULATED_ENGINE_LOAD",
                description="Calculated Engine Load",
                bytes_length=1,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(b[0] * 100.0 / 255.0, 2),
                category="engine",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x05,
                name="ENGINE_COOLANT_TEMPERATURE",
                description="Engine Coolant Temperature",
                bytes_length=1,
                unit="°C",
                min_value=-40.0,
                max_value=215.0,
                decoder=lambda b: b[0] - 40,
                category="temperature",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x06,
                name="SHORT_TERM_FUEL_TRIM_BANK_1",
                description="Short Term Fuel Trim — Bank 1",
                bytes_length=1,
                unit="%",
                min_value=-100.0,
                max_value=99.22,
                decoder=lambda b: round((b[0] - 128) * 100.0 / 128.0, 2),
                category="fuel",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x07,
                name="LONG_TERM_FUEL_TRIM_BANK_1",
                description="Long Term Fuel Trim — Bank 1",
                bytes_length=1,
                unit="%",
                min_value=-100.0,
                max_value=99.22,
                decoder=lambda b: round((b[0] - 128) * 100.0 / 128.0, 2),
                category="fuel",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x08,
                name="SHORT_TERM_FUEL_TRIM_BANK_2",
                description="Short Term Fuel Trim — Bank 2",
                bytes_length=1,
                unit="%",
                min_value=-100.0,
                max_value=99.22,
                decoder=lambda b: round((b[0] - 128) * 100.0 / 128.0, 2),
                category="fuel",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x09,
                name="LONG_TERM_FUEL_TRIM_BANK_2",
                description="Long Term Fuel Trim — Bank 2",
                bytes_length=1,
                unit="%",
                min_value=-100.0,
                max_value=99.22,
                decoder=lambda b: round((b[0] - 128) * 100.0 / 128.0, 2),
                category="fuel",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x0A,
                name="FUEL_PRESSURE",
                description="Fuel Pressure (Gauge)",
                bytes_length=1,
                unit="kPa",
                min_value=0.0,
                max_value=765.0,
                decoder=lambda b: b[0] * 3,
                category="fuel",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x0B,
                name="INTAKE_MANIFOLD_PRESSURE",
                description="Intake Manifold Absolute Pressure (MAP)",
                bytes_length=1,
                unit="kPa",
                min_value=0.0,
                max_value=255.0,
                decoder=lambda b: b[0],
                category="pressure",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x0C,
                name="ENGINE_RPM",
                description="Engine Speed",
                bytes_length=2,
                unit="rpm",
                min_value=0.0,
                max_value=16383.75,
                decoder=lambda b: round(((b[0] << 8) | b[1]) / 4.0, 2),
                category="engine",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x0D,
                name="VEHICLE_SPEED",
                description="Vehicle Road Speed",
                bytes_length=1,
                unit="km/h",
                min_value=0.0,
                max_value=255.0,
                decoder=lambda b: b[0],
                category="speed",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x0E,
                name="TIMING_ADVANCE",
                description="Timing Advance (Cylinder 1)",
                bytes_length=1,
                unit="° BTDC",
                min_value=-64.0,
                max_value=63.5,
                decoder=lambda b: round((b[0] / 2.0) - 64.0, 1),
                category="engine",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x0F,
                name="INTAKE_AIR_TEMPERATURE",
                description="Intake Air Temperature (IAT)",
                bytes_length=1,
                unit="°C",
                min_value=-40.0,
                max_value=215.0,
                decoder=lambda b: b[0] - 40,
                category="temperature",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x10,
                name="MAF_AIR_FLOW_RATE",
                description="Mass Air Flow Rate (MAF)",
                bytes_length=2,
                unit="g/s",
                min_value=0.0,
                max_value=655.35,
                decoder=lambda b: round(((b[0] << 8) | b[1]) / 100.0, 2),
                category="air",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x11,
                name="THROTTLE_POSITION",
                description="Throttle Position",
                bytes_length=1,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(b[0] * 100.0 / 255.0, 2),
                category="throttle",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x12,
                name="COMMANDED_SECONDARY_AIR_STATUS",
                description="Commanded Secondary Air Status",
                bytes_length=1,
                unit="bitmap",
                decoder=lambda b: b[0],
                category="status",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x13,
                name="OXYGEN_SENSORS_PRESENT_2_BANKS",
                description="Oxygen Sensors Present (2 Banks)",
                bytes_length=1,
                unit="bitmap",
                decoder=lambda b: b[0],
                category="o2",
            )
        )
        # O2 Sensors 1..8 (0x14..0x1B)
        for i, pid in enumerate(range(0x14, 0x1C), start=1):
            self.register(
                ObdPidDefinition(
                    pid=pid,
                    name=f"O2_SENSOR_{i}_VOLTAGE_TRIM",
                    description=f"Oxygen Sensor {i}: Voltage & Short Term Trim",
                    bytes_length=2,
                    unit="V, %",
                    decoder=_decode_o2_sensor_v_trim,
                    category="o2",
                )
            )
        self.register(
            ObdPidDefinition(
                pid=0x1C,
                name="OBD_STANDARDS_CONFORMANCE",
                description="OBD Standards This Vehicle Conforms To",
                bytes_length=1,
                unit="enum",
                decoder=lambda b: OBD_STANDARDS_MAP.get(b[0], f"Unknown (0x{b[0]:02X})"),
                category="identification",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x1D,
                name="OXYGEN_SENSORS_PRESENT_4_BANKS",
                description="Oxygen Sensors Present (4 Banks)",
                bytes_length=1,
                unit="bitmap",
                decoder=lambda b: b[0],
                category="o2",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x1E,
                name="AUXILIARY_INPUT_STATUS",
                description="Auxiliary Input Status (PTO)",
                bytes_length=1,
                unit="bitmap",
                decoder=lambda b: {"pto_active": bool(b[0] & 0x01)},
                category="status",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x1F,
                name="RUN_TIME_SINCE_ENGINE_START",
                description="Run Time Since Engine Start",
                bytes_length=2,
                unit="seconds",
                min_value=0.0,
                max_value=65535.0,
                decoder=lambda b: (b[0] << 8) | b[1],
                category="time",
            )
        )

        # 3. Block 0x21 - 0x40
        self.register(
            ObdPidDefinition(
                pid=0x21,
                name="DISTANCE_TRAVELED_WITH_MIL_ON",
                description="Distance Traveled with MIL On",
                bytes_length=2,
                unit="km",
                min_value=0.0,
                max_value=65535.0,
                decoder=lambda b: (b[0] << 8) | b[1],
                category="distance",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x22,
                name="FUEL_RAIL_PRESSURE_MANIFOLD_RELATIVE",
                description="Fuel Rail Pressure (Manifold Relative)",
                bytes_length=2,
                unit="kPa",
                min_value=0.0,
                max_value=5177.26,
                decoder=lambda b: round(((b[0] << 8) | b[1]) * 0.079, 2),
                category="fuel",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x23,
                name="FUEL_RAIL_GAUGE_PRESSURE",
                description="Fuel Rail Gauge Pressure (Diesel / GDI)",
                bytes_length=2,
                unit="kPa",
                min_value=0.0,
                max_value=655350.0,
                decoder=lambda b: ((b[0] << 8) | b[1]) * 10,
                category="fuel",
            )
        )
        # Wideband O2 1..8 (0x24..0x2B) - Equivalence & Voltage
        for i, pid in enumerate(range(0x24, 0x2C), start=1):
            self.register(
                ObdPidDefinition(
                    pid=pid,
                    name=f"O2_WIDEBAND_SENSOR_{i}_VOLTAGE",
                    description=f"Wideband O2 Sensor {i}: Equivalence Ratio & Voltage",
                    bytes_length=4,
                    unit="ratio, V",
                    decoder=_decode_wideband_lambda_voltage,
                    category="o2",
                )
            )
        self.register(
            ObdPidDefinition(
                pid=0x2C,
                name="COMMANDED_EGR",
                description="Commanded EGR Duty Cycle",
                bytes_length=1,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(b[0] * 100.0 / 255.0, 2),
                category="egr",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x2D,
                name="EGR_ERROR",
                description="EGR Error",
                bytes_length=1,
                unit="%",
                min_value=-100.0,
                max_value=99.22,
                decoder=lambda b: round((b[0] - 128) * 100.0 / 128.0, 2),
                category="egr",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x2E,
                name="COMMANDED_EVAP_PURGE",
                description="Commanded Evaporative Purge",
                bytes_length=1,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(b[0] * 100.0 / 255.0, 2),
                category="evap",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x2F,
                name="FUEL_TANK_LEVEL_INPUT",
                description="Fuel Tank Level Input",
                bytes_length=1,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(b[0] * 100.0 / 255.0, 2),
                category="fuel",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x30,
                name="WARM_UPS_SINCE_CODES_CLEARED",
                description="Warm-ups Since Codes Cleared",
                bytes_length=1,
                unit="counts",
                min_value=0.0,
                max_value=255.0,
                decoder=lambda b: b[0],
                category="status",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x31,
                name="DISTANCE_TRAVELED_SINCE_CODES_CLEARED",
                description="Distance Traveled Since Codes Cleared",
                bytes_length=2,
                unit="km",
                min_value=0.0,
                max_value=65535.0,
                decoder=lambda b: (b[0] << 8) | b[1],
                category="distance",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x32,
                name="EVAP_SYSTEM_VAPOR_PRESSURE",
                description="Evap System Vapor Pressure",
                bytes_length=2,
                unit="Pa",
                min_value=-8192.0,
                max_value=8191.75,
                decoder=lambda b: round(_decode_signed_16(b, scale=0.25), 2),
                category="evap",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x33,
                name="ABSOLUTE_BAROMETRIC_PRESSURE",
                description="Absolute Barometric Pressure",
                bytes_length=1,
                unit="kPa",
                min_value=0.0,
                max_value=255.0,
                decoder=lambda b: b[0],
                category="pressure",
            )
        )
        # Wideband O2 1..8 (0x34..0x3B) - Equivalence & Current
        for i, pid in enumerate(range(0x34, 0x3C), start=1):
            self.register(
                ObdPidDefinition(
                    pid=pid,
                    name=f"O2_WIDEBAND_SENSOR_{i}_CURRENT",
                    description=f"Wideband O2 Sensor {i}: Equivalence Ratio & Current",
                    bytes_length=4,
                    unit="ratio, mA",
                    decoder=_decode_wideband_lambda_current,
                    category="o2",
                )
            )
        # Catalyst Temperatures (0x3C..0x3F)
        cat_names = [
            (0x3C, "CATALYST_TEMPERATURE_BANK_1_SENSOR_1", "Catalyst Temperature: Bank 1 Sensor 1"),
            (0x3D, "CATALYST_TEMPERATURE_BANK_2_SENSOR_1", "Catalyst Temperature: Bank 2 Sensor 1"),
            (0x3E, "CATALYST_TEMPERATURE_BANK_1_SENSOR_2", "Catalyst Temperature: Bank 1 Sensor 2"),
            (0x3F, "CATALYST_TEMPERATURE_BANK_2_SENSOR_2", "Catalyst Temperature: Bank 2 Sensor 2"),
        ]
        for pid, name, desc in cat_names:
            self.register(
                ObdPidDefinition(
                    pid=pid,
                    name=name,
                    description=desc,
                    bytes_length=2,
                    unit="°C",
                    min_value=-40.0,
                    max_value=6513.5,
                    decoder=lambda b: round((((b[0] << 8) | b[1]) / 10.0) - 40.0, 1),
                    category="temperature",
                )
            )

        # 4. Block 0x41 - 0x60
        self.register(
            ObdPidDefinition(
                pid=0x41,
                name="MONITOR_STATUS_THIS_DRIVE_CYCLE",
                description="Monitor Status This Drive Cycle",
                bytes_length=4,
                unit="bitmap",
                decoder=lambda b: (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3],
                category="status",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x42,
                name="CONTROL_MODULE_VOLTAGE",
                description="Control Module Voltage",
                bytes_length=2,
                unit="V",
                min_value=0.0,
                max_value=65.535,
                decoder=lambda b: round(((b[0] << 8) | b[1]) / 1000.0, 3),
                category="voltage",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x43,
                name="ABSOLUTE_LOAD_VALUE",
                description="Absolute Load Value",
                bytes_length=2,
                unit="%",
                min_value=0.0,
                max_value=25700.0,
                decoder=lambda b: round((((b[0] << 8) | b[1]) * 100.0) / 255.0, 2),
                category="engine",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x44,
                name="COMMANDED_EQUIVALENCE_RATIO",
                description="Commanded Fuel-Air Equivalence Ratio (Lambda)",
                bytes_length=2,
                unit="ratio",
                min_value=0.0,
                max_value=1.999,
                decoder=lambda b: round(((b[0] << 8) | b[1]) / 32768.0, 4),
                category="fuel",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x45,
                name="RELATIVE_THROTTLE_POSITION",
                description="Relative Throttle Position",
                bytes_length=1,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(b[0] * 100.0 / 255.0, 2),
                category="throttle",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x46,
                name="AMBIENT_AIR_TEMPERATURE",
                description="Ambient Air Temperature",
                bytes_length=1,
                unit="°C",
                min_value=-40.0,
                max_value=215.0,
                decoder=lambda b: b[0] - 40,
                category="temperature",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x47,
                name="ABSOLUTE_THROTTLE_POSITION_B",
                description="Absolute Throttle Position B",
                bytes_length=1,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(b[0] * 100.0 / 255.0, 2),
                category="throttle",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x48,
                name="ABSOLUTE_THROTTLE_POSITION_C",
                description="Absolute Throttle Position C",
                bytes_length=1,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(b[0] * 100.0 / 255.0, 2),
                category="throttle",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x49,
                name="ACCELERATOR_PEDAL_POSITION_D",
                description="Accelerator Pedal Position D",
                bytes_length=1,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(b[0] * 100.0 / 255.0, 2),
                category="pedal",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x4A,
                name="ACCELERATOR_PEDAL_POSITION_E",
                description="Accelerator Pedal Position E",
                bytes_length=1,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(b[0] * 100.0 / 255.0, 2),
                category="pedal",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x4B,
                name="ACCELERATOR_PEDAL_POSITION_F",
                description="Accelerator Pedal Position F",
                bytes_length=1,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(b[0] * 100.0 / 255.0, 2),
                category="pedal",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x4C,
                name="COMMANDED_THROTTLE_ACTUATOR",
                description="Commanded Throttle Actuator Control",
                bytes_length=1,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(b[0] * 100.0 / 255.0, 2),
                category="throttle",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x4D,
                name="TIME_RUN_WITH_MIL_ON",
                description="Time Run with MIL On",
                bytes_length=2,
                unit="minutes",
                min_value=0.0,
                max_value=65535.0,
                decoder=lambda b: (b[0] << 8) | b[1],
                category="time",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x4E,
                name="TIME_SINCE_TROUBLE_CODES_CLEARED",
                description="Time Since Trouble Codes Cleared",
                bytes_length=2,
                unit="minutes",
                min_value=0.0,
                max_value=65535.0,
                decoder=lambda b: (b[0] << 8) | b[1],
                category="time",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x4F,
                name="MAX_VALUES_EQUIV_V_I_P",
                description="Maximum Values for Equivalence, Voltage, Current, Pressure",
                bytes_length=4,
                unit="mult",
                decoder=lambda b: {
                    "max_equivalence_ratio": b[0],
                    "max_o2_voltage_v": b[1],
                    "max_o2_current_ma": b[2],
                    "max_intake_map_kpa": b[3] * 10,
                },
                category="limits",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x50,
                name="MAX_MAF_AIR_FLOW_RATE",
                description="Maximum Mass Air Flow Sensor Air Flow Rate",
                bytes_length=4,
                unit="g/s",
                min_value=0.0,
                max_value=2550.0,
                decoder=lambda b: b[0] * 10,
                category="air",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x51,
                name="FUEL_TYPE",
                description="Fuel Type",
                bytes_length=1,
                unit="enum",
                decoder=lambda b: FUEL_TYPES_MAP.get(b[0], f"Unknown (0x{b[0]:02X})"),
                category="fuel",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x52,
                name="ETHANOL_FUEL_PERCENT",
                description="Ethanol Fuel Percentage",
                bytes_length=1,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(b[0] * 100.0 / 255.0, 2),
                category="fuel",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x53,
                name="ABSOLUTE_EVAP_VAPOR_PRESSURE",
                description="Absolute Evaporative System Vapor Pressure",
                bytes_length=2,
                unit="kPa",
                min_value=0.0,
                max_value=327.675,
                decoder=lambda b: round(((b[0] << 8) | b[1]) / 200.0, 3),
                category="evap",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x54,
                name="EVAP_SYSTEM_VAPOR_PRESSURE_PA",
                description="Evap System Vapor Pressure (Signed Pa)",
                bytes_length=2,
                unit="Pa",
                min_value=-32767.0,
                max_value=32768.0,
                decoder=lambda b: ((b[0] << 8) | b[1]) - 32767,
                category="evap",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x59,
                name="FUEL_RAIL_ABSOLUTE_PRESSURE",
                description="Fuel Rail Absolute Pressure",
                bytes_length=2,
                unit="kPa",
                min_value=0.0,
                max_value=655350.0,
                decoder=lambda b: ((b[0] << 8) | b[1]) * 10,
                category="fuel",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x5A,
                name="RELATIVE_ACCELERATOR_PEDAL_POSITION",
                description="Relative Accelerator Pedal Position",
                bytes_length=1,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(b[0] * 100.0 / 255.0, 2),
                category="pedal",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x5B,
                name="HYBRID_BATTERY_PACK_REMAINING_LIFE",
                description="Hybrid Battery Pack Remaining Life",
                bytes_length=1,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(b[0] * 100.0 / 255.0, 2),
                category="battery",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x5C,
                name="ENGINE_OIL_TEMPERATURE",
                description="Engine Oil Temperature",
                bytes_length=1,
                unit="°C",
                min_value=-40.0,
                max_value=215.0,
                decoder=lambda b: b[0] - 40,
                category="temperature",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x5D,
                name="FUEL_INJECTION_TIMING",
                description="Fuel Injection Timing",
                bytes_length=2,
                unit="°",
                min_value=-210.0,
                max_value=302.0,
                decoder=lambda b: round((((b[0] << 8) | b[1]) - 26880) / 128.0, 2),
                category="fuel",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x5E,
                name="ENGINE_FUEL_RATE",
                description="Engine Fuel Rate",
                bytes_length=2,
                unit="L/h",
                min_value=0.0,
                max_value=3276.75,
                decoder=lambda b: round(((b[0] << 8) | b[1]) * 0.05, 2),
                category="fuel",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x5F,
                name="EMISSION_REQUIREMENTS_CONFORMANCE",
                description="Emission Requirements Conformance",
                bytes_length=1,
                unit="bitmap",
                decoder=lambda b: b[0],
                category="status",
            )
        )

        # 5. Block 0x61 - 0x80
        self.register(
            ObdPidDefinition(
                pid=0x61,
                name="DRIVERS_DEMAND_ENGINE_PERCENT_TORQUE",
                description="Driver's Demand Engine Percent Torque",
                bytes_length=1,
                unit="%",
                min_value=-125.0,
                max_value=130.0,
                decoder=lambda b: b[0] - 125,
                category="torque",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x62,
                name="ACTUAL_ENGINE_PERCENT_TORQUE",
                description="Actual Engine Percent Torque",
                bytes_length=1,
                unit="%",
                min_value=-125.0,
                max_value=130.0,
                decoder=lambda b: b[0] - 125,
                category="torque",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x63,
                name="ENGINE_REFERENCE_TORQUE",
                description="Engine Reference Torque",
                bytes_length=2,
                unit="Nm",
                min_value=0.0,
                max_value=65535.0,
                decoder=lambda b: (b[0] << 8) | b[1],
                category="torque",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x67,
                name="ENGINE_COOLANT_TEMP_SENSORS_1_2",
                description="Engine Coolant Temperature Sensors 1 and 2",
                bytes_length=3,
                unit="°C",
                decoder=lambda b: {"sensor_1_temp_c": b[1] - 40, "sensor_2_temp_c": b[2] - 40},
                category="temperature",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x6F,
                name="TURBOCHARGER_COMPRESSOR_INLET_PRESSURE",
                description="Turbocharger Compressor Inlet Pressure",
                bytes_length=3,
                unit="kPa",
                decoder=lambda b: b[1],
                category="pressure",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x70,
                name="BOOST_PRESSURE_CONTROL",
                description="Boost Pressure Control (Commanded vs Actual)",
                bytes_length=10,
                unit="kPa",
                decoder=lambda b: {
                    "commanded_boost_kpa": round(((b[1] << 8) | b[2]) * 0.03125, 2),
                    "actual_boost_kpa": round(((b[3] << 8) | b[4]) * 0.03125, 2),
                },
                category="pressure",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x71,
                name="VGT_CONTROL",
                description="Variable Geometry Turbo (VGT) Control",
                bytes_length=6,
                unit="%",
                decoder=lambda b: {
                    "commanded_vgt_percent": round(b[1] * 100.0 / 255.0, 2),
                    "actual_vgt_percent": round(b[2] * 100.0 / 255.0, 2),
                },
                category="turbo",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x74,
                name="TURBOCHARGER_RPM",
                description="Turbocharger RPM",
                bytes_length=5,
                unit="rpm",
                decoder=lambda b: (b[1] << 8) | b[2],
                category="turbo",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x77,
                name="CHARGE_AIR_COOLER_TEMP",
                description="Charge Air Cooler (CAC) Temp Sensors",
                bytes_length=5,
                unit="°C",
                decoder=lambda b: {"cac_inlet_temp_c": b[1] - 40, "cac_outlet_temp_c": b[2] - 40},
                category="temperature",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x7A,
                name="DPF_DIFFERENTIAL_PRESSURE",
                description="Diesel Particulate Filter (DPF) Differential Pressure",
                bytes_length=7,
                unit="kPa",
                decoder=lambda b: round((((b[1] << 8) | b[2]) / 100.0) - 327.68, 2),
                category="dpf",
            )
        )

        # 6. Block 0x81 - 0xA0
        self.register(
            ObdPidDefinition(
                pid=0x83,
                name="NOX_SENSOR_CONCENTRATION_BANK_1",
                description="NOx Sensor Concentration (Bank 1)",
                bytes_length=5,
                unit="ppm",
                decoder=lambda b: (b[1] << 8) | b[2],
                category="emissions",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x84,
                name="NOX_SENSOR_CONCENTRATION_BANK_2",
                description="NOx Sensor Concentration (Bank 2)",
                bytes_length=5,
                unit="ppm",
                decoder=lambda b: (b[1] << 8) | b[2],
                category="emissions",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x87,
                name="DPF_SOOT_MASS",
                description="Diesel Particulate Filter (DPF) Soot Mass",
                bytes_length=5,
                unit="g",
                min_value=0.0,
                max_value=6553.5,
                decoder=lambda b: round(((b[1] << 8) | b[2]) * 0.1, 2),
                category="dpf",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x8D,
                name="THROTTLE_POSITION_G",
                description="Throttle Position Sensor G",
                bytes_length=1,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(b[0] * 100.0 / 255.0, 2),
                category="throttle",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x8E,
                name="ENGINE_FRICTION_PERCENT_TORQUE",
                description="Engine Friction — Percent Torque",
                bytes_length=1,
                unit="%",
                min_value=-125.0,
                max_value=130.0,
                decoder=lambda b: b[0] - 125,
                category="torque",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x9A,
                name="DEF_SENSOR_DATA",
                description="Diesel Exhaust Fluid (DEF/AdBlue) Tank Level and Urea Concentration",
                bytes_length=4,
                unit="%, % Urea",
                decoder=_decode_def_sensor_data,
                category="adblue",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0x9D,
                name="ENGINE_EXHAUST_FLOW_RATE",
                description="Engine Exhaust Flow Rate",
                bytes_length=4,
                unit="kg/h",
                min_value=0.0,
                max_value=13107.0,
                decoder=lambda b: round(((b[0] << 8) | b[1]) / 5.0, 2),
                category="exhaust",
            )
        )

        # 7. Block 0xA1 - 0xC0
        self.register(
            ObdPidDefinition(
                pid=0xA1,
                name="NOX_SENSOR_CORRECTED_BANK_1_SENSOR_2",
                description="NOx Sensor Corrected (Bank 1 Sensor 2)",
                bytes_length=9,
                unit="ppm",
                decoder=lambda b: (b[1] << 8) | b[2],
                category="emissions",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0xA4,
                name="TRANSMISSION_ACTUAL_GEAR_RATIO",
                description="Transmission Actual Gear and Ratio",
                bytes_length=4,
                unit="gear, ratio",
                decoder=_decode_gear_and_ratio,
                category="transmission",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0xA5,
                name="COMMANDED_DEF_DOSING",
                description="Commanded Diesel Exhaust Fluid (DEF) Dosing Rate",
                bytes_length=4,
                unit="g/h",
                min_value=0.0,
                max_value=6553.5,
                decoder=lambda b: round(((b[0] << 8) | b[1]) / 10.0, 2),
                category="adblue",
            )
        )
        self.register(
            ObdPidDefinition(
                pid=0xA6,
                name="ODOMETER",
                description="Total Vehicle Distance (Odometer)",
                bytes_length=4,
                unit="km",
                min_value=0.0,
                max_value=429496729.5,
                decoder=lambda b: round(
                    (((b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]) * 0.1), 1
                ),
                category="distance",
            )
        )


# Module-level default singleton registry
OBD_PID_REGISTRY: ObdPidRegistry = ObdPidRegistry()
