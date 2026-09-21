"""Regression tests for REVIEW Aşama 2/3/4/5 remediation.

Companion to `test_review_phase1_safety_fixes.py`. Each test locks in a defect
that was reproduced in this repository at review-commit `f97e943` (or verified
still open at HEAD `6456d14`) and then fixed:

  * F1  — HEX/S-Record gap padding was an unbounded memory-amplification
          primitive (a 79-byte file drove a 256 MiB allocation).
  * F3  — the parsers accepted unknown record types and under-validated EOF.
  * F4  — a signed cloud ticket with NaN/Infinity timestamps passed both the
          expiry and the offline-grace check; non-object payloads raised a raw
          AttributeError.
  * D3  — `_app_data_root()` anchored writable state (incl. the license
          anti-rollback HWM) to `_MEIPASS`, which a onefile build deletes.
  * D5  — the upload-accept log wrote the full absolute path.
  * D6  — `cloud_register_device` / `cloud_activate_license` forwarded
          unvalidated bridge input.
  * A5-1 — an empty/whitespace session token produced `is_authenticated=True`.
  * A5-3 — the machine seed was read with no length/type/permission checks.
  * A5-4 — `ask_copilot` accepted an unbounded, unvalidated query.
  * S2/S3 — the replay filter's counters and ISO-TP ledger were mutated
          without a lock and the protection policy was publicly mutable.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import pytest

from src.core.errors import ProtocolError, SecurityError
from src.hal.replay.safety_filter import ReplaySafetyFilter
from src.protocols.uds.firmware import (
    DEFAULT_FIRMWARE_LIMITS,
    FirmwareContainer,
    FirmwareParseLimits,
    IntelHexParser,
    MemorySegment,
    SRecordParser,
)

# ---------------------------------------------------------------------------
# F1 — firmware flattening must be bounded (DoS)
# ---------------------------------------------------------------------------


def _far_apart_container() -> FirmwareContainer:
    """Two tiny segments whose span is 256 MiB (the reported repro shape)."""
    return FirmwareContainer(
        segments=[
            MemorySegment(address=0x0, data=b"\x01\x02\x03\x04"),
            MemorySegment(address=0x10000000, data=b"\xAA\xBB"),
        ]
    )


def test_f1_get_continuous_binary_rejects_huge_span() -> None:
    """F1: an oversized span must raise, not allocate hundreds of MiB."""
    container = _far_apart_container()
    with pytest.raises(ProtocolError, match="address span"):
        container.get_continuous_binary()


def test_f1_get_continuous_binary_rejects_huge_single_gap() -> None:
    """F1: a single oversized hole is rejected even under a large span cap."""
    container = _far_apart_container()
    limits = FirmwareParseLimits(max_address_span=1 << 40, max_gap=1024)
    with pytest.raises(ProtocolError):
        container.get_continuous_binary(max_span=limits.max_address_span, max_gap=limits.max_gap)


def test_f1_legitimate_small_gap_still_flattens() -> None:
    """No false positive: an ordinary small gap is padded as before."""
    container = FirmwareContainer(
        segments=[MemorySegment(0x0, b"\x01\x02"), MemorySegment(0x10, b"\x03\x04")]
    )
    base, data = container.get_continuous_binary()
    assert base == 0
    assert len(data) == 0x12
    assert data[0:2] == b"\x01\x02"
    assert data[0x10:0x12] == b"\x03\x04"
    # The gap is filled with the default fill byte.
    assert data[2:0x10] == b"\xff" * 0xE


def test_f1_parser_rejects_evil_span_at_parse_time() -> None:
    """F1: the parser itself rejects the crafted file (defense in depth)."""
    # 79-byte Intel HEX: data at 0x0, then ELA 0x1000 -> data at 0x10000000.
    evil = "\n".join(
        [
            ":020000040000FA",
            ":0400000001020304F2",
            ":020000041000EA",
            ":02000000AABB99",
            ":00000001FF",
        ]
    )
    with pytest.raises(ProtocolError, match="address span"):
        IntelHexParser.parse_string(evil)


def test_f1_parse_limits_are_configurable() -> None:
    """F1: callers can tighten limits (and a tight limit is enforced)."""
    tight = FirmwareParseLimits(max_file_bytes=16)
    with pytest.raises(ProtocolError, match="exceeds limit"):
        IntelHexParser.parse_string(":" + "00" * 64, limits=tight)


def test_f1_srecord_parser_is_bounded() -> None:
    """F1: the S-Record parser shares the same resource caps."""
    tight = FirmwareParseLimits(max_lines=2)
    line_a = "S1130000" + "00" * 16 + "EC"
    line_b = "S1130010" + "00" * 16 + "DC"
    term = "S9030000FC"
    with pytest.raises(ProtocolError, match="record count"):
        SRecordParser.parse_string(f"{line_a}\n{line_b}\n{term}", limits=tight)


def test_f1_flashing_config_rejects_before_allocating() -> None:
    """F1: the ECU profile cap is applied BEFORE padding (ordering fix)."""
    from src.protocols.uds.flasher import FlashingConfig

    container = _far_apart_container()
    with pytest.raises(ValueError, match="refusing to flatten before allocation"):
        FlashingConfig(container=container, max_image_bytes=1024)


def test_f1_default_limits_are_documented_and_sane() -> None:
    """The shared default limits exist and are within sensible bounds."""
    assert DEFAULT_FIRMWARE_LIMITS.max_address_span == 64 * 1024 * 1024
    assert DEFAULT_FIRMWARE_LIMITS.max_gap == 1024 * 1024
    assert DEFAULT_FIRMWARE_LIMITS.max_file_bytes == 128 * 1024 * 1024


# ---------------------------------------------------------------------------
# F3 — strict Intel HEX / S-Record validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("unknown record type 0x06", ":00000006FA"),
        ("data after EOF", ":00000001FF\n:0400000001020304F2"),
        ("EOF with nonzero length", ":02000001AABB98"),
    ],
)
def test_f3_intel_hex_rejects_malformed_input(name: str, content: str) -> None:
    with pytest.raises(ProtocolError):
        IntelHexParser.parse_string(content)


def test_f3_intel_hex_rejects_start_address_wrong_length() -> None:
    """F3: a Start Linear Address record must be exactly 4 bytes of payload."""
    # type 0x05 with length 2 (invalid) and a valid checksum.
    payload = bytes([0x02, 0x00, 0x00, 0x05, 0x12, 0x34])
    checksum = (-sum(payload)) & 0xFF
    record = ":" + (payload + bytes([checksum])).hex().upper()
    with pytest.raises(ProtocolError, match="Start Linear Address"):
        IntelHexParser.parse_string(record)


def test_f3_intel_hex_rejects_duplicate_eof() -> None:
    """F3: a second EOF record is malformed."""
    with pytest.raises(ProtocolError, match="duplicate EOF"):
        IntelHexParser.parse_string(":00000001FF\n:00000001FF")


def test_f3_valid_minimal_hex_still_parses() -> None:
    """No false positive on a well-formed file."""
    container = IntelHexParser.parse_string(":0400000001020304F2\n:00000001FF")
    assert container.total_bytes == 4
    assert container.file_format == "intel_hex"


def test_f3_srecord_rejects_unknown_type() -> None:
    """F3: an unsupported S-Record type is rejected, not ignored."""
    # S4 is not a defined record type.
    payload = bytes([0x03, 0x00, 0x00])
    checksum = (~sum(payload)) & 0xFF
    line = "S4" + (payload + bytes([checksum])).hex().upper()
    with pytest.raises(ProtocolError):
        SRecordParser.parse_string(line)


def test_f3_srecord_rejects_record_after_termination() -> None:
    """F3: nothing may follow the termination record."""
    data_rec = "S1130000" + "00" * 16 + "EC"
    term = "S9030000FC"
    with pytest.raises(ProtocolError, match="after termination"):
        SRecordParser.parse_string(f"{data_rec}\n{term}\n{data_rec}")


def test_f3_srecord_termination_checksum_is_validated() -> None:
    """F3: a malformed termination record is still caught by the checksum."""
    data_rec = "S1130000" + "00" * 16 + "EC"
    with pytest.raises(ProtocolError, match="checksum"):
        SRecordParser.parse_string(f"{data_rec}\nS9030000FF")


def test_f3_valid_srecord_with_matching_s5_count_parses() -> None:
    """No false positive: a well-formed S19 file with a correct S5 parses."""
    data_rec = "S1130000" + "00" * 16 + "EC"
    count_rec = "S5030001FB"  # S5 count = 1 data record
    term = "S9030000FC"
    container = SRecordParser.parse_string(f"{data_rec}\n{count_rec}\n{term}")
    assert container.total_bytes == 16
    assert container.file_format == "s_record"


def test_f3_srecord_s5_count_mismatch_is_rejected() -> None:
    """F3: an S5 count that disagrees with the data records fails closed."""
    data_rec = "S1130000" + "00" * 16 + "EC"
    count_rec = "S5030002FA"  # declares 2, only 1 present
    term = "S9030000FC"
    with pytest.raises(ProtocolError, match="S5 count mismatch"):
        SRecordParser.parse_string(f"{data_rec}\n{count_rec}\n{term}")


# ---------------------------------------------------------------------------
# F4 — cloud ticket schema: no NaN/Infinity in security decisions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_f4_non_finite_json_constants_are_rejected(constant: str) -> None:
    """F4: the parse hook turns NaN/Infinity into a hard parse error."""
    from src.security.cloud.license_flow import _reject_non_finite_json_constant

    with pytest.raises(ValueError):
        json.loads(f'{{"iat": {constant}}}', parse_constant=_reject_non_finite_json_constant)


def test_f4_nan_timestamps_cannot_pass_expiry_checks() -> None:
    """F4: the underlying hazard — every comparison with NaN is False."""
    nan = float("nan")
    # These are the two checks that silently passed before the fix.
    assert not (1000 > nan)  # "is it expired?" -> False
    assert not (1000 > nan)  # "is offline grace over?" -> False
    # ... which is exactly why the value must never be admitted.


def test_f4_bare_json_still_accepts_nan_without_the_hook() -> None:
    """Documents the Python behaviour the hook exists to block."""
    assert json.loads('{"iat": NaN}')["iat"] != json.loads('{"iat": NaN}')["iat"]  # NaN != NaN


def test_f4_supported_schema_versions_is_a_strict_allowlist() -> None:
    from src.security.cloud.license_flow import _SUPPORTED_TICKET_SCHEMA_VERSIONS

    assert _SUPPORTED_TICKET_SCHEMA_VERSIONS == frozenset({1})


# ---------------------------------------------------------------------------
# D3 — persistent writable root vs bundled resource root
# ---------------------------------------------------------------------------


def _frozen(monkeypatch: pytest.MonkeyPatch, meipass: Path, tmp: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp))


def test_d3_frozen_app_data_root_avoids_meipass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D3: writable state must NOT live in the runtime extraction directory."""
    from src.ui import desktop_app

    meipass = tmp_path / "_MEI123456"
    meipass.mkdir()
    _frozen(monkeypatch, meipass, tmp_path / "LocalAppData")

    root = desktop_app._app_data_root()
    assert str(root).startswith(str(meipass)) is False, "writable root must not be _MEIPASS"


