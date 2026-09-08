"""Scania Truck & Bus Architecture J1939 Proprietary Decoder.

Target Systems: DC09 (5-Cyl), DC13 (6-Cyl In-line), DC16 (V8 500-770 hp) Engines.
Controllers: EMS S6, EMS S7, EMS S8, EEC3 / SCR Aftertreatment, RET Retarder (R3500/R4100).
Complies with SPEC-DIAG-J1939-V1.0 Section 6.3.
"""

from __future__ import annotations

from src.core.models.can_frame import CanFrame
from src.engine.decoder.dbc_decoder import DecodedSignal, SignalStatus
from src.protocols.j1939.oem.registry import BaseOemDecoder, OemDecodedPayload


class ScaniaDecoder(BaseOemDecoder):
    """Proprietary J1939 decoder for Scania truck and bus engine/retarder controllers."""

    NAME: str = "Scania"

    # Supported PGNs
    PGN_AFTERTREATMENT_DPF: int = 65400  # 0xFF78 - Proprietary B
    PGN_RETARDER_TELEMETRY: int = 65410  # 0xFF82 - Proprietary B
    PGN_CYLINDER_BALANCING: int = 65420  # 0xFF8C - Proprietary B
    PGN_PROPRIETARY_A: int = 61184  # 0xEF00 - Service Routine / Unicast

    REGEN_STATE_MAP: dict[int, str] = {
        0x00: "Not Required",
        0x01: "Automatic Highway Running",
        0x02: "Parked Regeneration Required",
        0x03: "Parked Regeneration Running",
        0x04: "Inhibited by Driver Switch",
        0x05: "Aborted High Temp",
        0x06: "System Fault",
    }

    RETARDER_STAGE_MAP: dict[int, str] = {
        0: "Off",
        1: "Stage 1 (20%)",
        2: "Stage 2 (40%)",
        3: "Stage 3 (60%)",
        4: "Stage 4 (80%)",
        5: "Stage 5 (100%)",
        6: "Aquatarder Auto Brake Mode",
    }

    @property
    def name(self) -> str:
        return self.NAME

    @property
    def supported_pgns(self) -> set[int]:
        return {
            self.PGN_AFTERTREATMENT_DPF,
            self.PGN_RETARDER_TELEMETRY,
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
        """Decode Scania frame into structured physical signals."""
        data = frame.data
        ts = frame.timestamp_ns
        arb_id = frame.arbitration_id

        if pgn == self.PGN_AFTERTREATMENT_DPF:
            return self._decode_aftertreatment_dpf(data, arb_id, sa, ts)
        elif pgn == self.PGN_RETARDER_TELEMETRY:
            return self._decode_retarder_telemetry(data, arb_id, sa, ts)
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
        """Decode PGN 65400 (0xFF78) Scania EMS Aftertreatment & DPF Control."""
        if len(data) < 8:
            return None

        signals: dict[str, DecodedSignal] = {}

        # Byte 0..1: Scania DPF Calculated Soot Mass (uint16 LE, 0.05 g, 0.0 offset)
        raw_soot = data[0] | (data[1] << 8)
        if raw_soot == 0xFFFF:
            signals["scania_dpf_soot_mass"] = DecodedSignal(
                name="scania_dpf_soot_mass",
                value=0.0,
                unit="g",
                raw_value=raw_soot,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_soot == 0xFFFE:
            signals["scania_dpf_soot_mass"] = DecodedSignal(
                name="scania_dpf_soot_mass",
                value=0.0,
                unit="g",
                raw_value=raw_soot,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["scania_dpf_soot_mass"] = DecodedSignal(
                name="scania_dpf_soot_mass",
                value=round(raw_soot * 0.05, 2),
                unit="g",
                raw_value=raw_soot,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 2: Scania DPF Regeneration State (uint8)
        raw_state = data[2]
        state_valid = True
        state_status = SignalStatus.VALID
        if raw_state == 0xFF:
            state_valid = False
            state_status = SignalStatus.NOT_AVAILABLE
        elif raw_state == 0xFE:
            state_valid = False
            state_status = SignalStatus.ERROR

        signals["scania_dpf_regeneration_state"] = DecodedSignal(
            name="scania_dpf_regeneration_state",
            value=self.REGEN_STATE_MAP.get(raw_state, f"State (0x{raw_state:02X})"),
            unit="enum",
            raw_value=raw_state,
            is_valid=state_valid,
            status=state_status,
        )

        # Byte 3: Scania AdBlue Dosing Command (uint8, 0.1 g/min, 0.0 offset)
        raw_dosing = data[3]
        if raw_dosing == 0xFF:
            signals["scania_adblue_dosing_command"] = DecodedSignal(
                name="scania_adblue_dosing_command",
                value=0.0,
                unit="g/min",
                raw_value=raw_dosing,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_dosing == 0xFE:
            signals["scania_adblue_dosing_command"] = DecodedSignal(
                name="scania_adblue_dosing_command",
                value=0.0,
                unit="g/min",
                raw_value=raw_dosing,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["scania_adblue_dosing_command"] = DecodedSignal(
                name="scania_adblue_dosing_command",
                value=round(raw_dosing * 0.1, 1),
                unit="g/min",
                raw_value=raw_dosing,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 4: Scania AdBlue Tank Level (High Res) (uint8, 0.4 %, 0.0 offset)
        raw_level = data[4]
        if raw_level == 0xFF:
            signals["scania_adblue_tank_level"] = DecodedSignal(
                name="scania_adblue_tank_level",
                value=0.0,
                unit="%",
                raw_value=raw_level,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_level == 0xFE:
            signals["scania_adblue_tank_level"] = DecodedSignal(
                name="scania_adblue_tank_level",
                value=0.0,
                unit="%",
                raw_value=raw_level,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["scania_adblue_tank_level"] = DecodedSignal(
                name="scania_adblue_tank_level",
                value=round(min(100.0, raw_level * 0.4), 1),
                unit="%",
                raw_value=raw_level,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 5: Scania AdBlue Refractometer Quality (uint8, 0.1 %, 0.0 offset)
        raw_quality = data[5]
        if raw_quality == 0xFF:
            signals["scania_adblue_refractometer_quality"] = DecodedSignal(
                name="scania_adblue_refractometer_quality",
                value=0.0,
                unit="%",
                raw_value=raw_quality,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_quality == 0xFE:
            signals["scania_adblue_refractometer_quality"] = DecodedSignal(
                name="scania_adblue_refractometer_quality",
                value=0.0,
                unit="%",
                raw_value=raw_quality,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["scania_adblue_refractometer_quality"] = DecodedSignal(
                name="scania_adblue_refractometer_quality",
                value=round(raw_quality * 0.1, 1),
                unit="%",
                raw_value=raw_quality,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 6..7: Scania SCR Catalyst Bed Temperature (uint16 LE, 0.1 °C, -40.0 offset)
        raw_temp = data[6] | (data[7] << 8)
        if raw_temp == 0xFFFF:
            signals["scania_scr_catalyst_bed_temperature"] = DecodedSignal(
                name="scania_scr_catalyst_bed_temperature",
                value=0.0,
                unit="°C",
                raw_value=raw_temp,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_temp == 0xFFFE:
            signals["scania_scr_catalyst_bed_temperature"] = DecodedSignal(
                name="scania_scr_catalyst_bed_temperature",
                value=0.0,
                unit="°C",
                raw_value=raw_temp,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["scania_scr_catalyst_bed_temperature"] = DecodedSignal(
                name="scania_scr_catalyst_bed_temperature",
                value=round(raw_temp * 0.1 - 40.0, 1),
                unit="°C",
                raw_value=raw_temp,
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

    def _decode_retarder_telemetry(
        self,
        data: bytes,
        arb_id: int,
        sa: int,
        ts: int,
    ) -> OemDecodedPayload | None:
        """Decode PGN 65410 (0xFF82) Scania Retarder Control & Telemetry."""
        if len(data) < 5:
            return None

        signals: dict[str, DecodedSignal] = {}

        # Byte 0: Scania Retarder Lever Stage Request (uint8)
        raw_stage = data[0]
        stage_valid = True
        stage_status = SignalStatus.VALID
        if raw_stage == 0xFF:
            stage_valid = False
            stage_status = SignalStatus.NOT_AVAILABLE
        elif raw_stage == 0xFE:
            stage_valid = False
            stage_status = SignalStatus.ERROR

        signals["scania_retarder_lever_stage_request"] = DecodedSignal(
            name="scania_retarder_lever_stage_request",
            value=self.RETARDER_STAGE_MAP.get(raw_stage, f"Stage ({raw_stage})"),
            unit="enum",
            raw_value=raw_stage,
            is_valid=stage_valid,
            status=stage_status,
        )

        # Byte 1: Scania Retarder Braking Torque Demand (uint8, 0.4 %, 0.0 offset)
        raw_demand = data[1]
        if raw_demand == 0xFF:
            signals["scania_retarder_braking_torque_demand"] = DecodedSignal(
                name="scania_retarder_braking_torque_demand",
                value=0.0,
                unit="%",
                raw_value=raw_demand,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_demand == 0xFE:
            signals["scania_retarder_braking_torque_demand"] = DecodedSignal(
                name="scania_retarder_braking_torque_demand",
                value=0.0,
                unit="%",
                raw_value=raw_demand,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["scania_retarder_braking_torque_demand"] = DecodedSignal(
                name="scania_retarder_braking_torque_demand",
                value=round(min(100.0, raw_demand * 0.4), 1),
                unit="%",
                raw_value=raw_demand,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 2..3: Scania Retarder Oil Temperature (uint16 LE, 0.03125 °C, -273.0 offset)
        raw_oil_temp = data[2] | (data[3] << 8)
        if raw_oil_temp == 0xFFFF:
            signals["scania_retarder_oil_temperature"] = DecodedSignal(
                name="scania_retarder_oil_temperature",
                value=0.0,
                unit="°C",
                raw_value=raw_oil_temp,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_oil_temp == 0xFFFE:
            signals["scania_retarder_oil_temperature"] = DecodedSignal(
                name="scania_retarder_oil_temperature",
                value=0.0,
                unit="°C",
                raw_value=raw_oil_temp,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["scania_retarder_oil_temperature"] = DecodedSignal(
                name="scania_retarder_oil_temperature",
                value=round(raw_oil_temp * 0.03125 - 273.0, 2),
                unit="°C",
                raw_value=raw_oil_temp,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 4: Scania Retarder Actuator Air Pressure (uint8, 0.05 bar, 0.0 offset)
        raw_air_p = data[4]
        if raw_air_p == 0xFF:
            signals["scania_retarder_actuator_air_pressure"] = DecodedSignal(
                name="scania_retarder_actuator_air_pressure",
                value=0.0,
                unit="bar",
                raw_value=raw_air_p,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_air_p == 0xFE:
            signals["scania_retarder_actuator_air_pressure"] = DecodedSignal(
                name="scania_retarder_actuator_air_pressure",
                value=0.0,
                unit="bar",
                raw_value=raw_air_p,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["scania_retarder_actuator_air_pressure"] = DecodedSignal(
                name="scania_retarder_actuator_air_pressure",
                value=round(raw_air_p * 0.05, 2),
                unit="bar",
                raw_value=raw_air_p,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Bytes 5..7: Reserved / Safety Status
        if len(data) >= 8:
            signals["scania_retarder_safety_status"] = DecodedSignal(
                name="scania_retarder_safety_status",
                value=data[5:8].hex(),
                unit="hex",
                raw_value=int.from_bytes(data[5:8], "little"),
                is_valid=True,
                status=SignalStatus.VALID,
            )

        return OemDecodedPayload(
            manufacturer=self.NAME,
            pgn=self.PGN_RETARDER_TELEMETRY,
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
        """Decode PGN 65420 (0xFF8C) Scania Smooth Running / Cylinder Balancing (DC09/DC13/DC16)."""
        if len(data) < 8:
            return None

        signals: dict[str, DecodedSignal] = {}

        # Bytes 0..7: Cyl 1..8 Smooth Running Correction (uint8, 0.25 mm³/stroke, -32.0 offset)
        for cyl_idx in range(1, 9):
            raw_val = data[cyl_idx - 1]
            sig_name = f"scania_cyl_{cyl_idx}_smooth_running"
            if raw_val == 0xFF:
                signals[sig_name] = DecodedSignal(
                    name=sig_name,
                    value=0.0,
                    unit="mm³/stroke",
                    raw_value=raw_val,
                    is_valid=False,
                    status=SignalStatus.NOT_AVAILABLE,
                )
            elif raw_val == 0xFE:
                signals[sig_name] = DecodedSignal(
                    name=sig_name,
                    value=0.0,
                    unit="mm³/stroke",
                    raw_value=raw_val,
                    is_valid=False,
                    status=SignalStatus.ERROR,
                )
            else:
                phys_val = round(raw_val * 0.25 - 32.0, 2)
                signals[sig_name] = DecodedSignal(
                    name=sig_name,
                    value=phys_val,
                    unit="mm³/stroke",
                    raw_value=raw_val,
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
        """Decode PGN 61184 (0xEF00) Scania Proprietary Service Commands."""
        if len(data) < 2:
            return None

        cmd_id = data[0]
        # Common Scania diagnostic routine IDs
        scania_cmds = {
            0x10: "Scania EMS Calibration Check",
            0x12: "Scania DPF Forced Regeneration Request",
            0x14: "Scania Retarder Calibration Mode",
        }

        # M-24 (P2-8): positive disambiguation — unknown command ids are
        # never claimed (the old DA-based guard let engine/EMS/retarder-
        # addressed unknowns through as generic Scania services).
        if cmd_id not in scania_cmds:
            return None

        cmd_name = scania_cmds[cmd_id]
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
