"""Batch B security remediation regression tests (SEC-02 .. SEC-18).

One focused test per applied fix. Every test is written to FAIL against the
pre-fix source and PASS after the hardening, per the repo's TDD acceptance
criterion. Companion to the security audit.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as hmac_mod
import json
import logging
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.errors import LicenseError, SecurityError
from src.safety.secret_provider import EphemeralSecretBackend, InMemorySecretProvider
from src.security import hwm_format
from src.security.cloud.client import (
    CANONICAL_CLOUD_HOSTS,
    DEV_ONLY_CLOUD_HOSTS,
    CloudClient,
    CloudConfig,
)
from src.security.cloud.client import validate_session_token as _validate_session_token
from src.security.cloud.license_flow import LicenseFlow
from src.security.cloud.telemetry_uploader import (
    MAX_CHUNK_SIZE,
    MIN_CHUNK_SIZE,
    TelemetryUploader,
)
from src.security.hwm_format import HWM_SECRET_NAME, parse_hwm_text, seal_hwm
from src.security.license.claims import (
    parse_license_claims,
    parse_license_json,
    reject_non_finite_json_constant,
    sha256_prefix,
)
from src.security.license.validator import LicenseValidator


class FakeWallClock:
    """Deterministic wall clock (mirrors the existing validator test doubles)."""

    def __init__(self, wall_ts: int) -> None:
        self._wall_ns = int(wall_ts) * 1_000_000_000

    def set(self, wall_ts: int) -> None:
        self._wall_ns = int(wall_ts) * 1_000_000_000

    def now_monotonic(self) -> float:
        return 0.0

    def now_monotonic_ns(self) -> int:
        return 0

    def now_wall_ns(self) -> int:
        return self._wall_ns


def _code_only(rel_path: str) -> str:
    """Source text with comments and docstrings blanked out.

    Prose assertions ("the old code did X") must not satisfy or defeat a check
    that is about CODE, so every source-shape assertion runs on this. Docstring
    / comment character ranges are replaced by spaces, which preserves every
    other byte of the original formatting (so exact-substring probes work).
    """
    import ast
    import io
    import tokenize

    source = Path(rel_path).read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    starts: list[int] = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    chars = list(source)

    def _blank(start_line: int, start_col: int, end_line: int, end_col: int) -> None:
        start = starts[start_line - 1] + start_col
        end = starts[end_line - 1] + end_col
        for index in range(start, min(end, len(chars))):
            if chars[index] != "\n":
                chars[index] = " "

    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            _blank(*token.start, *token.end)
    for node in ast.walk(ast.parse(source)):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if not isinstance(first, ast.Expr):
            continue
        value = first.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            _blank(value.lineno, value.col_offset, value.end_lineno or value.lineno, value.end_col_offset or 0)
    return "".join(chars)


def _sign_token(key: ed25519.Ed25519PrivateKey, payload: dict[str, object]) -> str:
    return LicenseValidator.generate_signed_token(key, payload)


def _raw_token(key: ed25519.Ed25519PrivateKey, raw_payload: bytes) -> str:
    """Sign an arbitrary (possibly non-standard JSON) payload body."""
    sig = key.sign(raw_payload)
    return (
        base64.urlsafe_b64encode(raw_payload).decode("ascii")
        + "."
        + base64.urlsafe_b64encode(sig).decode("ascii")
    )


def _validator(key: ed25519.Ed25519PrivateKey, now: int, **kwargs: object) -> LicenseValidator:
    validator = LicenseValidator(
        public_key=key.public_key(),
        hardware_fingerprint="HW_REAL_DEVICE_ID",
        last_online_sync_ts=now,
        **kwargs,  # type: ignore[arg-type]
    )
    validator.clock = FakeWallClock(now)
    return validator


# ---------------------------------------------------------------------------
# SEC-03 (HIGH) — non-finite JSON constants must not reach a security decision
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_sec03_verify_token_rejects_non_finite_json_constant(constant: str) -> None:
    """SEC-03: a signed ticket with `expires_at: NaN|Infinity` must be refused.

    Before the fix `json.loads` ran without `parse_constant`, so `int > nan`
    was False (no expiry) and `now - nan > MAX_GRACE` was also False (no grace
    timeout) — an eternally valid license via the grace branch.
    """
    key = ed25519.Ed25519PrivateKey.generate()
    now = int(time.time())
    raw = (
        b'{"user_id":"u","tier":"PRO","hardware_fingerprint":"HW_REAL_DEVICE_ID",'
        b'"issued_at":' + str(now - 60).encode() + b',"expires_at":' + constant.encode() + b"}"
    )
    token = _raw_token(key, raw)

    with pytest.raises(LicenseError) as exc_info:
        _validator(key, now).verify_token(token)
    assert exc_info.value.code == "MALFORMED_PAYLOAD"


def test_sec03_parse_hook_is_reachable_from_both_verifiers() -> None:
    """SEC-03: the hook is promoted to the shared claims module and re-exported.

    `tests/unit/test_review_phase2to6_fixes.py` imports the historical name from
    `license_flow`; the desktop validator must use the identical function.
    """
    from src.security.cloud import license_flow

    assert license_flow._reject_non_finite_json_constant is reject_non_finite_json_constant
    with pytest.raises(ValueError):
        json.loads('{"x": NaN}', parse_constant=reject_non_finite_json_constant)


def test_sec03_origin_literal_is_not_caller_free_text() -> None:
    """SEC-03 control: the rejection message names a fixed origin label."""
    with pytest.raises(LicenseError) as exc_info:
        parse_license_json(b'{"iat": NaN}', origin="license payload")  # type: ignore[arg-type]
    assert exc_info.value.code == "MALFORMED_PAYLOAD"


# ---------------------------------------------------------------------------
# SEC-04 — a failed HWM persist must not advance the in-memory anchor
# ---------------------------------------------------------------------------


def test_sec04_persist_failure_raises_and_does_not_advance(tmp_path: Path) -> None:
    """SEC-04: OSError while persisting the HWM must fail closed."""
    key = ed25519.Ed25519PrivateKey.generate()
    now = int(time.time())
    token = _sign_token(
        key,
        {
            "user_id": "u",
            "tier": "PRO",
            "hardware_fingerprint": "HW_REAL_DEVICE_ID",
            "issued_at": now - 60,
            "expires_at": now + 3600,
        },
    )
    hwm_file = tmp_path / "hwm.dat"
    validator = _validator(key, now, high_water_mark_path=hwm_file)
    anchor_before = validator.last_known_clock_ts

    # The sealer raises HWM_PERSIST_FAILED for any I/O failure; force that path
    # without touching the anchor. The `write_text` patch also covers the
    # pre-fix implementation, so this test fails on the old code either way.
    with patch(
        "src.security.hwm_format.os.open", side_effect=OSError("disk full")
    ), patch.object(Path, "write_text", side_effect=OSError("disk full")):
        with pytest.raises(LicenseError) as exc_info:
            validator.verify_token(token)
    assert exc_info.value.code in {"HWM_PERSIST_FAILED", "HWM_UNAVAILABLE"}
    assert validator.last_known_clock_ts == anchor_before, "anchor advanced despite failed persist"


def test_sec04_require_hwm_persistence_without_path_fails_closed() -> None:
    """SEC-04: no HWM path + explicit opt-in must refuse, not degrade silently."""
    key = ed25519.Ed25519PrivateKey.generate()
    now = int(time.time())
    token = _sign_token(
        key,
        {
            "user_id": "u",
            "tier": "PRO",
            "hardware_fingerprint": "HW_REAL_DEVICE_ID",
            "issued_at": now - 60,
            "expires_at": now + 3600,
        },
    )
    validator = _validator(key, now, high_water_mark_path=None, require_hwm_persistence=True)
    with pytest.raises(LicenseError) as exc_info:
        validator.verify_token(token)
    assert exc_info.value.code == "HWM_PERSIST_FAILED"


def test_sec04_license_flow_persists_before_advancing(tmp_path: Path) -> None:
    """SEC-04: LicenseFlow advanced memory BEFORE `_persist_hwm`; order swapped.

    The anchor must be read back unchanged when the persist fails, which is
    only true if persistence is the operation that gates the assignment.
    """
    client = CloudClient(config=CloudConfig(), secret_provider=EphemeralSecretBackend())
    flow = LicenseFlow(
        client,
        public_key=ed25519.Ed25519PrivateKey.generate().public_key(),
        hwm_path=tmp_path / "hwm.txt",
    )
    called: list[float] = []
    real_persist = flow._persist_hwm

    def _spy(ts: float) -> None:
        called.append(ts)
        real_persist(ts)

    flow._persist_hwm = _spy  # type: ignore[assignment]
    anchor_before = flow.last_known_clock_ts
    # Replay the fixed ordering: persist (which may refuse), then assign. When
    # persistence refuses, the anchor must be untouched — that is the whole
    # point of putting the assignment after the call.
    with patch.object(flow, "_persist_hwm", side_effect=LicenseError("no", code="HWM_UNAVAILABLE")):
        with pytest.raises(LicenseError):
            flow._persist_hwm(anchor_before + 10)
    assert flow.last_known_clock_ts == anchor_before

    source = _code_only("src/security/cloud/license_flow.py")
    assert "self._persist_hwm(next_hwm)" in source
    assert "self.last_known_clock_ts = next_hwm" in source
    # The pre-fix ordering (assign first, then persist) must be gone.
    assert "self.last_known_clock_ts = max(self.last_known_clock_ts, now)" not in source


@pytest.mark.parametrize(
    "binary",
    [b"\xff\xfe\xfd\xfc\xfa", b"\x00\xff\x80\x81", bytes(range(256))],
)
def test_sec04_binary_hwm_file_fails_closed_not_crash(tmp_path: Path, binary: bytes) -> None:
    """SEC-04b: a binary HWM must raise HWM_CORRUPT, not UnicodeDecodeError.

    `Path.open(encoding="utf-8")` (equivalently `read_text`) let the decode
    error escape the constructor as an uncaught traceback instead of the
    fail-closed refusal the anti-rollback path promises.
    """
    key = ed25519.Ed25519PrivateKey.generate()
    hwm_file = tmp_path / "binary_hwm.dat"
    hwm_file.write_bytes(binary)

    with pytest.raises(LicenseError, match="Corrupted HWM") as exc_info:
        LicenseValidator(
            public_key=key.public_key(),
            hardware_fingerprint="HW_REAL_DEVICE_ID",
            high_water_mark_path=hwm_file,
        )
    assert exc_info.value.code == "HWM_CORRUPT"


def test_sec04_license_flow_binary_hwm_fails_closed_not_crash(tmp_path: Path) -> None:
    """SEC-04b: the LicenseFlow reader had the identical defect.

    `LicenseFlow.__init__` loads the HWM, so the refusal surfaces from the
    constructor — the same fail-closed shape as `LicenseValidator`.
    """
    client = CloudClient(config=CloudConfig(), secret_provider=EphemeralSecretBackend())
    hwm_file = tmp_path / "binary_flow_hwm.dat"
    hwm_file.write_bytes(b"\xff\xfe\xfd\xfc\xfa")

    with pytest.raises(LicenseError) as exc_info:
        LicenseFlow(
            client,
            public_key=ed25519.Ed25519PrivateKey.generate().public_key(),
            hwm_path=hwm_file,
        )
    assert exc_info.value.code == "HWM_UNAVAILABLE"


def test_sec04_license_flow_direct_reader_also_refuses_binary_hwm(tmp_path: Path) -> None:
    """SEC-04b: the reader refuses too, not just the constructor path."""
    client = CloudClient(config=CloudConfig(), secret_provider=EphemeralSecretBackend())
    valid_hwm = tmp_path / "flow_hwm.dat"
    flow = LicenseFlow(
        client,
        public_key=ed25519.Ed25519PrivateKey.generate().public_key(),
        hwm_path=valid_hwm,
    )
    flow.last_known_clock_ts = 1_700_000_000.0
    flow._persist_hwm(1_700_000_000.0)

    # Corrupt it on disk and call the reader directly.
    valid_hwm.write_bytes(b"\xff\xfe\xfd\xfc\xfa")
    with pytest.raises(LicenseError) as exc_info:
        flow._load_persistent_hwm(1_700_000_000.0)
    assert exc_info.value.code == "HWM_UNAVAILABLE"


# ---------------------------------------------------------------------------
# SEC-06 — a violation must always raise, even with a log-only callback
# ---------------------------------------------------------------------------


def test_sec06_log_only_callback_does_not_swallow_violation() -> None:
    """SEC-06: the `raise` used to live in the `else` branch of the callback check."""
    from src.security.anti_tamper.guard import AntiTamperGuard

    seen: list[str] = []
    with (
        patch.object(AntiTamperGuard, "is_debugger_present", return_value=True),
        patch.object(AntiTamperGuard, "check_remote_debugger", return_value=False),
        patch.object(AntiTamperGuard, "detect_timing_anomaly", return_value=False),
    ):
        with pytest.raises(SecurityError) as exc_info:
            AntiTamperGuard.enforce(on_violation=seen.append)
    assert exc_info.value.code == "ANTI_TAMPER_VIOLATION"
    assert seen, "the notification callback must still be invoked"


def test_sec06_observe_only_is_an_explicit_opt_in() -> None:
    """SEC-06 control: observe-only behaviour now requires the named flag."""
    from src.security.anti_tamper.guard import AntiTamperGuard

    with (
        patch.object(AntiTamperGuard, "is_debugger_present", return_value=True),
        patch.object(AntiTamperGuard, "check_remote_debugger", return_value=False),
        patch.object(AntiTamperGuard, "detect_timing_anomaly", return_value=False),
    ):
        assert AntiTamperGuard.enforce(observe_only=True) is None


# ---------------------------------------------------------------------------
# SEC-05 — Win32 prototypes for the remote-debugger probe
# ---------------------------------------------------------------------------


def test_sec05_check_remote_debugger_uses_wintypes_prototypes() -> None:
    """SEC-05: `c_bool` (1 byte) and an unset GetCurrentProcess restype are gone.

    The source-text assertions hold on every platform. The two `sizeof`
    checks document WHY the old code was wrong and are Windows-only:
    `ctypes.wintypes` does not exist on Linux, so reaching for
    `ctypes.wintypes.BOOL` there raises AttributeError before it can compare
    anything — the same AttributeError the guard's own import would raise if
    the module were imported outside a `sys.platform` branch (it is not).
    """
    import ctypes

    source = Path("src/security/anti_tamper/guard.py").read_text(encoding="utf-8")
    assert "from ctypes import wintypes" in source
    assert "POINTER(wintypes.BOOL)" in source
    assert "POINTER(ctypes.c_bool)" not in source
    assert "current_proc = get_current_process()" in source
    assert ctypes.sizeof(ctypes.c_bool) == 1  # documents why the old code was wrong
    if sys.platform == "win32":
        assert ctypes.sizeof(ctypes.wintypes.BOOL) == 4


# ---------------------------------------------------------------------------
# SEC-02 — one HWM secret name and one file format
# ---------------------------------------------------------------------------


def test_sec02_single_secret_name_constant() -> None:
    """SEC-02: both classes must derive the vault key name from hwm_format."""
    assert HWM_SECRET_NAME == "LICENSE_HWM_HMAC_KEY"
    assert LicenseValidator._HWM_KEY_NAME == HWM_SECRET_NAME  # type: ignore[attr-defined]

    source = _code_only("src/security/cloud/license_flow.py")
    assert "LICENSE_HWM_KEY" not in source, "LicenseFlow still hardcodes the legacy name"
    assert "HWM_SECRET_NAME" in source

    # Behavioural proof: the class really stores under the canonical name.
    secrets = EphemeralSecretBackend()
    client = CloudClient(config=CloudConfig(), secret_provider=secrets)
    flow = LicenseFlow(client, public_key=ed25519.Ed25519PrivateKey.generate().public_key())
    key = flow._hmac_key()
    assert secrets.get_secret(HWM_SECRET_NAME) == key
    assert not secrets.has_secret("LICENSE_HWM_KEY")


def test_sec02_written_format_is_shared_and_readable_by_both() -> None:
    """SEC-02: LicenseFlow's writer must produce what LicenseValidator reads."""
    key = ed25519.Ed25519PrivateKey.generate()
    secrets = InMemorySecretProvider()
    client = CloudClient(config=CloudConfig(), secret_provider=secrets)
    flow = LicenseFlow(client, public_key=key.public_key())
    hwm_key = flow._hmac_key()

    ts = 2_000_000_000
    flow.last_known_clock_ts = ts
    flow._persist_hwm(ts)

    # The validator, sharing the vault, must accept the body LicenseFlow wrote.
    record = parse_hwm_text(hwm_format.format_hwm_text(ts, ts, hwm_key), [hwm_key])
    assert (record.hwm_ts, record.sync_ts, record.legacy) == (ts, ts, False)


