"""Batch F (TUR-2 round 2) — launcher remediation regression suite.

Covers the verified-OPEN findings from the launcher audit:

  C-1  launcher must not kill the child after 300 s
  C-2  the updater is a staging pipeline + an explicit gated install step
  H-1  the /updates/latest manifest body itself must be Ed25519-signed
  H-2  a non-mandatory newer release may not clear the obligation
  H-3  a deleted obligation record is TAMPERED, not "no obligation"
  H-4  both build scripts emit data/target.hash (static assertions only)
  H-5  frozen builds anchor to sys.executable, not to the source tree
  H-6  one byte ceiling for download + verification
  M-1  WebView2 is non-critical in --cli mode; VCRedist probes more than one key
  M-2  the update host allowlist shares the canonical cloud host set
  M-3  launch_main_app re-applies the preflight/license gate
  M-4  the .py branch is case-insensitive
  M-5  one embedded public key source + fail-closed decode
  M-6  the UI coupling is documented (not moved: out of scope)
  M-7  documented short flags are forwarded, not dropped
  M-8  --activate takes no argv value (getpass)
  M-9  SHA-256 comparison validates the digest format
  L-1  no "Vector" claim in the prereqs docstring
  L-2  AuthStatus().user_email is gone
  L-3  the dead `or not self.flow` branch is gone
  L-4  `import urllib.error` is gone
  L-5  `_unsigned_manifest_allowed` is gone
  L-6  download_update does not create directories before its gates
  L-7  _parse_semver requires exactly three numeric components
  L-8  main() surfaces check_succeeded=False

Everything is mock-based: no network, no frozen build, no real artifact.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

import src.launcher.paths as launcher_paths
from src.core.errors import LicenseError
from src.launcher import app as app_mod
from src.launcher.auth import AuthStatus, LauncherAuthManager
from src.launcher.manifest_sig import canonical_manifest_bytes
from src.launcher.prereqs import PrereqChecker, PrereqStatus
from src.launcher.updater import (
    ALLOWED_UPDATE_HOSTS,
    UNVERIFIED_VERSION,
    UpdateInfo,
    UpdateManager,
)
from src.safety.secret_provider import EphemeralSecretBackend, InMemorySecretProvider
from src.security.cloud.client import CANONICAL_CLOUD_HOSTS, CloudClient, CloudConfig

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _keypair() -> tuple[ed25519.Ed25519PrivateKey, ed25519.Ed25519PublicKey]:
    priv = ed25519.Ed25519PrivateKey.generate()
    return priv, priv.public_key()


def _signed_manifest(
    priv: ed25519.Ed25519PrivateKey,
    fields: dict[str, Any],
    kind: str,
    *,
    kid: str = "v1",
    body_key_id: str | None = None,
) -> dict[str, Any]:
    """Build a manifest whose detached signature covers its own body.

    `kind` selects the framing: "bare" (single base64), "prefixed"
    (`<kid>:<b64>`) or "field" (bare signature + a separate key-id field).
    """
    fields = dict(fields)
    if body_key_id is not None:
        fields["manifest_kid"] = body_key_id
    digest = priv.sign(canonical_manifest_bytes(fields))
    encoded = base64.b64encode(digest).decode("ascii")
    if kind == "prefixed":
        encoded = f"{kid}:{encoded}"
    return {**fields, "manifest_signature": encoded}


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.status = status
        self.body = body
        self.headers: dict[str, str] = {}

    def json_object(self) -> dict[str, Any]:
        parsed = json.loads(self.body.decode("utf-8"))
        assert isinstance(parsed, dict)
        return parsed


class _FakeCloudClient:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._response = _FakeResponse(body, status)
        self.requests: list[tuple[str, str]] = []

    def request(self, method: str, path: str) -> _FakeResponse:
        self.requests.append((method, path))
        return self._response


@pytest.fixture(autouse=True)
def _isolate_launcher_root(tmp_path: Path) -> Any:
    """Give every test in this module its own launcher root.

    The override goes into the real process environment (the launcher reads it
    via ``os.environ``) and is restored explicitly afterwards, so it never
    leaks into the shared launcher suites — a leaked root made them resolve
    ``src/main.py`` under an already-deleted tmp dir.
    """
    previous = os.environ.get(launcher_paths.LAUNCHER_ROOT_ENV)
    os.environ[launcher_paths.LAUNCHER_ROOT_ENV] = str(tmp_path)
    try:
        yield tmp_path
    finally:
        if previous is None:
            os.environ.pop(launcher_paths.LAUNCHER_ROOT_ENV, None)
        else:
            os.environ[launcher_paths.LAUNCHER_ROOT_ENV] = previous


def _launcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, version: str = "13.0.0") -> Any:
    """Launcher in test mode with a vault-isolated obligation record root."""
    monkeypatch.setenv(launcher_paths.LAUNCHER_ROOT_ENV, str(tmp_path))
    secrets = EphemeralSecretBackend()
    client = CloudClient(config=CloudConfig(), secret_provider=secrets)
    priv, pub = _keypair()
    auth = LauncherAuthManager(cloud_client=client, secret_provider=secrets, public_key=pub)
    return app_mod.UniversalCanLauncher.for_testing(current_version=version, auth_manager=auth)


def _ok_prereqs() -> list[PrereqStatus]:
    return [PrereqStatus(name="dummy", is_available=True, details="ok", is_critical=True)]


# ===========================================================================
# C-1 — the launcher must not bound the child's lifetime
# ===========================================================================


def test_c1_subprocess_call_receives_no_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """C-1: `timeout=300` killed a healthy 5-minute session; it must be gone."""
    target = tmp_path / "Main.PY"
    target.write_text("print('ok')\n", encoding="utf-8")
    captured: dict[str, Any] = {}

    def _fake_call(cmd: list[str], **kwargs: Any) -> int:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(app_mod.subprocess, "call", _fake_call)
    monkeypatch.setattr(app_mod.UniversalCanLauncher, "run_preflight", lambda self: _report(tmp_path, target))
    monkeypatch.setattr(app_mod.UniversalCanLauncher, "resolve_target_executable", classmethod(lambda cls: target))

    launcher = app_mod.UniversalCanLauncher.for_testing(current_version="13.0.0")
    assert launcher.launch_main_app() == 0
    assert "timeout" not in captured["kwargs"], f"launcher still bounds the child: {captured['kwargs']}"
    assert captured["kwargs"].get("shell") is False


def _capturing_call(captured: dict[str, Any], rc: int = 0) -> Any:
    """subprocess.call stand-in that records its arguments and returns rc."""

    def _fake(cmd: list[str], **kwargs: Any) -> int:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return rc

    return _fake


def test_c1_launch_source_has_no_timeout_literal() -> None:
    """C-1 (static): no timeout= argument survives in launch_main_app."""
    source = Path(app_mod.__file__).read_text(encoding="utf-8")
    body = source.split("def launch_main_app", 1)[1].split("\ndef ", 1)[0]
    final_call = body.rsplit("return subprocess.call", 1)[1].split("\n", 1)[0]
    assert "shell=False" in final_call
    assert "timeout" not in final_call
    # exactly one spawn: the launcher must not fall back to a second, bounded call
    assert body.count("return subprocess.call") == 1


# ===========================================================================
# H-5 — frozen builds anchor to the launcher's own directory
# ===========================================================================


def test_h5_app_root_anchors_to_sys_executable_when_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H-5: a frozen build must resolve paths from sys.executable."""
    monkeypatch.delenv(launcher_paths.LAUNCHER_ROOT_ENV, raising=False)
    monkeypatch.setattr(app_mod.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app_mod.sys, "executable", str(tmp_path / "ucanlab_launcher.exe"), raising=False)

    resolved = launcher_paths.app_root()
    assert resolved == tmp_path.resolve(), f"frozen root must be the exe dir, got {resolved}"
    assert resolved != launcher_paths.repo_root()


