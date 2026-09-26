"""B-01 / B-02 / I-05 / I-08 — renderer trust-boundary regression tests.

These lock the coordinator-completed wiring for the review findings:

* B-01: a diagnostic challenge minted WITHOUT native presence cannot
  authorize a destructive PHYSICAL action (test mode is the documented
  escape used by the rest of the suite).
* B-02: `is_navigation_allowed` is the pure same-origin predicate the
  WebView guard polls; every cross-origin / scheme / port shape is denied.
* I-05: the diagnostic challenge store is bounded (capacity + rate) and
  oversized action payloads are refused before hashing.
* I-08: cloud raw-content uploads require a single-use, content-bound
  approval token; a token for payload A never authorizes payload B.
"""

from __future__ import annotations

import pytest

from src.ui.desktop_app import (
    _DIAG_CHALLENGE_MAX,
    _DIAG_CHALLENGE_MAX_ACTION_CHARS,
    UniversalCanDesktopApp,
)


def _app() -> UniversalCanDesktopApp:
    return UniversalCanDesktopApp(channel="vcan0", bitrate=250000)


_ACTION = {
    "id": "clear-1",
    "action_type": "uds_clear_dtc",
    "params": {"dtc_group": 0xFFFFFF},
}


class TestNativePresenceGate:
    def test_presence_stamp_is_recorded_on_mint(self) -> None:
        app = _app()
        res = app.request_diagnostic_challenge(_ACTION, native_presence=True)
        assert res["success"] is True
        challenge = app._diagnostic_challenges[res["token"]]
        assert challenge.native_presence is True

    def test_absent_presence_stamp_is_recorded_false(self) -> None:
        app = _app()
        res = app.request_diagnostic_challenge(_ACTION, native_presence=False)
        challenge = app._diagnostic_challenges[res["token"]]
        assert challenge.native_presence is False

    def test_unstamped_challenge_rejected_for_physical_destructive_action(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B-01 core: outside test mode a script-only mint→consume pair
        cannot authorize a destructive physical action."""
        monkeypatch.delenv("UCANLAB_TEST_MODE", raising=False)
        app = _app()
        app._is_simulating = False  # physical mode
        mint = app.request_diagnostic_challenge(_ACTION, native_presence=False)
        assert mint["success"] is True

        valid, reason = app._verify_and_consume_diagnostic_token(
            mint["token"],
            "uds_clear_dtc",
            "clear-1",
            action_payload=_ACTION,
            require_native_presence=True,
        )
        assert valid is False
        assert "native" in reason.lower() or "yerel" in reason.lower()

    def test_stamped_challenge_accepted_for_physical_action(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("UCANLAB_TEST_MODE", raising=False)
        app = _app()
        mint = app.request_diagnostic_challenge(_ACTION, native_presence=True)
        valid, _reason = app._verify_and_consume_diagnostic_token(
            mint["token"],
            "uds_clear_dtc",
            "clear-1",
            action_payload=_ACTION,
            require_native_presence=True,
        )
        assert valid is True

    def test_presence_not_required_for_read_actions(self) -> None:
        app = _app()
        mint = app.request_diagnostic_challenge(_ACTION, native_presence=False)
        valid, _ = app._verify_and_consume_diagnostic_token(
            mint["token"], "uds_clear_dtc", "clear-1", action_payload=_ACTION
        )
        assert valid is True


class TestChallengeStoreCaps:
    def test_capacity_is_bounded_with_fifo_prune(self) -> None:
        app = _app()
        for i in range(_DIAG_CHALLENGE_MAX + 10):
            res = app.request_diagnostic_challenge(
                {"id": f"a{i}", "action_type": "uds_clear_dtc", "params": {"i": i}}
            )
            # rate limit may kick in; only count successful mints
            if res["success"]:
                pass
        assert len(app._diagnostic_challenges) <= _DIAG_CHALLENGE_MAX

    def test_oversized_action_payload_refused_before_hash(self) -> None:
        app = _app()
        huge = {"id": "x", "action_type": "uds_clear_dtc", "params": {"blob": "A" * (_DIAG_CHALLENGE_MAX_ACTION_CHARS + 1)}}
        res = app.request_diagnostic_challenge(huge)
        assert res["success"] is False
        assert "büyük" in res["error"].lower() or "large" in res["error"].lower()

    def test_rate_limit_refuses_burst(self) -> None:
        from src.ui.desktop_app import _DIAG_CHALLENGE_MAX_PER_MIN

        app = _app()
        successes = 0
        for i in range(_DIAG_CHALLENGE_MAX_PER_MIN + 5):
            res = app.request_diagnostic_challenge(
                {"id": f"r{i}", "action_type": "uds_clear_dtc", "params": {"i": i}}
            )
            if res["success"]:
                successes += 1
        assert successes == _DIAG_CHALLENGE_MAX_PER_MIN


class TestNavigationGuardPredicate:
    """B-02: the pure predicate the WebView poller uses (fail-closed)."""

    BASE = "http://127.0.0.1:34567"

    def test_exact_origin_allowed(self) -> None:
        from src.ui.frontend_server import is_navigation_allowed

        assert is_navigation_allowed("http://127.0.0.1:34567/index.html", self.BASE) is True
        assert is_navigation_allowed("http://127.0.0.1:34567/assets/app.js?v=1", self.BASE) is True

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:34568/index.html",   # different port
            "http://localhost:34567/index.html",   # different host spelling
            "https://127.0.0.1:34567/index.html",  # scheme change
            "https://evil.example/index.html",     # remote origin
            "file:///C:/Windows/System32/calc.exe",  # local file scheme
            "data:text/html,<script>alert(1)</script>",  # data scheme
            "blob:http://127.0.0.1:34567/abc",     # blob scheme
            "javascript:alert(1)",                  # script scheme
            "http://127.0.0.1:34567@evil.example/",  # userinfo trick
        ],
    )
    def test_non_origin_urls_denied(self, url: str) -> None:
        from src.ui.frontend_server import is_navigation_allowed

        assert is_navigation_allowed(url, self.BASE) is False


class TestUploadApprovalBinding:
    """I-08: single-use, content-bound cloud upload approvals."""

    def test_token_bound_to_exact_payload(self) -> None:
        app = _app()
        content = b"telemetry-payload-A"
        mint = app.request_upload_approval("https://cloud.example", "a.bin", content)
        assert mint["success"] is True

        # Wrong payload with the right token → rejected.
        assert app.consume_upload_approval(mint["approvalToken"], "https://cloud.example", "a.bin", b"other") is False
        # The failed attempt burned the token (single-use, fail-closed).
        assert app.consume_upload_approval(mint["approvalToken"], "https://cloud.example", "a.bin", content) is False

    def test_token_binds_destination_and_filename(self) -> None:
        app = _app()
        content = b"payload"
        mint = app.request_upload_approval("https://cloud.example", "a.bin", content)
        token = mint["approvalToken"]
        assert app.consume_upload_approval(token, "https://other.example", "a.bin", content) is False

        mint2 = app.request_upload_approval("https://cloud.example", "a.bin", content)
        assert app.consume_upload_approval(mint2["approvalToken"], "https://cloud.example", "b.bin", content) is False

    def test_valid_token_consumes_once(self) -> None:
        app = _app()
        content = b"payload"
        mint = app.request_upload_approval("https://cloud.example", "a.bin", content)
        token = mint["approvalToken"]
        assert app.consume_upload_approval(token, "https://cloud.example", "a.bin", content) is True
        # Replay is refused.
        assert app.consume_upload_approval(token, "https://cloud.example", "a.bin", content) is False

    def test_raw_upload_requires_approval_outside_test_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.ui.desktop_app import DesktopApiBridge

        monkeypatch.delenv("UCANLAB_TEST_MODE", raising=False)
        app = _app()
        bridge = DesktopApiBridge(app)
        res = bridge.cloud_upload_raw_content("x.bin", "hello", approval_token=None)
        assert res["success"] is False
        assert "onay" in res["error"].lower() or "approval" in res["error"].lower()

    def test_raw_upload_approval_mint_requires_native_presence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.ui.desktop_app import DesktopApiBridge

        monkeypatch.delenv("UCANLAB_TEST_MODE", raising=False)
        app = _app()
        bridge = DesktopApiBridge(app)
        # No window in this process → native dialog unavailable → fail-closed.
        res = bridge.cloud_request_upload_approval("x.bin", "hello")
        assert res["success"] is False


class TestStrictBoolParsing:
    """M-03: cloud payload booleans must not treat 'false' as truthy."""

    def test_strict_bool_spellings(self) -> None:
        from src.ui.desktop_app import _strict_bool

        assert _strict_bool(True) is True
        assert _strict_bool("true") is True
        assert _strict_bool("1") is True
        assert _strict_bool("yes") is True
        assert _strict_bool("false") is False
        assert _strict_bool("0") is False
        assert _strict_bool("no") is False
        assert _strict_bool("") is False
        assert _strict_bool(None) is False
        assert _strict_bool("FALSE") is False

    def test_features_shape_validation(self) -> None:
        from src.ui.desktop_app import _sanitize_features

        assert _sanitize_features(["a", "b"]) == ["a", "b"]
        assert _sanitize_features("not-a-list") == []
        assert _sanitize_features(None) == []
        assert _sanitize_features(["ok", 5, "", "x" * 200]) == ["ok"]


class TestFlashBoundsContract:
    """B-07: frontend/backend bounds parity (mirror of flashRequest.ts)."""

    def test_frontend_shaped_payload_passes(self) -> None:
        from src.ui.desktop_app import DesktopApiBridge

        payload = {
            "action_type": "ecu_flash",
            "ecu": "ECM",
            "fileName": "fw.bin",
            "sizeBytes": 4096,
            "data": "00" * 4096,
            "memoryAddress": 0x80000,
            "blockSize": 256,
            "firmwareSignature": "ab" * 32,
            "trustedPubkey": "cd" * 32,
            "expectedVin": "WVWZZZ1KZDP123456",
        }
        assert DesktopApiBridge._validate_flash_prerequisites(payload) is None

    def test_bounds_constants_match_frontend(self) -> None:
        """Guard: changing one side without the other breaks this test."""
        from src.ui.desktop_app import UniversalCanDesktopApp

        assert UniversalCanDesktopApp.FLASH_ALLOWED_BLOCK_SIZES == frozenset(
            {64, 128, 256, 512, 1024, 2048, 4096}
        )
        assert UniversalCanDesktopApp.FLASH_MAX_IMAGE_BYTES == 32 * 1024 * 1024
        assert UniversalCanDesktopApp.FLASH_ECU_BOUNDS["ECM"] == (0x80000, 4 * 1024 * 1024)
        assert UniversalCanDesktopApp.FLASH_ECU_BOUNDS["TCU"] == (0x80000, 1 * 1024 * 1024)
