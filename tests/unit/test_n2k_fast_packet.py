"""Unit tests for NMEA 2000 Fast Packet and PGN decoders."""

from src.core.models.can_frame import CanFrame
from src.protocols.nmea2000.fast_packet import Nmea2000FastPacketDecoder
from src.protocols.nmea2000.pgn_library import Nmea2000PgnDecoder


def test_n2k_fast_packet_reassembly() -> None:
    decoder = Nmea2000FastPacketDecoder()

    # Frame 0: Seq 0, Index 0 -> Header = 0x00, Total Bytes = 16
    f0_data = b"\x00\x10" + b"\x00\x01\x02\x03\x04\x05"
    f0 = CanFrame.create(
        channel_id="n2k",
        arbitration_id=0x19F20100,  # PGN 127489 (0x1F201)
        data=f0_data,
        is_extended=True,
    )
    res0 = decoder.handle_rx_frame(f0)
    assert res0 is None

    # Frame 1: Seq 0, Index 1 -> Header = 0x01, 7 bytes payload
    f1_data = b"\x01" + b"\x06\x07\x08\x09\x0a\x0b\x0c"
    f1 = CanFrame.create(
        channel_id="n2k",
        arbitration_id=0x19F20100,
        data=f1_data,
        is_extended=True,
    )
    res1 = decoder.handle_rx_frame(f1)
    assert res1 is None

    # Frame 2: Seq 0, Index 2 -> Header = 0x02, remaining 3 bytes payload
    f2_data = b"\x02" + b"\x0d\x0e\x0f\xff\xff\xff\xff"
    f2 = CanFrame.create(
        channel_id="n2k",
        arbitration_id=0x19F20100,
        data=f2_data,
        is_extended=True,
    )
    res2 = decoder.handle_rx_frame(f2)
    assert res2 is not None

    assert res2.pgn == 127489
    assert len(res2.data) == 16
    assert res2.data == bytes(range(16))


def test_decode_engine_rapid() -> None:
    # Instance 0, Speed 2000 RPM (2000 / 0.25 = 8000 = 0x1F40 -> 0x40, 0x1F)
    # Boost 150 kPa = 150000 Pa / 100 = 1500 = 0x05DC -> 0xDC, 0x05
    # Tilt 10%
    data = b"\x00\x40\x1f\xdc\x05\x0a\xff\xff"
    res = Nmea2000PgnDecoder.decode_engine_rapid(data)
    assert res is not None
    assert res.engine_instance == 0
    assert res.engine_speed_rpm == 2000.0
    assert res.boost_pressure_kpa == 150.0
    assert res.tilt_trim_percent == 10


def test_decode_fluid_level() -> None:
    # REVIEW 1-H1 fix: Instance occupies the LOW nibble, Type the HIGH nibble
    # (canboat DBC "fluidLevel"). Fuel (0) tank #1 -> Byte 0 = 0x01.
    # Level: 75.0% -> 75.0 / 0.004 = 18750 = 0x493E -> 0x3E, 0x49
    # Capacity: 500 L -> 500 / 0.1 = 5000 = 0x1388 -> 0x88, 0x13, 0x00, 0x00
    data = b"\x01\x3e\x49\x88\x13\x00\x00\xff"
    res = Nmea2000PgnDecoder.decode_fluid_level(data)
    assert res is not None
    assert res.fluid_type == "fuel"
    assert res.fluid_instance == 1
    assert res.level_percent == 75.0
    assert res.capacity_liters == 500.0

    # Instance 2, fresh water (1) -> Byte 0 = 0x12 — the exact case the old
    # swapped decoder mislabelled as "fuel tank #2".
    data2 = b"\x12\x3e\x49\x88\x13\x00\x00\xff"
    res2 = Nmea2000PgnDecoder.decode_fluid_level(data2)
    assert res2 is not None
    assert res2.fluid_type == "fresh_water"
    assert res2.fluid_instance == 2


