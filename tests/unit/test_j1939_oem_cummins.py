"""Unit tests for Cummins OEM J1939 Decoders and OemJ1939Registry."""

from __future__ import annotations

from src.core.models.can_frame import CanFrame
from src.engine.decoder.dbc_decoder import DecodedSignal, SignalStatus
from src.protocols.j1939.oem.cummins import CumminsDecoder
from src.protocols.j1939.oem.registry import (
    BaseOemDecoder,
    OemDecodedPayload,
    OemJ1939Registry,
    build_j1939_id,
    parse_j1939_id,
)


def test_j1939_id_parsing_and_building() -> None:
    """Test 29-bit CAN ID parsing and reconstruction for PDU1 and PDU2."""
    # PDU1 Format (PF < 240) - e.g. PGN 61184 (0xEF00), Priority 6, SA=0xF9, DA=0x00
    # CAN ID: (6 << 26) | (0xEF << 16) | (0x00 << 8) | 0xF9 = 0x18EF00F9
    can_id_pdu1 = 0x18EF00F9
    pgn, sa, da, priority = parse_j1939_id(can_id_pdu1)
    assert pgn == 61184
    assert sa == 0xF9
    assert da == 0x00
    assert priority == 6

    rebuilt_pdu1 = build_j1939_id(pgn=61184, sa=0xF9, da=0x00, priority=6)
    assert rebuilt_pdu1 == can_id_pdu1

    # PDU2 Format (PF >= 240) - e.g. PGN 65300 (0xFF14), Priority 6, SA=0x00, GE=0x14
    # CAN ID: (6 << 26) | (0xFF << 16) | (0x14 << 8) | 0x00 = 0x18FF1400
    can_id_pdu2 = 0x18FF1400
    pgn, sa, da, priority = parse_j1939_id(can_id_pdu2)
    assert pgn == 65300
    assert sa == 0x00
    assert da is None
    assert priority == 6

    rebuilt_pdu2 = build_j1939_id(pgn=65300, sa=0x00, priority=6)
    assert rebuilt_pdu2 == can_id_pdu2


def test_oem_registry_initialization_and_lookup() -> None:
    """Test registry initialization, decoder queries, and hint filtering."""
    registry = OemJ1939Registry()
    assert "Cummins" in registry.list_decoders()
    assert "Caterpillar" in registry.list_decoders()
    assert "Scania" in registry.list_decoders()
    assert "Volvo" in registry.list_decoders()
    assert "Detroit" in registry.list_decoders()
    assert "Mercedes-Benz" in registry.list_decoders()

    cummins_dec = registry.get_decoder("cummins")
    assert cummins_dec is not None
    assert cummins_dec.name == "Cummins"
    assert cummins_dec.supports_pgn(65300)
    assert cummins_dec.supports_pgn(65303)
    assert cummins_dec.supports_pgn(61184)
    assert not cummins_dec.supports_pgn(12345)


def test_oem_registry_dynamic_registration_and_unregistration() -> None:
    """Test custom decoder registration and unregistration in OemJ1939Registry."""

    class CustomDecoder(BaseOemDecoder):
        @property
        def name(self) -> str:
            return "CustomTest"

        @property
        def supported_pgns(self) -> set[int]:
            return {65299}

        def decode(self, frame: CanFrame, pgn: int, sa: int, da: int | None = None) -> OemDecodedPayload | None:
            return OemDecodedPayload(
                manufacturer=self.name,
                pgn=pgn,
                signals={"test_signal": DecodedSignal(name="test_signal", value=42.0, unit="V")},
                timestamp_ns=frame.timestamp_ns,
            )

    registry = OemJ1939Registry()
    custom = CustomDecoder()
    registry.register_decoder(custom)

    assert "CustomTest" in registry.list_decoders()
    assert registry.get_decoder("customtest") is custom

    payload = registry.decode_payload(pgn=65299, data=b"\x01\x02\x03\x04\x05\x06\x07\x08")
    assert payload is not None
    assert payload.manufacturer == "CustomTest"
    assert payload.get_value("test_signal") == 42.0

    registry.unregister_decoder("CustomTest")
    assert registry.get_decoder("customtest") is None
    assert registry.decode_payload(pgn=65299, data=b"\x01\x02\x03\x04\x05\x06\x07\x08") is None


def test_proprietary_pgn_classification() -> None:
    """Test Proprietary A and B PGN classification ranges."""
    registry = OemJ1939Registry()
    assert registry.is_proprietary_pgn(61184) is True  # Proprietary A (0xEF00)
    assert registry.is_proprietary_pgn(126720) is True  # Proprietary A2 (0x1EF00)
    assert registry.is_proprietary_pgn(65280) is True  # Proprietary B Start (0xFF00)
    assert registry.is_proprietary_pgn(65300) is True  # Cummins (0xFF14)
    assert registry.is_proprietary_pgn(65535) is True  # Proprietary B End (0xFFFF)
    assert registry.is_proprietary_pgn(61444) is False  # EEC1 (0xF004 - Standard J1939)
    assert registry.is_proprietary_pgn(65226) is False  # DM1 (0xFECA - Diagnostic)


