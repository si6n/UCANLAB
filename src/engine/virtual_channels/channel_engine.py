"""Verified Mathematical Virtual Channels Engine.

Complies with SAE J1939-71 and Marine Telemetry standards (MASTER_PLAN.md Section 8.2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar


@dataclass(slots=True)
class VirtualCalculations:
    """Calculated mathematical physical parameters from raw sensor streams.

    AGENTS.md §2.3 (No Fabricated Telemetry): every value in this record is a
    DERIVED channel computed from measured inputs, never a raw sensor reading.
    ``is_synthetic`` is ``True`` by construction so a future consumer that
    wires these into a telemetry path cannot merge them with measured channels
    unflagged — the engine also returns ``None`` (never a guess) when an input
    is missing, and ``None`` when the result is not a finite, physically
    plausible number.
    """

    is_synthetic: bool = True
    torque_nm: float | None = None
    power_kw: float | None = None
    power_hp: float | None = None
    marine_fuel_efficiency_l_nm: float | None = None
    road_fuel_consumption_l_100km: float | None = None
    propeller_slip_percent: float | None = None


class VirtualChannelEngine:
    """Computes derived engine torque, horsepower, fuel efficiency, and propeller slip."""

    KW_TO_HP_FACTOR: ClassVar[float] = 1.34102209
    TORQUE_CONSTANT: ClassVar[float] = 9549.2965855  # 60000 / (2 * pi)
    KNOTS_PITCH_CONSTANT: ClassVar[float] = 1215.22  # 1 knot = 1215.22 inches/min

    @classmethod
    def calculate_torque_and_power(
        cls,
        rpm: float | None,
        actual_torque_percent: float | None,
        nominal_torque_nm: float | None = None,
    ) -> tuple[float | None, float | None, float | None]:
        """Calculate Engine Torque (Nm), Power (kW), and Metric Horsepower (HP) (B-11).

        Formulas:
            Torque (Nm) = (Actual Torque % / 100) * Nominal Torque (Nm)
            Power (kW) = (RPM * Torque) / 9549.3
            Power (HP) = Power (kW) * 1.34102

        P2-7 (AGENTS.md §2.3): `nominal_torque_nm` used to DEFAULT to 1000.0.
        An engine's rated torque is a per-engine nameplate figure; a caller that
        omitted it was handed `1000 Nm * torque%` — a fabricated measurement
        indistinguishable from a real one. The parameter is now OPTIONAL and
        unevaluable without it, so the function returns `(None, None, None)`
        rather than inventing a rating. An invalid value that the caller DID
        supply is still rejected loudly (fail-closed, and it keeps the existing
        ValueError contract for explicit bad input).
        """
        if rpm is None or actual_torque_percent is None:
            return None, None, None
        if not (math.isfinite(rpm) and math.isfinite(actual_torque_percent)):
            return None, None, None
        if rpm < 0 or actual_torque_percent < -125 or actual_torque_percent > 125:
            return None, None, None

        # Omitted -> no nameplate rating -> not evaluable. Never fabricate one.
        if nominal_torque_nm is None:
            return None, None, None
        if not math.isfinite(nominal_torque_nm) or nominal_torque_nm <= 0:
            raise ValueError(
                f"nominal_torque_nm must be a finite positive number (> 0), got {nominal_torque_nm!r}"
            )

        torque_nm = (actual_torque_percent / 100.0) * nominal_torque_nm
        # Negative torque represents engine braking / retarder
        power_kw = (rpm * torque_nm) / cls.TORQUE_CONSTANT
        power_hp = power_kw * cls.KW_TO_HP_FACTOR
        # Input finiteness does not imply output finiteness: finite inputs
        # (e.g. 1e300 * 1e300) overflow to inf, and an infinite value is NOT
        # a measurement (AGENTS.md §2.3).
        if not (math.isfinite(torque_nm) and math.isfinite(power_kw) and math.isfinite(power_hp)):
            return None, None, None

        return round(torque_nm, 2), round(power_kw, 2), round(power_hp, 2)

    @classmethod
    def calculate_marine_fuel_efficiency(
        cls,
        fuel_rate_lph: float,
        speed_over_ground_knots: float,
    ) -> float | None:
        """Calculate Marine Fuel Economy in Liters per Nautical Mile (L/NM).

        L-6 (P3-1): NaN fails closed — NaN comparisons are always False, so
        the threshold checks alone let NaN leak into the division and
        produced NaN telemetry instead of None.
        """
        if not (math.isfinite(fuel_rate_lph) and math.isfinite(speed_over_ground_knots)):
            return None
        if speed_over_ground_knots < 0.5 or fuel_rate_lph < 0:
            return None
        result = fuel_rate_lph / speed_over_ground_knots
        if not math.isfinite(result):
            return None  # finite inputs can overflow to inf — not a measurement
        return round(result, 3)

    @classmethod
    def calculate_road_fuel_consumption(
        cls,
        fuel_rate_lph: float,
        vehicle_speed_kmh: float,
    ) -> float | None:
        """Calculate Road Vehicle Fuel Consumption in Liters per 100 Kilometers (L/100km).

        L-6 (P3-1): NaN fails closed (see marine efficiency).
        """
        if not (math.isfinite(fuel_rate_lph) and math.isfinite(vehicle_speed_kmh)):
            return None
        if vehicle_speed_kmh < 1.0 or fuel_rate_lph < 0:
            return None
        result = (fuel_rate_lph * 100.0) / vehicle_speed_kmh
        if not math.isfinite(result):
            return None  # finite inputs can overflow to inf — not a measurement
        return round(result, 2)

    #: P2-8: physically plausible bounds for propeller slip (%). A propeller
    #: cannot drive a hull faster than its own pitch speed, so slip below
    #: roughly -50 % means the sensed boat speed is not coming from this
    #: propeller (operator typo, wrong unit, GPS/engine signal cross-wired),
    #: and slip above 100 % means the boat is moving backwards or the shaft
    #: RPM is bogus. Outside this window the value is NOT a measurement, so it
    #: is reported as `None` rather than clamped into a plausible-looking
    #: number (AGENTS.md §2.3 — no silent normalisation).
    PROPELLER_SLIP_MIN_PERCENT: ClassVar[float] = -50.0
    PROPELLER_SLIP_MAX_PERCENT: ClassVar[float] = 100.0

    @classmethod
    def calculate_propeller_slip(
        cls,
        engine_rpm: float,
        gear_ratio: float,
        prop_pitch_inches: float,
        boat_speed_knots: float,
    ) -> float | None:
        """Calculate Marine Propeller Slip Percentage (Slip %).

        Formula:
            Theoretical Speed (knots) = ((Engine RPM / Gear Ratio) * Pitch (inches)) / 1215.22
            Slip % = (1.0 - (Boat Speed / Theoretical Speed)) * 100.0

        L-6 (P3-1): NaN fails closed (see marine efficiency).
        P2-8: implausible slip (outside PROPELLER_SLIP_MIN/MAX) returns None
        instead of a silently-clamped or impossible number.
        """
        if not (
            math.isfinite(engine_rpm)
            and math.isfinite(gear_ratio)
            and math.isfinite(prop_pitch_inches)
            and math.isfinite(boat_speed_knots)
        ):
            return None
        if engine_rpm <= 100 or gear_ratio <= 0 or prop_pitch_inches <= 0 or boat_speed_knots < 0:
            return None

        shaft_rpm = engine_rpm / gear_ratio
        theoretical_speed_knots = (shaft_rpm * prop_pitch_inches) / cls.KNOTS_PITCH_CONSTANT

        # An overflowed (infinite) theoretical speed turns any finite boat
        # speed into a fabricated slip of exactly 100 % — not a measurement.
        if not math.isfinite(theoretical_speed_knots) or theoretical_speed_knots <= 0:
            return None

        slip = (1.0 - (boat_speed_knots / theoretical_speed_knots)) * 100.0
        if not cls.PROPELLER_SLIP_MIN_PERCENT <= slip <= cls.PROPELLER_SLIP_MAX_PERCENT:
            return None
        return round(slip, 2)
