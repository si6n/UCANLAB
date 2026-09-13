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
- **Triage & Risk Assessment**: Within 72 hours (Complies with Saha Risk Kataloğu v1.2).
- **Patch & Critical Release**: Immediate expedited release based on severity.
