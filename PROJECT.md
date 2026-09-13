# Project: Universal CAN-Bus Diagnostic & Telemetry Tool

## Architecture
The Universal CAN-Bus Diagnostic & Telemetry Tool is built on a Python 3.11+ Hexagonal (Ports & Adapters) architecture with strict functional safety invariants (ISO 26262 ASIL-B/D design principles):
- **Core Domain & Models (`src/core/`)**: Immutable `CanFrame`, canonical telemetry types, error hierarchies (`PlatformError`), and port interfaces (`TxPort`, `RxSubscription`).
- **Hardware Abstraction Layer (`src/hal/`)**: Multi-vendor CAN bus abstraction (`AbstractBus`, `PythonCanBus`, `VirtualBus`, `RP1210Client`) enforcing protected frame dispatch.
- **Safety Subsystem (`src/safety/`)**: `TxSafetyGateway` 6-stage policy choke-point, `EmergencyStopSystem` HMAC-SHA256 reset tokens, monotonic clock watchdog leases, and the newly added `E2ESafetyValidator` / `E2ESafetyPackager`.
- **Protocol Stacks (`src/protocols/`)**:
  - `src/protocols/obd/`: SAE J1979 OBD-II Mode 01 PID knowledge base, physical conversion engine, and `ActiveDiagnosticPoller`.
  - `src/protocols/uds/`: ISO 14229 UDS Client, Services (0x10..0x3E), ISO 15765-2 DoCAN (ISO-TP), and UDS DID knowledge base.
  - `src/protocols/j1939/`: J1939-21 Transport Protocol (BAM & RTS/CTS), J1939-73 Diagnostic Messages (DM1..DM11), J1939-81 Address Claiming, and `src/protocols/j1939/oem/` proprietary decoders (Cummins, Caterpillar, Scania, Volvo, Detroit Diesel, Mercedes Actros).
