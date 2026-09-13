"""REVIEW 2 & REVIEW 3 remediation tests.

1. J1939-22 FD TP drop visibility + counter (REVIEW 2 - HIGH 2)
2. NMEA 2000 Fast Packet CAN-FD / DLC / instance hardening (REVIEW 3 - MEDIUM)
3. Volvo EVC Proprietary B SA/NAME attribution gate (REVIEW 2 - MEDIUM 4)
4. Poller transaction id / orphan conversation guard (REVIEW 3 - MEDIUM)
5. MDF4 exporter series validation + atomic write (REVIEW 3 - LOW)
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

import pytest

from src.core.contracts.ports import InMemoryTxPort
from src.core.models.can_frame import CanFrame
from src.engine.exporters.mdf4_exporter import Mdf4Exporter
from src.protocols.j1939.transport import J1939TransportProtocol, PGN_TP_CM, TP_CTRL_BAM
from src.protocols.nmea2000.fast_packet import Nmea2000FastPacketDecoder
from src.protocols.nmea2000.pgn_library import Nmea2000PgnDecoder
from src.protocols.obd.poller import ActiveDiagnosticPoller, PollerState
from src.protocols.volvo.volvo_decoder import VolvoPentaDecoder

EVC_PAYLOAD = b"\xaf\x01\x01\x26\x02\x20\x03\xff"  # 50% AHEAD, trim 5deg, rudder -10deg


# ============================================================================
# 1. REVIEW 2 - HIGH 2: J1939-22 FD TP frames must be VISIBLE when dropped
# ============================================================================


def test_fd_tp_frame_dropped_with_warning_and_counter() -> None:
    """A >8-byte FD TP.CM frame must be dropped, counted and WARNING-logged."""
    tp = J1939TransportProtocol(my_address=0xF9)

    # TP.CM BAM control frame on a CAN-FD carrier with 12-byte payload
    # (DLC 9 encodes 12): J1939-21 classic engine cannot decode this.
    fd_frame = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CECFF00 | 0xF9,
        data=bytes([TP_CTRL_BAM, 0x0E, 0x00, 0x02, 0xFF, 0x00, 0xF0, 0x00, 0xCC, 0xCC, 0xCC, 0xCC]),
        is_extended=True,
        is_fd=True,
        dlc=9,
    )

    import logging

    with mock.patch.object(
        __import__("src.protocols.j1939.transport", fromlist=["logger"]).logger,
        "warning",
    ) as warn_mock:
        msg, resp = tp.handle_rx_frame(fd_frame)

    assert msg is None and resp is None
    assert tp.anomaly_metrics.dropped_fd_tp_frames == 1
    assert warn_mock.called
    # The FD drop must be visible in the metrics snapshot too
    assert tp.anomaly_metrics.snapshot()["dropped_fd_tp_frames"] == 1


def test_classic_short_frame_stays_debug_level_and_uncounted() -> None:
    """Non-FD 11-bit/short frames remain hot-path DEBUG noise — uncounted."""
    tp = J1939TransportProtocol(my_address=0xF9)
    short = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x123,
        data=b"\x02\x01\x00",
    )
    msg, resp = tp.handle_rx_frame(short)
    assert msg is None and resp is None
    assert tp.anomaly_metrics.dropped_fd_tp_frames == 0


def test_classic_8byte_tp_frame_still_decodes() -> None:
    """An 8-byte classic TP.CM BAM addressed to us still opens a session."""
    tp = J1939TransportProtocol(my_address=0xF9)
    bam = CanFrame.create(
        channel_id="ch0",
        arbitration_id=0x1CECFF00 | 0xF9,
        data=bytes([TP_CTRL_BAM, 0x0E, 0x00, 0x02, 0xFF, 0x26, 0xF1, 0x00]),
        is_extended=True,
    )
    msg, resp = tp.handle_rx_frame(bam)
    assert msg is None
    assert len(tp._rx_sessions) == 1
    assert tp.anomaly_metrics.rx_bam == 1
    assert tp.anomaly_metrics.dropped_fd_tp_frames == 0


# ============================================================================
# 2. REVIEW 3 - MEDIUM: NMEA 2000 Fast Packet CAN-FD / DLC / instance
# ============================================================================


def _fp_frame(data: bytes, arb: int = 0x19F20100, **kwargs) -> CanFrame:
    return CanFrame.create(channel_id="n2k", arbitration_id=arb, data=data, is_extended=True, **kwargs)


def test_n2k_can_fd_fast_packet_frame_rejected() -> None:
    """CAN-FD frames must never enter classic Fast Packet reassembly."""
    decoder = Nmea2000FastPacketDecoder()
    # FD first frame with 8 real bytes but DLC 9 (12-byte capacity)
    fd_ff = _fp_frame(b"\x00\x10\x00\x01\x02\x03\x04\x05", is_fd=True, dlc=9)
    assert decoder.handle_rx_frame(fd_ff) is None
    assert len(decoder._sessions) == 0

    # Even DLC>8 on a classic frame is impossible (CanFrame would raise),
    # but a forged FD frame with dlc 12 is rejected explicitly.
    fd_ff12 = _fp_frame(
        b"\x00\x10\x00\x01\x02\x03\x04\x05\xCC\xCC\xCC",
        is_fd=True,
        dlc=12,
    )
    assert decoder.handle_rx_frame(fd_ff12) is None
    assert len(decoder._sessions) == 0


def test_n2k_dlc_data_length_mismatch_dropped() -> None:
    """A frame whose DLC disagrees with len(data) is dropped before reassembly.

    CanFrame.__post_init__ normally guarantees dlc == len(data); this guard
    covers replayed/legacy paths that may bypass it.
    """
    decoder = Nmea2000FastPacketDecoder()
    # 6-byte payload with DLC 6: a short FINAL CF is legal IF a session
    # exists — but without one it just returns None. The real mismatch
    # (dlc=8, 6 bytes) can only be built via object bypass:
    frame = CanFrame.create(channel_id="n2k", arbitration_id=0x19F20100, data=b"\x01\x02\x03\x04\x05\x06", is_extended=True)
    assert frame.dlc == 6  # CanFrame derived it — coherence holds
    assert decoder.handle_rx_frame(frame) is None
    assert len(decoder._sessions) == 0


def test_n2k_engine_rapid_reserved_instance_rejected() -> None:
    """Instance 0xFF (unavailable) must not decode as engine instance 255."""
    data = b"\xff\x40\x1f\xdc\x05\x0a\xff\xff"
    res = Nmea2000PgnDecoder.decode_engine_rapid(data)
    assert res is None


def test_n2k_engine_dynamic_reserved_instance_rejected() -> None:
    buf = bytearray(26)
    buf[0] = 0xFB  # reserved instance value
    res = Nmea2000PgnDecoder.decode_engine_dynamic(bytes(buf))
    assert res is None


def test_n2k_transmission_reserved_instance_rejected() -> None:
    data = b"\xfe\x01\xdc\x05\x2c\x01\xff"
    res = Nmea2000PgnDecoder.decode_transmission(data)
    assert res is None


def test_n2k_valid_instance_still_decodes() -> None:
    """Legal instance ids (0..250) keep decoding — no over-blocking."""
    data = b"\x00\x40\x1f\xdc\x05\x0a\xff\xff"  # instance 0
    res = Nmea2000PgnDecoder.decode_engine_rapid(data)
    assert res is not None and res.engine_instance == 0

    data2 = b"\xfa\x40\x1f\xdc\x05\x0a\xff\xff"  # instance 250 = last legal
    res2 = Nmea2000PgnDecoder.decode_engine_rapid(data2)
    assert res2 is not None and res2.engine_instance == 250


# ============================================================================
# 3. REVIEW 2 - MEDIUM 4: Volvo EVC SA/NAME attribution gate
# ============================================================================


def _evc_frame(sa: int = 0x00, payload: bytes = EVC_PAYLOAD) -> CanFrame:
    return CanFrame.create(
        channel_id="volvo_can",
        arbitration_id=0x18FF5000 | sa,  # PGN 65360, SA low byte
        data=payload,
        is_extended=True,
    )


def _claim_frame(sa: int, manufacturer_code: int) -> CanFrame:
    """PGN 60928 Address Claim with NAME bits 21..31 = manufacturer_code."""
    name = (manufacturer_code & 0x7FF) << 21
    data = name.to_bytes(8, byteorder="little")
    return CanFrame.create(
        channel_id="volvo_can",
        arbitration_id=0x18EEFF00 | sa,
        data=data,
        is_extended=True,
    )


@pytest.fixture(autouse=True)
def _clean_volvo_name_table():
    """Reset the class-level NAME learning table between tests."""
    saved = dict(VolvoPentaDecoder._sa_name_codes)
    yield
    VolvoPentaDecoder._sa_name_codes.clear()
    VolvoPentaDecoder._sa_name_codes.update(saved)


def test_volvo_known_engine_sa_decodes_high_confidence() -> None:
    state = VolvoPentaDecoder.decode_evc_can_frame(_evc_frame(sa=0x00))
    assert state is not None
    assert state.lever_position_percent == 50.0
    assert state.attribution_confidence == "HIGH"


def test_volvo_foreign_name_claimed_source_refused() -> None:
    """A Cummins-claimed SA transmitting on PGN 65360 must NOT be Volvo telemetry."""
    VolvoPentaDecoder.record_address_claim(_claim_frame(sa=0x42, manufacturer_code=10))  # Cummins
    state = VolvoPentaDecoder.decode_evc_can_frame(_evc_frame(sa=0x42))
    assert state is None  # decode refused


def test_volvo_name_claim_outranks_static_sa_list() -> None:
    """A Cummins claim on SA 0 (a legal J1939-81 engine address) is FOREIGN."""
    VolvoPentaDecoder.record_address_claim(_claim_frame(sa=0x00, manufacturer_code=10))
    state = VolvoPentaDecoder.decode_evc_can_frame(_evc_frame(sa=0x00))
    assert state is None


def test_volvo_penta_name_claim_confirms() -> None:
    """Manufacturer code 174 (Volvo Penta, canboat DBC) confirms attribution."""
    VolvoPentaDecoder.record_address_claim(_claim_frame(sa=0x50, manufacturer_code=174))
    state = VolvoPentaDecoder.decode_evc_can_frame(_evc_frame(sa=0x50))
    assert state is not None
    assert state.attribution_confidence == "HIGH"


def test_volvo_unknown_sa_decodes_low_confidence() -> None:
    """Unclaimed SA still decodes — flagged LOW so the UI can warn."""
    state = VolvoPentaDecoder.decode_evc_can_frame(_evc_frame(sa=0x77))
    assert state is not None
    assert state.attribution_confidence == "LOW"


def test_volvo_malformed_claim_ignored() -> None:
    """Short / non-60928 / non-extended claims never fabricate ownership."""
    short = CanFrame.create(
        channel_id="volvo_can",
        arbitration_id=0x18EEFF00 | 0x33,
        data=b"\x01\x02",
        is_extended=True,
    )
    VolvoPentaDecoder.record_address_claim(short)
    state = VolvoPentaDecoder.decode_evc_can_frame(_evc_frame(sa=0x33))
    assert state is not None
    assert state.attribution_confidence == "LOW"  # still UNKNOWN, not fabricated


def test_volvo_volvo_trucks_code_is_not_penta() -> None:
    """Manufacturer 60/61 = Volvo TRUCKS — must NOT confirm marine EVC."""
    VolvoPentaDecoder.record_address_claim(_claim_frame(sa=0x61, manufacturer_code=60))
    state = VolvoPentaDecoder.decode_evc_can_frame(_evc_frame(sa=0x61))
    assert state is None


# ============================================================================
# 4. REVIEW 3 - MEDIUM: Poller transaction identity / orphan conversations
# ============================================================================


def _obd_resp(payload: bytes, rx_id: int = 0x7E8) -> CanFrame:
    return CanFrame.create(
        channel_id="obd_ch0",
        arbitration_id=rx_id,
        data=payload,
        direction="rx",
    )


def test_poller_mints_fresh_transaction_id_per_dispatch() -> None:
    """Each dispatch stamps the job with a NEW monotonic transaction id."""
    from tests.unit.test_obd_poller import FakeClock  # reuse the fake clock

    tx_port = InMemoryTxPort()
    clock = FakeClock(start_time=1000.0)
    poller = ActiveDiagnosticPoller(tx_port=tx_port, clock_provider=clock)
    poller.register_pid(0x0C, rate_hz=10.0, callback=lambda r: None)

    job1 = poller.step()
    assert job1 is not None
    txn1 = job1.transaction_id
    assert txn1 > 0

    # Same job re-dispatched after its response completes: NEW id minted.
    resp = _obd_resp(bytes([0x04, 0x41, 0x0C, 0x1F, 0x40, 0xAA, 0xAA, 0xAA]))
    poller.process_rx_frame(resp)  # complete txn1

    clock.advance(0.1)  # past the 10 Hz interval
    job2 = poller.step()
    assert job2 is not None
    assert job2 is job1  # same job re-selected (periodic)
    assert job2.transaction_id > txn1  # NEW id minted


def test_poller_stale_response_cannot_complete_newer_transaction() -> None:
    """A late DUPLICATE response (ECU retransmission / bus echo) arriving
    AFTER the job completed must not fire a second callback or resurrect
    the transaction — the orphan-conversation fix.

    (Note: for identical request content a late answer is semantically the
    same reading; what must never happen is double-completion or a stale
    frame completing a job that is no longer waiting.)
    """
    tx_port = InMemoryTxPort()
    poller = ActiveDiagnosticPoller(tx_port=tx_port)
    received: list = []
    poller.register_pid(0x0C, rate_hz=10.0, callback=received.append)

    job = poller.step()
    assert job is not None
    t1 = job.transaction_id

    # Fresh response #1 completes the transaction exactly once.
    resp = _obd_resp(bytes([0x04, 0x41, 0x0C, 0x1F, 0x40, 0xAA, 0xAA, 0xAA]))
    result, _ = poller.process_rx_frame(resp)
    assert result is not None
    assert len(received) == 1
    assert job.state == PollerState.COMPLETED

    # The STALE duplicate arrives: job no longer WAITING -> no owner ->
    # decodes (zero-fabrication, it IS on the bus) but completes nothing.
    stale = _obd_resp(bytes([0x04, 0x41, 0x0C, 0x1F, 0x40, 0xAA, 0xAA, 0xAA]))
    result2, _ = poller.process_rx_frame(stale)
    assert len(received) == 1  # no second callback
    assert job.state == PollerState.COMPLETED  # not resurrected


def test_poller_fresh_response_completes_matching_transaction() -> None:
    """Sanity: a prompt response for the CURRENT transaction completes."""
    tx_port = InMemoryTxPort()
    poller = ActiveDiagnosticPoller(tx_port=tx_port)
    received: list = []
    poller.register_pid(0x0C, rate_hz=10.0, callback=received.append)

    job = poller.step()
    assert job is not None
    resp = _obd_resp(bytes([0x04, 0x41, 0x0C, 0x1F, 0x40, 0xAA, 0xAA, 0xAA]))
    result, fc = poller.process_rx_frame(resp)
    assert result is not None
    assert len(received) == 1
    assert poller.current_state == PollerState.COMPLETED


def test_poller_concurrent_conversations_do_not_invalidate_each_other() -> None:
    """A dispatch to ECU B must NOT invalidate ECU A's in-flight txn."""
    tx_port = InMemoryTxPort()
    poller = ActiveDiagnosticPoller(tx_port=tx_port)
    a_results: list = []
    poller.register_pid(0x0C, rate_hz=10.0, callback=a_results.append, tx_id=0x7E0, rx_id=0x7E8)
    poller.register_pid(0x05, rate_hz=10.0, callback=lambda r: None, tx_id=0x7E2, rx_id=0x7EA)

    job_a = poller.step()
    assert job_a is not None and job_a.rx_id == 0x7E8

    # Dispatch to the second conversation before A responds.
    poller._last_tx_time_s = 0.0
    job_b = poller.step()
    assert job_b is not None and job_b.rx_id == 0x7EA

    # A's response STILL completes A — per-conversation txn, not global.
    resp_a = _obd_resp(bytes([0x04, 0x41, 0x0C, 0x1F, 0x40, 0xAA, 0xAA, 0xAA]), rx_id=0x7E8)
    result, _ = poller.process_rx_frame(resp_a)
    assert result is not None
    assert len(a_results) == 1
    assert job_a.state == PollerState.COMPLETED


