"""T58-C — ORTA veri yolu guvenilirlik regresyon testleri.

Kapsam (yalniz bu gorevde atanan bulgular):
- RD-1: HMAC anahtar kaybi → mevcut TUM chunk'lar format filtresi olmadan
  ``unauthenticated_prior_key/`` altina tasinir ("legacy sweep" sozlesmesi).
- RD-2: ``read_all_stored_frames`` varsayilani izole eder; tek bozuk chunk
  tum kaydi okunamaz kilmaz + ``unreadable_chunks`` sayaci raporlanir.
- RB-1: ``_rev_channel_map`` kilit altinda snapshot'lanir → eszamanli
  ``clear()`` yarisi kareleri yanlis kanal adiyla etiketlemez.
- RT-1: ``_total_routed``/``_total_dropped`` tek ``_stats_lock`` altinda ve
  ``stats`` ayni kilit altinda tutarli okunur.
- RT-2: demote/restore ``Subscription`` nesnesini YERINDE mutasyona ugratmaz
  (immutable COW snapshot iddiasi dogru olur).
- DB-1: ``frame_len < msg_def.length`` sessizce yutulmaz; ``_truncated_frames``
  sayaci + rate-limited WARN uretir.
- DB-2: J1939-71 MSB sentinel kurali yalnizca J1939 (PF<240 PDU1, DP=0)
  karelerine uygulanir; NMEA2000/ISO-TP 29-bit kareler girer.
"""

from __future__ import annotations

import logging
import struct
import threading
from pathlib import Path

import pytest
import zstandard as zstd

from src.core.models.can_frame import CanFrame
from src.engine.buffer.ring_buffer import BinaryRingBuffer
from src.engine.buffer.rolling_disk import (
    CHUNK_MAGIC,
    CHUNK_VERSION,
    FRAME_SIZE,
    HEADER_PREFIX_FMT,
    RollingDiskBuffer,
)
from src.engine.decoder.dbc_decoder import DbcSignalDecoder, SignalStatus
from src.engine.router import FrameRouter, Subscription
from src.safety.secret_provider import EphemeralSecretBackend

ROLLING_KEY = b"k" * 32
NEW_KEY = b"n" * 32


def _frame(index: int = 1) -> CanFrame:
    return CanFrame(
        channel_id="can0",
        arbitration_id=0x18FF0000 + index,
        dlc=8,
        data=bytes(range(8)),
        is_extended=True,
        timestamp_ns=1_000_000 + index,
        sequence=index,
    )


class _RecordCollector(logging.Handler):
    """Collects LogRecords regardless of when the logger was configured."""

    def __init__(self, sink: list[logging.LogRecord]) -> None:
        super().__init__(level=logging.DEBUG)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self._sink.append(record)


def _write_chunk(storage: Path, key: bytes, frames: list[CanFrame], name: str) -> Path:
    """Write one authentic chunk (real header + HMAC) signed with ``key``."""
    body = b"".join(
        RollingDiskBuffer._serialize_chunk([frame], key)[48:] for frame in frames
    )
    prefix = struct.pack(HEADER_PREFIX_FMT, CHUNK_MAGIC, CHUNK_VERSION, 0, len(frames), FRAME_SIZE)
    import hashlib
    import hmac

    mac = hmac.new(key, prefix + body, hashlib.sha256).digest()
    path = storage / name
    path.write_bytes(zstd.ZstdCompressor(level=3).compress(prefix + mac + body))
    return path


# ---------------------------------------------------------------------------
# RD-1 — key-loss "legacy sweep" contract
# ---------------------------------------------------------------------------


def test_rd1_minted_key_quarantines_all_prior_key_chunks(tmp_path: Path) -> None:
    """RD-1: anahtar kaybi yeni anahtar urettiginde, ESKI anahtarla imzalanmis
    zstd chunk'lari (pickle olmayan, 0x28 magic) da aktif depodan cikarilir."""
    # Eski oturum: gercek bir chunk eski anahtarla yazildi.
    old_chunk = _write_chunk(
        tmp_path, b"old-" + b"k" * 28, [_frame(1)], "chunk_00000000_0000000000001000001.bin.zst"
    )
    assert old_chunk.exists()

    # Vault reset: ROLLING_DISK_HMAC_KEY yok -> yeni anahtar uretilir.
    provider = EphemeralSecretBackend({})
    RollingDiskBuffer(tmp_path, secret_provider=provider)

    assert not old_chunk.exists(), "eski-anahtarli zstd chunk aktif depoda kalmamali"
    assert (tmp_path / "unauthenticated_prior_key" / old_chunk.name).exists()


