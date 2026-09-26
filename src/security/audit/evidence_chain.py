# -*- coding: utf-8 -*-
"""RFC 4998 Evidence Record Syntax (ERS) Audit Chain & Merkle Tree.

Complies with:
- RFC 4998 (Evidence Record Syntax)
- ISO 26262 ASIL-B/D Fail-Closed Audit Trail Invariant
- Konsolide Keşif Raporu §D.6 (Kanıt zinciri ve test oracle'ları) & §H.3 (Aksiyon 32):
  "RFC 4998 kanıt zinciri — Merkle kökü + prev‖entry + düzenli çapraz imzalama;
   kırılma ⇒ SAFE_STATE + operatör alarmı. Denetim izinin DELETE edilebilir bir
   Loki akışı olmasına izin vermeyin."
"""

from __future__ import annotations

import hashlib
import hmac
import struct
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.core.contracts.ports import ClockProvider, SystemClockProvider
from src.core.errors import SecurityError
from src.core.logging import get_logger

logger = get_logger("security.audit.evidence_chain")

GENESIS_PREV_HASH = b"\x00" * 32


@dataclass(slots=True, frozen=True)
class EvidenceEntry:
    """Single immutable entry in the evidence audit chain."""

    sequence: int
    timestamp_ns: int
    payload: bytes
    prev_hash: bytes
    entry_hash: bytes

    def serialize(self) -> bytes:
        """Serialize entry to binary format."""
        # Format: <sequence: uint64><timestamp_ns: uint64><prev_hash: 32s><entry_hash: 32s><payload_len: uint32><payload: bytes>
        header = struct.pack(
            ">QQ32s32sI",
            self.sequence,
            self.timestamp_ns,
            self.prev_hash,
            self.entry_hash,
            len(self.payload),
        )
        return header + self.payload

    @classmethod
    def deserialize(cls, data: bytes) -> EvidenceEntry:
        """Deserialize entry from binary format."""
        header_size = struct.calcsize(">QQ32s32sI")
        if len(data) < header_size:
            raise SecurityError("Evidence entry data too short", code="EVIDENCE_CORRUPT")
        seq, ts_ns, prev_hash, entry_hash, payload_len = struct.unpack(
            ">QQ32s32sI", data[:header_size]
        )
        payload = data[header_size : header_size + payload_len]
        if len(payload) != payload_len:
            raise SecurityError("Evidence entry payload truncated", code="EVIDENCE_CORRUPT")
        return cls(
            sequence=seq,
            timestamp_ns=ts_ns,
            payload=payload,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )


def compute_entry_hash(key: bytes, prev_hash: bytes, seq: int, ts_ns: int, payload: bytes) -> bytes:
    """Calculate HMAC-SHA256 for an entry: HMAC(key, prev_hash || seq || ts_ns || payload)."""
    prefix = prev_hash + struct.pack(">QQ", seq, ts_ns)
    return hmac.new(key, prefix + payload, hashlib.sha256).digest()


class MerkleTree:
    """RFC 4998 Merkle Hash-Tree implementation for evidence records."""

    @staticmethod
    def hash_pair(left: bytes, right: bytes) -> bytes:
        """RFC 4998 internal node hash: SHA256(0x01 || left || right)."""
        return hashlib.sha256(b"\x01" + left + right).digest()

    @classmethod
    def compute_root(cls, leaves: list[bytes]) -> bytes:
        """Compute Merkle tree root from a list of leaf hashes."""
        if not leaves:
            return b"\x00" * 32
        current_level = list(leaves)
        while len(current_level) > 1:
            next_level: list[bytes] = []
            for i in range(0, len(current_level), 2):
                left = current_level[i]
                if i + 1 < len(current_level):
                    right = current_level[i + 1]
                else:
                    # RFC 4998 odd-leaf rule: duplicate right sibling
                    right = left
                next_level.append(cls.hash_pair(left, right))
            current_level = next_level
        return current_level[0]

    @classmethod
    def generate_proof(cls, leaves: list[bytes], index: int) -> list[tuple[bytes, bool]]:
        """Generate audit inclusion proof for leaf at `index`.

        Each proof step is (sibling_hash, is_right: bool).
        """
        if not (0 <= index < len(leaves)):
            raise IndexError("Leaf index out of range for Merkle proof")

        proof: list[tuple[bytes, bool]] = []
        current_level = list(leaves)
        curr_idx = index

        while len(current_level) > 1:
            level_len = len(current_level)
            if curr_idx % 2 == 0:
                # Leaf is left sibling
                if curr_idx + 1 < level_len:
                    sibling = current_level[curr_idx + 1]
                else:
                    sibling = current_level[curr_idx]
                proof.append((sibling, True))
            else:
                # Leaf is right sibling
                sibling = current_level[curr_idx - 1]
                proof.append((sibling, False))

            # Move to parent level
            next_level: list[bytes] = []
            for i in range(0, level_len, 2):
                left = current_level[i]
                right = current_level[i + 1] if i + 1 < level_len else left
                next_level.append(cls.hash_pair(left, right))
            current_level = next_level
            curr_idx //= 2

        return proof

    @classmethod
    def verify_proof(cls, leaf: bytes, proof: list[tuple[bytes, bool]], root: bytes) -> bool:
        """Verify an RFC 4998 inclusion proof from leaf to root."""
        current = leaf
        for sibling, is_right in proof:
            if is_right:
                current = cls.hash_pair(current, sibling)
            else:
                current = cls.hash_pair(sibling, current)
        return hmac.compare_digest(current, root)


