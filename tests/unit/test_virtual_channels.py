"""Unit tests for verified mathematical virtual channels."""

from src.engine.virtual_channels.channel_engine import VirtualCalculations, VirtualChannelEngine


def test_calculate_torque_and_power() -> None:
    # 1800 RPM, 80% Torque, 1000 Nm Reference
    # Torque = (80 / 100) * 1000 = 800 Nm
    # Power kW = (1800 * 800) / 9549.2966 = 1440000 / 9549.2966 = 150.80 kW
    # Power HP = 150.80 * 1.34102 = 202.22 HP
    torque_nm, power_kw, power_hp = VirtualChannelEngine.calculate_torque_and_power(
        rpm=1800.0,
        actual_torque_percent=80.0,
        nominal_torque_nm=1000.0,
    )

    assert torque_nm == 800.0
    assert abs(power_kw - 150.80) <= 0.1
    assert abs(power_hp - 202.22) <= 0.1


def test_calculate_marine_fuel_efficiency() -> None:
    # 50 L/h at 25 knots -> 2.0 L/NM
    eff = VirtualChannelEngine.calculate_marine_fuel_efficiency(
        fuel_rate_lph=50.0,
        speed_over_ground_knots=25.0,
    )
    assert eff == 2.0

    # Stopped / drifting (<0.5 knots) returns None
    assert VirtualChannelEngine.calculate_marine_fuel_efficiency(10.0, 0.2) is None


def test_calculate_road_fuel_consumption() -> None:
    # 24 L/h at 80 km/h -> (24 * 100) / 80 = 30.0 L/100km
    cons = VirtualChannelEngine.calculate_road_fuel_consumption(
        fuel_rate_lph=24.0,
        vehicle_speed_kmh=80.0,
    )
    assert cons == 30.0

    # Stationary returns None
    assert VirtualChannelEngine.calculate_road_fuel_consumption(2.5, 0.0) is None


def test_calculate_propeller_slip() -> None:
    # Engine: 3000 RPM, Gear Ratio: 2.0 -> Shaft RPM = 1500
    # Pitch: 21 inches
    # Theoretical Speed = (1500 * 21) / 1215.22 = 31500 / 1215.22 = 25.92 knots
    # Actual Speed = 22.0 knots
    # Slip = (1 - 22.0 / 25.92) * 100 = (1 - 0.8487) * 100 = 15.13%
    slip = VirtualChannelEngine.calculate_propeller_slip(
        engine_rpm=3000.0,
        gear_ratio=2.0,
        prop_pitch_inches=21.0,
        boat_speed_knots=22.0,
    )
    assert slip is not None
    assert abs(slip - 15.13) <= 0.2


def test_output_finiteness_fail_closed() -> None:
    """AGENTS.md §2.3: finite inputs that OVERFLOW must not return an infinite
    'measurement'. Before the fix calculate_torque_and_power(1e300, 125, 1e300)
    returned (1.25e300, inf, inf) and the marine/road divisors could overflow
    to inf; an overflowed propeller theoretical speed fabricated a slip of
    exactly 100 %."""
    assert VirtualChannelEngine.calculate_torque_and_power(1e300, 125.0, 1e300) == (None, None, None)
    assert VirtualChannelEngine.calculate_marine_fuel_efficiency(1e308, 0.5) is None
    assert VirtualChannelEngine.calculate_road_fuel_consumption(1e308, 1.0) is None
    assert VirtualChannelEngine.calculate_propeller_slip(1e300, 1.0, 1e300, 20.0) is None


def test_virtual_calculations_is_flagged_synthetic() -> None:
    """AGENTS.md §2.3: derived channels must be explicitly flagged
    synthetic/virtual so a future telemetry consumer cannot merge them with
    measured channels unflagged."""
    assert VirtualCalculations().is_synthetic is True
