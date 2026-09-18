# Security & Safety Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 13.x    | :white_check_mark: |
| < 13.0  | :x:                |

## Industrial Safety & Vulnerability Reporting

Safety is the fundamental core of the **Universal CAN-Bus Diagnostic & Telemetry Platform**.
If you discover a safety interlock bypass, buffer overflow, cryptographic flaw, or bus-flooding condition:

1. **DO NOT** create a public GitHub issue.
2. Report the vulnerability responsibly via a private **GitHub Security Advisory**
   (repo → Security tab → Advisories) or direct email to the maintainers.
   (Maintainer reporting email is not yet designated — until one is published,
   use GitHub Security Advisories as the reporting channel.)
3. Include reproduction steps, CAN frame traces (Vector `.asc` / `.csv`), and hardware interface specs.

### Coordinated Disclosure Timeline
- **Initial Acknowledgement**: Within 24 hours.
- **Triage & Risk Assessment**: Within 72 hours (Complies with Saha Risk Katalo�Yu v1.2).
- **Patch & Critical Release**: Immediate expedited release based on severity.

## Enforcement Scope Notes (R2 review remainders)

- **License gate (R2-S1):** license enforcement lives in the launcher chain
  (`src/launcher/app.py`); the core entry point (`src/main.py`) performs no
  license check — direct `python src/main.py` execution is outside the
  license threat model (open-core, honest-user assumption). If the commercial
  model ever requires otherwise, wire a launcher-issued HMAC launch ticket
  verified at core startup.
- **E-Stop latch (R2-E1):** the E-Stop latch is process-local by design — a
  process restart clears it. The out-of-band cryptographic reset ceremony
  (`estop_request_challenge` → `scripts/estop_reset_tool.py` → out-of-band
  authorization → `estop_submit_reset_token`, see `docs/runbook/estop-reset.md`)
  stops *remote/JS* actors; a *local* operator can always restart. This is an
  accepted, documented trade-off, not a bypass.
- **Telemetry privacy (R2-S3):** cloud telemetry uploads carry `vehicle_vin`
  only with explicit operator consent (`user_consented=True` on
  `TelemetryUploader.upload_file`); VINs never appear in logs.