def test_h5_resolve_target_executable_uses_app_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """H-5: the payload is looked up next to the frozen launcher."""
    monkeypatch.delenv(launcher_paths.LAUNCHER_ROOT_ENV, raising=False)
    monkeypatch.setattr(app_mod.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app_mod.sys, "executable", str(tmp_path / "ucanlab_launcher.exe"), raising=False)
    dist = tmp_path / "dist"
    dist.mkdir()
    payload = dist / "ucanlab.exe"
    payload.write_bytes(b"MZ-payload")

    assert app_mod.UniversalCanLauncher.resolve_target_executable() == payload


def test_h5_source_checkout_still_uses_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """H-5 control: running from source keeps the checkout anchor."""
    monkeypatch.delenv(launcher_paths.LAUNCHER_ROOT_ENV, raising=False)
    monkeypatch.delattr(app_mod.sys, "frozen", raising=False)
    assert launcher_paths.app_root() == launcher_paths.repo_root()


def test_h5_manifest_path_still_frozen_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """H-5/T62-U2: the manifest override stays ignored in a frozen build."""
    monkeypatch.setattr(app_mod.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app_mod.sys, "executable", str(tmp_path / "ucanlab_launcher.exe"), raising=False)
    monkeypatch.setenv(app_mod.TARGET_MANIFEST_ENV, str(tmp_path / "attacker.hash"))
    resolved = app_mod._manifest_path()
    assert resolved.name == "target.hash"
    assert "attacker" not in str(resolved)


# ===========================================================================
# H-1 — the manifest body must be signed before any field is trusted
# ===========================================================================


def test_h1_unsigned_manifest_yields_failed_check() -> None:
    """H-1: no manifest signature => no trusted field, check reported FAILED."""
    priv, pub = _keypair()
    _ = priv
    body = json.dumps({"version": "99.0.0", "mandatory": True, "download_url": "https://ucan-cloud.si6n.io/x.exe"}).encode()
    manager = UpdateManager(current_version="13.0.0", cloud_client=_FakeCloudClient(body), public_key=pub)

    info = manager._query_cloud_for_updates()

    assert info.check_succeeded is False
    assert info.has_update is False
    assert info.mandatory is False
    assert info.manifest_verified is False
    assert info.download_url == ""
    # H-1: the reported "latest" is the current version (there is no trusted
    # version in the response), never the attacker-supplied 99.0.0.
    assert info.latest_version == info.current_version
    assert UNVERIFIED_VERSION  # sentinel constant remains exported for callers


def test_h1_invalid_signature_is_refused() -> None:
    """H-1: a signature from the wrong key must not be trusted."""
    _attacker_priv, attacker_pub = _keypair()
    real_priv, _real_pub = _keypair()
    forged = _signed_manifest(real_priv, {"version": "99.0.0", "mandatory": True}, "bare")
    body = json.dumps(forged).encode()
    manager = UpdateManager(current_version="13.0.0", cloud_client=_FakeCloudClient(body), public_key=attacker_pub)

    info = manager._query_cloud_for_updates()
    assert info.manifest_verified is False
    assert info.mandatory is False


def test_h1_valid_signature_is_trusted() -> None:
    """H-1 positive control: a correctly signed manifest still routes."""
    priv, pub = _keypair()
    signed = _signed_manifest(
        priv,
        {
            "version": "13.4.0",
            "mandatory": True,
            "download_url": "https://ucan-cloud.si6n.io/ucanlab.exe",
            "sha256": "a" * 64,
        },
        "bare",
    )
    manager = UpdateManager(
        current_version="13.0.0",
        cloud_client=_FakeCloudClient(json.dumps(signed).encode()),
        manifest_keys={"v1": pub},
    )

    info = manager._query_cloud_for_updates()
    assert info.manifest_verified is True
    assert info.check_succeeded is True
    assert info.mandatory is True
    assert info.latest_version == "13.4.0"


def test_h1_kid_prefixed_signature_and_unknown_kid() -> None:
    """H-1: `<kid>:<b64>` is honored; an unknown kid fails closed."""
    priv, pub = _keypair()
    payload = _signed_manifest(priv, {"version": "13.4.0", "mandatory": False}, "prefixed", kid="v1")

    good = UpdateManager(
        current_version="13.0.0",
        cloud_client=_FakeCloudClient(json.dumps(payload).encode()),
        manifest_keys={"v1": pub},
    )
    assert good._query_cloud_for_updates().manifest_verified is True

    unknown_kid = _signed_manifest(priv, {"version": "13.4.0", "mandatory": False}, "prefixed", kid="v9")
    bad = UpdateManager(
        current_version="13.0.0",
        cloud_client=_FakeCloudClient(json.dumps(unknown_kid).encode()),
        manifest_keys={"v1": pub},
    )
    assert bad._query_cloud_for_updates().manifest_verified is False