def test_rd1_no_mint_leaves_existing_chunks_in_active_store(tmp_path: Path) -> None:
    """RD-1 karsi-kanit: anahtar mevcut ise (mint yok) chunk'lar tasinmaz."""
    provider = EphemeralSecretBackend({"ROLLING_DISK_HMAC_KEY": ROLLING_KEY})
    chunk = _write_chunk(tmp_path, ROLLING_KEY, [_frame(1)], "chunk_00000000_0000000000001000001.bin.zst")

    RollingDiskBuffer(tmp_path, secret_provider=provider)

    assert chunk.exists()
    assert not (tmp_path / "unauthenticated_prior_key").exists()


def test_rd1_minted_key_when_store_is_empty_dir_creates_no_quarantine(tmp_path: Path) -> None:
    """RD-1: ilk kurulumda (hic chunk yok) anahtar uretmek bos dizin olusturmaz."""
    provider = EphemeralSecretBackend({})
    RollingDiskBuffer(tmp_path, secret_provider=provider)

    assert not (tmp_path / "unauthenticated_prior_key").exists()


# ---------------------------------------------------------------------------
# RD-2 — per-chunk isolation (fail-closed DoS)
# ---------------------------------------------------------------------------


def test_rd2_default_read_isolates_corrupt_chunk(tmp_path: Path) -> None:
    """RD-2: tek bozuk chunk tum kaydi dusurmez; varsayilan izole eder."""
    provider = EphemeralSecretBackend({"ROLLING_DISK_HMAC_KEY": ROLLING_KEY})
    buf = RollingDiskBuffer(tmp_path, chunk_frame_threshold=2, secret_provider=provider)

    good_frames = [_frame(i) for i in range(1, 4)]
    for frame in good_frames:
        buf.append(frame)
    buf.flush(drain=True)

    # Bozuk chunk: gecerli zstd ama HMAC gecersiz (bit curumesi).
    raw = bytearray(zstd.ZstdDecompressor().decompress(next(tmp_path.glob("chunk_*.bin.zst")).read_bytes()))
    raw[-1] ^= 0xFF
    corrupt = tmp_path / "chunk_00000099_0000000000009999999.bin.zst"
    corrupt.write_bytes(zstd.ZstdCompressor(level=3).compress(bytes(raw)))

    frames = buf.read_all_stored_frames()  # varsayilan: izole et

    assert len(frames) == len(good_frames), "saglikli chunk'lar donmeli"
    assert buf.unreadable_chunks == 1
    assert not corrupt.exists()
    assert corrupt.with_suffix(corrupt.suffix + ".corrupt").exists()
    buf.close()


def test_rd2_strict_mode_still_raises(tmp_path: Path) -> None:
    """RD-2: tamper-detection cagiran tarafi quarantine_corrupt=False ile
    siki davranisi acikca isteyebilir (raise korunur)."""
    from src.core.errors import SecurityError

    provider = EphemeralSecretBackend({"ROLLING_DISK_HMAC_KEY": ROLLING_KEY})
    buf = RollingDiskBuffer(tmp_path, chunk_frame_threshold=1, secret_provider=provider)
    buf.append(_frame(1))
    buf.flush(drain=True)

    chunk = next(tmp_path.glob("chunk_*.bin.zst"))
    raw = bytearray(zstd.ZstdDecompressor().decompress(chunk.read_bytes()))
    raw[-1] ^= 0xFF
    chunk.write_bytes(zstd.ZstdCompressor(level=3).compress(bytes(raw)))

    with pytest.raises(SecurityError):
        buf.read_all_stored_frames(quarantine_corrupt=False)
    buf.close()


def test_rd2_unreadable_chunks_counter_is_not_global_state(tmp_path: Path) -> None:
    """RD-2: sayac okuma cagrisi basina sifirlanir (kumulatif sismez)."""
    provider = EphemeralSecretBackend({"ROLLING_DISK_HMAC_KEY": ROLLING_KEY})
    buf = RollingDiskBuffer(tmp_path, chunk_frame_threshold=1, secret_provider=provider)
    buf.append(_frame(1))
    buf.flush(drain=True)

    chunk = next(tmp_path.glob("chunk_*.bin.zst"))
    raw = bytearray(zstd.ZstdDecompressor().decompress(chunk.read_bytes()))
    raw[-1] ^= 0xFF
    chunk.write_bytes(zstd.ZstdCompressor(level=3).compress(bytes(raw)))

    buf.read_all_stored_frames()
    assert buf.unreadable_chunks == 1
    buf.read_all_stored_frames()
    assert buf.unreadable_chunks == 0, "izole edilen chunk bir daha sayilmaz"
    buf.close()


