# -*- coding: utf-8 -*-
"""RFC 8785 — JSON Canonicalization Scheme (JCS) Implementation.

Complies with:
- RFC 8785 (JSON Canonicalization Scheme)
- RFC 7493 (The I-JSON Message Format)
- Konsolide Keşif Raporu §A.3.1 (Madde K), §H.3 (Aksiyon 33):
  "RFC 8785 (JCS) — lisans imzası için kanonik serileştirme; I-JSON girdi,
   NaN/vekil karakter terminating error, Unicode normalizasyonu uygulanmaz.
   'Hangi baytları imzaladık?' belirsizliği yapısal olarak biter."
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from src.core.errors import SecurityError

_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def _utf16_sort_key(s: str) -> list[int]:
    """Sort key matching UTF-16 code unit ordering per RFC 8785 §3.2.3.

    Python strings are sequences of Unicode code points. Characters above
    U+FFFF are represented as surrogate pairs in UTF-16. For RFC 8785 key
    ordering, dictionary keys must be sorted lexicographically by UTF-16 code units.
    """
    code_units: list[int] = []
    for ch in s:
        cp = ord(ch)
        if cp <= 0xFFFF:
            code_units.append(cp)
        else:
            # Surrogate pair representation
            cp_prime = cp - 0x10000
            code_units.append(0xD800 + (cp_prime >> 10))
            code_units.append(0xDC00 + (cp_prime & 0x3FF))
    return code_units


def _format_number(val: int | float) -> str:
    """Format numbers per RFC 8785 §3.2.2.3 / ECMAScript ToString."""
    if isinstance(val, bool):
        raise SecurityError("Boolean is not a number", code="JCS_TYPE_ERROR")
    if not math.isfinite(val):
        raise SecurityError(
            f"RFC 8785 rejects non-finite number: {val!r}",
            code="JCS_INVALID_NUMBER",
        )
    if isinstance(val, int):
        return str(val)

    # Float handling
    if val == 0.0:
        return "0"

    if val.is_integer():
        return str(int(val))

    # Standard JSON serializer provides ECMAScript-compatible formatting for finite floats
    s = json.dumps(val)
    # Remove leading + in exponent if present
    s = s.replace("e+", "e")
    return s


def _canonicalize_value(val: Any) -> str:
    """Recursively serialize a Python object to RFC 8785 canonical string."""
    if val is None:
        return "null"
    if val is True:
        return "true"
    if val is False:
        return "false"
    if isinstance(val, (int, float)):
        return _format_number(val)
    if isinstance(val, str):
        if _SURROGATE_RE.search(val):
            raise SecurityError(
                "RFC 8785 rejects lone surrogates in strings",
                code="JCS_SURROGATE_ERROR",
            )
        # RFC 8785 §3.2.2.2: Do NOT apply Unicode normalization (e.g. NFC).
        # Standard json.dumps handles escape of control characters and quotes.
        return json.dumps(val, ensure_ascii=False)
    if isinstance(val, (list, tuple)):
        items = [_canonicalize_value(item) for item in val]
        return "[" + ",".join(items) + "]"
    if isinstance(val, dict):
        # Sort keys lexicographically by UTF-16 code units
        sorted_keys = sorted(val.keys(), key=_utf16_sort_key)
        parts: list[str] = []
        for k in sorted_keys:
            if not isinstance(k, str):
                raise SecurityError(
                    f"RFC 8785 object keys must be strings, got {type(k).__name__}",
                    code="JCS_TYPE_ERROR",
                )
            if _SURROGATE_RE.search(k):
                raise SecurityError(
                    "RFC 8785 rejects lone surrogates in keys",
                    code="JCS_SURROGATE_ERROR",
                )
            k_escaped = json.dumps(k, ensure_ascii=False)
            v_canonical = _canonicalize_value(val[k])
            parts.append(f"{k_escaped}:{v_canonical}")
        return "{" + ",".join(parts) + "}"

    raise SecurityError(
        f"RFC 8785 unsupported type: {type(val).__name__}",
        code="JCS_UNSUPPORTED_TYPE",
    )


def canonicalize(obj: Any) -> bytes:
    """Canonicalize a JSON-compatible Python data structure into RFC 8785 UTF-8 bytes.

    Terminating errors per RFC 8785:
    - NaN, Infinity, -Infinity
    - Lone surrogate characters
    - Non-string dictionary keys
    """
    canonical_str = _canonicalize_value(obj)
    return canonical_str.encode("utf-8")


def canonical_hash(obj: Any) -> bytes:
    """Return SHA-256 digest of the RFC 8785 canonical representation."""
    return hashlib.sha256(canonicalize(obj)).digest()