def _fp_first_frame(total_bytes: int, pgn_id: int = 0x19F20100, seq: int = 0, payload: bytes = b"") -> CanFrame:
    """Fast Packet index-0 frame: header = seq<<5, total length in byte 1."""
    header = (seq << 5) & 0xFF
    data = bytes([header, total_bytes]) + payload[:6] + b"\xff" * max(0, 6 - len(payload[:6]))
    return CanFrame.create(
        channel_id="n2k",
        arbitration_id=pgn_id,
        data=data,
        is_extended=True,
    )


def _fp_next_frame(index: int, payload: bytes, pgn_id: int = 0x19F20100, seq: int = 0) -> CanFrame:
    header = ((seq << 5) | (index & 0x1F)) & 0xFF
    data = bytes([header]) + payload + b"\xff" * (7 - len(payload))
    return CanFrame.create(
        channel_id="n2k",
        arbitration_id=pgn_id,
        data=data[:8],
        is_extended=True,
    )


def test_n2k_boundary_223_bytes_accepted() -> None:
    """223 bytes = 6 + 7*31 is the maximum legal Fast Packet size and must complete."""
    decoder = Nmea2000FastPacketDecoder()
    payload = bytes((i % 256) for i in range(223))

    assert decoder.handle_rx_frame(_fp_first_frame(223, payload=payload)) is None
    res = None
    for idx in range(1, 32):
        res = decoder.handle_rx_frame(_fp_next_frame(idx, payload[6 + (idx - 1) * 7 : 6 + idx * 7]))
    assert res is not None
    assert res.data == payload


def test_n2k_boundary_224_bytes_rejected() -> None:
    """224 bytes exceeds the 6+7*31 limit — the session must never open."""
    decoder = Nmea2000FastPacketDecoder()
    assert decoder.handle_rx_frame(_fp_first_frame(224)) is None
    # A subsequent CF finds no session and is dropped silently
    assert decoder.handle_rx_frame(_fp_next_frame(1, b"\x01" * 7)) is None
    assert len(decoder._sessions) == 0


def test_n2k_min_boundary_8_bytes_rejected() -> None:
    """Below 9 bytes there is no need for Fast Packet at all — rejected."""
    decoder = Nmea2000FastPacketDecoder()
    assert decoder.handle_rx_frame(_fp_first_frame(8)) is None
    assert len(decoder._sessions) == 0


def test_n2k_sequence_mismatch_drops_session() -> None:
    """An out-of-order CF must evict the session — partial data never completes."""
    decoder = Nmea2000FastPacketDecoder()
    decoder.handle_rx_frame(_fp_first_frame(20))

    # Skip index 1, deliver index 2
    assert decoder.handle_rx_frame(_fp_next_frame(2, b"\x41" * 7)) is None
    # Session evicted: even the correct next index finds nothing
    assert decoder.handle_rx_frame(_fp_next_frame(1, b"\x42" * 7)) is None
    assert len(decoder._sessions) == 0


def test_n2k_index0_restart_drops_stale_session() -> None:
    """A mid-transfer restart (index 0) replaces stale state — new data wins."""
    decoder = Nmea2000FastPacketDecoder()
    decoder.handle_rx_frame(_fp_first_frame(20))
    decoder.handle_rx_frame(_fp_next_frame(1, b"\x41" * 7))  # in-flight, 13/20 bytes

    # Sender restarts with a fresh 16-byte message for the same key
    payload = bytes(range(16))
    restarted = decoder.handle_rx_frame(_fp_first_frame(16, payload=payload))
    assert restarted is None
    assert len(decoder._sessions) == 1  # stale dropped, one fresh session

    assert decoder.handle_rx_frame(_fp_next_frame(1, payload[6:13])) is None
    res = decoder.handle_rx_frame(_fp_next_frame(2, payload[13:16]))
    assert res is not None
    assert res.data == payload  # the RESTARTED payload, not stale bytes


def test_n2k_timeout_evicts_session() -> None:
    """500 ms of silence must evict an in-flight session."""
    import time as _time

    decoder = Nmea2000FastPacketDecoder()
    decoder.handle_rx_frame(_fp_first_frame(20))
    assert len(decoder._sessions) == 1

    _time.sleep(0.55)
    # Any subsequent frame triggers the expired-session sweep first
    decoder.handle_rx_frame(_fp_next_frame(1, b"\x41" * 7))
    assert len(decoder._sessions) == 0


