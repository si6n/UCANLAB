"""Regression tests for REVIEW.md verification round-2 fixes.

Covers the confirmed findings from the second audit pass:
  B1  SafeMultiplexedBus stale physical bus after app._reconnect_bus
  B3  HWID PowerShell path must not resolve from %SystemRoot% / PATH
  B4  CloudClient extra_headers must not override session credentials
  B5  RTR frames must not inflate error_frames (see test_python_can_bus.py)
  B8  Anti-tamper timing threshold must be strict (20 ms, not 250 ms)
  B10 ISO-TP sub-millisecond STmin must yield to the event loop
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from src.core.models.can_frame import CanFrame
from src.engine.router import FrameRouter
from src.safety.multiplexer import SafeMultiplexedBus

# ==============================================================================
# B1 — SafeMultiplexedBus resolves the physical bus dynamically
# ==============================================================================


class _FakePhysicalBus:
    """Minimal AbstractBus stand-in with mutable identity."""

    def __init__(self, channel_id: str, connected: bool = True, is_fd: bool = False) -> None:
        self.channel_id = channel_id
        self.bitrate = 250000
        self.is_fd = is_fd
        self.is_connected = connected

    def connect(self) -> None:  # noqa: D102
        self.is_connected = True

    def disconnect(self) -> None:  # noqa: D102
        self.is_connected = False

    def send(self, frame: CanFrame) -> None:  # noqa: D102
        pass

    def recv(self, timeout_s: float | None = 0.1) -> CanFrame | None:  # noqa: D102
        return None


def _make_mux() -> tuple[SafeMultiplexedBus, dict[str, _FakePhysicalBus]]:
    holder = {"bus": _FakePhysicalBus("old_channel", connected=True)}
    mux = SafeMultiplexedBus(
        gateway=MagicMock(),
        router=FrameRouter(),
        bus_provider=lambda: holder["bus"],
    )
    return mux, holder


class TestSafeMultiplexedBusDynamicResolution:
    def test_reflects_live_bus_metadata_after_reconnect_swap(self) -> None:
        mux, holder = _make_mux()
        assert mux.channel_id == "old_channel"
        assert mux.is_connected is True
        assert mux.is_fd is False

        # _reconnect_bus: swap the driver instance and disconnect the old one
        holder["bus"].disconnect()
        holder["bus"] = _FakePhysicalBus("new_channel", connected=True, is_fd=True)

        assert mux.channel_id == "new_channel"
        assert mux.is_connected is True
        assert mux.is_fd is True
        mux.disconnect()

    def test_is_connected_follows_old_bus_disconnect_when_not_swapped(self) -> None:
        mux, holder = _make_mux()
        holder["bus"].disconnect()
        assert mux.is_connected is False
        mux.disconnect()

    def test_constructor_still_accepts_static_physical_bus(self) -> None:
        # Backwards compatibility: static instances are wrapped in a provider
        # closure — no regression for existing call sites/tests.
        phys = _FakePhysicalBus("static_ch")
        mux = SafeMultiplexedBus(physical_bus=phys, gateway=MagicMock(), router=FrameRouter())
        assert mux.channel_id == "static_ch"
        phys.channel_id = "renamed_ch"
        assert mux.channel_id == "renamed_ch"
        mux.disconnect()

    def test_constructor_requires_bus_argument(self) -> None:
        with pytest.raises(ValueError):
            SafeMultiplexedBus(gateway=MagicMock(), router=FrameRouter())


# ==============================================================================
# B4 — CloudClient header sanitization
# ==============================================================================


class TestCloudClientHeaderSanitization:
    def test_cookie_and_authorization_are_stripped_from_extra_headers(self) -> None:
        from src.security.cloud.client import _sanitize_extra_headers

        sanitized = _sanitize_extra_headers(
            {
                "Cookie": "ucan_session=attacker",
                "authorization": "Bearer attacker-token",
                "Host": "evil.example",
                "X-Trace-Id": "ok",
            }
        )
        assert sanitized == {"X-Trace-Id": "ok"}

    def test_empty_and_none_inputs(self) -> None:
        from src.security.cloud.client import _sanitize_extra_headers

        assert _sanitize_extra_headers(None) == {}
        assert _sanitize_extra_headers({}) == {}

    def test_session_cookie_survives_extra_header_injection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.security.cloud import client as cloud_client_module

        captured_headers: dict[str, str] = {}

        class _FakeResponse:
            status = 200
            body = b"{}"
            headers: dict[str, str] = {}

            def __enter__(self) -> "_FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def read(self) -> bytes:
                return self.body

        class _FakeOpener:
            def open(self, req, timeout=None):  # noqa: ANN001, ANN202
                captured_headers.update(req.headers)
                return _FakeResponse()

        secrets = MagicMock()
        secrets.has_secret.return_value = True
        secrets.get_secret.return_value = b"legitimate-session-token"

        monkeypatch.setattr(cloud_client_module.urllib.request, "build_opener", lambda handler: _FakeOpener())
        cfg = cloud_client_module.CloudConfig(base_url="https://cloud.example", max_retries=0)
        cc = cloud_client_module.CloudClient(config=cfg, secret_provider=secrets)

        cc.request(
            "GET",
            "/health",
            health_endpoint=True,
            extra_headers={"Cookie": "ucan_session=attacker"},
        )

        cookie = captured_headers.get("Cookie")
        assert cookie == "ucan_session=legitimate-session-token"


# ==============================================================================
# B8 — Anti-tamper threshold strictness
# ==============================================================================


class TestAntiTamperThreshold:
    def test_timing_threshold_is_strict(self) -> None:
        from src.security.anti_tamper.guard import AntiTamperGuard

        # The 250 ms threshold let hardware breakpoints and hypervisor
        # single-step hooks (5-20 ms overhead) through undetected.
        assert AntiTamperGuard.TIMING_THRESHOLD_MS <= 20.0

    def test_detect_timing_anomaly_uses_configured_threshold(self) -> None:
        from src.security.anti_tamper.guard import AntiTamperGuard

        # With an absurdly high threshold, the fast probes must not flag.
        assert AntiTamperGuard.detect_timing_anomaly(threshold_ms=10_000.0) is False


# ==============================================================================
# B10 — ISO-TP sub-ms STmin yields to the event loop
# ==============================================================================


class TestIsoTpStMinYieldsToEventLoop:
    def test_submillisecond_stmin_allows_other_coroutines_to_run(self) -> None:
        from src.protocols.uds.isotp import IsoTpSender

        async def scenario() -> tuple[bool, float]:
            sender = IsoTpSender.__new__(IsoTpSender)  # bypass __init__; only pacing needed
            yields: list[float] = []

            async def observer() -> None:
                # Records whenever the event loop lets this coroutine run
                yields.append(time.monotonic())

            observer_task = asyncio.ensure_future(observer())
            # A stream of 0xF1 (100 us) pacing calls across a full window
            for _ in range(2000):
                await sender._apply_st_min(0xF1)
            observer_task.cancel()
            return len(yields) > 0, 0.0

        ran, _ = asyncio.run(scenario())
        assert ran, "event loop was starved during sub-ms STmin pacing"

    def test_submillisecond_stmin_paces_at_least_half_the_target(self) -> None:
        from src.protocols.uds.isotp import IsoTpSender

        async def scenario() -> float:
            sender = IsoTpSender.__new__(IsoTpSender)
            t0 = time.perf_counter_ns()
            for _ in range(20):
                await sender._apply_st_min(0xF9)  # 900 us each -> >= 18 ms total
            return (time.perf_counter_ns() - t0) / 1e6

        elapsed_ms = asyncio.run(scenario())
        assert elapsed_ms >= 15.0, f"pacing under-slept: {elapsed_ms:.2f} ms"

    def test_millisecond_stmin_uses_asyncio_sleep(self) -> None:
        from src.protocols.uds.isotp import IsoTpSender

        async def scenario() -> float:
            sender = IsoTpSender.__new__(IsoTpSender)
            t0 = time.perf_counter_ns()
            await sender._apply_st_min(0x0A)  # 10 ms
            return (time.perf_counter_ns() - t0) / 1e6

        elapsed_ms = asyncio.run(scenario())
        assert elapsed_ms >= 8.0


# ==============================================================================
# B3 — HWID PowerShell path hardening (import-level contract)
# ==============================================================================


class TestHwidPowershellPathHardening:
    def test_run_powershell_source_contains_no_env_or_path_fallback(self) -> None:
        import inspect

        from src.security.hwid import collector

        source = inspect.getsource(collector._run_powershell)
        assert "os.environ" not in source, "%SystemRoot% must not resolve the binary path"
        assert '"powershell"' not in source, "bare PATH fallback must not remain"
        assert "GetSystemDirectoryW" in source, "system directory must come from the Win32 API"
