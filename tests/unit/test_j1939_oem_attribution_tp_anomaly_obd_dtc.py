"""Unit tests for HIGH-7 (OEM SA/NAME attribution), MEDIUM-7 (TP anomaly
metrics) and LOW-3 (OBD DTC readback modes) remediations."""

from __future__ import annotations

import pytest

from src.core.contracts.ports import InMemoryTxPort, QueueRxSubscription
from src.core.models.can_frame import CanFrame
from src.protocols.j1939.address_claim import J1939Name
from src.protocols.j1939.oem.registry import OemJ1939Registry
from src.protocols.j1939.transport import (
    TP_CTRL_ABORT,
    TP_CTRL_CTS,
    TP_CTRL_RTS,
    J1939TransportProtocol,
    TransportAnomalyMetrics,
)
from src.protocols.obd.models import ObdDtcResult, decode_dtc_pair
from src.protocols.obd.poller import ActiveDiagnosticPoller

# ============================================================================
# HIGH-7: OEM attribution via SA + NAME claim (PGN 60928)
# ============================================================================


def _cummins_claim_frame(sa: int = 0x00) -> CanFrame:
    """PGN 60928 Address Claim from a Cummins-engine (manufacturer code 10)."""
    name = J1939Name(
        arbitrary_address_capable=True,
        industry_group=0,
        vehicle_system_instance=0,
        vehicle_system=0,
        function=0,
        manufacturer_code=10,  # Cummins
        identity_number=1,
    )
    # 0x18EEFF00 | SA — priority 6, PGN 60928, DA 255
    return CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18EEFF00 | sa,
        data=name.to_bytes(),
        is_extended=True,
    )


def _scania_claim_frame(sa: int = 0x33) -> CanFrame:
    name = J1939Name(
        arbitrary_address_capable=True,
        industry_group=0,
        vehicle_system_instance=0,
        vehicle_system=0,
        function=0,
        manufacturer_code=68,  # Scania
        identity_number=2,
    )
    return CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18EEFF00 | sa,
        data=name.to_bytes(),
        is_extended=True,
    )


def test_record_address_claim_learns_manufacturer_code() -> None:
    """A PGN 60928 claim records the SA -> manufacturer code mapping."""
    registry = OemJ1939Registry()
    registry.record_address_claim(_cummins_claim_frame(sa=0x00))
    assert registry._sa_name_codes[0x00] == 10

    # Non-claim PGNs and malformed claims are ignored (fail-closed)
    registry.record_address_claim(
        CanFrame.create(
            channel_id="can0",
            arbitration_id=0x18EEFF00 | 0x11,
            data=b"\x01\x02",  # too short
            is_extended=True,
        )
    )
    assert 0x11 not in registry._sa_name_codes