# ---------------------------------------------------------------------------
# RB-1 — _rev_channel_map snapshot under the lock
# ---------------------------------------------------------------------------


def test_rb1_rev_channel_map_is_snapshotted_under_lock() -> None:
    """RB-1: get_latest_frames kanal adlarini okuyucu kilidi tutmadan
    materialize eder; bu yuzden rev-map'i kilit altinda snapshot'lamali.

    Test: materialization sirasinda rev-map temizlenirse (clear() yarisi),
    kanal adlari zaten snapshot'tan gelmeli → dogru etiket korunur.
    """
    buf = BinaryRingBuffer(capacity=8)
    buf.append(CanFrame.create(channel_id="engine0", arbitration_id=0x100, data=b"\x01"))

    original = buf.get_latest_view
    fired = {"done": False}

    def race_view(count: int, *, copy: bool = True):
        result = original(count, copy=copy)
        if not fired["done"]:
            fired["done"] = True
            # Eszamanli clear() yarisi: rev-map bu anda temizlenir.
            buf._rev_channel_map.clear()
            buf._channel_map.clear()
        return result

    buf.get_latest_view = race_view  # type: ignore[method-assign]
    frames = buf.get_latest_frames(1)

    assert len(frames) == 1
    assert frames[0].channel_id == "engine0", (
        "kanal adi snapshot'tan gelmeli; kilitsiz rev-map okumasi ch_0 fallback'ine dusuyor"
    )


# ---------------------------------------------------------------------------
# RT-1 — single stats lock
# ---------------------------------------------------------------------------


def test_rt1_stats_counters_share_one_lock() -> None:
    """RT-1: her iki sayac da ayni kilit altinda tutulur ve okunur."""
    router = FrameRouter()
    assert router._stats_lock is not None

    router.subscribe(use_queue=True, queue_maxsize=1)
    for i in range(5):
        router.route_frame(CanFrame.create(channel_id="can0", arbitration_id=i, data=b"\x01"))

    assert router.stats["total_routed"] == 5
    assert router.stats["total_dropped"] == 4


def test_rt1_stats_reads_are_consistent_under_concurrency() -> None:
    """RT-1: eszamanli route+drop altinda stats() tutarli bir an gorur:
    dropped <= routed."""
    router = FrameRouter()
    router.subscribe(use_queue=True, queue_maxsize=2)

    stop = threading.Event()
    inconsistent: list[tuple[int, int]] = []

    def producer() -> None:
        frame = CanFrame.create(channel_id="can0", arbitration_id=0x100, data=b"\x01")
        while not stop.is_set():
            router.route_frame(frame)

    def observer() -> None:
        for _ in range(500):
            stats = router.stats
            if stats["total_dropped"] > stats["total_routed"]:
                inconsistent.append((stats["total_routed"], stats["total_dropped"]))

    threads = [threading.Thread(target=producer) for _ in range(3)]
    for thread in threads:
        thread.start()
    observers = [threading.Thread(target=observer) for _ in range(2)]
    for thread in observers:
        thread.start()
    for thread in observers:
        thread.join()
    stop.set()
    for thread in threads:
        thread.join()

    assert inconsistent == []


# ---------------------------------------------------------------------------
# RT-2 — immutable Subscription snapshot (no in-place mutation)
# ---------------------------------------------------------------------------


def test_rt2_demote_replaces_subscription_object() -> None:
    """RT-2: demote YERINDE mutasyon yapmamali; snapshot'taki eski nesne
    immutable kalmali, yenisi replace ile uretilmeli."""
    router = FrameRouter()
    q: list = []
    sub_id, frame_queue = router.subscribe(use_queue=True, queue_maxsize=1)
    original = next(s for s in router._subscriptions.values() if s.sub_id == sub_id)
    assert (sub_id, q) is not None

    def slow_callback(frame: CanFrame) -> None:
        import time

        time.sleep(0.05)

    router.restore_callback(sub_id, slow_callback)
    armed = router._subscriptions[sub_id]
    assert armed is not original

    router.route_frame(CanFrame.create(channel_id="can0", arbitration_id=1, data=b"\x01"))

    demoted = router._subscriptions[sub_id]
    assert demoted is not armed, "demote dataclasses.replace ile YENI nesne uretmeli"
    assert armed.callback is slow_callback, "eski snapshot nesnesi yerinde mutasyona ugramamali"
    assert armed.is_demoted is False
    assert demoted.callback is None
    assert demoted.is_demoted is True
    assert frame_queue is not None


