"""Automated Comprehensive Performance Profiling and Benchmark Suite."""

from __future__ import annotations

import cProfile
import io
import pstats
import statistics
import time
import tracemalloc

# supply-chain: import guard instead of sys.path.insert (no import-time
# path mutation; keeps frozen/packaged runs hermetic).
try:
    from src.core.models.can_frame import CanFrame
    from src.engine.buffer.ring_buffer import BinaryRingBuffer
    from src.engine.decoder.dbc_decoder import DbcSignalDecoder
    from src.protocols.uds.isotp import IsoTpTransport
    from src.safety.estop import EmergencyStopSystem
    from src.safety.gateway import TxBudget, TxSafetyGateway
except ModuleNotFoundError as exc:
    raise SystemExit(
        "benchmarks must run from the repo root (python scripts/benchmarks.py) "
        f"so 'src' is importable: {exc}"
    ) from exc


class MockBus:
    def send(self, frame: CanFrame) -> None:
        pass


# F-40: run each benchmark REPEATS times and report median ± stdev instead of
# a single noisy sample.
REPEATS = 10


def run_with_stats(label: str, fn, *args, **kwargs) -> float:
    """Execute a benchmark REPEATS times; print median ± stdev (F-40)."""
    samples = [fn(*args, **kwargs) for _ in range(REPEATS)]
    median = statistics.median(samples)
    stdev = statistics.stdev(samples) if len(samples) > 1 else 0.0
    unit = "msgs/sec" if "ISO-TP" in label else "ops/sec"
    print(f"[STATS] {label}: median={median:,.0f} {unit}  stdev={stdev:,.0f}  (n={len(samples)})")
    return median


SAMPLE_DBC = """VERSION ""
BO_ 256 EngineTelemetry: 8 Engine
 SG_ EngineSpeed : 0|16@1+ (0.125,0) [0|8000] "rpm" Vector__XXX
 SG_ CoolantTemp : 16|8@1+ (1,-40) [-40|215] "degC" Vector__XXX
 SG_ FuelPressure : 24|8@1+ (5,0) [0|1000] "kPa" Vector__XXX
 SG_ EngineLoad : 32|8@1+ (0.5,0) [0|100] "%" Vector__XXX

BO_ 2364539904 EEC1_J1939: 8 Engine
 SG_ J1939_Speed : 24|16@1+ (0.125,0) [0|8031.875] "rpm" Vector__XXX
 SG_ J1939_Torque : 16|8@1+ (1,-125) [-125|125] "%" Vector__XXX
"""


def benchmark_can_frame_creation(n: int = 100_000) -> float:
    t0 = time.perf_counter()
    data = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    for i in range(n):
        _ = CanFrame(
            channel_id="vcan0",
            arbitration_id=0x100 + (i % 256),
            dlc=8,
            data=data,
            timestamp_ns=1_000_000_000 + i,
        )
    t1 = time.perf_counter()
    fps = n / (t1 - t0)
    print(f"[CanFrame Creation] {n:,} frames in {t1 - t0:.4f}s -> {fps:,.0f} frames/sec")
    return fps


def benchmark_ring_buffer_append(n: int = 100_000) -> float:
    rb = BinaryRingBuffer(capacity=300_000)
    frames = [
        CanFrame(
            channel_id="vcan0",
            arbitration_id=0x100 + (i % 256),
            dlc=8,
            data=b"\x01\x02\x03\x04\x05\x06\x07\x08",
            timestamp_ns=1_000_000_000 + i,
        )
        for i in range(10_000)
    ]

    tracemalloc.start()
    t0 = time.perf_counter()
    for i in range(n):
        rb.append(frames[i % 10_000])
    t1 = time.perf_counter()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    fps = n / (t1 - t0)
    print(
        f"[RingBuffer Single Append] {n:,} frames in {t1 - t0:.4f}s -> {fps:,.0f} frames/sec (Peak RAM: {peak / 1024 / 1024:.2f} MB)"
    )
    return fps


def benchmark_ring_buffer_batch_append(n: int = 100_000, batch_size: int = 100) -> float:
    rb = BinaryRingBuffer(capacity=300_000)
    batch = [
        CanFrame(
            channel_id="vcan0",
            arbitration_id=0x100 + (i % 256),
            dlc=8,
            data=b"\x01\x02\x03\x04\x05\x06\x07\x08",
            timestamp_ns=1_000_000_000 + i,
        )
        for i in range(batch_size)
    ]

    num_batches = n // batch_size
    t0 = time.perf_counter()
    for _ in range(num_batches):
        rb.append_batch(batch)
    t1 = time.perf_counter()
    fps = n / (t1 - t0)
    print(f"[RingBuffer Batch Append] {n:,} frames in {t1 - t0:.4f}s -> {fps:,.0f} frames/sec")
    return fps


