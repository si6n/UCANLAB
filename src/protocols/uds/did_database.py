"""ISO 14229-1 Unified Diagnostic Services (UDS) DID Knowledge Base & Decoders.

Provides complete registry for standard ISO 14229 / ISO 27145 DIDs (0xF180 to 0xF1AF)
and extended powertrain/telemetry DIDs (0x0100 to 0x03FF, 0xF010).
"""

from __future__ import annotations

import struct
from enum import IntEnum

from src.protocols.obd.models import UdsDidDefinition, UdsDidResult


class DiagnosticSessionEnum(IntEnum):
    """0xF186 Active Diagnostic Session types."""

    DEFAULT_SESSION = 0x01
    PROGRAMMING_SESSION = 0x02
    EXTENDED_DIAGNOSTIC_SESSION = 0x03
    SAFETY_SYSTEM_DIAGNOSTIC_SESSION = 0x04


class SecurityAccessStateEnum(IntEnum):
    """Security Access state levels."""

    LOCKED = 0x00
    UNLOCKED_LEVEL_1 = 0x01
    UNLOCKED_LEVEL_2 = 0x02
    UNLOCKED_LEVEL_3 = 0x03
    UNLOCKED_LEVEL_4 = 0x04
    UNLOCKED_ENGINEERING = 0x05


class IgnitionStatusEnum(IntEnum):
    """0x0101 Ignition Terminal 15 Status."""

    KEY_OFF = 0
    ACCESSORY = 1
    IGNITION_ON = 2
    CRANK_START = 3


class DpfRegenStatusEnum(IntEnum):
    """0x0201 DPF Regeneration Mode."""

    IDLE = 0
    PASSIVE = 1
    ACTIVE_LOW = 2
    ACTIVE_HIGH = 3
    PARKED = 4
    INHIBITED = 5


# Helper decoders


def _decode_ascii_string(raw: bytes) -> str:
    """Decode raw bytes as ASCII string, stripping trailing nulls and spaces."""
    return raw.decode("ascii", errors="replace").strip("\x00 \t\r\n")


def _decode_bcd_date(raw: bytes) -> str:
    """Decode 4-byte BCD date (YYYYMMDD) into formatted ISO date string 'YYYY-MM-DD'."""
    if len(raw) < 4:
        return raw.hex()
    try:
        year = f"{raw[0]:02X}{raw[1]:02X}"
        month = f"{raw[2]:02X}"
        day = f"{raw[3]:02X}"
        return f"{year}-{month}-{day}"
    except Exception:
        return raw.hex()


def _decode_fingerprint(raw: bytes) -> dict[str, str]:
    """Decode 9..16 byte programming fingerprint (Date + Tester ID / Tool ID)."""
    if len(raw) >= 4:
        date_str = _decode_bcd_date(raw[:4])
        tester_id = raw[4:].hex().upper()
        return {"date": date_str, "tester_id": tester_id, "raw_hex": raw.hex().upper()}
    return {"raw_hex": raw.hex().upper()}


def _decode_signed_16(data: bytes, scale: float = 1.0, offset: float = 0.0) -> float:
    """Decode signed 16-bit big-endian integer."""
    if len(data) < 2:
        raise ValueError(f"Signed 16-bit decoding requires at least 2 bytes, got {len(data)}")
    val = struct.unpack(">h", data[:2])[0]
    return round((val * scale) + offset, 3)


