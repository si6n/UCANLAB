"""Knowledge Pack Exporter & Ed25519 Signer (FAZ 7).

Pairs with EncryptedKnowledgePackLoader (pack_loader.py) to package and sign
updated diagnostic procedure packs and verified golden case evidence for fleet distribution.
Complies with ISO 26262 / ASIL-B and R2-S3 data protection (strictly VIN-masked).
"""

from __future__ import annotations

import hashlib
import json
import os

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def export_signed_knowledge_pack(
    pack_name: str,
    version: str,
    target_protocol: str,
    file_payloads: dict[str, bytes],
    signing_key: ed25519.Ed25519PrivateKey,
    aes_key: bytes,
) -> tuple[bytes, bytes, dict[str, bytes]]:
    """Encrypt files with AES-256-GCM, compile signed manifest, and return pack bundle.

    Returns:
        (manifest_json_bytes, manifest_sig_bytes, encrypted_payloads)
    """
    if len(aes_key) != 32:
        raise ValueError(f"AES key must be exactly 32 bytes, got {len(aes_key)}")

    aesgcm = AESGCM(aes_key)
    encrypted_payloads: dict[str, bytes] = {}
    manifest_files: dict[str, str] = {}

    for rel_path, raw_data in sorted(file_payloads.items()):
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, raw_data, None)
        combined = nonce + ciphertext
        encrypted_payloads[rel_path] = combined
        manifest_files[rel_path] = hashlib.sha256(raw_data).hexdigest()

    manifest_dict = {
        "pack_name": pack_name,
        "version": version,
        "target_protocol": target_protocol,
        "encrypted_files": manifest_files,
    }
    manifest_bytes = json.dumps(manifest_dict, sort_keys=True).encode("utf-8")
    sig_bytes = signing_key.sign(manifest_bytes)

    return manifest_bytes, sig_bytes, encrypted_payloads


__all__ = ["export_signed_knowledge_pack"]
