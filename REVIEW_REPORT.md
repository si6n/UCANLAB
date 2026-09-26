# Universal CAN Diagnostic & Telemetry Platform v13.0.0 — Code Review Report

**Date:** 2026-09-26 | **Architecture:** Hexagonal (Ports & Adapters), ISO 26262 ASIL-B/D
**Scope:** Full repository — 129 source files (50,528 LOC) + 136 test files (49,643 LOC, 2,059 tests)

---

## 1. Executive Summary

**Overall assessment: PROCEED WITH CAUTION.** The safety subsystem is mature, adversarial-tested, and architecturally sound. The dominant structural risk is the Wiring Gate (AGENTS.md §2.7): six major subsystems carry verified-but-unwired defect fixes and are excluded from any production path today.

### Key Metrics

| Metric | Value |
|---|---|
| Source files | 129 (`src/`) |
| Source LOC | 50,528 |
| Largest module | `src/ui/desktop_app.py` (4,394 LOC) |
| Largest safety module | `src/safety/gateway.py` (1,828 LOC) |
| Test files | 136 |
| Test LOC | 49,643 |
| Test functions | 2,059 |
| E-Stop triggers | 10 |
| Safety states | 6 |
| Gateway policy stages | 6 + optional E2E stamping |
| Watchdog lease | 800 ms |
| IPC bridge methods | ~50 |
| OEM decoders | 6 |
| Unwired components | 6 |

### Top Findings (P0/P1)

1. **Wiring Gate is the #1 release risk.** Six subsystems are verified-but-unwired. Any PR that wires one live without addressing its remediation notes must be rejected.
2. **E2E safety packaging is live-wirable but not wired.** The gateway's E2E stamping stage is a no-op today.
3. **AI layer is correctly isolated.** `FORBIDDEN_AI_IMPORT_ROOTS` locks the offline invariant.
4. **Speed interlock provenance is correctly split.** Synthetic speed can never authorize a critical command.
5. **E-Stop reset is cryptographically gated and UI-safe.** Renderer cannot mint or shortcut reset tokens.
6. **Residual TOCTOU window documented, not hidden.**

---

## 2. Architecture Overview

### Layer Dependency Graph

```
src/core/          — Domain models, error hierarchy, port interfaces (zero framework deps)
src/hal/           — Hardware abstraction: AbstractBus, PythonCanBus, VirtualBus, RP1210Client
src/safety/        — Functional safety: TxSafetyGateway, EmergencyStopSystem, Watchdog, Supervisor
src/protocols/     — Automotive protocols: UDS, J1939, OBD-II, NMEA 2000, OEM decoders
src/engine/        — Telemetry pipeline: FrameRouter, DBC decoder, buffers, AI copilot, exporters
src/security/      — Cloud licensing, HWID, anti-tamper, knowledge pack encryption
src/launcher/      — Bootstrapping: preflight, auth, auto-updater
src/ui/            — React 18 + WebView2 desktop app with pywebview IPC bridge
```

### Critical Data Flow

```
CAN Bus (HAL)
  → FrameRouter (Pub-Sub)
    → Protocol engines (UDS, J1939, NMEA2000)
    → DBC Signal Decoder
    → BinaryRingBuffer / RollingDiskBuffer
    → AI Diagnostic Copilot (fully offline)
    → UI (via pywebview bridge)

TX Path (ALL outbound):
  Operator/Protocol → TxSafetyGateway (6-stage choke-point) → SafeMultiplexedBus → HAL Driver
```

### The 6-Stage Safety Choke-Point

| Stage | Name | Key behaviour |
|---|---|---|
| 1 | Frame Sanity & Range | Validates CanFrame type, 11/29-bit ID range, classic <=8 / FD <=64 byte payloads |
| 2 | Safety State & E-Stop | Refuses if supervisor not tx_permitted, watchdog lease expired, or E-Stop engaged |
| 3 | Whitelist Authorization (Fail-Closed) | Empty whitelist → WhitelistFailClosedError; miss streak >= 5 latches E-Stop |
| 4 | Speed Interlock | Critical commands require fresh (<=1.0 s) physical speed <=0.5 km/h |
| 5 | Dual Confirmation | HMAC confirmation_token mandatory when a secret is configured |
| 6 | Rate Budget | Per-category token buckets + global aggregate envelope (400 msg/s) |
| 6b (opt) | E2E Stamping | WIRING-GATED: only active when e2e_packager + e2e_profiles configured |

---

## 3. Safety Subsystem Deep Dive (`src/safety/` — 6,381 LOC)

### 3.1 `TxSafetyGateway` (`src/safety/gateway.py`, 1,828 LOC)

The single audited choke-point for ALL outbound transmissions:

