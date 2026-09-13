# Universal CAN-Bus Diagnostic & Telemetry Platform

[![CI Pipeline](https://github.com/si6n/Universal-CAN-BUS-Tool/actions/workflows/ci.yml/badge.svg)](https://github.com/si6n/Universal-CAN-BUS-Tool/actions/workflows/ci.yml)
[![Python 3.12 | 3.13](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows%20WebView2-lightgrey.svg)](https://github.com/si6n/Universal-CAN-BUS-Tool)

A professional-grade CAN/CAN-FD diagnostics, telemetry, and ECU flashing
platform for automotive, heavy-duty, industrial, and marine applications.
Built on Python 3.12+ with a React 18 + TypeScript desktop UI (WebView2).

**Standards:** ISO 11898-1 (CAN/CAN-FD) · SAE J1939-21/-71/-73/-81 ·
ISO 14229-1 (UDS) · ISO 15765-2 (DoCAN) · NMEA 2000 · TMC RP1210 (A/B/C)

## Key Features

- **Real-time telemetry** — 60 FPS live cockpit with CAN sniffer table and
  signal oscilloscope; microsecond-timestamped frame capture.
- **Protocol suite** — J1939 (BAM, RTS/CTS, DM1–DM11, address claiming),
  UDS client with full flashing sequence (0x10–0x37, seed-key), NMEA 2000
  fast packet, and Volvo Penta EDC/EVC decoding.
- **Safety architecture** — E-Stop interlock, 800 ms TX watchdog,
  dual-confirmation TX gateway with speed interlock and dynamic whitelist,
  fail-closed replay filter. Every transmission passes a single audited
  choke-point.
- **Signal discovery** — evidence-based reverse engineering (stimulus–response
  protocol, Pearson/Spearman correlation, time-lag analysis) with one-click
  DBC export.
- **Virtual channels** — torque, power (kW/HP), fuel efficiency, and
  propeller slip derived from raw J1939/N2K signals.
- **AI diagnostic copilot** — fully offline deterministic rule-based root-cause
  analysis (no cloud LLM, no API keys, no data leaves the host); never
  fabricates measurements.
- **Export formats** — ASAM MDF4, MATLAB, KML, Vector ASC, CSV/JSON, plus
  SHA-256-signed tamper-evident HTML service reports.
- **Black-box recording** — 300K-frame zero-GC NumPy ring buffer and
  Zstandard-compressed rolling disk chunks with fsync durability.

## Hardware Support (HAL)

| Interface | Driver | Channel format |
| :--- | :--- | :--- |
| Virtual (demo) | built-in simulator | `vcan0` |
| PEAK PCAN | `PCANBasic.dll` | `PCAN_USBBUS1` |
| Kvaser | `canlib32.dll` | `0`, `1` |
| RP1210 adapters (Nexiq, Noregon, DPA5) | `RP121064.DLL` / `RP121032.DLL` (auto by bitness) | device ID (`1`) |
| Vector | `vcan2.dll` | `0`, `1` |
| Linux SocketCAN | kernel vcan/can | `can0`, `vcan0` |
| Replay | Vector `.asc`, `.csv`, `.blf` | file path |

## Quick Start

```bash
git clone https://github.com/si6n/Universal-CAN-BUS-Tool.git
cd Universal-CAN-BUS-Tool
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
pytest -v                         # full suite (1000+ tests)
ruff check .                      # lint / static analysis
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

## License

MIT — see [LICENSE](LICENSE).
