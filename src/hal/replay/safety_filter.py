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
    # M-22 (P2-9): the table is SPLIT by policy. The old single set hung
    # Address Claim AND every diagnostic-evidence-wipe PGN off the same
    # block_address_claim flag — an operator disabling address-claim
    # blocking (e.g. to replay a capture containing claims for analysis)
    # silently re-enabled DM3/DM4/DM5/DM11 (DTC evidence wipe) too.
    ADDRESS_CLAIM_PGNS: ClassVar[set[int]] = {
        0x0EE00,  # 60928: Address Claimed / Cannot Claim (J1939-81)
        0x0FED8,  # 65240: Commanded Address
        0x0EA00,  # 59904: Request PGN (arbitrary PGN trigger)
    }
    DIAGNOSTIC_WRITE_PGNS: ClassVar[set[int]] = {
        # REVIEW 1-L3: DM2 (65227) REMOVED — it is a read-only broadcast
        # of previously-active DTCs; replaying its data frame has no
        # write/erase effect on any ECU (erasure is requested via PGN
        # 59904 Request, which stays blocked). Blocking DM2 data replay
        # also blocked legitimate fault-history telemetry analysis and
        # inflated blocked-reason metrics (false positive).
        0x0FECC,  # 65228: DM3 — Diagnostic Data Clear (DTC evidence wipe)
        0x0FECD,  # 65229: DM4 — Freeze Frame Clear (write path)
        0x0FECE,  # 65230: DM5 — Diagnostic Readiness Clear (write path)
        0x0FED3,  # 65235: DM11 — Diagnostic Data Clear (write path)
    }
    # Backwards-compatible union (see is_frame_safe for the split checks).
    BLOCKED_PGNS: ClassVar[set[int]] = ADDRESS_CLAIM_PGNS | DIAGNOSTIC_WRITE_PGNS

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
    # MEDIUM-1: 0x7E8–0x7EF (ECU RESPONSE IDs) join the set. Replaying
    # crafted frames there spoofs the live tester's expectations (fake
    # "security access granted", fake "routine finished OK", fake flash
    # data) and previously bypassed every check — responses never matched
    # the request-only table. Responses are additionally default-blocked
    # outright (see ECU_RESPONSE_11BIT_IDS): a replayed response is spoofed
    # evidence by definition.
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
        0x7E8,  # ECU Response — spoofing protection (MEDIUM-1)
        0x7E9,
        0x7EA,
        0x7EB,
        0x7EC,
        0x7ED,
        0x7EE,
        0x7EF,  # ECU Response
    }

    # ECU Response IDs (11-bit): 0x7E0+k requests, 0x7E8+k responses.
    # MEDIUM-1: replaying ANY frame here counterfeits the live ECU's voice
    # (fake "security access granted" 0x67, fake "routine complete" 0x71).
    # A replayed response is by definition spoofed evidence — default-block.
    ECU_RESPONSE_11BIT_IDS: ClassVar[set[int]] = {
        0x7E8,
        0x7E9,
        0x7EA,
        0x7EB,
        0x7EC,
        0x7ED,
        0x7EE,
        0x7EF,
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

        # CRITICAL-1: per-arbitration-ID ISO-TP session ledger:
        # arb_id -> (SID the First Frame carried, payload bytes the CFs still owe).
        self._iso_tp_pending: dict[int, tuple[int, int]] = {}

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
                # T62-C3: ISO 15765-2:2016 requires CAN-FD escape SF to have 8 <= sf_dl <= 62
                if sf_dl < 8 or sf_dl > 62 or len(frame.data) < sf_dl + 2:
                    return self._UNKNOWN_SID
                return frame.data[2]
            # Classic SF: low_nibble is SF_DL (must be >= 1); SID at data[1]
            if low_nibble < 1 or len(frame.data) < low_nibble + 1:
                return self._UNKNOWN_SID
            return frame.data[1]

        if pci_type == 0x1:  # First Frame
            if (
                # CRITICAL-1: without the is_fd gate, a CLASSIC FF with
                # FF_DL=256 (data=[0x10, 0x00, ...]) was misread as the FD
                # escape form and its SID taken from data[6] — a prohibited
                # SID sitting at data[2] slipped through unchecked.
                frame.is_fd
                and frame.data[0] == 0x10
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

    def _register_first_frame(self, frame: CanFrame, sid: int) -> None:
        """CRITICAL-1: open a session ledger entry for an ISO-TP First Frame.

        CF/FC frames carry no SID, so blocking the FF alone never helped:
        the CFs carrying the actual request bytes sailed straight through.
        Recording (SID, remaining payload length) here makes every following
        CF/FC on the same arbitration ID answer for its FF's session.
        """
        ff_dl = ((frame.data[0] & 0x0F) << 8) | frame.data[1]
        # Classic FF carries 6 payload bytes; the FD escape form carries 10.
        # Same is_fd gate as _extract_uds_sid so both agree on the layout.
        carried = (
            10
            if (frame.is_fd and frame.data[0] == 0x10 and frame.data[1] == 0x00 and len(frame.data) >= 6)
            else 6
        )
        self._iso_tp_pending[frame.arbitration_id] = (sid, max(0, ff_dl - carried))

    def _prohibited_sid(self, sid: int) -> bool:
        """Fail-closed SID verdict shared by the FF and CF/FC gates."""
        return (
            sid == self._UNKNOWN_SID
            or sid in self.PROHIBITED_UDS_SIDS
            or (self.block_actuator_routines and sid in self.ACTUATOR_UDS_SIDS)
        )

    def _check_iso_tp_cf_fc(self, frame: CanFrame, is_diagnostic: bool) -> tuple[bool, str]:
        """CRITICAL-1: gate ISO-TP CF (PCI 0x2) / FC (PCI 0x3) frames.

        Fail-closed order:
          1. Tunneling ON (default): CF/FC on diagnostic IDs are blocked
             outright — a replayed CF/FC can only mean a live ECU is being
             herded through somebody else's session.
          2. Tunneling OFF (analysis mode): the session ledger still holds
             the FF's SID — CFs belonging to a PROHIBITED session are
             blocked; benign sessions pass until the FF_DL is accounted for.
        """
        if not frame.data or not is_diagnostic:
            return True, ""
        pci_type = (frame.data[0] >> 4) & 0x0F
        if pci_type not in (0x2, 0x3):
            return True, ""
        if self.block_transport_tunneling:
            return False, f"BLOCKED_TP_TUNNEL: CF/FC on diagnostic ID 0x{frame.arbitration_id:X}"
        if frame.arbitration_id in self._iso_tp_pending:
            sid, remaining = self._iso_tp_pending[frame.arbitration_id]
            if self._prohibited_sid(sid):
                return False, f"PROHIBITED_ISO_TP_SESSION_SID: 0x{sid:02X}"
            if pci_type == 0x2:  # FC carries no payload bytes
                carried = len(frame.data) - 1
                if remaining - carried <= 0:
                    del self._iso_tp_pending[frame.arbitration_id]  # session complete
                else:
                    self._iso_tp_pending[frame.arbitration_id] = (sid, remaining - carried)
        return True, ""

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

            # M-22 (P2-9): address-claiming PGNs answer to
            # block_address_claim; diagnostic-evidence-wipe PGNs answer to
            # block_diagnostic_write — the two policies are independent.
            if self.block_address_claim and (
                pgn in self.ADDRESS_CLAIM_PGNS
                or masked_pgn in self.ADDRESS_CLAIM_PGNS
                or edp_masked_pgn in self.ADDRESS_CLAIM_PGNS
                or edp_masked_norm in self.ADDRESS_CLAIM_PGNS
            ):
                return False, f"BLOCKED_J1939_PGN: {pgn} (0x{pgn:05X})"

            if self.block_diagnostic_write and (
                pgn in self.DIAGNOSTIC_WRITE_PGNS
                or masked_pgn in self.DIAGNOSTIC_WRITE_PGNS
                or edp_masked_pgn in self.DIAGNOSTIC_WRITE_PGNS
                or edp_masked_norm in self.DIAGNOSTIC_WRITE_PGNS
            ):
                return False, f"BLOCKED_DIAGNOSTIC_WRITE_PGN: {pgn} (0x{pgn:05X})"

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
            if self.block_diagnostic_write and pdu_format in {0xDA, 0xDB}:
                safe, reason = self._check_iso_tp_cf_fc(frame, is_diagnostic=True)
                if not safe:
                    return False, reason
                if len(frame.data) >= 2:
                    sid = self._extract_uds_sid(frame)
                    if sid is not None:
                        # CRITICAL-1: register the session BEFORE judging it —
                        # a prohibited FF that is blocked without a ledger entry
                        # would leave its CFs unaccounted for in tunneling-OFF
                        # (analysis) mode, re-opening the exact CF bypass.
                        if (frame.data[0] >> 4) & 0x0F == 0x1:
                            self._register_first_frame(frame, sid)
                        if self._prohibited_sid(sid):
                            return False, f"PROHIBITED_29BIT_UDS_SID: 0x{sid:02X}"

        # 11-bit Standard Frame Evaluation (OBD-II / UDS)
        else:
            # MEDIUM-1: a replayed frame on an ECU response ID is spoofed
            # evidence by definition — block before any SID whitelisting.
            if self.block_diagnostic_write and frame.arbitration_id in self.ECU_RESPONSE_11BIT_IDS:
                return False, f"BLOCKED_ECU_RESPONSE_SPOOF: 0x{frame.arbitration_id:X}"
            is_diag_id = frame.arbitration_id in self.DIAGNOSTIC_11BIT_IDS
            if self.block_diagnostic_write and is_diag_id:
                safe, reason = self._check_iso_tp_cf_fc(frame, is_diagnostic=True)
                if not safe:
                    return False, reason
                if len(frame.data) >= 2:
                    sid = self._extract_uds_sid(frame)
                    if sid is not None:
                        if (frame.data[0] >> 4) & 0x0F == 0x1:
                            self._register_first_frame(frame, sid)
                        if self._prohibited_sid(sid):
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
                    "reason": str(reason)[:500],
                    "data": frame.data.hex()[:16] + ("..." if len(frame.data.hex()) > 16 else ""),
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