def test_h1_key_id_may_come_from_a_manifest_field() -> None:
    """H-1: `manifest_kid` selects the ring entry; a field/prefix clash fails."""
    priv, pub = _keypair()
    payload = _signed_manifest(
        priv,
        {"version": "13.4.0", "mandatory": False},
        "bare",
        body_key_id="v1",
    )
    good = UpdateManager(
        current_version="13.0.0",
        cloud_client=_FakeCloudClient(json.dumps(payload).encode()),
        manifest_keys={"v1": pub},
    )
    assert good._query_cloud_for_updates().manifest_verified is True

    clashing = _signed_manifest(
        priv,
        {"version": "13.4.0", "mandatory": False},
        "prefixed",
        kid="v9",
        body_key_id="v1",
    )
    clash = UpdateManager(
        current_version="13.0.0",
        cloud_client=_FakeCloudClient(json.dumps(clashing).encode()),
        manifest_keys={"v1": pub},
    )
    assert clash._query_cloud_for_updates().manifest_verified is False


def test_h1_malformed_signature_is_refused() -> None:
    """H-1: garbage in the signature field is a hard reject, never a skip."""
    _priv, pub = _keypair()
    body = json.dumps({"version": "13.4.0", "manifest_signature": "!!!not-base64!!!"}).encode()
    manager = UpdateManager(
        current_version="13.0.0", cloud_client=_FakeCloudClient(body), manifest_keys={"v1": pub}
    )
    assert manager._query_cloud_for_updates().manifest_verified is False


def test_h1_test_only_escape_hatch_stays_explicitly_named_and_gated() -> None:
    """H-1: the unsigned escape hatch is reachable only via the named test API.

    It is an ALL-OR-NOTHING skip of the manifest-signature requirement (that
    is what "unsigned manifest allowed" means), so the important properties
    are: it is off by default, it is an explicit constructor flag, and it can
    never make the *package* checks (SHA-256 + Ed25519 over the binary)
    optional. An invalid signature is still refused while the hatch is OFF.
    """
    body = json.dumps({"version": "13.4.0", "mandatory": True}).encode()
    strict = UpdateManager(current_version="13.0.0", cloud_client=_FakeCloudClient(body))
    assert strict.allow_unsigned_manifest is False
    assert strict._query_cloud_for_updates().manifest_verified is False

    relaxed = UpdateManager(
        current_version="13.0.0", cloud_client=_FakeCloudClient(body), allow_unsigned_manifest=True
    )
    info = relaxed._query_cloud_for_updates()
    assert info.manifest_verified is True and info.mandatory is True
    # The hatch must NOT relax signature-required package downloads.
    assert relaxed.require_signature is True

    _priv, pub = _keypair()
    other_priv, _other_pub = _keypair()
    forged = json.dumps(
        _signed_manifest(other_priv, {"version": "13.4.0", "mandatory": True}, "bare")
    ).encode()
    forged_manager = UpdateManager(
        current_version="13.0.0",
        cloud_client=_FakeCloudClient(forged),
        manifest_keys={"v1": pub},
    )
    assert forged_manager._query_cloud_for_updates().manifest_verified is False


def test_h1_escape_hatch_is_refused_in_a_frozen_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """H-1: allow_unsigned_manifest is inert once sys.frozen is set."""
    monkeypatch.setattr(app_mod.sys, "frozen", True, raising=False)
    monkeypatch.setenv(launcher_paths.LAUNCHER_ROOT_ENV, str(tmp_path))
    launcher = app_mod.UniversalCanLauncher(current_version="13.0.0", allow_unsigned_manifest=True)
    assert launcher._allow_unsigned_manifest is False


def test_h1_unsigned_manifest_cannot_plant_a_blocking_obligation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H-1: app-level gate — an unverified mandatory flag is ignored."""
    launcher = _launcher(tmp_path, monkeypatch)
    monkeypatch.setattr(app_mod.PrereqChecker, "run_all_checks", classmethod(lambda cls: _ok_prereqs()))
    monkeypatch.setattr(launcher.update_manager, "check_for_updates", lambda: UpdateInfo(
        has_update=True,
        current_version="13.0.0",
        latest_version="99.0.0",
        mandatory=True,
        manifest_verified=False,
        check_succeeded=True,
    ))
    # `_launcher` uses the explicit test-only constructor, which legitimately
    # forwards an unverified manifest — so turn the hatch OFF for this case to
    # model a PRODUCTION launcher receiving an unsigned response.
    launcher._allow_unsigned_manifest = False
    launcher.update_manager.allow_unsigned_manifest = False

    report = launcher.run_preflight()
    assert report.update_info.mandatory is True
    assert report.can_launch is False
    # The untrusted flag must not create an obligation record.
    assert not launcher._obligation_path().exists(), "an unverified manifest planted an obligation"


# ===========================================================================
# H-2 — clearing the obligation requires actually satisfying it
# ===========================================================================


def test_h2_non_mandatory_release_does_not_clear_unsatisfied_obligation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H-2: running 13.0 with a 14.0 obligation + a 13.5 non-mandatory release."""
    launcher = _launcher(tmp_path, monkeypatch, version="13.0.0")
    monkeypatch.setattr(app_mod.PrereqChecker, "run_all_checks", classmethod(lambda cls: _ok_prereqs()))
    assert launcher._record_mandatory_obligation("14.0.0") is True
    assert launcher._load_recorded_mandatory_obligation() == "14.0.0"

    monkeypatch.setattr(launcher.update_manager, "check_for_updates", lambda: UpdateInfo(
        has_update=True,
        current_version="13.0.0",
        latest_version="13.5.0",
        mandatory=False,
        manifest_verified=True,
        check_succeeded=True,
    ))
    launcher.run_preflight()

    assert launcher._load_recorded_mandatory_obligation() == "14.0.0", "obligation was cleared without being satisfied"


