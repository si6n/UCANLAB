"""Unit tests for EncryptedKnowledgePackLoader and SecureZeroMemory."""

import json
import os

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.errors import SecurityError
from src.security.knowledge_pack.pack_loader import (
    EncryptedKnowledgePackLoader,
    secure_zero_memory,
)


def test_knowledge_pack_in_memory_decryption_flow() -> None:
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    aes_key = os.urandom(32)

    # 1. Create plaintext payload files
    dbc_content = b'VERSION ""\nBO_ 100 Engine: 8 Vector__XXX\n'
    script_content = b"def run_diagnostics(): return True\n"

    enc_dbc = EncryptedKnowledgePackLoader.encrypt_pack_file(aes_key, dbc_content)
    enc_script = EncryptedKnowledgePackLoader.encrypt_pack_file(aes_key, script_content)

    # L-21 (P3-15): the manifest MUST carry real 64-hex SHA-256 digests —
    # placeholder hashes are rejected by the loader now (mandatory
    # integrity verification, no key-less packs).
    import hashlib

    manifest_dict = {
        "pack_name": "Volvo_Penta_Marine_D4_D6",
        "version": "1.0.0",
        "target_protocol": "VOLVO",
        "encrypted_files": {
            "engine.dbc": hashlib.sha256(dbc_content).hexdigest(),
            "diag.py": hashlib.sha256(script_content).hexdigest(),
        },
    }
    manifest_bytes = json.dumps(manifest_dict).encode("utf-8")
    manifest_sig = priv_key.sign(manifest_bytes)

    # 2. Decrypt in RAM using loader
    loader = EncryptedKnowledgePackLoader(public_key=pub_key, aes_key=aes_key)
    decrypted_files = loader.load_pack_from_bytes(
        manifest_json_bytes=manifest_bytes,
        manifest_sig_bytes=manifest_sig,
        encrypted_payloads={"engine.dbc": enc_dbc, "diag.py": enc_script},
    )

    assert len(decrypted_files) == 2
    assert decrypted_files["engine.dbc"] == dbc_content
    assert decrypted_files["diag.py"] == script_content

    # L-21: close() scrubs the key material.
    loader.close()
    assert bytes(loader._aes_key) == bytes(32)


def test_knowledge_pack_placeholder_hash_rejected() -> None:
    """L-21 (P3-15): a manifest with short/invalid digests must be rejected
    outright — the old code silently skipped integrity verification for any
    hash that was not exactly 64 chars."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    aes_key = os.urandom(32)

    manifest_dict = {
        "pack_name": "LegacyPack",
        "encrypted_files": {"engine.dbc": "abc"},
    }
    manifest_bytes = json.dumps(manifest_dict).encode("utf-8")
    manifest_sig = priv_key.sign(manifest_bytes)

    loader = EncryptedKnowledgePackLoader(public_key=pub_key, aes_key=aes_key)
    with pytest.raises(SecurityError, match="valid 64-hex SHA-256"):
        loader.load_pack_from_bytes(manifest_bytes, manifest_sig, {"engine.dbc": b"\x00" * 40})


def test_knowledge_pack_keyless_manifest_rejected() -> None:
    """L-21 (P3-15): a manifest without an encrypted_files declaration must
    be rejected — it previously bypassed BOTH the file-set binding and the
    per-file integrity check."""
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    aes_key = os.urandom(32)

    manifest_bytes = json.dumps({"pack_name": "NoDeclaration"}).encode("utf-8")
    manifest_sig = priv_key.sign(manifest_bytes)

    loader = EncryptedKnowledgePackLoader(public_key=pub_key, aes_key=aes_key)
    with pytest.raises(SecurityError, match="no encrypted_files"):
        loader.load_pack_from_bytes(manifest_bytes, manifest_sig, {})


def test_knowledge_pack_tampered_manifest_fails() -> None:
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    aes_key = os.urandom(32)

    manifest_bytes = b'{"pack_name":"Hacked"}'
    manifest_sig = priv_key.sign(b'{"pack_name":"Original"}')

    loader = EncryptedKnowledgePackLoader(public_key=pub_key, aes_key=aes_key)
    with pytest.raises(SecurityError, match="manifest signature invalid"):
        loader.load_pack_from_bytes(manifest_bytes, manifest_sig, {})


def test_secure_zero_memory() -> None:
    secret = bytearray(b"super_secret_master_key_123456789")
    assert secret != bytearray(len(secret))

    secure_zero_memory(secret)
    assert secret == bytearray(len(secret))