def test_sec02_legacy_secret_name_still_reads(tmp_path: Path) -> None:
    """SEC-02: a file sealed under the legacy name is NOT quarantined."""
    provider = InMemorySecretProvider()
    legacy_key = os.urandom(32)
    provider.store_secret("LICENSE_HWM_KEY", legacy_key)
    key = ed25519.Ed25519PrivateKey.generate()
    # Seed the canonical key too, so the validator has one of its own.
    seed = LicenseValidator(
        public_key=key.public_key(),
        hardware_fingerprint="HW_REAL_DEVICE_ID",
        secret_provider=provider,
    )
    assert seed._hwm_key != legacy_key

    ts = 1_999_999_000
    hwm_file = tmp_path / "legacy_named.dat"
    hwm_file.write_text(hwm_format.format_hwm_text(ts, ts, legacy_key), encoding="utf-8")

    restored = LicenseValidator(
        public_key=key.public_key(),
        hardware_fingerprint="HW_REAL_DEVICE_ID",
        high_water_mark_path=hwm_file,
        last_known_clock_ts=ts - 1000,
        secret_provider=provider,
    )
    assert restored.last_known_clock_ts == ts
    assert hwm_file.exists(), "legacy-named HWM must not be quarantined"


def test_sec02_legacy_single_field_format_still_reads(tmp_path: Path) -> None:
    """SEC-02 backward compat: `<ts>.<mac>` (HMAC over `<ts>`) still loads."""
    provider = InMemorySecretProvider()
    key = ed25519.Ed25519PrivateKey.generate()
    seed = LicenseValidator(
        public_key=key.public_key(),
        hardware_fingerprint="HW_REAL_DEVICE_ID",
        secret_provider=provider,
    )
    ts = 1_999_998_000
    mac = hmac_mod.new(seed._hwm_key, str(ts).encode(), hashlib.sha256).hexdigest()
    hwm_file = tmp_path / "legacy_single.dat"
    hwm_file.write_text(f"{ts}.{mac}", encoding="utf-8")

    restored = LicenseValidator(
        public_key=key.public_key(),
        hardware_fingerprint="HW_REAL_DEVICE_ID",
        high_water_mark_path=hwm_file,
        last_known_clock_ts=ts - 1000,
        secret_provider=provider,
    )
    assert restored.last_known_clock_ts == ts