class EvidenceChain:
    """Tamper-evident RFC 4998 Evidence Chain with fail-closed state latching."""

    def __init__(
        self,
        secret_key: bytes,
        clock: ClockProvider | None = None,
        on_broken_chain: Callable[[], None] | None = None,
    ) -> None:
        if len(secret_key) < 16:
            raise SecurityError("Evidence chain secret key too short (<16 bytes)", code="INSUFFICIENT_KEY_LENGTH")
        self._key = secret_key
        self._clock: ClockProvider = clock or SystemClockProvider()
        self._on_broken_chain = on_broken_chain
        self._entries: list[EvidenceEntry] = []
        self._is_broken: bool = False

    @property
    def is_broken(self) -> bool:
        return self._is_broken

    @property
    def entries(self) -> list[EvidenceEntry]:
        return list(self._entries)

    def append(self, payload: bytes) -> EvidenceEntry:
        """Append a new evidence record to the chain.

        Fails closed if the chain was previously marked broken or clock unavailable.
        """
        if self._is_broken:
            raise SecurityError(
                "Cannot append to a broken evidence chain (fail-closed).",
                code="EVIDENCE_CHAIN_LATCHED",
            )

        now_ns = self._clock.now_monotonic_ns()
        if now_ns <= 0:
            self._trigger_fail_closed("System clock provider returned non-positive timestamp")

        seq = len(self._entries)
        if seq == 0:
            prev_hash = GENESIS_PREV_HASH
        else:
            last = self._entries[-1]
            if now_ns < last.timestamp_ns:
                self._trigger_fail_closed("Clock rollback detected inside evidence chain")
            prev_hash = last.entry_hash

        entry_hash = compute_entry_hash(self._key, prev_hash, seq, now_ns, payload)
        entry = EvidenceEntry(
            sequence=seq,
            timestamp_ns=now_ns,
            payload=payload,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )
        self._entries.append(entry)
        return entry

    def verify_chain(self) -> bool:
        """Verify complete cryptographic continuity of all entries.

        Triggers fail-closed action if any tampering, sequence gap, or hash mismatch occurs.
        """
        if not self._entries:
            return True

        expected_prev = GENESIS_PREV_HASH
        last_ts = -1

        for i, entry in enumerate(self._entries):
            # 1. Monotonic sequence check
            if entry.sequence != i:
                return self._trigger_fail_closed(
                    f"Evidence chain sequence gap at index {i}: expected {i}, got {entry.sequence}"
                )

            # 2. Monotonic timestamp check
            if entry.timestamp_ns < last_ts:
                return self._trigger_fail_closed(
                    f"Evidence chain timestamp rollback at index {i}: {entry.timestamp_ns} < {last_ts}"
                )
            last_ts = entry.timestamp_ns

            # 3. Previous hash link check
            if not hmac.compare_digest(entry.prev_hash, expected_prev):
                return self._trigger_fail_closed(
                    f"Evidence chain prev_hash mismatch at sequence {i}"
                )

            # 4. Entry HMAC integrity check
            recomputed = compute_entry_hash(
                self._key,
                entry.prev_hash,
                entry.sequence,
                entry.timestamp_ns,
                entry.payload,
            )
            if not hmac.compare_digest(entry.entry_hash, recomputed):
                return self._trigger_fail_closed(
                    f"Evidence chain HMAC verification failed at sequence {i}"
                )

            expected_prev = entry.entry_hash

        return True

    def compute_merkle_root(self) -> bytes:
        """Compute RFC 4998 Merkle Tree root over all entry hashes."""
        leaf_hashes = [entry.entry_hash for entry in self._entries]
        return MerkleTree.compute_root(leaf_hashes)

    def generate_inclusion_proof(self, sequence: int) -> list[tuple[bytes, bool]]:
        """Generate inclusion proof for entry at `sequence`."""
        leaf_hashes = [entry.entry_hash for entry in self._entries]
        return MerkleTree.generate_proof(leaf_hashes, sequence)

    def create_checkpoint(self, operator_id: str = "system") -> dict[str, Any]:
        """Create a signed audit checkpoint manifest for the current session state."""
        self.verify_chain()
        root = self.compute_merkle_root()
        count = len(self._entries)
        last_hash = self._entries[-1].entry_hash.hex() if self._entries else GENESIS_PREV_HASH.hex()
        ts_ns = self._clock.now_monotonic_ns()

        manifest = {
            "format": "RFC4998-ERS-V1",
            "operator_id": operator_id,
            "entry_count": count,
            "merkle_root_hex": root.hex(),
            "latest_entry_hash_hex": last_hash,
            "checkpoint_timestamp_ns": ts_ns,
        }
        # Manifest integrity signature
        manifest_bytes = f"{manifest['format']}:{manifest['entry_count']}:{manifest['merkle_root_hex']}:{ts_ns}".encode("utf-8")
        manifest["signature_hex"] = hmac.new(self._key, manifest_bytes, hashlib.sha256).hexdigest()
        return manifest

    def _trigger_fail_closed(self, reason: str) -> bool:
        """Latch broken state and invoke fail-closed callback immediately."""
        self._is_broken = True
        logger.critical("EVIDENCE CHAIN BROKEN: %s -> Triggering SAFE_STATE", reason)
        if self._on_broken_chain is not None:
            try:
                self._on_broken_chain()
            except Exception as exc:
                logger.error("Error executing on_broken_chain callback: %s", exc)
        raise SecurityError(f"Evidence chain integrity violation: {reason}", code="EVIDENCE_CHAIN_BROKEN")