- **Engine Subsystem (`src/engine/`)**: Pub-Sub `FrameRouter`, LRU-cached `DbcSignalDecoder` with J1939 PGN mask matching, `BinaryRingBuffer`, `RollingDiskBuffer` (HMAC & Zstd), and the newly added `ReassemblyPipeline` bridging multi-packet streams to decoders.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---|---|---|---|
| 1 | OBD-II Mode 01 PID Database | Full registry of SAE J1979 Mode 01 PIDs (0x00..0xFF), bitmask decoders, formulas, scaling, units, min/max | M1 | ORIGINAL_REQUEST §R1 |
| 2 | UDS ISO 14229 DID Knowledge Base | Standard DIDs (0xF190 VIN, 0xF188 SW, 0xF191 HW, 0xF187 Part No, 0xF197 System Name, 0xF1A0..0xF1AF, battery voltage) | M1 | ORIGINAL_REQUEST §R1 |
| 3 | Active Diagnostic Poller & Scheduler | Configurable periodic poller (10Hz, 5Hz, 1Hz), prioritization, TxPort integration, request/response state machine | M1 | ORIGINAL_REQUEST §R1 |
| 4 | Diagnostic Physical Value Converter | High-precision conversion engine mapping raw diagnostic byte buffers to validated physical telemetry values | M1 | ORIGINAL_REQUEST §R1 |
| 5 | J1939 Proprietary A/B Mapping Engine | PGN 61184 (0xEF00 Proprietary A) and PGN 65280-65535 (0xFF00-0xFFFF Proprietary B) dispatch and matching | M2 | ORIGINAL_REQUEST §R2 |
| 6 | Cummins OEM J1939 Decoders | DPF soot mass, active/inhibit regen, DEF dosing rate & tank level, cylinder balancing, fuel rail pressure | M2 | ORIGINAL_REQUEST §R2 |
| 7 | Caterpillar OEM J1939 Decoders | Cat engine diagnostics, cylinder cutout, compression brake/retarder stages, fuel delivery trimming | M2 | ORIGINAL_REQUEST §R2 |
| 8 | Scania OEM J1939 Decoders | Scania EMS/AdBlue dosing, DPF soot mass, retarder braking torque steps, cylinder balance | M2 | ORIGINAL_REQUEST §R2 |
| 9 | Volvo OEM J1939 Decoders | Volvo V-MAC / EMS / D13 DPF soot mass, DEF dosing & tank level, VEB engine brake retarder stages | M2 | ORIGINAL_REQUEST §R2 |
| 10 | Detroit Diesel OEM J1939 Decoders | DD13/DD15 DPF soot & ash accumulation, DEF dosing pressure/quality, cylinder power balance | M2 | ORIGINAL_REQUEST §R2 |
| 11 | Mercedes Actros OEM J1939 Decoders | OM471/Actros retarder braking levels, AdBlue injection rate, DPF soot load, cylinder trimming | M2 | ORIGINAL_REQUEST §R2 |
| 12 | J1939 TP BAM & RTS/CTS Transport | SAE J1939-21 multi-packet transport (PGN 60416 TP.CM & PGN 60160 TP.DT), BAM, RTS/CTS CMDT state machines | M3 | ORIGINAL_REQUEST §R3 |
| 13 | ISO 15765-2 DoCAN Transport | Multi-frame ISO-TP (SF, FF, CF, FC), Standard 11-bit & Extended 29-bit addressing, Flow Control with STmin pacing | M3 | ORIGINAL_REQUEST §R3 |
| 14 | Auto-Reassembly Pipeline Engine | Deterministic, thread-safe pipeline subscribing to FrameRouter, reassembling multi-packet frames to DbcSignalDecoder | M3 | ORIGINAL_REQUEST §R3 |
| 15 | Multi-DTC DM1 & VIN Reassembly | Reassembly and signal parsing of multi-packet broadcast messages (multi-DTC DM1, VIN PGN 65260; Component Identification PGN 65259 handled separately) | M3 | ORIGINAL_REQUEST §R3 |
| 16 | Mathematical CRC Foundation | Precomputed 256-entry lookup tables and algorithms for CRC-8 Polynomial 0x1D (J1850 / P1) and 0x2F (P2 / MQB) | M4 | ORIGINAL_REQUEST §R4 |
| 17 | AUTOSAR E2E Profile 1 & 2 Engine | AUTOSAR E2E Profile 1 (Data ID, counter 0..14/15, CRC 0x1D) and Profile 2 (Data ID list, counter 0..15, CRC 0x2F) | M4 | ORIGINAL_REQUEST §R4 |
| 18 | CRC-8 SAE J1850 Engine | Standard SAE J1850 CRC-8 calculation, verification, and frame error detection | M4 | ORIGINAL_REQUEST §R4 |
| 19 | OEM Checksum & Rolling Counter Profiles | Toyota (modulo-256 + counter), VAG MQB (CRC-8 0x2F + counter), Volvo (checksum/counter) safety profiles | M4 | ORIGINAL_REQUEST §R4 |
| 20 | Rx E2E Safety Validator | Stateful validation engine detecting frame corruption, sequence jumps, repeated frames, and CRC mismatches | M4 | ORIGINAL_REQUEST §R4 |
| 21 | Tx E2E Safety Packager | Outgoing frame safety wrapper stamping rolling counters and computing CRC bytes before TxPort dispatch | M4 | ORIGINAL_REQUEST §R4 |
| 22 | Comprehensive E2E & Unit Test Suite | 100% test pass for the full suite (1000+ unit/e2e tests) + comprehensive new test suites covering Tiers 1-4 | M5 | ORIGINAL_REQUEST §R5/AC5 |
| 23 | Cloud Client — HTTP Transport & Credential Store | DPAPI-backed session/device token storage, retrying HTTP transport (429/5xx backoff, Retry-After), health check (MASTER_PLAN §3.2) | M6 | MASTER_PLAN Task 5.3/5.4 |
| 24 | Cloud Client — Device Registration & Ed25519 License Activation | HWID registration, device_token acquisition, canonical ticket verification with embedded public key (13-field schema, iss/aud/exp) | M6 | MASTER_PLAN §3.1 |
| 25 | Cloud Client — Resumable Telemetry Upload | 5 MB chunked MDF4 upload (sessions -> chunks -> complete), SHA-256 declaration, progress callbacks, resume support | M6 | MASTER_PLAN §16 |
| 26 | AI Layer Fully-Offline (F-AI-ISO) | Cloud-LLM narration layer REMOVED (operator decision, M7): no API keys, no network calls from `src/engine/ai/**` (AST-locked incl. `urllib`/`http`/`socket`), severity authority is the deterministic expert engine only, action triggers minted only from operator input / deterministic DTC mappings; adversarial suite `tests/safety/test_ai_tx_isolation.py` runs in the existing CI pytest job | M7 | P0 roadmap |
| 27 | P1 Diagnostic Data Model (F-DATA-MODEL) | Frozen `SignalSample` / `DiagnosticEvent` + `VehicleSession` container in `src/core/models/diagnostics.py`; sentinel rejection (NaN/Inf), DISCOVERED confidence gating, raw-VIN fail-closed (only `mask_vin_in_text` output accepted) | M7 | P1 roadmap |
| 28 | Golden-Traces Case Format (F-GOLDEN-CASES) | Repair-verified diagnostic case schema v1 (`data/golden_traces/cases/schema.json`, stdlib-validated by `src/engine/ai/golden_cases.py`); fail-closed unknown-field/VIN/draft rules; draft cases excluded from calibration; seed case `volvo_penta_d4_300_nonstart.json` awaits user-supplied field data | M7 | P1 roadmap |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|---|---|---|---|
| M1 | UDS & OBD-II Knowledge Base & Active Poller | Features 1, 2, 3, 4 | none | DONE |
| M2 | Commercial Vehicle OEM Proprietary J1939 | Features 5, 6, 7, 8, 9, 10, 11 | none | DONE |
| M3 | Multi-Packet Transport & Auto-Reassembly Pipeline | Features 12, 13, 14, 15 | none | DONE |
| M4 | Checksum & Rolling Counter (E2E Safety) Engine | Features 16, 17, 18, 19, 20, 21 | none | DONE |
| M5 | E2E Integration & Full Test Suite (100% Pass) | Feature 22 (Full verification of M1-M4, all existing 822 + new tests) | M1, M2, M3, M4 | DONE |
| M6 | Universal-CAN-Cloud Client Integration | Features 23, 24, 25 (client side of cloud Tasks 5.3/5.4) | none | DONE |
| M7 | P0+P1 AI Diagnostic Foundation | Features 26, 27, 28 (AI TX isolation, diagnostic data model, Golden-Traces case format) | none | DONE |