def test_rt2_snapshots_are_immutable_objects_in_route_snapshot() -> None:
    """RT-2: route snapshot'i yerinde mutasyona ugrayan nesneler tutmamali."""
    router = FrameRouter()

    def noop(_frame: CanFrame) -> None:
        return None

    sub_id, _ = router.subscribe(callback=noop)
    before = router._route_snapshot
    assert all(isinstance(sub, Subscription) for sub in before)

    router.restore_callback(sub_id, noop)
    after = router._route_snapshot

    before_sub = next(sub for sub in before if sub.sub_id == sub_id)
    after_sub = next(sub for sub in after if sub.sub_id == sub_id)
    assert before_sub is not after_sub, "snapshot rebuild yeni nesne uretmeli (COW)"


# ---------------------------------------------------------------------------
# DB-1 — truncated frame observability
# ---------------------------------------------------------------------------

TRUNC_DBC = """VERSION ""
NS_ :
BS_:
BU_: Engine

BO_ 256 StandardMsg: 8 Engine
 SG_ CoolantTemp : 0|8@1+ (1,-40) [-40|215] "degC" Vector__XXX
"""


def test_db1_truncated_frame_counted_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    """DB-1: frame_len < msg_def.length sessizce None donmemeli; sayac + WARN."""
    decoder = DbcSignalDecoder.from_dbc_string(TRUNC_DBC)
    truncated = CanFrame.create(channel_id="ch0", arbitration_id=256, data=b"\x78\x00\x00\x00")

    with caplog.at_level(logging.WARNING, logger="universal_can.engine.decoder"):
        assert decoder.decode_frame(truncated) is None

    assert decoder.truncated_frames == 1

    # caplog replaces the target logger's handler list for the duration of the
    # context, so capture the emitted record through a dedicated handler on the
    # root logger instead (records still propagate there).
    captured: list[logging.LogRecord] = []
    handler = _RecordCollector(captured)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        with caplog.at_level(logging.WARNING, logger="universal_can.engine.decoder"):
            # Drive past the rate-limit threshold (TRUNCATED_LOG_INTERVAL).
            for _ in range(DbcSignalDecoder.TRUNCATED_LOG_INTERVAL):
                decoder.decode_frame(truncated)
    finally:
        root.removeHandler(handler)

    assert decoder.truncated_frames == 1 + DbcSignalDecoder.TRUNCATED_LOG_INTERVAL
    truncation_records = [r for r in captured if "truncated" in r.getMessage().lower()]
    assert truncation_records, "kesilmis kare WARN ile bildirilmeli"
    assert truncation_records[0].levelno == logging.WARNING
    assert getattr(truncation_records[0], "frame_bytes", None) == 4
    assert getattr(truncation_records[0], "expected_bytes", None) == 8


def test_db1_truncated_warn_is_rate_limited(caplog: pytest.LogCaptureFixture) -> None:
    """DB-1: per-frame WARN log doldurmasin — rate limit uygulanir, sayac artar."""
    decoder = DbcSignalDecoder.from_dbc_string(TRUNC_DBC)
    truncated = CanFrame.create(channel_id="ch0", arbitration_id=256, data=b"\x78\x00\x00\x00")

    with caplog.at_level(logging.WARNING, logger="universal_can.engine.decoder"):
        for _ in range(250):
            decoder.decode_frame(truncated)

    warns = [r for r in caplog.records if "truncated" in r.message.lower()]
    assert decoder.truncated_frames == 250
    assert 1 <= len(warns) < 250, f"WARN rate limit yok: {len(warns)} kayit"


def test_db1_valid_frame_does_not_increment_counter() -> None:
    """DB-1 karsi-kanit: saglikli kare sayaci artirmaz."""
    decoder = DbcSignalDecoder.from_dbc_string(TRUNC_DBC)
    ok = CanFrame.create(channel_id="ch0", arbitration_id=256, data=b"\x78\x00\x00\x00\x00\x00\x00\x00")
    assert decoder.decode_frame(ok) is not None
    assert decoder.truncated_frames == 0


# ---------------------------------------------------------------------------
# DB-2 — J1939 MSB sentinel gate is protocol-bound
# ---------------------------------------------------------------------------

N2K_DBC = """VERSION ""
NS_ :
BS_:
BU_: Nav

BO_ 2582643712 N2K_Propulsion: 8 Nav
 SG_ EngineSpeed : 24|16@1+ (0.125,0) [0|8031.875] "rpm" Vector__XXX
"""


