"""Volvo Penta EDC (MID 128 PID/SID) & EVC Marine Diagnostic Decoder.

Complies with Volvo Penta EDC1/4/7 and EVC-A..E specifications (MASTER_PLAN.md Section 8).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import ClassVar

from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame

logger = get_logger("protocols.volvo")

MID_ENGINE_ECU: int = 128

VOLVO_PIDS: dict[int, str] = {
    100: "Engine Oil Pressure",
    105: "Intake Manifold Temperature",
    110: "Engine Coolant Temperature",
    190: "Engine Speed",
}

VOLVO_SIDS: dict[int, str] = {
    1: "Injector Cylinder 1",
    2: "Injector Cylinder 2",
    3: "Injector Cylinder 3",
    4: "Injector Cylinder 4",
    5: "Injector Cylinder 5",
    6: "Injector Cylinder 6",
    21: "Engine Position Sensor (Crankshaft)",
    22: "Timing Sensor (Camshaft)",
    254: "Engine Control Module Microcontroller",
}

VOLVO_PSIDS: dict[int, str] = {
    96: "Fuel Rail Pressure High-Pressure System",
    98: "Boost Pressure Turbocharger Actuator (VGT)",
}


@dataclass(slots=True)
class VolvoDtc:
    """Volvo Penta EDC / EVC Diagnostic Trouble Code."""

    code_type: str  # "PID" | "SID" | "PPID" | "PSID"
    code_id: int
    fmi: int
    description: str
    is_active: bool = True


# REVIEW hardening: EVC plausibility bounds (fabricated 0.0 mid-scale
# values previously masked NOT_AVAILABLE sensors).
LEVER_RANGE_PCT: tuple[float, float] = (-100.0, 100.0)
TRIM_RANGE_DEG: tuple[float, float] = (-10.0, 15.0)
RUDDER_RANGE_DEG: tuple[float, float] = (-45.0, 45.0)


def _in_range(value: float | None, bounds: tuple[float, float]) -> bool:
    """Plausibility helper: None fails, out-of-range fails."""
    if value is None:
        return False
    lo, hi = bounds
    return lo <= value <= hi


@dataclass(slots=True)
class VolvoEvcHelmState:
    """Volvo Penta EVC Control Lever and Powertrim Telemetry (PGN 65360 / 65361).

    REVIEW hardening: sentinel (NOT_AVAILABLE / ERROR) inputs decode to
    None + is_valid=False — never to a fabricated 0.0/NEUTRAL mid-scale
    value. Plausibility ranges: lever ±100 %, rudder ±45 °.

    REVIEW 2-M4: attribution_confidence records whether the frame's
    Source Address was verified as a Volvo Penta node ("HIGH"), unknown
    ("LOW") — the masked PGN alone never proves Volvo origin on the
    Proprietary B range.
    """

    lever_position_percent: float | None  # -100% (Full Reverse) .. +100% (Full Ahead)
    gear_state: str  # "NEUTRAL" | "AHEAD" | "ASTERN" | "UNKNOWN"
    trim_angle_deg: float | None  # -10.0 .. +15.0 deg
    rudder_angle_deg: float | None  # -45.0 .. +45.0 deg
    station_active: bool
    is_valid: bool = True
    attribution_confidence: str = "HIGH"  # "HIGH" | "LOW" (SA-verified vs unknown)


class VolvoPentaDecoder:
    """Parser for Volvo Penta EDC J1587/J1939 fault payloads and EVC Marine CAN messages."""

    PGN_EVC_HELM: ClassVar[int] = 65360
    PGN_EVC_TRIM_RUDDER: ClassVar[int] = 65361

    # REVIEW 2-M4 (MEDIUM): PGN 65360/65361 are Proprietary B — every OEM
    # uses that range. The masked PGN alone never proves Volvo origin, so
    # the frame's Source Address gates attribution:
    #   - SAs in the documented Volvo Penta set decode at HIGH confidence;
    #   - SAs whose recorded PGN 60928 NAME claim carries a NON-Volvo
    #     manufacturer code are FOREIGN — decode refused outright (a
    #     third-party device's 0xFF50/0xFF51 traffic must never surface as
    #     Volvo helm/rudder telemetry);
    #   - unknown SAs (no static entry, no claim) still decode, flagged
    #     attribution_confidence="LOW" so the UI can warn.
    VOLVO_PENTA_SOURCE_ADDRESSES: ClassVar[frozenset[int]] = frozenset(
        {
            0x00,  # Engine 1 (SA 0) — J1939-81 preferred address, EDC primary
            0x01,  # Engine 2 (SA 1) — J1939-81 preferred address
        }
    )
    # canboat NAME Manufacturer Code for Volvo Penta — verified in BOTH
    # go-to-truth DBC tables: 174 "AB Volvo Penta" (data/dbc/j1939_canboat.dbc)
    # and 174 "Volvo Penta" (data/dbc/marine/n2k_canboat.dbc). NOTE: 60/61
    # are Volvo TRUCKS, a different company branch — not Volvo Penta marine.
    VOLVO_NAME_MANUFACTURER_CODES: ClassVar[frozenset[int]] = frozenset({174})

    # REVIEW 2-M4: SA -> NAME manufacturer code learned from PGN 60928
    # Address Claim frames. Thread-safe module-level learning table shared
    # by the classmethod decoder (classmethods cannot hold instance state).
    _sa_name_codes: dict[int, int] = {}
    _name_codes_lock = threading.Lock()

    @classmethod
    def record_address_claim(cls, frame: CanFrame) -> None:
        """Learn SA -> manufacturer code from a PGN 60928 Address Claim frame.

        REVIEW 2-M4: feeds the NAME-based ownership gate in
        decode_evc_can_frame. Fail-closed: only well-formed extended
        8-byte claims from PGN 60928 are accepted; everything else is
        ignored (never fabricates an ownership entry).
        """
        if not frame.is_extended or len(frame.data) < 8:
            return
        # PDU1 (PF < 0xF0): the PS octet is a destination address and must
        # be masked out — 0x18EEFF00 is PGN 60928 (0xEE00), NOT 0xEEFF.
        pgn_masked = (frame.arbitration_id >> 8) & 0x3FFFF
        pf = (pgn_masked >> 8) & 0xFF
        pgn = pgn_masked & 0x3FF00 if pf < 0xF0 else pgn_masked
        if pgn != 60928:  # 0xEE00
            return
        name = int.from_bytes(bytes(frame.data[0:8]), byteorder="little")
        manufacturer_code = (name >> 21) & 0x7FF  # NAME bits 21..31
        sa = frame.arbitration_id & 0xFF
        with cls._name_codes_lock:
            cls._sa_name_codes[sa] = manufacturer_code

    @classmethod
    def _source_ownership(cls, sa: int) -> str:
        """Classify the frame SA: "VOLVO_PENTA", "FOREIGN" or "UNKNOWN".

        A recorded PGN 60928 NAME claim ALWAYS outranks the static SA
        allowlist — a Cummins ECU claiming SA 0 (a perfectly legal J1939-81
        preferred address) must be FOREIGN, not "Volvo Penta by coincidence
        of address". The static list is only the fallback for unclaimed SAs.
        """
        with cls._name_codes_lock:
            code = cls._sa_name_codes.get(sa)
        if code is not None:
            if code in cls.VOLVO_NAME_MANUFACTURER_CODES:
                return "VOLVO_PENTA"
            return "FOREIGN"
        if sa in cls.VOLVO_PENTA_SOURCE_ADDRESSES:
            return "VOLVO_PENTA"
        return "UNKNOWN"

    @classmethod
    def parse_edc_fault_payload(cls, data: bytes) -> list[VolvoDtc]:
        """Parse Volvo Penta MID 128 PID/SID fault code payload."""
        dtcs: list[VolvoDtc] = []
        if len(data) < 3:
            return dtcs

        # Format: [Type (0=PID, 1=SID, 2=PPID, 3=PSID), Code_id, FMI | (Active << 7)]
        idx = 0
        while idx + 3 <= len(data):
            code_type_code = data[idx]
            code_id = data[idx + 1]
            fmi_raw = data[idx + 2]

            fmi = fmi_raw & 0x1F
            is_active = bool((fmi_raw >> 7) & 0x01)

            if code_type_code == 0:
                type_str = "PID"
                desc = VOLVO_PIDS.get(code_id, f"PID {code_id}")
            elif code_type_code == 1:
                type_str = "SID"
                desc = VOLVO_SIDS.get(code_id, f"SID {code_id}")
            elif code_type_code == 3:
                type_str = "PSID"
                desc = VOLVO_PSIDS.get(code_id, f"PSID {code_id}")
            else:
                type_str = "PPID"
                desc = f"PPID {code_id}"

            dtcs.append(
                VolvoDtc(
                    code_type=type_str,
                    code_id=code_id,
                    fmi=fmi,
                    description=desc,
                    is_active=is_active,
                )
            )
            idx += 3

        return dtcs

    @classmethod
    def decode_evc_can_frame(cls, frame: CanFrame) -> VolvoEvcHelmState | None:
        """Decode Volvo Penta EVC proprietary CAN frames (PGN 65360 / 65361).

        REVIEW 2-M4 (MEDIUM): the frame's Source Address gates attribution.
        FOREIGN sources (a recorded PGN 60928 NAME claim with a non-Volvo
        manufacturer code) decode to None — third-party 0xFF50/0xFF51
        traffic is never presented as Volvo telemetry. Unknown SAs decode
        with attribution_confidence="LOW".
        """
        if not frame.is_extended or len(frame.data) < 8:
            return None

        # Reject Extended Data Page frames: the EVC PGNs live in the standard
        # PGN space and an 18-bit mask would otherwise alias EDP=1 IDs onto
        # them (false-decode of unrelated traffic).
        if (frame.arbitration_id >> 25) & 0x01:
            return None

        pgn = (frame.arbitration_id >> 8) & 0x3FFFF

        if pgn not in (cls.PGN_EVC_HELM, cls.PGN_EVC_TRIM_RUDDER):
            return None

        # REVIEW 2-M4: source-ownership gate BEFORE payload decode —
        # PGN 65360/65361 are Proprietary B; the masked PGN alone never
        # proves the transmitter is a Volvo Penta node.
        sa = frame.arbitration_id & 0xFF
        ownership = cls._source_ownership(sa)
        if ownership == "FOREIGN":
            with cls._name_codes_lock:
                mfr = cls._sa_name_codes.get(sa)
            logger.warning(
                "Volvo EVC PGN from non-Volvo NAME-claimed source — decode refused",
                extra={"sa": sa, "pgn": pgn, "manufacturer_code": mfr},
            )
            return None
        attribution_confidence = "LOW" if ownership == "UNKNOWN" else "HIGH"

        if pgn == cls.PGN_EVC_HELM:
            # Byte 0: Lever position (-100% to +100%, 1% / bit, offset -125)
            # REVIEW hardening: 0xFF sentinel → None + invalid (was 0.0
            # fabricated mid-scale); 0xFE error → None + invalid.
            raw_lever = frame.data[0]
            if raw_lever == 0xFF or raw_lever == 0xFE:
                lever_pct: float | None = None
            else:
                lever_pct = float(raw_lever - 125)

            # Byte 1: Gear (0=Neutral, 1=Ahead, 2=Astern)
            # REVIEW hardening: reserved code 3 → UNKNOWN (was ASTERN).
            gear_code = frame.data[1] & 0x03
            gear = "NEUTRAL" if gear_code == 0 else ("AHEAD" if gear_code == 1 else ("ASTERN" if gear_code == 2 else "UNKNOWN"))

            # Byte 2: Station flags
            station_active = bool(frame.data[2] & 0x01)

            # Byte 3..4: Powertrim angle (0.1 deg / bit, offset -50 deg)
            raw_trim = int.from_bytes(frame.data[3:5], byteorder="little")
            trim_deg: float | None = (raw_trim * 0.1) - 50.0 if raw_trim not in (0xFFFF, 0xFFFE) else None

            # Byte 5..6: Rudder angle (0.1 deg / bit, offset -90 deg)
            raw_rudder = int.from_bytes(frame.data[5:7], byteorder="little")
            rudder_deg: float | None = (raw_rudder * 0.1) - 90.0 if raw_rudder not in (0xFFFF, 0xFFFE) else None

            # REVIEW hardening: plausibility — out-of-range physics marks
            # the whole helm state invalid (0xFFFE error pattern used to
            # decode as a huge-but-valid angle).
            valid = (
                (lever_pct is None or _in_range(lever_pct, LEVER_RANGE_PCT))
                and gear != "UNKNOWN"
                and (trim_deg is None or _in_range(trim_deg, TRIM_RANGE_DEG))
                and (rudder_deg is None or _in_range(rudder_deg, RUDDER_RANGE_DEG))
                and lever_pct is not None
                and trim_deg is not None
                and rudder_deg is not None
            )

            return VolvoEvcHelmState(
                lever_position_percent=lever_pct,
                gear_state=gear,
                trim_angle_deg=trim_deg,
                rudder_angle_deg=rudder_deg,
                station_active=station_active,
                is_valid=valid,
                attribution_confidence=attribution_confidence,
            )

        if pgn == cls.PGN_EVC_TRIM_RUDDER:
            # Byte 0..1: Powertrim angle (0.1 deg / bit, offset -50 deg)
            raw_trim = int.from_bytes(frame.data[0:2], byteorder="little")
            trim_deg2: float | None = (raw_trim * 0.1) - 50.0 if raw_trim not in (0xFFFF, 0xFFFE) else None

            # Byte 2..3: Rudder angle (0.1 deg / bit, offset -90 deg)
            raw_rudder = int.from_bytes(frame.data[2:4], byteorder="little")
            rudder_deg2: float | None = (raw_rudder * 0.1) - 90.0 if raw_rudder not in (0xFFFF, 0xFFFE) else None

            # Byte 4: Station flags
            station_active = bool(frame.data[4] & 0x01) if len(frame.data) > 4 else True

            # REVIEW hardening: this PGN carries no lever/gear — they stay
            # None/UNKNOWN + invalid instead of fabricated 0.0/NEUTRAL.
            valid2 = (trim_deg2 is None or _in_range(trim_deg2, TRIM_RANGE_DEG)) and (
                rudder_deg2 is None or _in_range(rudder_deg2, RUDDER_RANGE_DEG)
            ) and trim_deg2 is not None and rudder_deg2 is not None

            return VolvoEvcHelmState(
                lever_position_percent=None,
                gear_state="UNKNOWN",
                trim_angle_deg=trim_deg2,
                rudder_angle_deg=rudder_deg2,
                station_active=station_active,
                is_valid=valid2,
                attribution_confidence=attribution_confidence,
            )

        return None
