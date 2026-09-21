"""SAE J1939-81 Address Claiming State Machine and 64-Bit NAME Specification.

Complies with SAE J1939-81 and MASTER_PLAN.md Section 4.2.
"""

from __future__ import annotations

import threading
import time
from collections import UserDict
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar

from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame
from src.protocols.j1939.pgn import parse_j1939_id

logger = get_logger("protocols.j1939.address_claim")

PGN_ADDRESS_CLAIM: int = 60928  # 0xEE00
# REVIEW 1-H4 (HIGH): J1939-81 §4 mandatory PGNs previously unhandled here.
PGN_REQUEST_PGN: int = 59904  # 0xEA00 — Request PGN
PGN_COMMANDED_ADDRESS: int = 65240  # 0xFED8 — Commanded Address
NULL_ADDRESS: int = 254  # 0xFE
GLOBAL_ADDRESS: int = 255  # 0xFF


class AddressClaimState(Enum):
    """J1939-81 Address Claiming lifecycle states."""

    UNINITIALIZED = "UNINITIALIZED"
    CLAIMING = "CLAIMING"
    CLAIMED = "CLAIMED"
    CANNOT_CLAIM = "CANNOT_CLAIM"


# L3 (verified OPEN): J1939Name silently TRUNCATED out-of-range fields on
# encode (`industry_group=99` was masked to 3 bits and became 3), so a
# caller with a typo'd 10-bit manufacturer code silently transmitted a claim
# for a DIFFERENT manufacturer — a spoofing-shaped defect, not a cosmetic
# one. Each 64-bit NAME subfield is now range-checked at construction.
NAME_FIELD_WIDTHS: dict[str, int] = {
    "industry_group": 3,
    "vehicle_system_instance": 4,
    "vehicle_system": 7,
    "reserved": 1,
    "function": 8,
    "function_instance": 5,
    "ecu_instance": 3,
    "manufacturer_code": 11,
    "identity_number": 21,
}


