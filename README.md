# Universal CAN-Bus Diagnostic & Telemetry Platform

[![Python 3.12 | 3.13](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows%20WebView2-lightgrey.svg)](https://ucanlab.org)

A professional-grade CAN/CAN-FD diagnostics, telemetry, and ECU flashing
platform for automotive, heavy-duty, industrial, and marine applications.
Built on Python 3.12+ with a React 18 + TypeScript desktop UI (WebView2).

**Standards:** ISO 11898-1 (CAN/CAN-FD) · SAE J1939-21/-71/-73/-81 ·
ISO 14229-1 (UDS) · ISO 15765-2 (DoCAN) · NMEA 2000 · TMC RP1210 (A/B/C)

## Product Purpose

Mechanics often cannot locate a fault on their own and end up depending on an
electronics/IT specialist. uCAN Lab exists to remove that dependency: plug the
adapter into the vehicle or vessel, and a mechanic with **no technical
background** should be able to find the fault and learn **what to do next**.
Large fleets and teams are a secondary audience (multi-vehicle work, optional
cloud telemetry).

Design principles:

1. **Mechanic first.** The main flow is: connect adapter → scan → plain-language
   result → step-by-step guidance. Engineer tools (sniffer, oscilloscope, DBC,
   signal discovery) are secondary.
2. **No jargon.** Terms such as DTC, SPN/FMI or PGN come with a one-sentence
   explanation; details stay one click away.
3. **Honest diagnosis.** The copilot never fabricates measurements or
   conclusions; when unsure it says so and states what data is missing.
   Safety-relevant conditions (brakes, steering, high voltage, fire risk) are
   warned about first.
4. **Offline by default.** Workshops and vessels may have no internet; the cloud
   is optional and used only for fleet telemetry.

Current status vs. goal: the offline copilot, DTC/J1939/UDS/OBD-II knowledge
bases and safety architecture are implemented. The fully guided mechanic
workflow (automatic vehicle/bitrate detection, simplified UI) is the product
direction and is **not complete yet**; today the channel and bitrate are chosen
via CLI options or settings.

## Key Features

- **Real-time telemetry** — 60 FPS live cockpit with CAN sniffer table and
  signal oscilloscope; microsecond-timestamped frame capture.
- **Protocol suite** — J1939 (BAM, RTS/CTS, DM1–DM11, address claiming),
  UDS client with full flashing sequence (0x10–0x37, seed-key), NMEA 2000
  fast packet, and Volvo Penta EDC/EVC decoding.
- **Safety architecture** — E-Stop interlock, 800 ms TX watchdog,
  dual-confirmation TX gateway with speed interlock and dynamic whitelist,
  replay safety filter (analysis-side; replay has no live-bus TX path).
  Every transmission passes a single audited choke-point.
- **Signal discovery** — evidence-based reverse engineering (stimulus–response
  protocol, Pearson/Spearman correlation, time-lag analysis) with one-click
  DBC export.
- **Virtual channels** — torque, power (kW/HP), fuel efficiency, and
  propeller slip derived from raw J1939/N2K signals.
- **AI diagnostic copilot** — fully offline, deterministic root-cause analysis
  (no cloud LLM, no API keys, no data leaves the host; it can never transmit
  on the bus). It reads a free-text complaint in Turkish or English (typo
  tolerant), DTCs, J1939 SPN/FMI, raw DM1 frames and live telemetry together
  and answers in six plain sections: summary, urgency (red/yellow/green/gray),
  ranked causes with supporting **and** contradicting evidence, what to do
  (simplest checks first), which measurement is missing and how to take it,
  and collapsible technical details (SPN/FMI/PGN, glossary, NHTSA records).
  High-voltage, fire, brake and steering warnings come first. Every claim
  cites its source record (`dtc_database#P0101`, `j1939_spn_fmi#SPN_110.FMI_0`);
  it never fabricates measurements — no data means "no data". All 23 knowledge
  sources go through one lazy, cached `KnowledgeBase`; see `docs/COPILOT.md`.
- **Export formats** — ASAM MDF4, MATLAB, KML, Vector ASC, CSV/JSON, plus
  HMAC-sealed HTML service reports (keyless SHA-256 checksum when no
  `REPORT_SIGNING_KEY` is configured).
- **Black-box recording** — 300K-frame zero-GC NumPy ring buffer and
  Zstandard-compressed rolling disk chunks with fsync durability.

## Hardware Support (HAL)

| Interface | Driver | Channel format |
| :--- | :--- | :--- |
| Virtual (demo) | built-in simulator | `vcan0` |
| PEAK PCAN | `PCANBasic.dll` via `PythonCanBus` (python-can backend) | `PCAN_USBBUS1` |
| Kvaser | `canlib32.dll` via `PythonCanBus` (python-can backend) | `0`, `1` |
| RP1210 adapters (Nexiq, Noregon, DPA5) | `RP121064.DLL` / `RP121032.DLL` (auto by bitness), native implementation | device ID (`1`) |
| Vector | `vcan2.dll` via `PythonCanBus` (python-can backend, hardware-untested) | `0`, `1` |
| Linux SocketCAN | kernel vcan/can via `PythonCanBus` (python-can backend, hardware-untested) | `can0`, `vcan0` |
| Replay | Vector `.asc`, `.csv`, `.blf` (analysis-only, no live-bus TX) | file path |

## Quick Start

Download the installer or source package from https://ucanlab.org
(license required), then:

```bash
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt

# Demo mode (virtual bus)
python src/main.py

# Real hardware examples
python src/main.py --interface=pcan --channel=PCAN_USBBUS1 --bitrate=500000
python src/main.py --interface=rp1210 --channel=1 --bitrate=250000

# Console sniffer mode
python src/main.py --cli --interface=virtual --channel=vcan0
```

Common CLI options: `--interface/-i`, `--channel/-c`, `--bitrate/-b`,
`--cli`, `--log-level`. For RP1210, `--channel` is the numeric device ID.

## Build

```powershell
python scripts/build_exe.py        # PyInstaller single-file .exe
python scripts/build_nuitka.py    # Nuitka C-level compiled build
```

## Testing & Quality

```bash
pytest -v                                   # full suite
ruff check .                                # lint / static analysis
python scripts/validate_copilot_data.py     # knowledge-data gate (schema, provenance, cross-refs)
python scripts/rebuild_csv_exports.py --verify   # CSV twins in sync with their JSON
```

All modules — safety state machines, transport protocols, crypto licensing,
virtual channels — are covered by unit, integration, and e2e tests in CI.

## Architecture Overview

```
UI (React 18 + WebView2)
  └─ Domain: AI Copilot · Virtual Channels · Signal Discovery · DBC Decoder
      └─ Diagnostics: J1939 DM1-DM11 · UDS Services · Volvo EDC/EVC
          └─ Transport: J1939-21 TP (BAM/CMDT) · ISO-TP · N2K Fast Packet
              └─ Safety Core: State Machine · Watchdog · TX Gateway · E-Stop
                  └─ HAL: Virtual · PCAN · Kvaser · RP1210 · Vector · Replay
```

Source layout: `src/core` (models, errors), `src/engine` (AI, buffers,
decoders, exporters, discovery), `src/protocols` (J1939, UDS, N2K, Volvo),
`src/safety` (E-Stop, gateway, watchdog, state machine), `src/security`
(Ed25519 licensing, HWID, anti-tamper, cloud client), `src/hal` (drivers),
`src/ui` (desktop bridge + React frontend), `tests/` (pytest suite).

## Documentation

| Path | Content |
| :--- | :--- |
| `PROJECT.md` | Architecture summary and feature inventory |
| `docs/architecture/MASTER_PLAN.md` | Architecture specification, kept in sync with the code (section numbers are referenced from source) |
| `docs/adrs/` | Architecture decision records (hexagonal layers, TX safety choke-point) |
| `docs/COPILOT.md` | Copilot architecture, data sources, scoring, how to extend it |
| `docs/OFFLINE_AI_ENGINE.md` | Offline diagnostic engine internals (rule scenarios, packet explainer) |
| `docs/ai_context/` | Layered architecture, safety invariants, protocols, OEM matrix, testing guide |
| `docs/protocols/`, `docs/specs/` | J1939 / UDS references and subsystem specifications |
| `docs/runbook/` | Operational procedures (e.g. E-Stop reset) |
| `docs/audit/` | Data-integrity audit reports |
| `data/PROVENANCE.md` | Origin and licensing of every shipped data set |

## Data

`data/` ships the offline knowledge base used by the copilot and decoders:
`diagnostics/` (DTC, J1939 SPN/FMI, PID, Mode 06, symptoms, root-cause graph,
thresholds), `dbc/` (curated DBC packs by segment), `knowledge/`,
`golden_traces/` and `traces/`. Each data set is documented in its
`PROVENANCE.md` / `licenses/` entry.

## License

Proprietary. uCAN Lab is a commercial licensed product; licensing terms,
trial and purchase options: https://ucanlab.org. Copying, redistribution
or reverse engineering outside the terms of a purchased license is not
permitted.
