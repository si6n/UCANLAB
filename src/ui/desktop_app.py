"""Native Windows Desktop WebView2 Application Bridge for Universal CAN-Bus Diagnostic v13.0."""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import json
import math
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
import uuid
from collections import deque
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import webview
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.errors import HardwareError, SafetyError, SecurityError
from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame, length_to_dlc
from src.core.models.diagnostics import (
    DiagnosticDomain,
    DiagnosticEvent,
    SignalSample,
    SignalSource,
    VehicleSession,
)
from src.engine.ai.diagnostic_copilot import AiDiagnosticCopilot
from src.engine.buffer.ring_buffer import BinaryRingBuffer
from src.engine.buffer.rolling_disk import RollingDiskBuffer
from src.engine.discovery.engine import SignalDiscoveryEngine
from src.engine.pipeline.reassembly_pipeline import j1939_protocol_response_masks
from src.engine.router import FrameRouter
from src.hal.base import BusState
from src.hal.drivers.pcan_kvaser import PythonCanBus
from src.hal.replay.player import ReplayBus
from src.hal.replay.safety_filter import ReplaySafetyFilter
from src.protocols.j1939.diagnostics import J1939DiagnosticService
from src.protocols.j1939.oem.registry import OemJ1939Registry
from src.protocols.j1939.pgn import build_j1939_id, parse_j1939_id
from src.protocols.j1939.transport import J1939TransportProtocol
from src.protocols.nmea2000.fast_packet import Nmea2000FastPacketDecoder
from src.protocols.nmea2000.pgn_library import PGN_ENGINE_DYNAMIC, Nmea2000PgnDecoder
from src.protocols.uds.client import UdsClient
from src.protocols.uds.flasher import (
    EcuFlashingEngine,
    FlashingConfig,
    FlashingProgress,
    FlashingStep,
)
from src.protocols.uds.services import DiagnosticSessionType
from src.safety.estop import EmergencyStopSystem, EStopTriggerSource
from src.safety.gateway import TxSafetyGateway
from src.safety.multiplexer import SafeMultiplexedBus
from src.safety.secret_provider import get_default_secret_provider
from src.safety.state_machine import SafetyState, SafetySupervisor
from src.safety.watchdog import TxWatchdogSupervisor
from src.security.cloud.client import CANONICAL_CLOUD_HOSTS, CloudClient, CloudConfig
from src.security.cloud.license_flow import LicenseFlow
from src.security.cloud.telemetry_uploader import TelemetryUploader, UploadProgress
from src.security.hwid.collector import generate_hardware_fingerprint

logger = get_logger("app.desktop")

# T56-B / A3-1: DiagnosticEvent carries SPN/FMI only inside its text code
# ("SPN <n> FMI <m>"); the copilot's J1939 KB path needs the numeric fields,
# so the bridge re-derives them here. Anchored on the SPN token so an FMI-less
# code still yields its SPN, and a non-SPN code yields no match at all.
_SPN_FMI_RE = re.compile(r"SPN\s+(\d+)(?:\s+FMI\s+(\d+))?", re.I)


@dataclass(frozen=True)
class DiagnosticChallenge:
    """Short-lived (≤30s) single-use challenge token for dual-confirmation actions."""

    token: str
    action_type: str
    action_id: str
    created_at_monotonic_ns: int
    max_age_ns: int = 30_000_000_000  # 30 seconds
    params_hash: str = ""

DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64 = "eX3vJQWpo/pKrkpi5Y+f7m5ooUCRbCyY201DTnAjz/Q="

# P3 (G-3): secret names + entropy for the production TX-authorization gates.
# Derived through the platform SecretProvider (DPAPI on Windows, AES-GCM+0600
# on POSIX) and created on first run when absent. The values never leave the
# Python process: the token MINTING entry points are deliberately NOT exposed
# on the bridge (see `request_diagnostic_challenge`).
_ARM_AUTH_SECRET_BYTES = 32
_GATEWAY_CONFIRM_SECRET_BYTES = 32
# The confirmation token binds to a canonical arbitration id — the token is an
# authorization proof, not a per-ID transmission permission, so one stable ID
# is used and the real per-frame ID is enforced by the gateway whitelist.
_DIAGNOSTIC_CONFIRM_ARB_ID = 0x7E0

# B7 (REVIEW): production cloud endpoint. Override with UCANLAB_CLOUD_BASE_URL
# (any HTTPS URL) or switch to the local dev server with UCANLAB_CLOUD_DEV=1.
DEFAULT_CLOUD_BASE_URL = "https://ucan-cloud.si6n.io"
_DEV_CLOUD_BASE_URL = "http://127.0.0.1:8000"


def _resolve_cloud_base_url() -> str:
    """Resolve the cloud base URL from the environment (build/deploy-time config)."""
    is_frozen = getattr(sys, "frozen", False)
    is_prod = is_frozen or os.environ.get("UCANLAB_ENV", "").strip().lower() in ("production", "prod")

    dev_override = str(os.environ.get("UCANLAB_CLOUD_DEV", "")).strip().lower()
    if dev_override in ("1", "true", "yes"):
        if is_prod:
            raise SecurityError(
                "UCANLAB_CLOUD_DEV dev override rejected in production/frozen mode (fail-closed)",
                code="CLOUD_DEV_OVERRIDE_FORBIDDEN",
            )
        return _DEV_CLOUD_BASE_URL

    explicit = str(os.environ.get("UCANLAB_CLOUD_BASE_URL", "")).strip()
    if explicit:
        parsed = urllib.parse.urlsplit(explicit)
        hostname = (parsed.hostname or "").lower()
        if parsed.username or parsed.password:
            raise SecurityError(
                "Userinfo in cloud base URL is forbidden",
                code="CLOUD_USERINFO_FORBIDDEN",
                details={"url": explicit},
            )
        if is_prod and hostname not in CANONICAL_CLOUD_HOSTS:
            raise SecurityError(
                f"Unknown cloud host override {hostname!r} rejected in production/frozen mode (fail-closed)",
                code="CLOUD_UNTRUSTED_HOST_OVERRIDE",
                details={"url": explicit, "hostname": hostname},
            )
        if not hostname:
            raise SecurityError(
                f"Malformed cloud base URL {explicit!r} without hostname",
                code="CLOUD_MALFORMED_URL",
            )
        return explicit
    return DEFAULT_CLOUD_BASE_URL


