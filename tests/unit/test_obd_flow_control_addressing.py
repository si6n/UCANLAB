"""ISO 15765-4: flow control for a functional request goes to the ECU's physical id."""

from __future__ import annotations

import asyncio
from typing import Any

from src.core.contracts.ports import QueueRxSubscription
from src.core.models.can_frame import CanFrame
from src.protocols.obd.poller import ActiveDiagnosticPoller


class _Port:
    def __init__(self, queue: asyncio.Queue[CanFrame], answer: list[bytes]) -> None:
        self.sent: list[CanFrame] = []
        self._queue = queue
        self._answer = answer

    async def send(self, frame: CanFrame, **_: Any) -> None:
        self.sent.append(frame)
        if frame.data[0] == 0x30:  # flow control -> release the consecutive frames
            for chunk in self._answer[1:]:
                self._queue.put_nowait(CanFrame(channel_id="ch", arbitration_id=0x7E9, dlc=8, data=chunk))
        else:
            self._queue.put_nowait(CanFrame(channel_id="ch", arbitration_id=0x7E9, dlc=8, data=self._answer[0]))


def test_flow_control_id_mapping() -> None:
    fc = ActiveDiagnosticPoller._flow_control_id
    assert fc(0x7DF, 0x7E8) == 0x7E0
    assert fc(0x7DF, 0x7EF) == 0x7E7
    assert fc(0x7E0, 0x7E8) == 0x7E0  # physical requests are unchanged
    assert fc(0x18DB33F1, 0x18DAF110) == 0x18DA10F1
    assert fc(0x18DA10F1, 0x18DAF110) == 0x18DA10F1
    assert fc(0x7DF, 0x123) == 0x7DF  # unknown pair: unchanged


def test_functional_dtc_read_sends_flow_control_to_physical_id() -> None:
    # Mode 03 answer with 4 codes from ECU #2 (0x7E9): needs FF + CF + FC.
    payload = bytes([0x43, 0x04, 0x03, 0x01, 0x04, 0x20, 0x01, 0x71, 0x03, 0x00])
    answer = [bytes([0x10, len(payload)]) + payload[:6], bytes([0x21]) + payload[6:].ljust(7, b"\xaa")]

    async def run() -> tuple[Any, list[CanFrame]]:
        queue: asyncio.Queue[CanFrame] = asyncio.Queue()
        port = _Port(queue, answer)
        poller = ActiveDiagnosticPoller(tx_port=port, rx_subscription=QueueRxSubscription(queue), channel_id="ch")
        result = await poller.poll_dtc_once(0x03, tx_id=0x7DF, rx_id=0x7E9, timeout_s=0.5)
        return result, port.sent

    result, sent = asyncio.run(run())
    assert result.dtcs == ("P0301", "P0420", "P0171", "P0300")
    flow_controls = [f for f in sent if f.data[0] == 0x30]
    assert [f.arbitration_id for f in flow_controls] == [0x7E1]
    assert sent[0].arbitration_id == 0x7DF  # the request itself stays functional