- **Criticality is DERIVED, not asserted.** `_frame_is_critical` classifies a frame on its own evidence: 11-bit ISO-TP service byte in `CRITICAL_UDS_SIDS`, 29-bit `0x18DAxxxx` ISO-TP path, J1939 `CRITICAL_J1939_PGNS`, and J1939 Request PGN 59904 inheriting the requested PGN's criticality. A caller flag can only TIGHTEN the gate, never relax it.
- **Canonical criticality catalogue** (`src/safety/criticality.py`, 92 LOC): single source of truth shared by the gateway AND the replay safety filter.
- **Provenance-split speed interlock** (P1/G-1): `_physical_speed_kmh` (interlock) vs `_display_speed_kmh` (UI-only). `record_synthetic_speed()` writes display only and can never authorize a critical command.
- **HMAC confirmation tokens** with payload/context binding. Single-use, TTL-bounded, expiry-aware FIFO prune.
- **`create_confirmation_issuer()`**: composition-root-owned mint capability so the flasher mints through an explicitly passed factory.
- **Link-fault observers**: edge-triggered, epoch-keyed suppression.
- **Mask breadth gate**: rejects zero/negative masks and masks covering >65,536 IDs unless explicitly granted.
- **TOCTOU-hardened dispatch**: PHASE-3 fence re-check under `estop.tx_send_lock`.

### 3.2 `EmergencyStopSystem` — 10 Triggers (`src/safety/estop.py`, 920 LOC)

`EStopTriggerSource`: USER_UI_BUTTON, BUS_OFF_DETECTED, KEEPALIVE_TIMEOUT, SPEED_INTERLOCK_BREACH, HARDWARE_DISCONNECT, RATE_LIMIT_OVERFLOW, UNAUTHORIZED_PAYLOAD, TEMPERATURE_OVERHEAT, PROCESS_TERMINATION, COMMUNICATION_TIMEOUT.

Key properties:
- **Mint/verify separation (P1-1):** `EmergencyStopSystem` refuses to mint reset tokens by default. Only `EStopResetAuthority`, configured with an INDEPENDENT `SecretProvider`, may mint.
- **Challenge-response reset**: 8-step verification — parse, anti-replay nonce check, epoch match, action allowlist, nonce match, TTL, backoff-aware shape gate, constant-time HMAC-SHA256.
- **Backoff**: exponential capped at 5.0 s; first consecutive failure does NOT arm cooldown.
- **Secret caching (P5/E-2):** HMAC secret resolved ONCE at construction; `_get_secret()` reads the cache only.

### 3.3 `SafetySupervisor` — 6 States (`src/safety/state_machine.py`, 653 LOC)

`SafetyState`: STARTUP → SAFE → PASSIVE → ARMED_TX → ACTIVE → FAULT.

- **ALLOWED_TRANSITIONS**: FAULT may only transition to PASSIVE/SAFE — NEVER directly to ARMED_TX/ACTIVE.
- **Fail-open boot guard**: ARMED_TX/ACTIVE refused as `initial_state`.
- **E-Stop guard**: any transition to PASSIVE/ARMED_TX/ACTIVE while `estop.is_engaged` raises `ESTOP_ENGAGED`.
- **TX authorization gate (T41/S-1)**: `arm_tx`/`activate_tx` require a valid single-use HMAC arm token when `auth_secret` is configured.
- **`trigger_fault` is unconditional**: the 5/s rate limiter only thins LOG chatter; the transition ALWAYS runs.

### 3.4 `TxWatchdogSupervisor` — 800 ms Lease (`src/safety/watchdog.py`, 325 LOC)

- `DEFAULT_TIMEOUT_MS = 800.0`, `CHECK_INTERVAL_SEC = 0.050`.
- **H-2 heartbeat token**: `heartbeat()` refuses anonymous/mismatched pulses with `PermissionError`.
- **Lease anchored only on first start**; restarts never refresh it.
- **`stop()` escalation**: if the monitor survives the 1.0 s join, it independently triggers `supervisor.trigger_fault` + `estop.trigger(KEEPALIVE_TIMEOUT)`.

### 3.5 E2E Validator & Packager (`src/safety/e2e/`)

| File | LOC | Role |
|---|---|---|
| `crc.py` | 174 | CRC-8 0x1D (J1850/P1) and 0x2F (P2/MQB) lookup tables |
| `profiles.py` | 422 | AUTOSAR Profile 1, Profile 2, J1850, Toyota, VAG MQB, Volvo |
| `validator.py` | 267 | `E2ESafetyValidator` — verdicts OK/REPEATED/SOME_LOST/WRONG_SEQUENCE/CRC_ERROR/INITIAL/LENGTH_ERROR |
| `packager.py` | 230 | `E2ESafetyPackager` — Tx sealing with rewind-rejecting counter resolution |

**WIRING STATUS: UNWIRING-GATED.** The production composition root wires NO `e2e_profiles`; the gateway's E2E stamping stage is a no-op today.

### 3.6 Criticality Catalogue (`src/safety/criticality.py`, 92 LOC)

