"""SAE J1939 Commercial Vehicle OEM Proprietary Routing Registry & Canonical Types.

Complies with SAE J1939-21, SAE J1939-71, and SPEC-DIAG-J1939-V1.0 Section 5 & 6.
Handles Proprietary A (PGN 61184 / 0xEF00) and Proprietary B (PGN 65280-65535 / 0xFF00-0xFFFF).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any

from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame
from src.engine.decoder.dbc_decoder import DecodedSignal

logger = get_logger("protocols.j1939.oem")

# REVIEW (HIGH-7): SAE J1939-81 NAME Manufacturer Code (bits 31..21 of the
# 64-bit NAME, PGN 60928 Address Claim) per the canboat J1939 name table
# (data/dbc/j1939_canboat.dbc VAL_ 2565734400 Manufacturer_Code). An OEM
# attribution is CONFIRMED only when the claiming node's NAME carries that
# OEM's manufacturer code — otherwise the match is payload-pattern only.
OEM_NAME_MANUFACTURER_CODES: dict[str, set[int]] = {
    # "Caterpillar Inc."
    "Caterpillar": {8},
    # "Cummins Inc (formerly Cummins Engine Co)"; 440 = Cummins Power Generation
    "Cummins": {10, 440},
    # "Detroit Diesel Corporation"
    "Detroit": {14},
    # "Volvo Trucks North America Inc." / "Volvo Truck Corp." / AB Volvo Penta (174)
    "Volvo": {60, 61, 174},
    # "Scania"
    "Scania": {68},
    # "Daimler Benz AG - Engine Division (PBM)" (Actros powertrain);
    # 164 = MTU Friedrichshafen (formerly DaimlerChrysler Off-Highway)
    "Mercedes-Benz": {79, 164},
}

# REVIEW hardening: unknown/reserved enum helper shared by all OEM
# decoders. Map-miss values are NEVER valid telemetry — they surface as
# is_valid=False with a RESERVED/UNKNOWN status, never as HIGH-confidence
# fabricated strings.
RESERVED_UNKNOWN_LABEL: str = "RESERVED/UNKNOWN"


def resolve_enum(
    mapping: dict[int, str],
    raw: int,
    *,
    signal_name: str,
    unit: str = "enum",
) -> DecodedSignal:
    """Build a DecodedSignal for an enum/nibble field (fail-closed).

    Known codes → VALID/HIGH-confidence; unknown/reserved codes →
    is_valid=False with RESERVED status. Sentinel 0xFE/0xFF callers must
    screen those first (they carry ERROR/NOT_AVAILABLE semantics).
    """
    from src.engine.decoder.dbc_decoder import DecodedSignal, SignalStatus

    label = mapping.get(raw)
    if label is None:
        return DecodedSignal(
            name=signal_name,
            value=f"{RESERVED_UNKNOWN_LABEL} (0x{raw:02X})",
            unit=unit,
            raw_value=raw,
            is_valid=False,
            status=SignalStatus.ERROR,
            confidence="LOW",
        )
    return DecodedSignal(
        name=signal_name,
        value=label,
        unit=unit,
        raw_value=raw,
        is_valid=True,
        status=SignalStatus.VALID,
    )





def parse_j1939_id(arbitration_id: int) -> tuple[int, int, int | None, int]:
    """Parse a 29-bit CAN arbitration identifier into J1939 components.

    Returns:
        (pgn, source_address, destination_address, priority)
        - pgn: 18-bit Parameter Group Number (0x00000..0x3FFFF)
        - source_address: 8-bit SA (0..255)
        - destination_address: 8-bit DA (0..255) if PDU1 (PF < 240), else None
        - priority: 3-bit priority (0..7)
    """
    priority = (arbitration_id >> 26) & 0x07
    edp = (arbitration_id >> 25) & 0x01
    dp = (arbitration_id >> 24) & 0x01
    pf = (arbitration_id >> 16) & 0xFF
    ps = (arbitration_id >> 8) & 0xFF
    sa = arbitration_id & 0xFF

    if pf < 240:
        # PDU1 Format: Destination Specific (Unicast)
        da: int | None = ps
        pgn = (edp << 17) | (dp << 16) | (pf << 8)
    else:
        # PDU2 Format: Global Broadcast (Group Extension)
        da = None
        pgn = (edp << 17) | (dp << 16) | (pf << 8) | ps

    return pgn, sa, da, priority


def build_j1939_id(
    pgn: int,
    sa: int,
    da: int | None = None,
    priority: int = 6,
) -> int:
    """Construct a 29-bit CAN arbitration identifier from J1939 components."""
    priority = priority & 0x07
    edp = (pgn >> 17) & 0x01
    dp = (pgn >> 16) & 0x01
    pf = (pgn >> 8) & 0xFF
    ps = pgn & 0xFF

    if pf < 240:
        # PDU1: PS is Destination Address
        target_da = (da if da is not None else 0xFF) & 0xFF
        can_id = (priority << 26) | (edp << 25) | (dp << 24) | (pf << 16) | (target_da << 8) | (sa & 0xFF)
    else:
        # PDU2: PS is Group Extension
        can_id = (priority << 26) | (edp << 25) | (dp << 24) | (pf << 16) | (ps << 8) | (sa & 0xFF)

    return can_id


@dataclass(slots=True)
class OemDecodedPayload:
    """Standardized physical signal payload decoded from OEM proprietary J1939 frames."""

    manufacturer: str
    pgn: int
    signals: dict[str, DecodedSignal]
    timestamp_ns: int
    arbitration_id: int = 0
    source_address: int = 0
    destination_address: int | None = None
    is_broadcast: bool = True
    service_id: int | None = None
    raw_data: bytes = b""
    confidence: str = "HIGH"

    def get_value(self, name: str, default: Any = None) -> Any:
        """Return the physical value of a decoded signal if present and valid."""
        sig = self.signals.get(name)
        if sig is not None and sig.is_valid:
            return sig.value
        return default

    def get_signal(self, name: str) -> DecodedSignal | None:
        """Return the DecodedSignal instance by name."""
        return self.signals.get(name)

    def is_valid(self, name: str) -> bool:
        """Check if a specific signal was decoded with valid status."""
        sig = self.signals.get(name)
        return sig is not None and sig.is_valid

    def __getitem__(self, name: str) -> DecodedSignal:
        """Dict-like access to signals."""
        return self.signals[name]

    def __contains__(self, name: str) -> bool:
        """Check if signal name is present in payload."""
        return name in self.signals

    def to_dict(self) -> dict[str, Any]:
        """Serialize payload to dictionary for telemetry pipelines and JSON logging."""
        return {
            "manufacturer": self.manufacturer,
            "pgn": self.pgn,
            "pgn_hex": f"0x{self.pgn:X}",
            "arbitration_id": f"0x{self.arbitration_id:08X}",
            "source_address": self.source_address,
            "destination_address": self.destination_address,
            "timestamp_ns": self.timestamp_ns,
            "confidence": self.confidence,
            "signals": {
                name: {
                    "value": sig.value,
                    "unit": sig.unit,
                    "raw_value": sig.raw_value,
                    "is_valid": sig.is_valid,
                    "status": sig.status.value if hasattr(sig.status, "value") else str(sig.status),
                }
                for name, sig in self.signals.items()
            },
        }


class BaseOemDecoder(abc.ABC):
    """Abstract Base Class for Heavy-Duty Commercial Vehicle OEM Decoders."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Return the manufacturer identifier string."""
        ...

    @property
    @abc.abstractmethod
    def supported_pgns(self) -> set[int]:
        """Return the set of J1939 PGNs supported by this decoder."""
        ...

    def supports_pgn(self, pgn: int) -> bool:
        """Check if this decoder supports the given PGN."""
        return pgn in self.supported_pgns

    @abc.abstractmethod
    def decode(
        self,
        frame: CanFrame,
        pgn: int,
        sa: int,
        da: int | None = None,
    ) -> OemDecodedPayload | None:
        """Decode a CAN frame into structured physical signals."""
        ...