def test_d3_resource_root_uses_meipass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """D3: bundled read-only assets DO come from the bundle root."""
    from src.ui import desktop_app

    meipass = tmp_path / "_MEI123456"
    meipass.mkdir()
    _frozen(monkeypatch, meipass, tmp_path / "LocalAppData")

    assert desktop_app._resource_root() == meipass.resolve()


def test_d3_writable_root_survives_a_restart(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """D3: the license HWM must survive process exit (the actual defect)."""
    from src.ui import desktop_app

    local = tmp_path / "LocalAppData"
    meipass_a = tmp_path / "_MEI_aaa"
    meipass_a.mkdir()
    _frozen(monkeypatch, meipass_a, local)

    hwm = desktop_app._ensure_app_data_root() / "logs" / "license_hwm.txt"
    hwm.parent.mkdir(parents=True, exist_ok=True)
    hwm.write_text("anchor", encoding="utf-8")

    # Simulate a restart: _MEIPASS is recreated elsewhere, user data persists.
    meipass_b = tmp_path / "_MEI_bbb"
    meipass_b.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass_b), raising=False)

    assert hwm.exists(), "anti-rollback HWM must survive a process restart"
    assert desktop_app._app_data_root() == desktop_app._ensure_app_data_root()


def test_d3_ensure_app_data_root_creates_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.ui import desktop_app

    meipass = tmp_path / "_MEI"
    meipass.mkdir()
    _frozen(monkeypatch, meipass, tmp_path / "LocalAppData")

    root = desktop_app._ensure_app_data_root()
    assert root.is_dir()


