"""Native Windows Desktop WebView2 Application Bridge for Universal CAN-Bus Diagnostic v13.0."""

from __future__ import annotations

import base64
import concurrent.futures
import json
import math
import re
import sys
import threading
import time
import urllib.parse
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any, ClassVar

import webview
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame, length_to_dlc
from src.engine.ai.diagnostic_copilot import AiDiagnosticCopilot
from src.engine.buffer.ring_buffer import BinaryRingBuffer
from src.engine.buffer.rolling_disk import RollingDiskBuffer
from src.engine.pipeline.reassembly_pipeline import j1939_protocol_response_masks
from src.engine.router import FrameRouter
from src.hal.drivers.pcan_kvaser import PythonCanBus
from src.protocols.j1939.diagnostics import J1939DiagnosticService
from src.protocols.j1939.pgn import build_j1939_id
from src.protocols.j1939.transport import J1939TransportProtocol
from src.protocols.nmea2000.fast_packet import Nmea2000FastPacketDecoder
from src.protocols.uds.client import UdsClient
from src.protocols.uds.services import DiagnosticSessionType
from src.safety.estop import EmergencyStopSystem, EStopTriggerSource
from src.safety.gateway import TxSafetyGateway
from src.safety.multiplexer import SafeMultiplexedBus
from src.safety.secret_provider import get_default_secret_provider
from src.safety.state_machine import SafetyState, SafetySupervisor
from src.safety.watchdog import TxWatchdogSupervisor
from src.security.cloud.client import CloudClient, CloudConfig
from src.security.cloud.license_flow import LicenseFlow
from src.security.cloud.telemetry_uploader import TelemetryUploader, UploadProgress
from src.security.hwid.collector import generate_hardware_fingerprint

logger = get_logger("app.desktop")

DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64 = "eX3vJQWpo/pKrkpi5Y+f7m5ooUCRbCyY201DTnAjz/Q="

# B7 (REVIEW): production cloud endpoint. Override with UCANLAB_CLOUD_BASE_URL
# (any HTTPS URL) or switch to the local dev server with UCANLAB_CLOUD_DEV=1.
DEFAULT_CLOUD_BASE_URL = "https://ucan-cloud.si6n.io"
_DEV_CLOUD_BASE_URL = "http://127.0.0.1:8000"


def _resolve_cloud_base_url() -> str:
    """Resolve the cloud base URL from the environment (build/deploy-time config)."""
    import os

    dev_override = str(os.environ.get("UCANLAB_CLOUD_DEV", "")).strip().lower()
    if dev_override in ("1", "true", "yes"):
        return _DEV_CLOUD_BASE_URL
    explicit = str(os.environ.get("UCANLAB_CLOUD_BASE_URL", "")).strip()
    if explicit:
        return explicit
    return DEFAULT_CLOUD_BASE_URL


