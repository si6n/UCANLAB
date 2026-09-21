"""DBC database generation from discovered signal and protocol hypotheses."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import cantools
from cantools.database.can.database import Database
from cantools.database.can.message import Message
from cantools.database.can.signal import Signal
from cantools.database.conversion import BaseConversion

from src.engine.discovery.hypotheses import IdReport


def _sanitize_c_identifier(name: str) -> str:
    """Sanitize arbitrary string into valid DBC C identifier."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", name.strip())
    if cleaned and cleaned[0].isdigit():
        cleaned = f"sig_{cleaned}"
    return cleaned or "signal"


class DbcBuilder:
    """Constructs and serializes cantools CAN databases from discovery hypothesis reports."""

    @classmethod
    def build_database(
        cls,
        reports: Mapping[int, IdReport] | Sequence[IdReport],
        approved_only: bool = False,
    ) -> Database:
        """Create a cantools Database from ID reports."""
        db = Database()
        reports_list = reports.values() if isinstance(reports, Mapping) else reports

        for report in reports_list:
            if not report.hypotheses:
                continue

            signals: list[Signal] = []
            claimed_names: set[str] = set()
            occupied_bits: set[int] = set()
            # REVIEW 3 follow-up: compute the byte length ONCE, before signal
            # placement, so over-spanning candidates are rejected before they
            # claim bits. Previously an over-spanning candidate (dropped later
            # by the bounding pass) still marked its bits occupied, silently
            # evicting legitimate overlapping candidates (e.g. a 16-bit signal
            # at bit 48 lost to a later-rejected bit-56 candidate in an 8-byte
            # message).
            msg_len = max(1, int(report.dlc))

            # Sort hypotheses so higher confidence and wider signals are placed first
            sorted_hyps = sorted(
                report.hypotheses,
                key=lambda h: (1 if h.status == "approved" else 0, h.confidence, h.length),
                reverse=True,
            )

            for hyp in sorted_hyps:
                if approved_only and hyp.status != "approved":
                    continue

                # Ensure signal bits do not overlap with any previously placed signal
                hyp_bits = set(range(hyp.start_bit, hyp.start_bit + hyp.length))
                if hyp_bits & occupied_bits:
                    continue

                # Signal must fit inside the observed payload byte length.
                # IdReport.dlc is the observed payload BYTE length
                # (max(len(f.data))), never the FD DLC code — DLC 9..15 encode
                # capacities 12..64 bytes. cantools Message(length=) likewise
                # expects bytes.
                if hyp.start_bit + hyp.length > msg_len * 8:
                    continue

                raw_name = hyp.name or f"SIG_{hyp.start_bit}_{hyp.length}"
                sig_name = _sanitize_c_identifier(raw_name)
                # Guarantee signal name uniqueness within message
                base_name = sig_name
                counter = 1
                while sig_name in claimed_names:
                    sig_name = f"{base_name}_{counter}"
                    counter += 1
                claimed_names.add(sig_name)
                # Claim bits only for accepted candidates (span + overlap
                # checks already passed above).
                occupied_bits.update(hyp_bits)

                # Format comments from evidence
                evidence_comments = [e.detail for e in hyp.evidence]
                comment_str = " | ".join(evidence_comments) if evidence_comments else f"Auto-discovered {hyp.htype}"

                conv = BaseConversion.factory(scale=hyp.factor, offset=hyp.offset)
                # REVIEW 3 (Motorola start bit): DBC Motorola start bit uses
                # the SAME numeric position as LSB0 numbering within each
                # byte (Vector sawtooth: byte0 = 7..0, byte1 = 15..8). Our
                # Motorola-contiguous series anchors at byte bit 0, so the
                # MSB sits at LSB0 bit anchor+7 — that IS the DBC start bit.
                # Verified empirically: cantools start=7|16 big_endian decodes
                # [0x12,0x34] as 0x1234; the old `(anchor//8)*8+7` collapsed
                # non-byte-aligned anchors to the byte's bit 7.
                if hyp.is_little_endian:
                    start_bit = hyp.start_bit
                else:
                    start_bit = hyp.start_bit + 7

                signal = Signal(
                    name=sig_name,
                    start=start_bit,
                    length=hyp.length,
                    byte_order="little_endian" if hyp.is_little_endian else "big_endian",
                    is_signed=hyp.is_signed,
                    conversion=conv,
                    minimum=hyp.min_value,
                    maximum=hyp.max_value,
                    unit=hyp.unit or None,
                    comment=comment_str,
                )
                signals.append(signal)

            if signals:
                # REVIEW 4.1: honour the discovery engine's own `is_extended`
                # flag. The old `> 0x7FF` heuristic mis-typed a 29-bit frame
                # whose numeric ID is <= 0x7FF (e.g. Priority 0 / PGN 0 /
                # SA 1 => 0x00000001) as an 11-bit standard frame, so the
                # exported DBC never matched those messages in CANoe/Wireshark.
                # Fall back to the heuristic only when the report predates the
                # field (defensive getattr).
                is_extended = getattr(report, "is_extended", False) or report.arbitration_id > 0x7FF
                msg_name = f"MSG_0x{report.arbitration_id:04X}"
                msg = Message(
                    frame_id=report.arbitration_id,
                    name=msg_name,
                    length=msg_len,
                    signals=signals,
                    is_extended_frame=is_extended,
                    comment=f"Auto-generated for CAN ID 0x{report.arbitration_id:04X} ({report.frame_count} frames analyzed)",
                )
                db.messages.append(msg)

        if db.messages:
            # REVIEW 3 follow-up: `db.messages.append()` bypasses the
            # `_frame_id_to_message` index — refresh so the returned Database
            # supports get_message_by_frame_id()/name lookups directly.
            db.refresh()
        return db

    @staticmethod
    def _motorola_end_bit(start_msb: int, length: int) -> int:
        """Highest LSB0 bit covered by a Motorola signal given its DBC MSB start.

        DBC Motorola numbering matches LSB0 within each byte (start=7 is
        byte0 bit7). The signal walks down from the MSB; when it crosses a
        byte boundary it continues at the next byte's bit 7. The LSB0 span
        end equals `start - 7 + (length - 8)` for byte-crossing lengths.
        """
        byte_idx, bit_in_byte = divmod(start_msb, 8)
        # bits remaining in the MSB byte (counting down from bit_in_byte)
        bits_in_msb_byte = bit_in_byte + 1
        if length <= bits_in_msb_byte:
            return start_msb - (length - 1)
        remaining = length - bits_in_msb_byte
        # continue at (byte_idx+1) bit 7, walking down
        return (byte_idx + 1) * 8 + 7 - (remaining - 1)

    @classmethod
    def export_dbc_file(
        cls, db: Database, file_path: str | Path, exports_root: str | Path | None = None
    ) -> None:
        """Save database to a .dbc file."""
        # F7: shared, fail-closed confinement (see exporters.path_guard). The
        # former inline copy waived the root check for the system temp dir.
        from src.engine.exporters.path_guard import resolve_export_path

        path = resolve_export_path(file_path, exports_root, allow_cwd_fallback=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        cantools.database.dump_file(db, str(path))