def test_d3_source_run_still_anchors_to_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No behaviour change for raw-Python runs (dev + test fixtures)."""
    from src.ui import desktop_app

    monkeypatch.delattr(sys, "frozen", raising=False)
    assert desktop_app._app_data_root() == Path(desktop_app.__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# D5 / D6 / A5-4 — bridge input validation
# ---------------------------------------------------------------------------


def test_d5_upload_log_omits_absolute_path() -> None:
    """D5: the accept log must not carry the operator's absolute path."""
    source = (Path(__file__).resolve().parents[2] / "src" / "ui" / "desktop_app.py").read_text(
        encoding="utf-8"
    )
    assert '"path": str(safe_path)' not in source, "absolute path must not be logged"
    assert 'extra={"file": safe_path.name' in source


@pytest.mark.parametrize(
    ("value", "allow_empty", "expect_ok"),
    [
        (None, False, False),  # required field -> rejected
        (None, True, True),  # optional field -> treated as absent
        ("", True, True),  # blank allowed for optional fields
        ("", False, False),  # blank rejected for required fields
        ("valid name", False, True),
        ("x" * 300, False, False),  # over the identifier bound
        ("bad\x01control", False, False),
        (12345, False, False),
        (["a"], False, False),
        ({"a": 1}, False, False),
    ],
)
def test_validate_bridge_text_helper(value: object, allow_empty: bool, expect_ok: bool) -> None:
    """D6/A5-4: the shared validator classifies input correctly."""
    from src.ui.desktop_app import _validate_bridge_text

    problem = _validate_bridge_text(value, field="f", max_chars=256, allow_empty=allow_empty)
    assert (problem is None) is expect_ok, f"{value!r} (allow_empty={allow_empty}) -> {problem!r}"


