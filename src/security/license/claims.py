"""Shared, fail-closed license-claim parsing (SEC-03 / SEC-14, Batch B).

Two independent Ed25519 verifiers existed in this codebase with two very
different ideas of what "the payload is valid" means:

* ``src/security/license/validator.py`` only checked that a handful of keys
  were *present* (``set(...).issubset(data.keys())``) and then copied values
  straight into a dataclass. ``features`` was built with
  ``tuple(data.get("features", []))``, so a JSON string became a tuple of
  single characters; ``expires_at`` accepted ``NaN``.
* ``src/security/cloud/license_flow.py`` performed a full schema validation
  (bounded strings, bounded feature list, finite non-negative numerics).

The duplicated "schema downgrade" is what made SEC-03 exploitable: a signed
ticket carrying ``NaN``/``Infinity`` passed every comparison in the validator
(``int > nan`` is ``False``) and produced an eternally valid license via the
offline-grace branch.

Both verifiers now share :func:`parse_license_claims` and
:func:`reject_non_finite_json_constant`, so neither can drift from the other
and no non-finite value can reach a security decision.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from typing import Any

from src.core.errors import LicenseError

#: Hard bounds applied to every string field of a license payload.
MAX_CLAIM_STRING_CHARS: int = 256

#: Hard bound on the number of `features` entries.
MAX_CLAIM_FEATURES: int = 128

#: Sentinel used as the `origin` label in error messages. It is a fixed
#: label, never caller-supplied text, so no log-injection surface is created.
_DEFAULT_ORIGIN = "license payload"


def reject_non_finite_json_constant(value: str) -> Any:
    """``json.loads(parse_constant=...)`` hook rejecting NaN/Infinity/-Infinity.

    Python's JSON decoder accepts these non-standard literals by default. A
    non-finite value inside a signed ticket is security-relevant input: every
    comparison against ``NaN`` is ``False``, so an expiry check
    (``now > expires_at``) and the offline-grace check
    (``now - anchor > MAX_GRACE``) both silently pass and the license never
    expires. Infrastructure-free function (no module state, no I/O) so it can
    be imported from either verifier without creating a cycle.
    """
    raise ValueError(f"non-finite JSON constant '{value}' is not permitted in a license payload")


def _bounded_string(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise LicenseError(
            f"License payload field '{field}' must be a non-empty string",
            code="MALFORMED_PAYLOAD",
        )
    if len(value) > MAX_CLAIM_STRING_CHARS:
        raise LicenseError(
            f"License payload field '{field}' exceeds {MAX_CLAIM_STRING_CHARS} characters",
            code="MALFORMED_PAYLOAD",
        )
    return value


def _finite_number(data: dict[str, Any], field: str) -> int:
    """Return a finite, non-negative integer claim value.

    ``bool`` is rejected explicitly (``isinstance(True, int)`` is True), and
    any non-finite float is refused — the SEC-03 hole. A float with a
    fractional part is truncated toward zero so the returned type stays ``int``
    for the callers' arithmetic, but the *value* has already been proven
    finite and non-negative.
    """
    value = data.get(field)
    if type(value) not in (int, float) or isinstance(value, bool):
        raise LicenseError(
            f"License payload field '{field}' must be a number, got {type(value).__name__}",
            code="MALFORMED_PAYLOAD",
        )
    as_float = float(value)
    if not math.isfinite(as_float):
        raise LicenseError(
            f"License payload field '{field}' is not finite; refusing a non-expiring license",
            code="MALFORMED_PAYLOAD",
        )
    if as_float < 0:
        raise LicenseError(
            f"License payload field '{field}' must be non-negative",
            code="MALFORMED_PAYLOAD",
        )
    return int(as_float)


def _bounded_features(data: dict[str, Any]) -> tuple[str, ...]:
    """Coerce `features` to a tuple of bounded strings.

    A bare string used to become a tuple of characters
    (``tuple("PRO") -> ("P", "R", "O")``) — silently wrong and enough to make
    a feature gate disagree with the issuer. Anything that is not a list of
    strings is refused instead.
    """
    if "features" not in data:
        return ()
    raw = data.get("features")
    if not isinstance(raw, (list, tuple)):
        raise LicenseError(
            f"License payload 'features' must be a list, got {type(raw).__name__}",
            code="MALFORMED_PAYLOAD",
        )
    if len(raw) > MAX_CLAIM_FEATURES:
        raise LicenseError(
            f"License payload declares {len(raw)} features (max {MAX_CLAIM_FEATURES})",
            code="MALFORMED_PAYLOAD",
        )
    features: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item or len(item) > MAX_CLAIM_STRING_CHARS:
            raise LicenseError(
                "License payload 'features' entries must be non-empty bounded strings",
                code="MALFORMED_PAYLOAD",
            )
        features.append(item)
    return tuple(features)


def parse_license_json(payload_bytes: bytes, *, origin: str = _DEFAULT_ORIGIN) -> dict[str, Any]:
    """Decode + type-check a signed license payload into a plain dict.

    Raises ``LicenseError`` with ``code="MALFORMED_PAYLOAD"`` for a non-UTF-8
    body, a body that is not a JSON object, and for NaN/Infinity (via
    :func:`reject_non_finite_json_constant`).

    ``origin`` is a fixed literal chosen by the CALLER (never caller-supplied
    free text) so the error identifies which verifier rejected the payload.
    """
    try:
        text = payload_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LicenseError(
            f"Malformed {origin}: payload is not valid UTF-8 ({exc})",
            code="MALFORMED_PAYLOAD",
            cause=exc,
        ) from exc
    try:
        data = json.loads(text, parse_constant=reject_non_finite_json_constant)
    except (ValueError, TypeError) as exc:
        raise LicenseError(
            f"Malformed {origin}: {exc}",
            code="MALFORMED_PAYLOAD",
            cause=exc,
        ) from exc
    if not isinstance(data, dict):
        raise LicenseError(
            f"{origin} must be a JSON object, got {type(data).__name__}",
            code="MALFORMED_PAYLOAD",
        )
    return data


def parse_license_claims(
    payload_bytes: bytes,
    *,
    origin: str = "license payload",
    required_strings: Sequence[str] = (),
    issue_field: str = "issued_at",
    expiry_field: str = "expires_at",
) -> dict[str, Any]:
    """Full schema validation for a signed license payload.

    The single implementation used by BOTH the desktop ``LicenseValidator``
    and the cloud ``LicenseFlow`` ticket verifier. It enforces, fail-closed:

    * the payload is a JSON object (no arrays/strings/numbers),
    * ``NaN`` / ``Infinity`` / ``-Infinity`` are rejected at parse time
      (this is the SEC-03 fix — a non-finite timestamp never reaches a
      comparison, where ``now > nan`` is ``False``),
    * every name in ``required_strings`` is present, non-empty and bounded,
    * ``features`` (optional) is a bounded list of non-empty bounded strings.
      A bare string is REFUSED rather than silently exploded into a tuple of
      characters,
    * ``issue_field`` / ``expiry_field`` (defaulting to the desktop names
      ``issued_at`` / ``expires_at``; the cloud ticket schema passes ``iat`` /
      ``exp``) must exist when the caller lists them, and must be finite,
      non-negative numbers.

    Returns a mapping with the normalised keys ``features``, ``issued_at``,
    ``expires_at`` plus every name in ``required_strings`` — so a caller reads
    a single, uniform result regardless of the wire vocabulary.
    """
    data = parse_license_json(payload_bytes, origin=origin)
    missing = tuple(field for field in required_strings if field not in data)
    if missing:
        raise LicenseError(
            f"Malformed {origin} (incomplete schema; missing: {', '.join(missing)})",
            code="MALFORMED_PAYLOAD",
        )
    claims: dict[str, Any] = {"features": _bounded_features(data)}
    for label, wire_name in (("issued_at", issue_field), ("expires_at", expiry_field)):
        # A caller that lists the wire name in `required_strings` (the desktop
        # validator's vocabulary) has already required its presence above.
        if wire_name in data or wire_name in required_strings:
            claims[label] = _finite_number(data, wire_name)
    for field in required_strings:
        if field in (issue_field, expiry_field):
            continue  # numeric field, validated above
        claims[field] = _bounded_string(data, field)
    return claims


def verify_signature_then_parse(
    payload_bytes: bytes,
    signature_bytes: bytes,
    verify: Any,
    *,
    origin: str = "license payload",
) -> dict[str, Any]:
    """Convenience helper: ``verify(payload, signature)`` first, then parse.

    Kept here (rather than duplicated in each verifier) so the ordering
    contract — signature BEFORE schema, so an unsigned payload is never
    interpreted — has exactly one definition.
    """
    verify(signature_bytes, payload_bytes)
    return parse_license_claims(payload_bytes, origin=origin)


def sha256_prefix(value: str, length: int = 8) -> str:
    """Truncated SHA-256 prefix for logging a non-reversible identifier.

    SEC-18 (Batch B): ``user_id`` is personal data and must not be written to
    log files verbatim. This matches the existing SEC-C-004 convention used
    for hardware-fingerprint prefixes.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


__all__ = [
    "MAX_CLAIM_FEATURES",
    "MAX_CLAIM_STRING_CHARS",
    "parse_license_claims",
    "parse_license_json",
    "reject_non_finite_json_constant",
    "sha256_prefix",
    "verify_signature_then_parse",
]
