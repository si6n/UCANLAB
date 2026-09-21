"""Single source of truth for "critical" (state-mutating) diagnostic identities.

REVIEW Aşama 1 (S1-P1-3): the gateway's critical-SID set and the replay
filter's prohibited-SID set had drifted apart. The replay path was STRICTER
than the live TX path — the exact inversion the safety architecture forbids
(a replayed dangerous frame was blocked, while the same frame injected live
through the gateway was classified non-critical and skipped the speed
interlock + HMAC dual-confirmation stages).

This module is the canonical catalogue. Both the gateway (live TX
criticality derivation) and the replay safety filter (replay blocking)
import from here so the two policies can never disagree again.

Design notes
------------
* ``CRITICAL_UDS_SIDS`` — UDS service identifiers whose transmission mutates
  ECU state (session/reset/clear/security/routine/write/communication
  control/file transfer/memory write/link control).
* ``CRITICAL_READONLY_UDS_SIDS`` — SIDs that are dangerous to *broadcast*
  from a replay log but are NOT state-mutating in the TX-criticality sense,
  so they must not force a live, legitimately-requested frame onto the
  speed interlock. ``0x3E`` (TesterPresent) is the canonical example: it is
  a session keep-alive, and pinning it to the physical-speed interlock would
  make a normal diagnostic session unusable.
* J1939 write/actuate PGNs, and the PGNs that are dangerous when *requested*
  through PGN 59904 (Request) — because a Request is a "remote command" for
  the ECU that owns the target PGN (J1939-21 §5.4.2).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# UDS
# ---------------------------------------------------------------------------

#: UDS SIDs that mutate ECU state. A whitelisted ISO-TP single/first frame
#: carrying one of these is critical on EITHER the 11-bit or the 29-bit
#: (ISO 15765-4 / ``0x18DAxxxx``) addressing path.
#:
#: S-07: ``0x35`` (RequestUpload) was REMOVED — it only READS ECU memory out
#: (a read, not a mutation), so a live, legitimately-requested upload must not
#: be pinned to the speed interlock. It is replay-dangerous (bulk memory
#: exfiltration) and now lives in ``REPLAY_ONLY_PROHIBITED_UDS_SIDS``.
#: ``0x29`` (Authentication), ``0x2C`` (DynamicallyDefineDataIdentifier),
#: ``0x3B`` (WriteDataByIdentifier at a specific address),
#: ``0x84`` (SecuredDataTransmission) and ``0x86`` (ResponseOnEvent) ARE
#: state-mutating and were added.
CRITICAL_UDS_SIDS: frozenset[int] = frozenset(
    {
        0x10,  # DiagnosticSessionControl   — switches to programming session
        0x11,  # ECUReset                   — resets the ECU
        0x14,  # ClearDiagnosticInformation — wipes DTC evidence
        0x27,  # SecurityAccess             — unlocks protected services
        0x28,  # CommunicationControl       — can silence an ECU
        0x29,  # Authentication             — establishes an authenticated session
        0x2C,  # DynamicallyDefineDataId    — mutates the DID table
        0x2E,  # WriteDataByIdentifier      — persists a value
        0x2F,  # InputOutputControlById     — drives an actuator
        0x31,  # RoutineControl             — runs a routine (erase/self-test)
        0x34,  # RequestDownload            — starts a firmware transfer
        0x36,  # TransferData               — firmware body
        0x37,  # RequestTransferExit        — commits the transfer
        0x38,  # RequestFileTransfer        — file-system transfer
        0x3B,  # WriteDataByIdentifier (addr) — persists a value at an address
        0x3D,  # WriteMemoryByAddress       — raw memory write
        0x84,  # SecuredDataTransmission    — carries a secured (mutating) payload
        0x85,  # ControlDTCSetting          — suppresses DTC reporting
        0x86,  # ResponseOnEvent            — arms autonomous ECU reactions
        0x87,  # LinkControl                — changes bus bit rate
    }
)

#: SIDs that are blocked in REPLAY (dangerous to re-inject from a log) but
#: are NOT TX-criticality signals: they carry no standing-state mutation by
#: themselves, so a live frame must not be pushed onto the physical-speed
#: interlock for them. ``0x3E`` TesterPresent is a benign session keep-alive
#: required by every UDS flow (P2* timeout); ``0x35`` RequestUpload is the
#: S-07 relocation (bulk memory read-out, replay-prohibited).
REPLAY_ONLY_PROHIBITED_UDS_SIDS: frozenset[int] = frozenset({0x3E, 0x35})

#: Full replay-prohibited set = critical mutation set + replay-only extras.
PROHIBITED_UDS_SIDS: frozenset[int] = CRITICAL_UDS_SIDS | REPLAY_ONLY_PROHIBITED_UDS_SIDS


# ---------------------------------------------------------------------------
# J1939
# ---------------------------------------------------------------------------

#: J1939 PGNs whose (re)transmission directly clears / mutates diagnostic or
#: controller state.
#:
#: S-03: ``65229`` (DM4) and ``65230`` (DM5) are NOT clear commands. Per SAE
#: J1939-73 they are READ PGNs — DM4 carries freeze-frame data and DM5 carries
#: diagnostic readiness — so a live poll of either must not be escalated to the
#: speed interlock + dual confirmation. The previous comments ("Freeze Frame
#: Clear" / "Diagnostic Readiness Clear") stated the opposite of the standard
#: and were the reason for their inclusion. They stay allowed, read-only, in
#: ``READONLY_DIAGNOSTIC_J1939_PGNS`` below.
CRITICAL_J1939_PGNS: frozenset[int] = frozenset(
    {
        65235,  # DM11 — Clear Active DTCs
        65228,  # DM3  — Clear Previously Active DTCs
        65240,  # Commanded Address (re-addresses an ECU, J1939-81)
        0,      # TSC1 — Torque/Speed Control 1 (direct drivetrain command)
        1024,   # XBR  — External Brake Request
    }
)

#: S-03: J1939-73 READ-only diagnostic PGNs. They are legitimate polls an
#: operator may issue against a live vehicle, so they must NOT force the
#: physical-speed interlock; they remain explicitly catalogued here so no
#: allowlist silently drops them and so the policy is auditable.
READONLY_DIAGNOSTIC_J1939_PGNS: frozenset[int] = frozenset(
    {
        65226,  # DM1  — Active DTCs
        65227,  # DM2  — Previously Active DTCs
        65228,  # DM3  — (read form) Previously Active DTCs / clear-trigger context
        65229,  # DM4  — Freeze Frame Parameters (READ, J1939-73)
        65230,  # DM5  — Diagnostic Readiness 1 (READ, J1939-73)
    }
)

#: J1939-21 Request PGN. The first 3 payload bytes name the PGN the sender
#: is asking an ECU to transmit — so a Request is an indirect command for a
#: *writable* PGN (DM11 etc.) and must be treated as critical too.
J1939_REQUEST_PGN: int = 59904