class OemJ1939Registry:
    """Master registry and routing engine for OEM Proprietary J1939 messages.

    Routes Proprietary A (PGN 61184 / 0xEF00) and Proprietary B (PGN 65280-65535 / 0xFF00-0xFFFF)
    frames to OEM decoders (Cummins, Caterpillar, Scania, Volvo, Detroit Diesel, Mercedes Actros).

    REVIEW (HIGH-7): OEM attribution no longer trusts "first decoder that
    accepts the PGN". A decoder match is CONFIRMED when the frame's Source
    Address is backed by a NAME claim (PGN 60928) whose Manufacturer Code
    matches the OEM (see OEM_NAME_MANUFACTURER_CODES). Unconfirmed matches
    surface with confidence="LOW" and never displace a confirmed one.
    """

    PROPRIETARY_A_PGN: int = 61184  # 0xEF00
    PROPRIETARY_A2_PGN: int = 126720  # 0x1EF00
    PROPRIETARY_B_START: int = 65280  # 0xFF00
    PROPRIETARY_B_END: int = 65535  # 0xFFFF

    def __init__(self, decoders: list[BaseOemDecoder] | None = None) -> None:
        self._decoders: dict[str, BaseOemDecoder] = {}
        self._pgn_to_decoders: dict[int, list[BaseOemDecoder]] = {}
        # REVIEW (HIGH-7): SA -> NAME-manufacturer-code learned from PGN
        # 60928 Address Claim frames. Feeds decoder confirmation below.
        self._sa_name_codes: dict[int, int] = {}

        if decoders is not None:
            for dec in decoders:
                self.register_decoder(dec)
        else:
            self._register_default_decoders()

    PGN_ADDRESS_CLAIM: int = 60928  # 0xEE00 — J1939-81 Address Claimed

    def record_address_claim(self, frame: CanFrame) -> None:
        """Record a PGN 60928 (0xEE00) Address Claim for SA-based OEM confirmation.

        Feed every observed Address Claim frame here (e.g. from the RX
        pipeline / AddressClaimEngine table). The 64-bit NAME is decoded
        with J1939Name.from_bytes and its Manufacturer Code (bits 31..21)
        is stored against the claim's Source Address. Frames of other PGNs
        are ignored. Fail-closed: a malformed claim (payload < 8 bytes)
        is dropped, never guessed.
        """
        if not frame.is_extended or len(frame.data) < 8:
            return
        pgn, sa, _da, _prio = parse_j1939_id(frame.arbitration_id)
        if pgn != self.PGN_ADDRESS_CLAIM:
            return
        from src.protocols.j1939.address_claim import J1939Name

        try:
            name = J1939Name.from_bytes(bytes(frame.data[:8]))
        except Exception:
            return
        self._sa_name_codes[sa] = name.manufacturer_code

    def _decoder_confirms_sa(self, decoder: BaseOemDecoder, sa: int) -> bool | None:
        """NAME/SA confirmation for a decoder match (HIGH-7).

        Returns True when the SA's recorded NAME manufacturer code matches
        this decoder's OEM; False when a recorded claim contradicts it
        (another OEM's code — a misattributed / spoofed source); None when
        no claim has been observed for this SA (unknown, not fabricated).
        """
        codes = OEM_NAME_MANUFACTURER_CODES.get(decoder.name)
        if not codes:
            return None
        observed = self._sa_name_codes.get(sa)
        if observed is None:
            return None
        return observed in codes

    @staticmethod
    def _apply_attribution_confidence(decoded: OemDecodedPayload, confirmed: bool | None) -> None:
        """Stamp attribution confidence (never fabricate an OEM identity)."""
        if confirmed is True:
            # Confirmed via NAME claim — attribution is grounded, but keep
            # any explicit LOW already stamped (e.g. hinted-but-unknown PGN).
            return
        decoded.confidence = "LOW"

    def _register_default_decoders(self) -> None:
        """Lazily import and register built-in OEM decoders."""
        from src.protocols.j1939.oem.actros import ActrosDecoder
        from src.protocols.j1939.oem.caterpillar import CaterpillarDecoder
        from src.protocols.j1939.oem.cummins import CumminsDecoder
        from src.protocols.j1939.oem.detroit import DetroitDecoder
        from src.protocols.j1939.oem.scania import ScaniaDecoder
        from src.protocols.j1939.oem.volvo import VolvoDecoder

        for decoder in [
            CumminsDecoder(),
            CaterpillarDecoder(),
            ScaniaDecoder(),
            VolvoDecoder(),
            DetroitDecoder(),
            ActrosDecoder(),
        ]:
            self.register_decoder(decoder)

    def register_decoder(self, decoder: BaseOemDecoder) -> None:
        """Register a new or custom OEM decoder."""
        key = decoder.name.lower()
        self._decoders[key] = decoder
        for pgn in decoder.supported_pgns:
            self._pgn_to_decoders.setdefault(pgn, [])
            if decoder not in self._pgn_to_decoders[pgn]:
                self._pgn_to_decoders[pgn].append(decoder)
        logger.debug("Registered OEM J1939 decoder", extra={"oem": decoder.name, "pgns": len(decoder.supported_pgns)})

    def unregister_decoder(self, name: str) -> None:
        """Remove a decoder by name."""
        key = name.lower()
        decoder = self._decoders.pop(key, None)
        if decoder:
            for pgn, dec_list in list(self._pgn_to_decoders.items()):
                if decoder in dec_list:
                    dec_list.remove(decoder)
                if not dec_list:
                    del self._pgn_to_decoders[pgn]

    def get_decoder(self, name: str) -> BaseOemDecoder | None:
        """Retrieve a registered decoder by name."""
        return self._decoders.get(name.lower())

    def list_decoders(self) -> list[str]:
        """List registered decoder names."""
        return [d.name for d in self._decoders.values()]

    def is_proprietary_pgn(self, pgn: int) -> bool:
        """Check if a PGN falls into J1939 Proprietary A or B ranges."""
        return (
            pgn == self.PROPRIETARY_A_PGN
            or pgn == self.PROPRIETARY_A2_PGN
            or (self.PROPRIETARY_B_START <= pgn <= self.PROPRIETARY_B_END)
        )

    def decode_frame(
        self,
        frame: CanFrame,
        manufacturer_hint: str | None = None,
    ) -> OemDecodedPayload | None:
        """Route and decode an incoming CAN frame into physical OEM signals.

        Args:
            frame: Raw 29-bit CAN frame
            manufacturer_hint: Optional OEM name filter ("Cummins", "Scania", etc.)

        Returns:
            OemDecodedPayload if decoded successfully, else None.
        """
        if not frame.is_extended:
            return None

        pgn, sa, da, _ = parse_j1939_id(frame.arbitration_id)

        # Candidate decoders for this PGN (hint first, then registration).
        hinted: BaseOemDecoder | None = None
        if manufacturer_hint:
            hinted = self.get_decoder(manufacturer_hint)
            if hinted and not hinted.supports_pgn(pgn):
                hinted = None

        candidate_decoders = self._pgn_to_decoders.get(pgn, [])
        if not candidate_decoders:
            # Proprietary A (PGN 61184) is every OEM's shared private
            # channel — fall back to all decoders (payload-match required).
            if pgn == self.PROPRIETARY_A_PGN:
                candidate_decoders = list(self._decoders.values())
            else:
                return None
        # Hinted decoder tried first when it is a candidate for this PGN.
        if hinted is not None and hinted in candidate_decoders:
            candidate_decoders = [hinted, *[d for d in candidate_decoders if d is not hinted]]

        # REVIEW (HIGH-7): attribution via SA + NAME claim. A confirmed
        # match (NAME Manufacturer Code == decoder OEM) outranks any
        # unconfirmed payload-pattern match; a contradicted decoder
        # (NAME says another OEM) is skipped entirely. Unconfirmed
        # matches still surface — stamped confidence="LOW" (1-L4) —
        # because the payload pattern matched; identity is not guessed.
        fallback: OemDecodedPayload | None = None
        fallback_decoder: BaseOemDecoder | None = None
        for decoder in candidate_decoders:
            try:
                decoded = decoder.decode(frame, pgn, sa, da)
                if decoded is None:
                    continue
                confirmed = self._decoder_confirms_sa(decoder, sa)
                if confirmed is False:
                    logger.debug(
                        "Decoder match contradicted by NAME claim — skipped",
                        extra={"decoder": decoder.name, "sa": sa, "pgn": hex(pgn)},
                    )
                    continue
                if confirmed is True:
                    self._apply_attribution_confidence(decoded, True)
                    return decoded
                if fallback is None:
                    fallback = decoded
                    fallback_decoder = decoder
            except Exception as exc:
                logger.debug(
                    "Decoder failed for frame",
                    extra={"decoder": decoder.name, "pgn": hex(pgn), "error": str(exc)},
                )

        if fallback is not None:
            # No NAME-confirmed match: keep the first payload-pattern
            # match, explicitly marked LOW confidence (attribution is a
            # coincidence of registration order, not identification).
            # Exception: an operator hint (manufacturer_hint) is explicit
            # operator grounding of the identity — not stamped LOW.
            if fallback_decoder is not hinted:
                self._apply_attribution_confidence(fallback, False)
            if fallback_decoder is not None:
                logger.debug(
                    "OEM match unconfirmed by NAME claim",
                    extra={"decoder": fallback_decoder.name, "sa": sa, "pgn": hex(pgn)},
                )
            return fallback

        return None

    def decode_payload(
        self,
        pgn: int,
        data: bytes,
        sa: int = 0,
        da: int | None = None,
        manufacturer_hint: str | None = None,
        timestamp_ns: int | None = None,
        channel_id: str = "oem_j1939",
    ) -> OemDecodedPayload | None:
        """Convenience method to decode directly from PGN and raw data bytes."""
        can_id = build_j1939_id(pgn=pgn, sa=sa, da=da)
        frame = CanFrame.create(
            channel_id=channel_id,
            arbitration_id=can_id,
            data=data,
            is_extended=True,
            timestamp_ns=timestamp_ns,
        )
        return self.decode_frame(frame, manufacturer_hint=manufacturer_hint)
