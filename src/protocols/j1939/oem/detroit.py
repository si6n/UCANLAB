"""Detroit Diesel DD13 / DD15 / DD16 Architecture J1939 Proprietary Decoder.

Target Systems: DD13, DD15, DD16 Heavy Duty Powertrains.
Controllers: MCM21T (Motor Control Module), ACM21T (Aftertreatment Control Module), CPC4.
Complies with SPEC-DIAG-J1939-V1.0 Section 6.5.
"""

from __future__ import annotations

from src.core.models.can_frame import CanFrame
from src.engine.decoder.dbc_decoder import DecodedSignal, SignalStatus
from src.protocols.j1939.oem.registry import BaseOemDecoder, OemDecodedPayload


class DetroitDecoder(BaseOemDecoder):
    """Proprietary J1939 decoder for Detroit Diesel DD-series commercial engines."""

    NAME: str = "Detroit"

    # Supported PGNs
    PGN_AFTERTREATMENT_ACM: int = 65370  # 0xFF5A - Proprietary B
    PGN_JAKE_BRAKE_RETARDER: int = 65375  # 0xFF5F - Proprietary B
    PGN_CYLINDER_BALANCING: int = 65380  # 0xFF64 - Proprietary B
    PGN_PROPRIETARY_A: int = 61184  # 0xEF00 - Service Routine / Unicast

    REGEN_MODE_MAP: dict[int, str] = {
        0: "Passive",
        1: "Active Low",
        2: "Active High",
        3: "Parked Service Regen",
        4: "Inhibited",
    }

    INHIBIT_REASON_MAP: dict[int, str] = {
        0: "None",
        1: "Inhibit Switch",
        2: "Vehicle Speed",
        3: "Clutch/PTO",
    }

    DEF_QUALITY_MAP: dict[int, str] = {
        0x00: "Nominal (32.5% Urea)",
        0x01: "Degraded (28-30%)",
        0x02: "Poor Quality (<28%)",
        0x03: "Tamper / Water Detected",
    }

    JAKE_STAGE_MAP: dict[int, str] = {
        0: "Off",
        1: "Low (2-Cylinder Compression)",
        2: "Medium (4-Cylinder)",
        3: "High (6-Cylinder)",
    }

    @property
    def name(self) -> str:
        return self.NAME

    @property
    def supported_pgns(self) -> set[int]:
        return {
            self.PGN_AFTERTREATMENT_ACM,
            self.PGN_JAKE_BRAKE_RETARDER,
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
        """Decode Detroit Diesel frame into structured physical signals."""
        data = frame.data
        ts = frame.timestamp_ns
        arb_id = frame.arbitration_id

        if pgn == self.PGN_AFTERTREATMENT_ACM:
            return self._decode_aftertreatment_acm(data, arb_id, sa, ts)
        elif pgn == self.PGN_JAKE_BRAKE_RETARDER:
            return self._decode_jake_brake_retarder(data, arb_id, sa, ts)
        elif pgn == self.PGN_CYLINDER_BALANCING:
            return self._decode_cylinder_balancing(data, arb_id, sa, ts)
        elif pgn == self.PGN_PROPRIETARY_A:
            return self._decode_proprietary_a(data, arb_id, sa, da, ts)

        return None

    def _decode_aftertreatment_acm(
        self,
        data: bytes,
        arb_id: int,
        sa: int,
        ts: int,
    ) -> OemDecodedPayload | None:
        """Decode PGN 65370 (0xFF5A) Detroit Diesel ACM Aftertreatment & DPF Status."""
        if len(data) < 8:
            return None

        signals: dict[str, DecodedSignal] = {}

        # Byte 0..1: Detroit DPF Soot Mass Accumulation (uint16 LE, 0.1 g, 0.0 offset)
        raw_soot = data[0] | (data[1] << 8)
        if raw_soot == 0xFFFF:
            signals["detroit_dpf_soot_mass_accumulation"] = DecodedSignal(
                name="detroit_dpf_soot_mass_accumulation",
                value=0.0,
                unit="g",
                raw_value=raw_soot,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_soot == 0xFFFE:
            signals["detroit_dpf_soot_mass_accumulation"] = DecodedSignal(
                name="detroit_dpf_soot_mass_accumulation",
                value=0.0,
                unit="g",
                raw_value=raw_soot,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["detroit_dpf_soot_mass_accumulation"] = DecodedSignal(
                name="detroit_dpf_soot_mass_accumulation",
                value=round(raw_soot * 0.1, 1),
                unit="g",
                raw_value=raw_soot,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 2..3: Detroit DPF Ash Mass Accumulation (uint16 LE, 1.0 g, 0.0 offset)
        raw_ash = data[2] | (data[3] << 8)
        if raw_ash == 0xFFFF:
            signals["detroit_dpf_ash_mass_accumulation"] = DecodedSignal(
                name="detroit_dpf_ash_mass_accumulation",
                value=0.0,
                unit="g",
                raw_value=raw_ash,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_ash == 0xFFFE:
            signals["detroit_dpf_ash_mass_accumulation"] = DecodedSignal(
                name="detroit_dpf_ash_mass_accumulation",
                value=0.0,
                unit="g",
                raw_value=raw_ash,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["detroit_dpf_ash_mass_accumulation"] = DecodedSignal(
                name="detroit_dpf_ash_mass_accumulation",
                value=float(raw_ash),
                unit="g",
                raw_value=raw_ash,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 4 (bits 0..3): Detroit DPF Regeneration Mode (uint4)
        byte4 = data[4]
        raw_mode = byte4 & 0x0F
        mode_valid = True
        mode_status = SignalStatus.VALID
        if raw_mode == 14:
            mode_valid = False
            mode_status = SignalStatus.ERROR
        elif raw_mode == 15:
            mode_valid = False
            mode_status = SignalStatus.NOT_AVAILABLE

        signals["detroit_dpf_regeneration_mode"] = DecodedSignal(
            name="detroit_dpf_regeneration_mode",
            value=self.REGEN_MODE_MAP.get(raw_mode, f"Mode ({raw_mode})"),
            unit="enum",
            raw_value=raw_mode,
            is_valid=mode_valid,
            status=mode_status,
        )

        # Byte 4 (bits 4..7): Detroit DPF Regeneration Inhibit Reason (uint4)
        raw_inhibit = (byte4 >> 4) & 0x0F
        inhibit_valid = True
        inhibit_status = SignalStatus.VALID
        if raw_inhibit == 14:
            inhibit_valid = False
            inhibit_status = SignalStatus.ERROR
        elif raw_inhibit == 15:
            inhibit_valid = False
            inhibit_status = SignalStatus.NOT_AVAILABLE

        signals["detroit_dpf_regeneration_inhibit_reason"] = DecodedSignal(
            name="detroit_dpf_regeneration_inhibit_reason",
            value=self.INHIBIT_REASON_MAP.get(raw_inhibit, f"Reason ({raw_inhibit})"),
            unit="enum",
            raw_value=raw_inhibit,
            is_valid=inhibit_valid,
            status=inhibit_status,
        )

        # Byte 5..6: Detroit DEF Dosing Rate (Instantaneous) (uint16 LE, 0.1 g/min, 0.0 offset)
        raw_dosing = data[5] | (data[6] << 8)
        if raw_dosing == 0xFFFF:
            signals["detroit_def_dosing_rate"] = DecodedSignal(
                name="detroit_def_dosing_rate",
                value=0.0,
                unit="g/min",
                raw_value=raw_dosing,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_dosing == 0xFFFE:
            signals["detroit_def_dosing_rate"] = DecodedSignal(
                name="detroit_def_dosing_rate",
                value=0.0,
                unit="g/min",
                raw_value=raw_dosing,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["detroit_def_dosing_rate"] = DecodedSignal(
                name="detroit_def_dosing_rate",
                value=round(raw_dosing * 0.1, 1),
                unit="g/min",
                raw_value=raw_dosing,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 7: Detroit DEF Quality Status (uint8)
        raw_qual = data[7]
        qual_valid = True
        qual_status = SignalStatus.VALID
        if raw_qual == 0xFF:
            qual_valid = False
            qual_status = SignalStatus.NOT_AVAILABLE
        elif raw_qual == 0xFE:
            qual_valid = False
            qual_status = SignalStatus.ERROR

        signals["detroit_def_quality_status"] = DecodedSignal(
            name="detroit_def_quality_status",
            value=self.DEF_QUALITY_MAP.get(raw_qual, f"Quality (0x{raw_qual:02X})"),
            unit="enum",
            raw_value=raw_qual,
            is_valid=qual_valid,
            status=qual_status,
        )

        return OemDecodedPayload(
            manufacturer=self.NAME,
            pgn=self.PGN_AFTERTREATMENT_ACM,
            signals=signals,
            timestamp_ns=ts,
            arbitration_id=arb_id,
            source_address=sa,
            destination_address=None,
            is_broadcast=True,
            raw_data=data,
        )

    def _decode_jake_brake_retarder(
        self,
        data: bytes,
        arb_id: int,
        sa: int,
        ts: int,
    ) -> OemDecodedPayload | None:
        """Decode PGN 65375 (0xFF5F) Detroit Diesel Jake Brake & Secondary Retarder."""
        if len(data) < 4:
            return None

        signals: dict[str, DecodedSignal] = {}

        # Byte 0: Detroit Jake Brake Stage (uint8)
        raw_stage = data[0]
        stage_valid = True
        stage_status = SignalStatus.VALID
        if raw_stage == 0xFF:
            stage_valid = False
            stage_status = SignalStatus.NOT_AVAILABLE
        elif raw_stage == 0xFE:
            stage_valid = False
            stage_status = SignalStatus.ERROR

        signals["detroit_jake_brake_stage"] = DecodedSignal(
            name="detroit_jake_brake_stage",
            value=self.JAKE_STAGE_MAP.get(raw_stage, f"Stage ({raw_stage})"),
            unit="enum",
            raw_value=raw_stage,
            is_valid=stage_valid,
            status=stage_status,
        )

        # Byte 1: Detroit Voith Secondary Water Retarder (uint8, 0.4 %, 0.0 offset)
        raw_ret = data[1]
        if raw_ret == 0xFF:
            signals["detroit_voith_secondary_water_retarder"] = DecodedSignal(
                name="detroit_voith_secondary_water_retarder",
                value=0.0,
                unit="%",
                raw_value=raw_ret,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_ret == 0xFE:
            signals["detroit_voith_secondary_water_retarder"] = DecodedSignal(
                name="detroit_voith_secondary_water_retarder",
                value=0.0,
                unit="%",
                raw_value=raw_ret,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["detroit_voith_secondary_water_retarder"] = DecodedSignal(
                name="detroit_voith_secondary_water_retarder",
                value=round(min(100.0, raw_ret * 0.4), 1),
                unit="%",
                raw_value=raw_ret,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 2..3: Detroit Engine Retardation Power (uint16 LE, 0.1 kW, 0.0 offset)
        raw_power = data[2] | (data[3] << 8)
        if raw_power == 0xFFFF:
            signals["detroit_engine_retardation_power"] = DecodedSignal(
                name="detroit_engine_retardation_power",
                value=0.0,
                unit="kW",
                raw_value=raw_power,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_power == 0xFFFE:
            signals["detroit_engine_retardation_power"] = DecodedSignal(
                name="detroit_engine_retardation_power",
                value=0.0,
                unit="kW",
                raw_value=raw_power,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["detroit_engine_retardation_power"] = DecodedSignal(
                name="detroit_engine_retardation_power",
                value=round(raw_power * 0.1, 1),
                unit="kW",
                raw_value=raw_power,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Bytes 4..7: Reserved
        if len(data) >= 8:
            signals["detroit_retarder_safety_status"] = DecodedSignal(
                name="detroit_retarder_safety_status",
                value=data[4:8].hex(),
                unit="hex",
                raw_value=int.from_bytes(data[4:8], "little"),
                is_valid=True,
                status=SignalStatus.VALID,
            )

        return OemDecodedPayload(
            manufacturer=self.NAME,
            pgn=self.PGN_JAKE_BRAKE_RETARDER,
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
        """Decode PGN 65380 (0xFF64) Detroit MCM Injector Quantity Balancing."""
        if len(data) < 8:
            return None

        signals: dict[str, DecodedSignal] = {}

        # Bytes 0..5: Cylinder 1..6 Fuel Offset Trim (uint8, 0.05 mg/stroke, -6.4 offset)
        for cyl_idx in range(1, 7):
            raw_val = data[cyl_idx - 1]
            sig_name = f"detroit_cyl_{cyl_idx}_fuel_offset_trim"
            if raw_val == 0xFF:
                signals[sig_name] = DecodedSignal(
                    name=sig_name,
                    value=0.0,
                    unit="mg/stroke",
                    raw_value=raw_val,
                    is_valid=False,
                    status=SignalStatus.NOT_AVAILABLE,
                )
            elif raw_val == 0xFE:
                signals[sig_name] = DecodedSignal(
                    name=sig_name,
                    value=0.0,
                    unit="mg/stroke",
                    raw_value=raw_val,
                    is_valid=False,
                    status=SignalStatus.ERROR,
                )
            else:
                phys_val = round(raw_val * 0.05 - 6.4, 3)
                signals[sig_name] = DecodedSignal(
                    name=sig_name,
                    value=phys_val,
                    unit="mg/stroke",
                    raw_value=raw_val,
                    is_valid=True,
                    status=SignalStatus.VALID,
                )

        # Byte 6..7: Detroit Amplified Rail Pressure (APCRS) (uint16 LE, 0.1 MPa, 0.0 offset)
        raw_hp = data[6] | (data[7] << 8)
        if raw_hp == 0xFFFF:
            signals["detroit_amplified_rail_pressure"] = DecodedSignal(
                name="detroit_amplified_rail_pressure",
                value=0.0,
                unit="MPa",
                raw_value=raw_hp,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_hp == 0xFFFE:
            signals["detroit_amplified_rail_pressure"] = DecodedSignal(
                name="detroit_amplified_rail_pressure",
                value=0.0,
                unit="MPa",
                raw_value=raw_hp,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["detroit_amplified_rail_pressure"] = DecodedSignal(
                name="detroit_amplified_rail_pressure",
                value=round(raw_hp * 0.1, 1),
                unit="MPa",
                raw_value=raw_hp,
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
        """Decode PGN 61184 (0xEF00) Detroit Proprietary Service Commands."""
        if len(data) < 2:
            return None

        cmd_id = data[0]
        dd_cmds = {
            0x03: "Detroit MCM Compression Test Routine",
            0x07: "Detroit ACM DPF Service Regeneration Trigger",
            0x0B: "Detroit SCR Dosing Valve Override",
        }

        # M-24 (P2-8): positive disambiguation — unknown command ids are
        # never claimed (see Cummins for the full rationale).
        if cmd_id not in dd_cmds:
            return None

        cmd_name = dd_cmds[cmd_id]
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