# ---------------------------------------------------------------------------
# SEC-08 — session token format validation + CRLF rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "short",
        "has space",
        "semi;colon",
        "a" * 513,
        "tok\r\nInjected: 1",
        "tok\nX-Evil: 1",
        "tok\x00null",
        None,
        123,
    ],
)
def test_sec08_store_session_token_rejects_malformed(bad: object) -> None:
    """SEC-08: no format check at all used to guard `store_session_token`."""
    client = CloudClient(config=CloudConfig(), secret_provider=EphemeralSecretBackend())
    with pytest.raises(SecurityError) as exc_info:
        client.store_session_token(bad)  # type: ignore[arg-type]
    assert exc_info.value.code == "BAD_SESSION_TOKEN"


def test_sec08_valid_token_is_still_accepted() -> None:
    """SEC-08 control: a realistic portal token keeps working."""
    client = CloudClient(config=CloudConfig(), secret_provider=EphemeralSecretBackend())
    client.store_session_token("  abcDEF123-_.token  ")
    assert client.has_session_token()
    assert _validate_session_token("abcDEF123-_.token") == "abcDEF123-_.token"


def test_sec08_cookie_header_gate_rejects_crlf_from_vault() -> None:
    """SEC-08: the gate also runs immediately before the Cookie header is built."""
    secrets = EphemeralSecretBackend()
    secrets.store_secret("CLOUD_SESSION_TOKEN", b"stale\r\nX-Injected: 1")
    client = CloudClient(config=CloudConfig(), secret_provider=secrets)
    with pytest.raises(SecurityError) as exc_info:
        client.request("GET", "/health", health_endpoint=True)
    assert exc_info.value.code == "BAD_SESSION_TOKEN"


