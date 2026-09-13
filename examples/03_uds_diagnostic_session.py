"""Example 03: ISO 14229 UDS Diagnostic Session Control & DID Reader.

Demonstrates UDS Service 0x22 (ReadDataByIdentifier) request construction.

SAFETY BANNER (listen-only example):
- This script only BUILDS request bytes in memory — it never opens a bus,
  never transmits, and never touches a vehicle.
- Any on-bus use MUST go through TxSafetyGateway (single audited TX
  choke-point): validate + whitelist + E-Stop + speed-interlock.
- Do NOT copy these builders into a transmit path without the gateway.
"""

from src.protocols.uds.services import DiagnosticSessionType, UdsServiceBuilder


def main():
    print("=== ISO 14229 UDS Diagnostic Client Demo ===")

    # 1. Build Extended Diagnostic Session Request (0x10 0x03)
    session_req = UdsServiceBuilder.build_diagnostic_session_control(DiagnosticSessionType.EXTENDED_DIAGNOSTIC_SESSION)
    print(f"1. UDS 0x10 Extended Session Request: {session_req.hex(' ')}")

    # 2. Build Read DID 0xF190 (VIN Number) Request (0x22 0xF1 0x90)
    did_req = UdsServiceBuilder.build_read_data_by_identifier(0xF190)
    print(f"2. UDS 0x22 Read DID (VIN 0xF190) Request: {did_req.hex(' ')}")

    # 3. Build Routine Control Request (0x31 0x01 0x02 0x02) Checksum
    routine_req = UdsServiceBuilder.build_routine_control(1, 0x0202)
    print(f"3. UDS 0x31 Routine Start (0x0202) Request: {routine_req.hex(' ')}")


if __name__ == "__main__":
    main()
