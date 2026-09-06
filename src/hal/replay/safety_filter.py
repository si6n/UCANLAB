"""Replay Safety Filter protecting live CAN networks from malicious or invalid playback frames.

Complies with Saha Risk Kataloğu v1.2 Sections 21, 41, Risk R-17.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame

logger = get_logger("hal.replay.safety_filter")


class ReplaySafetyFilter:
    """Filters unsafe commands (Address Claim, ECU Reset, Diagnostics) from replay streams."""

    # Critical J1939 / NMEA2000 PGNs to block in replay.
    # D4/P1-3: hex values now DERIVED from the decimal PGN assignments in
    # SAE J1939-73 (comment = decimal → hex, verifiable at a glance). The
    # previous table wrote the hex digits of one PGN next to the decimal of
    # another (e.g. 0x0FED5 == 65237, not DM4's 65229), so DM3/DM4/DM5/DM11
    # were never actually blocked while ET1 (65262) was blocked instead.
    BLOCKED_PGNS: ClassVar[set[int]] = {
        0x0EE00,  # 60928: Address Claimed / Cannot Claim (J1939-81)
        0x0FED8,  # 65240: Commanded Address
        0x0EA00,  # 59904: Request PGN (arbitrary PGN trigger)
        0x0FECB,  # 65227: DM2 — Previously Active DTCs (diagnostic state churn)
        0x0FECC,  # 65228: DM3 — Diagnostic Data Clear (DTC evidence wipe)
        0x0FECD,  # 65229: DM4 — Freeze Frame Clear (write path)
        0x0FECE,  # 65230: DM5 — Diagnostic Readiness Clear (write path)
        0x0FED3,  # 65235: DM11 — Diagnostic Data Clear (write path)
    }

    # D5: J1939-21 transport PGNs. TP.CM carries control bytes that command
    # peer-side session behaviour (RTS/CTS/Abort) and TP.DT can tunnel any
    # blocked payload in 7-byte slices — replaying either onto a live bus
    # re-implements the exact session hijack the filter exists to stop.
    TRANSPORT_TUNNEL_PGNS: ClassVar[set[int]] = {
        0x0EC00,  # 60416: TP.CM (Connection Management)
        0x0EB00,  # 60160: TP.DT (Data Transfer)
    }

    # P1-2: PGNs whose replay physically actuates the vehicle. Guarded by
    # block_actuator_routines (previously a dead flag — now a real gate).
    ACTUATION_PGNS: ClassVar[set[int]] = {
        0x00000,  # 0: TSC1 — Torque/Speed Control 1
        0x00400,  # 1024: XBR — External Brake Request
    }

    # P1-2: UDS SIDs that directly drive outputs. Guarded by
    # block_actuator_routines.
    ACTUATOR_UDS_SIDS: ClassVar[set[int]] = {
        0x2F,  # Input/Output Control By Identifier
        0x3D,  # Write Memory By Address
    }

    # Standard Diagnostic Request Arbitration IDs (11-bit)
    DIAGNOSTIC_11BIT_IDS: ClassVar[set[int]] = {
        0x7DF,  # Functional Broadcast Request
        0x7E0,
        0x7E1,
        0x7E2,
        0x7E3,
        0x7E4,
        0x7E5,
        0x7E6,
        0x7E7,  # Physical Request
    }

    # Prohibited Diagnostic Service Identifiers (UDS SIDs)
    PROHIBITED_UDS_SIDS: ClassVar[set[int]] = {
        0x10,  # Diagnostic Session Control (switching to programming/extended)
        0x11,  # ECU Reset
        0x14,  # Clear Diagnostic Information
        0x27,  # Security Access
        0x28,  # Communication Control
        0x2E,  # Write Data By Identifier
        0x2F,  # Input/Output Control By Identifier (P1-2: direct actuator drive)
        0x31,  # Routine Control (actuator testing)
        0x34,  # Request Download
        0x36,  # Transfer Data
        0x37,  # Request Transfer Exit
        0x38,  # Request File Transfer
        0x3D,  # Write Memory By Address (P1-2: raw memory writes)
        0x3E,  # Tester Present (session keep-alive for the above)
        0x85,  # Control DTC Setting
        0x87,  # Link Control (baud-rate changes)
    }

    def __init__(
        self,
        block_address_claim: bool = True,
        block_diagnostic_write: bool = True,
        block_actuator_routines: bool = True,
        block_transport_tunneling: bool = True,
        custom_blocked_ids: set[int] | None = None,
    ) -> None:
        self.block_address_claim = block_address_claim
        self.block_diagnostic_write = block_diagnostic_write
        self.block_actuator_routines = block_actuator_routines
        self.block_transport_tunneling = block_transport_tunneling
        self.custom_blocked_ids = custom_blocked_ids or set()

        self.total_evaluated: int = 0
        self.total_passed: int = 0
        self.total_blocked: int = 0
        self.blocked_reasons: dict[str, int] = {}

    def _extract_uds_sid(self, frame: CanFrame) -> int | None:
        """Extract the UDS SID from an ISO 15765-2 encoded frame (P1-4).

        PCI layout rules per ISO 15765-2:2016:
          - Classic SF (CAN_DL <= 8): SID at data[1]
          - FD SF escape (CAN_DL > 8, low nibble == 0): SID at data[2]
          - Classic FF: SID at data[2]
          - FD FF escape (0x10 0x00 + 32-bit length): SID at data[6]

        Fail-closed: an SF/FF whose PCI implies a longer frame than we can
        see is treated as UNKNOWN → blocked by the caller.
        """
        if len(frame.data) < 2:
            return None

        pci_type = (frame.data[0] >> 4) & 0x0F

        if pci_type == 0x0:  # Single Frame
            low_nibble = frame.data[0] & 0x0F
            if frame.is_fd and len(frame.data) > 8:
                # FD escape SF: [0x00, SF_DL, payload...] — SF_DL lives in
                # data[1], so the SID starts at data[2]
                if low_nibble != 0x0:
                    return self._UNKNOWN_SID  # classic nibble on an FD-length frame: malformed
                if len(frame.data) < 3:
                    return self._UNKNOWN_SID
                sf_dl = frame.data[1]
                if sf_dl < 1 or len(frame.data) < sf_dl + 2:
                    return self._UNKNOWN_SID
                return frame.data[2]
            # Classic SF: low_nibble is SF_DL (must be >= 1); SID at data[1]
            if low_nibble < 1 or len(frame.data) < low_nibble + 1:
                return self._UNKNOWN_SID
            return frame.data[1]

        if pci_type == 0x1:  # First Frame
            if (
                frame.data[0] == 0x10
                and frame.data[1] == 0x00
                and len(frame.data) >= 6
            ):
                # FD escape FF: 0x10 0x00 + 32-bit FF_DL + payload
                # → SID at data[6]
                if len(frame.data) < 7:
                    return self._UNKNOWN_SID
                return frame.data[6]
            if len(frame.data) >= 3:
                return frame.data[2]
            return self._UNKNOWN_SID

        # Consecutive/Flow-Control/unknown PCI — SID not applicable here.
        return None

    # Sentinel for "a diagnostic frame we cannot prove safe" — never a
    # member of PROHIBITED_UDS_SIDS by construction (negative int).
    _UNKNOWN_SID: ClassVar[int] = -1

    def is_frame_safe(self, frame: CanFrame) -> tuple[bool, str]:
        """Evaluate if a frame is safe to be transmitted onto a CAN bus during replay."""
        self.total_evaluated += 1

        # Check custom blocked IDs
        if frame.arbitration_id in self.custom_blocked_ids:
            return False, f"CUSTOM_BLOCKED_ID: 0x{frame.arbitration_id:08X}"

        # 29-bit Extended Frame Evaluation (J1939 / N2K)
        if frame.is_extended:
            # Mask out EDP bit (bit 25) so EDP=1 cannot evade blocked tables (0x1FFFF)
            edp_masked_pgn = ((frame.arbitration_id >> 8) & 0x1FFFF)
            edp_masked_pf = (edp_masked_pgn >> 8) & 0xFF
            edp_masked_norm = (edp_masked_pgn & 0x1FF00) if edp_masked_pf < 240 else edp_masked_pgn

            pgn = (frame.arbitration_id >> 8) & 0x3FFFF
            pdu_format = (pgn >> 8) & 0xFF
            masked_pgn = (pgn & 0x3FF00) if pdu_format < 240 else pgn

            if self.block_address_claim and (
                pgn in self.BLOCKED_PGNS
                or masked_pgn in self.BLOCKED_PGNS
                or edp_masked_pgn in self.BLOCKED_PGNS
                or edp_masked_norm in self.BLOCKED_PGNS
            ):
                return False, f"BLOCKED_J1939_PGN: {pgn} (0x{pgn:05X})"

            # P1-2: TSC1/XBR physically command the vehicle — gated by the
            # (formerly dead) block_actuator_routines flag, default ON.
            if self.block_actuator_routines and (
                pgn in self.ACTUATION_PGNS or masked_pgn in self.ACTUATION_PGNS
            ):
                return False, f"BLOCKED_ACTUATION_PGN: {pgn} (0x{pgn:05X})"

            # D5: TP.CM/TP.DT frames can tunnel arbitrary payloads (including
            # every blocked diagnostic command) in 7-byte slices and can
            # command peer-side session behaviour — block by default.
            if self.block_transport_tunneling and (
                pgn in self.TRANSPORT_TUNNEL_PGNS or masked_pgn in self.TRANSPORT_TUNNEL_PGNS
            ):
                return False, f"BLOCKED_TP_TUNNEL: {pgn} (0x{pgn:05X})"

            # ISO-TP / UDS over 29-bit (e.g. 0x18DAxxF1)
            if self.block_diagnostic_write and pdu_format in {0xDA, 0xDB} and len(frame.data) >= 2:
                sid = self._extract_uds_sid(frame)
                prohibited = (
                    sid is not None
                    and (
                        sid == self._UNKNOWN_SID
                        or sid in self.PROHIBITED_UDS_SIDS
                        or (self.block_actuator_routines and sid in self.ACTUATOR_UDS_SIDS)
                    )
                )
                if prohibited:
                    return False, f"PROHIBITED_29BIT_UDS_SID: 0x{sid:02X}"

        # 11-bit Standard Frame Evaluation (OBD-II / UDS)
        else:
            if self.block_diagnostic_write and frame.arbitration_id in self.DIAGNOSTIC_11BIT_IDS:
                if len(frame.data) >= 2:
                    sid = self._extract_uds_sid(frame)
                    prohibited = (
                        sid is not None
                        and (
                            sid == self._UNKNOWN_SID
                            or sid in self.PROHIBITED_UDS_SIDS
                            or (self.block_actuator_routines and sid in self.ACTUATOR_UDS_SIDS)
                        )
                    )
                    if prohibited:
                        return False, f"PROHIBITED_11BIT_UDS_SID: 0x{sid:02X}"

        return True, ""

    def filter_frame(self, frame: CanFrame) -> CanFrame | None:
        """Return frame if safe, or None if blocked by safety policy."""
        is_safe, reason = self.is_frame_safe(frame)
        if not is_safe:
            self.total_blocked += 1
            self.blocked_reasons[reason] = self.blocked_reasons.get(reason, 0) + 1
            logger.warning(
                "Replay Safety Filter BLOCKED unsafe frame",
                extra={
                    "arbitration_id": hex(frame.arbitration_id),
                    "reason": reason,
                    "data": frame.data.hex(),
                },
            )
            return None

        self.total_passed += 1
        return frame

    def filter_sequence(self, frames: Sequence[CanFrame]) -> list[CanFrame]:
        """Filter an entire sequence of frames, removing unsafe entries."""
        safe_frames: list[CanFrame] = []
        for frame in frames:
            filtered = self.filter_frame(frame)
            if filtered is not None:
                safe_frames.append(filtered)
        return safe_frames
