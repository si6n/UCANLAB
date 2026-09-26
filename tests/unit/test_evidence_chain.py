# -*- coding: utf-8 -*-
"""Test suite for RFC 4998 Evidence Chain & Merkle Tree — Aksiyon 32 / Faz 6.2."""

import pytest
from src.core.errors import SecurityError
from src.security.audit.evidence_chain import EvidenceChain, MerkleTree, GENESIS_PREV_HASH


class DummyClock:
    def __init__(self, start_ns: int = 1_000_000_000) -> None:
        self.current_ns = start_ns

    def now_monotonic_ns(self) -> int:
        return self.current_ns

    def now_wall_ns(self) -> int:
        return self.current_ns


def test_evidence_chain_basic_append_and_verify() -> None:
    key = b"0123456789abcdef0123456789abcdef"
    clock = DummyClock(1_000_000_000)
    chain = EvidenceChain(secret_key=key, clock=clock)

    e0 = chain.append(b"Event 0: CAN Session Start")
    assert e0.sequence == 0
    assert e0.prev_hash == GENESIS_PREV_HASH

    clock.current_ns += 100_000_000
    e1 = chain.append(b"Event 1: Ingested Frame 0x18FEEF00")
    assert e1.sequence == 1
    assert e1.prev_hash == e0.entry_hash

    clock.current_ns += 50_000_000
    e2 = chain.append(b"Event 2: Diagnostic Event DTC P0AA6")
    assert e2.sequence == 2
    assert e2.prev_hash == e1.entry_hash

    assert chain.verify_chain() is True
    assert chain.is_broken is False


def test_evidence_chain_merkle_root_and_inclusion_proof() -> None:
    key = b"0123456789abcdef0123456789abcdef"
    clock = DummyClock()
    chain = EvidenceChain(secret_key=key, clock=clock)

    for i in range(5):
        clock.current_ns += 10_000_000
        chain.append(f"Payload-{i}".encode("utf-8"))

    root = chain.compute_merkle_root()
    assert len(root) == 32
    assert root != b"\x00" * 32

    # Verify inclusion proof for each leaf
    leaves = [entry.entry_hash for entry in chain.entries]
    for idx in range(len(leaves)):
        proof = chain.generate_inclusion_proof(idx)
        assert MerkleTree.verify_proof(leaves[idx], proof, root) is True


def test_evidence_chain_tamper_fails_closed() -> None:
    key = b"0123456789abcdef0123456789abcdef"
    clock = DummyClock()
    safe_state_triggered = []

    def on_safe_state() -> None:
        safe_state_triggered.append(True)

    chain = EvidenceChain(secret_key=key, clock=clock, on_broken_chain=on_safe_state)
    chain.append(b"Block 0")
    chain.append(b"Block 1")
    chain.append(b"Block 2")

    # Tamper with block 1 payload
    corrupted_entry = chain._entries[1]
    tampered_entry = type(corrupted_entry)(
        sequence=corrupted_entry.sequence,
        timestamp_ns=corrupted_entry.timestamp_ns,
        payload=b"Block 1 TAMPERED",
        prev_hash=corrupted_entry.prev_hash,
        entry_hash=corrupted_entry.entry_hash,
    )
    chain._entries[1] = tampered_entry

    with pytest.raises(SecurityError, match="Evidence chain integrity violation"):
        chain.verify_chain()

    assert chain.is_broken is True
    assert len(safe_state_triggered) == 1

    # Further append must be refused immediately
    with pytest.raises(SecurityError, match="Cannot append to a broken evidence chain"):
        chain.append(b"Block 3")


def test_evidence_chain_timestamp_rollback_detected() -> None:
    key = b"0123456789abcdef0123456789abcdef"
    clock = DummyClock(2_000_000_000)
    chain = EvidenceChain(secret_key=key, clock=clock)
    chain.append(b"Record 1")

    # Clock rollback
    clock.current_ns = 1_000_000_000
    with pytest.raises(SecurityError, match="Clock rollback detected"):
        chain.append(b"Record 2")


def test_evidence_chain_checkpoint() -> None:
    key = b"0123456789abcdef0123456789abcdef"
    clock = DummyClock()
    chain = EvidenceChain(secret_key=key, clock=clock)
    chain.append(b"Init")
    chain.append(b"Action")

    checkpoint = chain.create_checkpoint(operator_id="technician-42")
    assert checkpoint["format"] == "RFC4998-ERS-V1"
    assert checkpoint["operator_id"] == "technician-42"
    assert checkpoint["entry_count"] == 2
    assert "merkle_root_hex" in checkpoint
    assert "signature_hex" in checkpoint