- `CRITICAL_UDS_SIDS` (16 SIDs): 0x10, 0x11, 0x14, 0x27, 0x28, 0x2E, 0x2F, 0x31, 0x34, 0x35, 0x36, 0x37, 0x38, 0x3D, 0x85, 0x87
- `CRITICAL_J1939_PGNS`: 65235 (DM11), 65228 (DM3), 65229 (DM4), 65230 (DM5), 65240 (Commanded Address), 0 (TSC1), 1024 (XBR)
- `J1939_REQUEST_PGN`: 59904

---

## 4. Hardware Abstraction Layer Analysis (`src/hal/`)

### 4.1 `AbstractBus` (`src/hal/base.py`, 130 LOC)

- `BusState` enum: ACTIVE/PASSIVE/BUS_OFF/DISCONNECTED/ERROR/STOPPED.
- `BusMetrics`: separates `rtr_frames` from `error_frames`.
- Single canonical TX entry: `send()` is abstract; `privileged_send()` is the gateway-dispatch port; `_send_raw()` is a recursion-safe shim.

### 4.2 `PythonCanBus` (`src/hal/drivers/pcan_kvaser.py`, 441 LOC)

Multi-vendor `python-can` wrapper supporting PCAN, Kvaser, Vector, SocketCAN. Enforces protected frame dispatch and CAN-FD/BRS handling.

### 4.3 `VirtualBus` + `UdsServerEcu` (`src/hal/virtual.py`, 409 LOC)

- **`VirtualBus`**: thread-safe in-memory bus. Bounded TX transcript (`deque(maxlen=10_000)`) and RX queue. `listen_only` gate enforced on TX. `recv` honours `timeout_s=None` (block indefinitely) vs `0` (non-blocking poll).
- **`UdsServerEcu`**: simulated ISO 14229 ECU state machine — Sessions, SecurityAccess (Seed-Key), Memory Read/Write/Download/Upload, Routines, DTC Information, NRC generation. Full UDS service coverage: 0x10, 0x11, 0x14, 0x19, 0x22, 0x23, 0x27, 0x2E, 0x2F, 0x31, 0x34, 0x35, 0x36, 0x37, 0x3E, 0x38.

### 4.4 `RP1210Bus` + `RP1210Client` (`src/hal/rp1210/`)

- **`RP1210Client`** (client.py, 437 LOC): TMC RP1210 A/B/C 64-bit isolated ctypes wrapper. DLL name validated against allowlist; system directories resolved via `GetSystemDirectoryW`/`GetWindowsDirectoryW` (NOT `WINDIR` env); required entry points verified at load; strict ctypes argtypes/restype for all exports; pre-allocated 4096-byte RX scratch buffer; `read_message` uses `ret > 0` as byte count.
- **`RP1210Bus`** (bus.py, 465 LOC): adapts the client to `AbstractBus`, publishes `BusMetrics`, handles BUS_OFF latch and link state.

### 4.5 Replay Parsers & Player (`src/hal/replay/`)

- **`parsers.py`** (454 LOC): `.asc` (Vector ASCII), `.blf` (Binary Log Format), `.csv` parsers.
- **`player.py`**: `ReplayBus` — time-scaled frame injection into a `VirtualBus` or live bus.
- **`safety_filter.py`** (460 LOC): blocks replay of `PROHIBITED_UDS_SIDS` and critical J1939 PGNs, enforcing the same criticality catalogue as live TX.

### 4.6 Power Management (`src/hal/power/win32_power.py`)

Windows power-profile awareness (AC/battery) to gate aggressive polling rates on battery.

---

## 5. Protocol Layer Analysis (`src/protocols/` — 15,480 LOC)

### 5.1 UDS Client (`src/protocols/uds/`)

| File | LOC | Role |
|---|---|---|
| `client.py` | 1,013 | ISO 14229 `UdsClient` — full service coverage (0x10..0x3E) |
| `isotp.py` | 1,402 | ISO 15765-2 DoCAN transport: SF/FF/CF/FC, 11-bit & 29-bit addressing, Flow Control with STmin pacing, CAN-FD extended-length escape |
| `flasher.py` | 1,119 | `EcuFlashingEngine` — **WIRED** since TUR-2 R2: step tokens mint through `gateway.create_confirmation_issuer()`; all TX flows through the `TxSafetyGateway` choke-point |
| `did_database.py` | 762 | Standard ISO 14229 DID database (0xF190 VIN, 0xF188 SW, 0xF191 HW, 0xF187 Part No, 0xF197 System Name, 0xF1A0..0xF1AF) |
| `firmware.py` | 542 | Firmware image parsing & integrity |
| `services.py` | — | `UdsServiceId`, `DiagnosticSessionType`, `ReadDtcInformationType`, `RoutineControlType` |
| `nrc.py` | — | `UdsNrc` negative response codes |

### 5.2 J1939 Transport & Diagnostics (`src/protocols/j1939/`)