@dataclass(slots=True, frozen=True)
class J1939Name:
    """SAE J1939-81 64-bit NAME composed of all 10 standard subfields."""

    arbitrary_address_capable: bool  # 1 bit (Bit 63)
    industry_group: int  # 3 bits (Bits 62..60)
    vehicle_system_instance: int  # 4 bits (Bits 59..56)
    vehicle_system: int  # 7 bits (Bits 55..49)
    reserved: int = 0  # 1 bit (Bit 48, default 0)
    function: int = 0  # 8 bits (Bits 47..40)
    function_instance: int = 0  # 5 bits (Bits 39..35)
    ecu_instance: int = 0  # 3 bits (Bits 34..32)
    manufacturer_code: int = 0  # 11 bits (Bits 31..21)
    identity_number: int = 0  # 21 bits (Bits 20..0)

    def __post_init__(self) -> None:
        """L3 (verified OPEN): validate every subfield's bit width.

        Fail closed rather than mask: `to_int64()` used to drop the high bits
        of an out-of-range field, so a bad NAME was encoded as a plausible
        but WRONG one. `arbitrary_address_capable` is typed `bool`, so any
        truthy/falsy value is already unambiguous and needs no range check.
        """
        if not isinstance(self.arbitrary_address_capable, bool):
            raise ValueError(
                "J1939Name.arbitrary_address_capable must be a bool, "
                f"got {type(self.arbitrary_address_capable).__name__}"
            )
        for field_name, width in NAME_FIELD_WIDTHS.items():
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"J1939Name.{field_name} must be an int, got {type(value).__name__}")
            max_value = (1 << width) - 1
            if not 0 <= value <= max_value:
                raise ValueError(
                    f"J1939Name.{field_name} {value} out of range for its {width}-bit field "
                    f"(0..{max_value}) — refusing to truncate"
                )

    def to_int64(self) -> int:
        """Encode 10 subfields into a single 64-bit unsigned integer."""
        val = 0
        val |= (1 if self.arbitrary_address_capable else 0) << 63
        val |= (self.industry_group & 0x07) << 60
        val |= (self.vehicle_system_instance & 0x0F) << 56
        val |= (self.vehicle_system & 0x7F) << 49
        val |= (self.reserved & 0x01) << 48
        val |= (self.function & 0xFF) << 40
        val |= (self.function_instance & 0x1F) << 35
        val |= (self.ecu_instance & 0x07) << 32
        val |= (self.manufacturer_code & 0x07FF) << 21
        val |= self.identity_number & 0x1FFFFF
        return val

    def to_bytes(self) -> bytes:
        """Encode 64-bit NAME to 8-byte Little-Endian binary payload."""
        return self.to_int64().to_bytes(8, byteorder="little")

    @classmethod
    def from_int64(cls, val: int) -> J1939Name:
        """Decode 64-bit integer into 10-subfield J1939Name."""
        return cls(
            arbitrary_address_capable=bool((val >> 63) & 0x01),
            industry_group=(val >> 60) & 0x07,
            vehicle_system_instance=(val >> 56) & 0x0F,
            vehicle_system=(val >> 49) & 0x7F,
            reserved=(val >> 48) & 0x01,
            function=(val >> 40) & 0xFF,
            function_instance=(val >> 35) & 0x1F,
            ecu_instance=(val >> 32) & 0x07,
            manufacturer_code=(val >> 21) & 0x07FF,
            identity_number=val & 0x1FFFFF,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> J1939Name:
        """Decode 8-byte binary payload into J1939Name."""
        if len(data) < 8:
            raise ValueError(f"J1939 NAME requires 8 bytes, got {len(data)}")
        val = int.from_bytes(data[:8], byteorder="little")
        return cls.from_int64(val)


class _ExpiringAddressTable(UserDict[int, J1939Name]):
    """SA -> (NAME, expiry) table with a NAME-shaped read view.

    REVIEW hardening: entries carry a 60 s monotonic TTL. Internal code
    reads/writes (NAME, expiry) tuples; legacy readers (`in`, `[]`,
    `.get()`) transparently see the plain J1939Name (or None), and legacy
    `table[sa] = name` writes stamp a fresh TTL — so existing tests and
    callers keep working while expiry/rate-limit stay enforced on the RX
    path. Expired entries are pruned lazily on read.

    L1 (verified OPEN): this class used to extend `dict` directly while only
    overriding `__contains__`/`__getitem__`/`get`/`__setitem__`/`items`/
    `values`. Every OTHER dict API — `keys()`, `__iter__`, `pop()`,
    `clear()`, `update()`, `setdefault()`, `len()` — leaked the raw
    `(NAME, expiry)` tuples to the caller, so an external reader iterating
    the table saw tuples where a `J1939Name` was documented. Deriving from
    `UserDict` puts the whole Mapping surface behind the overridable
    `__getitem__`/`__setitem__`/`__contains__`/`__delitem__` hooks and makes
    the remaining unoverridden methods (`setdefault`, `pop`, `update`,
    `popitem`, `copy`) go through them too.
    """

    def _prune(self) -> None:
        try:
            now = time.monotonic()
        except Exception:
            return
        for sa, entry in list(self.data.items()):
            if isinstance(entry, tuple) and len(entry) == 2:
                _name, expiry = entry
                try:
                    if float(expiry) <= now:
                        self.data.pop(sa, None)
                except Exception:
                    continue

    @staticmethod
    def _unwrap(entry: object) -> J1939Name | None:
        if isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[0], J1939Name):
            return entry[0]
        if isinstance(entry, J1939Name):
            return entry
        return None

    def __contains__(self, key: object) -> bool:
        self._prune()
        return self._unwrap(self.data.get(key)) is not None

    def __getitem__(self, key: int) -> J1939Name:
        self._prune()
        name = self._unwrap(self.data.get(key))
        if name is None:
            raise KeyError(key)
        return name

    def get(self, key: int, default: J1939Name | None = None) -> J1939Name | None:  # type: ignore[override]
        self._prune()
        name = self._unwrap(self.data.get(key))
        return name if name is not None else default

    def __setitem__(self, key: int, value: J1939Name | tuple[J1939Name, float]) -> None:
        if isinstance(value, J1939Name):
            self.data[key] = (value, time.monotonic() + AddressClaimEngine.ADDRESS_TABLE_TTL_S)
        else:
            self.data[key] = value

    def __iter__(self) -> Any:
        """L1: iterate ONLY over live (pruned) SAs — never over raw tuples."""
        self._prune()
        return iter(list(self.data.keys()))

    def __len__(self) -> int:
        """L1: count only live entries, not expired-but-unpruned ones."""
        self._prune()
        return len(self.data)

    def keys(self) -> Any:  # type: ignore[override]
        self._prune()
        return list(self.data.keys())

    def items(self) -> Any:  # type: ignore[override]
        self._prune()
        for sa, entry in list(self.data.items()):
            name = self._unwrap(entry)
            if name is not None:
                yield sa, name

    def values(self) -> Any:  # type: ignore[override]
        self._prune()
        for _sa, entry in list(self.data.items()):
            name = self._unwrap(entry)
            if name is not None:
                yield name


