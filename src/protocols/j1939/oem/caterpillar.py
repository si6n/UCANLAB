"""Caterpillar Commercial Engine & Clean Emission Module (CEM) J1939 Decoder.

Target Systems: C7, C9, C12, C13, C15, C18 ACERT Engines (ADEM IV, ADEM V Controllers).
Complies with SPEC-DIAG-J1939-V1.0 Section 6.2.
"""

from __future__ import annotations

from src.core.models.can_frame import CanFrame
from src.engine.decoder.dbc_decoder import DecodedSignal, SignalStatus
from src.protocols.j1939.oem.registry import BaseOemDecoder, OemDecodedPayload


class CaterpillarDecoder(BaseOemDecoder):
    """Proprietary J1939 decoder for Caterpillar heavy-duty engine controllers."""

    NAME: str = "Caterpillar"

    # Supported PGNs
    PGN_AFTERTREATMENT_REGEN: int = 65320  # 0xFF28 - Proprietary B
    PGN_CYLINDER_TRIM_HEUI: int = 65325  # 0xFF2D - Proprietary B
    PGN_PROPRIETARY_A: int = 61184  # 0xEF00 - Service Routine / Unicast

    # Service Routine Commands
    CMD_CYLINDER_CUTOUT: int = 0x20
    CMD_ARD_IGNITION_TEST: int = 0x25
    CMD_MANUAL_DPF_REGEN: int = 0x2A

    REGEN_MODE_MAP: dict[int, str] = {
        0: "Off",
        1: "Low Temp Self-clean",
        2: "High Temp Active",
        3: "Parked Service Regen",
        4: "Cooldown",
    }

    INHIBIT_STATUS_MAP: dict[int, str] = {
        0: "Enabled",
        1: "Inhibited by Cab Switch",
        2: "Inhibited by Interlock (Brake/PTO)",
    }

    @property
    def name(self) -> str:
        return self.NAME

    @property
    def supported_pgns(self) -> set[int]:
        return {
            self.PGN_AFTERTREATMENT_REGEN,
            self.PGN_CYLINDER_TRIM_HEUI,
            self.PGN_PROPRIETARY_A,
        }

    def decode(
        self,
        frame: CanFrame,
        pgn: int,
        sa: int,
        da: int | None = None,
    ) -> OemDecodedPayload | None:
        """Decode Caterpillar frame into structured physical signals."""
        data = frame.data
        ts = frame.timestamp_ns
        arb_id = frame.arbitration_id

        if pgn == self.PGN_AFTERTREATMENT_REGEN:
            return self._decode_aftertreatment_regen(data, arb_id, sa, ts)
        elif pgn == self.PGN_CYLINDER_TRIM_HEUI:
            return self._decode_cylinder_trim_heui(data, arb_id, sa, ts)
        elif pgn == self.PGN_PROPRIETARY_A:
            return self._decode_proprietary_a(data, arb_id, sa, da, ts)

        return None

    def _decode_aftertreatment_regen(
        self,
        data: bytes,
        arb_id: int,
        sa: int,
        ts: int,
    ) -> OemDecodedPayload | None:
        """Decode PGN 65320 (0xFF28) CAT Aftertreatment & Regeneration Engine Control."""
        if len(data) < 8:
            return None

        signals: dict[str, DecodedSignal] = {}

        # Byte 0: CAT ARD Combustion Air Pressure (uint8, 0.5 kPa, 0.0 offset)
        raw_air = data[0]
        if raw_air == 0xFF:
            signals["cat_ard_combustion_air_pressure"] = DecodedSignal(
                name="cat_ard_combustion_air_pressure",
                value=0.0,
                unit="kPa",
                raw_value=raw_air,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_air == 0xFE:
            signals["cat_ard_combustion_air_pressure"] = DecodedSignal(
                name="cat_ard_combustion_air_pressure",
                value=0.0,
                unit="kPa",
                raw_value=raw_air,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["cat_ard_combustion_air_pressure"] = DecodedSignal(
                name="cat_ard_combustion_air_pressure",
                value=round(raw_air * 0.5, 1),
                unit="kPa",
                raw_value=raw_air,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 1: CAT ARD Fuel Pressure (uint8, 2.0 kPa, 0.0 offset)
        raw_fuel_p = data[1]
        if raw_fuel_p == 0xFF:
            signals["cat_ard_fuel_pressure"] = DecodedSignal(
                name="cat_ard_fuel_pressure",
                value=0.0,
                unit="kPa",
                raw_value=raw_fuel_p,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_fuel_p == 0xFE:
            signals["cat_ard_fuel_pressure"] = DecodedSignal(
                name="cat_ard_fuel_pressure",
                value=0.0,
                unit="kPa",
                raw_value=raw_fuel_p,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["cat_ard_fuel_pressure"] = DecodedSignal(
                name="cat_ard_fuel_pressure",
                value=float(raw_fuel_p * 2),
                unit="kPa",
                raw_value=raw_fuel_p,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 2: CAT ARD Flame Temperature (uint8, 5.0 °C, -40.0 offset)
        raw_flame_t = data[2]
        if raw_flame_t == 0xFF:
            signals["cat_ard_flame_temperature"] = DecodedSignal(
                name="cat_ard_flame_temperature",
                value=0.0,
                unit="°C",
                raw_value=raw_flame_t,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_flame_t == 0xFE:
            signals["cat_ard_flame_temperature"] = DecodedSignal(
                name="cat_ard_flame_temperature",
                value=0.0,
                unit="°C",
                raw_value=raw_flame_t,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["cat_ard_flame_temperature"] = DecodedSignal(
                name="cat_ard_flame_temperature",
                value=float(raw_flame_t * 5 - 40),
                unit="°C",
                raw_value=raw_flame_t,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 3 (bits 0..3): CAT DPF Regeneration Mode (uint4)
        byte3 = data[3]
        raw_mode = byte3 & 0x0F
        mode_valid = True
        mode_status = SignalStatus.VALID
        if raw_mode == 14:
            mode_valid = False
            mode_status = SignalStatus.ERROR
        elif raw_mode == 15:
            mode_valid = False
            mode_status = SignalStatus.NOT_AVAILABLE

        signals["cat_dpf_regeneration_mode"] = DecodedSignal(
            name="cat_dpf_regeneration_mode",
            value=self.REGEN_MODE_MAP.get(raw_mode, f"Mode ({raw_mode})"),
            unit="enum",
            raw_value=raw_mode,
            is_valid=mode_valid,
            status=mode_status,
        )

        # Byte 3 (bits 4..7): CAT Regeneration Inhibit Status (uint4)
        raw_inhibit = (byte3 >> 4) & 0x0F
        inhibit_valid = True
        inhibit_status = SignalStatus.VALID
        if raw_inhibit == 14:
            inhibit_valid = False
            inhibit_status = SignalStatus.ERROR
        elif raw_inhibit == 15:
            inhibit_valid = False
            inhibit_status = SignalStatus.NOT_AVAILABLE

        signals["cat_regeneration_inhibit_status"] = DecodedSignal(
            name="cat_regeneration_inhibit_status",
            value=self.INHIBIT_STATUS_MAP.get(raw_inhibit, f"Status ({raw_inhibit})"),
            unit="enum",
            raw_value=raw_inhibit,
            is_valid=inhibit_valid,
            status=inhibit_status,
        )

        # Byte 4..5: CAT DPF Soot Loading Index (uint16 LE, 0.01 %, 0.0 offset)
        raw_soot = data[4] | (data[5] << 8)
        if raw_soot == 0xFFFF:
            signals["cat_dpf_soot_loading_index"] = DecodedSignal(
                name="cat_dpf_soot_loading_index",
                value=0.0,
                unit="%",
                raw_value=raw_soot,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_soot == 0xFFFE:
            signals["cat_dpf_soot_loading_index"] = DecodedSignal(
                name="cat_dpf_soot_loading_index",
                value=0.0,
                unit="%",
                raw_value=raw_soot,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["cat_dpf_soot_loading_index"] = DecodedSignal(
                name="cat_dpf_soot_loading_index",
                value=round(raw_soot * 0.01, 2),
                unit="%",
                raw_value=raw_soot,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 6: CAT DEF Quality / Urea Concentration (uint8, 0.25 %, 0.0 offset)
        raw_def = data[6]
        if raw_def == 0xFF:
            signals["cat_def_quality"] = DecodedSignal(
                name="cat_def_quality",
                value=0.0,
                unit="%",
                raw_value=raw_def,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_def == 0xFE:
            signals["cat_def_quality"] = DecodedSignal(
                name="cat_def_quality",
                value=0.0,
                unit="%",
                raw_value=raw_def,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["cat_def_quality"] = DecodedSignal(
                name="cat_def_quality",
                value=round(raw_def * 0.25, 2),
                unit="%",
                raw_value=raw_def,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 7: CAT Compression Brake / Retarder Request (uint8, 0.5 %, 0.0 offset)
        raw_brake = data[7]
        if raw_brake == 0xFF:
            signals["cat_compression_brake_request"] = DecodedSignal(
                name="cat_compression_brake_request",
                value=0.0,
                unit="%",
                raw_value=raw_brake,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_brake == 0xFE:
            signals["cat_compression_brake_request"] = DecodedSignal(
                name="cat_compression_brake_request",
                value=0.0,
                unit="%",
                raw_value=raw_brake,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["cat_compression_brake_request"] = DecodedSignal(
                name="cat_compression_brake_request",
                value=round(min(100.0, raw_brake * 0.5), 1),
                unit="%",
                raw_value=raw_brake,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        return OemDecodedPayload(
            manufacturer=self.NAME,
            pgn=self.PGN_AFTERTREATMENT_REGEN,
            signals=signals,
            timestamp_ns=ts,
            arbitration_id=arb_id,
            source_address=sa,
            destination_address=None,
            is_broadcast=True,
            raw_data=data,
        )

    def _decode_cylinder_trim_heui(
        self,
        data: bytes,
        arb_id: int,
        sa: int,
        ts: int,
    ) -> OemDecodedPayload | None:
        """Decode PGN 65325 (0xFF2D) CAT Cylinder Injection Balancing Trim."""
        if len(data) < 8:
            return None

        signals: dict[str, DecodedSignal] = {}

        # Bytes 0..5: Cylinder 1..6 MEUI/HEUI Trim Offset (uint8, 0.1 mm³/stroke, -12.8 offset)
        for cyl_idx in range(1, 7):
            raw_trim = data[cyl_idx - 1]
            sig_name = f"cat_cyl_{cyl_idx}_trim_offset"
            if raw_trim == 0xFF:
                signals[sig_name] = DecodedSignal(
                    name=sig_name,
                    value=0.0,
                    unit="mm³/stroke",
                    raw_value=raw_trim,
                    is_valid=False,
                    status=SignalStatus.NOT_AVAILABLE,
                )
            elif raw_trim == 0xFE:
                signals[sig_name] = DecodedSignal(
                    name=sig_name,
                    value=0.0,
                    unit="mm³/stroke",
                    raw_value=raw_trim,
                    is_valid=False,
                    status=SignalStatus.ERROR,
                )
            else:
                phys_val = round(raw_trim * 0.1 - 12.8, 2)
                signals[sig_name] = DecodedSignal(
                    name=sig_name,
                    value=phys_val,
                    unit="mm³/stroke",
                    raw_value=raw_trim,
                    is_valid=True,
                    status=SignalStatus.VALID,
                )

        # Byte 6..7: CAT Rail / Actuation High Pressure (uint16 LE, 0.1 MPa, 0.0 offset)
        raw_hp = data[6] | (data[7] << 8)
        if raw_hp == 0xFFFF:
            signals["cat_rail_actuation_high_pressure"] = DecodedSignal(
                name="cat_rail_actuation_high_pressure",
                value=0.0,
                unit="MPa",
                raw_value=raw_hp,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_hp == 0xFFFE:
            signals["cat_rail_actuation_high_pressure"] = DecodedSignal(
                name="cat_rail_actuation_high_pressure",
                value=0.0,
                unit="MPa",
                raw_value=raw_hp,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["cat_rail_actuation_high_pressure"] = DecodedSignal(
                name="cat_rail_actuation_high_pressure",
                value=round(raw_hp * 0.1, 1),
                unit="MPa",
                raw_value=raw_hp,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        return OemDecodedPayload(
            manufacturer=self.NAME,
            pgn=self.PGN_CYLINDER_TRIM_HEUI,
            signals=signals,
            timestamp_ns=ts,
            arbitration_id=arb_id,
            source_address=sa,
            destination_address=None,
            is_broadcast=True,
            raw_data=data,
        )

    def _decode_proprietary_a(
        self,
        data: bytes,
        arb_id: int,
        sa: int,
        da: int | None,
        ts: int,
    ) -> OemDecodedPayload | None:
        """Decode PGN 61184 (0xEF00) CAT Proprietary Service Routine Commands."""
        if len(data) < 2:
            return None

        cmd_id = data[0]
        cmd_names = {
            self.CMD_CYLINDER_CUTOUT: "Cylinder Cutout Diagnostic Test",
            self.CMD_ARD_IGNITION_TEST: "ARD Burner Ignition Test",
            self.CMD_MANUAL_DPF_REGEN: "Manual DPF Regeneration Trigger",
        }

        # M-24 (P2-8): positive disambiguation — an unknown command id must
        # never be claimed (see Cummins for the full rationale; the old
        # `and da != 0x00` guard let engine-addressed unknowns through).
        if cmd_id not in cmd_names:
            return None

        cmd_name = cmd_names[cmd_id]
        target_cyl = data[1] if len(data) > 1 else 0
        param = (data[2] | (data[3] << 8)) if len(data) >= 4 else 0

        signals: dict[str, DecodedSignal] = {
            "service_command_id": DecodedSignal(
                name="service_command_id",
                value=cmd_id,
                unit="hex",
                raw_value=cmd_id,
                is_valid=True,
                status=SignalStatus.VALID,
            ),
            "service_command_name": DecodedSignal(
                name="service_command_name",
                value=cmd_name,
                unit="string",
                raw_value=cmd_id,
                is_valid=True,
                status=SignalStatus.VALID,
            ),
            "target_cylinder": DecodedSignal(
                name="target_cylinder",
                value=target_cyl,
                unit="index",
                raw_value=target_cyl,
                is_valid=True,
                status=SignalStatus.VALID,
            ),
            "service_parameter": DecodedSignal(
                name="service_parameter",
                value=param,
                unit="raw",
                raw_value=param,
                is_valid=True,
                status=SignalStatus.VALID,
            ),
        }

        return OemDecodedPayload(
            manufacturer=self.NAME,
            pgn=self.PGN_PROPRIETARY_A,
            signals=signals,
            timestamp_ns=ts,
            arbitration_id=arb_id,
            source_address=sa,
            destination_address=da,
            is_broadcast=False,
            service_id=cmd_id,
            raw_data=data,
        )
