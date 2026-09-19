"""Unit tests for AI Engine Roadmap FAZ 7 (Fleet Knowledge Pack Distribution Roundtrip)."""

import os

from cryptography.hazmat.primitives.asymmetric import ed25519

from src.security.knowledge_pack.pack_exporter import export_signed_knowledge_pack
from src.security.knowledge_pack.pack_loader import EncryptedKnowledgePackLoader


def test_knowledge_pack_roundtrip_signing_and_decryption() -> None:
    # Generate Ed25519 keypair and 32-byte AES key
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    aes_key = os.urandom(32)

    files_to_pack = {
        "procedures/P0101.json": b'{"dtc": "P0101", "procedure": "MAF inspection"}',
        "procedures/SPN_100.json": b'{"dtc": "SPN 100", "procedure": "Oil pressure test"}',
    }

    manifest_bytes, sig_bytes, encrypted = export_signed_knowledge_pack(
        pack_name="UCanLab-Procedures-Fleet-Update",
        version="2.0.0",
        target_protocol="J1939",
        file_payloads=files_to_pack,
        signing_key=private_key,
        aes_key=aes_key,
    )

    loader = EncryptedKnowledgePackLoader(public_key, aes_key)
    decrypted = loader.load_pack_from_bytes(
        manifest_json_bytes=manifest_bytes,
        manifest_sig_bytes=sig_bytes,
        encrypted_payloads=encrypted,
    )

    assert len(decrypted) == 2
    assert decrypted["procedures/P0101.json"] == files_to_pack["procedures/P0101.json"]
    assert decrypted["procedures/SPN_100.json"] == files_to_pack["procedures/SPN_100.json"]
    loader.close()