def benchmark_dbc_decoder(n: int = 50_000) -> float:
    decoder = DbcSignalDecoder.from_dbc_string(SAMPLE_DBC)
    data = b"\xa0\x0f\x78\x14\x64\x00\x00\x00"
    frames = [
        CanFrame(
            channel_id="vcan0",
            arbitration_id=256 if (i % 2 == 0) else 0x0CF00400,
            dlc=8,
            data=data,
            is_extended=(i % 2 != 0),
            timestamp_ns=1_000_000_000 + i,
        )
        for i in range(1000)
    ]

    t0 = time.perf_counter()
    for i in range(n):
        _ = decoder.decode_frame(frames[i % 1000])
    t1 = time.perf_counter()
    fps = n / (t1 - t0)
    print(f"[DBC Signal Decoder] {n:,} frames in {t1 - t0:.4f}s -> {fps:,.0f} frames/sec")
    return fps


def benchmark_isotp_segmentation_reassembly(n: int = 10_000) -> float:
    tx_transport = IsoTpTransport(tx_id=0x7E0, rx_id=0x7E8)
    rx_transport = IsoTpTransport(tx_id=0x7E8, rx_id=0x7E0)

    payload = bytes(range(120))  # 120 bytes multi-frame ISO-TP message

    t0 = time.perf_counter()
    for _ in range(n):
        frames = tx_transport.segment_message(payload)
        # Reassemble
        for frame in frames:
            completed, flow_control = rx_transport.handle_rx_frame(frame)
            if completed is not None:
                assert completed == payload
    t1 = time.perf_counter()
    mps = n / (t1 - t0)
    print(f"[ISO-TP Segment + Reassemble 120B] {n:,} messages in {t1 - t0:.4f}s -> {mps:,.0f} msgs/sec")
    return mps


def benchmark_tx_safety_gateway(n: int = 50_000) -> float:
    bus = MockBus()
    estop = EmergencyStopSystem()
    gateway = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids=set(range(0x100, 0x200)))
    frame = CanFrame(channel_id="vcan0", arbitration_id=0x150, dlc=8, data=b"\x01" * 8)

    t0 = time.perf_counter()
    # Bypass the rate window + default token bucket for raw engine speed
    # measurement (F-18 added the per-category TxBudget on top of the window).
    # supply-chain: restore mutated globals in finally — benchmarks must not
    # leak a 1M msg/s rate limit into any later in-process use.
    _saved_rate = gateway.MAX_TX_RATE_PER_SEC
    _saved_budget = gateway._budgets.get("default")
    gateway.MAX_TX_RATE_PER_SEC = 1_000_000
    gateway._budgets["default"] = TxBudget(capacity=n, refill_per_sec=float(n))
    try:
        for _ in range(n):
            gateway.validate_and_transmit(frame)
    finally:
        gateway.MAX_TX_RATE_PER_SEC = _saved_rate
        if _saved_budget is None:
            gateway._budgets.pop("default", None)
        else:
            gateway._budgets["default"] = _saved_budget
    t1 = time.perf_counter()
    fps = n / (t1 - t0)
    print(f"[TX Safety Gateway Filter] {n:,} frames in {t1 - t0:.4f}s -> {fps:,.0f} checks/sec")
    return fps


def run_cprofile() -> None:
    print("\n=======================================================")
    print(">>> Profiling Full Pipeline with cProfile")
    print("=======================================================")
    pr = cProfile.Profile()
    pr.enable()

    benchmark_can_frame_creation(50_000)
    benchmark_ring_buffer_append(50_000)
    benchmark_ring_buffer_batch_append(50_000)
    benchmark_dbc_decoder(25_000)
    benchmark_isotp_segmentation_reassembly(5_000)
    benchmark_tx_safety_gateway(25_000)

    pr.disable()
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats(pstats.SortKey.CUMULATIVE)
    ps.print_stats(30)
    print(s.getvalue())


if __name__ == "__main__":
    print("=======================================================")
    print(">>> Python Performance Benchmark Baseline")
    print("=======================================================")
    run_with_stats("CanFrame Creation", benchmark_can_frame_creation, 100_000)
    run_with_stats("RingBuffer Single Append", benchmark_ring_buffer_append, 100_000)
    run_with_stats("RingBuffer Batch Append", benchmark_ring_buffer_batch_append, 100_000)
    run_with_stats("DBC Signal Decoder", benchmark_dbc_decoder, 50_000)
    run_with_stats("ISO-TP Segment+Reassemble", benchmark_isotp_segmentation_reassembly, 10_000)
    run_with_stats("TX Safety Gateway Filter", benchmark_tx_safety_gateway, 50_000)
    run_cprofile()