- **`transport.py`** (1,671 LOC): SAE J1939-21 Transport Protocol — BAM (Broadcast Announce Message) and RTS/CTS state machines; PGN 60416 (TP.CM) and PGN 60160 (TP.DT); multi-packet reassembly up to 255 packets.
- **`diagnostics.py`**: J1939-73 Diagnostic Messages DM1..DM11.
- **`address_claim.py`** (521 LOC): J1939-81 Address Claiming — **UNWIRING-GATED**.
- **`pgn.py`**: `build_j1939_id` / `parse_j1939_id` with PGN mask matching; Proprietary A (PGN 61184 / 0xEF00) and Proprietary B (PGN 65280-65535 / 0xFF00-0xFFFF) dispatch.
- **`sentinel.py`**: J1939 sentinel-bounded plausibility checks for telemetry.

### 5.3 OBD-II (`src/protocols/obd/`)

- **`pids.py`** (1,441 LOC): Full SAE J1979 Mode 01 PID registry (0x00..0xFF), bitmask decoders, physical conversion formulas, scaling, units, min/max.
- **`poller.py`** (1,078 LOC): `ActiveDiagnosticPoller` — configurable periodic scheduler (10/5/1 Hz), prioritization, TxPort integration. **UNWIRING-GATED**.
- **`models.py`**: `ObdPidDefinition`, `ObdPidResult`.

### 5.4 NMEA 2000 Fast Packet (`src/protocols/nmea2000/`)

- **`fast_packet.py`**: NMEA 2000 multi-packet fast-packet reassembly (ISO 11783-3).
- **`pgn_library.py`**: `Nmea2000PgnDecoder` with `PGN_ENGINE_DYNAMIC` and the full marine PGN catalogue.

### 5.5 OEM J1939 Decoders (`src/protocols/j1939/oem/` — 6 decoders, 3,293 LOC)

| OEM | LOC | Key signals decoded |
|---|---|---|
| Cummins (`cummins.py`) | 484 | DPF soot mass, active/inhibit regen, DEF dosing rate & tank level, cylinder balancing, fuel rail pressure |
| Caterpillar (`caterpillar.py`) | 518 | Cat engine diagnostics, cylinder cutout, compression brake/retarder stages, fuel delivery trimming |
| Scania (`scania.py`) | 559 | Scania EMS/AdBlue dosing, DPF soot mass, retarder braking torque steps, cylinder balance |
| Volvo (`volvo.py`) | 577 | Volvo V-MAC/EMS/D13 DPF soot mass, DEF dosing & tank level, VEB engine brake retarder stages |
| Detroit Diesel (`detroit.py`) | 564 | DD13/DD15 DPF soot & ash accumulation, DEF dosing pressure/quality, cylinder power balance |
| Actros (`actros.py`) | 551 | OM471/Actros retarder braking levels, AdBlue injection rate, DPF soot load, cylinder trimming |
| `registry.py` | 489 | `OemJ1939Registry.decode_frame` — Proprietary A/B PGN dispatch |

---

## 6. Engine & Telemetry Pipeline (`src/engine/`)

### 6.1 `FrameRouter` (`src/engine/router.py`, 435 LOC)

Pub/sub frame broker. Subscribers obtain `QueueRxSubscription` or callback registrations. Backpressure WARN output is rate-limited to one summary per second.

### 6.2 `DbcSignalDecoder` (`src/engine/decoder/dbc_decoder.py`, 584 LOC)

LRU-cached signal decoder with J1939 PGN mask matching. Integrates with `OemJ1939Registry` for Proprietary A/B PGN fallback.

### 6.3 Buffers

- **`BinaryRingBuffer`** (`src/engine/buffer/ring_buffer.py`): NumPy-backed zero-GC ring buffer for high-frequency telemetry.
- **`RollingDiskBuffer`** (`src/engine/buffer/rolling_disk.py`, 952 LOC): Zstandard-compressed rolling disk buffer with HMAC-SHA256 integrity.

### 6.4 `ReassemblyPipeline` (`src/engine/pipeline/reassembly_pipeline.py`, 1,094 LOC)

Deterministic, thread-safe bridge subscribing to `FrameRouter`, reassembling multi-packet transport frames (ISO-TP FF/CF, J1939 TP BAM/RTS-CTS, NMEA 2000 fast packets, multi-DTC DM1, VIN PGN 65260) and emitting synthetic complete `CanFrame`s. **UNWIRING-GATED** — verified-but-not-wired into a production path.

### 6.5 AI Diagnostic Copilot (`src/engine/ai/` — 8,651 LOC total)

**FULLY OFFLINE (operator decision, M7).** The cloud-LLM narration layer was REMOVED.