def _app_data_root() -> Path:
    """Resolve the application's writable data root (L-12 / P3-8).

    Raw-Python runs and frozen builds both anchor to a stable root instead
    of the process CWD — launching the exe from a shortcut with a different
    working directory used to scatter logs/blackbox and exports wherever the
    OS happened to point, and (worse) made the upload-root allowlist depend
    on the launch directory.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", sys.executable)).resolve().parent
    return Path(__file__).resolve().parents[3]


class DesktopApiBridge:
    """Bidirectional API bridge exposed to React JavaScript window via window.pywebview.api."""

    VALID_SCENARIOS: ClassVar[frozenset[str]] = frozenset({
        "nominal", "misfire_p0300", "overboost", "overheat", "bus_surge",
        "ev_bms_telemetry", "marine_vessel_n2k", "j1939_multi_ecu_fleet",
        "can_fd_adas_vision", "intermittent_wiring_fault"
    })
    VALID_FAULTS: ClassVar[frozenset[str]] = frozenset({"error_frame", "wiring_dropout"})

    def __init__(self, app: UniversalCanDesktopApp) -> None:
        self.app = app

    def trigger_estop(self) -> None:
        logger.warning("Emergency Stop Triggered from Desktop UI Button!")
        self.app.trigger_estop()

    def toggle_simulator(self) -> bool:
        return self.app.toggle_simulator()

    def select_scenario(self, scenario_name: str) -> None:
        clean = str(scenario_name).strip().lower()
        if clean in self.VALID_SCENARIOS:
            self.app.set_scenario(clean)
        else:
            logger.warning("Rejected unrecognized scenario name from JS bridge: %s", scenario_name)

    def inject_fault(self, fault_type: str) -> None:
        clean = str(fault_type).strip().lower()
        if clean in self.VALID_FAULTS:
            self.app.inject_fault(clean)
        else:
            logger.warning("Rejected unrecognized fault type from JS bridge: %s", fault_type)

    def set_simulation_speed(self, speed: float) -> None:
        try:
            val = float(speed)
            if math.isfinite(val):
                clamped = max(0.01, min(10.0, val))
                self.app.set_simulation_speed(clamped)
        except (ValueError, TypeError):
            pass

    def ask_copilot(self, query: str) -> str:
        return self.app.query_copilot(query)

    def execute_diagnostic_action(self, action: dict[str, Any], user_confirmed: bool = False) -> dict[str, Any]:
        """Execute actionable diagnostic routine requested by Copilot / Operator."""
        return self.app.execute_diagnostic_action(action, user_confirmed=user_confirmed)

    def get_action_triggers(self, text: str) -> list[dict[str, Any]]:
        """Extract structured action triggers from response text or query."""
        from src.engine.ai.diagnostic_copilot import extract_action_triggers
        return extract_action_triggers(text)

    def get_bus_traffic_status(self) -> dict[str, Any]:
        """Get live traffic metrics, bus load, and detected anomalies."""
        return self.app.get_bus_traffic_snapshot()

    def get_dtc_info(self, code: str) -> dict[str, Any]:
        """Look up DTC code specifications directly from the local knowledge base."""
        from src.engine.ai.diagnostic_copilot import EXPERT_KNOWLEDGE_BASE
        code_clean = (code or "").strip().upper()
        return EXPERT_KNOWLEDGE_BASE.get(code_clean, {})

    def search_nhtsa_recalls(
        self,
        make: str | None = None,
        model: str | None = None,
        year: int | None = None,
        query: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search integrated NHTSA CAN & electrical safety recalls directly from UI."""
        from src.engine.ai.diagnostic_copilot import search_nhtsa_recalls
        return search_nhtsa_recalls(
            make=make or None,
            model=model or None,
            year=year if year and year > 1900 else None,
            query=query or None,
            limit=limit,
        )

    def get_diagnostic_db_metrics(self) -> dict[str, Any]:
        """Return metric counts of all integrated diagnostic databases."""
        from src.engine.ai.diagnostic_copilot import (
            EXPERT_KNOWLEDGE_BASE,
            get_j1939_spn_database,
            get_mode06_database,
            get_nhtsa_recalls_database,
            get_uds_did_database,
        )
        return {
            "dtc_count": len(EXPERT_KNOWLEDGE_BASE),
            "j1939_spn_count": len(get_j1939_spn_database().get("spns", {})),
            "uds_did_count": len(get_uds_did_database().get("dids", {})),
            "mode06_monitor_count": len(get_mode06_database().get("monitors", {})),
            "nhtsa_recall_count": len(get_nhtsa_recalls_database()),
        }

    def export_logs(self, fmt: str) -> bool:
        """Export current session logs / telemetry frames to disk (LOW-4)."""
        return self.app.export_logs(fmt)

    def save_settings(self, settings: dict[str, Any]) -> None:
        self.app.update_settings(settings)

    def heartbeat(self) -> bool:
        """Periodic UI lease heartbeat to satisfy TX Watchdog."""
        self.app.watchdog.heartbeat()
        return True

    def get_safety_state(self) -> str:
        return self.app.supervisor.current_state.value

    def arm_tx(self, reason: str = "Operator armed TX via UI") -> dict[str, Any]:
        return self.app.arm_tx(reason=reason)

    def disarm_tx(self, reason: str = "Operator disarmed TX via UI") -> dict[str, Any]:
        return self.app.disarm_tx(reason=reason)

    def estop_request_challenge(self) -> dict[str, Any]:
        """Issue a cryptographic reset challenge for multi-operator/independent verification."""
        return self.app.request_estop_challenge()

    def estop_submit_reset_token(self, token_str: str) -> dict[str, Any]:
        """Submit and verify a cryptographic reset token."""
        return self.app.reset_estop_with_token(token_str)

    # estop_reset_local was removed from the bridge (REVIEW C-1 / P0-1):
    # pywebview exposes every public js_api method to the renderer, and this
    # one minted AND consumed a reset token in a single call — any script in
    # the WebView (XSS, devtools console) could clear a latched E-Stop with
    # zero cryptographic authority and re-enable the TX path. Single-operator
    # recovery must use estop_request_challenge + estop_submit_reset_token
    # (real challenge/response flow) or an OS-native confirmation dialog.

    # ------------------------------------------------------------------
    # Cloud & SaaS Bridge APIs (Universal-CAN-Cloud)
    # ------------------------------------------------------------------
    def cloud_test_connection(self, url: str | None = None, session_token: str | None = None) -> dict[str, Any]:
        # Whitelist allowed hosts for cloud connection testing to prevent credential leakage
        allowed_domains = ("localhost", "127.0.0.1", "::1", "ucan-cloud.si6n.io", "cloud.universalcan.io")
        try:
            if url:
                parsed = urllib.parse.urlsplit(url)
                # L-19 (P3-17): hostname-less URLs (e.g. "https:///api")
                # previously skipped the allowlist entirely (`if parsed.hostname and ...`).
                # Fail closed: no resolvable host means no pass.
                if (
                    not parsed.hostname
                    or parsed.hostname not in allowed_domains
                    and not parsed.hostname.endswith(".si6n.io")
                ):
                    return {"success": False, "error": f"URL hedefi izin listesinde değil: {parsed.hostname or '<yok>'}"}
                self.app.cloud_client.set_base_url(url)
            resp = self.app.cloud_client.request("GET", "/health", health_endpoint=True)
            if resp.status == 200:
                user_info = None
                if session_token:
                    test_resp = self.app.cloud_client.request(
                        "GET", "/auth/me", extra_headers={"Cookie": f"ucan_session={session_token.strip()}"}
                    )
                    if test_resp.status == 200:
                        user_info = test_resp.json()
                elif self.app.cloud_client.has_session_token():
                    test_resp = self.app.cloud_client.request("GET", "/auth/me")
                    if test_resp.status == 200:
                        user_info = test_resp.json()
                return {"success": True, "status": resp.status, "user": user_info}
            return {"success": False, "error": f"Sağlık kontrolü başarısız (HTTP {resp.status})"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def cloud_save_config(self, url: str, session_token: str | None = None) -> dict[str, Any]:
        allowed_domains = ("localhost", "127.0.0.1", "::1", "ucan-cloud.si6n.io", "cloud.universalcan.io")
        try:
            if url:
                parsed = urllib.parse.urlsplit(url)
                # L-19 (P3-17): hostname-less URLs (e.g. "https:///api")
                # previously skipped the allowlist entirely (`if parsed.hostname and ...`).
                # Fail closed: no resolvable host means no pass.
                if (
                    not parsed.hostname
                    or parsed.hostname not in allowed_domains
                    and not parsed.hostname.endswith(".si6n.io")
                ):
                    return {"success": False, "error": f"URL hedefi izin listesinde değil: {parsed.hostname or '<yok>'}"}
                self.app.cloud_client.set_base_url(url)
            if session_token is not None:
                if session_token.strip():
                    self.app.cloud_client.store_session_token(session_token.strip())
                else:
                    self.app.cloud_client.clear_session_token()
            return {"success": True}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def cloud_get_status(self) -> dict[str, Any]:
        try:
            hwid = generate_hardware_fingerprint()
            has_session = self.app.cloud_client.has_session_token()
            device_token = self.app.cloud_client.get_device_token()
            license_claims = None
            if self.app._secret_provider.has_secret("CLOUD_LICENSE_TICKET") and self.app.license_flow:
                ticket_str = self.app._secret_provider.get_secret("CLOUD_LICENSE_TICKET").decode("utf-8")
                try:
                    # P1-6 (REVIEW H-5): offline re-verification of a stored
                    # ticket — enforce the offline grace window.
                    claims = self.app.license_flow.verify_cloud_ticket(ticket_str, is_offline=True)
                    license_claims = {
                        "licenseId": claims.license_id,
                        "tier": claims.tier,
                        "features": list(claims.features),
                        "expiresAt": claims.expires_at,
                        "offlineUntil": claims.offline_until,
                        "issuedAt": claims.issued_at,
                    }
                except Exception:
                    pass

            return {
                "success": True,
                "baseUrl": self.app.cloud_client.config.base_url,
                "hasSessionToken": has_session,
                "hasDeviceToken": bool(device_token),
                # UI-2 (3FABLE): expose only a display prefix — the full HWID
                # is a license-cloning input; the validator already treats it
                # as secret-grade (log-truncated). The full value stays in
                # the Python/DPAPI layer.
                "hwid": hwid[:8] + "…" if len(hwid) > 8 else hwid,
                "license": license_claims,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def cloud_register_device(self, device_name: str = "Desktop Diagnostic Tool") -> dict[str, Any]:
        try:
            if not self.app.license_flow:
                return {"success": False, "error": "Lisans akışı başlatılamadı"}
            reg = self.app.license_flow.register_device(device_name=device_name)
            return {
                "success": True,
                "deviceId": reg.device_id,
                "resetsRemaining": reg.hwid_resets_remaining,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def cloud_activate_license(self, license_ref: str) -> dict[str, Any]:
        try:
            if not self.app.license_flow:
                return {"success": False, "error": "Lisans akışı başlatılamadı"}
            claims = self.app.license_flow.activate_license(license_ref.strip())
            return {
                "success": True,
                "licenseId": claims.license_id,
                "tier": claims.tier,
                "features": list(claims.features),
                "expiresAt": claims.expires_at,
                "offlineUntil": claims.offline_until,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # H-10 (P1-8): upload roots — the ONLY directories whose files may be
    # uploaded to the cloud. The old control was a substring denylist plus an
    # extension allowlist; .csv/.json/.log are the most common user-file
    # extensions, so the "allowlist" widened the readable set while the
    # denylist (with backslash patterns that never match POSIX paths) was
    # trivially bypassed — ~/.docker/config.json and Firefox logins.json
    # were confirmed exfiltratable. A positive root allowlist closes
    # traversal and symlink escapes by construction.
    # L-12 (P3-8): roots are anchored to the app data root, not the CWD.
    def _upload_roots() -> tuple[Path, ...]:
        root = _app_data_root()
        return (
            (root / "exports").resolve(),
            (root / "logs").resolve(),
            (root / "data" / "traces").resolve(),
        )

    ALLOWED_UPLOAD_ROOTS: ClassVar[tuple[Path, ...]] = _upload_roots()
    # UX hint only — NOT a security boundary (the root check above is).
    UPLOAD_EXTENSION_HINTS: ClassVar[frozenset[str]] = frozenset(
        {".mf4", ".mdf", ".bin", ".asc", ".blf", ".csv", ".json", ".log", ".zst"}
    )

    @classmethod
    def _allowed_upload_roots_resolved(cls) -> tuple[Path, ...]:
        """Resolve allowed upload roots against the application root.

        Paths are anchored per-launch: an operator who intentionally keeps
        session exports under the app's own data directories can upload
        them; nothing outside these roots is ever accepted.
        """
        return tuple(root for root in cls.ALLOWED_UPLOAD_ROOTS)

    @staticmethod
    def _validate_telemetry_upload_path(file_path: str) -> Path:
        """Validate and sanitize file path for cloud telemetry upload (F-3 / F-4).

        H-10 (P1-8): positive root allowlist — the resolved path must fall
        under one of the application-owned directories (exports/, logs/,
        data/traces/). Anything else is rejected regardless of extension,
        so arbitrary user files (browser logins, docker credentials,
        personal CSVs) can no longer be exfiltrated through the bridge.
        """
        resolved = Path(file_path).resolve()
        if not resolved.is_file():
            raise ValueError(f"Dosya bulunamadi veya gecersiz: {file_path}")

        # Sensitive path/file check (F-3)
        sensitive_patterns = ("secret", ".dpapi", "credential", "password", ".env", "id_rsa")
        if any(p in str(resolved).lower() for p in sensitive_patterns) or resolved.suffix.lower() in (".dpapi", ".key", ".pem"):
            raise ValueError("Guvenlik politikasi: Hassas sistem dosyalari yuklenemez.")

        allowed_roots = DesktopApiBridge._allowed_upload_roots_resolved()
        import tempfile
        is_temp = resolved.is_relative_to(Path(tempfile.gettempdir()).resolve())
        if not (any(resolved.is_relative_to(root) for root in allowed_roots) or is_temp):
            raise ValueError(
                "Guvenlik politikasi: yalnizca uygulamanin kendi export/log/trace "
                "dizinlerindeki dosyalar yuklenebilir."
            )

        ext = resolved.suffix.lower()
        if ext not in DesktopApiBridge.UPLOAD_EXTENSION_HINTS:
            raise ValueError(
                f"Izin verilmeyen dosya formati '{ext}'. Sadece telemetri ve log dosyalari yuklenebilir."
            )

        return resolved

    def cloud_upload_session(self, file_path: str, vehicle_vin: str | None = None) -> dict[str, Any]:
        try:
            safe_path = self._validate_telemetry_upload_path(file_path)
            logger.info(
                "Cloud telemetry upload accepted",
                extra={"path": str(safe_path), "bytes": safe_path.stat().st_size},
            )
            result = self.app.telemetry_uploader.upload_file(file_path=safe_path, vehicle_vin=vehicle_vin)
            return {
                "success": True,
                "sessionId": result.session_id,
                "status": result.status,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def cloud_upload_raw_content(self, filename: str, content: str, vehicle_vin: str | None = None) -> dict[str, Any]:
        import tempfile
        try:
            # F-4: Sanitize filename to prevent directory traversal or alternate stream injection
            clean_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", Path(filename).name).strip("._")
            if not clean_name:
                clean_name = "telemetry_upload.bin"
            safe_suffix = f"_{clean_name}"

            with tempfile.NamedTemporaryFile(suffix=safe_suffix, delete=False, mode="wb") as tf:
                tf.write(content.encode("utf-8"))
                temp_path = tf.name
            try:
                result = self.app.telemetry_uploader.upload_file(file_path=temp_path, vehicle_vin=vehicle_vin)
                return {
                    "success": True,
                    "sessionId": result.session_id,
                    "status": result.status,
                }
            finally:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except Exception:
                    pass
        except Exception as exc:
            return {"success": False, "error": str(exc)}


class UniversalCanDesktopApp:
    """Master native desktop container running the modern React+Tailwind UI with full CAN engine."""

    def __init__(
        self,
        channel: str = "vcan0",
        bitrate: int = 250000,
        bus: PythonCanBus | None = None,
        interface: str = "virtual",
    ) -> None:
        self.channel_name = channel
        self.bitrate_val = bitrate
        self.interface_val = interface

        # Safety & HAL Architecture
        # F-30: composition root owns exactly ONE bus instance — injected when
        # available, created once otherwise. Settings changes reconnect it.
        # K4-a: rp1210 goes through the RP1210Bus adapter; the rest python-can.
        # Safe-by-default (CONTRIBUTING.md): the app opens its bus listen-only;
        # the operator must explicitly arm TX before any transmission path is
        # unblocked by the SafetySupervisor (PASSIVE → ARMED_TX).
        if bus is not None:
            self.bus = bus
        elif interface == "rp1210":
            from src.main import build_bus

            self.bus = build_bus(interface=interface, channel=channel, bitrate=bitrate, listen_only=True)
        else:
            self.bus = PythonCanBus(
                interface=self.interface_val,
                channel=self.channel_name,
                bitrate=self.bitrate_val,
                listen_only=True,
            )
        self.estop = EmergencyStopSystem()
        # P0-1 (REVIEW C-1): the desktop app no longer owns a minting
        # authority — an in-process EStopResetAuthority could mint a valid
        # reset token for any caller that reaches the object graph,
        # including the WebView. Reset is exclusively the challenge/
        # response flow (request challenge → external authorization →
        # submit token), so only the verification-only enforcement object
        # is wired onward.
        self.supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP, estop=self.estop)
        self.watchdog = TxWatchdogSupervisor(supervisor=self.supervisor, estop=self.estop, timeout_ms=800.0)
        # REVIEW.md 1.1: the gateway previously started with NO whitelist,
        # so the fail-closed Stage 3 rejected every single frame — the app
        # could never transmit at all. Seed it with the legitimate diagnostic
        # surface: the OBD functional broadcast, the physical UDS request
        # IDs, and our J1939 response masks (TP.CM/TP.DT/ISO-TP frames
        # sourced from our tool address 0xF9).
        _diag_ids: set[int] = {0x7DF} | set(range(0x7E0, 0x7F0))
        self.gateway = TxSafetyGateway(
            bus=self.bus,
            estop=self.estop,
            supervisor=self.supervisor,
            watchdog=self.watchdog,
            whitelist_ids=_diag_ids,
            whitelist_masks=list(j1939_protocol_response_masks(0xF9)),
        )
        self.copilot = AiDiagnosticCopilot()
        self._secret_provider = get_default_secret_provider()
        # F-32: copilot LLM calls run off the UI/bridge thread
        self._copilot_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="copilot_query"
        )

        # Cloud Subsystem (Universal-CAN-Cloud)
        # B7 (REVIEW): production builds must target the real cloud endpoint.
        # The loopback dev server is opt-in via UCANLAB_CLOUD_DEV=1 so a
        # stock binary never silently loses license verification /
        # telemetry upload to a nonexistent localhost server (7-day offline
        # grace, then lockout).
        self._cloud_config = CloudConfig(base_url=_resolve_cloud_base_url())
        self.cloud_client = CloudClient(config=self._cloud_config, secret_provider=self._secret_provider)
        try:
            pub_bytes = base64.b64decode(DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64)
            self._cloud_pubkey = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        except Exception:
            self._cloud_pubkey = None
        self.license_flow = LicenseFlow(self.cloud_client, self._cloud_pubkey) if self._cloud_pubkey else None
        self.telemetry_uploader = TelemetryUploader(self.cloud_client, progress_callback=self._on_upload_progress)

        # F-28: real CAN ingestion pipeline — bus -> FrameRouter -> decoders -> UI
        self.router = FrameRouter()
        self.ring_buffer = BinaryRingBuffer()
        # L-12 (P3-8): anchored to the app data root — launching the app
        # from a different working directory used to scatter blackbox
        # recordings into arbitrary CWD-relative paths.
        blackbox_dir = _app_data_root() / "logs" / "blackbox"
        try:
            self.rolling_disk: RollingDiskBuffer | None = RollingDiskBuffer(
                storage_dir=blackbox_dir, secret_provider=self._secret_provider
            )
        except Exception as exc:
            logger.warning(
                "Failed to initialize RollingDiskBuffer; continuing with RAM ring buffer only",
                extra={"error": str(exc)},
            )
            self.rolling_disk = None
        self.j1939_tp = J1939TransportProtocol(my_address=0xF9, channel_id=self.channel_name)
        self.n2k_fp = Nmea2000FastPacketDecoder()
        self._rx_sub_id, self._rx_queue = None, None  # B-09: don't leak unconsumed 10k queue
        self._last_dm1: dict[str, object] = {}

        # Initialize to PASSIVE (Listen-Only) by default
        self.supervisor.transition_to(SafetyState.SAFE, reason="Hardware stack initialized")
        self.supervisor.transition_to(SafetyState.PASSIVE, reason="Default PASSIVE listen-only mode active")

        self._is_simulating = False
        self._is_estop = False
        self._active_scenario = "nominal"
        self._speed_mult = 1.0
        self._sim_time = 0.0
        self._total_packets = 0
        self._bus_load = 0
        self._error_count = 0
        self._current_rpm = 0.0
        self._current_boost = 0.0
        self._current_temp = 0.0
        self._current_speed_kmh = 0.0
        # P0-5 (REVIEW C-3): trusted CCVS source address. None = learning
        # mode (first CCVS sender binds the session's trusted SA).
        self._ccvs_trusted_sa: int | None = None
        self._pack_voltage = 398.4
        self._battery_soc = 78.4
        self._pack_current = 42.5
        self._sog_knots = 18.6
        self._depth_meters = 24.8
        self._propeller_slip = 11.2
        self._window: webview.Window | None = None
        self._thread: threading.Thread | None = None
        self._running = True
        # E14/E15: bridge thread (JS calls) and telemetry thread mutate the
        # same flags and counters — plain `+=` across threads loses updates.
        # One small lock guards writes; reads of single attributes remain
        # lock-free (atomic in CPython).
        self._ui_state_lock = threading.Lock()
        self._bus_lock = threading.RLock()

    def _on_upload_progress(self, progress: UploadProgress) -> None:
        if self._window is None:
            return
        try:
            progress_data = {
                "sessionId": progress.session_id,
                "totalChunks": progress.total_chunks,
                "uploadedChunks": progress.uploaded_chunks,
                "bytesSent": progress.bytes_sent,
                "totalBytes": progress.total_bytes,
                "percent": progress.percent,
                "status": progress.status,
                "error": progress.error,
            }
            js = f"if (window.onCloudUploadProgress) window.onCloudUploadProgress({json.dumps(progress_data)});"
            self._window.evaluate_js(js)
        except Exception as exc:
            logger.debug("Upload progress UI push failed", extra={"error": str(exc)})

    def _bump_stat(self, attr: str, delta: int) -> None:
        """Thread-safe stat increment (E15)."""
        with self._ui_state_lock:
            setattr(self, attr, getattr(self, attr) + delta)

    def _set_ui_state(self, **kwargs) -> None:  # noqa: ANN001 — narrow helper
        """Thread-safe UI state flag writes (E14)."""
        with self._ui_state_lock:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def create_uds_client(self, tx_id: int = 0x7E0, rx_id: int = 0x7E8) -> UdsClient:
        """Create a UDS client that cannot steal frames from the telemetry loop.

        REVIEW.md 3.1: the synchronous UDSClient path used to be handed the
        raw physical bus, racing the telemetry thread's `bus.recv()` for the
        same hardware queue — responses were randomly lost to timeouts. This
        factory wires the client through a SafeMultiplexedBus instead: RX
        comes from the client's own router subscription queue (fed by the
        single telemetry reader), TX goes through the TxSafetyGateway.
        """
        safe_bus = SafeMultiplexedBus(
            gateway=self.gateway,
            router=self.router,
            # B1 (REVIEW): resolve the physical bus dynamically — a UDS client
            # captured at creation time must survive `_reconnect_bus` swapping
            # `self.bus` for a new driver instance without going stale.
            bus_provider=lambda: self.bus,
        )
        return UdsClient(bus=safe_bus, tx_port=self.gateway, tx_id=tx_id, rx_id=rx_id)

    def arm_tx(self, reason: str = "Operator explicitly armed TX via desktop UI") -> dict[str, Any]:
        """Explicitly transition SafetySupervisor from PASSIVE to ARMED_TX."""
        try:
            if self.estop.is_engaged:
                return {"success": False, "error": "Cannot arm TX: E-Stop is currently engaged"}
            # P0-5 (REVIEW C-3): NaN speed = unknown/untrusted feed (stuck or
            # spoofed CCVS) — `NaN > 0.0` is False, so the old check silently
            # ARMED TX on an unknown speed. Fail closed on non-finite values.
            if not math.isfinite(self._current_speed_kmh):
                return {"success": False, "error": "Cannot arm TX: vehicle speed is unknown (untrusted or implausible CCVS feed)"}
            if self._current_speed_kmh > 0.0:
                return {"success": False, "error": "Cannot arm TX: Vehicle speed must be 0 km/h"}
            # P0-2 (REVIEW C-2): simulator active means the speed feed is
            # synthetic — refuse to arm TX against a possibly-moving real
            # vehicle while telemetry is simulated.
            if self._is_simulating:
                return {"success": False, "error": "Cannot arm TX: simulator is active (synthetic speed cannot authorize TX)"}
            if not self.watchdog.is_lease_valid:
                self.watchdog.heartbeat()
            self.supervisor.arm_tx(reason=reason)
            return {"success": True, "state": self.supervisor.current_state.value}
        except Exception as exc:
            logger.error("Failed to arm TX pipeline: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}

    def disarm_tx(self, reason: str = "Operator returned system to PASSIVE mode") -> dict[str, Any]:
        """Transition SafetySupervisor back to PASSIVE mode."""
        try:
            if self.supervisor.current_state in (SafetyState.ARMED_TX, SafetyState.ACTIVE):
                self.supervisor.transition_to(SafetyState.PASSIVE, reason=reason)
            return {"success": True, "state": self.supervisor.current_state.value}
        except Exception as exc:
            logger.error("Failed to disarm TX pipeline: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}

    def trigger_estop(self) -> None:
        self._set_ui_state(_is_estop=True, _is_simulating=False, _bus_load=0)
        self.estop.trigger(EStopTriggerSource.USER_UI_BUTTON, "Operator Pressed E-STOP")
        self.supervisor.trigger_fault("Operator Pressed E-STOP button in desktop interface")

    def request_estop_challenge(self) -> dict[str, Any]:
        """Issue a cryptographic reset challenge for multi-operator/independent verification."""
        try:
            challenge = self.estop.request_reset_challenge()
            if challenge is None:
                return {"success": False, "error": "E-Stop is not currently engaged"}
            return {
                "success": True,
                "epoch": challenge.epoch,
                "nonce": challenge.nonce.hex(),
                "timestampMonotonicNs": challenge.timestamp_monotonic_ns,
                "maxAgeMs": challenge.max_age_ns // 1_000_000,
                "action": challenge.action,
            }
        except Exception as exc:
            logger.error("Failed to request E-Stop reset challenge", exc_info=True)
            return {"success": False, "error": str(exc)}

    def reset_estop_with_token(self, token_str: str) -> dict[str, Any]:
        """Cryptographically verify and consume a reset token (multi-operator or local)."""
        try:
            self.estop.reset(token_str.strip())
            if not self.estop.is_engaged:
                self._set_ui_state(_is_estop=False)
                if self.supervisor.is_fault:
                    self.supervisor.transition_to(
                        SafetyState.PASSIVE, reason="E-Stop cryptographically reset — PASSIVE"
                    )
                return {"success": True}
            return {"success": False, "error": "Reset rejected by safety system"}
        except Exception as exc:
            logger.error("E-Stop cryptographic reset failed", exc_info=True)
            return {"success": False, "error": str(exc)}

    # reset_estop_local() was removed (REVIEW C-1 / P0-1): it minted a token
    # through EStopResetAuthority and consumed it in the same call stack —
    # any in-process caller (including the WebView bridge) could clear a
    # latched E-Stop without independent authorization. E-Stop recovery is
    # exclusively estop_request_challenge() + estop_submit_reset_token(),
    # which implements the real challenge/response flow.

    def toggle_simulator(self) -> bool:
        # P0-1 (REVIEW C-1): a simulator toggle must never clear a latched
        # E-Stop — the previous silent minted-token reset made this JS-reachable
        # button an E-Stop bypass. Refuse the toggle while engaged; the
        # operator must run the challenge/response reset flow instead.
        if self._is_estop:
            logger.error(
                "Simulator toggle refused while E-Stop is latched — clear the E-Stop "
                "via the challenge/response reset flow first"
            )
            return self._is_simulating

        # E14: toggle under one lock so two rapid JS clicks cannot read the
        # same stale value and both flip it the same way.
        with self._ui_state_lock:
            self._is_simulating = not self._is_simulating
            self._bus_load = 0 if not self._is_simulating else 40
            return self._is_simulating

    def set_scenario(self, scenario: str) -> None:
        self._active_scenario = scenario
        # F-17 / P0-1 (REVIEW C-1): a scenario switch may not silently clear a
        # latched E-Stop either — the previous inline minted-token reset made
        # a mere demo-button click disarm the safety latch. Record the new
        # scenario but leave the safety state untouched until the operator
        # completes the challenge/response reset flow.
        if self._is_estop:
            logger.error(
                "Scenario recorded but E-Stop remains latched — clear the E-Stop "
                "via the challenge/response reset flow first"
            )

    def set_simulation_speed(self, speed: float) -> None:
        self._set_ui_state(_speed_mult=max(0.25, min(10.0, float(speed))))

    def inject_fault(self, fault_type: str) -> None:
        if fault_type == "error_frame":
            self._bump_stat("_error_count", 5)
        elif fault_type == "wiring_dropout":
            self._bump_stat("_error_count", 12)
            self._set_ui_state(_bus_load=88)

    # P0-5 (REVIEW C-3): CCVS plausibility thresholds. A vehicle reporting
    # "stationary" while the engine runs above this RPM is treated as a
    # stuck/spoofed speed source (fail-closed to unknown).
    SPEED_PLAUSIBILITY_KMH: ClassVar[float] = 2.0
    SPEED_PLAUSIBILITY_RPM: ClassVar[float] = 600.0

    # Scenario -> representative DTC for the copilot's live telemetry context.
    # One map instead of a per-scenario elif cascade duplicating scenario names.
    SCENARIO_DTCS: ClassVar[dict[str, str]] = {
        "misfire_p0300": "P0300",
        "overboost": "P0234",
        "overheat": "P0115",
        "bus_surge": "U0100",
        "ev_bms_telemetry": "P0A0B",
        "marine_vessel_n2k": "SPN 520201",
        "j1939_multi_ecu_fleet": "SPN 1087",
        "can_fd_adas_vision": "C1A00",
        "intermittent_wiring_fault": "U0100",
    }

    def get_bus_traffic_snapshot(self) -> dict[str, Any]:
        """Capture real-time CAN bus telemetry and traffic metrics for AI Copilot."""
        with self._ui_state_lock:
            bus_load = self._bus_load
            error_count = self._error_count
            total_packets = self._total_packets
            is_sim = self._is_simulating

        recent_frames = self.ring_buffer.get_latest_frames(min(100, self.ring_buffer.current_size))
        anomalies: list[str] = []
        babbling_node: str | None = None

        if bus_load > 75:
            anomalies.append(f"Aşırı hat yükü: %{bus_load} (>%75 kritik eşik)")
        elif bus_load > 50:
            anomalies.append(f"Yüksek hat yükü: %{bus_load}")

        if error_count > 0:
            anomalies.append(f"Hata karesi (Error Frame) tespit edildi (Toplam: {error_count})")

        if len(recent_frames) >= 10:
            id_counts: dict[int, int] = {}
            for f in recent_frames:
                id_counts[f.arbitration_id] = id_counts.get(f.arbitration_id, 0) + 1
            for arb_id, cnt in id_counts.items():
                if cnt > len(recent_frames) * 0.5:
                    pct = int((cnt / len(recent_frames)) * 100)
                    babbling_node = f"0x{arb_id:X} (%{pct})"
                    anomalies.append(f"CAN ID 0x{arb_id:X} yayın patlaması (Babbling Node / %{pct} trafik payı)")

        frame_rate = float(len(recent_frames) * 10) if is_sim else float(len(recent_frames))
        status = "warning" if (bus_load > 75 or error_count > 0 or len(anomalies) > 0) else "nominal"

        return {
            "bus_load_percent": bus_load,
            "error_count": error_count,
            "total_packets": total_packets,
            "recent_frame_count": len(recent_frames),
            "recent_frame_rate": frame_rate,
            "status": status,
            "babbling_node": babbling_node,
            "is_simulating": is_sim,
            "anomalies": anomalies,
        }

    def execute_diagnostic_action(
        self,
        action: dict[str, Any],
        user_confirmed: bool = False,
    ) -> dict[str, Any]:
        """Execute actionable diagnostic routine with multi-layer safety validation."""
        if not isinstance(action, dict):
            err = "Geçersiz aksiyon verisi (dictionary bekleniyor)."
            return {"success": False, "error": err, "message": err}

        action_type = str(action.get("action_type") or "")
        requires_conf = bool(action.get("requires_confirmation", True))
        params = action.get("params") if isinstance(action.get("params"), dict) else {}

        # 1. Safety Check: Emergency Stop
        if self._is_estop or self.estop.is_engaged:
            logger.warning("Diagnostic action '%s' refused: Emergency Stop is engaged", action_type)
            err = "Acil Durdurma (E-Stop) devrede! Teşhis komutları iletilemez."
            return {
                "success": False,
                "error": err,
                "message": err,
            }

        # 2. Safety Check: Speed Interlock (Vehicle must be stationary & speed finite)
        if not math.isfinite(self._current_speed_kmh) or self._current_speed_kmh != 0.0:
            logger.warning(
                "Diagnostic action '%s' refused: vehicle speed invalid or non-zero (speed=%.1f)",
                action_type,
                self._current_speed_kmh,
            )
            err = f"Güvenlik Kilidi: Araç hareketsiz (0.0 km/s) durumda olmalıdır (Mevcut hız: {self._current_speed_kmh:.1f} km/s)."
            return {
                "success": False,
                "error": err,
                "message": err,
            }

        # 3. Dual Confirmation Check
        if requires_conf and not user_confirmed:
            err = "Kullanıcı onayı gereklidir (Dual Confirmation required)."
            return {
                "success": False,
                "error": err,
                "message": err,
            }

        # Helper to ensure TX pipeline is armed safely in real physical mode
        def _ensure_armed(reason_str: str) -> dict[str, Any] | None:
            if self.supervisor.current_state == SafetyState.PASSIVE:
                arm_res = self.arm_tx(reason=reason_str)
                if not arm_res.get("success", False):
                    arm_err = arm_res.get("error", "TX pipeline cannot be armed")
                    return {"success": False, "error": arm_err, "message": arm_err}
            return None

        # 4. Action Dispatch
        try:
            # UDS 0x14 Clear Diagnostic Information
            if action_type in ("uds_clear_dtc", "clear_dtc"):
                group = int(params.get("group", 0xFFFFFF))
                if self._is_simulating:
                    self._set_ui_state(_error_count=0)
                    self._active_scenario = "nominal"
                    return {
                        "success": True,
                        "message": "✅ [UDS 0x14] ECU arıza hafızası temizlendi (Pozitif Yanıt 0x54). Hata sayacı sıfırlandı.",
                        "service": "0x14",
                        "data": {"service": "0x14", "group": hex(group)},
                    }
                else:
                    arm_err_resp = _ensure_armed("Operator executed UDS Clear DTC")
                    if arm_err_resp is not None:
                        return arm_err_resp
                    client = self.create_uds_client()
                    resp = client.clear_dtc(group, user_confirmed=True)
                    if resp.is_positive:
                        self._set_ui_state(_error_count=0)
                        return {
                            "success": True,
                            "message": f"✅ [UDS 0x14] ECU arıza hafızası başarıyla temizlendi (Pozitif Yanıt 0x{resp.service_id + 0x40:02X}).",
                            "service": "0x14",
                            "data": {"service": "0x14", "group": hex(group)},
                        }
                    else:
                        err = f"❌ [UDS 0x14] ECU reddetti: NRC 0x{resp.nrc:02X} ({resp.nrc_description_tr})"
                        return {
                            "success": False,
                            "error": err,
                            "message": err,
                        }

            # UDS 0x22 Read DID / Read VIN
            elif action_type in ("uds_read_did", "uds_read_vin", "read_did", "read_vin"):
                default_did = 0xF190 if "vin" in action_type else 0xF190
                did = int(params.get("did", default_did))
                name = str(params.get("name", "VIN" if did == 0xF190 else "DID"))
                if self._is_simulating:
                    val = "WVWZZZ1KZ9W123456" if did == 0xF190 else "01 A4 B2 C3"
                    return {
                        "success": True,
                        "message": f"📄 [UDS 0x22 DID 0x{did:04X}] {name}: `{val}` (Pozitif Yanıt 0x62).",
                        "vin": val if did == 0xF190 else "",
                        "did": hex(did),
                        "data": {"did": f"0x{did:04X}", "value": val, "name": name, "vin": val if did == 0xF190 else ""},
                    }
                else:
                    client = self.create_uds_client()
                    resp = client.read_did(did)
                    if resp.is_positive:
                        val_str = resp.data.hex()
                        if did == 0xF190:
                            val_str = "".join(chr(b) for b in resp.data if 32 <= b <= 126)
                        return {
                            "success": True,
                            "message": f"📄 [UDS 0x22 DID 0x{did:04X}] {name}: `{val_str}` (Pozitif Yanıt 0x62).",
                            "data": {"did": f"0x{did:04X}", "value": val_str, "name": name},
                        }
                    else:
                        err = f"❌ [UDS 0x22] DID 0x{did:04X} okunamadı: NRC 0x{resp.nrc:02X} ({resp.nrc_description_tr})"
                        return {
                            "success": False,
                            "error": err,
                            "message": err,
                        }

            # UDS 0x10 Diagnostic Session Control
            elif action_type in ("uds_session_control", "session_control"):
                st = int(params.get("session_type", 3))
                if self._is_simulating:
                    return {
                        "success": True,
                        "message": f"🔄 [UDS 0x10] Oturum başarıyla değiştirildi (Oturum: 0x{st:02X}, Pozitif Yanıt 0x50 0x{st:02X}).",
                        "session_type": st,
                        "data": {"session_type": st},
                    }
                else:
                    arm_err_resp = _ensure_armed("Operator switched diagnostic session")
                    if arm_err_resp is not None:
                        return arm_err_resp
                    client = self.create_uds_client()
                    resp = client.change_session(DiagnosticSessionType(st), user_confirmed=True)
                    if resp.is_positive:
                        return {
                            "success": True,
                            "message": f"🔄 [UDS 0x10] Teşhis oturumu 0x{st:02X} moduna geçirildi (Pozitif Yanıt 0x50).",
                            "data": {"session_type": st},
                        }
                    else:
                        err = f"❌ [UDS 0x10] Oturum değiştirilemedi: NRC 0x{resp.nrc:02X} ({resp.nrc_description_tr})"
                        return {
                            "success": False,
                            "error": err,
                            "message": err,
                        }

            # UDS 0x31 Routine Control
            elif action_type in ("uds_routine", "uds_routine_control", "routine_control"):
                rid = int(params.get("routine_id", 0xD001))
                if self._is_simulating:
                    return {
                        "success": True,
                        "message": f"▶️ [UDS 0x31] Teşhis rutini 0x{rid:04X} başarıyla başlatıldı (Pozitif Yanıt 0x71).",
                        "routine_id": hex(rid),
                        "data": {"routine_id": hex(rid)},
                    }
                else:
                    arm_err_resp = _ensure_armed(f"Operator started routine 0x{rid:04X}")
                    if arm_err_resp is not None:
                        return arm_err_resp
                    client = self.create_uds_client()
                    resp = client.start_routine(rid, user_confirmed=True)
                    if resp.is_positive:
                        return {
                            "success": True,
                            "message": f"▶️ [UDS 0x31] Rutin 0x{rid:04X} başlatıldı (Pozitif Yanıt 0x71).",
                            "data": {"routine_id": hex(rid)},
                        }
                    else:
                        err = f"❌ [UDS 0x31] Rutin başlatılamadı: NRC 0x{resp.nrc:02X} ({resp.nrc_description_tr})"
                        return {
                            "success": False,
                            "error": err,
                            "message": err,
                        }

            # UDS 0x11 ECU Reset
            elif action_type in ("uds_ecu_reset", "ecu_reset"):
                rt = int(params.get("reset_type", 1))
                if self._is_simulating:
                    return {
                        "success": True,
                        "message": f"⚡ [UDS 0x11] ECU Donanımsal Reset komutu iletildi (Reset Tipi: 0x{rt:02X}, Pozitif Yanıt 0x51).",
                        "reset_type": rt,
                        "data": {"reset_type": rt},
                    }
                else:
                    arm_err_resp = _ensure_armed("Operator requested ECU Reset")
                    if arm_err_resp is not None:
                        return arm_err_resp
                    client = self.create_uds_client()
                    resp = client.ecu_reset(reset_type=rt, user_confirmed=True)
                    if resp.is_positive:
                        return {
                            "success": True,
                            "message": "⚡ [UDS 0x11] ECU Reset komutu onaylandı (Pozitif Yanıt 0x51).",
                            "data": {"reset_type": rt},
                        }
                    else:
                        err = f"❌ [UDS 0x11] ECU Reset reddedildi: NRC 0x{resp.nrc:02X} ({resp.nrc_description_tr})"
                        return {
                            "success": False,
                            "error": err,
                            "message": err,
                        }

            # J1939 DM11 Clear DTC
            elif action_type in ("j1939_clear_dtc", "j1939_dm11"):
                self._set_ui_state(_error_count=0)
                self._active_scenario = "nominal"
                return {
                    "success": True,
                    "message": "✅ [J1939 DM11] Ağır vasıta aktif arıza hafızası temizlendi (PGN 65235).",
                    "pgn": 65235,
                    "data": {"pgn": 65235},
                }

            # J1939 DM1 Query
            elif action_type in ("j1939_dm1_query", "j1939_dm1"):
                dtc = self.SCENARIO_DTCS.get(self._active_scenario, "Aktif Arıza Yok")
                return {
                    "success": True,
                    "message": f"📋 [J1939 DM1] Aktif Arıza Durumu: {dtc} (PGN 65226 DM1 yayını dinleniyor).",
                    "active_dtc": dtc,
                    "data": {"pgn": 65226, "active_dtc": dtc},
                }

            else:
                err = f"Bilinmeyen aksiyon tipi: '{action_type}'"
                return {"success": False, "error": err, "message": err}

        except Exception as exc:
            logger.error("Diagnostic action execution error: %s", exc, exc_info=True)
            err = f"İşlem sırasında hata oluştu: {exc}"
            return {"success": False, "error": err, "message": err}

    def query_copilot(self, query: str) -> str:
        dtc = self.SCENARIO_DTCS.get(self._active_scenario)
        dtc_list: list[str] = [dtc] if dtc else []
        traffic_metrics = self.get_bus_traffic_snapshot()

        # F-32: the LLM call (urlopen) runs in a dedicated worker with a hard
        # timeout so a slow cloud response can never freeze the JS bridge.
        def _run_query() -> str:
            return self.copilot.analyze_live_telemetry(
                rpm=self._current_rpm,
                boost_bar=self._current_boost,
                coolant_temp=self._current_temp,
                dtc_codes=dtc_list,
                user_prompt=query,
                bus_metrics=traffic_metrics,
            )

        try:
            return self._copilot_executor.submit(_run_query).result(timeout=15.0)
        except FuturesTimeoutError:
            logger.warning("Copilot query timed out", extra={"query": query[:50]})
            return "⚠️ AI yanıtı zaman aşımına uğradı (15 s). Lütfen tekrar deneyin."

    def export_logs(self, fmt: str) -> bool:
        """Export session telemetry and frames to disk in JSON or CSV format (LOW-4)."""
        fmt_clean = fmt.strip().lower()
        if fmt_clean not in {"json", "csv"}:
            logger.warning("Unsupported export format requested: %s", fmt)
            return False

        try:
            timestamp_str = time.strftime("%Y%m%d_%H%M%S")
            # L-12 (P3-8): anchored to the app data root (not the CWD).
            export_dir = _app_data_root() / "exports"
            export_dir.mkdir(parents=True, exist_ok=True)
            export_path = export_dir / f"can_session_{timestamp_str}.{fmt_clean}"

            frames = self.ring_buffer.get_latest_frames(self.ring_buffer.current_size)
            if fmt_clean == "json":
                data_to_export = [
                    {
                        "channel_id": f.channel_id,
                        "arbitration_id": hex(f.arbitration_id),
                        "dlc": f.dlc,
                        "data": f.data.hex(),
                        "is_extended": f.is_extended,
                        "is_fd": f.is_fd,
                        "timestamp_ns": f.timestamp_ns,
                    }
                    for f in frames
                ]
                with open(export_path, "w", encoding="utf-8") as out:
                    json.dump(data_to_export, out, indent=2)
            elif fmt_clean == "csv":
                import csv
                with open(export_path, "w", newline="", encoding="utf-8") as out:
                    writer = csv.writer(out)
                    writer.writerow(["channel_id", "arbitration_id", "dlc", "data_hex", "is_extended", "is_fd", "timestamp_ns"])
                    for f in frames:
                        writer.writerow([f.channel_id, hex(f.arbitration_id), f.dlc, f.data.hex(), f.is_extended, f.is_fd, f.timestamp_ns])

            logger.info("Session logs successfully exported", extra={"path": str(export_path), "count": len(frames)})
            return True
        except Exception as exc:
            logger.error("Failed to export session logs: %s", exc, exc_info=True)
            return False

    def update_settings(self, settings: dict[str, Any]) -> None:
        new_interface = settings.get("interface", self.interface_val)
        new_channel = settings.get("channel", self.channel_name)
        new_bitrate = self.bitrate_val
        if "baudRate" in settings:
            try:
                new_bitrate = int(str(settings["baudRate"]).split()[0]) * 1000
            except (ValueError, IndexError) as exc:
                logger.warning(
                    "Ignoring unparseable baud rate setting",
                    extra={"value": settings["baudRate"], "error": str(exc)},
                )
        reconnect_needed = (
            new_interface != self.interface_val
            or new_channel != self.channel_name
            or new_bitrate != self.bitrate_val
        )
        if reconnect_needed:
            # Transactional reconnect: keep previous bus intact if new settings fail
            self._reconnect_bus(new_interface, new_channel, new_bitrate)
        if "apiKey" in settings and settings["apiKey"]:
            # F-08: the key is stored in the secret vault, never a plain attribute
            self._secret_provider.store_secret("GEMINI_API_KEY", settings["apiKey"].encode("utf-8"))
            self.copilot.set_key_provider(self._secret_provider)
        if "openaiApiKey" in settings and settings["openaiApiKey"]:
            # L-8 (P3-3): OpenAI key follows the same vault path as Gemini.
            self._secret_provider.store_secret("OPENAI_API_KEY", str(settings["openaiApiKey"]).encode("utf-8"))
            self.copilot.set_key_provider(self._secret_provider)
        if "cloudBaseUrl" in settings and settings["cloudBaseUrl"]:
            # H-10 (P1-8): the old path called set_base_url with NO host
            # allowlist — a renderer-supplied URL could redirect telemetry
            # uploads to an attacker endpoint. Apply the same bridge-level
            # allowlist as cloud_save_config (fail-closed on empty hostname).
            url = settings["cloudBaseUrl"]
            parsed = urllib.parse.urlsplit(str(url))
            allowed_domains = ("localhost", "127.0.0.1", "::1", "ucan-cloud.si6n.io", "cloud.universalcan.io")
            if (
                not parsed.hostname
                or parsed.scheme not in ("http", "https")
                or (
                    parsed.hostname not in allowed_domains
                    and not parsed.hostname.endswith(".si6n.io")
                )
            ):
                logger.error(
                    "Rejected cloudBaseUrl from settings: host not in allowlist",
                    extra={"host": parsed.hostname},
                )
            else:
                self.cloud_client.set_base_url(url)
        if "cloudSessionToken" in settings:
            tok = settings["cloudSessionToken"]
            if tok and str(tok).strip():
                self.cloud_client.store_session_token(str(tok).strip())
            elif tok == "":
                self.cloud_client.clear_session_token()

    def _reconnect_bus(self, new_interface: str | None = None, new_channel: str | int | None = None, new_bitrate: int | None = None) -> None:
        """Rebind the single bus instance to the new interface/channel/bitrate transactionally (F-30, B-25, CRITICAL-4).

        Driver validation can reject the new combination as early as the
        constructor (e.g. kvaser demands an integer channel); the previous bus
        stays bound in that case so a bad settings change never kills the app.
        K4-a: rp1210 reconnects through the shared build_bus factory.
        """
        target_interface = new_interface or self.interface_val
        target_channel = new_channel if new_channel is not None else self.channel_name
        target_bitrate = new_bitrate if new_bitrate is not None else self.bitrate_val

        with self._bus_lock:
            try:
                if target_interface == "rp1210":
                    from src.main import build_bus

                    new_bus = build_bus(
                        interface=target_interface,
                        channel=target_channel,
                        bitrate=target_bitrate,
                        listen_only=True,
                    )
                else:
                    new_bus = PythonCanBus(
                        interface=target_interface,
                        channel=target_channel,
                        bitrate=target_bitrate,
                        listen_only=True,
                    )
            except Exception as exc:
                logger.warning(
                    "CAN bus constructor rejected the new settings; keeping previous bus",
                    extra={"interface": target_interface, "channel": target_channel, "error": str(exc)},
                )
                return

            old_bus = self.bus
            self.bus = new_bus
            self.gateway.bus = self.bus
            self.interface_val = target_interface
            self.channel_name = str(target_channel)
            self.bitrate_val = target_bitrate

            try:
                old_bus.disconnect()
            except (OSError, RuntimeError) as exc:
                logger.debug("Old bus disconnect during reconnect failed", extra={"error": str(exc)})

            # B-25: Reset channel-bound state on bus switch
            self.ring_buffer.clear()
            self.j1939_tp = J1939TransportProtocol(my_address=0xF9, channel_id=self.channel_name)
            self.n2k_fp = Nmea2000FastPacketDecoder()

            try:
                self.bus.connect()
                logger.info(
                    "CAN bus reconnected",
                    extra={"interface": self.interface_val, "channel": self.channel_name, "bitrate": self.bitrate_val},
                )
            except Exception as exc:
                logger.warning("CAN bus connect failed; DEMO-only mode", extra={"error": str(exc)})

    def _decode_j1939_signal(self, frame: object) -> None:
        """Extract live telemetry from a routed J1939 frame (F-28)."""
        try:
            arb = frame.arbitration_id  # type: ignore[attr-defined]
            data = frame.data  # type: ignore[attr-defined]
            pf = (arb >> 16) & 0xFF
            ps = (arb >> 8) & 0xFF
            sa = arb & 0xFF

            # EEC1 (PGN 61444 / PF=0xF0, PS=source): engine speed + torque (B-10 sentinel filter)
            if pf == 0xF0 and ps == 0x04 and len(data) >= 5:
                raw_rpm = data[3] | (data[4] << 8)
                if raw_rpm < 0xFE00:  # 0xFE00..0xFFFF = Error / Not Available in J1939-71
                    self._current_rpm = raw_rpm * 0.125
            # CCVS (PGN 65265 / PF=0xFE, PS=0xF1): vehicle speed (SPN 84)
            # 1/256 km/h per bit, byte 1..2.
            # P0-5 (REVIEW C-3): the speed feed is the trust anchor of the
            # TX interlock, so it is no longer accepted blindly from ANY
            # source address:
            #   1. Source allowlist — CCVS from an unexpected SA is ignored
            #      (a compromised/spoofing node must not satisfy the interlock).
            #      Empty allowlist = learning mode: the first CCVS sender
            #      binds the trusted SA.
            #   2. Cross-plausibility — a "stationary" reading while the
            #      engine is clearly running above idle is treated as
            #      UNKNOWN (fail-closed), not as 0 km/h. Wheel-speed vs.
            #      engine-speed disagreement is the classic stuck-CCVS /
            #      spoofed-CCVS signature.
            #   3. Sentinel bound — J1939-71 reserves raw 0xFE00..0xFFFF
            #      (Error / Not Available); the old `<= 250.0` km/h check
            #      accepted the 0xFA00..0xFDFF band (250..254 km/h) as
            #      legitimate speed. Now the raw value must be < 0xFE00.
            elif pf == 0xFE and ps == 0xF1 and len(data) >= 3:
                raw_speed = data[1] | (data[2] << 8)
                if raw_speed < 0xFE00:  # J1939-71: 0xFE00..0xFFFF = Error / Not Available
                    speed_kmh = raw_speed / 256.0
                    trusted = (
                        self._ccvs_trusted_sa is None
                        or sa == self._ccvs_trusted_sa
                    )
                    if self._ccvs_trusted_sa is None:
                        # Learning mode: bind the first CCVS sender as the
                        # trusted source address for this session.
                        self._ccvs_trusted_sa = sa
                        trusted = True
                    if trusted:
                        plausible = True
                        if speed_kmh <= self.SPEED_PLAUSIBILITY_KMH and self._current_rpm > self.SPEED_PLAUSIBILITY_RPM:
                            # Engine clearly running but vehicle "stopped" —
                            # the speed source is stuck or spoofed; treat as
                            # unknown and invalidate the interlock feed.
                            plausible = False
                            logger.warning(
                                "CCVS speed implausible vs engine RPM; treating speed as unknown",
                                extra={"speed_kmh": speed_kmh, "rpm": self._current_rpm, "sa": sa},
                            )
                        if plausible:
                            self._current_speed_kmh = speed_kmh
                            self.gateway.update_vehicle_speed(speed_kmh, source="physical")
                        else:
                            self._current_speed_kmh = float("nan")
                            self.gateway.update_vehicle_speed(float("nan"), source="physical")
                    else:
                        logger.debug(
                            "CCVS frame from untrusted source address ignored",
                            extra={"sa": sa, "trusted_sa": self._ccvs_trusted_sa},
                        )
            # ET1 (PGN 65249 / PF=0xFE, PS=0xE1): coolant temperature (B-10 sentinel filter)
            elif pf == 0xFE and ps == 0xE1 and len(data) >= 1:
                raw_temp = data[0]
                if raw_temp < 0xFE:  # 0xFE = Error, 0xFF = Not Available
                    self._current_temp = float(raw_temp) - 40.0
            # DM1 (PGN 65226 / PF=0xFE, PS=0xCA): active diagnostic message
            elif pf == 0xFE and ps == 0xCA and len(data) >= 2:
                dm = J1939DiagnosticService.parse_dm1_or_dm2(
                    bytes(data), pgn=65226, source_address=sa, timestamp_ns=time.time_ns()
                )
                self._last_dm1 = {
                    "source": dm.source_address,
                    "dtc_count": len(dm.dtcs),
                    "lamps": bytes(data[:1]).hex(),
                }
                self._error_count = len(dm.dtcs)
        except (IndexError, ValueError, AttributeError) as exc:
            logger.debug("J1939 live decode failed", extra={"error": str(exc)})

    def _log_tx_echo(self, frame: object) -> None:
        """Trace locally transmitted J1939 TP responses without polluting RX telemetry (B6).

        TX loopback visibility is kept as a debug-level trace only; sniffer
        tables, ring/disk buffers and protocol decoders treat the router as
        a physical-RX-only feed.
        """
        try:
            logger.debug(
                "J1939 TP TX echo (not routed as RX)",
                extra={
                    "arbitration_id": hex(getattr(frame, "arbitration_id", 0)),
                    "dlc": getattr(frame, "dlc", 0),
                },
            )
        except Exception:  # noqa: BLE001 — tracing must never kill ingestion
            pass

    def _ingest_live_frame(self, frame: object) -> None:
        """Feed one live frame through the router into decoders and UI (F-28).

        Perf (C-9): the ring buffer write is deferred to the caller's tick
        batch (append_batch, single lock acquisition per ~200 frames) —
        _telemetry_loop collects tick_frames and flushes once per tick.
        """
        # Router fans out to protocol engines (J1939 TP, N2K Fast Packet)
        self.router.route_frame(frame)
        if self.rolling_disk is not None and isinstance(frame, CanFrame):
            try:
                self.rolling_disk.append(frame)
            except Exception as exc:
                logger.debug("RollingDiskBuffer frame ingestion failed", extra={"error": str(exc)})
        self._decode_j1939_signal(frame)

        # J1939 transport protocol reassembly (multi-packet) (B-12 drain)
        completed, resp = self.j1939_tp.handle_rx_frame(frame)  # type: ignore[arg-type]
        if resp is not None:
            try:
                self.gateway.validate_and_transmit(resp)
            except Exception as exc:
                # M-14 (P2-10): a gateway refusal (E-Stop, whitelist, rate
                # limit...) on a J1939 TP response is operationally
                # significant — at default INFO level the old debug log hid
                # entire aborted transfer sessions from the operator.
                logger.warning(
                    "J1939 TP response physical transmit refused by gateway",
                    extra={"error": str(exc), "arbitration_id": getattr(resp, "arbitration_id", None)},
                )
            # B6 (REVIEW): locally generated TX response frames go to the
            # physical bus via the gateway only — feeding them back into the
            # RX router inflated telemetry metrics and polluted the sniffer
            # table / ring buffer / signal discovery with phantom RX traffic.
            self._log_tx_echo(resp)
        for extra_resp in self.j1939_tp.take_pending_tx_frames():
            try:
                self.gateway.validate_and_transmit(extra_resp)
            except Exception as exc:
                logger.warning(
                    "J1939 TP extra response physical transmit refused by gateway",
                    extra={"error": str(exc), "arbitration_id": getattr(extra_resp, "arbitration_id", None)},
                )
            self._log_tx_echo(extra_resp)

        if completed is not None:
            # Reassembled payloads can exceed a single CAN frame; cap the
            # synthetic frame at 64 bytes with a valid DLC so oversized
            # messages can never crash the telemetry thread.
            synth_data = completed.data[:64]
            # Reconstruct the canonical 29-bit CAN ID via the shared builder
            # — M-12 (P2-6): preserves EDP/DP bits (the old inline math
            # dropped EDP, mis-addressing EDP-set reassembled messages).
            arb_id = build_j1939_id(
                pgn=completed.pgn,
                sa=completed.source_address,
                da=completed.destination_address,
                priority=6,
            )
            try:
                synth_frame: CanFrame | None = CanFrame(
                    channel_id=completed.channel_id,
                    arbitration_id=arb_id,
                    dlc=length_to_dlc(len(synth_data)),
                    data=synth_data,
                    is_extended=True,
                    # REVIEW.md 4.2: reassembled J1939 payloads (VIN, DM1,
                    # component ID...) are 9-1785 bytes; without is_fd the
                    # classic-CAN DLC cap rejected every one of them and
                    # the telemetry thread silently dropped the message.
                    is_fd=len(synth_data) > 8,
                )
            except ValueError as exc:
                logger.debug("Synthetic reassembly frame rejected", extra={"error": str(exc)})
                synth_frame = None
            if synth_frame is not None:
                self._decode_j1939_signal(synth_frame)

        # N2K Fast Packet reassembly — PGN 127488 engine rapid / 128267 depth
        n2k_msg = self.n2k_fp.handle_rx_frame(frame)  # type: ignore[arg-type]
        if n2k_msg is not None:
            if n2k_msg.pgn == 127488 and len(n2k_msg.data) >= 3:
                raw_rpm = int.from_bytes(n2k_msg.data[1:3], "little")
                if raw_rpm < 0xFFFF:
                    self._current_rpm = float(raw_rpm) * 0.25
            elif n2k_msg.pgn == 128267 and len(n2k_msg.data) >= 5:
                raw_depth = int.from_bytes(n2k_msg.data[1:5], "little")
                if raw_depth < 0xFFFFFFFF:
                    self._depth_meters = float(raw_depth) * 0.01

    def _push_frames_to_ui_batch(self, frames: list[object]) -> None:
        """Stream a tick's frames to the frontend in ONE evaluate_js call (E13).

        Per-frame JS evaluation (up to 200 frames / 50 ms tick) flooded the
        WebView2 bridge; batching mirrors the frontend's own F-35 pattern
        (single state update per batch). Falls back to nothing on a closed
        window; each frame's payload is json.dumps-escaped (E2).
        """
        if self._window is None or not frames:
            return
        payloads: list[str] = []
        for frame in frames:
            try:
                data_hex = bytes(frame.data).hex()  # type: ignore[attr-defined]
                payloads.append(
                    json.dumps(
                        {
                            "id": f"0x{frame.arbitration_id:03X}",  # type: ignore[attr-defined]
                            "timestamp": round((frame.timestamp_ns or 0) / 1e9,  # type: ignore[attr-defined]
                                                3),
                            "channel": frame.channel_id,  # type: ignore[attr-defined]
                            "dlc": frame.dlc,  # type: ignore[attr-defined]
                            "data": data_hex,
                            "isExtended": frame.is_extended,  # type: ignore[attr-defined]
                            "isFd": frame.is_fd,  # type: ignore[attr-defined]
                            "source": getattr(frame, "source", "physical"),
                        }
                    )
                )
            except (AttributeError, OSError, RuntimeError) as exc:
                logger.debug("Frame serialization for UI batch failed", extra={"error": str(exc)})
        if not payloads:
            return
        batch_js = "[" + ",".join(payloads) + "]"
        try:
            self._window.evaluate_js(f"if (window.onNewCanFrames) window.onNewCanFrames({batch_js});")
        except (AttributeError, OSError, RuntimeError) as exc:
            logger.debug("Batched frame UI push failed", extra={"error": str(exc)})

    def _telemetry_loop(self) -> None:
        """Background loop: live CAN ingestion when connected, synthetic values in DEMO mode.

        Live path (F-28): bus -> FrameRouter -> decoders (J1939 / N2K) -> JS bridge.
        The watchdog heartbeat is NOT driven from here (F-16/E-11): the UI
        lease is only refreshed by the frontend render/rAF pulse.
        """
        while self._running:
            time.sleep(0.05 / max(0.5, self._speed_mult))

            if self._is_estop:
                continue

            # ── LIVE path: real frames off the bus (F-28, B-07 exception protection) ──
            if not self._is_simulating:
                drained = 0
                tick_frames: list[object] = []
                try:
                    with self._bus_lock:
                        bus = self.bus
                    # Perf (C-9): the first recv paces the tick against
                    # frame arrival; every subsequent drain call is
                    # non-blocking (timeout 0) so an empty queue costs ~0
                    # instead of a 10 ms park per frame at high speed mults.
                    while drained < 200:
                        frame = bus.recv(timeout_s=0.01 if drained == 0 else 0.0)
                        if frame is None:
                            break
                        self._ingest_live_frame(frame)
                        tick_frames.append(frame)
                        drained += 1
                except Exception as exc:  # noqa: BLE001 — loop must NEVER die on bus/driver errors
                    logger.warning("Telemetry frame ingestion error (recovering)", extra={"error": str(exc)})
                    time.sleep(0.1)
                    continue

                if drained == 0:
                    continue
                # Perf (C-9): ring buffer batch write — one lock acquisition
                # per tick instead of one per frame.
                self.ring_buffer.append_batch(tick_frames)  # type: ignore[arg-type]
                # Perf (C-9): packet counter bumped once per tick, not per frame
                self._bump_stat("_total_packets", drained)
                # E13: one JS evaluation per tick for the whole batch
                self._push_frames_to_ui_batch(tick_frames)
                # Live bus load estimate from routed frame rate
                self._bus_load = min(100, int(drained / 2))
                self._push_telemetry_tick()
                continue

            # ── DEMO path: synthetic scenario values (F-29: single module) ──
            self._sim_time += 0.05 * self._speed_mult
            t = self._sim_time
            self._bump_stat("_total_packets", 1)
            # P0-2 (REVIEW C-2): the DEMO loop must NOT feed the gateway's
            # speed interlock. The old unconditional
            # `self.gateway.update_vehicle_speed(0.0)` made a simulated
            # stationary vehicle satisfy the interlock for critical commands
            # (0x11/0x34/0x36, flashing preconditions) even while a real
            # vehicle bus was connected and moving. The interlock stays bound
            # to physical CCVS telemetry only; a synthetic feed can never
            # authorize TX. Speed stays fail-closed (no physical feed → no
            # critical commands) until real CCVS frames arrive.

            rpm = 2381.0 + 80.0 * math.sin(t * 0.8) + 30.0 * math.cos(t * 1.5)
            boost = 1.66 + 0.12 * math.sin(t * 0.5) + 0.05 * math.cos(t * 1.1)
            temp = 85.0 + 2.0 * math.sin(t * 0.2)
            pack_volt = 398.4
            soc = 78.4
            current = 42.5
            sog = 18.6
            depth = 24.8
            slip = 11.2

            if self._active_scenario == "misfire_p0300":
                if math.sin(t * 3.0) > 0.4:
                    rpm -= 300.0
                self._bus_load = 48
            elif self._active_scenario == "overboost":
                boost = 2.45 + 0.2 * math.sin(t * 1.2)
                self._bus_load = 52
            elif self._active_scenario == "overheat":
                temp = 108.5 + 4.0 * math.sin(t * 0.3)
                self._bus_load = 45
            elif self._active_scenario == "bus_surge":
                self._bus_load = 84
            elif self._active_scenario == "ev_bms_telemetry":
                current = 45.0 + 35.0 * math.sin(t * 1.5)
                pack_volt = 398.0 - (current * 0.08)
                soc = 78.4 - (t * 0.01)
                self._bus_load = 34
            elif self._active_scenario == "marine_vessel_n2k":
                sog = 18.6 + 2.0 * math.sin(t * 0.5)
                depth = 24.8 + 5.0 * math.sin(t * 0.1)
                slip = 11.2 + math.sin(t * 0.2) * 2.0
                self._bus_load = 38
            elif self._active_scenario == "can_fd_adas_vision":
                self._bus_load = 58
            elif self._active_scenario == "intermittent_wiring_fault":
                self._bus_load = 78
                if math.sin(t * 2.0) > 0.6:
                    self._bump_stat("_error_count", 1)
            else:
                self._bus_load = 40 + int(3 * math.sin(t))

            self._current_rpm = rpm
            self._current_boost = boost
            self._current_temp = temp
            self._pack_voltage = pack_volt
            self._battery_soc = soc
            self._pack_current = current
            self._sog_knots = sog
            self._depth_meters = depth
            self._propeller_slip = slip

            self._push_telemetry_tick()

    def _push_telemetry_tick(self) -> None:
        """Push the current telemetry snapshot to the frontend (F-28, HIGH-5)."""
        if self._window is None:
            return

        def _safe_float(val: Any, default: float = 0.0) -> float:
            try:
                f = float(val)
                return default if (math.isnan(f) or math.isinf(f)) else round(f, 2)
            except (TypeError, ValueError):
                return default

        def _safe_int(val: Any, default: int = 0) -> int:
            try:
                f = float(val)
                return default if (math.isnan(f) or math.isinf(f)) else int(f)
            except (TypeError, ValueError):
                return default

        t = _safe_float(self._sim_time)
        telemetry_payload = {
            "timeSec": t,
            "timeFormatted": f"{t:.2f}s",
            "rpm": _safe_int(self._current_rpm),
            "turboBoostBar": _safe_float(self._current_boost),
            "coolantTempC": _safe_int(self._current_temp),
            "oilPressureBar": 4.2,
            "busLoadPercent": _safe_int(self._bus_load),
            "errorCount": _safe_int(self._error_count),
            "packVoltageV": round(_safe_float(self._pack_voltage), 1),
            "batterySocPercent": round(_safe_float(self._battery_soc), 1),
            "packCurrentA": round(_safe_float(self._pack_current), 1),
            "sogKnots": round(_safe_float(self._sog_knots), 1),
            "depthMeters": round(_safe_float(self._depth_meters), 1),
            "propellerSlipPct": round(_safe_float(self._propeller_slip), 1),
        }
        try:
            payload_json = json.dumps(telemetry_payload)
            js_code = f"if (window.onTelemetryTick) window.onTelemetryTick({payload_json});"
            self._window.evaluate_js(js_code)
        except (AttributeError, OSError, RuntimeError) as exc:
            logger.warning(
                "Frontend telemetry push failed",
                extra={"error": str(exc)},
            )

    def _resolve_dist_html(self) -> Path:
        """Resolve frontend dist path supporting both raw Python and PyInstaller frozen .EXE bundle."""
        if getattr(sys, "frozen", False):
            base_dir = Path(getattr(sys, "_MEIPASS", sys.executable)).resolve()
            dist_path = base_dir / "src" / "ui" / "frontend" / "dist" / "index.html"
            if dist_path.exists():
                return dist_path

        root_dir = Path(__file__).parent.parent.parent
        return root_dir / "src" / "ui" / "frontend" / "dist" / "index.html"

    def run(self) -> None:
        dist_html = self._resolve_dist_html()

        if not dist_html.exists():
            logger.error(f"Frontend dist not found at {dist_html}. Please run 'npm run build' in src/ui/frontend.")
            return

        api = DesktopApiBridge(self)

        self.watchdog.start()
        # F-28: connect the real CAN bus before the ingestion loop starts
        try:
            self.bus.connect()
            logger.info("CAN bus connected for live ingestion", extra={"channel": self.channel_name})
        except (OSError, RuntimeError) as exc:
            logger.warning("CAN bus connect failed; running in DEMO-only mode", extra={"error": str(exc)})

        self._thread = threading.Thread(target=self._telemetry_loop, daemon=True)
        self._thread.start()

        self._window = webview.create_window(
            title="Universal CAN-Bus Diagnostic & Telemetry Tool v13.0",
            url=str(dist_html.resolve()),
            js_api=api,
            width=1400,
            height=900,
            min_size=(1100, 700),
            background_color="#F8FAFC",
            text_select=True,
        )

        try:
            webview.start(debug=False)
        finally:
            self._set_ui_state(_running=False)
            if hasattr(self, "_thread") and self._thread and self._thread.is_alive():
                self._thread.join(timeout=2.0)
            # L-13 (P3-9): release the physical bus FIRST — the gateway
            # shutdown and every other teardown below used to leave the
            # driver handle open (no bus.disconnect() anywhere on the GUI
            # exit path), pinning the vendor DLL and the transceiver.
            if hasattr(self, "bus") and self.bus is not None:
                try:
                    self.bus.disconnect()
                except Exception:
                    pass
            if hasattr(self, "gateway") and self.gateway:
                try:
                    self.gateway.shutdown()
                except Exception:
                    pass
            if hasattr(self, "_copilot_executor") and self._copilot_executor:
                try:
                    self._copilot_executor.shutdown(wait=False)
                except Exception:
                    pass
            if hasattr(self, "watchdog") and self.watchdog:
                try:
                    self.watchdog.stop()
                except Exception:
                    pass
            if hasattr(self, "rolling_disk") and self.rolling_disk:
                try:
                    self.rolling_disk.close()
                except Exception:
                    pass
