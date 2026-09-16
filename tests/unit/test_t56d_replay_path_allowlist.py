"""T56-D regression tests — KRİTİK C-1 (+ O-5, D-4).

C-1: ``DesktopApiBridge.replay_load`` / ``UniversalCanDesktopApp.load_replay``
     accepted any IPC-supplied ``file_path`` and resolved it with no root
     allowlist (UNC/SMB coercion, ``dist/`` takeover, arbitrary FS read).
O-5: ``replay_load`` performed no type/length validation on ``file_path``.
D-4: ``UniversalCanLauncher.resolve_target_executable`` accepted any binary
     dropped into ``dist/`` with no hash-manifest verification.

These tests were written BEFORE the fix (TDD) and must FAIL on the pre-fix
code: a UNC/device/out-of-root path was happily accepted and opened.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.launcher.app import UniversalCanLauncher, _load_target_manifest, _verify_target_hash
from src.ui.desktop_app import DesktopApiBridge, _app_data_root


def _valid_trace_bytes() -> str:
    return (
        "date Mon Jan 1 00:00:00 2024\n"
        "base hex timestamps absolute\n"
        "   0.001000 1  18FEE100x       Rx d 8 50 10 20 30 40 50 60 70\n"
        "   0.002000 1  18FEE100x       Rx d 8 51 10 20 30 40 50 60 70\n"
    )


@pytest.fixture()
def trace_in_allowed_root() -> Path:
    root = _app_data_root() / "data" / "traces"
    root.mkdir(parents=True, exist_ok=True)
    f = root / "t56d_valid_trace.asc"
    f.write_text(_valid_trace_bytes())
    yield f
    f.unlink(missing_ok=True)


def _bridge() -> DesktopApiBridge:
    from src.ui.desktop_app import UniversalCanDesktopApp

    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    return DesktopApiBridge(app)


class TestC1ReplayLoadRejectsHostilePaths:
    """C-1: hostile file paths must be refused (fail-closed) — pre-fix these
    were accepted and handed straight to ``Path.resolve()`` + ``open()``."""

    @pytest.mark.parametrize(
        "hostile",
        [
            r"\\attacker.example\share\poc.asc",  # UNC -> SMB / NTLM coercion
            r"\\?\C:\Windows\System32\config\SAM",  # \\?\ extended-length
            r"\\.\PhysicalDrive0",  # device namespace
            r"\\.\GLOBALROOT\Device\HarddiskVolume1\x.asc",
            r"\\.\pipe\evil.asc",  # named pipe
            "CON",  # reserved DOS device name
            "NUL",
            "COM1",
            "LPT1",
            r"C:\Windows\System32\drivers\etc\hosts",  # out-of-root absolute
            "../outside_the_root.asc",  # traversal out of the tree
            "secrets.asc",  # relative, not under an app-owned root
        ],
    )
    def test_replay_load_rejects_hostile_paths(self, hostile: str) -> None:
        res = _bridge().replay_load(hostile)
        assert res["success"] is False
        assert res.get("error")
        # Fail-closed: the hostile string must never be echoed back as a
        # resolved filesystem location.
        assert "path" not in res

    def test_replay_load_rejects_out_of_root_tmp_file(self, tmp_path: Path) -> None:
        """A real, readable .asc OUTSIDE the app roots is still refused."""
        evil = tmp_path / "real_but_out_of_root.asc"
        evil.write_text(_valid_trace_bytes())
        res = _bridge().replay_load(str(evil))
        assert res["success"] is False
        assert "path" not in res

    def test_replay_load_rejects_directory(self) -> None:
        """is_file() gate: an allowed-root *directory* is not a trace."""
        root = _app_data_root() / "data" / "traces"
        root.mkdir(parents=True, exist_ok=True)
        res = _bridge().replay_load(str(root))
        assert res["success"] is False

    def test_replay_load_rejects_disallowed_extension_in_root(self) -> None:
        root = _app_data_root() / "data" / "traces"
        root.mkdir(parents=True, exist_ok=True)
        evil = root / "t56d_payload.exe"
        evil.write_bytes(b"MZ")
        try:
            res = _bridge().replay_load(str(evil))
            assert res["success"] is False
        finally:
            evil.unlink(missing_ok=True)

    def test_replay_load_accepts_valid_trace_in_allowed_root(self, trace_in_allowed_root: Path) -> None:
        """Positive control: the guard must not break legitimate traces."""
        res = _bridge().replay_load(str(trace_in_allowed_root))
        assert res["success"] is True
        assert res["frame_count"] == 2

    def test_load_replay_app_layer_also_guarded(self) -> None:
        """C-1 covers BOTH layers: the app method (not just the bridge)
        must refuse a hostile path even when called directly."""
        from src.ui.desktop_app import UniversalCanDesktopApp

        app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
        res = app.load_replay(r"\\attacker.example\share\poc.asc")
        assert res["success"] is False
        assert app.replay_bus is None


class TestO5ReplayLoadTypeAndLengthValidation:
    """O-5: ``replay_load`` is an independent IPC endpoint — it must coerce/
    reject non-str and over-long inputs instead of raising raw OS errors."""

    @pytest.mark.parametrize("bad", [None, 123, 12.5, b"bytes.path", ["/etc/passwd"], {"a": 1}])
    def test_non_string_path_rejected(self, bad: object) -> None:
        res = _bridge().replay_load(bad)  # type: ignore[arg-type]
        assert res["success"] is False
        assert "path" not in res

    def test_overlong_path_rejected(self) -> None:
        res = _bridge().replay_load("a" * 5000)
        assert res["success"] is False
        assert "uzun" in res["error"].lower() or "length" in res["error"].lower()

    def test_empty_and_null_byte_path_rejected(self) -> None:
        assert _bridge().replay_load("")["success"] is False
        assert _bridge().replay_load("ok\x00.asc")["success"] is False


class TestD4ResolveTargetExecutableHashManifest:
    """D-4: the launcher must verify the selected binary against a
    signed-ish hash manifest; an unsigned binary dropped in dist/ is a
    red-alert (refused when the manifest forbids unsigned targets)."""

    def test_manifest_helper_reads_and_hashes(self, tmp_path: Path) -> None:
        manifest = tmp_path / "dist" / "target.hash"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        payload = b"GENUINE_BINARY"
        manifest.write_text(hashlib.sha256(payload).hexdigest() + "\n")
        assert _load_target_manifest(manifest) == hashlib.sha256(payload).hexdigest()

        exe = tmp_path / "target.exe"
        exe.write_bytes(payload)
        assert _verify_target_hash(exe, hashlib.sha256(payload).hexdigest()) is True
        exe.write_bytes(b"TAMPERED")
        assert _verify_target_hash(exe, hashlib.sha256(payload).hexdigest()) is False

    def test_tampered_binary_is_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        dist = tmp_path / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        exe = dist / "ucanlab.exe"
        exe.write_bytes(b"MALICIOUS_PAYLOAD")
        manifest = tmp_path / "target.hash"
        manifest.write_text("0" * 64 + "\n")
        monkeypatch.setenv("UCANLAB_TARGET_MANIFEST", str(manifest))
        with pytest.raises(RuntimeError, match="hash|manifest|imza"):
            UniversalCanLauncher.verify_resolved_target(exe)

    def test_matching_binary_accepted_when_manifest_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manifest = tmp_path / "target.hash"
        payload = b"GENUINE_BINARY"
        (tmp_path / "bin").mkdir(parents=True, exist_ok=True)
        exe = tmp_path / "bin" / "ucanlab.exe"
        exe.write_bytes(payload)
        manifest.write_text(hashlib.sha256(payload).hexdigest() + "\n")
        monkeypatch.setenv("UCANLAB_TARGET_MANIFEST", str(manifest))
        assert UniversalCanLauncher.verify_resolved_target(exe) is True

    def test_missing_manifest_is_not_a_silent_bypass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No manifest => only the raw-python entry point is acceptable;
        an unsigned frozen exe in dist/ must be rejected."""
        dist = tmp_path / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        exe = dist / "ucanlab.exe"
        exe.write_bytes(b"UNSIGNED")
        monkeypatch.setenv("UCANLAB_TARGET_MANIFEST", str(tmp_path / "missing.hash"))
        with pytest.raises(RuntimeError):
            UniversalCanLauncher.verify_resolved_target(exe)
