"""Cummins Heavy-Duty Engine & Aftertreatment J1939 Proprietary Decoder.

Target Systems: ISX15, X15, ISB6.7, QSL9, L9 Engines (CM2350, CM2450, CM871, CM2250 ECM & ACM).
Complies with SPEC-DIAG-J1939-V1.0 Section 6.1.
"""

from __future__ import annotations

from src.core.models.can_frame import CanFrame
from src.engine.decoder.dbc_decoder import DecodedSignal, SignalStatus
from src.protocols.j1939.oem.registry import BaseOemDecoder, OemDecodedPayload


class CumminsDecoder(BaseOemDecoder):
    """Proprietary J1939 decoder for Cummins commercial powertrain controllers."""

    NAME: str = "Cummins"

    # Supported PGNs
    PGN_AFTERTREATMENT_DPF: int = 65300  # 0xFF14 - Proprietary B
    PGN_CYLINDER_BALANCING: int = 65303  # 0xFF17 - Proprietary B
    PGN_PROPRIETARY_A: int = 61184  # 0xEF00 - Service Routine / Unicast

    # Known Service Routine IDs
    CMD_DPF_FORCED_REGEN_START: int = 0x3A
    CMD_DPF_REGEN_ABORT: int = 0x3B
    CMD_CYLINDER_CUTOUT_TEST: int = 0x41

    REGEN_STATUS_MAP: dict[int, str] = {
        0: "Disabled/Off",
        1: "Active Stationary (Parked)",
        2: "Active Mobile (Highway)",
        3: "Inhibited",
    }

    INHIBIT_SWITCH_MAP: dict[int, str] = {
        0: "Inhibit Off",
        1: "Inhibit Switch Active",
        2: "Error",
        3: "Not Available",
    }

    WARNING_LAMP_MAP: dict[int, str] = {
        0: "Off",
        1: "Solid (Level 1)",
        2: "Flashing (Level 2)",
        3: "Flashing with Stop Lamp (Level 3 Critical)",
    }

    @property
    def name(self) -> str:
        return self.NAME

    @property
    def supported_pgns(self) -> set[int]:
        return {
            self.PGN_AFTERTREATMENT_DPF,
            self.PGN_CYLINDER_BALANCING,
            self.PGN_PROPRIETARY_A,
        }

    def decode(
        self,
        frame: CanFrame,
        pgn: int,
        sa: int,
        da: int | None = None,
    ) -> OemDecodedPayload | None:
        """Decode Cummins frame into structured physical signals."""
        data = frame.data
        ts = frame.timestamp_ns
        arb_id = frame.arbitration_id

        if pgn == self.PGN_AFTERTREATMENT_DPF:
            return self._decode_aftertreatment_dpf(data, arb_id, sa, ts)
        elif pgn == self.PGN_CYLINDER_BALANCING:
            return self._decode_cylinder_balancing(data, arb_id, sa, ts)
        elif pgn == self.PGN_PROPRIETARY_A:
            return self._decode_proprietary_a(data, arb_id, sa, da, ts)

        return None

    def _decode_aftertreatment_dpf(
        self,
        data: bytes,
        arb_id: int,
        sa: int,
        ts: int,
    ) -> OemDecodedPayload | None:
        """Decode PGN 65300 (0xFF14) Cummins Aftertreatment Status & DPF Control."""
        if len(data) < 8:
            return None

        signals: dict[str, DecodedSignal] = {}

        # Byte 0..1: DPF Soot Mass Load (uint16 LE, 0.1 g, 0.0 offset)
        raw_soot = data[0] | (data[1] << 8)
        if raw_soot == 0xFFFF:
            signals["dpf_soot_mass_load"] = DecodedSignal(
                name="dpf_soot_mass_load",
                value=0.0,
                unit="g",
                raw_value=raw_soot,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_soot == 0xFFFE:
            signals["dpf_soot_mass_load"] = DecodedSignal(
                name="dpf_soot_mass_load",
                value=0.0,
                unit="g",
                raw_value=raw_soot,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["dpf_soot_mass_load"] = DecodedSignal(
                name="dpf_soot_mass_load",
                value=round(raw_soot * 0.1, 1),
                unit="g",
                raw_value=raw_soot,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 2 (bits 0..1): DPF Active Regeneration Status
        byte2 = data[2]
        raw_regen = byte2 & 0x03
        signals["dpf_active_regeneration_status"] = DecodedSignal(
            name="dpf_active_regeneration_status",
            value=self.REGEN_STATUS_MAP.get(raw_regen, f"Reserved ({raw_regen})"),
            unit="enum",
            raw_value=raw_regen,
            is_valid=True,
            status=SignalStatus.VALID,
        )

        # Byte 2 (bits 2..3): DPF Regeneration Inhibit Switch
        raw_inhibit = (byte2 >> 2) & 0x03
        inhibit_status = SignalStatus.VALID
        inhibit_valid = True
        if raw_inhibit == 2:
            inhibit_status = SignalStatus.ERROR
            inhibit_valid = False
        elif raw_inhibit == 3:
            inhibit_status = SignalStatus.NOT_AVAILABLE
            inhibit_valid = False

        signals["dpf_regeneration_inhibit_switch"] = DecodedSignal(
            name="dpf_regeneration_inhibit_switch",
            value=self.INHIBIT_SWITCH_MAP.get(raw_inhibit, f"State ({raw_inhibit})"),
            unit="enum",
            raw_value=raw_inhibit,
            is_valid=inhibit_valid,
            status=inhibit_status,
        )

        # Byte 2 (bits 4..7): DPF Warning Lamp State
        raw_lamp = (byte2 >> 4) & 0x0F
        lamp_status = SignalStatus.VALID
        lamp_valid = True
        if raw_lamp == 14:
            lamp_status = SignalStatus.ERROR
            lamp_valid = False
        elif raw_lamp == 15:
            lamp_status = SignalStatus.NOT_AVAILABLE
            lamp_valid = False

        signals["dpf_warning_lamp_state"] = DecodedSignal(
            name="dpf_warning_lamp_state",
            value=self.WARNING_LAMP_MAP.get(raw_lamp, f"State ({raw_lamp})"),
            unit="enum",
            raw_value=raw_lamp,
            is_valid=lamp_valid,
            status=lamp_status,
        )

        # Byte 3: DPF Ash Mass Load Index (uint8, 1.0 g)
        raw_ash = data[3]
        if raw_ash == 0xFF:
            signals["dpf_ash_mass_load_index"] = DecodedSignal(
                name="dpf_ash_mass_load_index",
                value=0.0,
                unit="g",
                raw_value=raw_ash,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_ash == 0xFE:
            signals["dpf_ash_mass_load_index"] = DecodedSignal(
                name="dpf_ash_mass_load_index",
                value=0.0,
                unit="g",
                raw_value=raw_ash,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["dpf_ash_mass_load_index"] = DecodedSignal(
                name="dpf_ash_mass_load_index",
                value=float(raw_ash),
                unit="g",
                raw_value=raw_ash,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 4..5: DPF Differential Pressure (High Res) (uint16 LE, 0.01 kPa)
        raw_dp = data[4] | (data[5] << 8)
        if raw_dp == 0xFFFF:
            signals["dpf_differential_pressure"] = DecodedSignal(
                name="dpf_differential_pressure",
                value=0.0,
                unit="kPa",
                raw_value=raw_dp,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_dp == 0xFFFE:
            signals["dpf_differential_pressure"] = DecodedSignal(
                name="dpf_differential_pressure",
                value=0.0,
                unit="kPa",
                raw_value=raw_dp,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["dpf_differential_pressure"] = DecodedSignal(
                name="dpf_differential_pressure",
                value=round(raw_dp * 0.01, 2),
                unit="kPa",
                raw_value=raw_dp,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 6..7: DEF Actual Dosing Rate (uint16 LE, 0.01 g/s)
        raw_def = data[6] | (data[7] << 8)
        if raw_def == 0xFFFF:
            signals["def_actual_dosing_rate"] = DecodedSignal(
                name="def_actual_dosing_rate",
                value=0.0,
                unit="g/s",
                raw_value=raw_def,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_def == 0xFFFE:
            signals["def_actual_dosing_rate"] = DecodedSignal(
                name="def_actual_dosing_rate",
                value=0.0,
                unit="g/s",
                raw_value=raw_def,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["def_actual_dosing_rate"] = DecodedSignal(
                name="def_actual_dosing_rate",
                value=round(raw_def * 0.01, 2),
                unit="g/s",
                raw_value=raw_def,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        return OemDecodedPayload(
            manufacturer=self.NAME,
            pgn=self.PGN_AFTERTREATMENT_DPF,
            signals=signals,
            timestamp_ns=ts,
            arbitration_id=arb_id,
            source_address=sa,
            destination_address=None,
            is_broadcast=True,
            raw_data=data,
        )

    def _decode_cylinder_balancing(
        self,
        data: bytes,
        arb_id: int,
        sa: int,
        ts: int,
    ) -> OemDecodedPayload | None:
        """Decode PGN 65303 (0xFF17) Cummins Cylinder Balancing & Injector Trimming."""
        if len(data) < 8:
            return None

        signals: dict[str, DecodedSignal] = {}

        # Bytes 0..5: Cylinder 1..6 Fuel Trim Offset (uint8, 0.1 mg/stroke, -12.8 offset)
        for cyl_idx in range(1, 7):
            raw_trim = data[cyl_idx - 1]
            sig_name = f"cylinder_{cyl_idx}_fuel_trim_offset"
            if raw_trim == 0xFF:
                signals[sig_name] = DecodedSignal(
                    name=sig_name,
                    value=0.0,
                    unit="mg/stroke",
                    raw_value=raw_trim,
                    is_valid=False,
                    status=SignalStatus.NOT_AVAILABLE,
                )
            elif raw_trim == 0xFE:
                signals[sig_name] = DecodedSignal(
                    name=sig_name,
                    value=0.0,
                    unit="mg/stroke",
                    raw_value=raw_trim,
                    is_valid=False,
                    status=SignalStatus.ERROR,
                )
            else:
                phys_val = round(raw_trim * 0.1 - 12.8, 2)
                signals[sig_name] = DecodedSignal(
                    name=sig_name,
                    value=phys_val,
                    unit="mg/stroke",
                    raw_value=raw_trim,
                    is_valid=True,
                    status=SignalStatus.VALID,
                )

        # Byte 6: Engine Balancing Quality Score (uint8, 0.4 %, 0.0 offset)
        raw_score = data[6]
        if raw_score == 0xFF:
            signals["cummins_balancing_quality_score"] = DecodedSignal(
                name="cummins_balancing_quality_score",
                value=0.0,
                unit="%",
                raw_value=raw_score,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_score == 0xFE:
            signals["cummins_balancing_quality_score"] = DecodedSignal(
                name="cummins_balancing_quality_score",
                value=0.0,
                unit="%",
                raw_value=raw_score,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["cummins_balancing_quality_score"] = DecodedSignal(
                name="cummins_balancing_quality_score",
                value=round(min(100.0, raw_score * 0.4), 1),
                unit="%",
                raw_value=raw_score,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 7: Checksum / Parity
        signals["cummins_checksum"] = DecodedSignal(
            name="cummins_checksum",
            value=data[7],
            unit="raw",
            raw_value=data[7],
            is_valid=True,
            status=SignalStatus.VALID,
        )

        return OemDecodedPayload(
            manufacturer=self.NAME,
            pgn=self.PGN_CYLINDER_BALANCING,
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
        """Decode PGN 61184 (0xEF00) Cummins Proprietary Service Routine Commands."""
        if len(data) < 2:
            return None

        cmd_id = data[0]
        # Disambiguate Cummins commands
        cmd_names = {
            self.CMD_DPF_FORCED_REGEN_START: "DPF Forced Parked Regeneration Start",
            self.CMD_DPF_REGEN_ABORT: "DPF Regeneration Abort",
            self.CMD_CYLINDER_CUTOUT_TEST: "Cylinder Cut-out Diagnostic Test",
        }

        # M-24 (P2-8): positive disambiguation — an unknown command id must
        # NEVER be claimed. The old guard only declined when the DA was not
        # the Engine (0x00), so every unknown Proprietary A command targeted
        # at the engine was reported as Cummins (first-match) with a generic
        # "Proprietary Routine" name and HIGH confidence.
        if cmd_id not in cmd_names:
            return None

        cmd_name = cmd_names[cmd_id]
        target_cyl = data[1] if len(data) > 1 else 0
        token = (data[2] | (data[3] << 8)) if len(data) >= 4 else 0

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
            "security_token": DecodedSignal(
                name="security_token",
                value=token,
                unit="raw",
                raw_value=token,
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