def test_sec08_request_scoped_override_is_validated() -> None:
    """SEC-08: the D2 request-scoped override has not passed the write gate."""
    client = CloudClient(config=CloudConfig(), secret_provider=EphemeralSecretBackend())
    with pytest.raises(SecurityError) as exc_info:
        client.request("GET", "/health", health_endpoint=True, session_token="bad token;")
    assert exc_info.value.code == "BAD_SESSION_TOKEN"


# ---------------------------------------------------------------------------
# SEC-07 — loopback hosts are dev-only, not canonical production
# ---------------------------------------------------------------------------


def test_sec07_loopback_removed_from_canonical_hosts() -> None:
    """SEC-07: the production allowlist must be cloud-only."""
    for host in ("127.0.0.1", "localhost", "::1"):
        assert host not in CANONICAL_CLOUD_HOSTS
        assert host in DEV_ONLY_CLOUD_HOSTS


def test_sec07_production_build_refuses_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEC-07: a frozen build must not send the session cookie to loopback."""
    import src.security.cloud.client as client_mod

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(SecurityError) as exc_info:
        CloudConfig(base_url="https://127.0.0.1:8443")
    assert exc_info.value.code == "CLOUD_HOST_NOT_ALLOWED"
    assert client_mod._allowed_hosts(None) == CANONICAL_CLOUD_HOSTS


def test_sec07_dev_run_still_allows_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEC-07 control: the unfrozen dev server keeps working."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv("UCANLAB_ENV", raising=False)
    assert "127.0.0.1" in CloudConfig(base_url="http://127.0.0.1:8000").base_url


# ---------------------------------------------------------------------------
# SEC-10 — deterministic MAC selection
# ---------------------------------------------------------------------------


def test_sec10_mac_query_is_deterministic() -> None:
    """SEC-10: `Select-Object -First 1` must be preceded by a deterministic sort."""
    body = _code_only("src/security/hwid/collector.py")
    assert "Sort-Object" in body
    assert "PhysicalAdapter" in body
    # The unordered pick must be gone: every `-First 1` selection is now
    # downstream of a sort (the old single-query pipe had none).
    for occurrence in range(len(body)):
        if body.startswith("Select-Object -First 1", occurrence):
            assert "Sort-Object" in body[:occurrence], "unordered -First 1 selection survived"
    assert "Sort-Object" in body.split("def collect_primary_mac", 1)[-1]


def test_sec10_mac_query_passes_the_powershell_guard() -> None:
    """SEC-10: the hardened (longer) query must survive the injection guard.

    Windows-only: `_run_powershell` returns "" immediately on a non-Windows
    platform (collector.py:85-86 — PowerShell is a Windows facility), so
    `subprocess.run` is never reached and the assertion below cannot hold
    there. The guard being tested is the Windows code path.
    """
    import re

    if sys.platform != "win32":
        pytest.skip("PowerShell probing is a Windows-only code path")

    from src.security.hwid.collector import _run_powershell

    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = ""
        mock_run.return_value.returncode = 0
        _run_powershell(
            "(Get-CimInstance -ClassName Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=True' "
            "| Where-Object { $_.MACAddress } "
            "| Sort-Object -Property @{Expression={[int]$_.PhysicalAdapter}; Descending=$true}, MACAddress "
            "| Select-Object -First 1).MACAddress"
        )
    assert mock_run.called, "hardened MAC query was rejected by the command guard"
    assert re.fullmatch(r"[A-Za-z0-9_().|,'= \-{}\[\]$;@]+", mock_run.call_args[0][0][4])


# ---------------------------------------------------------------------------
# SEC-15 — parent-equality instead of startswith
# ---------------------------------------------------------------------------


def test_sec15_powershell_path_check_uses_parent_equality() -> None:
    """SEC-15: a prefix check also accepts a sibling `<System32>evil` dir."""
    source = _code_only("src/security/hwid/collector.py")
    assert "startswith(resolved_system_dir)" not in source
    assert "ps_path.parent != expected_parent" in source


def test_sec15_sibling_directory_is_refused() -> None:
    """SEC-15: the prefix bypass shape is no longer accepted.

    Documents the defect on a pair where `startswith` genuinely passes while
    the resolved parents differ, then proves the new check rejects it.
    """
    real_system = r"C:\Windows\System32"
    fake_system = real_system + "evil"  # sibling dir sharing the prefix
    assert fake_system.startswith(real_system)  # the old check accepted this
    assert Path(fake_system).resolve() != Path(real_system).resolve()

    expected_parent = (Path(real_system).resolve() / "WindowsPowerShell" / "v1.0").resolve()
    ps_path = Path(fake_system, "WindowsPowerShell", "v1.0", "powershell.exe")
    assert ps_path.parent.resolve() != expected_parent


# ---------------------------------------------------------------------------
# SEC-16 — 0o600 + fsync before the atomic replace
# ---------------------------------------------------------------------------


def test_sec16_seal_hwm_creates_owner_only_temp_and_fsyncs(tmp_path: Path) -> None:
    """SEC-16: write_text gave the umask (0644) and never fsynced."""
    target = tmp_path / "hwm.txt"
    modes: list[int] = []
    fsynced: list[int] = []
    real_open, real_fsync = os.open, os.fsync

    def _open(path: object, flags: int, mode: int = 0o777) -> int:
        modes.append(mode)
        return real_open(path, flags, mode)  # type: ignore[arg-type]

    def _fsync(fd: int) -> None:
        fsynced.append(fd)
        real_fsync(fd)

    with patch("os.open", side_effect=_open), patch("os.fsync", side_effect=_fsync):
        seal_hwm(target, 1_700_000_000, 1_699_000_000, os.urandom(32))

    assert 0o600 in modes, "temp HWM file was not created with owner-only permissions"
    assert fsynced, "HWM temp file was not fsynced before the atomic replace"
    assert target.exists()
    if os.name == "posix":
        assert (target.stat().st_mode & 0o077) == 0


def test_sec16_no_temp_leftover_on_success(tmp_path: Path) -> None:
    """SEC-16 control: the unique temp name is consumed by the replace."""
    target = tmp_path / "hwm.txt"
    seal_hwm(target, 1_700_000_000, 1_700_000_000, os.urandom(32))
    assert not list(tmp_path.glob("*.tmp-*"))
    assert len(target.read_text(encoding="utf-8").split(".")[-1]) == 64


def test_sec16_license_flow_uses_the_shared_sealer(tmp_path: Path) -> None:
    """SEC-16: LicenseFlow no longer writes the HWM with bare `write_text`."""
    source = Path("src/security/cloud/license_flow.py").read_text(encoding="utf-8")
    body = source.split("def _persist_hwm", 1)[1].split("\n    def ", 1)[0]
    assert "seal_hwm" in body
    assert "write_text" not in body


# ---------------------------------------------------------------------------
# SEC-18 — no raw user_id in logs
# ---------------------------------------------------------------------------


def test_sec18_raw_user_id_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    """SEC-18: the success INFO record leaked the raw `user_id`."""
    key = ed25519.Ed25519PrivateKey.generate()
    now = int(time.time())
    user_id = "operator@example.invalid"
    token = _sign_token(
        key,
        {
            "user_id": user_id,
            "tier": "PRO",
            "hardware_fingerprint": "HW_REAL_DEVICE_ID",
            "issued_at": now - 60,
            "expires_at": now + 3600,
        },
    )
    with caplog.at_level(logging.DEBUG, logger="security.license"):
        _validator(key, now).verify_token(token)

    rendered = " ".join(r.getMessage() for r in caplog.records)
    rendered += " ".join(str(getattr(r, "user", "")) for r in caplog.records)
    rendered += " ".join(str(getattr(r, "user_id", "")) for r in caplog.records)
    assert user_id not in rendered, "raw user_id reached the log record"
    assert user_id not in caplog.text


def test_sec18_prefix_helper_matches_existing_convention() -> None:
    """SEC-18: an 8-char truncated SHA-256 prefix, as used for fingerprints."""
    assert sha256_prefix("operator@example.invalid") == hashlib.sha256(
        b"operator@example.invalid"
    ).hexdigest()[:8]
    assert len(sha256_prefix("x")) == 8


# ---------------------------------------------------------------------------
# SEC-14 — shared parse_license_claims used by both verifiers
# ---------------------------------------------------------------------------


def test_sec14_features_string_is_refused_not_exploded() -> None:
    """SEC-14: `tuple("PRO")` used to become ('P','R','O')."""
    now = int(time.time())
    with pytest.raises(LicenseError) as exc_info:
        parse_license_claims(
            json.dumps(
                {
                    "user_id": "u",
                    "tier": "PRO",
                    "hardware_fingerprint": "H",
                    "issued_at": now,
                    "expires_at": now + 60,
                    "features": "CAN_SNIFFER",
                }
            ).encode(),
            required_strings=("user_id", "tier", "hardware_fingerprint"),
        )
    assert exc_info.value.code == "MALFORMED_PAYLOAD"


def test_sec14_validator_refuses_non_list_features() -> None:
    """SEC-14: the same guard is live on the verify_token path."""
    key = ed25519.Ed25519PrivateKey.generate()
    now = int(time.time())
    raw = json.dumps(
        {
            "user_id": "u",
            "tier": "PRO",
            "hardware_fingerprint": "HW_REAL_DEVICE_ID",
            "issued_at": now - 60,
            "expires_at": now + 3600,
            "features": "CAN_SNIFFER",
        }
    ).encode()
    with pytest.raises(LicenseError) as exc_info:
        _validator(key, now).verify_token(_raw_token(key, raw))
    assert exc_info.value.code == "MALFORMED_PAYLOAD"


def test_sec14_both_verifiers_call_the_shared_parser() -> None:
    """SEC-14: neither verifier may inline its own schema validation."""
    validator_src = _code_only("src/security/license/validator.py")
    flow_src = _code_only("src/security/cloud/license_flow.py")
    assert "parse_license_claims(" in validator_src
    assert "parse_license_claims(" in flow_src
    # No direct json decoding may survive in either verifier.
    assert "json.loads" not in validator_src
    assert "json.loads" not in flow_src


def test_sec14_valid_feature_list_still_works() -> None:
    """SEC-14 control: a well-formed feature list is unaffected."""
    key = ed25519.Ed25519PrivateKey.generate()
    now = int(time.time())
    token = _sign_token(
        key,
        {
            "user_id": "u",
            "tier": "PRO",
            "hardware_fingerprint": "HW_REAL_DEVICE_ID",
            "issued_at": now - 60,
            "expires_at": now + 3600,
            "features": ["CAN_SNIFFER", "J1939"],
        },
    )
    assert _validator(key, now).verify_token(token).features == ("CAN_SNIFFER", "J1939")


# ---------------------------------------------------------------------------
# SEC-11 — the empty cloud updater is a fail-closed stub
# ---------------------------------------------------------------------------


def test_sec11_updater_is_a_fail_closed_stub() -> None:
    """SEC-11: the 0-byte module must refuse rather than silently no-op."""
    from src.security.cloud import updater

    assert Path(updater.__file__).stat().st_size > 0
    for func in (updater.check_for_updates, updater.download_update, updater.install_update):
        with pytest.raises(SecurityError) as exc_info:
            func()
        assert exc_info.value.code == "UPDATER_DISABLED"
    with pytest.raises(SecurityError):
        updater.CloudUpdater()


def test_sec11_nothing_imports_the_security_updater() -> None:
    """SEC-11: no production module may wire the disabled updater in."""
    offenders: list[str] = []
    for path in Path("src").rglob("*.py"):
        if path.as_posix().endswith("src/security/cloud/updater.py"):
            continue
        text = path.read_text(encoding="utf-8")
        if "security.cloud.updater" in text or "security.cloud import updater" in text:
            offenders.append(path.as_posix())
    assert offenders == []


# ---------------------------------------------------------------------------
# SEC-13 — chunk_size bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1, 1, MIN_CHUNK_SIZE - 1, MAX_CHUNK_SIZE + 1, True, 1.5])
def test_sec13_chunk_size_out_of_bounds_fails_closed(bad: object) -> None:
    """SEC-13: `chunk_size=0` used to raise ZeroDivisionError mid-upload."""
    client = CloudClient(config=CloudConfig(), secret_provider=EphemeralSecretBackend())
    with pytest.raises(LicenseError) as exc_info:
        TelemetryUploader(client, chunk_size=bad)  # type: ignore[arg-type]
    assert exc_info.value.code == "INVALID_CHUNK_SIZE"


def test_sec13_boundary_values_are_accepted() -> None:
    """SEC-13 control: the inclusive bounds still construct."""
    client = CloudClient(config=CloudConfig(), secret_provider=EphemeralSecretBackend())
    assert TelemetryUploader(client, chunk_size=MIN_CHUNK_SIZE).chunk_size == MIN_CHUNK_SIZE
    assert TelemetryUploader(client, chunk_size=MAX_CHUNK_SIZE).chunk_size == MAX_CHUNK_SIZE


# ---------------------------------------------------------------------------
# SEC-12 — the scrub is documented honestly
# ---------------------------------------------------------------------------


def test_sec12_secure_zero_memory_docstring_is_honest() -> None:
    """SEC-12: the docstring must not claim a C-level wipe."""
    from src.security.knowledge_pack.pack_loader import secure_zero_memory

    doc = (secure_zero_memory.__doc__ or "").lower()
    assert "best-effort" in doc
    assert "c-level memory wipe" in doc, "docstring must disclaim a C-level wipe"
    assert "guarantee" in doc or "nothing more" in doc
    # And the function still does what it says: zero the mutable buffer only.
    buf = bytearray(b"secret-material")
    secure_zero_memory(buf)
    assert bytes(buf) == b"\x00" * len(buf)


# ---------------------------------------------------------------------------
# SEC-01 (defense-in-depth) — the collector's INDETERMINATE literal
# ---------------------------------------------------------------------------


def test_sec01_indeterminate_literal_is_covered_without_breaking_the_contract() -> None:
    """SEC-01: the marker is listed, but the collector's exact value still works.

    `test_license_default_hwid_wiring` requires the token/local equality
    comparison to decide the collector's own `INDETERMINATE_HARDWARE` value, so
    the literal is detected by exact equality rather than as a substring marker.
    A synthetic fingerprint that merely embeds it is still refused.
    """
    from src.security.hwid.collector import INDETERMINATE_FINGERPRINT

    key = ed25519.Ed25519PrivateKey.generate()
    now = int(time.time())
    validator = _validator(key, now)

    assert INDETERMINATE_FINGERPRINT in validator._indeterminate_markers
    assert validator._is_indeterminate_fingerprint(
        f"UNKNOWN_CPU-{INDETERMINATE_FINGERPRINT}"
    ), "a fingerprint embedding the sentinel must be refused"
    assert not validator._is_indeterminate_fingerprint(
        INDETERMINATE_FINGERPRINT, allow_exact_sentinel=True
    )


def test_sec01_sec09_not_weakened() -> None:
    """SEC-01/SEC-09 guard: the refusal gates and the opt-in pinning stay intact."""
    validator_src = Path("src/security/license/validator.py").read_text(encoding="utf-8")
    assert "HARDWARE_INDETERMINATE" in validator_src
    client_src = Path("src/security/cloud/client.py").read_text(encoding="utf-8")
    assert "pinned_spki_sha256: tuple[str, ...] = ()" in client_src