def test_cummins_aftertreatment_dpf_valid_decoding() -> None:
    """Test valid signal decoding for Cummins PGN 65300 (0xFF14)."""
    raw_payload = bytes([0xC4, 0x01, 0x16, 0x1C, 0xD2, 0x04, 0x37, 0x02])

    frame = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18FF1400,
        data=raw_payload,
        is_extended=True,
    )

    registry = OemJ1939Registry()
    decoded = registry.decode_frame(frame)

    assert decoded is not None
    assert decoded.manufacturer == "Cummins"
    assert decoded.pgn == 65300
    assert decoded.is_broadcast is True

    # Check signals
    soot_sig = decoded.get_signal("dpf_soot_mass_load")
    assert soot_sig is not None
    assert soot_sig.value == 45.2
    assert soot_sig.unit == "g"
    assert soot_sig.is_valid is True
    assert soot_sig.status == SignalStatus.VALID

    regen_sig = decoded.get_signal("dpf_active_regeneration_status")
    assert regen_sig is not None
    assert regen_sig.value == "Active Mobile (Highway)"
    assert regen_sig.is_valid is True

    inhibit_sig = decoded.get_signal("dpf_regeneration_inhibit_switch")
    assert inhibit_sig is not None
    assert inhibit_sig.value == "Inhibit Switch Active"
    assert inhibit_sig.is_valid is True

    lamp_sig = decoded.get_signal("dpf_warning_lamp_state")
    assert lamp_sig is not None
    assert lamp_sig.value == "Solid (Level 1)"
    assert lamp_sig.is_valid is True

    ash_sig = decoded.get_signal("dpf_ash_mass_load_index")
    assert ash_sig is not None
    assert ash_sig.value == 28.0
    assert ash_sig.unit == "g"

    dp_sig = decoded.get_signal("dpf_differential_pressure")
    assert dp_sig is not None
    assert dp_sig.value == 12.34
    assert dp_sig.unit == "kPa"

    def_sig = decoded.get_signal("def_actual_dosing_rate")
    assert def_sig is not None
    assert def_sig.value == 5.67
    assert def_sig.unit == "g/s"

    # Helper functions on OemDecodedPayload
    assert decoded.get_value("dpf_soot_mass_load") == 45.2
    assert decoded.is_valid("dpf_differential_pressure") is True
    assert "dpf_soot_mass_load" in decoded
    assert decoded["dpf_ash_mass_load_index"].value == 28.0

    # Serialization
    dict_repr = decoded.to_dict()
    assert dict_repr["manufacturer"] == "Cummins"
    assert dict_repr["pgn"] == 65300
    assert "signals" in dict_repr


def test_cummins_aftertreatment_sentinel_and_error_states() -> None:
    """Test guard bytes (0xFFFF, 0xFFFE, 0xFF, 0xFE) and bitfield errors."""
    raw_payload = bytes([0xFF, 0xFF, 0xE8, 0xFE, 0xFE, 0xFF, 0xFF, 0xFF])

    frame = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18FF1400,
        data=raw_payload,
        is_extended=True,
    )

    decoder = CumminsDecoder()
    decoded = decoder.decode(frame, 65300, sa=0)
    assert decoded is not None

    soot_sig = decoded.get_signal("dpf_soot_mass_load")
    assert soot_sig is not None
    assert soot_sig.is_valid is False
    assert soot_sig.status == SignalStatus.NOT_AVAILABLE

    inhibit_sig = decoded.get_signal("dpf_regeneration_inhibit_switch")
    assert inhibit_sig is not None
    assert inhibit_sig.is_valid is False
    assert inhibit_sig.status == SignalStatus.ERROR

    lamp_sig = decoded.get_signal("dpf_warning_lamp_state")
    assert lamp_sig is not None
    assert lamp_sig.is_valid is False
    assert lamp_sig.status == SignalStatus.ERROR

    ash_sig = decoded.get_signal("dpf_ash_mass_load_index")
    assert ash_sig is not None
    assert ash_sig.is_valid is False
    assert ash_sig.status == SignalStatus.ERROR

    dp_sig = decoded.get_signal("dpf_differential_pressure")
    assert dp_sig is not None
    assert dp_sig.is_valid is False
    assert dp_sig.status == SignalStatus.ERROR

    def_sig = decoded.get_signal("def_actual_dosing_rate")
    assert def_sig is not None
    assert def_sig.is_valid is False
    assert def_sig.status == SignalStatus.NOT_AVAILABLE


