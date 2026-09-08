"""Volvo Trucks & Renault Architecture J1939 Proprietary Decoder.

Target Systems: D11, D13 (D13K Euro 6), D16 Engines.
Controllers: EMS 2.2 / 2.3 / 2.4, ACM 2.1 / 2.2, V-MAC, VEB+ Braking.
Complies with SPEC-DIAG-J1939-V1.0 Section 6.4.
"""

from __future__ import annotations

from src.core.models.can_frame import CanFrame
from src.engine.decoder.dbc_decoder import DecodedSignal, SignalStatus
from src.protocols.j1939.oem.registry import BaseOemDecoder, OemDecodedPayload


class VolvoDecoder(BaseOemDecoder):
    """Proprietary J1939 decoder for Volvo Trucks and Renault commercial vehicles."""

    NAME: str = "Volvo"

    # Supported PGNs
    PGN_AFTERTREATMENT_ACM: int = 65350  # 0xFF46 - Proprietary B
    PGN_VEB_RETARDER: int = 65352  # 0xFF48 - Proprietary B
    PGN_CYLINDER_BALANCING: int = 65355  # 0xFF4B - Proprietary B
    PGN_PROPRIETARY_A: int = 61184  # 0xEF00 - Service Routine / Unicast

    REGEN_STATE_MAP: dict[int, str] = {
        0: "Inactive",
        1: "Service Regeneration",
        2: "Active In-drive",
        3: "Inhibited",
    }

    INHIBIT_STATE_MAP: dict[int, str] = {
        0: "Normal",
        1: "Inhibit Demanded by Driver",
        2: "Error",
        3: "Not Available",
    }

    HIGH_EXHAUST_TEMP_MAP: dict[int, str] = {
        0: "Normal / Off",
        1: "Warning Level 1",
        2: "Warning Level 2 Critical",
    }

    VEB_STAGE_MAP: dict[int, str] = {
        0: "Off",
        1: "Low (40% Compression)",
        2: "Med (70%)",
        3: "High (100% VEB+ Compression Brake)",
        4: "Brake Blending Active",
    }

    @property
    def name(self) -> str:
        return self.NAME

    @property
    def supported_pgns(self) -> set[int]:
        return {
            self.PGN_AFTERTREATMENT_ACM,
            self.PGN_VEB_RETARDER,
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
        """Decode Volvo frame into structured physical signals."""
        data = frame.data
        ts = frame.timestamp_ns
        arb_id = frame.arbitration_id

        if pgn == self.PGN_AFTERTREATMENT_ACM:
            return self._decode_aftertreatment_acm(data, arb_id, sa, ts)
        elif pgn == self.PGN_VEB_RETARDER:
            return self._decode_veb_retarder(data, arb_id, sa, ts)
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
        """Decode PGN 65350 (0xFF46) Volvo Aftertreatment ACM Telemetry & DPF Status."""
        if len(data) < 8:
            return None

        signals: dict[str, DecodedSignal] = {}

        # Byte 0..1: Volvo DPF Soot Accumulation Level (uint16 LE, 0.1 g, 0.0 offset)
        raw_soot = data[0] | (data[1] << 8)
        if raw_soot == 0xFFFF:
            signals["volvo_dpf_soot_accumulation_level"] = DecodedSignal(
                name="volvo_dpf_soot_accumulation_level",
                value=0.0,
                unit="g",
                raw_value=raw_soot,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_soot == 0xFFFE:
            signals["volvo_dpf_soot_accumulation_level"] = DecodedSignal(
                name="volvo_dpf_soot_accumulation_level",
                value=0.0,
                unit="g",
                raw_value=raw_soot,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["volvo_dpf_soot_accumulation_level"] = DecodedSignal(
                name="volvo_dpf_soot_accumulation_level",
                value=round(raw_soot * 0.1, 1),
                unit="g",
                raw_value=raw_soot,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 2 (bits 0..1): DPF Regeneration Active State
        byte2 = data[2]
        raw_regen = byte2 & 0x03
        signals["dpf_regeneration_active_state"] = DecodedSignal(
            name="dpf_regeneration_active_state",
            value=self.REGEN_STATE_MAP.get(raw_regen, f"State ({raw_regen})"),
            unit="enum",
            raw_value=raw_regen,
            is_valid=True,
            status=SignalStatus.VALID,
        )

        # Byte 2 (bits 2..3): DPF Regeneration Inhibit Switch State
        raw_inhibit = (byte2 >> 2) & 0x03
        inhibit_valid = True
        inhibit_status = SignalStatus.VALID
        if raw_inhibit == 2:
            inhibit_valid = False
            inhibit_status = SignalStatus.ERROR
        elif raw_inhibit == 3:
            inhibit_valid = False
            inhibit_status = SignalStatus.NOT_AVAILABLE

        signals["dpf_regeneration_inhibit_switch_state"] = DecodedSignal(
            name="dpf_regeneration_inhibit_switch_state",
            value=self.INHIBIT_STATE_MAP.get(raw_inhibit, f"State ({raw_inhibit})"),
            unit="enum",
            raw_value=raw_inhibit,
            is_valid=inhibit_valid,
            status=inhibit_status,
        )

        # Byte 2 (bits 4..7): High Exhaust Temperature Warning Flag
        raw_warn = (byte2 >> 4) & 0x0F
        warn_valid = True
        warn_status = SignalStatus.VALID
        if raw_warn == 14:
            warn_valid = False
            warn_status = SignalStatus.ERROR
        elif raw_warn == 15:
            warn_valid = False
            warn_status = SignalStatus.NOT_AVAILABLE

        signals["high_exhaust_temperature_warning_flag"] = DecodedSignal(
            name="high_exhaust_temperature_warning_flag",
            value=self.HIGH_EXHAUST_TEMP_MAP.get(raw_warn, f"Warning ({raw_warn})"),
            unit="enum",
            raw_value=raw_warn,
            is_valid=warn_valid,
            status=warn_status,
        )

        # Byte 3..4: Volvo AdBlue Dosing Mass Flow Rate (uint16 LE, 0.05 g/s, 0.0 offset)
        raw_dosing = data[3] | (data[4] << 8)
        if raw_dosing == 0xFFFF:
            signals["volvo_adblue_dosing_mass_flow_rate"] = DecodedSignal(
                name="volvo_adblue_dosing_mass_flow_rate",
                value=0.0,
                unit="g/s",
                raw_value=raw_dosing,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_dosing == 0xFFFE:
            signals["volvo_adblue_dosing_mass_flow_rate"] = DecodedSignal(
                name="volvo_adblue_dosing_mass_flow_rate",
                value=0.0,
                unit="g/s",
                raw_value=raw_dosing,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["volvo_adblue_dosing_mass_flow_rate"] = DecodedSignal(
                name="volvo_adblue_dosing_mass_flow_rate",
                value=round(raw_dosing * 0.05, 2),
                unit="g/s",
                raw_value=raw_dosing,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 5: Volvo AdBlue Tank Level (uint8, 0.4 %, 0.0 offset)
        raw_level = data[5]
        if raw_level == 0xFF:
            signals["volvo_adblue_tank_level"] = DecodedSignal(
                name="volvo_adblue_tank_level",
                value=0.0,
                unit="%",
                raw_value=raw_level,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_level == 0xFE:
            signals["volvo_adblue_tank_level"] = DecodedSignal(
                name="volvo_adblue_tank_level",
                value=0.0,
                unit="%",
                raw_value=raw_level,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["volvo_adblue_tank_level"] = DecodedSignal(
                name="volvo_adblue_tank_level",
                value=round(min(100.0, raw_level * 0.4), 1),
                unit="%",
                raw_value=raw_level,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 6: Volvo AdBlue Concentration Quality (uint8, 0.2 %, 0.0 offset)
        raw_quality = data[6]
        if raw_quality == 0xFF:
            signals["volvo_adblue_concentration_quality"] = DecodedSignal(
                name="volvo_adblue_concentration_quality",
                value=0.0,
                unit="%",
                raw_value=raw_quality,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_quality == 0xFE:
            signals["volvo_adblue_concentration_quality"] = DecodedSignal(
                name="volvo_adblue_concentration_quality",
                value=0.0,
                unit="%",
                raw_value=raw_quality,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["volvo_adblue_concentration_quality"] = DecodedSignal(
                name="volvo_adblue_concentration_quality",
                value=round(raw_quality * 0.2, 1),
                unit="%",
                raw_value=raw_quality,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 7: Volvo ACM Subsystem Health Status
        signals["volvo_acm_health_status"] = DecodedSignal(
            name="volvo_acm_health_status",
            value=data[7],
            unit="raw",
            raw_value=data[7],
            is_valid=True,
            status=SignalStatus.VALID,
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

    def _decode_veb_retarder(
        self,
        data: bytes,
        arb_id: int,
        sa: int,
        ts: int,
    ) -> OemDecodedPayload | None:
        """Decode PGN 65352 (0xFF48) Volvo VEB+ Engine Brake & Retarder Control."""
        if len(data) < 4:
            return None

        signals: dict[str, DecodedSignal] = {}

        # Byte 0: Volvo VEB+ Engine Brake Stage (uint8)
        raw_stage = data[0]
        stage_valid = True
        stage_status = SignalStatus.VALID
        if raw_stage == 0xFF:
            stage_valid = False
            stage_status = SignalStatus.NOT_AVAILABLE
        elif raw_stage == 0xFE:
            stage_valid = False
            stage_status = SignalStatus.ERROR

        signals["volvo_veb_engine_brake_stage"] = DecodedSignal(
            name="volvo_veb_engine_brake_stage",
            value=self.VEB_STAGE_MAP.get(raw_stage, f"Stage ({raw_stage})"),
            unit="enum",
            raw_value=raw_stage,
            is_valid=stage_valid,
            status=stage_status,
        )

        # Byte 1: Volvo Retarder Torque Demand (uint8, 0.4 %, 0.0 offset)
        raw_demand = data[1]
        if raw_demand == 0xFF:
            signals["volvo_retarder_torque_demand"] = DecodedSignal(
                name="volvo_retarder_torque_demand",
                value=0.0,
                unit="%",
                raw_value=raw_demand,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_demand == 0xFE:
            signals["volvo_retarder_torque_demand"] = DecodedSignal(
                name="volvo_retarder_torque_demand",
                value=0.0,
                unit="%",
                raw_value=raw_demand,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["volvo_retarder_torque_demand"] = DecodedSignal(
                name="volvo_retarder_torque_demand",
                value=round(min(100.0, raw_demand * 0.4), 1),
                unit="%",
                raw_value=raw_demand,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 2..3: Volvo Retarder Delivered Braking Torque (uint16 LE, 0.5 Nm, -1000.0 offset)
        raw_torque = data[2] | (data[3] << 8)
        if raw_torque == 0xFFFF:
            signals["volvo_retarder_delivered_braking_torque"] = DecodedSignal(
                name="volvo_retarder_delivered_braking_torque",
                value=0.0,
                unit="Nm",
                raw_value=raw_torque,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_torque == 0xFFFE:
            signals["volvo_retarder_delivered_braking_torque"] = DecodedSignal(
                name="volvo_retarder_delivered_braking_torque",
                value=0.0,
                unit="Nm",
                raw_value=raw_torque,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["volvo_retarder_delivered_braking_torque"] = DecodedSignal(
                name="volvo_retarder_delivered_braking_torque",
                value=round(raw_torque * 0.5 - 1000.0, 1),
                unit="Nm",
                raw_value=raw_torque,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Bytes 4..7: Interlocks / Flags
        if len(data) >= 8:
            signals["volvo_retarder_interlocks"] = DecodedSignal(
                name="volvo_retarder_interlocks",
                value=data[4:8].hex(),
                unit="hex",
                raw_value=int.from_bytes(data[4:8], "little"),
                is_valid=True,
                status=SignalStatus.VALID,
            )

        return OemDecodedPayload(
            manufacturer=self.NAME,
            pgn=self.PGN_VEB_RETARDER,
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
        """Decode PGN 65355 (0xFF4B) Volvo Cylinder Balancing / Adaptive Trimming."""
        if len(data) < 8:
            return None

        signals: dict[str, DecodedSignal] = {}

        # Bytes 0..5: Cylinder 1..6 Adaptive Trim Offset (uint8, 0.1 mg/stroke, -12.8 offset)
        for cyl_idx in range(1, 7):
            raw_val = data[cyl_idx - 1]
            sig_name = f"volvo_cyl_{cyl_idx}_adaptive_trim_offset"
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
                phys_val = round(raw_val * 0.1 - 12.8, 2)
                signals[sig_name] = DecodedSignal(
                    name=sig_name,
                    value=phys_val,
                    unit="mg/stroke",
                    raw_value=raw_val,
                    is_valid=True,
                    status=SignalStatus.VALID,
                )

        # Byte 6..7: Volvo Common Rail System Pressure (Actual) (uint16 LE, 0.1 MPa, 0.0 offset)
        raw_hp = data[6] | (data[7] << 8)
        if raw_hp == 0xFFFF:
            signals["volvo_common_rail_pressure_actual"] = DecodedSignal(
                name="volvo_common_rail_pressure_actual",
                value=0.0,
                unit="MPa",
                raw_value=raw_hp,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_hp == 0xFFFE:
            signals["volvo_common_rail_pressure_actual"] = DecodedSignal(
                name="volvo_common_rail_pressure_actual",
                value=0.0,
                unit="MPa",
                raw_value=raw_hp,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["volvo_common_rail_pressure_actual"] = DecodedSignal(
                name="volvo_common_rail_pressure_actual",
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
        """Decode PGN 61184 (0xEF00) Volvo Proprietary Service Commands."""
        if len(data) < 2:
            return None

        cmd_id = data[0]
        volvo_cmds = {
            0x01: "Volvo EMS Diagnostic Mode Active",
            0x05: "Volvo DPF Stationary Regeneration Request",
            0x09: "Volvo Injector Calibration Reset",
        }

        # M-24 (P2-8): positive disambiguation — unknown command ids are
        # never claimed (see Cummins for the full rationale).
        if cmd_id not in volvo_cmds:
            return None

        cmd_name = volvo_cmds[cmd_id]
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
