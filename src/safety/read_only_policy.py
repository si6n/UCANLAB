"""Read-only OBD-II session policy (MECHANIC_FLOW.md §7.2, Aşama 6).

A passenger car only reports its fault codes when asked, so reading them needs
a request frame on the wire. This policy is the narrow exception that allows
exactly that and nothing else. While it is installed on the
``TxSafetyGateway`` every outgoing frame must be:

* an 11-bit, classic-CAN frame to the OBD functional address ``0x7DF`` or a
  physical request address ``0x7E0``–``0x7E7``;
* an ISO-TP *single frame* whose service byte is a read-only SAE J1979 mode:
  ``0x01`` (current data), ``0x02`` (freeze frame data), ``0x03`` (stored
  codes), ``0x06`` (on-board monitoring test results), ``0x07`` (pending
  codes), ``0x09`` (vehicle information / VIN) or ``0x0A`` (permanent codes);
* or an ISO-TP *flow control* "continue to send" (``0x30``) to a physical
  address, which a tester must send to receive a multi-frame answer.

Everything else — Mode 04 (clear codes), any UDS service, any J1939 frame,
multi-frame requests, CAN-FD — is refused, and a critical-classified frame is
refused even if it would otherwise match.

A heavy-duty read session (``j1939=True``) additionally allows exactly three
29-bit frame kinds, for SAE J1939-73 reads:

* a Request (PGN 59904) whose requested PGN is a read-only diagnostic or
  identification message (``READ_ONLY_J1939_REQUEST_PGNS``: DM1, DM2, DM4
  freeze frame, DM5, DM6, DM12, DM19 calibration information, VI vehicle
  identification, SOFT software identification, CI component identification)
  — never DM3/DM11 (clear) or anything else;
* a DM7 (PGN 58112) with test identifier 247 and FMI 31: "report the results
  of the tests already run for this SPN" (answered with DM30). Test
  identifiers 1–245 command a test to run and are refused;
* a transport-protocol answer (TP.CM CTS / end-of-message ACK / abort) to a
  specific ECU for one of the PGNs above, which a receiver must send to get a
  multi-packet answer.

The policy also carries an expiry: after it, every frame is refused until a
new explicit consent.

The policy never *grants* TX by itself: the gateway's other stages (safety
state, watchdog lease, E-Stop, whitelist, rate budget) still apply.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.models.can_frame import CanFrame

OBD_FUNCTIONAL_ID = 0x7DF
OBD_PHYSICAL_REQUEST_IDS = frozenset(range(0x7E0, 0x7E8))
# Modes 02 and 06 only READ what the ECU already stored (freeze frame, monitor
# test results); Mode 04 (clear) and Mode 08 (on-board control) stay refused.
READ_ONLY_OBD_SERVICES = frozenset({0x01, 0x02, 0x03, 0x06, 0x07, 0x09, 0x0A})
_FLOW_CONTROL_CONTINUE = 0x30

# SAE J1939-73 read-only messages a heavy-duty read session may request.
READ_ONLY_J1939_REQUEST_PGNS = frozenset({
    65226, 65227, 65229, 65230, 65231, 65236,  # DM1 DM2 DM4 DM5 DM6 DM12
    54016, 65260, 65242, 65259,  # DM19 (calibration info), VI (VIN), SOFT, CI: identification only
})
_J1939_PF_REQUEST = 0xEA
_J1939_PF_DM7 = 0xE3
_J1939_PF_TP_CM = 0xEC
_J1939_DM7_TID_REPORT = 247
_J1939_FMI_ALL = 31
_J1939_PGN_DM30 = 41984
# TP.CM control bytes a receiver sends: clear to send, end-of-message ACK, abort.
_J1939_TP_RECEIVER_CONTROLS = frozenset({0x11, 0x13, 0xFF})
_J1939_TP_PGNS = READ_ONLY_J1939_REQUEST_PGNS | {_J1939_PGN_DM30}


def _j1939_violation(frame: CanFrame) -> str | None:
    """None when a 29-bit frame is one of the three read-only J1939 kinds (module docstring)."""
    arbitration_id = frame.arbitration_id
    if (arbitration_id >> 24) & 0x03:  # extended data page / data page: not J1939-73
        return "READ_ONLY_ID"
    pf = (arbitration_id >> 16) & 0xFF
    da = (arbitration_id >> 8) & 0xFF
    data = bytes(frame.data)
    if pf == _J1939_PF_REQUEST:
        if len(data) < 3 or any(b != 0xFF for b in data[3:]):
            return "READ_ONLY_PAYLOAD"
        requested = int.from_bytes(data[:3], "little")
        return None if requested in READ_ONLY_J1939_REQUEST_PGNS else "READ_ONLY_SERVICE"
    if pf == _J1939_PF_DM7:
        if len(data) != 8 or any(b != 0xFF for b in data[4:]):
            return "READ_ONLY_PAYLOAD"
        if data[0] != _J1939_DM7_TID_REPORT or data[3] & 0x1F != _J1939_FMI_ALL:
            return "READ_ONLY_SERVICE"  # any other test identifier commands a test
        return None
    if pf == _J1939_PF_TP_CM:
        if da == 0xFF or len(data) != 8 or data[0] not in _J1939_TP_RECEIVER_CONTROLS:
            return "READ_ONLY_PAYLOAD"
        return None if int.from_bytes(data[5:8], "little") in _J1939_TP_PGNS else "READ_ONLY_PAYLOAD"
    return "READ_ONLY_ID"


@dataclass(frozen=True)
class ReadOnlyPolicy:
    """Installed on the gateway for one consented read session."""

    expires_ns: int
    reason: str = "mechanic read-only session"
    # Heavy-duty read session: also allow the read-only J1939-73 requests.
    j1939: bool = False

    def violation(self, frame: CanFrame, now_ns: int) -> str | None:
        """None when the frame is a permitted read request, else the reason code."""
        if now_ns >= self.expires_ns:
            return "READ_ONLY_EXPIRED"
        if frame.is_fd:
            return "READ_ONLY_ID"
        if frame.is_extended:
            return _j1939_violation(frame) if self.j1939 else "READ_ONLY_ID"
        arbitration_id = frame.arbitration_id
        if arbitration_id != OBD_FUNCTIONAL_ID and arbitration_id not in OBD_PHYSICAL_REQUEST_IDS:
            return "READ_ONLY_ID"
        data = bytes(frame.data)
        if not data:
            return "READ_ONLY_PAYLOAD"
        pci = data[0] >> 4
        if pci == 0x0:
            length = data[0] & 0x0F
            if not 1 <= length <= 7 or len(data) < 1 + length:
                return "READ_ONLY_PAYLOAD"
            return None if data[1] in READ_ONLY_OBD_SERVICES else "READ_ONLY_SERVICE"
        if pci == 0x3:
            if data[0] == _FLOW_CONTROL_CONTINUE and arbitration_id in OBD_PHYSICAL_REQUEST_IDS:
                return None
            return "READ_ONLY_PAYLOAD"
        # First/consecutive frames: a read request always fits in one frame.
        return "READ_ONLY_PAYLOAD"