def test_h2_satisfied_obligation_is_cleared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """H-2: running >= the obligated version clears the record."""
    launcher = _launcher(tmp_path, monkeypatch, version="14.1.0")
    monkeypatch.setattr(app_mod.PrereqChecker, "run_all_checks", classmethod(lambda cls: _ok_prereqs()))
    assert launcher._record_mandatory_obligation("14.0.0") is True
    assert launcher._load_recorded_mandatory_obligation() == "14.0.0"

    monkeypatch.setattr(launcher.update_manager, "check_for_updates", lambda: UpdateInfo(
        has_update=True,
        current_version="14.1.0",
        latest_version="14.2.0",
        mandatory=False,
        manifest_verified=True,
        check_succeeded=True,
    ))
    launcher.run_preflight()

    # Cleared on the merits: the record is GONE while the install is still
    # armed, so the loader must report "armed but absent" (H-3), not "absent".
    assert launcher._load_recorded_mandatory_obligation() == "MISSING_WHILE_ARMED"
    assert launcher._obligation_armed() is True
    assert launcher._clear_mandatory_obligation_if_satisfied("14.0.0") is True


def test_h2_unparseable_versions_keep_the_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """H-2: when satisfaction cannot be proven, the record stays (fail-closed)."""
    launcher = _launcher(tmp_path, monkeypatch, version="13.0.0")
    assert launcher._clear_mandatory_obligation_if_satisfied("not-a-version") is False
    assert launcher._clear_mandatory_obligation_if_satisfied("14.0.0") is False
    assert launcher._clear_mandatory_obligation_if_satisfied("12.0.0") is True


# ===========================================================================
# H-3 — a deleted obligation record is tampering
# ===========================================================================


def test_h3_deleted_obligation_record_is_tampered_not_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H-3: deleting the sealed record must NOT restore "no obligation"."""
    launcher = _launcher(tmp_path, monkeypatch)
    assert launcher._load_recorded_mandatory_obligation() is None  # first run: genuinely none

    assert launcher._record_mandatory_obligation("14.0.0") is True
    launcher._obligation_path().unlink()
    assert launcher._load_recorded_mandatory_obligation() == "MISSING_WHILE_ARMED"


def test_h3_missing_while_armed_blocks_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """H-3: the tampered state blocks launch and closes can_launch."""
    launcher = _launcher(tmp_path, monkeypatch)
    monkeypatch.setattr(app_mod.PrereqChecker, "run_all_checks", classmethod(lambda cls: _ok_prereqs()))
    monkeypatch.setattr(launcher.auth_manager, "get_current_status", lambda: AuthStatus(
        is_authenticated=True, has_valid_license=True, hwid="A" * 32, tier="PRO"
    ))
    monkeypatch.setattr(launcher.update_manager, "check_for_updates", lambda: UpdateInfo(
        has_update=False,
        current_version="13.0.0",
        latest_version="13.0.0",
        manifest_verified=True,
        check_succeeded=True,
    ))
    assert launcher._record_mandatory_obligation("14.0.0") is True
    launcher._obligation_path().unlink()

    report = launcher.run_preflight()
    assert report.can_launch is False


def test_h3_obligation_record_lives_outside_the_writable_dist_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H-3: the record is in the per-user data root, not in dist/."""
    launcher = _launcher(tmp_path, monkeypatch)
    path = launcher._obligation_path()
    assert "dist" not in path.parts
    assert launcher_paths.app_data_root() in path.parents


def test_h3_armed_marker_is_provisioned_in_the_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """H-3: the armed marker is vault-backed and survives a file wipe."""
    launcher = _launcher(tmp_path, monkeypatch)
    launcher._record_mandatory_obligation("14.0.0")
    assert launcher.auth_manager.secrets.has_secret(launcher._OBLIGATION_ARMED_KEY_NAME) is True
    launcher._obligation_path().unlink()
    assert launcher._obligation_armed() is True


# ===========================================================================
# H-6 / M-9 — byte ceilings and digest validation
# ===========================================================================


def test_h6_verify_ceiling_matches_the_download_ceiling() -> None:
    """H-6: one cap, so a large package cannot download then fail verification."""
    assert UpdateManager._VERIFY_MAX_BYTES == UpdateManager.MAX_PACKAGE_BYTES
    assert UpdateManager.MAX_PACKAGE_BYTES == 2 * 1024 * 1024 * 1024


def test_h6_signature_verification_refuses_an_over_cap_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H-6: the ceiling is enforced BEFORE buffering (fail-closed)."""
    _priv, pub = _keypair()
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 64)
    monkeypatch.setattr(UpdateManager, "_VERIFY_MAX_BYTES", 16)
    assert UpdateManager.verify_file_signature(big, base64.b64encode(b"s" * 64).decode(), pub) is False


def test_h6_sha256_verification_refuses_an_over_cap_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _priv, _pub = _keypair()
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 64)
    monkeypatch.setattr(UpdateManager, "_VERIFY_MAX_BYTES", 16)
    assert UpdateManager.verify_file_sha256(big, hashlib.sha256(b"x" * 64).hexdigest()) is False


def test_m9_sha256_comparison_validates_digest_format(tmp_path: Path) -> None:
    """M-9: a malformed expected digest is refused, not compared leniently."""
    payload = b"payload"
    target = tmp_path / "pkg.bin"
    target.write_bytes(payload)
    good = hashlib.sha256(payload).hexdigest()

    assert UpdateManager.verify_file_sha256(target, good) is True
    assert UpdateManager.verify_file_sha256(target, good.upper()) is True
    assert UpdateManager.verify_file_sha256(target, "not-hex") is False
    assert UpdateManager.verify_file_sha256(target, good[:-1]) is False
    assert UpdateManager.verify_file_sha256(target, "z" * 64) is False
    assert UpdateManager.verify_file_sha256(target, "") is False


def test_m9_verify_sha256_uses_constant_time_comparison() -> None:
    """M-9: hmac.compare_digest is used (consistency with app._verify_target_hash)."""
    source = Path(UpdateManager.__module__.replace(".", "/") + ".py")
    text = source.read_text(encoding="utf-8")
    body = text.split("def verify_file_sha256", 1)[1].split("MAX_PACKAGE_BYTES", 1)[0]
    assert "hmac.compare_digest" in body


# ===========================================================================
# C-2 — staging, not installation; the docstring must be honest
# ===========================================================================


def test_c2_module_docstring_no_longer_claims_atomic_replacement() -> None:
    """C-2: the "atomic file replacement" claim is gone from the docstring."""
    import src.launcher.updater as updater_mod

    doc = (updater_mod.__doc__ or "").lower()
    assert "atomic file replacement" not in doc
    assert "staged, not installed" in doc or "not an installer" in doc


def test_c2_install_update_exists_and_requires_a_confirmation_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C-2: an explicit gated install step exists and refuses without a token."""
    _priv, pub = _keypair()
    manager = UpdateManager(current_version="13.0.0", public_key=pub, require_signature=True)

    root = tmp_path / "updates"
    monkeypatch.setattr(UpdateManager, "_updates_root", classmethod(lambda cls: root))
    manager_dest = manager._updates_root() / "13.5.0" / "staged.exe"
    manager_dest.parent.mkdir(parents=True, exist_ok=True)
    payload = b"STAGED-BINARY"
    manager_dest.write_bytes(payload)
    live = tmp_path / "live" / "ucanlab.exe"
    live.parent.mkdir()
    live.write_bytes(b"OLD")

    monkeypatch.delenv("UCANLAB_UPDATE_INSTALL_TOKEN", raising=False)
    monkeypatch.delenv("UCAN_LAUNCHER_DEV_OVERRIDE", raising=False)
    assert manager.install_update(manager_dest, live, expected_sha256=hashlib.sha256(payload).hexdigest()) is False
    assert live.read_bytes() == b"OLD"


