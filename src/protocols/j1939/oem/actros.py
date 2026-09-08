"""Mercedes-Benz Actros (OM470 / OM471 / OM473) J1939 Proprietary Decoder.

Target Systems: OM470, OM471, OM473 In-line 6 Engines (Euro 6 BlueTec 6).
Controllers: MCM (Motor Control Module), ACM (Aftertreatment Control Module), CPC.
Complies with SPEC-DIAG-J1939-V1.0 Section 6.6.
"""

from __future__ import annotations

from src.core.models.can_frame import CanFrame
from src.engine.decoder.dbc_decoder import DecodedSignal, SignalStatus
from src.protocols.j1939.oem.registry import BaseOemDecoder, OemDecodedPayload


class ActrosDecoder(BaseOemDecoder):
    """Proprietary J1939 decoder for Mercedes-Benz Actros commercial vehicles."""

    NAME: str = "Mercedes-Benz"

    # Supported PGNs
    PGN_BLUETEC_AFTERTREATMENT: int = 65450  # 0xFFAA - Proprietary B
    PGN_HPEB_RETARDER: int = 65455  # 0xFFAF - Proprietary B
    PGN_LAUFRUHEREGELUNG: int = 65460  # 0xFFB4 - Proprietary B
    PGN_PROPRIETARY_A: int = 61184  # 0xEF00 - Service Routine / Unicast

    REGEN_MODE_MAP: dict[int, str] = {
        0x00: "Inaktiv (Passive)",
        0x01: "Regeneration Fahren (Highway)",
        0x02: "Regeneration Stand (Parked)",
        0x03: "Gesperrt (Inhibit Switch Active)",
        0x04: "Stoerung (Fault)",
    }

    HPEB_STAGE_MAP: dict[int, str] = {
        0: "Aus",
        1: "Stufe 1 (Dekompression 30%)",
        2: "Stufe 2 (HPEB 60%)",
        3: "Stufe 3 (Volllast Dauerbremse 100%)",
    }

    @property
    def name(self) -> str:
        return self.NAME

    @property
    def supported_pgns(self) -> set[int]:
        return {
            self.PGN_BLUETEC_AFTERTREATMENT,
            self.PGN_HPEB_RETARDER,
            self.PGN_LAUFRUHEREGELUNG,
            self.PGN_PROPRIETARY_A,
        }

    def decode(
        self,
        frame: CanFrame,
        pgn: int,
        sa: int,
        da: int | None = None,
    ) -> OemDecodedPayload | None:
        """Decode Mercedes Actros frame into structured physical signals."""
        data = frame.data
        ts = frame.timestamp_ns
        arb_id = frame.arbitration_id

        if pgn == self.PGN_BLUETEC_AFTERTREATMENT:
            return self._decode_bluetec_aftertreatment(data, arb_id, sa, ts)
        elif pgn == self.PGN_HPEB_RETARDER:
            return self._decode_hpeb_retarder(data, arb_id, sa, ts)
        elif pgn == self.PGN_LAUFRUHEREGELUNG:
            return self._decode_laufruheregelung(data, arb_id, sa, ts)
        elif pgn == self.PGN_PROPRIETARY_A:
            return self._decode_proprietary_a(data, arb_id, sa, da, ts)

        return None

    def _decode_bluetec_aftertreatment(
        self,
        data: bytes,
        arb_id: int,
        sa: int,
        ts: int,
    ) -> OemDecodedPayload | None:
        """Decode PGN 65450 (0xFFAA) Mercedes BlueTec 6 Aftertreatment & DPF Control."""
        if len(data) < 8:
            return None

        signals: dict[str, DecodedSignal] = {}

        # Byte 0..1: Mercedes DPF Soot Load Index (uint16 LE, 0.1 %, 0.0 offset)
        raw_soot = data[0] | (data[1] << 8)
        if raw_soot == 0xFFFF:
            signals["mercedes_dpf_soot_load_index"] = DecodedSignal(
                name="mercedes_dpf_soot_load_index",
                value=0.0,
                unit="%",
                raw_value=raw_soot,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_soot == 0xFFFE:
            signals["mercedes_dpf_soot_load_index"] = DecodedSignal(
                name="mercedes_dpf_soot_load_index",
                value=0.0,
                unit="%",
                raw_value=raw_soot,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["mercedes_dpf_soot_load_index"] = DecodedSignal(
                name="mercedes_dpf_soot_load_index",
                value=round(raw_soot * 0.1, 1),
                unit="%",
                raw_value=raw_soot,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 2: Mercedes BlueTec Regeneration Mode (uint8)
        raw_mode = data[2]
        mode_valid = True
        mode_status = SignalStatus.VALID
        if raw_mode == 0xFF:
            mode_valid = False
            mode_status = SignalStatus.NOT_AVAILABLE
        elif raw_mode == 0xFE:
            mode_valid = False
            mode_status = SignalStatus.ERROR

        signals["mercedes_bluetec_regeneration_mode"] = DecodedSignal(
            name="mercedes_bluetec_regeneration_mode",
            value=self.REGEN_MODE_MAP.get(raw_mode, f"Modus (0x{raw_mode:02X})"),
            unit="enum",
            raw_value=raw_mode,
            is_valid=mode_valid,
            status=mode_status,
        )

        # Byte 3..4: Mercedes AdBlue Dosierrate Istwert (uint16 LE, 0.01 g/s, 0.0 offset)
        raw_dosing = data[3] | (data[4] << 8)
        if raw_dosing == 0xFFFF:
            signals["mercedes_adblue_dosierrate_istwert"] = DecodedSignal(
                name="mercedes_adblue_dosierrate_istwert",
                value=0.0,
                unit="g/s",
                raw_value=raw_dosing,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_dosing == 0xFFFE:
            signals["mercedes_adblue_dosierrate_istwert"] = DecodedSignal(
                name="mercedes_adblue_dosierrate_istwert",
                value=0.0,
                unit="g/s",
                raw_value=raw_dosing,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["mercedes_adblue_dosierrate_istwert"] = DecodedSignal(
                name="mercedes_adblue_dosierrate_istwert",
                value=round(raw_dosing * 0.01, 2),
                unit="g/s",
                raw_value=raw_dosing,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 5: Mercedes AdBlue Fuellstand Kombi (uint8, 0.4 %, 0.0 offset)
        raw_level = data[5]
        if raw_level == 0xFF:
            signals["mercedes_adblue_fuellstand_kombi"] = DecodedSignal(
                name="mercedes_adblue_fuellstand_kombi",
                value=0.0,
                unit="%",
                raw_value=raw_level,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_level == 0xFE:
            signals["mercedes_adblue_fuellstand_kombi"] = DecodedSignal(
                name="mercedes_adblue_fuellstand_kombi",
                value=0.0,
                unit="%",
                raw_value=raw_level,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["mercedes_adblue_fuellstand_kombi"] = DecodedSignal(
                name="mercedes_adblue_fuellstand_kombi",
                value=round(min(100.0, raw_level * 0.4), 1),
                unit="%",
                raw_value=raw_level,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 6: Mercedes AdBlue Qualitaet / Konzentration (uint8, 0.1 %, 0.0 offset)
        raw_qual = data[6]
        if raw_qual == 0xFF:
            signals["mercedes_adblue_qualitaet_konzentration"] = DecodedSignal(
                name="mercedes_adblue_qualitaet_konzentration",
                value=0.0,
                unit="%",
                raw_value=raw_qual,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_qual == 0xFE:
            signals["mercedes_adblue_qualitaet_konzentration"] = DecodedSignal(
                name="mercedes_adblue_qualitaet_konzentration",
                value=0.0,
                unit="%",
                raw_value=raw_qual,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["mercedes_adblue_qualitaet_konzentration"] = DecodedSignal(
                name="mercedes_adblue_qualitaet_konzentration",
                value=round(raw_qual * 0.1, 1),
                unit="%",
                raw_value=raw_qual,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 7: Mercedes SCR Katalysator Wirkungsgrad (uint8, 0.4 %, 0.0 offset)
        raw_eff = data[7]
        if raw_eff == 0xFF:
            signals["mercedes_scr_katalysator_wirkungsgrad"] = DecodedSignal(
                name="mercedes_scr_katalysator_wirkungsgrad",
                value=0.0,
                unit="%",
                raw_value=raw_eff,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_eff == 0xFE:
            signals["mercedes_scr_katalysator_wirkungsgrad"] = DecodedSignal(
                name="mercedes_scr_katalysator_wirkungsgrad",
                value=0.0,
                unit="%",
                raw_value=raw_eff,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["mercedes_scr_katalysator_wirkungsgrad"] = DecodedSignal(
                name="mercedes_scr_katalysator_wirkungsgrad",
                value=round(min(100.0, raw_eff * 0.4), 1),
                unit="%",
                raw_value=raw_eff,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        return OemDecodedPayload(
            manufacturer=self.NAME,
            pgn=self.PGN_BLUETEC_AFTERTREATMENT,
            signals=signals,
            timestamp_ns=ts,
            arbitration_id=arb_id,
            source_address=sa,
            destination_address=None,
            is_broadcast=True,
            raw_data=data,
        )

    def _decode_hpeb_retarder(
        self,
        data: bytes,
        arb_id: int,
        sa: int,
        ts: int,
    ) -> OemDecodedPayload | None:
        """Decode PGN 65455 (0xFFAF) Mercedes High Performance Engine Brake (HPEB) & Retarder."""
        if len(data) < 4:
            return None

        signals: dict[str, DecodedSignal] = {}

        # Byte 0: Mercedes HPEB Motorbremse Stufe (uint8)
        raw_stage = data[0]
        stage_valid = True
        stage_status = SignalStatus.VALID
        if raw_stage == 0xFF:
            stage_valid = False
            stage_status = SignalStatus.NOT_AVAILABLE
        elif raw_stage == 0xFE:
            stage_valid = False
            stage_status = SignalStatus.ERROR

        signals["mercedes_hpeb_motorbremse_stufe"] = DecodedSignal(
            name="mercedes_hpeb_motorbremse_stufe",
            value=self.HPEB_STAGE_MAP.get(raw_stage, f"Stufe ({raw_stage})"),
            unit="enum",
            raw_value=raw_stage,
            is_valid=stage_valid,
            status=stage_status,
        )

        # Byte 1: Mercedes Retarder Bremsmomentanforderung (uint8, 0.4 %, 0.0 offset)
        raw_demand = data[1]
        if raw_demand == 0xFF:
            signals["mercedes_retarder_bremsmomentanforderung"] = DecodedSignal(
                name="mercedes_retarder_bremsmomentanforderung",
                value=0.0,
                unit="%",
                raw_value=raw_demand,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_demand == 0xFE:
            signals["mercedes_retarder_bremsmomentanforderung"] = DecodedSignal(
                name="mercedes_retarder_bremsmomentanforderung",
                value=0.0,
                unit="%",
                raw_value=raw_demand,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["mercedes_retarder_bremsmomentanforderung"] = DecodedSignal(
                name="mercedes_retarder_bremsmomentanforderung",
                value=round(min(100.0, raw_demand * 0.4), 1),
                unit="%",
                raw_value=raw_demand,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 2..3: Mercedes Retarder Kuehlmitteltemperatur (uint16 LE, 0.03125 °C, -273.0 offset)
        raw_temp = data[2] | (data[3] << 8)
        if raw_temp == 0xFFFF:
            signals["mercedes_retarder_kuehlmitteltemperatur"] = DecodedSignal(
                name="mercedes_retarder_kuehlmitteltemperatur",
                value=0.0,
                unit="°C",
                raw_value=raw_temp,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_temp == 0xFFFE:
            signals["mercedes_retarder_kuehlmitteltemperatur"] = DecodedSignal(
                name="mercedes_retarder_kuehlmitteltemperatur",
                value=0.0,
                unit="°C",
                raw_value=raw_temp,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["mercedes_retarder_kuehlmitteltemperatur"] = DecodedSignal(
                name="mercedes_retarder_kuehlmitteltemperatur",
                value=round(raw_temp * 0.03125 - 273.0, 2),
                unit="°C",
                raw_value=raw_temp,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        # Byte 4..7: Mercedes Dauerbremse Statuswort
        if len(data) >= 8:
            signals["mercedes_dauerbremse_statuswort"] = DecodedSignal(
                name="mercedes_dauerbremse_statuswort",
                value=data[4:8].hex(),
                unit="hex",
                raw_value=int.from_bytes(data[4:8], "little"),
                is_valid=True,
                status=SignalStatus.VALID,
            )

        return OemDecodedPayload(
            manufacturer=self.NAME,
            pgn=self.PGN_HPEB_RETARDER,
            signals=signals,
            timestamp_ns=ts,
            arbitration_id=arb_id,
            source_address=sa,
            destination_address=None,
            is_broadcast=True,
            raw_data=data,
        )

    def _decode_laufruheregelung(
        self,
        data: bytes,
        arb_id: int,
        sa: int,
        ts: int,
    ) -> OemDecodedPayload | None:
        """Decode PGN 65460 (0xFFB4) Mercedes Laufruheregelung / Zylinder-Ausgleich (MCM)."""
        if len(data) < 8:
            return None

        signals: dict[str, DecodedSignal] = {}

        # Byte 0..5: Zylinder 1..6 Mengenkorrektur (uint8, 0.1 mm³/Hub, -12.8 offset)
        for cyl_idx in range(1, 7):
            raw_val = data[cyl_idx - 1]
            sig_name = f"zylinder_{cyl_idx}_mengenkorrektur"
            if raw_val == 0xFF:
                signals[sig_name] = DecodedSignal(
                    name=sig_name,
                    value=0.0,
                    unit="mm³/Hub",
                    raw_value=raw_val,
                    is_valid=False,
                    status=SignalStatus.NOT_AVAILABLE,
                )
            elif raw_val == 0xFE:
                signals[sig_name] = DecodedSignal(
                    name=sig_name,
                    value=0.0,
                    unit="mm³/Hub",
                    raw_value=raw_val,
                    is_valid=False,
                    status=SignalStatus.ERROR,
                )
            else:
                phys_val = round(raw_val * 0.1 - 12.8, 2)
                signals[sig_name] = DecodedSignal(
                    name=sig_name,
                    value=phys_val,
                    unit="mm³/Hub",
                    raw_value=raw_val,
                    is_valid=True,
                    status=SignalStatus.VALID,
                )

        # Byte 6..7: Common-Rail Raildruck Istwert (uint16 LE, 0.1 bar, 0.0 offset)
        raw_hp = data[6] | (data[7] << 8)
        if raw_hp == 0xFFFF:
            signals["common_rail_raildruck_istwert"] = DecodedSignal(
                name="common_rail_raildruck_istwert",
                value=0.0,
                unit="bar",
                raw_value=raw_hp,
                is_valid=False,
                status=SignalStatus.NOT_AVAILABLE,
            )
        elif raw_hp == 0xFFFE:
            signals["common_rail_raildruck_istwert"] = DecodedSignal(
                name="common_rail_raildruck_istwert",
                value=0.0,
                unit="bar",
                raw_value=raw_hp,
                is_valid=False,
                status=SignalStatus.ERROR,
            )
        else:
            signals["common_rail_raildruck_istwert"] = DecodedSignal(
                name="common_rail_raildruck_istwert",
                value=round(raw_hp * 0.1, 1),
                unit="bar",
                raw_value=raw_hp,
                is_valid=True,
                status=SignalStatus.VALID,
            )

        return OemDecodedPayload(
            manufacturer=self.NAME,
            pgn=self.PGN_LAUFRUHEREGELUNG,
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
        """Decode PGN 61184 (0xEF00) Mercedes Proprietary Service Commands."""
        if len(data) < 2:
            return None

        cmd_id = data[0]
        mb_cmds = {
            0x21: "Mercedes Star Diagnosis Routine Start",
            0x24: "Mercedes DPF Service Regeneration Trigger",
            0x28: "Mercedes Cylinder Cutoff Test",
        }

        # M-24 (P2-8): positive disambiguation — unknown command ids are
        # never claimed (see Cummins for the full rationale).
        if cmd_id not in mb_cmds:
            return None

        cmd_name = mb_cmds[cmd_id]
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