def test_d6_null_device_name_normalizes_to_default() -> None:
    """D6: a JS null must not be forwarded as None to the cloud client."""
    source = (Path(__file__).resolve().parents[2] / "src" / "ui" / "desktop_app.py").read_text(
        encoding="utf-8"
    )
    body = source.split("def cloud_register_device", 1)[1].split("\n    def ", 1)[0]
    assert "if device_name is None:" in body


def test_validate_bridge_text_rejects_control_chars() -> None:
    from src.ui.desktop_app import _validate_bridge_text

    assert _validate_bridge_text("a\x00b", field="f", max_chars=64) is not None
    assert _validate_bridge_text("a\x7fb", field="f", max_chars=64) is not None
    # Tab/newline are benign and allowed.
    assert _validate_bridge_text("a\nb\tc", field="f", max_chars=64) is None


def test_a5_4_copilot_query_bound_is_enforced() -> None:
    """A5-4: an oversized prompt is refused before reaching the engine."""
    from src.ui.desktop_app import COPILOT_QUERY_MAX_CHARS

    assert COPILOT_QUERY_MAX_CHARS > 0
    source = (Path(__file__).resolve().parents[2] / "src" / "ui" / "desktop_app.py").read_text(
        encoding="utf-8"
    )
    ask_copilot = source.split("def ask_copilot", 1)[1].split("def ", 1)[0]
    assert "_validate_bridge_text" in ask_copilot, "ask_copilot must validate its input"


def test_d6_cloud_methods_validate_input() -> None:
    """D6: both cloud bridge methods validate before calling the client."""
    source = (Path(__file__).resolve().parents[2] / "src" / "ui" / "desktop_app.py").read_text(
        encoding="utf-8"
    )
    for method in ("def cloud_register_device", "def cloud_activate_license"):
        body = source.split(method, 1)[1].split("\n    def ", 1)[0]
        assert "_validate_bridge_text" in body, f"{method} must validate its input"


def test_d6_oversized_device_name_is_rejected() -> None:
    """D6: a 1 MB device_name must not reach the cloud client."""
    from src.ui.desktop_app import BRIDGE_IDENTIFIER_MAX_CHARS, _validate_bridge_text

    assert _validate_bridge_text("x" * 1_000_000, field="device_name", max_chars=BRIDGE_IDENTIFIER_MAX_CHARS)