def test_c2_install_update_refuses_an_unsigned_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C-2: a confirmed install still refuses an artifact with no signature."""
    _priv, pub = _keypair()
    manager = UpdateManager(current_version="13.0.0", public_key=pub, require_signature=True)
    root = tmp_path / "updates"
    monkeypatch.setattr(UpdateManager, "_updates_root", classmethod(lambda cls: root))
    staged = root / "13.5.0" / "staged.exe"
    staged.parent.mkdir(parents=True, exist_ok=True)
    payload = b"STAGED-BINARY"
    staged.write_bytes(payload)
    live = tmp_path / "live" / "ucanlab.exe"
    live.parent.mkdir()
    live.write_bytes(b"OLD")

    monkeypatch.setenv("UCANLAB_UPDATE_INSTALL_TOKEN", "operator-confirmed-token-1234")
    assert manager.install_update(staged, live, expected_sha256=hashlib.sha256(payload).hexdigest()) is False
    assert live.read_bytes() == b"OLD"


def test_c2_install_update_installs_a_fully_verified_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C-2 positive control: confirmed + hashed + signed => replacement prepared."""
    priv, pub = _keypair()
    manager = UpdateManager(current_version="13.0.0", public_key=pub, require_signature=True)
    root = tmp_path / "updates"
    monkeypatch.setattr(UpdateManager, "_updates_root", classmethod(lambda cls: root))
    staged = root / "13.5.0" / "staged.exe"
    staged.parent.mkdir(parents=True, exist_ok=True)
    payload = b"STAGED-BINARY"
    staged.write_bytes(payload)
    live = tmp_path / "live" / "ucanlab.exe"
    live.parent.mkdir()
    live.write_bytes(b"OLD")

    monkeypatch.setenv(launcher_paths.LAUNCHER_ROOT_ENV, str(tmp_path))
    monkeypatch.setenv("UCANLAB_UPDATE_INSTALL_TOKEN", "operator-confirmed-token-1234")
    monkeypatch.delenv("UCAN_LAUNCHER_DEV_OVERRIDE", raising=False)
    ok = manager.install_update(
        staged,
        live,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        signature_b64=base64.b64encode(priv.sign(payload)).decode("ascii"),
    )
    assert ok is True
    assert (live.parent / "ucanlab.exe.new").read_bytes() == payload