def test_db2_nmea2000_29bit_frame_is_not_sentinel_gated() -> None:
    """DB-2: NMEA2000 29-bit kare mesru 0xFF** degeri tasidiginda NA/ERROR
    isaretlenmemeli.

    Ayni PGN (0x1F004) hem J1939 DBC'sinde (DP=0, 0x18F004xx) hem N2K
    DBC'sinde (DP=1, 0x19F004xx) bulunur; tek fark Data Page bitidir.
    """
    decoder = DbcSignalDecoder.from_dbc_string(N2K_DBC)
    # 0x19F00400: priority 6, DP=1, PF=0xF0, PS=0x04, SA=0x00
    frame = CanFrame.create(
        channel_id="n2k0",
        arbitration_id=0x19F00400,
        data=b"\x00\x00\x00\x00\xff\x00\x00\x00",
        is_extended=True,
    )
    assert decoder._is_j1939_frame(frame) is False

    decoded = decoder.decode_frame(frame)
    assert decoded is not None
    sig = decoded.signals["EngineSpeed"]
    assert sig.raw_value == 0xFF00
    assert sig.is_valid is True, "N2K 29-bit mesru sinyal NA isaretlenmemeli"
    assert sig.status == SignalStatus.VALID


def test_db2_page1_dbc_does_not_decode_page0_frame() -> None:
    """DB-2 karsi yon: yalnizca DP=1 (N2K) tanimli DBC, DP=0 J1939 karesini
    ayni PGN numarasiyla cozmemeli (sayfa karisikligi)."""
    decoder = DbcSignalDecoder.from_dbc_string(N2K_DBC)
    # Ayni PGN numarasi, fakat DP=0 → J1939 sayfa-0 karesi.
    page0 = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18F00400,
        data=b"\x00\x00\x00\x00\xff\x00\x00\x00",
        is_extended=True,
    )
    assert decoder.decode_frame(page0) is None


def test_db2_isotp_extended_addressing_pf_is_not_reserved() -> None:
    """DB-2: ISO-TP uzatilmis adresleme PF=0xDA (<240) ile 29-bit uzerinden
    calisir; ISO-TP bu PDU1 PGN'e tunchlendigi icin tanimlayici seviyesinde
    'J1939 degil' denemez. Sentinel kapisi bu yuzden PF=0xDA'yi kapsamda
    tutar; J1939-olmayan 29-bit trafik DP biti ve PF=0xFF kapilarindan dislanir.
    """
    decoder = DbcSignalDecoder.from_dbc_string(N2K_DBC)
    frame = CanFrame.create(
        channel_id="isotp0",
        arbitration_id=0x18DAF110,
        data=b"\x10\x00\x00\x00\x00\xff\x00\x00",
        is_extended=True,
    )
    # ISO-TP tasiyicisi DP=0 ve PF=0xDA oldugu icin J1939 kapsaminda kalir;
    # yuk tasiyan katman protokoldur (IsoTpTransport), tanimlayici degil.
    assert decoder._is_j1939_frame(frame) is True


def test_db2_j1939_pdu1_ps_is_destination_not_datapage() -> None:
    """DB-2: J1939 PDU1 (PF<240) karelerinde PS byte'i hedef adrestir;
    DP biti bit 24, EDP biti bit 25'tir (ikisi ayri bit — registry.py ile ayni)."""
    decoder = DbcSignalDecoder.from_dbc_string(N2K_DBC)
    # PF=0xE0 (<0xF0) -> PDU1, PS=0x04 hedef adres, DP=0
    j1939 = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18E00400,
        data=b"\x00\x00\x00\x00\xff\x00\x00\x00",
        is_extended=True,
    )
    assert decoder._is_j1939_frame(j1939) is True
    # PF=0xE0 ve DP biti set -> hala J1939 PGN alani, PDU1 kapsaminda
    j1939_dp1 = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x19E00400,
        data=b"\x00\x00\x00\x00\xff\x00\x00\x00",
        is_extended=True,
    )
    assert decoder._is_j1939_frame(j1939_dp1) is False

    # J1939-21: PDU2'de PF=0xFF rezervedir — J1939 parametre grubu degil.
    reserved = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18FF0400,
        data=b"\x00\x00\x00\x00\xff\x00\x00\x00",
        is_extended=True,
    )
    assert decoder._is_j1939_frame(reserved) is False

    # N2K PF=0xF0, DP=1 → kapsam disi
    n2k = CanFrame.create(
        channel_id="n2k0",
        arbitration_id=0x19F00400,
        data=b"\x00\x00\x00\x00\xff\x00\x00\x00",
        is_extended=True,
    )
    assert decoder._is_j1939_frame(n2k) is False