def _resource_root() -> Path:
    """Resolve the read-only BUNDLE/resource root (frozen) or repo root (source).

    D3 (REVIEW Aşama 2): this is the directory that holds bundled, read-only
    assets (the frontend ``dist/``, DBCs, catalogs). In a PyInstaller onefile
    build it is ``sys._MEIPASS`` — a RUNTIME EXTRACTION directory that the
    bootloader DELETES on exit. It must never be used as a writable location.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    return Path(__file__).resolve().parents[2]


def _app_data_root() -> Path:
    """Resolve the application's PERSISTENT writable data root.

    D3 (REVIEW Aşama 2): the frozen branch previously returned
    ``Path(sys._MEIPASS).resolve().parent``. For a onefile build ``_MEIPASS``
    is the runtime extraction directory, which is destroyed when the process
    exits — so every artefact written under it was silently lost on restart.
    The damage was not limited to logs: the LICENSE ANTI-ROLLBACK HWM
    (``logs/license_hwm.txt``) also lived here, so a frozen build lost its
    clock anchor each run and, now that the HWM loader fails closed, hit
    ``LicenseError(HWM_UNAVAILABLE)`` on the next launch. The upload
    allowlist, blackbox ring and exports had the same durability problem.

    The writable root is therefore the OS-standard per-user data directory:
      * Windows: ``%LOCALAPPDATA%\\UniversalCAN``
      * macOS:   ``~/Library/Application Support/UniversalCAN``
      * Linux:   ``$XDG_STATE_HOME/universal_can`` (or ``~/.local/state/...``)

    A raw-Python run keeps anchoring to the repository root so developer
    workflows (and the test-suite fixtures) stay unchanged.
    """
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parents[2]

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return (root / "UniversalCAN").resolve()
    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Application Support" / "UniversalCAN").resolve()
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return (base / "universal_can").resolve()


def _ensure_app_data_root() -> Path:
    """Return the writable data root, creating it (owner-only where possible).

    D3: the persistent root may not exist on first run (and the old
    `_MEIPASS`-parent location happened to exist already), so every writable
    sub-path must be created before use. On POSIX the directory is created
    with mode 0700 so the secret/HWM files inside are not world-readable.
    """
    root = _app_data_root()
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            try:
                os.chmod(root, 0o700)
            except OSError:  # pragma: no cover - best-effort hardening
                logger.warning("Could not restrict data-root permissions", extra={"root": str(root)})
    return root


# D6 / A5-4 (REVIEW Aşama 2 & 5): bridge input bounds. Every JS-reachable
# method that takes free-form text must validate type, length and character
# class before the value reaches a protocol client, the filesystem, an HTTP
# body or the AI engine. Centralised so a new endpoint cannot silently omit
# them (mirrors the existing REPLAY_PATH_MAX_CHARS pattern).
BRIDGE_TEXT_MAX_CHARS: int = 512
BRIDGE_IDENTIFIER_MAX_CHARS: int = 256
#: Upper bound for a copilot prompt (the engine does O(n^2) fuzzy matching).
COPILOT_QUERY_MAX_CHARS: int = 8192
#: Bounds for a license reference / activation token string.
LICENSE_REF_MAX_CHARS: int = 2048


def _validate_bridge_text(
    value: object,
    *,
    field: str,
    max_chars: int,
    allow_empty: bool = False,
) -> str | None:
    """Return a human-readable problem string, or None when `value` is usable.

    Rejects non-string input, control characters other than tab/newline, and
    over-long values. Used by the WebView bridge so malformed renderer input
    becomes a controlled error rather than a raw TypeError deep in a protocol
    client (D6) or an unbounded workload in the analyzer (A5-4).
    """
    if value is None:
        return f"'{field}' is required" if not allow_empty else None
    if not isinstance(value, str):
        return f"'{field}' must be a string, got {type(value).__name__}"
    if len(value) > max_chars:
        return f"'{field}' exceeds the maximum length of {max_chars} characters"
    if not value.strip():
        return None if allow_empty else f"'{field}' must not be empty"
    # Reject control characters (except tab/newline which are benign in text).
    for ch in value:
        if ch in ("\t", "\n", "\r"):
            continue
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            return f"'{field}' contains a disallowed control character"
    return None


class DesktopApiBridge:
    """Bidirectional API bridge exposed to React JavaScript window via window.pywebview.api."""

    VALID_SCENARIOS: ClassVar[frozenset[str]] = frozenset({
        "nominal", "misfire_p0300", "overboost", "overheat", "bus_surge",
        "ev_bms_telemetry", "marine_vessel_n2k", "j1939_multi_ecu_fleet",
        "can_fd_adas_vision", "intermittent_wiring_fault"
    })
    VALID_FAULTS: ClassVar[frozenset[str]] = frozenset({"error_frame", "wiring_dropout"})

    # R2-U1: bridge risk manifest. Every JS-reachable method is classified so
    # a new endpoint defaults to scrutiny, not silent exposure:
    #   "safety" — TX/E-Stop/flash authority (fail-closed token/interlock gates)
    #   "data"   — export/upload (allowlist + size/rate guards)
    #   "read"   — queries (low risk) | "config" — settings (validated)
    # Methods missing here are flagged by test_bridge_manifest_covers_all_methods.
    BRIDGE_RISK_MANIFEST: ClassVar[dict[str, str]] = {
        "trigger_estop": "safety",
        "estop_request_challenge": "safety",
        "estop_submit_reset_token": "safety",
        "arm_tx": "safety",
        "disarm_tx": "safety",
        "flash_start": "safety",
        "flash_progress": "safety",
        "flash_cancel": "safety",
        "request_diagnostic_challenge": "safety",
        "execute_diagnostic_action": "safety",
        "heartbeat": "safety",
        "inject_fault": "safety",
        "replay_load": "safety",
        "replay_start": "safety",
        "replay_stop": "safety",
        "set_simulation_speed": "safety",
        "export_logs": "data",
        "export_session_report": "data",
        "cloud_upload_session": "data",
        "cloud_upload_raw_content": "data",
        "discovery_export_dbc": "data",
        "record_operator_measurement": "data",
        "record_operator_answer": "data",
        "record_technician_feedback": "data",
        "get_dialogue_state": "read",
        "ask_copilot": "read",
        "cloud_activate_license": "config",
        "cloud_get_status": "read",
        "cloud_register_device": "config",
        "cloud_save_config": "config",
        "cloud_test_connection": "read",
        "discovery_analyze_all": "read",
        "discovery_analyze_id": "read",
        "discovery_clear": "config",
        "discovery_get_summary": "read",
        "get_action_triggers": "read",
        "get_bus_traffic_status": "read",
        "get_diagnostic_analysis": "read",
        "get_diagnostic_db_metrics": "read",
        "get_diagnostic_kpi_metrics": "read",
        "get_dtc_info": "read",
        "get_safety_state": "read",
        "get_session_evidence_summary": "read",
        "oem_list_decoders": "read",
        "reset_diagnostic_session": "safety",
        "save_settings": "config",
        "search_nhtsa_recalls": "read",
        "select_scenario": "config",
        "toggle_simulator": "config",
        "window_minimize": "read",
        "window_maximize": "read",
        "window_close": "read",
    }

    def __init__(self, app: UniversalCanDesktopApp) -> None:
        self.app = app
        # H-2: the bridge is the UI-reachable heartbeat entry point. It adopts
        # the composition root's shared heartbeat token (or mints one if the
        # app did not arm it) so the 800 ms liveness interlock is driven by an
        # identified channel — not an anonymous stream. The token never
        # crosses the JS boundary.
        import secrets as _secrets

        self._heartbeat_token = getattr(app, "_heartbeat_token", None) or _secrets.token_hex(32)
        watchdog = getattr(app, "watchdog", None)
        if watchdog is not None and hasattr(watchdog, "arm_heartbeat_token"):
            watchdog.arm_heartbeat_token(self._heartbeat_token)
        if getattr(app, "_heartbeat_token", None) is None:
            try:
                app._heartbeat_token = self._heartbeat_token
            except Exception:  # noqa: BLE001 — duck-typed test doubles may be read-only
                pass

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
        # A5-4 (REVIEW Aşama 5): the WebView input had no upper bound and no
        # type check, so an oversized prompt pushed the deterministic engine
        # through normalisation + tokenisation + Levenshtein + rule matching
        # with unbounded CPU/memory. A non-str argument previously escaped as
        # a raw TypeError/AttributeError into the bridge. Bound it here and
        # return a structured error instead.
        problem = _validate_bridge_text(
            query, field="query", max_chars=COPILOT_QUERY_MAX_CHARS
        )
        if problem is not None:
            logger.warning("ask_copilot rejected input", extra={"reason": problem})
            return json.dumps(
                {"error": problem, "code": "INVALID_COPILOT_QUERY"},
                ensure_ascii=False,
            )
        return self.app.query_copilot(query)

    # ------------------------------------------------------------------
    # Diagnostic session bridge (FAZ 1/5/6) — TS side never re-implements
    # analysis (Bulgu 3); it consumes these payloads only.
    # ------------------------------------------------------------------
    def get_session_evidence_summary(self) -> dict[str, Any]:
        """Gate status + signal inventory for the Teşhis Oturumu panel."""
        return self.app.get_session_evidence_summary()

    def get_diagnostic_analysis(self) -> dict[str, Any]:
        """Full gate/anomaly/hypothesis/similarity analysis (FAZ 2..6)."""
        return self.app.get_diagnostic_analysis()

    def reset_diagnostic_session(self) -> dict[str, Any]:
        """Close + reopen the live evidence session."""
        self.app.reset_diagnostic_session()
        return self.app.get_session_evidence_summary()

    def record_operator_measurement(self, name: str, value: float) -> dict[str, Any]:
        """Record an operator chat measurement into the session (FAZ 5)."""
        return self.app.record_operator_measurement(name, value)

    def record_operator_answer(
        self,
        question_id: str,
        value: Any,
        kind: str = "yes_no",
        unit: str | None = None,
        is_unknown: bool = False,
    ) -> dict[str, Any]:
        """Record a structured operator answer into the interactive dialogue session (FAZ 1)."""
        return self.app.record_operator_answer(
            question_id=question_id,
            value=value,
            kind=kind,
            unit=unit,
            is_unknown=is_unknown,
        )

    def get_dialogue_state(self) -> dict[str, Any]:
        """Get current interactive dialogue session state and active question."""
        return self.app.get_dialogue_state()

    def get_diagnostic_kpi_metrics(self) -> dict[str, Any]:
        """Get live AI diagnostic quality metrics dashboard (FAZ 0)."""
        return self.app.get_diagnostic_kpi_metrics()

    def record_technician_feedback(self, dtc: str, resolved: bool, notes: str = "") -> dict[str, Any]:
        """Record technician resolution feedback ('çözdü/çözmedi/yanıldım') into local learning pool (FAZ 5)."""
        return self.app.record_technician_feedback(dtc=dtc, resolved=resolved, notes=notes)

    def export_session_report(self) -> dict[str, Any]:
        """Persist the technician report as .md under reports/ (FAZ 6)."""
        return self.app.export_session_report()

    def request_diagnostic_challenge(self, action: dict[str, Any]) -> dict[str, Any]:
        """Issue a short-lived (≤30s) single-use cryptographic confirmation token for a diagnostic action."""
        return self.app.request_diagnostic_challenge(action)

    def execute_diagnostic_action(
        self,
        action: dict[str, Any],
        confirmation_token: str | None = None,
        user_confirmed: bool = False,
    ) -> dict[str, Any]:
        """Execute actionable diagnostic routine requested by Copilot / Operator with challenge token verification."""
        res = self.app.execute_diagnostic_action(
            action,
            confirmation_token=confirmation_token,
            user_confirmed=user_confirmed,
        )
        if isinstance(res, dict):
            out = dict(res)
            is_ai = bool(isinstance(action, dict) and (action.get("is_ai_suggested") or action.get("source") == "ai_dialogue"))
            out["provenance"] = "AI önerdi / operatör onayladı" if is_ai else "Operatör başlattı"
            return out
        return res

    # ------------------------------------------------------------------
    # Signal Discovery & Reverse Engineering Bridge APIs
    # ------------------------------------------------------------------
    def discovery_get_summary(self) -> dict[str, Any]:
        """Get summary of discovered CAN arbitration IDs and traffic metrics."""
        return self.app.discovery_get_summary()

    def discovery_analyze_id(self, arb_id: int) -> dict[str, Any]:
        """Run statistical reverse engineering analysis on a specific arbitration ID."""
        return self.app.discovery_analyze_id(arb_id)

    def discovery_analyze_all(self) -> dict[str, Any]:
        """Run reverse engineering analysis across all discovered arbitration IDs."""
        return self.app.discovery_analyze_all()

    def discovery_export_dbc(self, approved_only: bool = False) -> dict[str, Any]:
        """Generate and export automated DBC database from discovered signals."""
        return self.app.discovery_export_dbc(approved_only=approved_only)

    def discovery_clear(self) -> dict[str, Any]:
        """Clear discovery buffer and cached hypothesis reports."""
        return self.app.discovery_clear()

    # ------------------------------------------------------------------
    # OEM Proprietary Protocol Bridge APIs
    # ------------------------------------------------------------------
    def oem_list_decoders(self) -> list[str]:
        """List active OEM proprietary J1939 decoders (Cummins, Cat, Scania, Volvo, Detroit, Actros)."""
        return self.app.oem_list_decoders()

    # ------------------------------------------------------------------
    # Deterministic Trace Replay Bridge APIs
    # ------------------------------------------------------------------
    def replay_load(self, file_path: str) -> dict[str, Any]:
        """Load CAN trace file (.asc, .csv, .blf) into ReplayBus engine.

        C-1 / O-5: this is a renderer-reachable IPC endpoint. The raw path
        used to go straight into ``Path.resolve()`` + ``open()``, so a
        hostile renderer could force an SMB/UNC connect (NTLM coercion) or
        reach any file the process can read. Replay files must now live
        under an app-owned root, and non-str/over-long input is refused
        before any OS call.
        """
        if not isinstance(file_path, str):
            logger.warning(
                "replay_load rejected: non-string path",
                extra={"type": type(file_path).__name__},
            )
            return {"success": False, "error": "Geçersiz trace yolu (string bekleniyor)."}
        if len(file_path) == 0 or len(file_path) > DesktopApiBridge.REPLAY_PATH_MAX_CHARS:
            logger.warning(
                "replay_load rejected: path length",
                extra={"length": len(file_path)},
            )
            return {
                "success": False,
                "error": (
                    f"Geçersiz trace yolu uzunluğu ({len(file_path)}); en fazla "
                    f"{DesktopApiBridge.REPLAY_PATH_MAX_CHARS} karakter olabilir."
                ),
            }
        # C-1: enforce the positive root allowlist at the IPC boundary too —
        # the renderer must never be able to reach the filesystem through
        # this method even if the app-layer guard were bypassed.
        try:
            DesktopApiBridge._validate_replay_path(file_path)
        except ValueError as exc:
            logger.warning("replay_load rejected by path policy", extra={"error": str(exc)})
            return {"success": False, "error": str(exc)}
        return self.app.load_replay(file_path)

    def replay_start(self, speed: float = 1.0, loop: bool = False) -> dict[str, Any]:
        """Start trace playback with high-precision timestamp delta replay."""
        return self.app.start_replay(speed=speed, loop=loop)

    def replay_stop(self) -> dict[str, Any]:
        """Stop active trace playback."""
        return self.app.stop_replay()

    # ------------------------------------------------------------------
    # UDS / ISO-TP ECU Flashing Bridge APIs
    # ------------------------------------------------------------------
    def flash_start(self, config: dict[str, Any], confirmation_token: str | None = None) -> dict[str, Any]:
        """Start UDS ECU reprogramming sequence with dual confirmation challenge verification."""
        return self.app.flash_start(config, confirmation_token=confirmation_token)

    def flash_progress(self) -> dict[str, Any]:
        """Get live step-by-step progress and status of ECU reprogramming."""
        return self.app.flash_progress()

    def flash_cancel(self) -> dict[str, Any]:
        """Safely abort active ECU reprogramming at the next protocol boundary."""
        return self.app.flash_cancel()

    def get_action_triggers(self, text: str) -> list[dict[str, Any]]:
        """Extract structured action triggers from response text or query."""
        from src.engine.ai.diagnostic_copilot import extract_action_triggers
        return extract_action_triggers(text)

    def get_bus_traffic_status(self) -> dict[str, Any]:
        """Get live traffic metrics, bus load, and detected anomalies."""
        return self.app.get_bus_traffic_snapshot()

    def get_dtc_info(self, code: str) -> dict[str, Any]:
        """Look up DTC code specifications directly from local knowledge base and procedure library."""
        from src.engine.ai.diagnostic_copilot import EXPERT_KNOWLEDGE_BASE
        from src.engine.ai.procedure_validator import get_procedure

        code_clean = (code or "").strip().upper()
        info = dict(EXPERT_KNOWLEDGE_BASE.get(code_clean, {}))
        proc = get_procedure(code_clean) or get_procedure(code_clean.replace(" ", "_"))
        if proc:
            info["procedure"] = proc.to_dict()
            if not info.get("steps") and proc.measurement_steps:
                info["steps"] = [[m.get("test_type", ""), m.get("target", ""), "Standard"] for m in proc.measurement_steps]
            if not info.get("subsystem"):
                info["subsystem"] = proc.system
            if not info.get("symptoms"):
                info["symptoms"] = list(proc.symptoms)
        return info

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
        """Periodic UI lease heartbeat to satisfy TX Watchdog.

        H-2: the pulse must carry the shared bridge<->watchdog token so the
        lease is extended by an identified caller, not an anonymous stream.
        """
        self.app.watchdog.heartbeat(self._heartbeat_token)
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
    def cloud_test_connection(
        self,
        url: str | None = None,
        session_token: str | None = None,
        session_override: str | None = None,
    ) -> dict[str, Any]:
        # Whitelist allowed hosts for cloud connection testing to prevent credential leakage
        allowed_domains = CANONICAL_CLOUD_HOSTS
        try:
            if url:
                parsed = urllib.parse.urlsplit(url)
                # L-19 (P3-17): hostname-less URLs (e.g. "https:///api")
                # previously skipped the allowlist entirely (`if parsed.hostname and ...`).
                # Fail closed: no resolvable host means no pass.
                if not parsed.hostname or parsed.hostname not in allowed_domains:
                    return {"success": False, "error": f"URL hedefi izin listesinde değil: {parsed.hostname or '<yok>'}"}
                self.app.cloud_client.set_base_url(url)
            resp = self.app.cloud_client.request("GET", "/health", health_endpoint=True)
            if resp.status == 200:
                user_info = None
                # D2 (REVIEW Aşama 2): the previous implementation SAVED the
                # candidate token into the shared secret vault, fired the
                # request, then RESTORED the old value. During that window a
                # concurrent request used the wrong session, and a crash left
                # the vault holding the candidate. The override is now passed
                # per-request (`session_token=`) and shared state is untouched.
                override = session_override if session_override is not None else session_token
                if override is not None and str(override).strip():
                    candidate = str(override).strip()
                    if len(candidate) > 4096:
                        return {"success": False, "error": "Oturum belirteci çok uzun"}
                    test_resp = self.app.cloud_client.request(
                        "GET", "/auth/me", session_token=candidate
                    )
                    if test_resp.status == 200:
                        user_info = test_resp.json_object()
                elif self.app.cloud_client.has_session_token():
                    test_resp = self.app.cloud_client.request("GET", "/auth/me")
                    if test_resp.status == 200:
                        user_info = test_resp.json_object()
                return {"success": True, "status": resp.status, "user": user_info}
            return {"success": False, "error": f"Sağlık kontrolü başarısız (HTTP {resp.status})"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def cloud_save_config(self, url: str, session_token: str | None = None) -> dict[str, Any]:
        allowed_domains = CANONICAL_CLOUD_HOSTS
        try:
            if url:
                parsed = urllib.parse.urlsplit(url)
                # L-19 (P3-17): hostname-less URLs (e.g. "https:///api")
                # previously skipped the allowlist entirely (`if parsed.hostname and ...`).
                # Fail closed: no resolvable host means no pass.
                if not parsed.hostname or parsed.hostname not in allowed_domains:
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
        # D6 (REVIEW Aşama 2): this bridge method previously forwarded
        # `device_name` straight into the cloud client with no type/length/
        # character checks, so a 1 MB string, a list, or an int reached the
        # HTTP layer. Validate at the boundary and return a controlled error.
        # A JS `null`/`undefined` is normalized back to the default rather
        # than forwarded as `None`.
        if device_name is None:
            device_name = "Desktop Diagnostic Tool"
        problem = _validate_bridge_text(
            device_name,
            field="device_name",
            max_chars=BRIDGE_IDENTIFIER_MAX_CHARS,
            allow_empty=True,
        )
        if problem is not None:
            return {"success": False, "error": problem, "code": "INVALID_DEVICE_NAME"}
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
        # D6 (REVIEW Aşama 2): validate the activation reference before it is
        # placed in the cloud request body.
        problem = _validate_bridge_text(
            license_ref, field="license_ref", max_chars=LICENSE_REF_MAX_CHARS
        )
        if problem is not None:
            return {"success": False, "error": problem, "code": "INVALID_LICENSE_REF"}
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

        # Extension gate first (cheap fail-fast before root resolution audit).
        ext = resolved.suffix.lower()
        if ext not in DesktopApiBridge.UPLOAD_EXTENSION_HINTS:
            raise ValueError(
                f"Izin verilmeyen dosya formati '{ext}'. Sadece telemetri ve log dosyalari yuklenebilir."
            )

        # Sensitive path/file check (F-3)
        sensitive_patterns = ("secret", ".dpapi", "credential", "password", ".env", "id_rsa")
        if any(p in str(resolved).lower() for p in sensitive_patterns) or resolved.suffix.lower() in (".dpapi", ".key", ".pem"):
            raise ValueError("Guvenlik politikasi: Hassas sistem dosyalari yuklenemez.")

        allowed_roots = DesktopApiBridge._allowed_upload_roots_resolved()
        # is_temp exception removed: OS temp is world-writable and let any
        # renderer reach other apps' sensitive files. Only allowed_roots.
        if not any(resolved.is_relative_to(root) for root in allowed_roots):
            raise ValueError(
                "Guvenlik politikasi: yalnizca uygulamanin kendi export/log/trace "
                "dizinlerindeki dosyalar yuklenebilir."
            )

        return resolved

    # Replay trace loading (C-1 / O-5). Traces are read-only inputs, so the
    # policy is *stricter* than the upload path: fixed app-owned roots plus
    # an extension keep-list, and every reserved device / UNC / extended
    # path shape is refused before it can reach the filesystem.
    REPLAY_PATH_MAX_CHARS: ClassVar[int] = 4096
    REPLAY_ROOTS: ClassVar[tuple[Path, ...]] = (
        Path("data") / "traces",
        Path("logs"),
        Path("exports"),
    )
    # Extension keep-list mirrors UPLOAD_EXTENSION_HINTS minus formats that
    # are never trace containers (.json/.log stay: ReplayBus parses .csv).
    REPLAY_EXTENSION_HINTS: ClassVar[frozenset[str]] = frozenset(
        {".mf4", ".mdf", ".bin", ".asc", ".blf", ".csv", ".json", ".log", ".zst"}
    )
    # Windows reserved device names (case-insensitive, with or without an
    # extension: ``CON`` and ``CON.asc`` both address the device).
    _RESERVED_DEVICE_NAMES: ClassVar[frozenset[str]] = frozenset(
        {"CON", "PRN", "AUX", "NUL"}
        | {f"COM{i}" for i in range(1, 10)}
        | {f"LPT{i}" for i in range(1, 10)}
    )
    # UNC prefix (``\\host`` / ``//host``), DOS device namespace (``\\.\``,
    # ``\\?\``, ``\??\``), and drive-relative forms (``C:``).
    _UNC_RE: ClassVar[re.Pattern[str]] = re.compile(r"^[\\/]{2}")
    _DEVICE_NS_RE: ClassVar[re.Pattern[str]] = re.compile(r"^[\\/]{2}[.?][\\/]")
    _DRIVE_RELATIVE_RE: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Za-z]:(?![\\/])")
    # NUL / newline / carriage-return: never valid in a trace path and used
    # to smuggle truncation or log injection.
    _REPLAY_CONTROL_RE: ClassVar[re.Pattern[str]] = re.compile(r"[\x00\n\r]")

    @classmethod
    def _replay_roots_resolved(cls) -> tuple[Path, ...]:
        root = _app_data_root().resolve()
        return tuple((root / rel).resolve() for rel in cls.REPLAY_ROOTS)

    @classmethod
    def _has_reserved_device_name(cls, raw: str) -> bool:
        """True if any path segment names a reserved DOS device."""
        for segment in re.split(r"[\\/]+", raw):
            if not segment:
                continue
            stem = segment.split(".", 1)[0].strip().upper()
            if stem in cls._RESERVED_DEVICE_NAMES:
                return True
        return False

    @classmethod
    def _validate_replay_path(cls, file_path: str) -> Path:
        """Validate a trace path from the IPC surface (C-1 / O-5).

        Fail-closed: reserved device names, UNC/device-namespace/relative
        drive shapes, non-files, disallowed extensions, and any path outside
        the app-owned replay roots are refused. Raises ``ValueError``; the
        caller converts it to ``{"success": False}`` without ever echoing a
        resolved location back to the renderer.
        """
        if not isinstance(file_path, str):
            raise ValueError("Geçersiz trace yolu (string bekleniyor).")

        raw = file_path.strip()
        if not raw:
            raise ValueError("Geçersiz trace yolu (boş).")
        if len(raw) > cls.REPLAY_PATH_MAX_CHARS:
            raise ValueError(
                f"Geçersiz trace yolu uzunluğu; en fazla {cls.REPLAY_PATH_MAX_CHARS} karakter olabilir."
            )
        if cls._REPLAY_CONTROL_RE.search(raw):
            raise ValueError("Geçersiz trace yolu (control karakteri).")

        # --- pre-resolve shape gates (no OS call performed) ----------------
        if cls._UNC_RE.match(raw) or cls._DEVICE_NS_RE.match(raw):
            raise ValueError("Güvenlik politikası: UNC/ağ yolları ve aygıt yolları reddedilir.")
        if cls._DRIVE_RELATIVE_RE.match(raw):
            raise ValueError("Güvenlik politikası: sürücü-göreli yollar reddedilir.")
        if cls._has_reserved_device_name(raw):
            raise ValueError("Güvenlik politikası: ayrılmış aygıt adları reddedilir.")

        resolved = Path(raw).resolve()

        # A resolved UNC/device shape can still appear after normalisation.
        if cls._UNC_RE.match(str(resolved)) or cls._DEVICE_NS_RE.match(str(resolved)):
            raise ValueError("Güvenlik politikası: UNC/ağ yolları ve aygıt yolları reddedilir.")

        if not resolved.is_file():
            raise ValueError(f"Dosya bulunamadı veya geçersiz: {raw}")

        ext = resolved.suffix.lower()
        if ext not in cls.REPLAY_EXTENSION_HINTS:
            raise ValueError(f"İzin verilmeyen trace dosya formatı '{ext}'.")

        if not any(resolved.is_relative_to(root) for root in cls._replay_roots_resolved()):
            raise ValueError(
                "Güvenlik politikası: yalnızca uygulamanın kendi veri/traces, "
                "logs ve exports dizinlerindeki iz kayıtları yüklenebilir."
            )
        return resolved

    def cloud_upload_session(
        self, file_path: str, vehicle_vin: str | None = None, user_consented: bool = False
    ) -> dict[str, Any]:
        try:
            safe_path = self._validate_telemetry_upload_path(file_path)
            logger.info(
                "Cloud telemetry upload accepted",
                # D5 (REVIEW Aşama 2): log only the file NAME, not the absolute
            # path — a Windows path such as
            # `C:\Users\<name>\...\<vehicle-project>\...` leaks the operator's
            # username and vehicle/project identity into log aggregation.
            extra={"file": safe_path.name, "bytes": safe_path.stat().st_size},
            )
            # R2-S3: VIN is forwarded only with explicit operator consent.
            result = self.app.telemetry_uploader.upload_file(
                file_path=safe_path, vehicle_vin=vehicle_vin, user_consented=user_consented
            )
            return {
                "success": True,
                "sessionId": result.session_id,
                "status": result.status,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # Raw-content upload guard: 1MB cap + 60 req/s simple token bucket.
    _RAW_UPLOAD_MAX_BYTES: ClassVar[int] = 1 * 1024 * 1024
    _RAW_UPLOAD_MAX_PER_SEC: ClassVar[int] = 60
    _raw_upload_window_start: ClassVar[float] = 0.0
    _raw_upload_count: ClassVar[int] = 0
    _raw_upload_lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def _check_raw_upload_rate(cls) -> bool:
        now = time.monotonic()
        with cls._raw_upload_lock:
            if now - cls._raw_upload_window_start >= 1.0:
                cls._raw_upload_window_start = now
                cls._raw_upload_count = 0
            cls._raw_upload_count += 1
            return cls._raw_upload_count <= cls._RAW_UPLOAD_MAX_PER_SEC

    def cloud_upload_raw_content(
        self,
        filename: str,
        content: str,
        vehicle_vin: str | None = None,
        user_consented: bool = False,
    ) -> dict[str, Any]:
        import os as _os
        try:
            if not self._check_raw_upload_rate():
                logger.warning("cloud_upload_raw_content rate-limited")
                return {"success": False, "error": "Hız limiti aşıldı (60/sn). Daha sonra tekrar deneyin."}
            raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
            if len(raw) > self._RAW_UPLOAD_MAX_BYTES:
                logger.warning("cloud_upload_raw_content rejected: oversize", extra={"bytes": len(raw)})
                return {"success": False, "error": "İçerik 1MB sınırını aşıyor."}
            # F-4: Sanitize filename to prevent directory traversal or alternate stream injection
            clean_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", Path(filename).name).strip("._")
            if not clean_name:
                clean_name = "telemetry_upload.bin"
            logger.info(
                "cloud_upload_raw_content accepted",
                extra={"upload_filename": clean_name, "bytes": len(raw)},
            )
            # Secure staging: O_CREAT|O_EXCL + 0o600 inside the app exports
            # tree (no world-readable system-temp file, no delete=False race).
            stage_dir = _app_data_root() / "exports" / ".upload_stage"
            try:
                stage_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return {"success": False, "error": str(exc)}
            tmp_path: Path | None = None
            for _ in range(5):
                candidate = stage_dir / f"raw_{int(time.monotonic_ns())}_{_os.getpid()}_{clean_name}"
                try:
                    fd = _os.open(str(candidate), _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY, 0o600)
                except FileExistsError:
                    continue
                try:
                    with _os.fdopen(fd, "wb") as fh:
                        fh.write(raw)
                except Exception:
                    try:
                        candidate.unlink(missing_ok=True)
                    except Exception:
                        pass
                    raise
                tmp_path = candidate
                break
            if tmp_path is None:
                return {"success": False, "error": "Geçici dosya oluşturulamadı."}
            try:
                result = self.app.telemetry_uploader.upload_file(
                    file_path=tmp_path, vehicle_vin=vehicle_vin, user_consented=user_consented
                )
                return {
                    "success": True,
                    "sessionId": result.session_id,
                    "status": result.status,
                }
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def window_minimize(self) -> dict[str, bool]:
        """Minimize the native desktop window."""
        if hasattr(self.app, "_window") and self.app._window:
            try:
                self.app._window.minimize()
                return {"success": True}
            except Exception:
                pass
        return {"success": False}

    def window_maximize(self) -> dict[str, bool]:
        """Toggle maximize / fullscreen for the native desktop window."""
        if hasattr(self.app, "_window") and self.app._window:
            try:
                self.app._window.toggle_fullscreen()
                return {"success": True}
            except Exception:
                pass
        return {"success": False}

    def window_close(self) -> dict[str, bool]:
        """Close/destroy the native desktop window."""
        if hasattr(self.app, "_window") and self.app._window:
            try:
                self.app._window.destroy()
                return {"success": True}
            except Exception:
                pass
        return {"success": False}


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
        self._secret_provider = get_default_secret_provider()
        # A5-2 (REVIEW Aşama 5): the provider's `protection_level()` was never
        # called, so an EPHEMERAL or FALLBACK_FILE backend — i.e. a silent
        # downgrade from DPAPI/AES-GCM — went unnoticed at startup. Surface it
        # explicitly so an operator (and log aggregation) can see the strength
        # of the secret store actually in use.
        self._secret_protection_level = self._report_secret_protection_level()
        # P3 (G-3): the composition root must WIRE the cryptographic gates the
        # T41/G-1/HMAC work added — otherwise those fail-closed paths are dead
        # code in production and Stage 5 degrades to a bare `user_confirmed`
        # boolean while `arm_tx` falls back to its legacy WARNING path. Both
        # secrets are derived from the SecretProvider (created on first run if
        # absent) and handed to the supervisor/gateway below.
        self._arm_auth_secret = self._derive_secret("ARM_AUTH_SECRET", _ARM_AUTH_SECRET_BYTES)
        self._gateway_confirm_secret = self._derive_secret(
            "GATEWAY_CONFIRM_SECRET", _GATEWAY_CONFIRM_SECRET_BYTES
        )
        # S1-P2-5: pass the SAME provider instance the rest of the app uses.
        # `EmergencyStopSystem()` with no argument calls
        # `get_default_secret_provider()` internally, minting a SECOND
        # provider object. With a persistent backend both instances happen to
        # share one file, but with `EphemeralSecretBackend` (or a provider-init
        # failure) they hold INDEPENDENT keys — so the external E-Stop reset
        # tool, which reads THIS provider, could not validate a token minted by
        # the E-Stop's own key ("invalid token", no visible cause).
        self.estop = EmergencyStopSystem(secret_provider=self._secret_provider)
        # P0-1 (REVIEW C-1): the desktop app no longer owns a minting
        # authority — an in-process EStopResetAuthority could mint a valid
        # reset token for any caller that reaches the object graph,
        # including the WebView. Reset is exclusively the challenge/
        # response flow (request challenge → external authorization →
        # submit token), so only the verification-only enforcement object
        # is wired onward.
        self.supervisor = SafetySupervisor(
            initial_state=SafetyState.STARTUP,
            estop=self.estop,
            auth_secret=self._arm_auth_secret,
        )
        self.watchdog = TxWatchdogSupervisor(supervisor=self.supervisor, estop=self.estop, timeout_ms=800.0)
        # H-2: arm the shared heartbeat token here so an in-process caller
        # (e.g. arm_tx's opportunistic lease refresh) can hold the lease with
        # an identified token. DesktopApiBridge adopts this same token when
        # the WebView bridge is created, re-arming the watchdog with it.
        self._heartbeat_token = secrets.token_hex(32)
        self.watchdog.arm_heartbeat_token(self._heartbeat_token)
        # REVIEW.md 1.1: the gateway previously started with NO whitelist,
        # so the fail-closed Stage 3 rejected every single frame — the app
        # could never transmit at all. Seed it with the legitimate diagnostic
        # surface: the OBD functional broadcast, the physical UDS REQUEST
        # ids (0x7E0..0x7E7 — the tester transmits requests; ECU response
        # families 0x7E8..0x7EF belong to the ECU side, not this tool), and
        # our J1939 response masks (TP.CM/TP.DT/ISO-TP frames sourced from
        # our tool address 0xF9).
        # REVIEW.md LOW-8: the response range (0x7E8..0x7EF) is no longer
        # statically whitelisted — a whitelisted ECU-reply family let any
        # bug that reached the gateway impersonate ECU responses on the
        # physical bus. If an ECU-simulation mode ever needs to transmit
        # them, it must grant the range explicitly at scenario entry.
        _diag_ids: set[int] = {0x7DF} | set(range(0x7E0, 0x7E8))
        self.gateway = TxSafetyGateway(
            bus=self.bus,
            estop=self.estop,
            supervisor=self.supervisor,
            watchdog=self.watchdog,
            whitelist_ids=_diag_ids,
            whitelist_masks=list(j1939_protocol_response_masks(0xF9)),
            # P3 (G-3): a configured confirmation secret makes the HMAC
            # ConfirmationToken the ONLY accepted Stage-5 proof (fail-closed).
            confirmation_secret=self._gateway_confirm_secret,
            # P9 (G-11): the J1939 response mask above is deliberately broad —
            # acknowledge that override explicitly rather than silently
            # accepting every masked ID.
            whitelist_superset_allowed=True,
        )
        self.copilot = AiDiagnosticCopilot()
        # F-32: copilot LLM calls run off the UI/bridge thread
        self._copilot_executor = concurrent.futures.ThreadPoolExecutor(
            # M-28 (P2-18): two workers so a wedged LLM request (uncancellable
            # urlopen) cannot starve every subsequent query behind the old
            # single-thread pool.
            max_workers=2, thread_name_prefix="copilot_query"
        )
        # M-28 (P2-18): track the in-flight future so a timeout CANCELS it
        # instead of leaving it to occupy a worker slot forever.
        self._copilot_inflight: concurrent.futures.Future[str] | None = None
        self._copilot_inflight_lock = threading.Lock()

        # Cloud Subsystem (Universal-CAN-Cloud)
        # B7 (REVIEW): production builds must target the real cloud endpoint.
        # The loopback dev server is opt-in via UCANLAB_CLOUD_DEV=1 so a
        # stock binary never silently loses license verification /
        # telemetry upload to a nonexistent localhost server (7-day offline
        # grace, then lockout).
        self._cloud_config = CloudConfig(base_url=_resolve_cloud_base_url())
        self.cloud_client = CloudClient(config=self._cloud_config, secret_provider=self._secret_provider)

        # REVIEW.md MEDIUM-6 / REVIEW2 #6: the HWID computation spawns 4-5
        # PowerShell/WMI subprocesses with 10 s timeouts EACH — a corrupted
        # WMI repository made the FIRST generate_hardware_fingerprint()
        # call block a pywebview JsApi thread for up to ~50 s (frozen
        # Settings/Cloud panel). Warm the lru_cache in a daemon thread at
        # construction so no user-facing bridge call ever pays the cold cost.
        self._hwid_warmup = threading.Thread(
            target=self._warm_hwid_cache,
            name="hwid_warmup",
            daemon=True,
        )
        self._hwid_warmup.start()

        try:
            pub_bytes = base64.b64decode(DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64)
            self._cloud_pubkey = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        except Exception:
            self._cloud_pubkey = None
        # M-19 (P2-13): persistent HWM — anti-rollback survives restarts.
        # D3: anchored to the PERSISTENT writable root (created on demand). In
        # a frozen onefile build the old `_MEIPASS`-derived path was deleted on
        # exit, so the anti-rollback anchor silently vanished every run — and
        # with the fail-closed HWM loader that makes the license path raise
        # HWM_UNAVAILABLE on the next launch.
        self.license_flow = (
            LicenseFlow(
                self.cloud_client,
                self._cloud_pubkey,
                hwm_path=_ensure_app_data_root() / "logs" / "license_hwm.txt",
            )
            if self._cloud_pubkey
            else None
        )
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

        # ── Composition Root Wiring: Central Protocol & Analysis Engines ──
        self._diagnostic_challenges: dict[str, DiagnosticChallenge] = {}
        self._challenges_lock = threading.Lock()
        self.discovery_engine = SignalDiscoveryEngine()
        self.oem_registry = OemJ1939Registry()
        self.replay_bus: ReplayBus | None = None
        self.replay_safety_filter: ReplaySafetyFilter | None = None
        self._replay_thread: threading.Thread | None = None
        self._replay_stop_event = threading.Event()
        self.flashing_engine: EcuFlashingEngine | None = None
        self._flash_lock = threading.Lock()
        self._flash_progress_state: dict[str, Any] = {
            "status": "idle",
            "percent": 0.0,
            "step": "IDLE",
            "step_index": 0,
            "bytes_transferred": 0,
            "total_bytes": 0,
            "speed_kbps": 0.0,
            "elapsed_s": 0.0,
            "logs": [],
            "error": None,
        }
        self._flash_thread: threading.Thread | None = None

        # ── Diagnostic evidence session (FAZ 1) ──
        # P1 model: immutable SignalSample/DiagnosticEvent records accumulate
        # in ONE VehicleSession per live bus session. The AI layer only ever
        # receives this container — evidence production stays here in the UI
        # host (plan §Mimari Kural). Sim path never appends (Bulgu 7).
        self._diag_session: VehicleSession | None = None
        self._dialogue_session: Any | None = None
        self._session_lock = threading.Lock()
        # Per-signal bounded evidence ring (O(1) append, RX hot-path safe).
        self._signal_rings: dict[str, deque] = {}
        self._open_diagnostic_session()

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
        # LINK-FAULT observers (Kontrol #21/#23/#44): consecutive interface
        # I/O-error streak + last-RX monotonic anchor for the silence check.
        self._hw_error_streak = 0
        self._last_rx_monotonic_ns = 0
        # Mirror ANY E-Stop engagement (UI button AND hardware link faults)
        # into the UI state so the frontend can never show "Connected/TX"
        # while the safety latch is engaged. Engagement-only: clearing stays
        # exclusively on the challenge/response reset flow (C-1/P0-1).
        self.estop.register_callback(self._sync_ui_mirror_on_estop)

    def _sync_ui_mirror_on_estop(self, event: object) -> None:
        """E-Stop engagement → UI mirror (link-fault AND operator paths).

        Runs on the E-Stop callback chain (outside all locks, exception-
        isolated by trigger()), so it only performs lock-guarded flag writes.
        """
        self._set_ui_state(_is_estop=True, _is_simulating=False, _bus_load=0)

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

    # ------------------------------------------------------------------
    # Diagnostic evidence session (FAZ 1..6) — the ONLY place evidence is
    # produced. AI receives the VehicleSession; sim data never enters
    # (Bulgu 7 / plan §Mimari Kural).
    # ------------------------------------------------------------------
    _EVIDENCE_RING_MAX: ClassVar[int] = 500  # per-signal sample cap (plan §Riskler)

    def _domain_for_current_bus(self) -> DiagnosticDomain:
        """Map the active profile/sources to a session domain (plan FAZ 1)."""
        iface = (self.interface_val or "").lower()
        if "n2k" in iface or "nmea" in iface:
            return DiagnosticDomain.MARINE
        # J1939-capable / default commercial-vehicle channel
        return DiagnosticDomain.HEAVY_DUTY

    def _open_diagnostic_session(self) -> None:
        """(Re)open a VehicleSession for evidence collection (fail-never)."""
        try:
            self._diag_session = VehicleSession(
                session_id=f"sess-{time.time_ns()}-{uuid.uuid4().hex[:8]}",
                started_at_ns=time.monotonic_ns(),
                domain=self._domain_for_current_bus(),
            )
            self._signal_rings = {}
        except Exception as exc:  # noqa: BLE001 — evidence plumbing must never kill the app
            logger.warning("Failed to open diagnostic session", extra={"error": str(exc)})
            self._diag_session = None

    def _record_signal_sample(self, name: str, raw: int, physical: float, unit: str) -> None:
        """Append one SignalSample under the session lock (FAZ 1, hook 2).

        Called from the live RX decode path only — the sim branch of
        _telemetry_loop never reaches this (Bulgu 7). Bounded ring per
        signal keeps RX hot-path cost O(1).
        """
        session = self._diag_session
        if session is None or self._is_simulating:
            return
        try:
            sample = SignalSample(
                timestamp_ns=time.monotonic_ns(),
                name=name,
                raw_value=raw,
                physical_value=physical,
                unit=unit,
                source=SignalSource.J1939,
            )
        except ValueError as exc:
            logger.debug("SignalSample rejected", extra={"error": str(exc), "signal": name})
            return
        with self._session_lock:
            ring = self._signal_rings.get(name)
            if ring is None:
                ring = deque(maxlen=self._EVIDENCE_RING_MAX)
                self._signal_rings[name] = ring
            ring.append(sample)
            session.samples.append(sample)

    def _record_dm1_events(self, dtcs: list) -> None:
        """Turn parsed DM1 SPN/FMI records into DiagnosticEvents (FAZ 1, Bulgu 1).

        Severity maps from the KB / SPN DB record; an unknown SPN gets
        "UNKNOWN" — the AI layer never invents severity. SPN 0 / 0xFF
        (no-active-DTC placeholders) produce no event (Bulgu 4).
        """
        session = self._diag_session
        if session is None or self._is_simulating or not dtcs:
            return
        from src.engine.ai.diagnostic_copilot import get_j1939_spn_database

        spn_db = get_j1939_spn_database().get("spns", {})
        ts = time.monotonic_ns()
        with self._session_lock:
            for dtc in dtcs:
                spn, fmi = getattr(dtc, "spn", 0), getattr(dtc, "fmi", 0)
                if spn in (0, 0xFF):
                    continue
                severity = "UNKNOWN"
                rec = spn_db.get(f"SPN_{spn}")
                if rec:
                    fm = rec.get("fault_matrix", {})
                    fmi_rec = fm.get(str(fmi)) if isinstance(fm, dict) else None
                    if isinstance(fmi_rec, dict) and fmi_rec.get("severity"):
                        severity = str(fmi_rec["severity"])
                try:
                    session.events.append(
                        DiagnosticEvent(
                            timestamp_ns=ts,
                            code=f"SPN {spn} FMI {fmi}",
                            domain=DiagnosticDomain.HEAVY_DUTY,
                            severity=severity,
                            status="ACTIVE",
                        )
                    )
                except ValueError as exc:
                    logger.debug("DiagnosticEvent rejected", extra={"error": str(exc), "spn": spn, "fmi": fmi})

    def reset_diagnostic_session(self) -> None:
        """Close the current evidence session and open a fresh one (FAZ 1)."""
        with self._session_lock:
            self._open_diagnostic_session()
            self._dialogue_session = None

    def record_operator_measurement(self, name: str, value: float) -> dict[str, Any]:
        """Append an operator-declared measurement to the session (FAZ 5).

        Chat-parsed operator input only; the name carries the "OP:" prefix
        convention (core SignalSource enum stays untouched — plan §Kapsam
        Dışı). AI never writes the session directly; this bridge method is
        the single ingestion point (closed evidence chain).
        """
        clean = (name or "").strip()
        try:
            val = float(value)
        except (TypeError, ValueError):
            return {"success": False, "error": "Geçersiz ölçüm değeri"}
        if not clean:
            return {"success": False, "error": "Ölçüm adı boş olamaz"}
        session = self._diag_session
        if session is None:
            return {"success": False, "error": "Aktif teşhis oturumu yok"}
        if not math.isfinite(val):
            return {"success": False, "error": "Ölçüm değeri sonlu olmalı"}
        prefixed = clean if clean.startswith("OP:") else f"OP:{clean}"
        try:
            sample = SignalSample(
                timestamp_ns=time.monotonic_ns(),
                name=prefixed,
                raw_value=int(round(val)) if abs(val) < 2**31 else 0,
                physical_value=val,
                unit="",
                source=SignalSource.J1939,
                confidence=1.0,
            )
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        with self._session_lock:
            session.samples.append(sample)
        return {"success": True, "recorded": prefixed, "value": val}

    def record_operator_answer(
        self,
        question_id: str,
        value: Any,
        kind: str = "yes_no",
        unit: str | None = None,
        is_unknown: bool = False,
    ) -> dict[str, Any]:
        """Record structured operator answer for dialogue questionnaire (FAZ 1)."""
        clean_qid = (question_id or "").strip()
        if not clean_qid:
            return {"success": False, "error": "Soru ID boş olamaz"}
        session = self._diag_session
        if session is None:
            return {"success": False, "error": "Aktif teşhis oturumu yok"}

        from src.engine.ai.dialogue_engine import DialogueSession, OperatorAnswer

        with self._session_lock:
            if self._dialogue_session is None:
                self._dialogue_session = DialogueSession()
                active_dtcs = [e.code for e in session.events if e.status == "ACTIVE"]
                self._dialogue_session.start_triage(session, dtc_codes=active_dtcs)

            answer = OperatorAnswer(
                question_id=clean_qid,
                kind=kind,  # type: ignore[arg-type]
                value=value,
                unit=unit,
                is_unknown=is_unknown,
                recorded_at_ns=time.monotonic_ns(),
            )
            result = self._dialogue_session.record_answer(answer, session)
            active_dtcs = [e.code for e in session.events if e.status == "ACTIVE"]
            next_q = self._dialogue_session.select_next_question(session, dtc_codes=active_dtcs)

            return {
                "success": True,
                "recorded": clean_qid,
                "result": result,
                "next_question": next_q.to_dict() if next_q else None,
                "dialogue_state": self._dialogue_session.state.value,
                "eliminated_hypotheses": [e.to_dict() for e in self._dialogue_session.eliminated_hypotheses],
                "proposed_actions": list(self._dialogue_session.proposed_actions),
                "concluded_fault": self._dialogue_session.concluded_fault,
                "next_guidance": result.get("next_guidance"),
            }

    def get_dialogue_state(self) -> dict[str, Any]:
        """Get current interactive dialogue session state and active question."""
        session = self._diag_session
        if session is None:
            return {"success": False, "error": "Aktif teşhis oturumu yok"}

        from src.engine.ai.dialogue_engine import DialogueSession

        with self._session_lock:
            if self._dialogue_session is None:
                self._dialogue_session = DialogueSession()
                active_dtcs = [e.code for e in session.events if e.status == "ACTIVE"]
                self._dialogue_session.start_triage(session, dtc_codes=active_dtcs)

            active_dtcs = [e.code for e in session.events if e.status == "ACTIVE"]
            current_q = self._dialogue_session.current_question or self._dialogue_session.select_next_question(
                session, dtc_codes=active_dtcs
            )

            return {
                "success": True,
                "dialogue_state": self._dialogue_session.state.value,
                "current_question": current_q.to_dict() if current_q else None,
                "answered_count": len(self._dialogue_session.answers),
                "eliminated_hypotheses": [e.to_dict() for e in self._dialogue_session.eliminated_hypotheses],
                "proposed_actions": list(self._dialogue_session.proposed_actions),
                "concluded_fault": self._dialogue_session.concluded_fault,
                "concluded_confidence": self._dialogue_session.concluded_confidence,
            }

    def get_diagnostic_kpi_metrics(self) -> dict[str, Any]:
        """Compute live AI diagnostic quality KPIs and calibration metrics (FAZ 0 & FAZ 3)."""
        from src.engine.ai.calibration import evaluate_calibration
        from src.engine.ai.golden_cases import load_all_cases
        from src.engine.ai.metrics import compute_metrics_dashboard
        from src.engine.ai.procedure_validator import load_all_procedures

        try:
            all_procs = load_all_procedures()
            all_cases = load_all_cases()
            cal = evaluate_calibration(all_cases)
            covered_count = len(all_procs)
            total_catalog = 14352
            total_q = len(self._dialogue_session.answers) if self._dialogue_session else 0

            metrics = compute_metrics_dashboard(
                covered_dtcs=covered_count,
                total_dtcs=total_catalog,
                golden_correct=cal.top1_matches,
                golden_total=cal.verified_cases,
                total_diagnoses=1,
                total_questions=total_q,
                false_diagnoses=0,
            )
            out = metrics.to_dict()
            out["calibration_factor"] = cal.calibration_factor
            out["verified_golden_cases"] = cal.verified_cases
            out["total_procedures_count"] = covered_count
            return {"success": True, "metrics": out}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def record_technician_feedback(self, dtc: str, resolved: bool, notes: str = "") -> dict[str, Any]:
        """Record technician resolution note ('yanıldım/çözdü') into local learning pool (FAZ 5)."""
        from src.engine.ai.user_kb import record_operator_feedback
        res = record_operator_feedback(dtc=dtc, resolved=resolved, notes=notes)
        return {"success": True, "result": res}

    def get_diagnostic_analysis(self) -> dict[str, Any]:
        """Full FAZ 2..6 analysis over the live evidence session (bridge)."""
        from src.engine.ai.anomaly_detector import detect_anomalies, load_thresholds
        from src.engine.ai.evidence_gate import evaluate_sufficiency
        from src.engine.ai.golden_similarity import find_similar_cases
        from src.engine.ai.hypothesis_engine import rank_hypotheses
        from src.engine.ai.session_report import report_summary_dict
        from src.engine.ai.user_report_composer import compose_user_card

        session = self._diag_session
        if session is None:
            return {"success": False, "error": "Aktif teşhis oturumu yok"}
        sufficiency = evaluate_sufficiency(session)
        anomalies: list = []
        hypotheses: list = []
        similar: list = []
        if sufficiency.anomaly_sufficient:
            try:
                thresholds = load_thresholds()
            except Exception as exc:  # noqa: BLE001 — missing/bad DB must not kill the bridge
                logger.warning("Threshold DB load failed; anomaly scan skipped", extra={"error": str(exc)})
                thresholds = {}
            anomalies = detect_anomalies(session, thresholds) if thresholds else []
            similar = find_similar_cases(session, k=3)
        if sufficiency.dtc_sufficient:
            hypotheses = rank_hypotheses(session, anomalies, similar)

        # Engineering report feeds the deterministic user decision card.
        # T56-B / A3-1: the bridge used to hardcode spn/fmi to None, so the
        # copilot's J1939 KB path (SPN<num> lookup in _analyze_local_expert)
        # was unreachable for live DM1 codes and the 4.2k-entry SPN database
        # never enriched a session report. The SPN/FMI is already textually
        # present in DiagnosticEvent.code ("SPN <n> FMI <m>") — re-derive it
        # instead of dropping the information. Codes without the SPN form
        # keep (None, None): never fabricate an SPN from arbitrary text.
        dtc_payload = []
        for e in session.events:
            if e.status != "ACTIVE":
                continue
            match = _SPN_FMI_RE.search(e.code or "")
            dtc_payload.append(
                {
                    "code": e.code,
                    "spn": int(match.group(1)) if match else None,
                    "fmi": int(match.group(2)) if match and match.group(2) is not None else None,
                }
            )
        # F-07 (P1): the panel path used to pass an EMPTY telemetry dict
        # (`analyze_session(dtc_payload, {}, [])`) while the *chat* path fed the
        # same copilot the live `_current_rpm/_current_boost/_current_temp`.
        # The two entry points therefore produced different severities and
        # confidence scores for the same vehicle state, and the panel silently
        # dropped every TELEMETRY-correlation cause. Rebuild the same snapshot
        # the chat path uses, and omit a signal ENTIRELY when it is not
        # trustworthy so the engine's "Veri Yok" logic (AGENTS.md §2.3) still
        # fires instead of reading a stale 0.0 as a measured value.
        live_telemetry: dict[str, Any] = {}
        if math.isfinite(self._current_rpm) and self._current_rpm > 0.0:
            live_telemetry["EngineSpeed"] = self._current_rpm
        if math.isfinite(self._current_boost):
            live_telemetry["BoostPressure"] = self._current_boost
        if math.isfinite(self._current_temp) and self._current_temp > 0.0:
            live_telemetry["CoolantTemp"] = self._current_temp
        report = self.copilot.analyze_session(dtc_payload, live_telemetry, [])
        card = compose_user_card(report, session, is_simulating=self._is_simulating)
        return {
            "success": True,
            "user_card": card.card_to_dict(),
            # T56-B / A3-1: expose the engineering report (KB/J1939-enriched
            # causes + subsystems) alongside the decision card. It was
            # previously computed and discarded here, so the SPN/FMI
            # enrichment was unobservable to callers.
            "report": {
                "summary": report.summary,
                "severity": report.severity.value,
                "likely_causes": list(report.likely_causes),
                "affected_subsystems": list(report.affected_subsystems),
                "troubleshooting_steps": [
                    {
                        "step": s.step_number,
                        "action": s.action,
                        "component": s.target_component,
                        "difficulty": s.difficulty,
                    }
                    for s in report.troubleshooting_steps
                ],
                "telemetry_correlations": list(report.telemetry_correlations),
                "raw_dtc_count": report.raw_dtc_count,
                "ai_model_used": report.ai_model_used,
            },
            **report_summary_dict(sufficiency, anomalies, hypotheses, similar),
        }

    def get_session_evidence_summary(self) -> dict[str, Any]:
        """Gate report + signal inventory for the Teşhis Oturumu panel (FAZ 1)."""
        from src.engine.ai.evidence_gate import evaluate_sufficiency

        session = self._diag_session
        if session is None:
            return {"success": False, "error": "Aktif teşhis oturumu yok"}
        with self._session_lock:
            sufficiency = evaluate_sufficiency(session)
            sample_count = len(session.samples)
            event_count = len(session.events)
            session_id = session.session_id
        return {
            "success": True,
            "session_id": session_id,
            "sample_count": sample_count,
            "event_count": event_count,
            "is_simulating": self._is_simulating,
            **sufficiency.to_dict(),
        }

    def export_session_report(self) -> dict[str, Any]:
        """Build + persist the FAZ 6 technician report (.md) to the reports dir."""
        from src.engine.ai.anomaly_detector import detect_anomalies, load_thresholds
        from src.engine.ai.discriminating_tests import propose_discriminating_tests
        from src.engine.ai.evidence_gate import evaluate_sufficiency
        from src.engine.ai.golden_similarity import find_similar_cases
        from src.engine.ai.hypothesis_engine import rank_hypotheses
        from src.engine.ai.session_report import build_technician_report

        session = self._diag_session
        if session is None:
            return {"success": False, "error": "Aktif teşhis oturumu yok"}
        sufficiency = evaluate_sufficiency(session)
        anomalies: list = []
        similar: list = []
        if sufficiency.anomaly_sufficient:
            try:
                thresholds = load_thresholds()
            except Exception:
                thresholds = {}
            anomalies = detect_anomalies(session, thresholds) if thresholds else []
            similar = find_similar_cases(session, k=3)
        hypotheses = rank_hypotheses(session, anomalies, similar) if sufficiency.dtc_sufficient else []
        active_codes = [e.code for e in session.events if e.status == "ACTIVE"]
        tests = propose_discriminating_tests(hypotheses, active_codes)

        # FAZ 5: Extract dialogue transcript & eliminated hypotheses if session active
        transcript: list[dict[str, Any]] = []
        eliminated: list[Any] = []
        if self._dialogue_session is not None:
            for q in self._dialogue_session.questions_history:
                ans = self._dialogue_session.answers.get(q.id)
                if ans is not None:
                    ans_str = "Bilmiyorum" if ans.is_unknown else str(ans.value)
                    if ans.unit:
                        ans_str += f" {ans.unit}"
                    transcript.append({
                        "question": q.text,
                        "answer": ans_str,
                        "kind": ans.kind,
                        "evidence_link": q.evidence_link,
                    })
            eliminated = list(self._dialogue_session.eliminated_hypotheses)

        report = build_technician_report(
            session,
            sufficiency,
            anomalies,
            hypotheses,
            similar,
            tests,
            dialogue_transcript=transcript,
            eliminated_hypotheses=eliminated,
        )
        try:
            reports_dir = _app_data_root() / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            out_path = reports_dir / f"diagnostic_report_{time.strftime('%Y%m%d_%H%M%S')}.md"
            out_path.write_text(report, encoding="utf-8")
        except OSError as exc:
            return {"success": False, "error": str(exc)}
        return {
            "success": True,
            "path": str(out_path),
            "report_length": len(report),
        }


    @staticmethod
    def _warm_hwid_cache() -> None:
        """Pre-compute the hardware fingerprint off the UI/bridge threads.

        REVIEW.md MEDIUM-6 / REVIEW2 #6: the cold HWID collection runs 4-5
        PowerShell/WMI subprocesses with individual 10 s timeouts; on a
        broken WMI repository that is ~50 s of blocking on the FIRST call.
        Warming the lru_cache here (daemon thread, started in __init__)
        means cloud_get_status / launcher preflight always hit the cache.
        Failures are swallowed on purpose: a fallback fingerprint path
        exists, and a warm-up failure must never block app startup.
        """
        try:
            generate_hardware_fingerprint()
        except Exception as exc:  # noqa: BLE001 — warm-up is best-effort only
            logger.debug("HWID warm-up failed (will retry lazily)", extra={"error": str(exc)})

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

    def _set_driver_listen_only(self, listen_only: bool) -> bool:
        """Flip the physical driver's listen-only mode (verified, best-effort).

        REVIEW (arm_tx vs PASSIVE driver): the supervisor transitioned to
        ARMED_TX while the driver was still opened listen-only — the very
        first UDS/J1939 send then raised HardwareError from deep inside
        the protocol path. Arming now performs the driver mode transition
        atomically: an unverified backend state fails the ARM request
        itself instead of arming a dead protocol.

        Returns True when the requested mode is confirmed active. Backends
        without a mutable state (RP1210 adapter: send-block only) are
        handled by their own listen_only semantics.
        """
        bus = self.bus
        set_state = getattr(bus, "set_listen_only", None)
        if callable(set_state):
            try:
                return bool(set_state(listen_only))
            except Exception as exc:  # noqa: BLE001 — driver refusal = not armed
                logger.error(
                    "Driver refused listen-only mode change",
                    extra={"listen_only": listen_only, "error": str(exc)},
                )
                return False
        # Bus types without a mutable mode (RP1210, mocks): listen_only flag
        # is a constructor contract; verify it matches the request.
        return getattr(bus, "listen_only", not listen_only) == listen_only

    def _derive_secret(self, key_name: str, nbytes: int = 32) -> bytes:
        """P3 (G-3): fetch (or create-on-first-run) a TX-authorization secret.

        Fail-closed: if the platform SecretProvider cannot persist a newly
        generated key, the exception propagates — the app must NOT boot with a
        silently weakened TX authorization gate.
        """
        provider = self._secret_provider
        if not provider.has_secret(key_name):
            provider.store_secret(key_name, os.urandom(nbytes))
            logger.info("Generated and persisted TX authorization secret %s", key_name)
        return provider.get_secret(key_name)

    def _report_secret_protection_level(self) -> str:
        """A5-2: surface (and warn about) the active secret-store strength.

        A silent downgrade from DPAPI / AES-GCM-0600 to an EPHEMERAL or
        FALLBACK_FILE backend weakens every key this app derives (TX
        authorization, gateway confirmation, E-Stop, cloud session). The
        provider already exposes `protection_level()`; nothing consumed it, so
        the downgrade was invisible. Returns the level's string value.
        """
        try:
            level = self._secret_provider.protection_level()
        except Exception as exc:  # noqa: BLE001 — reporting must never block boot
            logger.warning("Could not determine secret-store protection level", extra={"error": str(exc)})
            return "UNKNOWN"

        name = getattr(level, "value", str(level))
        degraded = name in ("EPHEMERAL", "FALLBACK_FILE")
        if degraded:
            logger.warning(
                "Secret store is running in a DEGRADED protection mode: cryptographic gates "
                "use non-persistent or weaker key material.",
                extra={"protection_level": name},
            )
        else:
            logger.info("Secret-store protection level", extra={"protection_level": name})
        return name

    def arm_tx(self, reason: str = "Operator explicitly armed TX via desktop UI") -> dict[str, Any]:
        """Explicitly transition SafetySupervisor from PASSIVE to ARMED_TX."""
        try:
            if self.estop.is_engaged:
                return {"success": False, "error": "Cannot arm TX: E-Stop is currently engaged"}
            # P0-2 (REVIEW C-2): simulator active means the speed feed is
            # synthetic — refuse to arm TX against a possibly-moving real
            # vehicle while telemetry is simulated. Checked BEFORE the speed
            # interlock: the simulator refusal is the more specific diagnosis
            # and P0-2's contract ("synthetic speed cannot authorize TX")
            # must surface even when the physical feed is stale.
            if self._is_simulating:
                return {"success": False, "error": "Cannot arm TX: simulator is active (synthetic speed cannot authorize TX)"}
            # P0-5 (REVIEW C-3) / REVIEW 3: the gateway is the SINGLE
            # authoritative speed truth (physical-feed freshness, NaN
            # invalidation, noise threshold) — the desktop's mirror
            # `_current_speed_kmh` is display-only and never a gate.
            speed_state, speed_val = self.gateway.speed_interlock_state()
            if speed_state == "stale":
                return {"success": False, "error": "Cannot arm TX: vehicle speed is unknown (untrusted or implausible CCVS feed)"}
            if speed_state == "moving":
                return {"success": False, "error": f"Cannot arm TX: Vehicle speed must be 0 km/h (current: {speed_val:.1f} km/h)"}
            # REVIEW (driver mode atomicity): the supervisor flips to ARMED_TX
            # only AFTER the physical driver actually left listen-only. An
            # unverified/silent backend keeps the protocol dead-but-armed.
            # O-1: refresh the liveness lease BEFORE the driver-mode
            # transition. The old code refreshed inside the arm critical
            # section, masking the start race (the lease was extended at the
            # same instant as the ARMED_TX flip). An identified, token-
            # authenticated pulse here keeps the interlock honest.
            if not self.watchdog.is_lease_valid:
                self.watchdog.heartbeat(self._heartbeat_token)
            with self._bus_lock:
                if not self._set_driver_listen_only(False):
                    return {
                        "success": False,
                        "error": "Cannot arm TX: hardware driver did not confirm active (TX-capable) mode",
                    }
                try:
                    # P3 (G-3): the supervisor now enforces HMAC authorization.
                    # The desktop composition root mints the arm token here, in the
                    # TRUSTED process, immediately before the transition — the
                    # WebView bridge never receives a minting primitive.
                    arm_token = self._mint_arm_token()
                    self.supervisor.arm_tx(reason=reason, auth_token=arm_token)
                    if not self.supervisor.is_tx_permitted:
                        raise SafetyError("SafetySupervisor did not permit TX after arm")
                except Exception as arm_exc:
                    # T62-M1: Rollback driver to listen-only if supervisor arm fails
                    rollback_ok = False
                    try:
                        rollback_ok = self._set_driver_listen_only(True)
                    except Exception:
                        rollback_ok = False
                    if not rollback_ok:
                        logger.critical("Failed to roll back driver to listen-only mode after failed arm_tx — forcing FAULT")
                        try:
                            self.supervisor._force_fault("TX_ARM_ROLLBACK_FAILED")
                        except Exception:
                            pass
                        try:
                            self.estop.trigger(EStopTriggerSource.UNAUTHORIZED_PAYLOAD, "TX_ARM_ROLLBACK_FAILED: driver active rollback failed")
                        except Exception:
                            pass
                        return {"success": False, "error": "TX_ARM_ROLLBACK_FAILED: hardware driver stuck in active mode"}
                    raise arm_exc
            return {"success": True, "state": self.supervisor.current_state.value}
        except Exception as exc:
            logger.error("Failed to arm TX pipeline: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}

    def _mint_arm_token(self) -> str | None:
        """Mint a single-use ARMED_TX authorization token (P3 / G-3).

        Returns None when the supervisor has no auth_secret configured (the
        legacy unauthenticated path is then still valid). The minting entry
        point stays private to the desktop app — it is NOT bridged to JS.
        """
        try:
            return self.supervisor.issue_arm_token()
        except SafetyError:
            # No auth_secret configured -> supervisor keeps its legacy path.
            return None

    def disarm_tx(self, reason: str = "Operator returned system to PASSIVE mode") -> dict[str, Any]:
        """Transition SafetySupervisor back to PASSIVE mode."""
        try:
            if self.supervisor.current_state in (SafetyState.ARMED_TX, SafetyState.ACTIVE):
                self.supervisor.transition_to(SafetyState.PASSIVE, reason=reason)
                # Return the physical transceiver to listen-only alongside the
                # supervisor disarm — a disarmed supervisor with an active
                # driver still ACKs onto a live bus.
                with self._bus_lock:
                    if not self._set_driver_listen_only(True):
                        logger.critical("Driver failed to return to listen-only mode during disarm_tx")
                        try:
                            self.supervisor._force_fault("DISARM_DRIVER_ROLLBACK_FAILED")
                        except Exception:
                            pass
                        try:
                            self.estop.trigger(EStopTriggerSource.UNAUTHORIZED_PAYLOAD, "DISARM_DRIVER_ROLLBACK_FAILED")
                        except Exception:
                            pass
                        return {"success": False, "error": "DISARM_DRIVER_ROLLBACK_FAILED: hardware driver stuck in active mode"}
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
        # O-2: the authoritative latch is `estop.is_engaged` (the safety
        # object). `_is_estop` is only the UI mirror and can lag a hardware
        # E-Stop. Check BOTH so a drifted mirror cannot open the toggle.
        if self._is_estop or self.estop.is_engaged:
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
    # LINK-FAULT wiring (Kontrol #21/#44): consecutive telemetry-tick
    # HardwareErrors before the interface is declared disconnected. 3 ticks
    # ≈ 150–300 ms — fast enough to bound stale-TX authority, slow enough
    # that one transient vendor glitch cannot latch the E-Stop.
    HW_ERROR_ESTOP_AFTER: ClassVar[int] = 3

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

    # ------------------------------------------------------------------
    # Diagnostic Challenge & Action Execution Subsystem (Dual Confirmation)
    # ------------------------------------------------------------------
    def _mint_confirmation_token(self, context: str | None = None) -> str:
        """Mint a single-use HMAC confirmation token via the gateway (P3 / G-3).

        The token is produced from the `GATEWAY_CONFIRM_SECRET` by the SAME
        component that verifies it in Stage 5, so it is cryptographic proof
        bound to the canonical diagnostic arbitration ID — not an in-process
        random string the renderer could mint for itself.

        R2-EN2: process-internal ONLY — never returned to JS. The renderer
        path (`request_diagnostic_challenge`) issues nonce challenges;
        gateway tokens are minted here and consumed by the TX path.
        R2-EN3: `context` (e.g. "action_type:action_id") binds the token to
        the authorized action.
        """
        token = self.gateway.issue_confirmation_token(
            _DIAGNOSTIC_CONFIRM_ARB_ID, ttl_s=30.0, context=context
        )
        return token.hex()

    def _confirm_token_for(
        self, arbitration_id: int, context: str | None = None
    ) -> bytes | None:
        """Fresh single-use gateway ConfirmationToken bound to `arbitration_id`.

        P3 (G-3): only the trusted composition root mints these; returns None
        when the gateway has no confirmation secret (legacy wiring), so the
        UDS client simply omits the parameter.
        R2-EN3: `context` binds the token to the calling action; the gateway
        rejects the token when presented with a different context.
        """
        if self.gateway._confirmation_secret is None:  # noqa: SLF001 - wiring introspection
            return None
        return self.gateway.issue_confirmation_token(
            arbitration_id, ttl_s=30.0, context=context
        )

    @staticmethod
    def _compute_action_params_hash(action: dict[str, Any]) -> str:
        """Compute deterministic SHA-256 digest of security-relevant action parameters (T62-M9)."""
        params = action.get("params")
        if isinstance(params, dict):
            payload = params
        else:
            critical_keys = (
                "routine_id", "routineId", "target_address", "targetAddress",
                "destination_address", "destinationAddress", "group", "session_type",
                "sessionType", "reset_type", "resetType", "memoryAddress", "blockSize",
                "sizeBytes", "fileName", "expectedVin", "expectedSerial", "data", "id",
            )
            payload = {k: action[k] for k in critical_keys if k in action}
        try:
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        except Exception:
            canonical = str(sorted(payload.items()))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def request_diagnostic_challenge(self, action: dict[str, Any]) -> dict[str, Any]:
        """Issue a short-lived (≤30s) single-use nonce challenge for a diagnostic action.

        R2-EN2: this renderer-reachable endpoint NEVER mints a gateway HMAC
        token — it returns a nonce bound to (action_type, action_id, params_hash).
        The gateway token authorizing the TX is minted process-internally in
        `execute_diagnostic_action` after the nonce verifies, so a renderer
        script can never manufacture its own TX authorization.
        """
        if not isinstance(action, dict):
            return {"success": False, "error": "Geçersiz aksiyon verisi (dictionary bekleniyor)."}

        action_type = str(action.get("action_type") or "").strip()
        if not action_type:
            return {"success": False, "error": "Aksiyon türü (action_type) belirtilmelidir."}

        action_id = str(action.get("id") or "")
        params_hash = self._compute_action_params_hash(action)

        token = secrets.token_hex(16)
        now_ns = time.monotonic_ns()

        challenge = DiagnosticChallenge(
            token=token,
            action_type=action_type,
            action_id=action_id,
            created_at_monotonic_ns=now_ns,
            max_age_ns=30_000_000_000,
            params_hash=params_hash,
        )

        with self._challenges_lock:
            # Prune expired tokens
            expired = [k for k, ch in self._diagnostic_challenges.items() if (now_ns - ch.created_at_monotonic_ns) > ch.max_age_ns]
            for k in expired:
                self._diagnostic_challenges.pop(k, None)
            self._diagnostic_challenges[token] = challenge

        return {
            "success": True,
            "token": token,
            "expires_in_s": 30.0,
            "action_type": action_type,
            "action_id": action_id,
        }

    def _verify_and_consume_diagnostic_token(
        self,
        token: str | None,
        action_type: str,
        action_id: str = "",
        action_payload: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Verify that a single-use nonce challenge is present, unexpired (≤30s), and matches action_type/action_id/params.

        R2-EN2: renderer-presented tokens are nonce challenges ONLY. A
        gateway HMAC token presented from JS is rejected — gateway tokens
        are minted process-internally (`_confirm_token_for`) and never cross
        the bridge.
        """
        if not token or not isinstance(token, str) or not token.strip():
            return False, "Kullanıcı onayı gereklidir (Dual Confirmation challenge token eksik)."

        cleaned_token = token.strip()

        now_ns = time.monotonic_ns()

        with self._challenges_lock:
            challenge = self._diagnostic_challenges.pop(cleaned_token, None)
            if challenge is None:
                return False, "Geçersiz veya daha önce kullanılmış onay token'ı (tek kullanımlık)."

            if (now_ns - challenge.created_at_monotonic_ns) > challenge.max_age_ns:
                return False, "Onay token'ının süresi dolmuş (≤30s limit)."

            if challenge.action_type != action_type:
                return False, f"Onay token'ı aksiyon türü ile uyuşmuyor ({challenge.action_type} != {action_type})."

            if action_id and challenge.action_id and challenge.action_id != action_id:
                return False, f"Onay token'ı aksiyon kimliği ile uyuşmuyor ({challenge.action_id} != {action_id})."

            if action_payload is not None and challenge.params_hash:
                current_hash = self._compute_action_params_hash(action_payload)
                if current_hash != challenge.params_hash:
                    return False, "Onay token'ı aksiyon parametreleri ile uyuşmuyor (parametreler değiştirilmiş)."

        return True, "OK"

    def execute_diagnostic_action(
        self,
        action: dict[str, Any],
        confirmation_token: str | None = None,
        user_confirmed: bool = False,
    ) -> dict[str, Any]:
        """Execute actionable diagnostic routine with challenge token verification and safety gates."""
        if not isinstance(action, dict):
            err = "Geçersiz aksiyon verisi (dictionary bekleniyor)."
            return {"success": False, "error": err, "message": err}

        action_type = str(action.get("action_type") or "")
        action_id = str(action.get("id") or "")
        # REVIEW 3 (CRITICAL): mutation/elevation/reset/clear-DTC/security-
        # access/routine actions are confirmation-mandatory BY TYPE. An
        # attacker- or config-supplied requires_confirmation:false on these
        # types must never downgrade the gate (fail-closed: type wins).
        CONFIRMATION_EXEMPT_ACTIONS = frozenset({
            "uds_read_did",
            "uds_read_vin",
            "read_did",
            "read_vin",
            "j1939_dm1_query",
            "j1939_dm1",
        })
        if action_type in CONFIRMATION_EXEMPT_ACTIONS:
            requires_conf = False
        else:
            requires_conf = True
            if action.get("requires_confirmation") is False:
                logger.warning(
                    "Diagnostic action '%s' tried to disable dual confirmation via "
                    "requires_confirmation=false — ignored (type-mandatory gate)",
                    action_type,
                )
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

        # 2. Safety Check: Speed Interlock (REVIEW 3: gateway = single authoritative
        # source in PHYSICAL mode. Simulation mode is display/sandbox only and by
        # design cannot authorize TX onto a live bus (P0-2), so the sandboxed
        # scenario speed mirror gates the simulated action; physical actions
        # always answer to the gateway's physical-feed interlock.)
        if self._is_simulating:
            if not math.isfinite(self._current_speed_kmh) or self._current_speed_kmh != 0.0:
                err = f"Güvenlik Kilidi: Araç hareketsiz (0.0 km/s) durumda olmalıdır (Mevcut hız: {self._current_speed_kmh:.1f} km/s)."
                return {"success": False, "error": err, "message": err}
        else:
            speed_state, speed_val = self.gateway.speed_interlock_state()
            if speed_state == "stale":
                err = "Güvenlik Kilidi: Araç hızı bilinmiyor veya güncel değil (fiziksel CCVS akışı yok/NaN)."
                logger.warning("Diagnostic action '%s' refused: speed stale/unknown (gateway interlock state)", action_type)
                return {"success": False, "error": err, "message": err}
            if speed_state == "moving":
                logger.warning(
                    "Diagnostic action '%s' refused: vehicle moving (speed=%.1f)",
                    action_type,
                    speed_val,
                )
                err = f"Güvenlik Kilidi: Araç hareketsiz (0.0 km/s) durumda olmalıdır (Mevcut hız: {speed_val:.1f} km/s)."
                return {"success": False, "error": err, "message": err}

        # 3. Dual Confirmation Check (Challenge Token Verification)
        if requires_conf:
            # H-1: the token must come ONLY from the explicit
            # `confirmation_token` parameter — an out-of-band channel the
            # caller cannot populate via its own `action` payload. The old
            # `action["confirmation_token"]` / `action["token"]` / string
            # `user_confirmed` fallbacks let the caller satisfy its own
            # "second, independent channel" invariant.
            token_candidate = confirmation_token if isinstance(confirmation_token, str) else None

            try:
                valid, reason = self._verify_and_consume_diagnostic_token(
                    token_candidate, action_type, action_id, action_payload=action
                )
            except TypeError:
                valid, reason = self._verify_and_consume_diagnostic_token(
                    token_candidate, action_type, action_id
                )
            if not valid:
                logger.warning("Diagnostic action '%s' dual confirmation failed: %s", action_type, reason)
                return {
                    "success": False,
                    "error": reason,
                    "message": reason,
                }

        # FAZ 4: AI-Initiative Safe Action Enforcement
        is_ai_suggested = bool(action.get("is_ai_suggested") or action.get("source") == "ai_dialogue")
        if is_ai_suggested:
            from src.engine.ai.drive_safety_policy import validate_ai_dialogue_action
            speed_val = self._current_speed_kmh if self._is_simulating else (self.gateway.speed_interlock_state()[1] or 0.0)
            valid_action, action_msg = validate_ai_dialogue_action(
                action_type=action_type,
                confirmed_by_operator=user_confirmed or (not requires_conf),
                vehicle_speed_kmh=speed_val,
            )
            if not valid_action:
                logger.warning("AI-suggested action '%s' rejected by drive_safety_policy: %s", action_type, action_msg)
                return {"success": False, "error": action_msg, "message": action_msg}


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
                    _ctx = f"{action_type}:{action_id}"
                    resp = client.clear_dtc(
                        group,
                        user_confirmed=True,
                        confirmation_token=self._confirm_token_for(client.tx_id, _ctx),
                        confirmation_context=_ctx,
                    )
                    if resp.is_positive:
                        self._set_ui_state(_error_count=0)
                        return {
                            "success": True,
                            "message": f"✅ [UDS 0x14] ECU arıza hafızası başarıyla temizlendi (Pozitif Yanıt 0x{resp.service_id + 0x40:02X}).",
                            "service": "0x14",
                            "data": {"service": "0x14", "group": hex(group)},
                        }
                    else:
                        err = f"âŒ [UDS 0x14] ECU reddetti: NRC 0x{resp.nrc:02X} ({resp.nrc_description_tr})"
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
                        "message": f"ğŸ“„ [UDS 0x22 DID 0x{did:04X}] {name}: `{val}` (Pozitif Yanıt 0x62).",
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
                            "message": f"ğŸ“„ [UDS 0x22 DID 0x{did:04X}] {name}: `{val_str}` (Pozitif Yanıt 0x62).",
                            "data": {"did": f"0x{did:04X}", "value": val_str, "name": name},
                        }
                    else:
                        err = f"âŒ [UDS 0x22] DID 0x{did:04X} okunamadı: NRC 0x{resp.nrc:02X} ({resp.nrc_description_tr})"
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
                        "message": f"ğŸ”„ [UDS 0x10] Oturum başarıyla değiştirildi (Oturum: 0x{st:02X}, Pozitif Yanıt 0x50 0x{st:02X}).",
                        "session_type": st,
                        "data": {"session_type": st},
                    }
                else:
                    arm_err_resp = _ensure_armed("Operator switched diagnostic session")
                    if arm_err_resp is not None:
                        return arm_err_resp
                    client = self.create_uds_client()
                    _ctx = f"{action_type}:{action_id}"
                    resp = client.change_session(
                        DiagnosticSessionType(st),
                        user_confirmed=True,
                        confirmation_token=self._confirm_token_for(client.tx_id, _ctx),
                        confirmation_context=_ctx,
                    )
                    if resp.is_positive:
                        return {
                            "success": True,
                            "message": f"ğŸ”„ [UDS 0x10] Teşhis oturumu 0x{st:02X} moduna geçirildi (Pozitif Yanıt 0x50).",
                            "data": {"session_type": st},
                        }
                    else:
                        err = f"âŒ [UDS 0x10] Oturum değiştirilemedi: NRC 0x{resp.nrc:02X} ({resp.nrc_description_tr})"
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
                        "message": f"â–¶ï¸ [UDS 0x31] Teşhis rutini 0x{rid:04X} başarıyla başlatıldı (Pozitif Yanıt 0x71).",
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
                            "message": f"â–¶ï¸ [UDS 0x31] Rutin 0x{rid:04X} başlatıldı (Pozitif Yanıt 0x71).",
                            "data": {"routine_id": hex(rid)},
                        }
                    else:
                        err = f"âŒ [UDS 0x31] Rutin başlatılamadı: NRC 0x{resp.nrc:02X} ({resp.nrc_description_tr})"
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
                    _ctx = f"{action_type}:{action_id}"
                    resp = client.ecu_reset(
                        reset_type=rt,
                        user_confirmed=True,
                        confirmation_token=self._confirm_token_for(client.tx_id, _ctx),
                        confirmation_context=_ctx,
                    )
                    if resp.is_positive:
                        return {
                            "success": True,
                            "message": "⚡ [UDS 0x11] ECU Reset komutu onaylandı (Pozitif Yanıt 0x51).",
                            "data": {"reset_type": rt},
                        }
                    else:
                        err = f"âŒ [UDS 0x11] ECU Reset reddedildi: NRC 0x{resp.nrc:02X} ({resp.nrc_description_tr})"
                        return {
                            "success": False,
                            "error": err,
                            "message": err,
                        }

            # J1939 DM11 Clear DTC
            elif action_type in ("j1939_clear_dtc", "j1939_dm11"):
                da = int(params.get("destination_address", params.get("target_address", 0x00)))
                if self._is_simulating:
                    self._set_ui_state(_error_count=0)
                    self._active_scenario = "nominal"
                    return {
                        "success": True,
                        "message": "✅ [J1939 DM11] Ağır vasıta aktif arıza hafızası temizlendi (PGN 65235).",
                        "pgn": 65235,
                        "data": {"pgn": 65235, "target_address": da},
                    }
                else:
                    arm_err_resp = _ensure_armed("Operator executed J1939 DM11 Clear DTC")
                    if arm_err_resp is not None:
                        return arm_err_resp
                    try:
                        req_frame = J1939DiagnosticService.create_dm11_frame(
                            target_address=da, source_address=0xF9
                        )
                        # P3 (G-3): the gateway now requires HMAC proof for a
                        # critical frame. The operator's dual confirmation was
                        # already verified above, so the trusted composition
                        # root mints a fresh single-use gateway token for this
                        # exact transmission.
                        self.gateway.validate_and_transmit(
                            req_frame,
                            budget_category="diagnostic",
                            is_critical_command=True,
                            user_confirmed=True,
                            confirmation_token=self.gateway.issue_confirmation_token(
                                req_frame.arbitration_id, ttl_s=30.0
                            ),
                        )
                        self._set_ui_state(_error_count=0)
                        return {
                            "success": True,
                            "message": f"✅ [J1939 DM11] Ağır vasıta aktif arıza hafızası temizleme komutu iletildi (PGN 65235, Hedef: 0x{da:02X}).",
                            "pgn": 65235,
                            "data": {"pgn": 65235, "target_address": hex(da)},
                        }
                    except Exception as exc:
                        logger.error("J1939 DM11 transmission failed", exc_info=True)
                        err = f"âŒ [J1939 DM11] Komut iletilemedi: {exc}"
                        return {"success": False, "error": err, "message": err}

            # J1939 DM1 Query
            elif action_type in ("j1939_dm1_query", "j1939_dm1"):
                dtc = self.SCENARIO_DTCS.get(self._active_scenario, "Aktif Arıza Yok")
                return {
                    "success": True,
                    "message": f"ğŸ“‹ [J1939 DM1] Aktif Arıza Durumu: {dtc} (PGN 65226 DM1 yayını dinleniyor).",
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

    # ------------------------------------------------------------------
    # Signal Discovery Engine Methods
    # ------------------------------------------------------------------
    def discovery_get_summary(self) -> dict[str, Any]:
        """Summary of discovered IDs and frame counts."""
        return {
            "total_frames": self.discovery_engine._total_frames,
            "discovered_ids": [f"0x{arb:03X}" for arb in self.discovery_engine.discovered_ids],
            "id_counts": {
                f"0x{arb:03X}": self.discovery_engine.get_frame_count(arb)
                for arb in self.discovery_engine.discovered_ids
            },
        }

    def discovery_analyze_id(self, arb_id: int) -> dict[str, Any]:
        """Analyze a specific CAN arbitration ID."""
        report = self.discovery_engine.analyze_id(arb_id)
        return {
            "arbitration_id": f"0x{report.arbitration_id:03X}",
            "frame_count": report.frame_count,
            "rate_hz": report.rate_hz,
            "dlc": report.dlc,
            "hypotheses_count": len(report.hypotheses),
            "hypotheses": [
                {
                    "name": h.name,
                    "type": getattr(h, "htype", "SIGNAL"),
                    "start_bit": h.start_bit,
                    "length": h.length,
                    "confidence": h.confidence,
                    "unit": getattr(h, "unit", ""),
                }
                for h in report.hypotheses
            ],
        }

    def discovery_analyze_all(self) -> dict[str, Any]:
        """Analyze all discovered arbitration IDs."""
        reports = self.discovery_engine.analyze_all()
        return {
            (f"{k[0]}:{'ext' if k[1] else 'std'}:0x{k[2]:03X}" if isinstance(k, tuple) else f"0x{int(k):03X}"): {
                "frame_count": r.frame_count,
                "rate_hz": r.rate_hz,
                "hypotheses_count": len(r.hypotheses),
            }
            for k, r in reports.items()
        }

    def discovery_export_dbc(self, approved_only: bool = False) -> dict[str, Any]:
        """Export DBC string for discovered signals."""
        try:
            db = self.discovery_engine.build_dbc(approved_only=approved_only)
            dbc_text = db.as_dbc_string()
            return {"success": True, "dbc": dbc_text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def discovery_clear(self) -> dict[str, Any]:
        """Clear discovery buffer."""
        self.discovery_engine.clear()
        return {"success": True}

    # ------------------------------------------------------------------
    # OEM J1939 Registry Methods
    # ------------------------------------------------------------------
    def oem_list_decoders(self) -> list[str]:
        """List all active OEM proprietary decoders."""
        return self.oem_registry.list_decoders()

    # ------------------------------------------------------------------
    # ReplayBus Trace Methods
    # ------------------------------------------------------------------
    def load_replay(self, file_path: str) -> dict[str, Any]:
        """Load a trace file (.asc, .csv, .blf) into ReplayBus.

        C-1: the path arrives from the renderer over IPC. It is validated
        against the app-owned replay roots (plus UNC/device fail-closed
        refusal) before ``Path.resolve()`` touches the filesystem, so a
        hostile renderer can no longer trigger an SMB connect or read an
        arbitrary file. On rejection ``replay_bus`` is left untouched.
        """
        try:
            path = DesktopApiBridge._validate_replay_path(file_path)
            bus = ReplayBus.from_trace_file(path)
            self.replay_bus = bus
            return {"success": True, "frame_count": bus.frame_count, "path": str(path)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def start_replay(self, speed: float = 1.0, loop: bool = False) -> dict[str, Any]:
        """Start trace replay feeding into the live ingestion pipeline."""
        if self.replay_bus is None:
            return {"success": False, "error": "ReplayBus yüklenmedi (önce load_replay çağırın)."}
        if self._replay_thread and self._replay_thread.is_alive():
            return {"success": False, "error": "Replay zaten çalışıyor."}

        self._replay_stop_event.clear()
        safety_filter = ReplaySafetyFilter()
        self.replay_safety_filter = safety_filter

        def _safe_replay_callback(frame: CanFrame) -> None:
            filtered = safety_filter.filter_frame(frame)
            if filtered is not None:
                self._ingest_live_frame(filtered)

        def _worker() -> None:
            assert self.replay_bus is not None
            self.replay_bus.play(
                callback=_safe_replay_callback,
                speed=speed,
                stop_event=self._replay_stop_event,
                loop=loop,
            )

        self._replay_thread = threading.Thread(target=_worker, name="replay_bus", daemon=True)
        self._replay_thread.start()
        return {"success": True}

    def stop_replay(self) -> dict[str, Any]:
        """Stop trace replay."""
        self._replay_stop_event.set()
        return {"success": True}

    # ------------------------------------------------------------------
    # ECU Flashing Engine & Progress Subsystem
    # ------------------------------------------------------------------
    @staticmethod
    def _flash_identity_waiver_allowed() -> bool:
        """S1-P1-6: may a flash skip the target VIN/serial binding?

        Only in an explicitly-flagged LAB environment. Previously
        `skipTargetIdentity` was a plain key in the JS bridge `flash_start`
        payload, so a compromised renderer / open devtools / XSS could send
        ``pywebview.api.flash_start({..., skipTargetIdentity: true}, nonce)``
        and drop the VIN/serial binding. The Ed25519 firmware signature would
        still verify, so a correctly-signed image could be written to the WRONG
        ECU on the same OEM trust anchor (fleet vehicle) — a silent bricking
        and safety hazard.

        Both environment variables are required, so this cannot be triggered
        by renderer input alone; it is an operator/lab decision made before the
        process starts.
        """
        return (
            os.environ.get("UCANLAB_FLASH_SKIP_IDENTITY") == "1"
            and os.environ.get("UCANLAB_TEST_MODE") == "1"
        )

    @classmethod
    def _validate_flash_prerequisites(cls, config: dict[str, Any]) -> str | None:
        """R2-P1: synchronous flash precondition check (runs BEFORE arm_tx).

        Returns an error string when the UI-supplied material cannot satisfy
        the motor's fail-closed gates (signature / trust anchor / target
        identity), else None. The caller refuses synchronously so the bus is
        never armed for a flash that is doomed to fail.

        S1-P1-6: the identity waiver is NOT honored from the bridge config.
        It requires the out-of-band lab environment gate
        (`_flash_identity_waiver_allowed`); a `skipTargetIdentity: true` key
        arriving from the renderer is ignored and the identity requirement
        stays enforced.
        """
        if not config.get("firmwareSignature") and not config.get("firmware_signature"):
            return "Flashing ön-koşulu sağlanamadı: firmware imzası (firmwareSignature) gerekli."
        if not config.get("trustedPubkey") and not config.get("trusted_pubkey"):
            return "Flashing ön-koşulu sağlanamadı: güvenilir ortak anahtar (trustedPubkey) gerekli."
        identity_waived = config.get("skipTargetIdentity") is True and cls._flash_identity_waiver_allowed()
        if identity_waived and config.get("skipTargetIdentity") is True:
            logger.warning(
                "Flash target-identity check waived by LAB environment gate "
                "(UCANLAB_FLASH_SKIP_IDENTITY=1 + UCANLAB_TEST_MODE=1)",
            )
        if not (
            config.get("expectedVin") or config.get("expected_vin")
            or config.get("expectedSerial") or config.get("expected_serial")
            or identity_waived
        ):
            return "Flashing ön-koşulu sağlanamadı: hedef VIN/seri (expectedVin) gerekli."
        return None

    @staticmethod
    def _parse_flash_signature(config: dict[str, Any]) -> bytes | None:
        raw = config.get("firmwareSignature", config.get("firmware_signature"))
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        try:
            return bytes.fromhex(str(raw).strip())
        except ValueError:
            return None

    @staticmethod
    def _parse_flash_pubkey(config: dict[str, Any]) -> Any | None:
        raw = config.get("trustedPubkey", config.get("trusted_pubkey"))
        if raw is None:
            return None
        if not isinstance(raw, str):
            return raw
        try:
            return ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(raw.strip()))
        except Exception:
            return None

    def flash_start(self, config: dict[str, Any], confirmation_token: str | None = None) -> dict[str, Any]:
        """Start ECU reprogramming via EcuFlashingEngine with challenge verification."""
        if not isinstance(config, dict):
            return {"success": False, "error": "Geçersiz flash konfigürasyonu (dict bekleniyor)."}

        # 1. Safety Checks
        if self._is_estop or self.estop.is_engaged:
            err = "Acil Durdurma (E-Stop) devrede! Flashing başlatılamaz."
            return {"success": False, "error": err, "message": err}

        # 1b. Speed Interlock (REVIEW 3: gateway = single authoritative source in
        # physical mode; simulation sandbox uses its own scenario mirror and
        # by design cannot authorize TX onto a live bus, P0-2.)
        if self._is_simulating:
            if not math.isfinite(self._current_speed_kmh) or self._current_speed_kmh != 0.0:
                err = f"Güvenlik Kilidi: Araç hareketsiz (0.0 km/s) olmalıdır (Mevcut hız: {self._current_speed_kmh:.1f} km/s)."
                return {"success": False, "error": err, "message": err}
        else:
            speed_state, speed_val = self.gateway.speed_interlock_state()
            if speed_state == "stale":
                err = "Güvenlik Kilidi: Araç hızı bilinmiyor veya güncel değil (fiziksel CCVS akışı yok/NaN)."
                return {"success": False, "error": err, "message": err}
            if speed_state == "moving":
                err = f"Güvenlik Kilidi: Araç hareketsiz (0.0 km/s) olmalıdır (Mevcut hız: {speed_val:.1f} km/s)."
                return {"success": False, "error": err, "message": err}

        # 2. Dual confirmation token check
        # H-1b: accept the token ONLY from the explicit `confirmation_token`
        # parameter — never from the caller-supplied `config` dict (which
        # would let the caller satisfy its own independent-confirmation
        # invariant via config["confirmation_token"] / config["token"]).
        token_candidate = confirmation_token if isinstance(confirmation_token, str) else None
        try:
            valid, reason = self._verify_and_consume_diagnostic_token(
                token_candidate,
                action_type=str(config.get("action_type") or "ecu_flash"),
                action_id=str(config.get("id") or ""),
                action_payload=config,
            )
        except TypeError:
            valid, reason = self._verify_and_consume_diagnostic_token(
                token_candidate,
                action_type=str(config.get("action_type") or "ecu_flash"),
                action_id=str(config.get("id") or ""),
            )
        if not valid:
            return {"success": False, "error": reason, "message": reason}

        with self._flash_lock:
            if self._flash_progress_state.get("status") == "in_progress":
                return {"success": False, "error": "Zaten devam eden bir flash işlemi mevcut."}

            self._flash_progress_state = {
                "status": "in_progress",
                "percent": 0.0,
                "step": "INIT",
                "step_index": 1,
                "bytes_transferred": 0,
                "total_bytes": int(config.get("sizeBytes") or config.get("size") or 1024),
                "speed_kbps": 0.0,
                "elapsed_s": 0.0,
                "logs": ["[INIT] Flashing başlatıldı..."],
                "error": None,
            }

        # Simulation mode branch
        if self._is_simulating:
            def _sim_flash_worker() -> None:
                total_bytes = self._flash_progress_state["total_bytes"]
                start_t = time.monotonic()
                steps = [
                    (FlashingStep.SAFETY_VALIDATION, 1, "Hız ve güvenlik kilitleri doğrulandı"),
                    (FlashingStep.EXTENDED_SESSION, 2, "Genişletilmiş Diyagnostik Oturumu (0x10 0x03)"),
                    (FlashingStep.SECURITY_ACCESS, 3, "Güvenlik Erişimi (0x27 Seed-Key)"),
                    (FlashingStep.PROGRAMMING_SESSION, 4, "Programlama Oturumu (0x10 0x02)"),
                    (FlashingStep.REQUEST_DOWNLOAD, 5, "İndirme Talebi (0x34)"),
                    (FlashingStep.TRANSFER_DATA, 6, "Veri Transferi (0x36 Blokları)"),
                    (FlashingStep.TRANSFER_EXIT, 7, "Transfer Tamamlama (0x37)"),
                    (FlashingStep.CHECKSUM_VERIFICATION, 8, "Bütünlük Doğrulama (CRC32/SHA-256)"),
                    (FlashingStep.ECU_RESET, 9, "ECU Donanımsal Reset (0x11 0x01)"),
                    (FlashingStep.COMPLETED, 10, "Flashing Tamamlandı"),
                ]
                for step, idx, desc in steps:
                    time.sleep(0.3 / max(0.5, self._speed_mult))
                    with self._flash_lock:
                        if self._flash_progress_state.get("status") == "cancelled":
                            self._flash_progress_state["logs"].append("[ABORT] Flashing iptal edildi.")
                            self._push_flash_progress()
                            return
                        pct = round((idx / len(steps)) * 100.0, 1)
                        now = time.monotonic()
                        bytes_transferred = int((idx / len(steps)) * total_bytes)
                        self._flash_progress_state.update({
                            "percent": pct,
                            "step": step.name,
                            "step_index": idx,
                            "bytes_transferred": bytes_transferred,
                            "elapsed_s": round(now - start_t, 2),
                            "speed_kbps": round((bytes_transferred / 1024.0) / max(0.01, now - start_t), 2),
                        })
                        self._flash_progress_state["logs"].append(f"[{step.name}] {desc}")
                    self._push_flash_progress()

                with self._flash_lock:
                    self._flash_progress_state["status"] = "completed"
                    self._flash_progress_state["logs"].append("✅ [COMPLETED] Flashing simülasyonu başarıyla tamamlandı.")
                self._push_flash_progress()

            self._flash_thread = threading.Thread(target=_sim_flash_worker, name="sim_flasher", daemon=True)
            self._flash_thread.start()
            return {"success": True, "message": "Flashing işlemi başlatıldı."}

        # Real physical mode branch
        def _on_progress(prog: FlashingProgress) -> None:
            with self._flash_lock:
                self._flash_progress_state.update({
                    "percent": prog.percent,
                    "step": prog.current_step.name,
                    "step_index": prog.step_index,
                    "bytes_transferred": prog.bytes_transferred,
                    "total_bytes": prog.total_bytes,
                    "speed_kbps": prog.transfer_speed_kbps,
                    "elapsed_s": prog.elapsed_time_s,
                    "crc32": prog.crc32_checksum,
                })
            self._push_flash_progress()

        def _on_log(msg: str, level: str) -> None:
            with self._flash_lock:
                self._flash_progress_state["logs"].append(f"[{level.upper()}] {msg}")
            self._push_flash_log(msg, level)

        if self.supervisor.current_state == SafetyState.PASSIVE:
            # R2-P1: synchronous precondition check BEFORE arming — the motor
            # fail-closes on missing signature/trust-anchor/target-identity,
            # so arming first would leave the bus TX-capable for a flash that
            # can never start. Validate the UI-supplied material here.
            _pre_err = self._validate_flash_prerequisites(config)
            if _pre_err is not None:
                with self._flash_lock:
                    self._flash_progress_state["status"] = "failed"
                    self._flash_progress_state["error"] = _pre_err
                return {"success": False, "error": _pre_err, "message": _pre_err}
            arm_res = self.arm_tx(reason="Operator started ECU flashing")
            if not arm_res.get("success", False):
                err = arm_res.get("error", "TX pipeline cannot be armed for flashing")
                with self._flash_lock:
                    self._flash_progress_state["status"] = "failed"
                    self._flash_progress_state["error"] = err
                return {"success": False, "error": err, "message": err}

        uds_client = self.create_uds_client()
        self.flashing_engine = EcuFlashingEngine(
            uds_client=uds_client,
            gateway=self.gateway,
            on_progress=_on_progress,
            on_log=_on_log,
            # R2-P2: the flasher mints step tokens through the
            # composition-root-owned issuer, never the gateway directly.
            confirmation_token_factory=self.gateway.create_confirmation_issuer(),
        )

        raw_data = config.get("data")
        if isinstance(raw_data, str):
            try:
                payload_bytes = bytes.fromhex(raw_data)
            except ValueError:
                payload_bytes = raw_data.encode("latin-1")
        elif isinstance(raw_data, (bytes, bytearray)):
            payload_bytes = bytes(raw_data)
        else:
            payload_bytes = b"\x00" * int(config.get("sizeBytes", 1024))

        flash_cfg = FlashingConfig(
            memory_address=int(config.get("memoryAddress", 0x80000)),
            data=payload_bytes,
            block_size=int(config.get("blockSize", 256)),
            # R2-P3: operator approval is taken ONCE at flash_start entry via
            # the diagnostic nonce challenge above; this flag forwards that
            # session-level approval to the motor (NOT a per-step UI prompt).
            user_confirmed=True,
            # R2-P1: feed the motor's mandatory gates from the UI config —
            # without these the real-mode flash fail-closes by design.
            firmware_signature=self._parse_flash_signature(config),
            trusted_pubkey=self._parse_flash_pubkey(config),
            expected_vin=config.get("expectedVin", config.get("expected_vin")),
            expected_serial=config.get("expectedSerial", config.get("expected_serial")),
            # S1-P1-6: the identity waiver needs the out-of-band LAB env gate;
            # a renderer-supplied `skipTargetIdentity: true` no longer relaxes
            # this motor gate (it would let a correctly-signed image be
            # written to the wrong ECU on the same OEM trust anchor).
            require_target_identity=not (
                config.get("skipTargetIdentity") is True and self._flash_identity_waiver_allowed()
            ),
        )

        def _real_flash_worker() -> None:
            try:
                assert self.flashing_engine is not None
                success = self.flashing_engine.execute_flash(flash_cfg)
                with self._flash_lock:
                    self._flash_progress_state["status"] = "completed" if success else "failed"
                    if not success:
                        self._flash_progress_state["error"] = "Flashing başarısız oldu."
            except Exception as exc:
                with self._flash_lock:
                    self._flash_progress_state["status"] = "failed"
                    self._flash_progress_state["error"] = str(exc)
                    self._flash_progress_state["logs"].append(f"[ERROR] {exc}")
            finally:
                # T62-M2: Flashing exit must disarm TX and verify listen-only driver mode
                cleanup_ok = self._safe_disarm_after_flash()
                with self._flash_lock:
                    if not cleanup_ok:
                        self._flash_progress_state["status"] = "cleanup_failed"
                        self._flash_progress_state["error"] = "TX cleanup failed after flashing"
                        self._flash_progress_state["logs"].append("[CRITICAL] TX cleanup failed after flash!")
                self._push_flash_progress()

        self._flash_thread = threading.Thread(target=_real_flash_worker, name="real_flasher", daemon=True)
        self._flash_thread.start()
        # R2-P4: the worker validates asynchronously — report ACCEPTANCE, not success.
        return {"success": True, "accepted": True, "message": "Flashing isteği kabul edildi (ön-koşullar doğrulandı, işlem sürüyor)."}

    def _safe_disarm_after_flash(self) -> bool:
        """Ensure TX is disarmed and driver is restored to listen-only after flashing (T62-M2)."""
        cleanup_ok = True
        try:
            if not self._is_simulating:
                res = self.disarm_tx(reason="Flashing finished or terminated — restoring listen-only")
                if not res.get("success", False):
                    cleanup_ok = False
                with self._bus_lock:
                    if self.bus is not None and not getattr(self.bus, "listen_only", True):
                        if not self._set_driver_listen_only(True):
                            cleanup_ok = False
        except Exception as exc:
            logger.error("Exception during post-flash TX disarm cleanup: %s", exc)
            cleanup_ok = False

        if not cleanup_ok:
            logger.critical("Post-flash TX disarm cleanup failed — forcing FAULT and E-Stop")
            try:
                self.supervisor._force_fault("FLASH_TX_CLEANUP_FAILED")
            except Exception:
                pass
            try:
                self.estop.trigger(EStopTriggerSource.UNAUTHORIZED_PAYLOAD, "FLASH_TX_CLEANUP_FAILED")
            except Exception:
                pass
        return cleanup_ok

    def _push_flash_progress(self) -> None:
        if self._window is None:
            return
        try:
            with self._flash_lock:
                state_copy = dict(self._flash_progress_state)
            self._window.evaluate_js(f"if (window.onFlashProgress) window.onFlashProgress({json.dumps(state_copy)});")
        except Exception:
            pass

    def _push_flash_log(self, msg: str, level: str) -> None:
        if self._window is None:
            return
        try:
            payload = json.dumps({"message": msg, "level": level})
            self._window.evaluate_js(f"if (window.onFlashLog) window.onFlashLog({payload});")
        except Exception:
            pass

    def flash_progress(self) -> dict[str, Any]:
        """Get live flashing progress state."""
        with self._flash_lock:
            return dict(self._flash_progress_state)

    def flash_cancel(self) -> dict[str, Any]:
        """Cancel live flashing operation."""
        with self._flash_lock:
            self._flash_progress_state["status"] = "cancelled"
            if self.flashing_engine is not None:
                self.flashing_engine.cancel()
            self._flash_progress_state["logs"].append("[CANCEL] Flashing kullanıcı tarafından iptal edildi.")
        if self._flash_thread is None or not self._flash_thread.is_alive():
            cleanup_ok = self._safe_disarm_after_flash()
            if not cleanup_ok:
                with self._flash_lock:
                    self._flash_progress_state["status"] = "cleanup_failed"
        self._push_flash_progress()
        return {"success": True, "message": "Flashing iptal edildi."}

    _OP_MEASUREMENT_RE = re.compile(
        r"(?:test sonucu|ölçüm|olcum|measurement)\s*[:=]?\s*([^=:]+?)\s*=\s*(-?\d+(?:[.,]\d+)?)\s*(.*)",
        re.IGNORECASE,
    )

    def _parse_operator_measurement_query(self, query: str) -> tuple[str, float] | None:
        """Extract 'test sonucu: <name> = <value>' operator measurements (FAZ 5).

        Deterministic pattern match only — no LLM, no foreign-text command
        scanning (trigger-source isolation invariant). Returns None when the
        query is not a measurement entry.
        """
        match = self._OP_MEASUREMENT_RE.search(query or "")
        if not match:
            return None
        name = match.group(1).strip()
        try:
            value = float(match.group(2).replace(",", "."))
        except ValueError:
            return None
        if not name:
            return None
        return name, value

    def query_copilot(self, query: str) -> str:
        # FAZ 5 (interactive diagnosis): operator chat measurement entries
        # ("test sonucu: yağ basıncı = 0.4" style) are parsed HERE (host
        # layer) and recorded into the evidence session; the AI layer never
        # writes the session (closed evidence chain, plan §FAZ 5.2).
        measurement = self._parse_operator_measurement_query(query)
        if measurement is not None:
            name, value = measurement
            res = self.record_operator_measurement(name, value)
            if res.get("success"):
                analysis = self.get_diagnostic_analysis()
                hyps = analysis.get("hypotheses", []) if analysis.get("success") else []
                lines = [f"ğŸ“ Operatör ölçümü kaydedildi: **{res.get('recorded')} = {value:g}** (kanıt tabanına %50 ağırlıkla eklendi)."]
                if hyps:
                    lines.append("")
                    lines.append("**Güncel hipotez sıralaması:**")
                    for i, h in enumerate(hyps, start=1):
                        lines.append(f"{i}. {h.get('fault', '')} — %{h.get('score', 0) * 100:.0f}")
                else:
                    lines.append("Hipotez üretilemedi (yetersiz kanıt / aktif DTC yok).")
                return "\n".join(lines)
            return f"âš ï¸ {res.get('error', 'Ölçüm kaydedilemedi.')}"

        # FAZ 5: "hipotezler" query — deterministic keyword gate on the
        # OPERATOR query (never foreign text), session analysis stays host-side.
        norm_q = (query or "").strip().lower()
        if norm_q in {"hipotezler", "hipotez", "hipotheses", "hipotez sıralaması", "hipotez siralamasi"}:
            analysis = self.get_diagnostic_analysis()
            if not analysis.get("success"):
                return "âš ï¸ Aktif teşhis oturumu yok."
            gate = analysis.get("gate", {})
            hyps = analysis.get("hypotheses", [])
            anomalies = analysis.get("anomalies", [])
            cases = analysis.get("similar_cases", [])
            lines = ["**Teşhis Oturumu Analizi** (ağırlıklı kanıt skorları):"]
            lines.append("")
            lines.append(f"- Kanıt kapısı: anomali {'✅ yeterli' if gate.get('anomaly_sufficient') else 'âŒ yetersiz'} | DTC/hipotez {'✅ yeterli' if gate.get('dtc_sufficient') else 'âŒ yetersiz'} ({gate.get('active_dtc_count', 0)} aktif DTC)")
            for gap in gate.get("gaps", []):
                lines.append(f"  - {gap}")
            if anomalies:
                lines.append("")
                lines.append("**Anomaliler:**")
                for a in anomalies:
                    tag = " (operatör beyanı)" if a.get("synthetic") else ""
                    lines.append(f"- {a.get('signal')}: {a.get('finding')}{tag}")
            if hyps:
                lines.append("")
                lines.append("**Hipotez sıralaması:**")
                for i, h in enumerate(hyps, start=1):
                    lines.append(f"{i}. {h.get('fault', '')} — %{h.get('score', 0) * 100:.0f} ağırlıklı kanıt skoru")
                    for s in h.get("supporting_evidence", []):
                        lines.append(f"   - destek: {s}")
                    for c in h.get("contradicting_evidence", []):
                        lines.append(f"   - çelişki: {c}")
            else:
                lines.append("")
                lines.append("Hipotez üretilmedi (yetersiz DTC/kanıt).")
            if cases:
                lines.append("")
                lines.append(f"**Benzer vakalar:** {analysis.get('similarity_label', '')}")
                for m in cases:
                    lines.append(f"- {m.get('case_id')} — %{m.get('similarity', 0) * 100:.0f}")
            return "\n".join(lines)

        # FAZ 2: Symptom matching when query matches known failure symptoms
        from src.engine.ai.symptom_mapper import map_symptoms_to_systems
        symptom_res = map_symptoms_to_systems(query)
        # F-07 (P1): prefer the REAL active DTCs from the live evidence session
        # over the scenario label table. `SCENARIO_DTCS` is a display/demo
        # mapping keyed by scenario name; using it as the diagnostic input meant
        # the chat reported a DTC that the vehicle had not actually raised (and
        # missed the ones it had). The scenario label remains a fallback ONLY
        # for the simulator with no real session evidence.
        dtc_list: list[str] = []
        session = self._diag_session
        if session is not None:
            dtc_list = [e.code for e in session.events if e.status == "ACTIVE" and e.code]
        if not dtc_list:
            dtc = self.SCENARIO_DTCS.get(self._active_scenario)
            if dtc:
                dtc_list = [dtc]

        if symptom_res.matched_symptoms and not dtc_list:
            s_lines = [f"ğŸ” **Semptom Tespiti:** '{query}'"]
            s_lines.append(f"- **Etkilenen Sistemler:** {', '.join(symptom_res.suspected_subsystems)}")
            s_lines.append(f"- **Olası DTC Adayları:** {', '.join(symptom_res.candidate_dtcs)}")
            s_lines.append("")
            s_lines.append("**Teşhisi daraltmak için başlangıç kontrol adımları:**")
            for i, q in enumerate(symptom_res.initial_questions, start=1):
                s_lines.append(f"{i}. {q}")
            return "\n".join(s_lines)

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
            future = self._copilot_executor.submit(_run_query)
            with self._copilot_inflight_lock:
                self._copilot_inflight = future
            try:
                return future.result(timeout=15.0)
            finally:
                # M-28 (P2-18): always drop the in-flight handle; on timeout
                # also try to cancel (a not-yet-started query is abandoned; a
                # running urlopen still ends by its own 10/12 s socket
                # timeout — the worker is not occupied indefinitely).
                with self._copilot_inflight_lock:
                    self._copilot_inflight = None
                if not future.done():
                    future.cancel()
        except FuturesTimeoutError:
            logger.warning("Copilot query timed out", extra={"query": query[:50]})
            return "âš ï¸ AI yanıtı zaman aşımına uğradı (15 s). Lütfen tekrar deneyin."

    def export_logs(self, fmt: str) -> bool:
        """Export session telemetry and frames to disk (LOW-4).

        Formats: json/csv (raw frames), mat (MATLAB) and mdf4/mf4 (ASAM MDF4)."""
        fmt_clean = fmt.strip().lower()
        if fmt_clean not in {"json", "csv", "mat", "mdf4", "mf4"}:
            logger.warning("Unsupported export format requested: %s", fmt)
            return False

        try:
            timestamp_str = time.strftime("%Y%m%d_%H%M%S")
            # L-12 (P3-8): anchored to the app data root (not the CWD).
            export_dir = _app_data_root() / "exports"
            export_dir.mkdir(parents=True, exist_ok=True)
            ext = "mf4" if fmt_clean in {"mdf4", "mf4"} else fmt_clean
            export_path = export_dir / f"can_session_{timestamp_str}.{ext}"

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
            elif fmt_clean == "mat":
                # P2-26: wire the MATLAB exporter into the live path.
                from src.engine.exporters.mat_exporter import MatExporter

                t0_ns = frames[0].timestamp_ns if frames else 0
                per_id: dict[int, tuple[list[float], list[float]]] = {}
                for f in frames:
                    ts_s = max(0.0, (f.timestamp_ns - t0_ns) / 1e9)
                    cur = per_id.setdefault(f.arbitration_id, ([], []))
                    cur[0].append(ts_s)
                    cur[1].append(float(f.arbitration_id))
                signals_data = {
                    f"arb_0x{arb:X}": (ts_list, val_list, "id")
                    for arb, (ts_list, val_list) in per_id.items()
                }
                if not signals_data:
                    logger.warning("Nothing to export: session ring buffer is empty")
                    return False
                MatExporter.export_signals(export_path, signals_data, exports_root=export_dir)
            elif fmt_clean in {"mdf4", "mf4"}:
                from src.engine.exporters.mdf4_exporter import Mdf4Exporter

                t0_ns = frames[0].timestamp_ns if frames else 0
                per_id_mdf: dict[int, tuple[list[float], list[float]]] = {}
                for f in frames:
                    ts_s = max(0.0, (f.timestamp_ns - t0_ns) / 1e9)
                    cur = per_id_mdf.setdefault(f.arbitration_id, ([], []))
                    cur[0].append(ts_s)
                    cur[1].append(float(f.arbitration_id))
                signals_data_mdf = {
                    f"CAN_ID_0x{arb:X}": (ts_list, val_list, "id")
                    for arb, (ts_list, val_list) in per_id_mdf.items()
                }
                if not signals_data_mdf:
                    logger.warning("Nothing to export: session ring buffer is empty")
                    return False
                Mdf4Exporter.export_signals(export_path, signals_data_mdf, exports_root=export_dir)

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
        if "cloudBaseUrl" in settings and settings["cloudBaseUrl"]:
            # H-10 (P1-8): the old path called set_base_url with NO host
            # allowlist — a renderer-supplied URL could redirect telemetry
            # uploads to an attacker endpoint. Apply the same bridge-level
            # allowlist as cloud_save_config (fail-closed on empty hostname).
            url = settings["cloudBaseUrl"]
            parsed = urllib.parse.urlsplit(str(url))
            allowed_domains = CANONICAL_CLOUD_HOSTS
            if (
                not parsed.hostname
                or parsed.scheme not in ("http", "https")
                or parsed.hostname not in allowed_domains
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
            # REVIEW (irreversible reconnect): commit the new bus only after
            # it actually connects WHEN the old channel is live — swapping
            # first and connecting later lost the working channel forever on
            # a failed connect(). If the old bus was never connected
            # (DEMO-only mode), a failed connect degrades to DEMO-only as
            # before and the settings change is still recorded.
            old_channel_live = bool(getattr(old_bus, "is_connected", False))
            if old_channel_live:
                try:
                    new_bus.connect()
                except Exception as exc:  # noqa: BLE001 — new settings must not kill the old link
                    logger.warning(
                        "New CAN bus rejected the settings; keeping previous bus",
                        extra={"interface": target_interface, "channel": target_channel, "error": str(exc)},
                    )
                    try:
                        new_bus.disconnect()
                    except Exception:
                        pass
                    return
            else:
                try:
                    new_bus.connect()
                except Exception as exc:  # noqa: BLE001 — was already DEMO-only; stay honest about it
                    logger.warning(
                        "CAN bus connect failed; DEMO-only mode",
                        extra={"interface": target_interface, "channel": target_channel, "error": str(exc)},
                    )

            self.bus = new_bus
            self.gateway.rebind_bus(self.bus)
            self.interface_val = target_interface
            self.channel_name = str(target_channel)
            self.bitrate_val = target_bitrate

            # REVIEW3 #41 (reconnect vs ARMED_TX): the reconnected bus is
            # opened listen-only (Safe-by-Default), but the supervisor state
            # used to survive the swap — ARMED_TX + a PASSIVE transceiver is
            # a dead protocol (gateway passes, driver raises HardwareError,
            # J1939 CTS/ACK and UDS responses die silently to T2/T3
            # timeouts). A settings change is a physical-channel change: TX
            # authority must be re-armed explicitly by the operator. Disarm
            # to PASSIVE fail-closed on every successful rebind.
            if self.supervisor.is_tx_permitted:
                try:
                    self.supervisor.transition_to(
                        SafetyState.PASSIVE, reason="bus reconnect — TX re-arm required"
                    )
                    logger.warning(
                        "Bus reconnected — supervisor disarmed to PASSIVE; operator must re-arm TX"
                    )
                except Exception as exc:  # noqa: BLE001 — disarm failure must not kill reconnect
                    logger.error(
                        "Supervisor disarm after reconnect failed — forcing FAULT",
                        extra={"error": str(exc)},
                    )
                    self.supervisor._force_fault("reconnect disarm failed")

            try:
                old_bus.disconnect()
            except Exception as exc:  # noqa: BLE001 — old handle cleanup is best-effort
                logger.debug("Old bus disconnect during reconnect failed", extra={"error": str(exc)})

            # B-25: Reset channel-bound state on bus switch
            self.ring_buffer.clear()
            self.j1939_tp = J1939TransportProtocol(my_address=0xF9, channel_id=self.channel_name)
            self.n2k_fp = Nmea2000FastPacketDecoder()

            logger.info(
                "CAN bus reconnected",
                extra={"interface": self.interface_val, "channel": self.channel_name, "bitrate": self.bitrate_val},
            )

    def _decode_j1939_payload(self, arb_id: int, source_address: int, data: bytes) -> None:
        """Decode a fully reassembled J1939 application message (no frame cap).

        REVIEW (DM1 truncation): mirrors `_decode_j1939_signal` but accepts
        the complete transport payload so multi-DTC DM1 broadcasts keep
        every 4-byte DTC record instead of the first 15.
        """
        try:
            pgn, sa, _da, _priority = parse_j1939_id(arb_id)
            if pgn == 65226 and len(data) >= 2:
                dm = J1939DiagnosticService.parse_dm1_or_dm2(
                    bytes(data), pgn=65226, source_address=sa, timestamp_ns=time.time_ns()
                )
                if dm is None:
                    return
                self._last_dm1 = {
                    "source": dm.source_address,
                    "dtc_count": len(dm.dtcs),
                    "lamps": bytes(data[:1]).hex(),
                }
                self._error_count = len(dm.dtcs)
        except (IndexError, ValueError, AttributeError) as exc:
            logger.debug("J1939 payload decode failed", extra={"error": str(exc)})

    def _decode_j1939_signal(self, frame: object) -> None:
        """Extract live telemetry from a routed J1939 frame (F-28).

        REVIEW3 #5: PGN extraction now goes through the shared, EDP/DP-
        preserving parser (`pgn_from_id`) — the old hand-rolled
        `(pf, ps)` bit math here (a) matched ET1 to PS=0xE1, which is
        PGN 65249 (Engine Hours, Revolutions), not ET1 (PGN 65262,
        PS=0xEE), rendering the engine-hours LSB as a fabricated coolant
        temperature, and (b) mis-filed EDP-set frames into the wrong PF/PS
        buckets, feeding the TX speed interlock with garbage.
        """
        try:
            arb = frame.arbitration_id  # type: ignore[attr-defined]
            data = frame.data  # type: ignore[attr-defined]
            pgn, sa, _da, _priority = parse_j1939_id(arb)

            # EEC1 (PGN 61444): engine speed + torque (B-10 sentinel filter)
            if pgn == 61444 and len(data) >= 5:
                raw_rpm = data[3] | (data[4] << 8)
                if raw_rpm < 0xFE00:  # 0xFE00..0xFFFF = Error / Not Available in J1939-71
                    self._current_rpm = raw_rpm * 0.125
                    self._record_signal_sample("EngineSpeed", raw_rpm, self._current_rpm, "rpm")
            # CCVS (PGN 65265): vehicle speed (SPN 84), 1/256 km/h per bit, bytes 1..2.
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
            elif pgn == 65265 and len(data) >= 3:
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
                            self._record_signal_sample("VehicleSpeed", raw_speed, speed_kmh, "km/h")
                        else:
                            self._current_speed_kmh = float("nan")
                            self.gateway.update_vehicle_speed(float("nan"), source="physical")
                    else:
                        logger.debug(
                            "CCVS frame from untrusted source address ignored",
                            extra={"sa": sa, "trusted_sa": self._ccvs_trusted_sa},
                        )
            # ET1 (PGN 65262 / 0xFEEE): engine coolant temperature (B-10
            # sentinel filter). REVIEW3 #5: the old PS=0xE1 guard matched
            # PGN 65249 (Engine Hours) and rendered its LSB as °C — a
            # fabricated overheat/under-temp reading. SAE J1939-71: ET1
            # SPN 110, byte 1, offset -40 °C.
            elif pgn == 65262 and len(data) >= 1:
                raw_temp = data[0]
                if raw_temp < 0xFE:  # 0xFE = Error, 0xFF = Not Available
                    self._current_temp = float(raw_temp) - 40.0
                    self._record_signal_sample("EngineCoolantTemp", raw_temp, self._current_temp, "C")
            # DM1 (PGN 65226): active diagnostic message
            elif pgn == 65226 and len(data) >= 2:
                dm = J1939DiagnosticService.parse_dm1_or_dm2(
                    bytes(data), pgn=65226, source_address=sa, timestamp_ns=time.time_ns()
                )
                # REVIEW hardening: short DM frames return None (fail-closed)
                # — never render a fabricated all-OFF lamp state.
                if dm is None:
                    return
                self._last_dm1 = {
                    "source": dm.source_address,
                    "dtc_count": len(dm.dtcs),
                    "lamps": bytes(data[:1]).hex(),
                }
                self._error_count = len(dm.dtcs)
                # FAZ 1 / Bulgu 1: DM1 SPN/FMI codes were parsed then dropped
                # (only dtc_count survived). Record them as DiagnosticEvents
                # now — severity from the SPN DB / KB, never invented.
                self._record_dm1_events(dm.dtcs)

            # OEM Proprietary J1939 Decoders (Cummins, Caterpillar, Scania, Volvo, Detroit, Actros)
            if isinstance(frame, CanFrame) and frame.is_extended:
                oem_payload = self.oem_registry.decode_frame(frame)
                if oem_payload is not None:
                    for sig in oem_payload.signals.values():
                        if not getattr(sig, "is_valid", True):
                            continue
                        raw_val = getattr(sig, "raw_value", None)
                        if isinstance(raw_val, int) and raw_val in (
                            0xFF, 0xFE, 0xFFFF, 0xFEFE, 0xFFFFFF, 0xFEFEFE, 0xFFFFFFFF, 0xFEFEFEFE
                        ):
                            continue
                        phys_val = getattr(sig, "physical_value", getattr(sig, "value", None))
                        self._record_signal_sample(sig.name, raw_val, phys_val, getattr(sig, "unit", ""))
                        sig_name_lower = sig.name.lower()
                        if "enginespeed" in sig_name_lower or "rpm" in sig_name_lower:
                            if isinstance(phys_val, (int, float)):
                                self._current_rpm = float(phys_val)
                        elif "coolant" in sig_name_lower or "enginetemp" in sig_name_lower:
                            if isinstance(phys_val, (int, float)):
                                self._current_temp = float(phys_val)
                        elif "boost" in sig_name_lower:
                            if isinstance(phys_val, (int, float)):
                                self._current_boost = float(phys_val)
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
        if isinstance(frame, CanFrame):
            self.discovery_engine.ingest_frame(frame)
            if self.rolling_disk is not None:
                try:
                    self.rolling_disk.append(frame)
                except Exception as exc:
                    logger.debug("RollingDiskBuffer frame ingestion failed", extra={"error": str(exc)})
        self._decode_j1939_signal(frame)

        # J1939 transport protocol reassembly (multi-packet) (B-12 drain)
        # REVIEW 2.1 (defense-in-depth): a frame sourced from a replayed
        # trace must NEVER elicit a physical TX response. ReplayBus frames
        # carry source="replay" (hal/replay/parsers.py). Replayed transport
        # frames are still reassembled for telemetry, but the TP engine is
        # told to suppress every outbound response — so even if the
        # ReplaySafetyFilter is mis-configured (e.g. tunneling blocking
        # disabled), a replayed RTS/FF cannot make this tool emit a CTS/FC
        # onto the live vehicle network.
        _is_replay_frame = getattr(frame, "source", "") == "replay"
        try:
            completed, resp = self.j1939_tp.handle_rx_frame(  # type: ignore[arg-type]
                frame, suppress_tx_responses=_is_replay_frame
            )
        except TypeError:
            # Backward-compat: a lightweight handle_rx_frame double with the
            # historical single-arg signature (test stubs, TxPort-shaped shims)
            # cannot express the suppression contract. Fall back to the plain
            # call and enforce suppression on the CALLER side below — the
            # replay frame must still never reach the gateway.
            completed, resp = self.j1939_tp.handle_rx_frame(frame)  # type: ignore[arg-type]
            if _is_replay_frame:
                resp = None
        if _is_replay_frame:
            # Fail-closed: drop any queued overflow responses as well.
            self.j1939_tp.take_pending_tx_frames()
        if resp is not None:
            try:
                # REVIEW HIGH-3: inbound-triggered protocol responses travel
                # the protocol_burst lane (a CTS/ACK burst from an RTS storm
                # must not trip the 100 msg/s default-lane wall) and are
                # marked inbound_triggered so a remote node cannot bait the
                # responder into a latched RATE_LIMIT_OVERFLOW E-Stop.
                self.gateway.validate_and_transmit(
                    resp, budget_category="protocol_burst", inbound_triggered=True
                )
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
                self.gateway.validate_and_transmit(
                    extra_resp, budget_category="protocol_burst", inbound_triggered=True
                )
            except Exception as exc:
                logger.warning(
                    "J1939 TP extra response physical transmit refused by gateway",
                    extra={"error": str(exc), "arbitration_id": getattr(extra_resp, "arbitration_id", None)},
                )
            self._log_tx_echo(extra_resp)

        if completed is not None:
            # REVIEW (DM1 truncation): the completed TP payload is an
            # APPLICATION message, not a transport frame — slicing it to a
            # 64-byte CAN-FD frame silently dropped every DTC record past
            # byte 64 (a 20-record DM1 lost 5). The full payload goes to the
            # diagnostic decoder directly; the synthetic frame is built
            # only for stream-shaped consumers and carries the first 64
            # bytes as a *view*, never as the source of record.
            # Reconstruct the canonical 29-bit CAN ID via the shared builder
            # — M-12 (P2-6): preserves EDP/DP bits (the old inline math
            # dropped EDP, mis-addressing EDP-set reassembled messages).
            arb_id = build_j1939_id(
                pgn=completed.pgn,
                sa=completed.source_address,
                da=completed.destination_address,
                priority=6,
            )
            # Full-payload application decode (DM1: every DTC record kept).
            self._decode_j1939_payload(arb_id, completed.source_address, completed.data)

            synth_data = completed.data[:64]
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

        # N2K Fast Packet reassembly — only Fast-Packet PGNs (see
        # FAST_PACKET_PGNS membership) yield completed messages now.
        # REVIEW 1-H3 (HIGH): single-frame PGNs are decoded DIRECTLY from
        # the frame below — previously they were fed through the fast-
        # packet filter, where 127488 (Engine Rapid: data[1] = RPM LSB,
        # e.g. idle 600 rpm -> 0x60 = 96 in 9..223) opened phantom 96-byte
        # sessions that swallowed the real RPM forever.
        n2k_msg = self.n2k_fp.handle_rx_frame(frame)  # type: ignore[arg-type]
        if n2k_msg is not None:
            self._decode_n2k_fast_payload(n2k_msg.pgn, n2k_msg.source_address, n2k_msg.data)

        # Single-frame N2K PGNs — direct 8-byte payload decode.
        try:
            _pgn, _sa, _da, _prio = parse_j1939_id(frame.arbitration_id)
        except Exception:  # noqa: BLE001
            _pgn = 0
        if _pgn == 127488 and len(frame.data) >= 3:
            # Engine Parameters, Rapid: EngineSpeed bytes 1..2, 0.25 rpm/bit
            raw_rpm = int.from_bytes(frame.data[1:3], "little")
            if raw_rpm < 0xFFFF:
                self._current_rpm = float(raw_rpm) * 0.25
                self._record_signal_sample("EngineSpeed", raw_rpm, self._current_rpm, "rpm")
        elif _pgn == 128267 and len(frame.data) >= 5:
            # Water Depth: Depth bytes 1..4, 0.01 m/bit
            raw_depth = int.from_bytes(frame.data[1:5], "little")
            if raw_depth < 0xFFFFFFFF:
                self._depth_meters = float(raw_depth) * 0.01
        elif _pgn == 127493 and len(frame.data) >= 2:
            # Transmission Parameters, Dynamic (single frame per canboat DBC)
            self._decode_n2k_single_transmission(frame.data)
        elif _pgn == 127505 and len(frame.data) >= 7:
            # Fluid Level (single frame; Instance low nibble, Type high nibble)
            fluid = Nmea2000PgnDecoder.decode_fluid_level(bytes(frame.data[:8]))
            if fluid is not None:
                self._record_signal_sample(
                    f"FluidLevel_{fluid.fluid_type}_{fluid.fluid_instance}",
                    int(round(fluid.level_percent * 250)) if fluid.level_percent is not None else 0xFFFF,
                    fluid.level_percent,
                    "percent",
                )

    def _decode_n2k_fast_payload(self, pgn: int, source_address: int, data: bytes) -> None:
        """Decode a completed NMEA 2000 Fast-Packet message (PGN 127489 etc.)."""
        if pgn == PGN_ENGINE_DYNAMIC:
            params = Nmea2000PgnDecoder.decode_engine_dynamic(data)
            if params is not None and params.engine_load_percent is not None:
                self._record_signal_sample(
                    "EngineLoad", params.engine_load_percent, float(params.engine_load_percent), "percent"
                )
            if params is not None and params.engine_torque_percent is not None:
                self._record_signal_sample(
                    "EngineTorque", params.engine_torque_percent, float(params.engine_torque_percent), "percent"
                )

    def _decode_n2k_single_transmission(self, data: bytes) -> None:
        """Decode single-frame PGN 127493 Transmission Parameters, Dynamic."""
        params = Nmea2000PgnDecoder.decode_transmission(data)
        if params is not None:
            self._record_signal_sample(
                f"TransmissionGear_{params.transmission_instance}",
                {"neutral": 0, "forward": 1, "reverse": 2, "unknown": 3}.get(params.gear, 3),
                params.gear,
                "enum",
            )

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

    def _observe_link_state(self, bus: object | None, *, drained: int) -> None:
        """Poll HAL link state each telemetry tick and report to the gateway.

        * BUS_OFF latched by the driver (128+ consecutive error frames) →
          ``gateway.notify_bus_off`` (Kontrol #23). Recovery (driver leaves
          BUS_OFF on the next good frame, M-30) clears the edge latch so a
          later bus-off re-fires.
        * RX silence beyond ``COMMUNICATION_TIMEOUT_NS`` → the gateway's
          ``notify_communication_timeout`` (Kontrol #44), which itself only
          engages while TX is permitted — a quiet listen-only bus is normal
          and never latches.

        Runs outside ``_bus_lock``; the gateway engages the E-Stop outside
        its own lock (snapshot-then-release, G-10). Latches clear ONLY on
        positive link evidence, never on a timer.
        """
        now_ns = time.monotonic_ns()
        if self._last_rx_monotonic_ns == 0:
            self._last_rx_monotonic_ns = now_ns
        metrics = getattr(bus, "metrics", None)
        state = getattr(metrics, "state", None)
        if state == BusState.BUS_OFF:
            self.gateway.notify_bus_off(
                "CAN controller BUS_OFF latched by HAL "
                f"(error_frames={getattr(metrics, 'error_frames', '?')})"
            )
        elif drained > 0:
            self.gateway.clear_link_fault(TxSafetyGateway.LINK_FAULT_BUS_OFF)
        if drained == 0 and (now_ns - self._last_rx_monotonic_ns) > TxSafetyGateway.COMMUNICATION_TIMEOUT_NS:
            self.gateway.notify_communication_timeout(
                f"No CAN RX for {(now_ns - self._last_rx_monotonic_ns) // 1_000_000} ms while link observed"
            )

    def _telemetry_loop(self) -> None:
        """Background loop: live CAN ingestion when connected, synthetic values in DEMO mode.

        Live path (F-28): bus -> FrameRouter -> decoders (J1939 / N2K) -> JS bridge.
        The watchdog heartbeat is NOT driven from here (F-16/E-11): the UI
        lease is only refreshed by the frontend render/rAF pulse.

        REVIEW3 #1 (forensic black-box window): an engaged E-Stop cuts TX
        ONLY. The live RX ingest / ring buffer / rolling-disk recording
        keeps running — the moments right after an E-Stop (bus-off
        cascades, keepalive timeouts, interlock trips) are exactly the
        evidence an investigation needs, and the old `continue` made the
        host recording go blind exactly then. Protocol TX responses are
        still refused fail-closed inside _ingest_live_frame by the
        gateway's E-Stop stage.
        """
        while self._running:
            time.sleep(0.05 / max(0.5, self._speed_mult))

            # ── LIVE path: real frames off the bus (F-28, B-07 exception protection) ──
            # Runs in BOTH normal and E-Stop states (recording must survive
            # the stop); only the DEMO generator is skipped while latched.
            if not self._is_simulating:
                drained = 0
                tick_frames: list[object] = []
                bus_snapshot: object | None = None
                try:
                    with self._bus_lock:
                        bus_snapshot = self.bus
                    # Perf (C-9): the first recv paces the tick against
                    # frame arrival; every subsequent drain call is
                    # non-blocking (timeout 0) so an empty queue costs ~0
                    # instead of a 10 ms park per frame at high speed mults.
                    while drained < 200:
                        frame = bus_snapshot.recv(timeout_s=0.01 if drained == 0 else 0.0)  # type: ignore[union-attr]
                        if frame is None:
                            break
                        self._ingest_live_frame(frame)
                        tick_frames.append(frame)
                        drained += 1
                except HardwareError as exc:
                    # LINK-FAULT #21: persistent interface I/O errors (hot-
                    # unplug, wedged vendor handle) fail CLOSED after a short
                    # streak — a single transient glitch must not latch the
                    # E-Stop. The gateway edge-triggers (one engagement per
                    # E-Stop epoch) and the supervisor FAULT revokes TX.
                    self._hw_error_streak += 1
                    if self._hw_error_streak >= self.HW_ERROR_ESTOP_AFTER:
                        self.gateway.notify_hardware_disconnect(
                            f"CAN interface I/O failure ({self._hw_error_streak} consecutive "
                            f"errors): {type(exc).__name__}: {str(exc)[:200]}"
                        )
                    time.sleep(0.1)
                    self._observe_link_state(bus_snapshot, drained=0)
                    continue
                except Exception as exc:  # noqa: BLE001 — loop must NEVER die on bus/driver errors
                    logger.warning("Telemetry frame ingestion error (recovering)", extra={"error": str(exc)})
                    time.sleep(0.1)
                    continue

                if drained > 0:
                    # Positive link evidence: reset the I/O-error streak and
                    # re-anchor the silence check; a live link clears its own
                    # timeout/disconnect latches (bus-off clears separately
                    # below once the driver leaves the BUS_OFF metrics state).
                    self._hw_error_streak = 0
                    self._last_rx_monotonic_ns = time.monotonic_ns()
                    self.gateway.clear_link_fault(TxSafetyGateway.LINK_FAULT_COMM_TIMEOUT)
                    self.gateway.clear_link_fault(TxSafetyGateway.LINK_FAULT_DISCONNECT)
                self._observe_link_state(bus_snapshot, drained=drained)

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
            # Skipped while E-Stop is latched: the operator must see the
            # REAL bus state (or nothing), never a fabricated live-looking
            # scenario behind a safety stop.
            if self._is_estop:
                continue
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
            # D3: this is a READ of a bundled asset -> resource root, not the
            # (deleted-on-exit) writable root.
            base_dir = _resource_root()
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

        # REVIEW (lifecycle coverage): watchdog start, bus connect, worker and
        # window creation all used to sit OUTSIDE the try/finally — an
        # escaping HardwareError (a PlatformError, NOT OSError/RuntimeError,
        # so the old except clause missed it) skipped every teardown step:
        # the watchdog monitor leaked and the driver handle stayed open.
        # One try/finally wraps every resource; domain errors degrade to
        # DEMO mode exactly like network errors.
        try:
            self.watchdog.start()
            # F-28: connect the real CAN bus before the ingestion loop starts
            try:
                self.bus.connect()
                logger.info("CAN bus connected for live ingestion", extra={"channel": self.channel_name})
            except Exception as exc:  # noqa: BLE001 — HardwareError/PlatformError degrade to DEMO, never crash startup
                logger.warning("CAN bus connect failed; running in DEMO-only mode", extra={"error": str(exc)})

            self._thread = threading.Thread(target=self._telemetry_loop, daemon=True)
            self._thread.start()

            # F6-1 (REVIEW Aşama 6): serve the frontend over a loopback-only
            # server that applies a strict Content-Security-Policy, instead of
            # loading it from file:// with no CSP and no navigation policy. The
            # renderer holds the pywebview bridge (TX / E-Stop / flash
            # authority), so an injected script or a navigation to a remote
            # origin must be blocked by policy, not by convention.
            from src.ui.frontend_server import FrontendServer

            self._frontend_server = FrontendServer(dist_html.parent)
            frontend_url = self._frontend_server.start()

            self._window = webview.create_window(
                title="Universal CAN-Bus Diagnostic & Telemetry Tool v13.0",
                url=frontend_url,
                js_api=api,
                width=1400,
                height=900,
                min_size=(1100, 700),
                frameless=True,
                easy_drag=False,
                transparent=False,
                background_color="#0c0e14",
                text_select=True,
            )

            webview.start(debug=False)
        finally:
            self._set_ui_state(_running=False)
            if getattr(self, "_frontend_server", None) is not None:
                self._frontend_server.stop()
                self._frontend_server = None
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