def test_c2_install_update_refuses_an_artifact_outside_the_staging_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C-2: only a file inside dist/updates/<v>/ can be installed."""
    priv, pub = _keypair()
    manager = UpdateManager(current_version="13.0.0", public_key=pub, require_signature=True)
    root = tmp_path / "updates"
    root.mkdir()
    monkeypatch.setattr(UpdateManager, "_updates_root", classmethod(lambda cls: root))
    outside = tmp_path / "elsewhere.exe"
    payload = b"PAYLOAD"
    outside.write_bytes(payload)
    live = tmp_path / "live" / "ucanlab.exe"
    live.parent.mkdir()
    live.write_bytes(b"OLD")

    monkeypatch.setenv("UCANLAB_UPDATE_INSTALL_TOKEN", "operator-confirmed-token-1234")
    ok = manager.install_update(
        outside,
        live,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        signature_b64=base64.b64encode(priv.sign(payload)).decode("ascii"),
    )
    assert ok is False
    assert live.read_bytes() == b"OLD"


def test_c2_download_reports_staged_not_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """C-2: the download leaves the target path untouched (staging only)."""
    import io
    import urllib.request

    priv, pub = _keypair()
    payload = b"UPDATE-PAYLOAD"
    updater = UpdateManager(current_version="13.0.0", public_key=pub, require_signature=True)
    info = UpdateInfo(
        has_update=True,
        current_version="13.0.0",
        latest_version="13.5.0",
        download_url="https://cloud.universalcan.io/update.exe",
        sha256_hash=hashlib.sha256(payload).hexdigest(),
        signature=base64.b64encode(priv.sign(payload)).decode("ascii"),
        package_size_bytes=len(payload),
        manifest_verified=True,
    )

    class _Body(io.BytesIO):
        headers = {"Content-Length": str(len(payload))}

    def _open(self: Any, req: Any, timeout: float = 60) -> _Body:
        return _Body(payload)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", _open)
    root = tmp_path / "updates"
    monkeypatch.setattr(UpdateManager, "_updates_root", classmethod(lambda cls: root))

    live_target = tmp_path / "live" / "ucanlab.exe"
    live_target.parent.mkdir()
    live_target.write_bytes(b"RUNNING-BINARY")

    assert updater.download_update(info, "update.exe") is True
    assert (root / "13.5.0" / "update.exe").read_bytes() == payload
    assert live_target.read_bytes() == b"RUNNING-BINARY", "download must not touch the live target"


# ===========================================================================
# M-1 / L-1 — prerequisites
# ===========================================================================


def test_m1_webview2_is_non_critical_in_cli_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """M-1: --cli must not be blocked by a missing WebView2 runtime."""
    monkeypatch.setattr(PrereqChecker, "check_webview2", classmethod(lambda cls, cli_mode=False: PrereqStatus(
        "Microsoft Edge WebView2", False, "missing", is_critical=not cli_mode
    )))
    monkeypatch.setattr(PrereqChecker, "check_vcredist", classmethod(lambda cls: PrereqStatus(
        "Visual C++ Redistributable", True, "ok", is_critical=True
    )))
    monkeypatch.setattr(PrereqChecker, "check_can_drivers", classmethod(lambda cls: []))

    cli = PrereqChecker.run_all_checks(cli_mode=True)
    assert [p.is_critical for p in cli if p.name == "Microsoft Edge WebView2"] == [False]
    assert any(p.is_critical for p in cli if p.name == "Microsoft Edge WebView2") is False

    gui = PrereqChecker.run_all_checks(cli_mode=False)
    webview = [p for p in gui if p.name == "Microsoft Edge WebView2"][0]
    assert webview.is_critical is True


def test_m1_cli_mode_detected_from_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UCANLAB_CLI_MODE", raising=False)
    assert PrereqChecker.is_cli_mode(["launcher", "--cli"]) is True
    assert PrereqChecker.is_cli_mode(["-c", "vcan0"]) is True
    assert PrereqChecker.is_cli_mode(["launcher"]) is False
    monkeypatch.setenv("UCANLAB_CLI_MODE", "1")
    assert PrereqChecker.is_cli_mode([]) is True


def test_m1_vcredist_probes_more_than_one_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """M-1: the runtime DLL / WinSxS evidence path must exist and be used."""
    assert "msvcp140.dll" in PrereqChecker.VCRUNTIME_DLLS

    monkeypatch.setattr(PrereqChecker, "_vcredist_evidence", classmethod(lambda cls: "Runtime DLL present (System32\\msvcp140.dll)"))
    status = PrereqChecker.check_vcredist()
    assert status.is_available is True
    assert "msvcp140.dll" in status.details

    monkeypatch.setattr(PrereqChecker, "_vcredist_evidence", classmethod(lambda cls: None))
    assert PrereqChecker.check_vcredist().is_available is False


def test_l1_docstring_no_longer_claims_vector_support() -> None:
    import src.launcher.prereqs as prereqs_mod

    doc = prereqs_mod.__doc__ or ""
    assert "Vector" not in doc.split("runtime")[0] or "deliberately NOT probed" in doc
    assert "deliberately NOT probed" in doc


# ===========================================================================
# M-2 — the update host allowlist shares one canonical source
# ===========================================================================


def test_m2_update_hosts_derive_from_the_canonical_cloud_hosts() -> None:
    """M-2: no divergent second host list; loopback dev hosts are excluded."""
    for host in ("ucan-cloud.si6n.io", "cloud.universalcan.io", "ucanlab.org", "api.ucanlab.org"):
        assert host in CANONICAL_CLOUD_HOSTS
        assert host in ALLOWED_UPDATE_HOSTS
    for loopback in ("127.0.0.1", "localhost", "::1"):
        assert loopback not in ALLOWED_UPDATE_HOSTS


# ===========================================================================
# M-3 — launch_main_app re-applies the preflight gate
# ===========================================================================


def _report(tmp_path: Path, target: Path, *, can_launch: bool = True) -> Any:
    return app_mod.LauncherPreflightReport(
        can_launch=can_launch,
        prereqs=_ok_prereqs(),
        auth_status=AuthStatus(is_authenticated=True, has_valid_license=can_launch, hwid="A" * 32, tier="PRO"),
        update_info=UpdateInfo(
            has_update=False, current_version="13.0.0", latest_version="13.0.0", manifest_verified=True
        ),
        target_executable=target,
    )


def test_m3_launch_main_app_refuses_when_preflight_is_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-3: an unlicensed in-process caller must not reach the payload."""
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("print('x')\n", encoding="utf-8")

    called: list[Any] = []
    monkeypatch.setattr(app_mod.subprocess, "call", lambda *a, **kw: called.append(a) or 0)
    monkeypatch.setattr(app_mod.UniversalCanLauncher, "run_preflight", lambda self, *a, **kw: _report(tmp_path, target, can_launch=False))

    launcher = app_mod.UniversalCanLauncher.for_testing(current_version="13.0.0")
    assert launcher.launch_main_app() == 1
    assert called == [], "launch_main_app spawned the payload with a closed preflight gate"


def test_m3_launch_main_app_proceeds_when_preflight_is_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("print('x')\n", encoding="utf-8")

    monkeypatch.setattr(app_mod.subprocess, "call", lambda *a, **kw: 7)
    monkeypatch.setattr(app_mod.UniversalCanLauncher, "run_preflight", lambda self, *a, **kw: _report(tmp_path, target))
    monkeypatch.setattr(app_mod.UniversalCanLauncher, "resolve_target_executable", classmethod(lambda cls: target))

    launcher = app_mod.UniversalCanLauncher.for_testing(current_version="13.0.0")
    assert launcher.launch_main_app() == 7


# ===========================================================================
# M-4 — case-insensitive .py/.exe branch
# ===========================================================================


