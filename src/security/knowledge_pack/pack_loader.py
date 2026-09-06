"""In-Memory Decryption and Zero-Disk Footprint Knowledge Pack Loader.

Complies with MASTER_PLAN.md Section 6.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.core.errors import SecurityError
from src.core.logging import get_logger

logger = get_logger("security.knowledge_pack")


def secure_zero_memory(buf: bytearray) -> None:
    """Overwrites sensitive RAM buffers in-place with zeros to prevent memory scraping."""
    for i in range(len(buf)):
        buf[i] = 0


@dataclass(slots=True)
class KnowledgePackManifest:
    """Parsed manifest describing OEM Knowledge Pack files and checksums."""

    pack_name: str
    version: str
    target_protocol: str  # "J1939" | "UDS" | "N2K" | "VOLVO"
    encrypted_files: dict[str, str]  # relative_path -> sha256_hex


class EncryptedKnowledgePackLoader:
    """Validates signature and decrypts Knowledge Packs purely in-memory."""

    def __init__(self, public_key: ed25519.Ed25519PublicKey, aes_key: bytes) -> None:
        if len(aes_key) != 32:
            raise ValueError(f"AES-256-GCM key must be exactly 32 bytes, got {len(aes_key)}")
        self.public_key = public_key
        self._aes_key = aes_key
        self._aesgcm = AESGCM(self._aes_key)

    def load_pack_from_bytes(
        self,
        manifest_json_bytes: bytes,
        manifest_sig_bytes: bytes,
        encrypted_payloads: dict[str, bytes],
    ) -> dict[str, bytes]:
        """Verify manifest Ed25519 signature and decrypt all files purely in RAM."""
        # 1. Verify Manifest Signature
        try:
            self.public_key.verify(manifest_sig_bytes, manifest_json_bytes)
        except InvalidSignature as exc:
            logger.critical("Knowledge Pack manifest signature verification failed!")
            raise SecurityError(
                "Knowledge Pack manifest signature invalid or tampered with.",
                code="MANIFEST_SIGNATURE_INVALID",
                cause=exc,
            ) from exc

        # 2. Parse Manifest
        try:
            manifest_dict = json.loads(manifest_json_bytes.decode("utf-8"))
            pack_name = manifest_dict.get("pack_name", "UNKNOWN")
        except Exception as exc:
            raise SecurityError(
                f"Malformed Knowledge Pack manifest: {exc}",
                code="MALFORMED_MANIFEST",
                cause=exc,
            ) from exc

        # Cryptographically bind manifest file declarations to provided payload set
        if "encrypted_files" in manifest_dict:
            manifest_files = set(manifest_dict["encrypted_files"].keys())
            payload_files = set(encrypted_payloads.keys())
            if manifest_files != payload_files:
                raise SecurityError(
                    f"Payload files do not match manifest declarations: missing {manifest_files - payload_files}, unexpected {payload_files - manifest_files}",
                    code="FILE_SET_MISMATCH",
                )

        logger.info("Validated Knowledge Pack manifest", extra={"pack": pack_name})

        # 3. In-Memory Decryption of Each File
        decrypted_memory_files: dict[str, bytes] = {}

        for filename, enc_data in encrypted_payloads.items():
            if len(enc_data) < 28:  # 12B nonce + 16B tag minimum
                raise SecurityError(f"Ciphertext too short for '{filename}'", code="CORRUPT_CIPHERTEXT")

            nonce = enc_data[:12]
            ciphertext_with_tag = enc_data[12:]

            try:
                decrypted = self._aesgcm.decrypt(nonce, ciphertext_with_tag, None)
                if "encrypted_files" in manifest_dict:
                    expected_hash = manifest_dict["encrypted_files"].get(filename)
                    if expected_hash and len(expected_hash) == 64:
                        actual_hash = hashlib.sha256(decrypted).hexdigest()
                        if actual_hash.lower() != expected_hash.lower():
                            raise SecurityError(
                                f"SHA-256 integrity mismatch for '{filename}'",
                                code="CONTENT_TAMPERED",
                            )
                decrypted_memory_files[filename] = decrypted
            except SecurityError:
                raise
            except Exception as exc:
                logger.error("Failed to decrypt Knowledge Pack file", extra={"file": filename, "error": str(exc)})
                raise SecurityError(
                    f"Decryption failed for '{filename}' (wrong key or corrupted pack).",
                    code="DECRYPTION_FAILED",
                    cause=exc,
                ) from exc

        logger.info(
            "Successfully decrypted Knowledge Pack in-memory", extra={"files_count": len(decrypted_memory_files)}
        )
        return decrypted_memory_files

    @classmethod
    def encrypt_pack_file(cls, aes_key: bytes, plaintext: bytes) -> bytes:
        """Helper to create AES-256-GCM encrypted payload (12B nonce + ciphertext + tag)."""
        aesgcm = AESGCM(aes_key)
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)
        return nonce + ciphertext