def test_n2k_single_frame_pgn_never_opens_session() -> None:
    """REVIEW 1-H3: single-frame PGNs (127488/127493/128267/127505) whose
    data[0]/data[1] mimic a fast-packet first frame must NOT open sessions —
    the old PGN-agnostic filter swallowed the real single-frame signal.
    """
    from src.protocols.nmea2000.fast_packet import FAST_PACKET_PGNS

    for pgn, arb in ((127488, 0x19F10000), (127493, 0x19F104FF), (128267, 0x1F20D00), (127505, 0x1F21100)):
        assert pgn not in FAST_PACKET_PGNS, f"PGN {pgn} must stay single-frame"
        # data[1]=16 (9..223 range) with a normal engine-rapid payload —
        # the exact pattern that used to open a phantom 16-byte session.
        mimic = CanFrame.create(
            channel_id="n2k",
            arbitration_id=arb,
            data=b"\x00\x10\x40\x1f\xdc\x05\x0a\xff",
            is_extended=True,
        )
        decoder = Nmea2000FastPacketDecoder()
        assert decoder.handle_rx_frame(mimic) is None
        assert len(decoder._sessions) == 0


def test_n2k_pgn_library_fluid_pgn_is_127505() -> None:
    """REVIEW 1-H1: the Fluid Level PGN constant must be 127505 (the real
    NMEA 2000 Fluid Level message), never 127497 (Trip Parameters, Engine —
    a Fast-Packet message per canboat DBC).
    """
    from src.protocols.nmea2000.fast_packet import FAST_PACKET_PGNS
    from src.protocols.nmea2000.pgn_library import PGN_FLUID_LEVEL

    assert PGN_FLUID_LEVEL == 127505
    assert 127505 not in FAST_PACKET_PGNS  # fluid level is single-frame
    assert 127497 in FAST_PACKET_PGNS  # trip parameters IS fast-packet (canboat)


def test_n2k_engine_dynamic_load_byte24_and_signed() -> None:
    """REVIEW 1-H2: Engine Load reads byte 24 (not 21), Torque byte 25;
    Alternator Voltage & Fuel Rate decode as SIGNED int16.
    """
    # 26-byte fast-packet payload: instance 0, oil P 250 kPa, oil T 90 C,
    # coolant 88 C, voltage +14.5 V (1450 = 0x05AA), fuel rate -1.5 L/h
    # (-15 = 0xFFF1 two's complement), hours 12345 s, status bytes 15..23,
    # load 87 % at byte 24, torque -12 % at byte 25 (0xF4).
    buf = bytearray(26)
    buf[0] = 0x00
    buf[1:3] = (2500).to_bytes(2, "little")  # 2500 * 100 Pa = 250 kPa
    buf[3:5] = int((90 + 273.15) * 10).to_bytes(2, "little")
    buf[5:7] = int((88 + 273.15) * 100).to_bytes(2, "little")
    buf[7:9] = (1450).to_bytes(2, "little")  # +14.50 V
    buf[9:11] = (-15 & 0xFFFF).to_bytes(2, "little")  # -1.5 L/h
    buf[11:15] = (12345).to_bytes(4, "little")
    buf[24] = 87  # Engine Load at byte 24
    buf[25] = (-12) & 0xFF  # Engine Torque -12% (signed int8)

    res = Nmea2000PgnDecoder.decode_engine_dynamic(bytes(buf))
    assert res is not None
    assert res.engine_load_percent == 87
    assert res.engine_torque_percent == -12
    assert res.alternator_voltage_v == 14.5
    assert res.fuel_rate_lph == -1.5

    # Signed edge: 0x8000 (most-negative int16) must yield None, not a
    # physically impossible 327.68 V / -3276.8 L/h reading.
    buf2 = bytearray(26)
    buf2[7:9] = (0x8000).to_bytes(2, "little")
    buf2[9:11] = (0x8000).to_bytes(2, "little")
    res2 = Nmea2000PgnDecoder.decode_engine_dynamic(bytes(buf2))
    assert res2 is not None
    assert res2.alternator_voltage_v is None
    assert res2.fuel_rate_lph is None
