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

## Secret Store & Key Derivation (A5-2, REVIEW Aşama 5)

The application derives every cryptographic gate from a single
`SecretProvider` (`src/safety/secret_provider.py`): the TX-authorization secret
(`ARM_AUTH_SECRET`), the gateway dual-confirmation secret
(`GATEWAY_CONFIRM_SECRET`), the E-Stop HMAC key, and the cloud session token.

### Protection levels

`SecretProvider.protection_level()` reports the strength actually in use, and
the composition root logs it at startup — a downgrade is never silent.

| Level | Backend | Notes |
| --- | --- | --- |
| `DPAPI` | Windows DPAPI | Per-user OS-protected key material |
| `FILE_AES_GCM_0600` | POSIX AES-256-GCM file backend | HKDF from machine seed |
| `FALLBACK_FILE` | Plain file, no OS protection | Logged as DEGRADED |
| `EPHEMERAL` | In-memory only | Lost on restart — logged as DEGRADED |

### Machine seed and same-user trust boundary

On POSIX the AES-GCM backend derives its key via HKDF from
`machine_seed.bin`, stored **in the same directory as the secret vault** (0600).
This is a deliberate design point, and its limits are explicit:

- **Same-user assumption.** The seed and the vault are both readable by the
  owning user. The design protects against *other* users and at-rest disk
  inspection; it does **not** protect against an attacker already executing as
  the same user. That is the accepted threat-model boundary for this
  deployment (open-core, honest-user assumption — see the license-gate note
  above).
- **Integrity checks (A5-3).** The seed is validated on read: symlinks,
  non-regular files, group/world-accessible modes, and any length other than
  32 bytes are rejected with a typed `SecurityError` (fail-closed). Earlier
  versions accepted a 3-byte seed, a directory, or a symlink, which would have
  enabled vault destruction or controlled key substitution.
- **Windows.** DPAPI binds the vault to the OS user profile, so the POSIX seed
  path does not apply.

Operators who need stronger separation should move the vault to a platform
keyring or an HSM-backed provider; the `SecretProvider` port supports this
without touching call sites.