## Interface Contracts

### Diagnostics (M1) ↔ Safety & Transport (`TxPort` & `IsoTpTransport`)
- `ActiveDiagnosticPoller(tx_port: TxPort, isotp_transport: Optional[IsoTpTransport] = None, clock_provider: Optional[ClockProvider] = None)`
- `poller.register_pid(pid: int, rate_hz: float, callback: Callable[[ObdPidResult], None])`
- `poller.register_did(did: int, rate_hz: float, callback: Callable[[UdsDidResult], None])`
- `ObdPidRegistry.decode(pid: int, raw_bytes: bytes) -> ObdPidResult`
- `UdsDidRegistry.decode(did: int, raw_bytes: bytes) -> UdsDidResult`

### OEM J1939 Decoders (M2) ↔ `DbcSignalDecoder` & `FrameRouter`
- `OemJ1939Registry.decode_frame(frame: CanFrame) -> Optional[OemDecodedPayload]`
- `OemDecodedPayload`: `manufacturer: str`, `pgn: int`, `signals: dict[str, DecodedSignalValue]`, `timestamp_ns: int`.
- Integrates cleanly with `DbcSignalDecoder.decode(frame)` by providing fallback / augmentation for proprietary PGNs (61184 / 65280..65535).

### Transport Auto-Reassembly (M3) ↔ `FrameRouter` & `DbcSignalDecoder`
- `ReassemblyPipeline(router: FrameRouter, dbc_decoder: DbcSignalDecoder, j1939_transport: Optional[J1939TransportProtocol] = None, isotp_transport: Optional[IsoTpTransport] = None)`
- Emits synthetic complete `CanFrame` (or `ReassembledMessage`) to `FrameRouter` and passes decoded signals to listeners.
- Thread-safe, non-blocking callback execution with session isolation.

