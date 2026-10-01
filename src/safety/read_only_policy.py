"""Read-only OBD-II session policy (MECHANIC_FLOW.md §7.2, Aşama 6).

A passenger car only reports its fault codes when asked, so reading them needs
a request frame on the wire. This policy is the narrow exception that allows
exactly that and nothing else. While it is installed on the
``TxSafetyGateway`` every outgoing frame must be:

* an 11-bit, classic-CAN frame to the OBD functional address ``0x7DF`` or a
  physical request address ``0x7E0``–``0x7E7``;
* an ISO-TP *single frame* whose service byte is a read-only SAE J1979 mode:
  ``0x01`` (current data), ``0x03`` (stored codes), ``0x07`` (pending codes),
  ``0x09`` (vehicle information / VIN) or ``0x0A`` (permanent codes);
* or an ISO-TP *flow control* "continue to send" (``0x30``) to a physical
  address, which a tester must send to receive a multi-frame answer.

Everything else — Mode 04 (clear codes), any UDS service, any J1939 frame,
multi-frame requests, CAN-FD — is refused, and a critical-classified frame is
refused even if it would otherwise match. The policy also carries an expiry:
after it, every frame is refused until a new explicit consent.

The policy never *grants* TX by itself: the gateway's other stages (safety
state, watchdog lease, E-Stop, whitelist, rate budget) still apply.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.models.can_frame import CanFrame

OBD_FUNCTIONAL_ID = 0x7DF
OBD_PHYSICAL_REQUEST_IDS = frozenset(range(0x7E0, 0x7E8))
READ_ONLY_OBD_SERVICES = frozenset({0x01, 0x03, 0x07, 0x09, 0x0A})
_FLOW_CONTROL_CONTINUE = 0x30


@dataclass(frozen=True)
class ReadOnlyPolicy:
    """Installed on the gateway for one consented read session."""

    expires_ns: int
    reason: str = "mechanic read-only session"

    def violation(self, frame: CanFrame, now_ns: int) -> str | None:
        """None when the frame is a permitted read request, else the reason code."""
        if now_ns >= self.expires_ns:
            return "READ_ONLY_EXPIRED"
        if frame.is_extended or frame.is_fd:
            return "READ_ONLY_ID"
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
