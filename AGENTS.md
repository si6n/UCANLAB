# AGENTS.md — Universal CAN-Bus Diagnostic & Telemetry Platform

This document defines context, architectural boundaries, safety requirements, and development guidelines for Hermes Agent and autonomous sub-agents working within this repository.

---

## 1. Project Overview & Architecture

Universal CAN-Bus Diagnostic & Telemetry Tool is a professional-grade automotive, heavy-duty, marine, and industrial CAN/CAN-FD diagnostic, telemetry, and flashing suite built on Python 3.11+ following a strict Hexagonal (Ports & Adapters) architecture and ISO 26262 ASIL-B/D functional safety design principles.

### Key Subsystems (`src/`)
- `src/core/` (Domain Layer): Immutable data models (`CanFrame`, `TelemetrySignal`), error hierarchies (`PlatformError`), port interfaces (`TxPort`, `RxSubscription`). Zero external framework dependencies.
- `src/hal/` (Hardware Abstraction Layer): Multi-vendor bus abstraction (`AbstractBus`, `PythonCanBus`, `VirtualBus`, `RP1210Client`) handling PCAN, Kvaser, RP1210, Vector, SocketCAN, and log replay (`.asc`, `.blf`, `.csv`).
- `src/safety/` (Functional Safety Subsystem):
  - `TxSafetyGateway`: 6-stage policy choke-point for all outbound transmissions.
  - `EmergencyStopSystem`: HMAC-SHA256 authenticated reset tokens and state machine.
  - Monotonic clock watchdog leases (800ms limit).
  - `E2ESafetyValidator` & `E2ESafetyPackager`: AUTOSAR Profile 1 & 2, SAE J1850 CRC-8, rolling counter validation.
- `src/protocols/` (Automotive & Heavy-Duty Protocols):
  - `protocols/obd/`: SAE J1979 OBD-II Mode 01 PIDs database and physical conversion engine (`ActiveDiagnosticPoller`).
  - `protocols/uds/`: ISO 14229 UDS Client, ISO 15765-2 DoCAN (ISO-TP), flashing sequences (0x10..0x37), DID database.
  - `protocols/j1939/`: SAE J1939-21 Transport Protocol (BAM & RTS/CTS), J1939-73 Diagnostics (DM1..DM11), Address Claiming, and OEM decoders (`cummins`, `caterpillar`, `scania`, `volvo`, `detroit_diesel`, `actros`).
- `src/engine/` (Telemetry & Processing Pipeline):
  - Pub-Sub `FrameRouter`, LRU-cached `DbcSignalDecoder` with J1939 PGN mask matching.
  - `BinaryRingBuffer` (NumPy, zero-GC) and `RollingDiskBuffer` (Zstandard compression + HMAC-SHA256).
  - `ReassemblyPipeline`: Multi-packet transport to decoder bridge.
- `src/cloud/` (Cloud Client):
  - Universal-CAN-Cloud REST client (`/api/v1`), Ed25519 license activation, resumable 5 MB chunked MDF4 telemetry upload.

---

## 2. Inviolable Functional Safety Invariants (ISO 26262)

Any agent modifying or interacting with transmission, flashing, or diagnostic injection MUST uphold the following:

1. **Single Audited Transmission Choke-Point**:
   - ALL outbound CAN transmissions MUST pass through `TxSafetyGateway` and conform to `TxPort`.
   - Never inject frames directly into HAL drivers bypassing safety checks.
2. **Fail-Closed Principle**:
   - If an error, timeout, CRC mismatch, sequence counter gap, or watchdog expiration occurs, the transmission state machine must enter `SAFE_STATE` or `ESTOP_TRIGGERED`.
3. **No Fabricated Telemetry**:
   - Diagnostic decoders must never invent, smooth, or extrapolate raw sensor measurements without explicitly flagging them as synthetic/virtual channels.
4. **Watchdog & E-Stop Compliance**:
   - Watchdog leases must refresh strictly against `time.monotonic()` or the platform `ClockProvider`.
   - E-Stop reset sequences require valid token validation.
5. **Renderer Cannot Disarm Safety** (REVIEW C-1/P0-1):
   - No `pywebview` bridge method may mint, consume, or shortcut E-Stop reset tokens on the renderer's behalf. Recovery is exclusively the `estop_request_challenge` → out-of-band authorization → `estop_submit_reset_token` flow. Simulator toggles / scenario switches / any UI convenience must NEVER clear a latched E-Stop.
6. **Speed Interlock Provenance** (REVIEW C-2/C-3/P0-2):
   - Only physical CCVS telemetry (SA allowlisted, plausibility-checked, sentinel-bounded) may satisfy the TX speed interlock. Synthetic/simulated speed feeds are recorded for display but can never authorize critical commands.
7. **Wiring Gate (LATENT Defect Lock)** (REVIEW Faz 4):
   - Several engine/protocol components carry verified-but-unwired defect fixes and are NOT wired into any production path today: `E2ESafetyValidator`/`E2ESafetyPackager`, `EcuFlashingEngine` (`flasher.py`), `AddressClaimEngine`, `OemJ1939Registry`, `ActiveDiagnosticPoller`, `ReassemblyPipeline`, `engine/discovery/**`, and `engine/exporters/**` (except `MatExporter`, wired into `export_logs("mat")`; `KmlExporter`/`Mdf4Exporter` await GPS/MDF signal-history plumbing).
   - BEFORE wiring any of these into a live RX/TX/telemetry path, the agent MUST review the P2-7/P2-8/H-KA/P1-10/P2-22/M-18 remediation notes in the corresponding source files and their regression tests, and MUST wire them through the `TxSafetyGateway` choke-point where they transmit. A pull request that activates one of these components without addressing its known defect notes must be rejected.

---

## 3. Pit-Crew Team Collaboration ("Ofis Mantığı" & Non-Blocking Workflow)

The Pit-Crew operates on an asynchronous delegation model so the user is never blocked:

1. **PitBoss Dispatches & Returns Immediately**:
   - When a task requires research, coding, or testing, PitBoss assigns the job to the specialist via `delegate_task(..., background=True)` or background sub-processes.
   - PitBoss responds immediately to the user: *"Görevleri ofislere dağıttım, arka planda çalışıyorlar. Ben buradayım, yeni bir talimatınız var mı?"*
2. **Specialists Work in Their Respective Offices**:
   - **`telemetry` [Data]**: CAN & J1939 telemetry decoding and frame analysis.
   - **`marshal` [Safety]**: ASIL-B/D compliance and TxSafetyGateway auditing.
   - **`tuner` [Dev]**: Clean code implementation and pytest validation.
   - **`scout` [RE/DBC]**: Signal discovery and DBC generation.
   - **`chassis` [HAL]**: Hardware driver abstraction and replay logs.
   - **`uplink` [Cloud]**: Telemetry uploads and licensing.
   - **`cockpit` [UI]**: React 18 & WebView2 dashboard.
3. **Synthesis on Completion**:
   - Specialists report back milestones and final outcomes to PitBoss.
   - PitBoss updates the user concisely without cluttering the chat history.