### E2E Safety Engine (M4) ↔ `CanFrame` & `TxSafetyGateway`
- `E2ESafetyValidator.validate(frame: CanFrame, profile: E2EProfileConfig) -> E2EValidationResult`
  - `E2EValidationResult.verdict`: `OK`, `REPEATED`, `SOME_LOST`, `WRONG_SEQUENCE`, `CRC_ERROR`, `INITIAL`.
- `E2ESafetyPackager.package(frame: CanFrame, profile: E2EProfileConfig) -> CanFrame`
  - Stamps rolling counter and computes CRC byte directly into frame payload.

### Cloud Client (M6) ↔ Universal-CAN-Cloud REST API (`/api/v1`)
- `CloudClient(config: CloudConfig, secret_provider: SecretProvider)`
  - `client.request(method, path, json_body=..., raw_body=...) -> CloudResponse` (retrying HTTP)
  - `client.store_session_token(token)` / `client.get_device_token()` / `client.store_license_ticket(t)`
- `LicenseFlow(client, public_key: Ed25519PublicKey).register_device(name, hwid) -> DeviceRegistration`
- `LicenseFlow.activate_license(license_ref) -> CloudLicenseClaims` (verifies signature locally)
- `TelemetryUploader(client, chunk_size=5MB, progress_callback).upload_file(path, vin) -> UploadResult`
  - `uploader.resume(session_id) -> UploadProgress`

## Code Layout
```
Universal-CAN-BUS-Tool/
├── src/
│   ├── protocols/
│   │   ├── obd/
│   │   │   ├── __init__.py
│   │   │   ├── pids.py              # SAE J1979 Mode 01 PID database & decoding formulas
│   │   │   ├── poller.py            # ActiveDiagnosticPoller scheduler & state machine
│   │   │   └── models.py            # ObdPidDefinition, ObdPidResult
│   │   ├── uds/
│   │   │   ├── did_database.py      # Standard ISO 14229 DID database & decoders
│   │   │   └── ... (existing client, isotp, services)
│   │   └── j1939/
│   │       ├── oem/
│   │       │   ├── __init__.py
│   │       │   ├── registry.py      # OEM J1939 proprietary dispatch registry
│   │       │   ├── cummins.py       # Cummins PGN 61184/65280-65535 definitions
│   │       │   ├── caterpillar.py   # Caterpillar definitions
│   │       │   ├── scania.py        # Scania EMS / AdBlue / Retarder definitions
│   │       │   ├── volvo.py         # Volvo V-MAC / D13 DPF / VEB definitions
│   │       │   ├── detroit.py       # Detroit Diesel DD13/DD15 definitions
│   │       │   └── actros.py        # Mercedes Actros OM471 definitions
│   │       └── ... (existing transport, diagnostics, address_claim)
│   ├── engine/
│   │   └── pipeline/
│   │       ├── __init__.py
│   │       └── reassembly_pipeline.py # Deterministic multi-packet reassembly & DBC feed
│   └── safety/
│       └── e2e/
│           ├── __init__.py
│           ├── crc.py               # CRC-8 Polynomial 0x1D & 0x2F lookup tables & algos
│           ├── profiles.py          # AUTOSAR Profile 1/2, J1850, Toyota, VAG MQB, Volvo
│           ├── validator.py         # E2ESafetyValidator (Rx validation)
│           └── packager.py          # E2ESafetyPackager (Tx frame packaging)
└── tests/
    ├── unit/
    │   ├── test_obd_pids.py
    │   ├── test_obd_poller.py
    │   ├── test_uds_did_database.py
    │   ├── test_j1939_oem_cummins.py
    │   ├── test_j1939_oem_scania_volvo.py
    │   ├── test_j1939_oem_cat_detroit_actros.py
    │   ├── test_reassembly_pipeline.py
    │   ├── test_e2e_safety_crc.py
    │   ├── test_e2e_safety_profiles.py
    │   └── test_e2e_safety_rx_tx.py
    └── e2e/
        ├── test_challenger_diagnostics.py
        └── test_challenger_safety_transport.py
```