| Module | Role |
|---|---|
| `diagnostic_copilot.py` (5,060 LOC) | `AiDiagnosticCopilot` — deterministic expert engine; `EXPERT_KNOWLEDGE_BASE`, `_analyze_local_expert`; `FaultSeverity`; `CopilotActionTrigger`; `DiagnosticAnalysisReport` |
| `golden_cases.py` (236 LOC) | Golden-Traces case loader; stdlib-only schema v1 validation; fail-closed unknown-field/VIN/draft rules |
| `dialogue_engine.py` (588 LOC) | Turkish/English automotive NLP tokenization + dialogue |
| `anomaly_detector.py` | Statistical anomaly detection |
| `hypothesis_engine.py` | Causal Bayesian inference (offline) |
| `symptom_mapper.py`, `discriminating_tests.py`, `evidence_gate.py`, `harvest_validator.py`, `procedure_validator.py`, `drive_safety_policy.py`, `user_kb.py`, `user_report_composer.py`, `session_report.py`, `metrics.py`, `calibration.py`, `golden_similarity.py` | Supporting components |

**AI TX isolation (F-AI-ISO)**: `tests/safety/test_ai_tx_isolation.py` (262 LOC, 30 tests) enforces the invariant via:
1. **AST import scan** — every module under `src/engine/ai/` is parsed; imports checked against `FORBIDDEN_AI_IMPORT_ROOTS`: `src.hal`, `src.safety.gateway`, `src.safety.multiplexer`, `src.safety.estop`, `src.safety.watchdog`, `src.safety.state_machine`, `src.protocols.uds.client`, `src.protocols.uds.flasher`, `src.protocols.uds.isotp`, `src.protocols.j1939.transport`, `src.protocols.j1939.diagnostics`, `src.core.contracts.ports`, `urllib`, `http`, `requests`, `socket`.
2. **Network ban** — no HTTP client imports at all.
3. **Namespace leak tests** — no `TxPort`/`send_sync`/`validate_and_transmit` symbols in the copilot namespace.
4. **Trigger source test** — foreign text never mints triggers; triggers derive only from operator input or deterministic DTC mappings.

**Severity authority**: only `EXPERT_KNOWLEDGE_BASE` / `_analyze_local_expert`. There is no LLM.

### 6.6 Golden-Traces (`data/golden_traces/cases/`)

Schema v1, stdlib-validated by `golden_cases.py`. Seed case is a DRAFT skeleton awaiting user-supplied field data. Draft cases are NEVER calibration-eligible. Agents fabricate NO field values (AGENTS.md §2.3 extension).

### 6.7 Exporters (`src/engine/exporters/`)

- **`mat_exporter.py`**: **WIRED** — into `export_logs("mat")`.
- **`kml_exporter.py`**, **`mdf4_exporter.py`**: AWAIT GPS/MDF signal-history plumbing (unwired).
- **`html_report.py`**, **`pdf_report.py`**: standalone report generators.
- **`path_guard.py`**: export path allowlist.
- **`discovery/`**: `SignalDiscoveryEngine` + detectors — **UNWIRING-GATED**.
- **`virtual_channels/`**: `ChannelEngine` — synthetic/virtual channel flagging.

---

## 7. Security Subsystem (`src/security/`)

### 7.1 License Validator (`src/security/license/validator.py`, 483 LOC)

- **Ed25519 asymmetric ticketing** with 7-day offline grace period (`MAX_OFFLINE_GRACE_SEC = 604,800`).
- **HWM (High-Water Mark)** HMAC'd timestamp persistence; tampered HWM → quarantine (rename, never delete) + conservative grace floor.
- **Clock-rollback detection** (`CLOCK_ROLLBACK_DETECTED`) + monotonic-counter drift cross-check.
- **`_is_indeterminate_fingerprint`**: rejects collector sentinels on BOTH the local and token-carried fingerprint.
- **Wildcard (`*`)** only via explicit constructor opt-in, REFUSED in frozen builds.
- **Future-dated tokens rejected** (`LICENSE_FUTURE_DATED`).
- All time readings flow through the injected `ClockProvider`.

### 7.2 Cloud Client (`src/security/cloud/client.py`, 694 LOC)