def test_m4_uppercase_py_extension_takes_the_python_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-4: `Main.PY` must not fall through to the refusal branch."""
    target = tmp_path / "Main.PY"
    target.write_text("print('x')\n", encoding="utf-8")

    captured: dict[str, Any] = {}
    monkeypatch.setattr(app_mod.subprocess, "call", _capturing_call(captured))
    monkeypatch.setattr(app_mod.UniversalCanLauncher, "run_preflight", lambda self, *a, **kw: _report(tmp_path, target))
    monkeypatch.setattr(app_mod.UniversalCanLauncher, "resolve_target_executable", classmethod(lambda cls: target))

    launcher = app_mod.UniversalCanLauncher.for_testing(current_version="13.0.0")
    assert launcher.launch_main_app() == 0
    assert captured["cmd"][-1] == str(target.resolve())
    assert captured["cmd"][0] == app_mod.sys.executable


def test_m4_uppercase_exe_extension_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "dist" / "UCANLAB.EXE"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"MZ")

    captured: dict[str, Any] = {}
    monkeypatch.setattr(app_mod.subprocess, "call", _capturing_call(captured))
    monkeypatch.setattr(app_mod.UniversalCanLauncher, "run_preflight", lambda self, *a, **kw: _report(tmp_path, target))
    monkeypatch.setattr(app_mod.UniversalCanLauncher, "resolve_target_executable", classmethod(lambda cls: target))
    monkeypatch.setattr(app_mod.UniversalCanLauncher, "verify_resolved_target", classmethod(lambda cls, t: True))

    launcher = app_mod.UniversalCanLauncher.for_testing(current_version="13.0.0")
    assert launcher.launch_main_app() == 0
    assert captured["cmd"][0] == str(target.resolve())


# ===========================================================================
# M-5 — a single embedded key source, fail-closed
# ===========================================================================


def test_m5_app_imports_the_single_embedded_key_source() -> None:
    """M-5: no duplicated literal; the value comes from the security module."""
    import src.launcher.app as app_mod_local
    from src.security.cloud.license_flow import DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64 as canonical

    assert app_mod_local.DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64 == canonical
    source = Path(app_mod.__file__).read_text(encoding="utf-8")
    assert "eX3vJQWpo/pKrkpi5Y+f7m5ooUCRbCyY201DTnAjz/Q=" not in source


def test_m5_corrupt_embedded_key_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """M-5: a corrupt key must raise, never become `pub_key = None`."""
    monkeypatch.setattr(app_mod, "DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64", "!!!not-base64!!!")
    with pytest.raises(LicenseError):
        app_mod.UniversalCanLauncher.for_testing(current_version="13.0.0")


def test_m5_key_ring_is_fail_closed_on_a_corrupt_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.security.cloud.license_flow as flow_mod

    monkeypatch.setitem(flow_mod.TRUSTED_CLOUD_PUBLIC_KEYS_B64, "v2", "!!!broken!!!")
    with pytest.raises(LicenseError):
        app_mod.UniversalCanLauncher._embedded_key_ring()


# ===========================================================================
# M-6 — the documented UI-coupling constraint
# ===========================================================================


def test_m6_ui_coupling_is_documented() -> None:
    """M-6: the constraint is documented; the import stays function-local."""
    source = Path(LauncherAuthManager.__module__.replace(".", "/") + ".py").read_text(encoding="utf-8")
    assert "M-6 CONSTRAINT" in source
    assert "src/security/cloud/endpoints.py" in source
    # still function-local: no module-level `from src.ui.desktop_app import`
    head = source.split("class LauncherAuthManager", 1)[0]
    assert "from src.ui.desktop_app import" not in head


# ===========================================================================
# M-7 — documented short flags are forwarded
# ===========================================================================


def test_m7_documented_short_flags_are_allowlisted() -> None:
    """M-7: README-documented flags must not be silently dropped."""
    for flag in ("-i", "-c", "-b", "--log-level", "--interface", "--channel", "--bitrate", "--cli", "--help"):
        assert flag in app_mod.UniversalCanLauncher._ALLOWED_EXTRA_ARGS


def test_m7_filter_forwards_short_flags_with_their_values() -> None:
    filtered = app_mod.UniversalCanLauncher._filter_extra_args(
        ["-i", "pcan", "-c", "PCAN_USBBUS1", "-b", "500000", "--log-level", "debug"]
    )
    assert filtered == ["-i", "pcan", "-c", "PCAN_USBBUS1", "-b", "500000", "--log-level", "debug"]


def test_m7_unknown_flags_are_still_dropped() -> None:
    assert app_mod.UniversalCanLauncher._filter_extra_args(["--evil-flag", "x"]) == []


# ===========================================================================
# M-8 — the license key never travels on argv
# ===========================================================================


def test_m8_activate_is_a_flag_not_a_value() -> None:
    """M-8: --activate must be store_true (no argv secret) + getpass prompt."""
    source = Path(app_mod.__file__).read_text(encoding="utf-8")
    assert '"--activate", action="store_true"' in source
    assert '"--activate", type=str' not in source
    assert "getpass.getpass" in source


def test_m8_prompt_uses_getpass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "  SECRET-KEY  ")
    assert app_mod._prompt_license_key() == "SECRET-KEY"


def test_m8_prompt_returns_empty_on_ctrl_c(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(prompt: str = "") -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("getpass.getpass", _boom)
    assert app_mod._prompt_license_key() == ""


# ===========================================================================
# L-2 / L-3 — dead auth fields and branches
# ===========================================================================


def test_l2_auth_status_has_no_user_email_field() -> None:
    import dataclasses

    names = {f.name for f in dataclasses.fields(AuthStatus)}
    assert "user_email" not in names


def test_l3_dead_flow_branch_is_gone() -> None:
    source = Path(LauncherAuthManager.__module__.replace(".", "/") + ".py").read_text(encoding="utf-8")
    code_lines = [ln for ln in source.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("or not self.flow" in ln for ln in code_lines)
    assert 'if not self.secrets.has_secret("CLOUD_LICENSE_TICKET"):' in source


# ===========================================================================
# L-4 / L-5 — dead code removal (only when truly unreferenced)
# ===========================================================================


def test_l4_urllib_error_import_is_gone() -> None:
    import src.launcher.updater as updater_mod

    source = Path(updater_mod.__file__).read_text(encoding="utf-8")
    assert "import urllib.error" not in source


def test_l5_unsigned_manifest_allowed_helper_is_gone() -> None:
    import src.launcher.app as app_mod_local

    assert not hasattr(app_mod_local, "_unsigned_manifest_allowed")
    source = Path(app_mod.__file__).read_text(encoding="utf-8")
    assert "def _unsigned_manifest_allowed" not in source


# ===========================================================================
# L-6 — no filesystem side effect before the download gates
# ===========================================================================


def test_l6_staging_dir_is_not_created_for_a_rejected_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L-6: a rejected URL/host/hash must leave no directory behind."""
    root = tmp_path / "updates"
    monkeypatch.setattr(UpdateManager, "_updates_root", classmethod(lambda cls: root))
    updater = UpdateManager(current_version="13.0.0")

    rejections = [
        UpdateInfo(has_update=True, current_version="13.0.0", latest_version="13.5.0"),  # no URL
        UpdateInfo(
            has_update=True,
            current_version="13.0.0",
            latest_version="13.5.0",
            download_url="http://cloud.universalcan.io/x.exe",
            sha256_hash="a" * 64,
        ),  # not HTTPS
        UpdateInfo(
            has_update=True,
            current_version="13.0.0",
            latest_version="13.5.0",
            download_url="https://attacker.example/x.exe",
            sha256_hash="a" * 64,
        ),  # host not allowlisted
        UpdateInfo(
            has_update=True,
            current_version="13.0.0",
            latest_version="13.5.0",
            download_url="https://cloud.universalcan.io/x.exe",
            sha256_hash="",
        ),  # hash-less
    ]
    for info in rejections:
        assert updater.download_update(info, "x.exe") is False
    assert not root.exists(), "download_update created the staging tree before its gates"