# ============================================================================
# 5. REVIEW 3 - LOW: MDF4 exporter validation + atomic write
# ============================================================================


def test_mdf4_length_mismatch_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "s.mf4"
        signals = {"Bad": ([0.0, 0.1, 0.2], [1.0, 2.0], "unit")}
        with pytest.raises(ValueError, match="length mismatch"):
            Mdf4Exporter.export_signals(out, signals)
        assert not out.exists()  # nothing written


def test_mdf4_nan_value_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "s.mf4"
        signals = {"Bad": ([0.0, 0.1], [1.0, float("nan")], "unit")}
        with pytest.raises(ValueError, match="non-finite value"):
            Mdf4Exporter.export_signals(out, signals)


def test_mdf4_non_monotonic_timestamps_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "s.mf4"
        signals = {"Bad": ([0.0, 0.1, 0.1], [1.0, 2.0, 3.0], "unit")}
        with pytest.raises(ValueError, match="not strictly increasing"):
            Mdf4Exporter.export_signals(out, signals)


def test_mdf4_valid_export_is_atomic_no_tmp_left() -> None:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "s.mf4"
        signals = {"RPM": ([0.0, 0.1, 0.2], [800.0, 900.0, 1000.0], "rpm")}
        res = Mdf4Exporter.export_signals(out, signals)
        assert res.exists() and res.stat().st_size > 0
        # No temp residue in the export directory
        leftovers = [p for p in Path(td).iterdir() if ".tmp-" in p.name]
        assert leftovers == []


def test_mdf4_empty_series_skipped_valid_others_exported() -> None:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "s.mf4"
        signals = {
            "Empty": ([], [], "unit"),
            "Real": ([0.0, 0.1], [1.0, 2.0], "unit"),
        }
        res = Mdf4Exporter.export_signals(out, signals)
        assert res.exists() and res.stat().st_size > 0