- **`CloudConfig`**: HTTPS mandatory (loopback http://localhost excepted for dev); canonical host allowlist; optional SPKI pinning; userinfo forbidden in base URL.
- **`CloudClient`**: retrying urllib transport (429/5xx with linear backoff + `Retry-After` honour, capped at 60 s); bounded response body reads (8 MiB cap); `_SafeRedirectHandler` strips credentials on cross-origin/scheme-downgrade redirects; `_PROTECTED_REQUEST_HEADERS` prevents caller override of `Cookie`/`Authorization`; request-scoped `session_token` override never mutates the shared vault; `set_base_url` re-validates via `CloudConfig` reconstruction.
- Secret names: `CLOUD_SESSION_TOKEN`, `CLOUD_DEVICE_TOKEN`, `CLOUD_DEVICE_ID`, `CLOUD_LICENSE_TICKET` — all DPAPI/AES-GCM-backed.

### 7.3 License Flow (`src/security/cloud/license_flow.py`, 639 LOC)

- `LicenseFlow`: device registration, Ed25519 license activation, canonical ticket verification with embedded public key (13-field schema, `iss`/`aud`/`exp`), `verify_cloud_ticket` with `is_offline` enforcement and explicit device-id binding.
- `default_license_hwm_path()` shared between the launcher and the desktop app so both seal/read the SAME anti-rollback anchor.

### 7.4 Telemetry Uploader (`src/security/cloud/telemetry_uploader.py`)

Resumable 5 MB chunked MDF4 upload (`sessions -> chunks -> complete`), SHA-256 declaration, progress callbacks, resume support.

### 7.5 HWID Collector (`src/security/hwid/collector.py`, 333 LOC)

4-component Windows hardware fingerprint (motherboard UUID, CPU ProcessorId, disk serial, BIOS serial) via WMI/CIM:
- Strict WMI class/field allow-list — free-form command interpolation rejected.
- PowerShell resolved strictly via `GetSystemDirectoryW` (never `%SystemRoot%`).
- `uuid.getnode()` random-node detection → `UNKNOWN_MAC` sentinel.
- Fail-closed: <2 independent real components → `INDETERMINATE_FINGERPRINT`.
- `generate_hardware_fingerprint()` is `@functools.lru_cache(maxsize=1)` — computed once per process.

### 7.6 Anti-Tamper Guard (`src/security/anti_tamper/guard.py`)

Runtime integrity checks; detects tampered knowledge packs and firmware images.

### 7.7 Knowledge Pack (`src/security/knowledge_pack/`)

- `pack_exporter.py` / `pack_loader.py`: encrypted knowledge pack import/export (AES-256-GCM + Ed25519 manifest signing).

---

## 8. Launcher Subsystem (`src/launcher/`)

### 8.1 Preflight Engine (`src/launcher/app.py`, 536 LOC)

- Bootstrap controller coordinating pre-flight checks, cloud auth/HWID binding, update validation, and secure execution of the core app.
- **D-5**: project root prepended to `sys.path` ONLY when running from source — frozen builds never let on-disk repo files outrank bundled modules.
- **D-4 / T62-U2**: target executable verified against a SHA-256 manifest (`data/target.hash`) living OUTSIDE the writable `dist/` tree; env override ignored in frozen builds; streaming SHA-256 (never loads large binaries into RAM).
- Embedded cloud public key `eX3vJQWpo/pKrkpi5Y+f7m5ooUCRbCyY201DTnAjz/Q=`.

### 8.2 Auth Manager (`src/launcher/auth.py`, 207 LOC)

`LauncherAuthManager`:
- Resolves the production cloud endpoint through `_resolve_cloud_base_url()` (P1-6/E7 fix — the old default was the loopback dev server).
- Embedded public key decode is FAIL-CLOSED: a corrupt/mis-packaged key raises `LicenseError(EMBEDDED_KEY_INVALID)` rather than silently dropping licensing (T41/LA-1/Y-3 fix).
- Shares the desktop app's persistent HWM file so both call paths seal/read the SAME anchor (T57-B/L-2 fix).
- `get_current_status()`: offline re-verification of the stored ticket with `is_offline=True` and explicit device-id binding; structured warning on failure (A-1/ASAMA-2 fix).
- `login_web_session`: rejects blank/whitespace tokens (A5-1 fix).

### 8.3 Prerequisite Checker (`src/launcher/prereqs.py`, 171 LOC)

- `check_webview2()`, `check_vcredist()` (Win32 registry probes; non-Windows returns non-critical pass).
- `check_can_drivers()`: PEAK PCAN-Basic, Kvaser CANlib, TMC RP1210 — all optional/non-critical.
- `_system_root()` resolves via `GetSystemDirectoryW` (collector.py model, not `%SystemRoot%`).

### 8.4 Update Manager (`src/launcher/updater.py`, 421 LOC)

Version checking, signed update download, and atomic install with rollback. Pinned update hosts; no HTTP redirects; Ed25519 signature verification enforced; hash-less packages fail closed; 2 GiB cap on downloaded package.

---

## 9. UI & Desktop App (`src/ui/`)

### 9.1 `DesktopApiBridge` (`src/ui/desktop_app.py`, 4,394 LOC)

The native WebView2 IPC bridge exposing ~50 methods to the React 18 frontend. Key method groups:

- **Bus lifecycle**: `connect_bus`, `disconnect_bus`, `reconnect_bus`, `start_rx`, `stop_rx`, `get_bus_metrics`, `set_listen_only`
- **Transmission**: `send_can_frame`, `send_can_frame_sync` (all routed through `TxSafetyGateway`), `set_budget_category`
- **Protocol services**: `uds_request`, `uds_read_did`, `uds_write_did`, `uds_ecu_reset`, `uds_security_access`, `uds_routine_control`, `uds_request_download`, `uds_transfer_data`, `uds_request_transfer_exit`, `j1939_request_pgn`, `j1939_send_pgn`
- **Flashing**: `flash_start`, `flash_continue`, `flash_abort` (EcuFlashingEngine — the ONE wired engine; step tokens mint through `gateway.create_confirmation_issuer()`)
- **Telemetry**: `export_logs`, `upload_session`, `get_signal_history`, `start_stream`, `stop_stream`
- **AI**: `analyze_diagnostics`, `get_golden_cases`, `compose_user_report`
- **Safety state**: `get_safety_state`, `arm_tx`, `activate_tx`, `trigger_fault`, `enter_passive_mode`
- **E-Stop**: `estop_trigger`, `estop_request_challenge`, `estop_submit_reset_token`, `estop_clear_link_fault`
- **Cloud/license**: `cloud_get_status`, `login_web_session`, `logout`, `activate_license`, `get_auth_status`

### 9.2 pywebview Bridge & Frontend Security

- **REVIEW C-1/P0-1 (Renderer Cannot Disarm Safety)**: No bridge method may mint, consume, or shortcut E-Stop reset tokens on the renderer's behalf. Recovery is EXCLUSIVELY the `estop_request_challenge` → out-of-band authorization → `estop_submit_reset_token` flow. `request_diagnostic_challenge` deliberately does NOT expose the token-MINTING entry point. Simulator toggles / scenario switches / any UI convenience must NEVER clear a latched E-Stop.
- **DiagnosticChallenge**: short-lived (<=30 s), single-use, `params_hash`-bound challenge tokens for dual-confirmation actions.
- **`_resolve_cloud_base_url`**: production HTTPS endpoint; `UCANLAB_CLOUD_DEV=1` refused in frozen builds.
- **SPN/FMI re-derivation**: anchored on the SPN token so an FMI-less code still yields its SPN.

### 9.3 Speed Interlock Provenance (REVIEW C-2/C-3/P0-2)

Only physical CCVS telemetry (SA allowlisted, plausibility-checked, sentinel-bounded) may satisfy the TX speed interlock. `record_synthetic_speed()` is recorded for display but can never authorize critical commands.

### 9.4 Frontend Security

- Static frontend served by `frontend_server.py`.
- All IPC calls validated against the bridge method allowlist; unknown methods rejected.
- VIN masking (`mask_vin_in_text`) applied before any report/export leaves the host.

---

## 10. Test Suite Analysis

### 10.1 Test Inventory

| Directory | Files | Tests | LOC |
|---|---|---|---|
| `tests/unit/` | 126 | 1,883 | ~40,000 |
| `tests/safety/` | 4 | 30 | ~2,000 |
| `tests/e2e/` | 5 | 210 | ~7,500 |
| `tests/integration/` | 1 | 3 | ~300 |
| **Total** | **136** | **2,059** | **~49,643** |

### 10.2 Coverage of the 8 Safety Invariants

| Invariant | Primary test files | Status |
|---|---|---|
| 1. Single Audited Transmission Choke-Point | `tests/safety/test_tx_chokepoint_architecture.py`, `tests/unit/test_safety_gateway.py` (1,230 LOC) | Covered |
| 2. Fail-Closed Principle | `tests/unit/test_safety_state_machine.py`, `tests/safety/test_link_fault_estop.py` | Covered |
| 3. No Fabricated Telemetry | `tests/unit/test_virtual_channels.py`, `tests/unit/test_ai_fabrication_fixes.py` | Covered |
| 4. Watchdog & E-Stop Compliance | `tests/unit/test_tx_watchdog.py`, `tests/unit/test_estop.py`, `tests/unit/test_safety_estop.py` | Covered |
| 5. Renderer Cannot Disarm Safety | `tests/unit/test_desktop_app.py` (815 LOC), `tests/unit/test_ui_token_watchdog.py` | Covered |
| 6. Speed Interlock Provenance | `tests/unit/test_safety_gateway.py`, `tests/unit/test_t47_tx_review_fixes.py` (959 LOC) | Covered |
| 7. Wiring Gate | `tests/e2e/test_safety_wiring.py`, `tests/unit/test_t42_p2_wiring.py`, `tests/unit/test_t41_arm_tx_auth.py` | Covered |
| 8. AI Layer Fully Offline & TX Isolation | `tests/safety/test_ai_tx_isolation.py` (262 LOC, 30 tests) | Covered |

### 10.3 High-Value Test Files

| File | LOC | What it locks |
|---|---|---|
| `tests/e2e/test_phase1_e2e.py` | 2,166 | End-to-end safety + transport scenarios |
| `tests/e2e/test_phase2_e2e.py` | 1,550 | End-to-end protocol + telemetry scenarios |
| `tests/e2e/test_challenger_diagnostics.py` | 1,119 | Adversarial diagnostic injection |
| `tests/e2e/test_challenger_safety_transport.py` | 798 | Adversarial safety + transport |
| `tests/e2e/harness.py` | 788 | Shared E2E test harness |
| `tests/unit/test_safety_gateway.py` | 1,230 | Gateway 6-stage policy + rate budgets + whitelist masks |
| `tests/unit/test_protocol_properties.py` | 1,095 | Protocol invariants |
| `tests/unit/test_adversarial_phase1_gate.py` | 1,033 | Phase-1 safety gate attacks |
| `tests/unit/test_isotp.py` | 718 | ISO-TP SF/FF/CF/FC + flow control |
| `tests/unit/test_flasher.py` | 741 | EcuFlashingEngine sequences + trust anchors |
| `tests/unit/test_license_validator.py` | 854 | Ed25519 + HWM + grace + rollback + fingerprint |
| `tests/unit/test_reassembly_pipeline.py` | 943 | Multi-packet reassembly vectors |
| `tests/unit/test_t47_tx_review_fixes.py` | 959 | TX review fixes (rate/whitelist/E-Stop) |
| `tests/unit/test_adversarial_stress.py` | 682 | Stress/fuzz vectors |
| `tests/safety/test_ai_tx_isolation.py` | 262 | AI TX isolation (AST scan) |

### 10.4 Adversarial & Regression Suites

The suite includes dedicated adversarial and regression families:
- `test_adversarial_challenger2.py`, `test_adversarial_final_gate.py`, `test_adversarial_m1_challenger2.py`, `test_adversarial_phase1_gate.py`, `test_adversarial_stress.py`
- `test_review_round2_fixes.py`, `test_review_round3_fixes.py`, `test_review_phase2to6_fixes.py`, `test_review_phase1_safety_fixes.py`, `test_review_residual_risk_fixes.py`, `test_review_ci_governance_fixes.py`
- `test_t41_*`, `test_t42*`, `test_t46*`, `test_t47*`, `test_t48*`, `test_t56*`, `test_t57*`, `test_t58*`, `test_t62*` — milestone/tagged regression suites

---

## 11. Known Defects & Unwired Components (AGENTS.md §2.7 Wiring Gate)

Per AGENTS.md §2.7, the following components carry verified-but-unwired defect fixes and are NOT wired into any production path today:

| # | Component | Source | Status | Remediation notes |
|---|---|---|---|---|
| 1 | `E2ESafetyValidator` / `E2ESafetyPackager` | `src/safety/e2e/validator.py`, `packager.py` | UNWIRING-GATED | AUTOSAR P1/P2, J1850 CRC-8; gateway E2E stamping stage is a no-op | P2-7, P2-8 |
| 2 | `AddressClaimEngine` | `src/protocols/j1939/address_claim.py` (521 LOC) | UNWIRING-GATED | J1939-81 Address Claiming state machine | H-KA |
| 3 | `OemJ1939Registry` | `src/protocols/j1939/oem/registry.py` (489 LOC) | UNWIRING-GATED | OEM proprietary PGN dispatch — exists as a class but is not in the live decode path | P1-10 |
| 4 | `ActiveDiagnosticPoller` | `src/protocols/obd/poller.py` (1,078 LOC) | UNWIRING-GATED | Configurable periodic OBD/UDS poller scheduler | P2-22 |
| 5 | `ReassemblyPipeline` | `src/engine/pipeline/reassembly_pipeline.py` (1,094 LOC) | UNWIRING-GATED | Multi-packet transport-to-decoder bridge | M-18 |
| 6 | `engine/discovery/**` + `engine/exporters/**` (except `MatExporter`) | `src/engine/discovery/`, `src/engine/exporters/kml_exporter.py`, `mdf4_exporter.py` | UNWIRING-GATED | Signal discovery engine + KML/MDF4 exporters await GPS/MDF signal-history plumbing | P2-7, P2-8 |

**WIRED exception**: `EcuFlashingEngine` (`src/protocols/uds/flasher.py`, 1,119 LOC) is WIRED since TUR-2 R2. Step tokens mint through the composition-root-owned `gateway.create_confirmation_issuer()` factory; `flash_start` enforces synchronous signature/trust-anchor/target-identity preconditions BEFORE `arm_tx`; all TX flows through the `TxSafetyGateway` choke-point.

### Latent Concerns

1. **KML/MDF4 exporters await signal-history plumbing.** `KmlExporter` and `Mdf4Exporter` are complete but disconnected.
2. **`OemJ1939Registry` exists but is not in the live decode path.** Proprietary PGN decoding (6 OEMs, 3,293 LOC) is therefore not active in production telemetry.
3. **`ActiveDiagnosticPoller` is not scheduled.** The OBD-II/UDS polling engine exists and is tested but no composition-root loop drives it.
4. **`ReassemblyPipeline` is not subscribed.** Multi-packet reassembly is implemented and tested but `FrameRouter` does not feed it in production.
5. **`AddressClaimEngine` is dormant.** J1939-81 address claiming is implemented but never runs on a live bus.
6. **Residual TOCTOU window** (gateway.py:1669-1681): bounded to drivers with no TX-flush primitive. Accepted, documented risk.
7. **`platform.node()` binding in DPAPI entropy** (`secret_provider.py:82-104`): a machine rename permanently invalidates DPAPI-sealed secrets with a misleading "EXPIRED" status.

---