def test_l6_mkdir_happens_after_the_gates_in_source() -> None:
    source = Path(UpdateManager.__module__.replace(".", "/") + ".py").read_text(encoding="utf-8")
    body = source.split("def download_update", 1)[1]
    assert body.index("pinned allowlist") < body.index("dest_dir.mkdir")
    assert body.index("no SHA-256 hash") < body.index("dest_dir.mkdir")


# ===========================================================================
# L-7 — semver requires exactly three components
# ===========================================================================


def test_l7_parse_semver_requires_three_numeric_components() -> None:
    assert UpdateManager._parse_semver("13.2.1") == (13, 2, 1)
    assert UpdateManager._parse_semver("v13.2.1-beta") == (13, 2, 1)
    for malformed in ("13.2", "13", "13.2.1.4", "garbage.version", "13.x.y", ""):
        assert UpdateManager._parse_semver(malformed) is None, malformed


def test_l7_is_newer_version_treats_padded_versions_as_unknown() -> None:
    updater = UpdateManager(current_version="13.0.0")
    assert updater.is_newer_version("13.5") is False
    assert updater.is_newer_version("14") is False
    assert updater.is_newer_version("13.0.1") is True


# ===========================================================================
# L-8 — main() surfaces a failed update check
# ===========================================================================


def test_l8_main_prints_update_check_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """L-8: "server unreachable" must not look like "no update"."""
    launcher = app_mod.UniversalCanLauncher.for_testing(current_version="13.0.0")
    monkeypatch.setattr(app_mod, "UniversalCanLauncher", lambda *a, **kw: launcher)
    monkeypatch.setattr(launcher, "run_preflight", lambda *a, **kw: app_mod.LauncherPreflightReport(
        can_launch=False,
        prereqs=_ok_prereqs(),
        auth_status=AuthStatus(is_authenticated=False, has_valid_license=False, hwid="A" * 32),
        update_info=UpdateInfo(
            has_update=False,
            current_version="13.0.0",
            latest_version="13.0.0",
            check_succeeded=False,
            manifest_verified=False,
        ),
        target_executable=tmp_path / "src" / "main.py",
    ))
    monkeypatch.setattr(app_mod.sys, "argv", ["launcher", "--check-only"])

    rc = app_mod.main()
    out = capsys.readouterr().out
    assert rc == 1
    assert "UPDATE CHECK FAILED" in out
    assert "NOT a green light" in out


# ===========================================================================
# H-4 — both build scripts emit data/target.hash (STATIC ONLY)
# ===========================================================================


@pytest.mark.parametrize("script", ["scripts/build_exe.py", "scripts/build_nuitka.py"])
def test_h4_build_script_emits_target_hash(script: str) -> None:
    """H-4: a frozen artifact must always ship the D-4 manifest."""
    root = Path(__file__).resolve().parents[2]
    source = (root / script).read_text(encoding="utf-8")
    assert "target.hash" in source
    assert "def write_target_manifest" in source
    assert '"data" / "target.hash"' in source or "target.hash" in source


@pytest.mark.parametrize("script", ["scripts/build_exe.py", "scripts/build_nuitka.py"])
def test_h4_manifest_writer_round_trips_with_the_loader(
    script: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H-4: the emitted file is exactly what `_load_target_manifest` parses."""
    import importlib.util

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(f"probe_{Path(script).stem}", root / script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    artifact = tmp_path / "dist" / "ucanlab.exe"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"MZ-frozen-artifact")

    written = module.write_target_manifest(tmp_path, artifact)
    assert written == tmp_path / "data" / "target.hash"
    assert app_mod._load_target_manifest(written) == hashlib.sha256(b"MZ-frozen-artifact").hexdigest()
    assert app_mod._verify_target_hash(artifact, hashlib.sha256(b"MZ-frozen-artifact").hexdigest()) is True


@pytest.mark.parametrize("script", ["scripts/build_exe.py", "scripts/build_nuitka.py"])
def test_h4_manifest_writer_fails_when_the_artifact_is_missing(
    script: str, tmp_path: Path
) -> None:
    """H-4: a missing artifact must fail the build, never silently skip."""
    import importlib.util

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(f"probe2_{Path(script).stem}", root / script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with pytest.raises(FileNotFoundError):
        module.write_target_manifest(tmp_path, tmp_path / "dist" / "does-not-exist.exe")


# ===========================================================================
# H-1/updater plumbing + secret-provider sanity
# ===========================================================================


def test_update_info_defaults_are_fail_closed() -> None:
    info = UpdateInfo(has_update=False, current_version="13.0.0", latest_version="13.0.0")
    assert info.manifest_verified is False
    assert info.check_succeeded is True


def test_updater_requires_signature_by_default() -> None:
    assert UpdateManager(current_version="13.0.0").require_signature is True


def test_launcher_wires_the_embedded_key_ring_into_the_updater(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H-1 wiring: the launcher gives the updater the embedded kid ring."""
    launcher = _launcher(tmp_path, monkeypatch)
    ring = launcher.update_manager._manifest_keys
    assert "v1" in ring
    assert isinstance(ring["v1"], ed25519.Ed25519PublicKey)


def test_in_memory_secret_provider_is_used_by_helpers() -> None:
    """Sanity: the obligation helpers work against a plain vault too."""
    secrets = InMemorySecretProvider()
    client = CloudClient(config=CloudConfig(), secret_provider=secrets)
    _priv, pub = _keypair()
    auth = LauncherAuthManager(cloud_client=client, secret_provider=secrets, public_key=pub)
    assert auth.secrets is secrets


def test_subprocess_module_is_still_imported_for_call() -> None:
    assert subprocess.call is not None