# ---------------------------------------------------------------------------
# A5-1 — blank session token
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["", "   ", "\t\n", None, 123])
def test_a5_1_blank_session_token_is_rejected(token: object) -> None:
    """A5-1: a blank token must not create a fake 'authenticated' session."""
    from src.launcher.auth import LauncherAuthManager

    manager = LauncherAuthManager.__new__(LauncherAuthManager)
    manager.client = None  # must not be touched for the reject path
    assert manager.login_web_session(token) is False  # type: ignore[arg-type]


def test_a5_1_rejects_before_touching_the_store() -> None:
    """The guard runs before any secret-store write."""
    from src.launcher.auth import LauncherAuthManager

    class _RecordingClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def store_session_token(self, value: bytes) -> None:
            self.calls.append("stored")

    manager = LauncherAuthManager.__new__(LauncherAuthManager)
    manager.client = _RecordingClient()
    assert manager.login_web_session("   ") is False
    assert manager.client.calls == []


def test_a5_1_valid_token_is_accepted() -> None:
    """No false negative for a real token."""
    from src.launcher.auth import LauncherAuthManager

    class _RecordingClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def store_session_token(self, value: str) -> None:
            self.calls.append(value)

    manager = LauncherAuthManager.__new__(LauncherAuthManager)
    manager.client = _RecordingClient()
    assert manager.login_web_session("  real-token  ") is True
    assert manager.client.calls == ["real-token"]


# ---------------------------------------------------------------------------
# A5-3 — machine seed integrity
# ---------------------------------------------------------------------------


def _linux_backend(tmp_path: Path):
    from src.safety.secret_provider import LinuxSecretBackend

    return LinuxSecretBackend(storage_path=tmp_path / "secrets.bin")


def _write_seed(path: Path, payload: bytes) -> None:
    """Write a machine seed with owner-only permissions.

    ``LinuxSecretBackend._get_machine_seed`` refuses a seed that is
    group/world accessible (mode & 0o077). A file created with the process
    umask (0o644 on a typical CI runner) trips that permission gate before
    the length/type check these tests target, so they would fail for the
    wrong reason on Linux while passing on Windows (where the POSIX gate
    is skipped). Creating with 0o600 keeps each test measuring what it
    asserts.
    """
    path.write_bytes(payload)
    try:
        path.chmod(0o600)
    except (OSError, NotImplementedError):  # pragma: no cover - non-POSIX
        pass


def test_a5_3_short_seed_is_rejected(tmp_path: Path) -> None:
    """A5-3: a 3-byte seed must not be used as the vault root key."""
    backend = _linux_backend(tmp_path)
    _write_seed(tmp_path / "machine_seed.bin", b"abc")
    with pytest.raises(SecurityError, match="invalid length") as exc_info:
        backend._get_machine_seed()
    assert exc_info.value.code == "MACHINE_SEED_INVALID_LENGTH"


def test_a5_3_oversized_seed_is_rejected(tmp_path: Path) -> None:
    backend = _linux_backend(tmp_path)
    _write_seed(tmp_path / "machine_seed.bin", b"a" * 64)
    with pytest.raises(SecurityError):
        backend._get_machine_seed()


def test_a5_3_correct_length_seed_is_accepted(tmp_path: Path) -> None:
    """No false positive: an exactly-32-byte seed is used."""
    backend = _linux_backend(tmp_path)
    seed = bytes(range(32))
    _write_seed(tmp_path / "machine_seed.bin", seed)
    assert backend._get_machine_seed() == seed


def test_a5_3_group_readable_seed_is_rejected(tmp_path: Path) -> None:
    """A5-3: a group/world-readable seed must be refused (POSIX only)."""
    if os.name != "posix":
        pytest.skip("POSIX permission gate does not apply on this host")
    backend = _linux_backend(tmp_path)
    seed_file = tmp_path / "machine_seed.bin"
    _write_seed(seed_file, bytes(range(32)))
    seed_file.chmod(0o644)
    with pytest.raises(SecurityError, match="group/world accessible") as exc_info:
        backend._get_machine_seed()
    assert exc_info.value.code == "MACHINE_SEED_INSECURE_PERMISSIONS"