def test_name_confirmed_match_wins_over_registration_order() -> None:
    """A NAME-confirmed Cummins attribution must not be labeled another OEM.

    PGN 61184 (Proprietary A) is every OEM's shared channel; previously the
    first decoder in registration order (Cummins) silently claimed the frame.
    With a Scania NAME claim recorded for the SA, the Scania decoder is the
    CONFIRMED match and must win even though Cummins is registered first.
    """
    registry = OemJ1939Registry()
    registry.record_address_claim(_scania_claim_frame(sa=0x33))

    # Scania Proprietary A service routine frame from SA 0x33 — command ids
    # live in the decoder's local map (scania.py _decode_proprietary_a):
    # 0x10 / 0x12 / 0x14 are Scania-only (Cummins: 0x3A/0x3B, Volvo: 0x01/
    # 0x05/0x09, so 0x12 payload-matches ONLY the Scania decoder).
    from src.protocols.j1939.oem.scania import ScaniaDecoder

    scania_cmd = 0x12  # Scania DPF Forced Regeneration Request
    assert scania_cmd not in (getattr(ScaniaDecoder, "CMD_DPF_FORCED_REGEN_START", 0x3A),)

    frame = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18EF0033,  # PGN 61184, DA 0x00, SA 0x33
        data=bytes([scania_cmd, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
        is_extended=True,
    )
    decoded = registry.decode_frame(frame)
    assert decoded is not None
    assert decoded.manufacturer == "Scania"
    assert decoded.confidence == "HIGH"


def test_contradicted_decoder_is_skipped() -> None:
    """A decoder whose OEM contradicts the SA's recorded NAME is skipped."""
    registry = OemJ1939Registry()
    # SA 0x00 claimed by Cummins NAME...
    registry.record_address_claim(_cummins_claim_frame(sa=0x00))
    # ...but we feed a Volvo Proprietary-A service frame from SA 0x00 that
    # would payload-match the Volvo decoder. The NAME says Cummins, so the
    # Volvo attribution must be skipped — the frame decodes either as
    # Cummins (if the payload matches Cummins) or not at all, never Volvo.
    # Volvo command ids live in the decoder's local map (volvo.py
    # _decode_proprietary_a): 0x01 / 0x05 / 0x09 — no CMD_* class attrs.
    volvo_cmd = 0x05  # Volvo DPF Stationary Regeneration Request
    assert volvo_cmd not in (0x3A, 0x3B)  # not a Cummins command id

    frame = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18EF0000,
        data=bytes([volvo_cmd, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
        is_extended=True,
    )
    decoded = registry.decode_frame(frame)
    if decoded is not None:
        assert decoded.manufacturer != "Volvo"


def test_unconfirmed_match_is_low_confidence() -> None:
    """Without any NAME claim the first payload-pattern match surfaces LOW."""
    registry = OemJ1939Registry()
    from src.protocols.j1939.oem.cummins import CumminsDecoder

    regen_cmd = getattr(CumminsDecoder, "CMD_DPF_FORCED_REGEN_START", None)
    assert regen_cmd is not None

    frame = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18EF00F9,
        data=bytes([regen_cmd, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
        is_extended=True,
    )
    decoded = registry.decode_frame(frame)
    assert decoded is not None
    assert decoded.confidence == "LOW"


def test_hinted_match_not_stamped_low() -> None:
    """An explicit operator hint is operator grounding — never LOW."""
    registry = OemJ1939Registry()
    from src.protocols.j1939.oem.cummins import CumminsDecoder

    regen_cmd = getattr(CumminsDecoder, "CMD_DPF_FORCED_REGEN_START", None)
    assert regen_cmd is not None

    frame = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18EF00F9,
        data=bytes([regen_cmd, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
        is_extended=True,
    )
    decoded = registry.decode_frame(frame, manufacturer_hint="Cummins")
    assert decoded is not None
    assert decoded.confidence != "LOW"


def test_confirmed_match_is_high_confidence() -> None:
    """Cummins NAME claim + Cummins-payload frame -> HIGH confidence."""
    registry = OemJ1939Registry()
    registry.record_address_claim(_cummins_claim_frame(sa=0x00))

    from src.protocols.j1939.oem.cummins import CumminsDecoder

    regen_cmd = getattr(CumminsDecoder, "CMD_DPF_FORCED_REGEN_START", None)
    frame = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18EF0000,  # SA 0x00 = the claimed Cummins engine
        data=bytes([regen_cmd, 0xFF, 0xB2, 0xA1, 0x00, 0x00, 0x00, 0x00]),
        is_extended=True,
    )
    decoded = registry.decode_frame(frame)
    assert decoded is not None
    assert decoded.manufacturer == "Cummins"
    assert decoded.confidence == "HIGH"


# ============================================================================
# MEDIUM-7: TP anomaly metrics (ABORT / CTS storm observability)
# ============================================================================


def _tp_cm_frame(ctrl: int, sa: int, target_pgn: int, da: int = 0xF9) -> CanFrame:
    data = bytearray(8)
    data[0] = ctrl
    data[1:3] = (14).to_bytes(2, byteorder="little")
    data[3] = 2
    data[4] = 0xFF
    data[5:8] = target_pgn.to_bytes(3, byteorder="little")
    # 0x1CECDA00 | SA — priority 7, PGN 60416, DA, SA.
    # PS byte (bits 15..8) is the DA for PDU1 (PF=0xEC < 0xF0) — must be
    # zeroed before OR-ing, otherwise a stale 0xFF masks the real DA.
    return CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CEC0000 | (da << 8) | sa,
        data=bytes(data),
        is_extended=True,
    )


def test_tp_anomaly_metrics_count_rts_bam_abort() -> None:
    tp = J1939TransportProtocol(my_address=0xF9)
    # RTS addressed to us (valid size) -> rx_rts
    tp.handle_rx_frame(_tp_cm_frame(TP_CTRL_RTS, sa=0x01, target_pgn=65226))
    # Abort -> rx_abort
    tp.handle_rx_frame(_tp_cm_frame(TP_CTRL_ABORT, sa=0x01, target_pgn=65226))
    m = tp.anomaly_metrics
    assert m.rx_rts == 1
    assert m.rx_abort == 1
    assert m.abort_ratio == 0.5


def test_tp_unsolicited_cts_flags_suspect() -> None:
    """A CTS with no matching sender session is flagged immediately."""
    tp = J1939TransportProtocol(my_address=0xF9)
    tp.handle_rx_frame(_tp_cm_frame(TP_CTRL_CTS, sa=0x44, target_pgn=65226))
    m = tp.anomaly_metrics
    assert m.rx_cts == 1
    assert m.rx_unsolicited_cts == 1
    assert m.is_suspect() is True
    snap = m.snapshot()
    assert snap["is_suspect"] is True
    assert snap["rx_unsolicited_cts"] == 1


def test_tp_abort_storm_triggers_suspect_heuristic() -> None:
    """A burst of Abort frames (bus-hijack attempt) trips the alarm once
    the abort ratio exceeds the threshold with enough traffic."""
    tp = J1939TransportProtocol(my_address=0xF9)
    for i in range(15):
        tp.handle_rx_frame(_tp_cm_frame(TP_CTRL_ABORT, sa=0x55, target_pgn=65226 + (i % 4)))
    m = tp.anomaly_metrics
    assert m.rx_abort == 15
    # min_frames default is 20 — 15 frames are below it, so NOT suspect yet.
    # Verify the gate actually needs the traffic volume:
    assert m.is_suspect(min_frames=10) is True
    assert m.is_suspect() is False


def test_tp_metrics_empty_and_healthy_states() -> None:
    m = TransportAnomalyMetrics()
    assert m.abort_ratio == 0.0
    assert m.is_suspect() is False
    m.rx_rts = 10
    m.rx_cts = 10
    assert m.abort_ratio == 0.0
    assert m.is_suspect() is False


def test_tp_emitted_abort_is_counted() -> None:
    """Our own emitted Abort frames are observable (tx_abort_emitted)."""
    tp = J1939TransportProtocol(my_address=0xF9)
    # Valid RTS to us -> creates an RX session; a second colliding RTS on
    # the same key aborts the first (emits TP.Conn_Abort reason=2).
    tp.handle_rx_frame(_tp_cm_frame(TP_CTRL_RTS, sa=0x01, target_pgn=65226))
    resp = tp.handle_rx_frame(_tp_cm_frame(TP_CTRL_RTS, sa=0x01, target_pgn=65226))
    assert tp.anomaly_metrics.tx_abort_emitted >= 1
    assert resp[1] is not None


# ============================================================================
# LOW-3: OBD Mode 03 / 07 / 0A DTC readback
# ============================================================================


def test_decode_dtc_pair_text_form() -> None:
    assert decode_dtc_pair(0x01, 0x23) == "P0123"
    assert decode_dtc_pair(0x42, 0x10) == "C0210"
    assert decode_dtc_pair(0x82, 0x00) == "B0200"
    assert decode_dtc_pair(0xC1, 0x00) == "U0100"
    assert decode_dtc_pair(0x03, 0xFF) == "P03FF"


def test_register_dtc_mode_validates_mode_and_rate() -> None:
    poller = ActiveDiagnosticPoller(tx_port=InMemoryTxPort())
    with pytest.raises(ValueError, match="must be 0x03, 0x07 or 0x0A"):
        poller.register_dtc_mode(0x02, rate_hz=1.0, callback=lambda r: None)
    with pytest.raises(ValueError, match="exceeds ceiling"):
        poller.register_dtc_mode(0x03, rate_hz=100.0, callback=lambda r: None)


def test_dtc_mode_request_frame_and_lifecycle() -> None:
    tx_port = InMemoryTxPort()
    poller = ActiveDiagnosticPoller(tx_port=tx_port)

    results: list[ObdDtcResult] = []
    poller.register_dtc_mode(0x03, rate_hz=1.0, callback=results.append)

    job = poller.step()
    assert job is not None
    assert job.kind == "obd_dtc_mode"
    frame = tx_port.sent_frames[-1]
    # SAE J1979 Mode 03 request: single byte mode, no PID byte
    assert frame.data[0] == 0x01  # PCI: 1 data byte
    assert frame.data[1] == 0x03  # Mode 03

    # ECU response: SID 0x43, 2 DTCs: P0123 + U0100
    resp = CanFrame.create(
        channel_id="obd_ch0",
        arbitration_id=0x7E8,
        data=bytes([0x06, 0x43, 0x02, 0x01, 0x23, 0xC1, 0x00, 0x00]),
        direction="rx",
    )
    result, fc = poller.process_rx_frame(resp)
    assert fc is None
    assert result is not None
    assert isinstance(result, ObdDtcResult)
    assert result.mode == 0x03
    assert result.dtc_count == 2
    assert result.dtcs == ("P0123", "U0100")
    assert result.is_valid is True
    assert len(results) == 1

    assert poller.unregister_dtc_mode(0x03) is True
    assert poller.unregister_dtc_mode(0x03) is False


def test_dtc_mode_payload_shorter_than_declared_count_is_flagged() -> None:
    """Zero-fabrication: ECU declares 3 DTCs but payload carries only 2 pairs."""
    tx_port = InMemoryTxPort()
    poller = ActiveDiagnosticPoller(tx_port=tx_port)
    poller.register_dtc_mode(0x07, rate_hz=1.0, callback=lambda r: None)
    poller.step()

    resp = CanFrame.create(
        channel_id="obd_ch0",
        arbitration_id=0x7E8,
        data=bytes([0x06, 0x47, 0x03, 0x01, 0x23, 0xC1, 0x00, 0x00]),  # 2 pairs present
        direction="rx",
    )
    result, _ = poller.process_rx_frame(resp)
    assert result is not None
    assert result.dtc_count == 3
    assert result.dtcs == ("P0123", "U0100")  # only physically present pairs
    assert result.is_valid is False
    assert result.error_message is not None


def test_dtc_mode_response_only_completes_matching_mode() -> None:
    """A Mode 03 response must not complete a pending Mode 0A job."""
    tx_port = InMemoryTxPort()
    poller = ActiveDiagnosticPoller(tx_port=tx_port)
    poller.register_dtc_mode(0x0A, rate_hz=1.0, callback=lambda r: None)
    poller.step()
    job_before = poller._active_job

    wrong_mode_resp = CanFrame.create(
        channel_id="obd_ch0",
        arbitration_id=0x7E8,
        data=bytes([0x04, 0x43, 0x01, 0x01, 0x23, 0x00, 0x00, 0x00]),  # SID 0x43 = Mode 03
        direction="rx",
    )
    result, _ = poller.process_rx_frame(wrong_mode_resp)
    # Result decodes (Mode 03 payload observed on the bus), but the pending
    # Mode 0A job is NOT completed by it.
    assert result is not None
    assert result.mode == 0x03
    assert poller._active_job is job_before


@pytest.mark.asyncio
async def test_poll_dtc_once_one_shot() -> None:
    tx_port = InMemoryTxPort()
    rx_sub = QueueRxSubscription()
    poller = ActiveDiagnosticPoller(tx_port=tx_port, rx_subscription=rx_sub)

    resp = CanFrame.create(
        channel_id="obd_ch0",
        arbitration_id=0x7E8,
        data=bytes([0x04, 0x4A, 0x01, 0x02, 0x00, 0x00, 0x00, 0x00]),  # P0200
        direction="rx",
    )
    rx_sub.put_nowait(resp)

    result = await poller.poll_dtc_once(mode=0x0A, timeout_s=1.0)
    assert result.mode == 0x0A
    assert result.dtcs == ("P0200",)
    assert result.is_valid is True

    with pytest.raises(ValueError):
        await poller.poll_dtc_once(mode=0x04, timeout_s=0.1)