class AddressClaimEngine:
    """J1939-81 Dynamic Address Claiming State Machine."""

    DEFAULT_PREFERRED_ADDRESS: ClassVar[int] = 0xF9  # 249 (Diagnostic tool #1)
    FALLBACK_ADDRESS_RANGE: ClassVar[tuple[int, ...]] = tuple(range(128, 248))

    # REVIEW hardening: address-table entry TTL + per-SA claim rate
    # limit. Without expiry 121 spoofed claims permanently exhaust the
    # fallback range (persistent CANNOT_CLAIM DoS).
    ADDRESS_TABLE_TTL_S: ClassVar[float] = 60.0
    CLAIM_RATE_LIMIT_PER_SA: ClassVar[int] = 8
    CLAIM_RATE_WINDOW_S: ClassVar[float] = 1.0

    def __init__(
        self,
        name: J1939Name,
        preferred_address: int = DEFAULT_PREFERRED_ADDRESS,
        channel_id: str = "j1939_ch0",
    ) -> None:
        self.name = name
        self.preferred_address = preferred_address
        self.current_address = preferred_address
        self.channel_id = channel_id
        self.state = AddressClaimState.UNINITIALIZED
        # SA -> (NAME, monotonic_expiry). Stale entries are pruned on
        # every RX and on fallback scans.
        self._address_table: _ExpiringAddressTable = _ExpiringAddressTable()
        # SA -> [window_start_monotonic, count] claim rate bucket.
        self._claim_rate: dict[int, list[float]] = {}
        self._claim_timer: threading.Timer | None = None  # F-31: 250ms claim window
        # P6: address table + current address + state mutate from the rx path
        # and the timer callback concurrently; RLock because the confirmation
        # timer callback re-enters engine state.
        self._engine_lock = threading.RLock()
        # M-26 (P2-7): generation counter + arming deadline. A Timer callback
        # already queued behind _engine_lock contention could no longer be
        # cancelled by Timer.cancel(); without the generation check it would
        # instantly finalize a FRESH claim the engine just started (or an
        # address it moved to after contention) before its window elapsed.
        self._claim_generation: int = 0
        self._claim_window_deadline_ns: int = 0

    @property
    def is_address_claimed(self) -> bool:
        """Return True if an address is successfully claimed and active for TX."""
        return self.state == AddressClaimState.CLAIMED and self.current_address != NULL_ADDRESS

    def start_claiming(self, auto_arm_timer: bool = True) -> CanFrame:
        """Initiate address claiming sequence and return the Address Claim frame to transmit."""
        with self._engine_lock:
            self.current_address = self.preferred_address
            self.state = AddressClaimState.CLAIMING

            # Construct J1939 29-bit CAN ID: Priority 6, PGN 60928 (0xEE00), DA 255, SA
            # 0x18EEFF00 | SA
            can_id = 0x18EEFF00 | (self.current_address & 0xFF)
            claim_frame = CanFrame.create(
                channel_id=self.channel_id,
                arbitration_id=can_id,
                data=self.name.to_bytes(),
                is_extended=True,
                direction="tx",
            )

            # F-31: J1939-81 claim window — if no contention arrives within 250 ms
            # the claim finalizes automatically (daemon timer).
            if auto_arm_timer:
                self._arm_claim_confirmation_timer()

            logger.info(
                "Broadcasting Address Claim",
                extra={"address": self.current_address, "name_int": hex(self.name.to_int64())},
            )
            return claim_frame

    def on_claim_transmitted(self) -> None:
        """Explicitly arm claim window timer upon confirmed physical transmission."""
        with self._engine_lock:
            if self.state == AddressClaimState.CLAIMING:
                self._arm_claim_confirmation_timer()

    def on_claim_transmission_failed(self) -> None:
        """Cancel claim timer and revert state if transmission fails or is rejected."""
        with self._engine_lock:
            self.cancel_pending_claim_timer()
            if self.state == AddressClaimState.CLAIMING:
                self.state = AddressClaimState.UNINITIALIZED

    CLAIM_CONFIRM_WINDOW_MS: ClassVar[float] = 250.0  # F-31 (J1939-81 250 ms window)

    def _arm_claim_confirmation_timer(self) -> None:
        """Start the daemon-thread timer that finalizes an uncontested claim (F-31).

        M-26 (P2-7): every arming bumps the generation and records the wall
        deadline; confirm_claimed() rejects callbacks whose generation does
        not match or whose window has not actually elapsed yet — a stale
        (uncancellable, lock-queued) Timer must never confirm a fresh claim
        early.
        """
        self._claim_generation += 1
        self._claim_window_deadline_ns = time.monotonic_ns() + int(self.CLAIM_CONFIRM_WINDOW_MS * 1_000_000)
        if self._claim_timer is not None:
            self._claim_timer.cancel()
        timer = threading.Timer(
            self.CLAIM_CONFIRM_WINDOW_MS / 1000.0, self._confirm_claimed_guarded, args=(self._claim_generation,)
        )
        self._claim_timer = timer
        timer.daemon = True
        timer.start()

    def _confirm_claimed_guarded(self, generation: int) -> None:
        """Timer entry: enforce the generation/deadline before finalizing."""
        with self._engine_lock:
            # A newer claim superseded this timer (re-arm after contention or
            # a fresh start_claiming) — ignore.
            if generation != self._claim_generation:
                return
            # Early-fire guard: a Timer scheduled just before a re-arm can
            # fire before the new window has elapsed (scheduling jitter);
            # the deadline check keeps the 250 ms window honest. The 25 ms
            # tolerance absorbs OS timer resolution (Windows ~15 ms) so a
            # legitimately-elapsed window is never rejected.
            if time.monotonic_ns() < self._claim_window_deadline_ns - 25_000_000:
                return
            self._finalize_claim()

    def cancel_pending_claim_timer(self) -> None:
        """Cancel any in-flight claim confirmation timer (contention received)."""
        # M-26 (P2-7): bump the generation so an already-queued callback is
        # rejected even though Timer.cancel() cannot unqueue it.
        self._claim_generation += 1
        if self._claim_timer is not None:
            self._claim_timer.cancel()
            self._claim_timer = None

    def handle_rx_frame(self, frame: CanFrame) -> CanFrame | None:
        """Process incoming frame. If Address Claim contention occurs, handles arbitration."""
        if not frame.is_extended or len(frame.data) < 8:
            return None

        # Extract PGN and Source Address from 29-bit CAN ID with PDU1/PDU2
        # distinction — M-12 (P2-6): shared parser preserves EDP/DP bits;
        # the old hand-rolled math dropped EDP, mis-deriving the PGN for
        # EDP-set frames.
        pgn, source_address, _da, _priority = parse_j1939_id(frame.arbitration_id)

        # REVIEW 1-H4 (HIGH): SAE J1939-81 §4 — a claimed node MUST answer
        # a Request PGN (59904) for its own Address Claimed (60928) by
        # re-broadcasting the claim. Without this, bridges/dataloggers that
        # dictionary-scan the network never learn we exist and may suggest
        # our address to another node.
        #
        # H2 (verified OPEN): the request's DESTINATION ADDRESS (PS octet,
        # exposed as `_da` by the shared parser) was discarded, so every
        # Request-for-Address-Claimed on the bus was answered — including
        # point-to-point requests addressed to OTHER nodes. On a real bus
        # that is a spurious claim broadcast per request, and it can hand
        # our NAME to a node that never asked. A PDU1 request is answered
        # only when it is addressed to us, broadcast (0xFF) or
        # globally-addressed (None for a PDU2-parsed id).
        if pgn == PGN_REQUEST_PGN and len(frame.data) >= 3:
            requested_pgn = int.from_bytes(bytes(frame.data[0:3]), byteorder="little")
            if requested_pgn != PGN_ADDRESS_CLAIM:
                return None
            # H2: drop requests for another node's address claim outright.
            if _da not in (None, GLOBAL_ADDRESS, self.current_address):
                logger.debug(
                    "Ignoring Request PGN for Address Claimed addressed to another node",
                    extra={"requester": source_address, "da": _da, "sa": self.current_address},
                )
                return None
            if self.state == AddressClaimState.CLAIMED:
                can_id = 0x18EEFF00 | (self.current_address & 0xFF)
                logger.debug(
                    "Answering Request PGN for Address Claimed",
                    extra={"requester": source_address, "sa": self.current_address},
                )
                return CanFrame.create(
                    channel_id=self.channel_id,
                    arbitration_id=can_id,
                    data=self.name.to_bytes(),
                    is_extended=True,
                    direction="tx",
                )
            if self.state == AddressClaimState.CANNOT_CLAIM:
                # H2 (verified OPEN): J1939-81 §4 — a node that holds no
                # address MUST answer a Request for Address Claimed with the
                # Cannot-Claim broadcast (SA 0xFE, i.e. the Null Address), so
                # the requester learns the address is free. The old handler
                # only ever answered from CLAIMED, so this reply was never
                # emitted and address-arbitration peers were left blind.
                can_id = 0x18EEFF00 | NULL_ADDRESS
                logger.info(
                    "Answering Request PGN with Cannot-Claim (Null Address 0xFE)",
                    extra={"requester": source_address},
                )
                return CanFrame.create(
                    channel_id=self.channel_id,
                    arbitration_id=can_id,
                    data=self.name.to_bytes(),
                    is_extended=True,
                    direction="tx",
                )
            return None

        # REVIEW 1-H4 (HIGH): SAE J1939-81 — Commanded Address (PGN 65240)
        # carries NAME + new source address; a node whose NAME matches MUST
        # adopt the new address and re-claim. Frames for other NAMEs are
        # ignored (other target).
        if pgn == PGN_COMMANDED_ADDRESS and len(frame.data) == 9:
            commanded_name = J1939Name.from_bytes(frame.data[0:8])
            if commanded_name.to_int64() != self.name.to_int64():
                return None  # Commanded Address targets a different node
            new_address = frame.data[8]
            if not (0 <= new_address <= 0xFD):
                logger.warning(
                    "Ignoring Commanded Address with reserved/invalid SA value",
                    extra={"new_sa": new_address},
                )
                return None
            with self._engine_lock:
                logger.info(
                    "Commanded Address received — adopting new SA",
                    extra={"old_sa": self.current_address, "new_sa": new_address},
                )
                self.preferred_address = new_address
            return self.start_claiming()

        # Check if frame is an Address Claim message (PGN 60928 / 0xEE00)
        if pgn != PGN_ADDRESS_CLAIM:
            return None

        other_name = J1939Name.from_bytes(frame.data)

        # P6: a peer echoing our exact 64-bit NAME is our own claim reflected
        # back (or a duplicate transmitter) — not contention. Re-asserting
        # against it would ping-pong forever and could evict us from a valid
        # address on a single bus glitch.
        if other_name.to_int64() == self.name.to_int64():
            logger.debug("Ignoring address claim with identical NAME (self-echo)", extra={"sa": source_address})
            return None

        with self._engine_lock:
            # REVIEW hardening: per-SA rate-limit first (claim flood),
            # then expiry-pruned table write (60 s TTL).
            now = time.monotonic()
            if not self._check_claim_rate_limit(source_address, now):
                logger.warning(
                    "Address claim rate-limited (flood/spoof?)",
                    extra={"sa": source_address},
                )
                return None
            self._prune_address_table(now)
            # L1: write the raw (NAME, expiry) tuple through the internal
            # `data` mapping — `__setitem__` would re-stamp the TTL and the
            # write-through-`__setitem__` path is the legacy convenience API.
            self._address_table.data[source_address] = (other_name, now + self.ADDRESS_TABLE_TTL_S)
            return self._handle_contention_locked(source_address, other_name)

    def _prune_address_table(self, now: float) -> None:
        """Drop expired (SA -> NAME) entries and stale claim-rate buckets.

        L2 (verified OPEN): `_claim_rate` buckets were never pruned, so a
        spoofed-claim sweep left one entry per SA forever. The table is
        bounded by the SA space (<=256) so this is hygiene rather than a
        hole, but it is unbounded in *time* and the prune path already runs
        on every RX. Caller holds _engine_lock.
        """
        expired = [
            sa
            for sa, entry in list(self._address_table.data.items())
            if isinstance(entry, tuple)
            and len(entry) == 2
            and isinstance(entry[1], (int, float))
            and float(entry[1]) <= now
        ]
        for sa in expired:
            self._address_table.data.pop(sa, None)

        # L2: rate buckets older than 2x the window can never influence a
        # future decision (the window already reset them), so drop them.
        stale_after = 2.0 * self.CLAIM_RATE_WINDOW_S
        stale_sas = [
            sa
            for sa, bucket in self._claim_rate.items()
            if isinstance(bucket, list)
            and len(bucket) == 2
            and isinstance(bucket[0], (int, float))
            and (now - float(bucket[0])) > stale_after
        ]
        for sa in stale_sas:
            self._claim_rate.pop(sa, None)

    def _check_claim_rate_limit(self, sa: int, now: float) -> bool:
        """Per-SA claim rate gate. Caller holds _engine_lock.

        Returns True when the claim may be recorded, False when the SA
        exceeded CLAIM_RATE_LIMIT_PER_SA claims within CLAIM_RATE_WINDOW_S
        (spoof/flood — drop the frame).
        """
        bucket = self._claim_rate.get(sa)
        if bucket is None:
            self._claim_rate[sa] = [now, 1.0]
            return True
        window_start, count = bucket
        if (now - window_start) > self.CLAIM_RATE_WINDOW_S:
            bucket[0] = now
            bucket[1] = 1.0
            return True
        if count >= self.CLAIM_RATE_LIMIT_PER_SA:
            return False
        bucket[1] = count + 1.0
        return True

    def _live_address_table(self) -> dict[int, J1939Name]:
        """Pruned SA -> NAME snapshot for fallback scans. Holds the lock."""
        now = time.monotonic()
        self._prune_address_table(now)
        # L2: keep the claim-rate window fresh for the fallback scan too.
        return dict(self._address_table.items())

    def _handle_contention_locked(
        self, source_address: int, other_name: J1939Name
    ) -> CanFrame | None:
        """Contention + fallback path. Caller holds _engine_lock."""
        # If claim is from another SA, no collision with our current SA
        # P2-7: a Null Address (254) Cannot-Claim broadcast is never a
        # contention against our working address — other nodes are in
        # distress, not competing for this SA.
        if source_address != self.current_address or source_address == NULL_ADDRESS:
            return None

        # Contention detected on our address! Compare 64-bit NAMEs
        # F-31: contention inside the window cancels auto-confirmation
        self.cancel_pending_claim_timer()
        my_val = self.name.to_int64()
        other_val = other_name.to_int64()

        if my_val < other_val:
            # We have higher priority (lower numerical NAME). Re-assert our address!
            logger.info("Defending address claim against higher numerical NAME", extra={"sa": self.current_address})
            self._arm_claim_confirmation_timer()
            can_id = 0x18EEFF00 | (self.current_address & 0xFF)
            return CanFrame.create(
                channel_id=self.channel_id,
                arbitration_id=can_id,
                data=self.name.to_bytes(),
                is_extended=True,
                direction="tx",
            )

        # We lost contention.
        logger.warning("Lost address claim contention", extra={"sa": self.current_address})
        if self.name.arbitrary_address_capable:
            # REVIEW hardening: scan the EXPIRY-PRUNED table so stale
            # spoofed entries cannot permanently exhaust the fallback
            # range (persistent CANNOT_CLAIM DoS).
            live_table = self._live_address_table()
            # Try next available address
            for candidate_sa in self.FALLBACK_ADDRESS_RANGE:
                if candidate_sa not in live_table:
                    self.current_address = candidate_sa
                    # P2-7: a fallback claim is a FRESH claim — the state
                    # must return to CLAIMING so is_address_claimed stays
                    # False until the 250 ms contention window confirms.
                    # (The old code left state==CLAIMED, allowing TX on an
                    # unconfirmed address — a J1939-81 violation.)
                    self.state = AddressClaimState.CLAIMING
                    can_id = 0x18EEFF00 | (self.current_address & 0xFF)
                    logger.info("Attempting next fallback address", extra={"new_sa": candidate_sa})
                    # The fallback claim is a fresh claim: it needs its own
                    # 250 ms contention window or it can never confirm and
                    # is_address_claimed stays False (J1939 TX permanently dead).
                    self._arm_claim_confirmation_timer()
                    return CanFrame.create(
                        channel_id=self.channel_id,
                        arbitration_id=can_id,
                        data=self.name.to_bytes(),
                        is_extended=True,
                        direction="tx",
                    )

            # Cannot claim any address -> Send 'Cannot Claim' (SA = 254)
            self.current_address = NULL_ADDRESS
            self.state = AddressClaimState.CANNOT_CLAIM
            can_id = 0x18EEFF00 | NULL_ADDRESS
            logger.error("Transitioned to CANNOT_CLAIM (Null Address 0xFE)")
            return CanFrame.create(
                channel_id=self.channel_id,
                arbitration_id=can_id,
                data=self.name.to_bytes(),
                is_extended=True,
                direction="tx",
            )

    def confirm_claimed(self) -> None:
        """Call after claim contention timeout (250 ms) without collision to finalize claim.

        M-26 (P2-7): manual confirmation (tests / explicit callers) bypasses
        the generation guard but still requires an ELAPSED window — calling it
        during a live claim window is a bug in the caller and is ignored
        until the window has actually expired.
        """
        with self._engine_lock:
            if time.monotonic_ns() < self._claim_window_deadline_ns:
                return
            self._finalize_claim()

    def _finalize_claim(self) -> None:
        """Internal state transition to CLAIMED (caller holds _engine_lock)."""
        if self.state == AddressClaimState.CLAIMING and self.current_address != NULL_ADDRESS:
            self.state = AddressClaimState.CLAIMED
            logger.info("Address Claim Confirmed", extra={"sa": self.current_address})