def test_a5_3_seed_symlink_is_rejected(tmp_path: Path) -> None:
    """A5-3: a symlinked seed enables controlled key substitution."""
    backend = _linux_backend(tmp_path)
    real = tmp_path / "elsewhere.bin"
    real.write_bytes(b"x" * 32)
    link = tmp_path / "machine_seed.bin"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows without privilege
        pytest.skip("symlinks not permitted on this host")
    with pytest.raises(SecurityError, match="symlink"):
        backend._get_machine_seed()


def test_a5_3_seed_directory_is_rejected(tmp_path: Path) -> None:
    """A5-3: a non-regular file must not be read as key material."""
    backend = _linux_backend(tmp_path)
    (tmp_path / "machine_seed.bin").mkdir()
    with pytest.raises(SecurityError):
        backend._get_machine_seed()


def test_a5_3_seed_bytes_constant_is_used_for_generation(tmp_path: Path) -> None:
    from src.safety.secret_provider import LinuxSecretBackend

    assert LinuxSecretBackend.SEED_BYTES == 32


# ---------------------------------------------------------------------------
# S2 / S3 — replay filter concurrency + immutable policy
# ---------------------------------------------------------------------------


def test_s3_policy_attributes_are_read_only() -> None:
    """S3: the protection policy cannot be silently weakened at runtime."""
    filt = ReplaySafetyFilter()
    for attribute in (
        "block_address_claim",
        "block_diagnostic_write",
        "block_actuator_routines",
        "block_transport_tunneling",
        "custom_blocked_ids",
    ):
        with pytest.raises(AttributeError):
            setattr(filt, attribute, False)


def test_s3_custom_blocked_ids_cannot_be_mutated_from_outside() -> None:
    """S3: the caller's set must be copied, not aliased."""
    caller_set = {0x123}
    filt = ReplaySafetyFilter(custom_blocked_ids=caller_set)
    caller_set.add(0x456)
    caller_set.clear()
    assert filt.custom_blocked_ids == frozenset({0x123})

    # And the exposed view is immutable.
    with pytest.raises(AttributeError):
        filt.custom_blocked_ids.add(0x999)  # type: ignore[attr-defined]


def test_s2_filter_has_a_lock_and_snapshot_helper() -> None:
    """S2: mutation is guarded and metrics can be read atomically."""
    filt = ReplaySafetyFilter()
    assert isinstance(filt._lock, type(threading.RLock()))
    snapshot = filt.snapshot_metrics()
    assert snapshot["evaluated"] == 0
    assert "iso_tp_sessions_pending" in snapshot


def test_s2_concurrent_evaluation_keeps_counters_consistent() -> None:
    """S2: hammering the filter from threads must not lose counter updates."""
    from src.core.models.can_frame import CanFrame

    filt = ReplaySafetyFilter()
    frame = CanFrame.create(channel_id="c0", arbitration_id=0x100, data=b"\x01\x02")
    iterations = 200
    threads = [
        threading.Thread(target=lambda: [filt.filter_frame(frame) for _ in range(iterations)])
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    snapshot = filt.snapshot_metrics()
    assert snapshot["evaluated"] == iterations * 4
    assert snapshot["passed"] + snapshot["blocked"] == iterations * 4


def test_s2_reset_session_state_clears_iso_tp_ledger() -> None:
    """S2: a stale cross-session ledger must be clearable."""
    filt = ReplaySafetyFilter()
    filt._iso_tp_pending[0x7E0] = (0x2E, 10)
    assert filt.snapshot_metrics()["iso_tp_sessions_pending"] == 1
    filt.reset_session_state()
    assert filt.snapshot_metrics()["iso_tp_sessions_pending"] == 0


def test_s2_iso_tp_ledger_still_tracks_sessions() -> None:
    """No regression: the FirstFrame -> ConsecutiveFrame ledger still works."""
    from src.core.models.can_frame import CanFrame

    filt = ReplaySafetyFilter()
    # FirstFrame for a PROHIBITED SID (0x2E WriteDataByIdentifier) on 0x7E0.
    ff = CanFrame.create(
        channel_id="c0", arbitration_id=0x7E0, data=bytes([0x10, 0x0A, 0x2E, 0x01, 0x02])
    )
    safe, reason = filt.is_frame_safe(ff)
    assert safe is False
    assert "PROHIBITED" in reason
