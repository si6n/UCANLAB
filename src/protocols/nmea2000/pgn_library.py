"""NMEA 2000 Standard Marine PGN Decoders (Engine Rapid, Dynamic, Transmission, Fluid)."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.logging import get_logger

logger = get_logger("protocols.nmea2000.pgn")

PGN_ENGINE_RAPID: int = 127488
PGN_ENGINE_DYNAMIC: int = 127489
PGN_TRANSMISSION_DYNAMIC: int = 127493
# REVIEW 1-H1 (HIGH): the real NMEA 2000 Fluid Level message is PGN 127505
# (8-byte single frame). The previous value 127497 is "Trip Parameters,
# Engine" — a completely different 9-byte Fast-Packet message whose Trip
# Fuel Used field would be mis-scaled as a fluid level percentage.
PGN_FLUID_LEVEL: int = 127505


@dataclass(slots=True)
class EngineRapidParameters:
    """NMEA 2000 PGN 127488 (Engine Parameters, Rapid Update)."""

    engine_instance: int
    engine_speed_rpm: float | None
    boost_pressure_kpa: float | None
    tilt_trim_percent: int | None


@dataclass(slots=True)
class EngineDynamicParameters:
    """NMEA 2000 PGN 127489 (Engine Parameters, Dynamic - Fast Packet)."""

    engine_instance: int
    oil_pressure_kpa: float | None
    oil_temp_c: float | None
    coolant_temp_c: float | None
    alternator_voltage_v: float | None
    fuel_rate_lph: float | None
    total_engine_hours: float | None
    engine_load_percent: int | None
    engine_torque_percent: int | None


@dataclass(slots=True)
class TransmissionParameters:
    """NMEA 2000 PGN 127493 (Transmission Parameters, Dynamic - Fast Packet)."""

    transmission_instance: int
    gear: str  # "neutral" | "forward" | "reverse" | "unknown"
    oil_pressure_kpa: float | None
    oil_temp_c: float | None


@dataclass(slots=True)
class FluidLevelParameters:
    """NMEA 2000 PGN 127505 (Fluid Level)."""

    fluid_type: str  # "fuel" | "fresh_water" | "waste_water" | "oil" | "black_water"
    fluid_instance: int
    level_percent: float | None
    capacity_liters: float | None


FLUID_TYPES: dict[int, str] = {
    0: "fuel",
    1: "fresh_water",
    2: "waste_water",
    3: "live_well",
    4: "oil",
    5: "black_water",
}

# REVIEW 3-M (MEDIUM): N2K instance-field validity — 0..250 are legal
# instance ids; 0xFB..0xFF are reserved / error / not-available /
# unavailable indicators (canboat DBC "DI_INSTANCE" table). An instance
# byte in the reserved band means the payload is not a valid instance-
# carrying message — decode refuses instead of fabricating an engine/
# transmission "instance 251".
RESERVED_INSTANCE_VALUES: frozenset[int] = frozenset({0xFB, 0xFC, 0xFD, 0xFE, 0xFF})


def _valid_instance(value: int) -> bool:
    """True when the instance byte is a legal N2K instance id (0..250)."""
    return value not in RESERVED_INSTANCE_VALUES and 0 <= value <= 0xFA

GEAR_TYPES: dict[int, str] = {
    0: "neutral",
    1: "forward",
    2: "reverse",
}


class Nmea2000PgnDecoder:
    """Parser for common Marine NMEA 2000 PGNs."""

    @classmethod
    def decode_engine_rapid(cls, data: bytes) -> EngineRapidParameters | None:
        """Decode PGN 127488 (8 bytes)."""
        if len(data) < 6:
            return None

        instance = data[0]
        if not _valid_instance(instance):  # REVIEW 3-M: reserved instance band
            logger.warning(
                "N2K Engine Rapid reserved instance value — decode refused",
                extra={"instance": instance},
            )
            return None

        # Engine Speed (Bytes 1..2): 0.25 rpm / bit
        raw_speed = int.from_bytes(data[1:3], byteorder="little")
        speed_rpm = raw_speed * 0.25 if raw_speed < 0xFFFE else None

        # Boost Pressure (Bytes 3..4): 100 Pa / bit -> / 1000 = 0.1 kPa
        raw_boost = int.from_bytes(data[3:5], byteorder="little")
        boost_kpa = (raw_boost * 100) / 1000.0 if raw_boost < 0xFFFE else None

        # Tilt / Trim (Byte 5): 1% / bit (signed int8: -100% .. +100%)
        raw_tilt = int.from_bytes(data[5:6], byteorder="little", signed=True)
        tilt_percent = raw_tilt if (-100 <= raw_tilt <= 100) else None

        return EngineRapidParameters(
            engine_instance=instance,
            engine_speed_rpm=speed_rpm,
            boost_pressure_kpa=boost_kpa,
            tilt_trim_percent=tilt_percent,
        )

    @classmethod
    def decode_engine_dynamic(cls, data: bytes) -> EngineDynamicParameters | None:
        """Decode PGN 127489 (Fast Packet, 26 bytes).

        REVIEW 1-H2 (HIGH): per the repo's own canboat DBC (PGN 127489,
        "engineParametersDynamic"), Engine Load is byte 24 and Engine
        Torque byte 25 — the previous code read Load from byte 21, which is
        the Discrete Status 1 flags byte, silently reporting e.g. "23 %
        engine load" for what are actually status bits. Alternator Voltage
        (bytes 7-8) and Fuel Rate (bytes 9-10) are SIGNED int16 in the DBC
        (two's complement); treating 0x8000+ raw values as unsigned
        produced physically impossible 327.x V / 3276 L/h readings.
        """
        if len(data) < 26:
            return None

        instance = data[0]
        if not _valid_instance(instance):  # REVIEW 3-M: reserved instance band
            logger.warning(
                "N2K Engine Dynamic reserved instance value — decode refused",
                extra={"instance": instance},
            )
            return None

        # Oil Pressure (Bytes 1..2): 100 Pa / bit -> kPa
        raw_oil_p = int.from_bytes(data[1:3], byteorder="little")
        oil_p_kpa = (raw_oil_p * 100) / 1000.0 if raw_oil_p < 0xFFFE else None

        # Oil Temp (Bytes 3..4): 0.1 K / bit -> °C
        raw_oil_t = int.from_bytes(data[3:5], byteorder="little")
        oil_t_c = (raw_oil_t * 0.1) - 273.15 if raw_oil_t < 0xFFFE else None

        # Coolant Temp (Bytes 5..6): 0.01 K / bit -> °C
        raw_cool_t = int.from_bytes(data[5:7], byteorder="little")
        cool_t_c = (raw_cool_t * 0.01) - 273.15 if raw_cool_t < 0xFFFE else None

        # Alternator Potential / Voltage (Bytes 7..8): 0.01 V / bit, signed
        raw_volt = int.from_bytes(data[7:9], byteorder="little", signed=True)
        volt_v = raw_volt * 0.01 if raw_volt != -0x8000 else None

        # Fuel Rate (Bytes 9..10): 0.1 L/h / bit, signed
        raw_fuel_r = int.from_bytes(data[9:11], byteorder="little", signed=True)
        fuel_lph = raw_fuel_r * 0.1 if raw_fuel_r != -0x8000 else None

        # Total Engine Hours (Bytes 11..14): 1 s / bit -> hours
        raw_hours = int.from_bytes(data[11:15], byteorder="little")
        hours = raw_hours / 3600.0 if raw_hours < 0xFFFFFFFE else None

        # Engine Load % (Byte 24); Engine Torque % (Byte 25, signed int8)
        raw_load = data[24]
        load_pct = raw_load if raw_load <= 100 else None

        raw_torque = int.from_bytes(data[25:26], byteorder="little", signed=True)
        torque_pct = raw_torque if -100 <= raw_torque <= 100 else None

        return EngineDynamicParameters(
            engine_instance=instance,
            oil_pressure_kpa=oil_p_kpa,
            oil_temp_c=oil_t_c,
            coolant_temp_c=cool_t_c,
            alternator_voltage_v=volt_v,
            fuel_rate_lph=fuel_lph,
            total_engine_hours=hours,
            engine_load_percent=load_pct,
            engine_torque_percent=torque_pct,
        )

    @classmethod
    def decode_transmission(cls, data: bytes) -> TransmissionParameters | None:
        """Decode PGN 127493 (Fast Packet ~12 bytes)."""
        if len(data) < 6:
            return None

        instance = data[0]
        if not _valid_instance(instance):  # REVIEW 3-M: reserved instance band
            logger.warning(
                "N2K Transmission reserved instance value — decode refused",
                extra={"instance": instance},
            )
            return None
        gear_code = data[1] & 0x03
        gear_str = GEAR_TYPES.get(gear_code, "unknown")

        raw_oil_p = int.from_bytes(data[2:4], byteorder="little")
        oil_p_kpa = (raw_oil_p * 100) / 1000.0 if raw_oil_p < 0xFFFE else None

        raw_oil_t = int.from_bytes(data[4:6], byteorder="little")
        oil_t_c = (raw_oil_t * 0.1) - 273.15 if raw_oil_t < 0xFFFE else None

        return TransmissionParameters(
            transmission_instance=instance,
            gear=gear_str,
            oil_pressure_kpa=oil_p_kpa,
            oil_temp_c=oil_t_c,
        )

    @classmethod
    def decode_fluid_level(cls, data: bytes) -> FluidLevelParameters | None:
        """Decode PGN 127505 (8 bytes).

        REVIEW 1-H1 (HIGH): per the repo's own canboat DBC
        (data/dbc/marine/n2k_canboat.dbc, "fluidLevel"), Instance occupies
        bits 0-3 (low nibble of byte 0) and Type bits 4-7 (high nibble) —
        the previous code had them swapped, so e.g. fuel tank #2 (0x20)
        decoded as "fresh water tank #0".
        """
        if len(data) < 8:
            return None

        fluid_instance = data[0] & 0x0F
        fluid_type_code = (data[0] >> 4) & 0x0F
        fluid_type_str = FLUID_TYPES.get(fluid_type_code, "other")

        # Level % (Bytes 1..2): 0.004 % / bit
        raw_level = int.from_bytes(data[1:3], byteorder="little")
        level_pct = raw_level * 0.004 if raw_level < 0xFFFE else None

        # Capacity (Bytes 3..6): 0.1 L / bit
        raw_cap = int.from_bytes(data[3:7], byteorder="little")
        cap_l = raw_cap * 0.1 if raw_cap < 0xFFFFFFFE else None

        return FluidLevelParameters(
            fluid_type=fluid_type_str,
            fluid_instance=fluid_instance,
            level_percent=level_pct,
            capacity_liters=cap_l,
        )
