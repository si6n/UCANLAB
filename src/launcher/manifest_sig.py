"""Ed25519 signature over the ``/updates/latest`` manifest body (H-1).

H-1
====
``mandatory`` and ``version`` were read straight out of the cloud response and
acted on (a ``mandatory`` flag blocks the whole platform) while the Ed25519
signature was only ever checked against the *binary* at download time. A
compromised or pinned ``/updates/latest`` response could therefore dictate
update routing, the blocking obligation and the download URL with no signature
covering any of it.

This module adds the missing provenance step: the manifest body carries a
detached signature and a key id, and NO field is trusted until that signature
verifies against the embedded public key / kid ring used for the binary.

Format accepted (fail-closed on anything else)
----------------------------------------------
The signature covers the CANONICAL JSON serialisation of the manifest object::

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False).encode("utf-8")

where ``payload`` is the parsed manifest **including** the ``signature`` key it
came with ("self-covering" signature) or **excluding** it (plain payload
signature) — both are tried. Requiring a *specific* canonicalisation is what
makes verification deterministic: the alternative (verifying raw response
bytes) is not reproducible once the body has been parsed.

Accepted encodings: a single base64 Ed25519 signature, or ``<kid>:<base64>``
(the ``kid`` selects the ring entry; an unknown kid fails closed). Base64 is
decoded with ``validate=True`` — a lenient decoder can silently drop junk
characters.

Test-only escape hatch
----------------------
``_allow_unsigned_manifest`` (constructor flag / ``for_testing()``) skips the
requirement and is only reachable through the explicitly named test
constructor on :class:`~src.launcher.app.UniversalCanLauncher`. It is never
enabled by an environment variable and never in a frozen build.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

#: Manifest field carrying the detached signature (base64 or ``<kid>:<base64>``).
MANIFEST_SIGNATURE_FIELD = "manifest_signature"
#: Optional manifest field naming the signing key id (alternative to ``kid:`` prefix).
MANIFEST_KEY_ID_FIELD = "manifest_kid"
#: Key id used when the manifest names none.
DEFAULT_MANIFEST_KEY_ID = "v1"

_B64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_ED25519_SIG_BYTES = 64


def canonical_manifest_bytes(payload: dict[str, Any]) -> bytes:
    """Deterministic JSON encoding of a manifest object (see module docstring)."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _decode_signature(raw: str) -> tuple[str, bytes] | None:
    """Split ``<kid>:<b64>`` / ``<b64>`` and strict-decode the signature bytes."""
    token = str(raw or "").strip()
    if not token:
        return None
    kid = DEFAULT_MANIFEST_KEY_ID
    body = token
    # A bare base64 blob never contains ':' (not in the alphabet), so a single
    # colon unambiguously separates the kid. More than one colon is malformed.
    if token.count(":") == 1:
        head, _, tail = token.partition(":")
        if head.strip():
            kid = head.strip()
            body = tail.strip()
    if not body or not _B64_RE.fullmatch(body):
        return None
    pad_len = -len(body) % 4
    try:
        sig = base64.b64decode(body + ("=" * pad_len), validate=True)
    except Exception:  # noqa: BLE001 - any decode failure is a hard reject
        return None
    if len(sig) != _ED25519_SIG_BYTES:
        return None
    return kid, sig


def verify_manifest_signature(
    manifest: dict[str, Any],
    keys: dict[str, ed25519.Ed25519PublicKey],
    *,
    require: bool = True,
) -> bool:
    """Verify the detached Ed25519 signature over a manifest object.

    Returns ``True`` only when a signature is present, well-formed, and valid
    under a ring key. Returns ``False`` when ``require`` is True and anything
    is missing/unknown/invalid — callers must treat ``False`` as "do not trust
    any field of this manifest".
    """
    raw_sig = manifest.get(MANIFEST_SIGNATURE_FIELD, "")
    if not isinstance(raw_sig, str) or not raw_sig.strip():
        # Fail closed on anything that is not a non-empty string. The caller
        # (UpdateManager) has already decided whether a signature is required
        # at all (test-only escape hatch), so no `require` relaxation here.
        return False
    parsed = _decode_signature(raw_sig)
    if parsed is None:
        return False
    kid, sig_bytes = parsed

    explicit_kid = manifest.get(MANIFEST_KEY_ID_FIELD)
    if isinstance(explicit_kid, str) and explicit_kid.strip():
        # An explicit kid wins; a disagreement between the two is malformed.
        if kid != DEFAULT_MANIFEST_KEY_ID and kid != explicit_kid.strip():
            return False
        kid = explicit_kid.strip()

    key = keys.get(kid)
    if key is None:
        return False

    # 1) self-covering signature (the signed payload still carries the field)
    # 2) plain payload signature (the field was stripped before signing)
    candidates = [manifest, {k: v for k, v in manifest.items() if k != MANIFEST_SIGNATURE_FIELD}]
    for candidate in candidates:
        try:
            key.verify(sig_bytes, canonical_manifest_bytes(candidate))
            return True
        except (InvalidSignature, ValueError, TypeError):
            continue
    return False