def test_cummins_cylinder_balancing_decoding() -> None:
    """Test Cummins PGN 65303 (0xFF17) Cylinder Balancing and Quality Score."""
    raw_payload = bytes([0x80, 0x8F, 0x69, 0x88, 0x7B, 0x82, 0xF6, 0x5A])

    frame = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18FF1700,
        data=raw_payload,
        is_extended=True,
    )

    registry = OemJ1939Registry()
    decoded = registry.decode_frame(frame)
    assert decoded is not None
    assert decoded.manufacturer == "Cummins"
    assert decoded.pgn == 65303

    assert decoded.get_value("cylinder_1_fuel_trim_offset") == 0.0
    assert decoded.get_value("cylinder_2_fuel_trim_offset") == 1.5
    assert decoded.get_value("cylinder_3_fuel_trim_offset") == -2.3
    assert decoded.get_value("cylinder_4_fuel_trim_offset") == 0.8
    assert decoded.get_value("cylinder_5_fuel_trim_offset") == -0.5
    assert decoded.get_value("cylinder_6_fuel_trim_offset") == 0.2
    assert decoded.get_value("cummins_balancing_quality_score") == 98.4

    chk_sig = decoded.get_signal("cummins_checksum")
    assert chk_sig is not None
    assert chk_sig.raw_value == 0x5A


def test_cummins_proprietary_a_service_routines() -> None:
    """Test Cummins PGN 61184 (0xEF00) service routine request parsing."""
    # Forced Parked DPF Regen Start (0x3A), Target Cyl = All (0xFF), Token = 0xA1B2
    payload_regen = bytes([0x3A, 0xFF, 0xB2, 0xA1, 0x00, 0x00, 0x00, 0x00])
    frame_regen = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18EF00F9,  # PGN 61184, DA=0x00, SA=0xF9
        data=payload_regen,
        is_extended=True,
    )

    registry = OemJ1939Registry()
    decoded = registry.decode_frame(frame_regen, manufacturer_hint="Cummins")
    assert decoded is not None
    assert decoded.pgn == 61184
    assert decoded.is_broadcast is False
    assert decoded.service_id == 0x3A
    assert decoded.get_value("service_command_name") == "DPF Forced Parked Regeneration Start"
    assert decoded.get_value("target_cylinder") == 0xFF
    assert decoded.get_value("security_token") == 0xA1B2

    # Cylinder Cutout Test (0x41), Target Cyl = 3 (0x03)
    payload_cutout = bytes([0x41, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    frame_cutout = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18EF00F9,
        data=payload_cutout,
        is_extended=True,
    )
    decoded_cutout = registry.decode_frame(frame_cutout, manufacturer_hint="Cummins")
    assert decoded_cutout is not None
    assert decoded_cutout.service_id == 0x41
    assert decoded_cutout.get_value("target_cylinder") == 3


def test_truncated_frames_and_unsupported_pgns() -> None:
    """Test that truncated frames or standard 11-bit frames are safely rejected."""
    registry = OemJ1939Registry()

    # Truncated payload for PGN 65300 (only 4 bytes instead of 8)
    frame_short = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18FF1400,
        data=bytes([0x01, 0x02, 0x03, 0x04]),
        is_extended=True,
    )
    assert registry.decode_frame(frame_short) is None

    # 11-bit Standard CAN frame is ignored by J1939 registry
    frame_std = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x7E8,
        data=bytes([0x03, 0x41, 0x0C, 0x1A, 0xF8, 0x00, 0x00, 0x00]),
        is_extended=False,
    )
    assert registry.decode_frame(frame_std) is None


# ============================================================================
# M-24 (P2-8): unknown Proprietary A command ids must never be claimed
# ============================================================================


def test_unknown_proprietary_a_command_not_claimed_by_any_oem() -> None:
    """M-24 (REVIEW M-25): PGN 61184 with an UNKNOWN command byte used to be
    claimed by the first decoder (Cummins) as a generic 'Proprietary
    Routine' with HIGH confidence whenever DA == 0x00 (Engine) — the DA
    escape hatch in the guard never triggered. Every decoder must now
    require a POSITIVE command match; unknown ids decode to None."""
    registry = OemJ1939Registry()

    # Unknown command 0x77 addressed to the engine (DA=0x00) — the exact
    # case that used to be misattributed to Cummins.
    frame_unknown = CanFrame.create(
        channel_id="can0",
        # Priority 6, PGN 61184 (PDU1), DA 0x00, SA 0xF9
        arbitration_id=0x18EF00F9,
        data=bytes([0x77, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
        is_extended=True,
    )
    assert registry.decode_frame(frame_unknown) is None

    # Known Cummins command still decodes with attribution.
    from src.protocols.j1939.oem.cummins import CumminsDecoder

    regen_cmd = getattr(CumminsDecoder, "CMD_DPF_FORCED_REGEN_START", None)
    if regen_cmd is not None:
        frame_regen = CanFrame.create(
            channel_id="can0",
            arbitration_id=0x18EF00F9,
            data=bytes([regen_cmd, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
            is_extended=True,
        )
        decoded = registry.decode_frame(frame_regen)
        assert decoded is not None
        assert decoded.manufacturer == "Cummins"