class UdsDidRegistry:
    """Master registry of ISO 14229 UDS Data Identifiers (DIDs)."""

    def __init__(self) -> None:
        self._dids: dict[int, UdsDidDefinition] = {}
        self._register_default_dids()

    def register(self, definition: UdsDidDefinition) -> None:
        """Register or update a DID definition."""
        self._dids[definition.did] = definition

    def get(self, did: int) -> UdsDidDefinition | None:
        """Retrieve DID definition by integer code."""
        return self._dids.get(did)

    def all_dids(self) -> dict[int, UdsDidDefinition]:
        """Return shallow copy of all registered DID definitions."""
        return dict(self._dids)

    def decode(self, did: int, raw_bytes: bytes) -> UdsDidResult:
        """Decode raw response bytes for given DID into a validated UdsDidResult."""
        from src.protocols.obd.models import DECODE_FAILURE_CODE, MAX_RAW_BYTES

        # REVIEW hardening: raw-payload ceiling BEFORE any decode or .hex()
        # (hex doubles memory; unbounded attacker bytes must never become
        # "valid" telemetry).
        truncated = len(raw_bytes) > MAX_RAW_BYTES
        bounded = bytes(raw_bytes[:MAX_RAW_BYTES]) if truncated else bytes(raw_bytes)

        definition = self.get(did)
        if definition is None:
            # REVIEW hardening: unknown DIDs are NOT valid telemetry —
            # attacker bytes must not reflect as VALID. Unit is "unknown"
            # (not "raw"), value is the (possibly cut) hex, flagged.
            return UdsDidResult(
                did=did,
                name=f"UNKNOWN_DID_0x{did:04X}",
                raw_bytes=bounded,
                value=bounded.hex().upper(),
                unit="unknown",
                is_valid=False,
                error_message="unknown DID (unlisted identifier)",
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

            return UdsDidResult(
                did=did,
                name=definition.name,
                raw_bytes=bounded,
                value=val,
                unit=definition.unit,
                is_valid=is_valid,
                error_message=err_msg,
                truncated=truncated,
            )
        except Exception:
            # REVIEW hardening: fixed machine-readable code — no str(exc)
            # reflection (internal exception text is an oracle + log bloat).
            # The expected length is definition metadata (not exception
            # text), so it is safe to include for diagnostics.
            return UdsDidResult(
                did=did,
                name=definition.name,
                raw_bytes=bounded,
                value=None,
                unit=definition.unit,
                is_valid=False,
                error_message=f"{DECODE_FAILURE_CODE}: requires at least {definition.length} bytes",
                truncated=truncated,
            )

    def _register_default_dids(self) -> None:
        """Initialize all standard ISO 14229 / ISO 27145 and Extended Telemetry DIDs."""
        # 1. Standard DIDs (0xF180 - 0xF1AF)
        self.register(
            UdsDidDefinition(
                did=0xF180,
                name="BOOT_SOFTWARE_IDENTIFICATION",
                description="Boot Software Identification",
                length=None,
                unit="string",
                data_format="ascii",
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF181,
                name="APPLICATION_SOFTWARE_IDENTIFICATION",
                description="Application Software Identification",
                length=None,
                unit="string",
                data_format="ascii",
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF182,
                name="APPLICATION_DATA_IDENTIFICATION",
                description="Application Data / Calibration Identification",
                length=None,
                unit="string",
                data_format="ascii",
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF183,
                name="BOOT_SOFTWARE_FINGERPRINT",
                description="Boot Software Fingerprint (Date + Tester ID)",
                length=None,
                unit="fingerprint",
                decoder=_decode_fingerprint,
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF184,
                name="APPLICATION_SOFTWARE_FINGERPRINT",
                description="Application Software Fingerprint (Date + Tester ID)",
                length=None,
                unit="fingerprint",
                decoder=_decode_fingerprint,
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF185,
                name="APPLICATION_DATA_FINGERPRINT",
                description="Application Data Fingerprint (Date + Tester ID)",
                length=None,
                unit="fingerprint",
                decoder=_decode_fingerprint,
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF186,
                name="ACTIVE_DIAGNOSTIC_SESSION",
                description="Active Diagnostic Session",
                length=1,
                unit="enum",
                # Length is asserted by UdsDidDefinition.decode before this
                # runs; short payloads surface as the fixed DID_TRUNCATED
                # code (no str(exc) reflection).
                decoder=lambda b: DiagnosticSessionEnum(b[0])
                if b[0] in DiagnosticSessionEnum._value2member_map_
                else b[0],
                category="session",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF187,
                name="VEHICLE_MANUFACTURER_SPARE_PART_NUMBER",
                description="Vehicle Manufacturer Spare Part Number",
                length=None,
                unit="string",
                data_format="ascii",
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF188,
                name="VEHICLE_MANUFACTURER_ECU_SOFTWARE_NUMBER",
                description="Vehicle Manufacturer ECU Software Number",
                length=None,
                unit="string",
                data_format="ascii",
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF189,
                name="VEHICLE_MANUFACTURER_ECU_SOFTWARE_VERSION",
                description="Vehicle Manufacturer ECU Software Version",
                length=None,
                unit="string",
                data_format="ascii",
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF18A,
                name="SYSTEM_SUPPLIER_IDENTIFIER",
                description="System Supplier Identifier",
                length=None,
                unit="string",
                data_format="ascii",
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF18B,
                name="ECU_MANUFACTURING_DATE",
                description="ECU Manufacturing Date (BCD YYYYMMDD)",
                length=4,
                unit="date",
                decoder=_decode_bcd_date,
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF18C,
                name="ECU_SERIAL_NUMBER",
                description="ECU Serial Number",
                length=None,
                unit="string",
                data_format="ascii",
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF190,
                name="VEHICLE_IDENTIFICATION_NUMBER",
                description="Vehicle Identification Number (VIN)",
                length=17,
                unit="string",
                data_format="ascii",
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF191,
                name="VEHICLE_MANUFACTURER_ECU_HARDWARE_NUMBER",
                description="Vehicle Manufacturer ECU Hardware Number",
                length=None,
                unit="string",
                data_format="ascii",
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF192,
                name="SYSTEM_SUPPLIER_ECU_HARDWARE_NUMBER",
                description="System Supplier ECU Hardware Number",
                length=None,
                unit="string",
                data_format="ascii",
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF193,
                name="SYSTEM_SUPPLIER_ECU_HARDWARE_VERSION",
                description="System Supplier ECU Hardware Version",
                length=None,
                unit="string",
                data_format="ascii",
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF194,
                name="SYSTEM_SUPPLIER_ECU_SOFTWARE_NUMBER",
                description="System Supplier ECU Software Number",
                length=None,
                unit="string",
                data_format="ascii",
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF195,
                name="SYSTEM_SUPPLIER_ECU_SOFTWARE_VERSION",
                description="System Supplier ECU Software Version",
                length=None,
                unit="string",
                data_format="ascii",
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF196,
                name="EXHAUST_REGULATION_TYPE_APPROVAL_NUMBER",
                description="Exhaust Regulation or Type Approval Number",
                length=None,
                unit="string",
                data_format="ascii",
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF197,
                name="SYSTEM_NAME_OR_ENGINE_TYPE",
                description="System Name or Engine Type",
                length=None,
                unit="string",
                data_format="ascii",
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF198,
                name="REPAIR_SHOP_CODE_OR_TESTER_SERIAL_NUMBER",
                description="Repair Shop Code or Tester Serial Number",
                length=None,
                unit="string",
                data_format="ascii",
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF199,
                name="PROGRAMMING_DATE",
                description="Programming Date (BCD YYYYMMDD)",
                length=4,
                unit="date",
                decoder=_decode_bcd_date,
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF19A,
                name="CALIBRATION_REPAIR_SHOP_CODE",
                description="Calibration Repair Shop Code",
                length=None,
                unit="string",
                data_format="ascii",
                category="identification",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF19D,
                name="ECU_INSTALLATION_DATE",
                description="ECU Installation Date (BCD YYYYMMDD)",
                length=4,
                unit="date",
                decoder=_decode_bcd_date,
                category="identification",
            )
        )
        # CVN and Counters (0xF1A0..0xF1AF)
        self.register(
            UdsDidDefinition(
                did=0xF1A0,
                name="CALIBRATION_VERIFICATION_NUMBER_1",
                description="Calibration Verification Number (CVN) #1",
                length=4,
                unit="hex",
                data_format="raw_hex",
                category="security",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF1A1,
                name="CALIBRATION_VERIFICATION_NUMBER_2",
                description="Calibration Verification Number (CVN) #2",
                length=4,
                unit="hex",
                data_format="raw_hex",
                category="security",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF1A2,
                name="FLASH_COUNTER",
                description="Flash Counter (Cumulative Successful Flashes)",
                length=2,
                unit="counts",
                decoder=lambda b: (b[0] << 8) | b[1],
                category="security",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF1A3,
                name="FLASH_ATTEMPT_COUNTER",
                description="Flash Attempt Counter",
                length=2,
                unit="counts",
                decoder=lambda b: (b[0] << 8) | b[1],
                category="security",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF1A4,
                name="SEED_KEY_FAILURE_PENALTY_COUNTER",
                description="Seed-Key Security Failure Penalty Counter",
                length=1,
                unit="counts",
                decoder=lambda b: b[0],
                category="security",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0xF1A5,
                name="SECURITY_ACCESS_STATE_LOCK_FLAG",
                description="Security Access State Lock Flag",
                length=1,
                unit="enum",
                decoder=lambda b: SecurityAccessStateEnum(b[0])
                if b[0] in SecurityAccessStateEnum._value2member_map_
                else b[0],
                category="security",
            )
        )

        # 2. Extended Powertrain & Telemetry DIDs (0x0100 - 0x03FF & 0xF010)
        self.register(
            UdsDidDefinition(
                did=0xF010,
                name="BATTERY_TERMINAL_30_VOLTAGE_F010",
                description="Battery Terminal 30 Voltage",
                length=2,
                unit="V",
                min_value=0.0,
                max_value=655.35,
                decoder=lambda b: round(((b[0] << 8) | b[1]) / 100.0, 2),
                category="telemetry",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0x0100,
                name="BATTERY_TERMINAL_30_VOLTAGE",
                description="Battery Terminal 30 Voltage",
                length=2,
                unit="V",
                min_value=0.0,
                max_value=655.35,
                decoder=lambda b: round(((b[0] << 8) | b[1]) / 100.0, 2),
                category="telemetry",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0x0101,
                name="IGNITION_SWITCH_TERMINAL_15_STATUS",
                description="Ignition Switch / Terminal 15 Status",
                length=1,
                unit="enum",
                decoder=lambda b: IgnitionStatusEnum(b[0])
                if b[0] in IgnitionStatusEnum._value2member_map_
                else b[0],
                category="telemetry",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0x0102,
                name="ENGINE_COOLANT_TEMPERATURE",
                description="Engine Coolant Temperature",
                length=2,
                unit="°C",
                min_value=-40.0,
                max_value=215.0,
                decoder=lambda b: round((((b[0] << 8) | b[1]) / 10.0) - 40.0, 1),
                category="telemetry",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0x0103,
                name="ENGINE_CRANKSHAFT_SPEED",
                description="Engine Crankshaft Rotational Speed",
                length=2,
                unit="rpm",
                min_value=0.0,
                max_value=16383.75,
                decoder=lambda b: round(((b[0] << 8) | b[1]) / 4.0, 2),
                category="telemetry",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0x0104,
                name="ACCELERATOR_PEDAL_POSITION",
                description="Accelerator Pedal Position",
                length=2,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(((b[0] << 8) | b[1]) / 100.0, 2),
                category="telemetry",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0x0105,
                name="BRAKE_MASTER_CYLINDER_PRESSURE",
                description="Brake Master Cylinder Hydraulic Pressure",
                length=2,
                unit="bar",
                min_value=0.0,
                max_value=6553.5,
                decoder=lambda b: round(((b[0] << 8) | b[1]) / 10.0, 1),
                category="telemetry",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0x0106,
                name="STEERING_WHEEL_ANGLE",
                description="Steering Wheel Angle",
                length=2,
                unit="deg",
                min_value=-3276.8,
                max_value=3276.7,
                decoder=lambda b: _decode_signed_16(b, scale=0.1),
                category="telemetry",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0x0107,
                name="VEHICLE_ROAD_SPEED",
                description="Vehicle Road Speed",
                length=2,
                unit="km/h",
                min_value=0.0,
                max_value=655.35,
                decoder=lambda b: round(((b[0] << 8) | b[1]) / 100.0, 2),
                category="telemetry",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0x0110,
                name="SECURITY_ACCESS_STATE",
                description="Security Access State Mask",
                length=1,
                unit="enum",
                decoder=lambda b: SecurityAccessStateEnum(b[0])
                if b[0] in SecurityAccessStateEnum._value2member_map_
                else b[0],
                category="security",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0x0200,
                name="DPF_SOOT_MASS_ACCUMULATION",
                description="DPF Soot Mass Accumulation",
                length=2,
                unit="g",
                min_value=0.0,
                max_value=6553.5,
                decoder=lambda b: round(((b[0] << 8) | b[1]) / 10.0, 1),
                category="aftertreatment",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0x0201,
                name="DPF_REGENERATION_STATUS_MODE",
                description="DPF Regeneration Status Mode",
                length=1,
                unit="enum",
                decoder=lambda b: DpfRegenStatusEnum(b[0])
                if b[0] in DpfRegenStatusEnum._value2member_map_
                else b[0],
                category="aftertreatment",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0x0202,
                name="DPF_DIFFERENTIAL_PRESSURE",
                description="DPF Differential Pressure",
                length=2,
                unit="kPa",
                min_value=0.0,
                max_value=655.35,
                decoder=lambda b: round(((b[0] << 8) | b[1]) / 100.0, 2),
                category="aftertreatment",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0x0210,
                name="ADBLUE_DEF_DOSING_RATE",
                description="AdBlue / DEF Dosing Rate",
                length=2,
                unit="g/s",
                min_value=0.0,
                max_value=655.35,
                decoder=lambda b: round(((b[0] << 8) | b[1]) / 100.0, 2),
                category="aftertreatment",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0x0211,
                name="ADBLUE_DEF_TANK_LEVEL",
                description="AdBlue / DEF Tank Level",
                length=1,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(b[0] * 100.0 / 255.0, 1),
                category="aftertreatment",
            )
        )
        self.register(
            UdsDidDefinition(
                did=0x0212,
                name="ADBLUE_DEF_UREA_CONCENTRATION",
                description="AdBlue / DEF Urea Concentration",
                length=2,
                unit="%",
                min_value=0.0,
                max_value=100.0,
                decoder=lambda b: round(((b[0] << 8) | b[1]) / 100.0, 2),
                category="aftertreatment",
            )
        )

        # Injector Balancing Trim Offsets (0x0300 - 0x0305)
        for cyl in range(1, 7):
            did = 0x0300 + (cyl - 1)
            self.register(
                UdsDidDefinition(
                    did=did,
                    name=f"INJECTOR_{cyl}_BALANCING_TRIM_OFFSET",
                    description=f"Cylinder #{cyl} Injector Balancing Trim Offset",
                    length=2,
                    unit="mg/stroke",
                    min_value=-327.68,
                    max_value=327.67,
                    decoder=lambda b: _decode_signed_16(b, scale=0.01),
                    category="cylinder_balancing",
                )
            )


# Module-level default singleton registry
UDS_DID_REGISTRY: UdsDidRegistry = UdsDidRegistry()
