"""AŞAMA 3 — Faz 3A: HAL + Replay + E2E derin incelemesi.

PLAN (her bulgu için başarısız-önce testi → düzeltme → regresyon):
  A3-F1..Fn: E2E validator/packager/profiles + replay player/parsers +
      VirtualBus/RP1210Bus/PythonCanBus. Aşama 1'in filtre + Aşama 2'nin
      watchdog bulguları buradan çıkmıştı; şimdi kalan HAL yüzeyini
      aynı fail-closed mercekle tarıyoruz.
"""

from __future__ import annotations

from src.core.models.can_frame import CanFrame
from src.safety.e2e.packager import E2ESafetyPackager
from src.safety.e2e.profiles import E2EProfileConfig, E2EProfileType, E2EStatus
from src.safety.e2e.validator import E2ESafetyValidator


def _frame(payload: bytes, *, arb_id: int = 0x100, channel: str = "can0") -> CanFrame:
    return CanFrame(
        channel_id=channel,
        arbitration_id=arb_id,
        dlc=len(payload),
        data=payload,
        is_extended=False,
    )


def _p1_profile(**over: object) -> E2EProfileConfig:
    return E2EProfileConfig(profile_type=E2EProfileType.AUTOSAR_PROFILE_1, **over)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Probe 1: validator INITIAL verdict (fail-open taraması)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# A3-F1 (BULGU): E2E tombstone ledger sınırsız büyüyor (P2-14 yarım kalmış)
# ---------------------------------------------------------------------------


def test_a3f1_evicted_ledger_is_bounded() -> None:
    """A3-F1 DÜZELTME: tombstone ledger MAX_TOMBSTONE_STREAMS ile sınırlı.

    3000 farklı ID tarayan flood sonrası ledger 2048'i geçmemeli VE
    en eski tombstone'lar düşmüş olmalı.
    """
    profile = _p1_profile()
    validator = E2ESafetyValidator()
    packager = E2ESafetyPackager()
    from src.core.models.can_frame import CanFrame as _CF

    for arb_id in range(3000):
        raw = _CF(
            channel_id="can0",
            arbitration_id=arb_id,
            dlc=8,
            data=bytes(8),
            is_extended=True,
        )
        sealed = packager.package(raw, profile)
        validator.validate(sealed, profile)
    assert len(validator._evicted_streams) <= validator.MAX_TOMBSTONE_STREAMS
    assert len(validator._evicted_order) == len(validator._evicted_streams)
    # 3000 ID - 1024 canlı = ~1976 eviction < 2048 cap: bu ölçekte kayıp
    # YOK (flood cap'i aşmıyor). Cap davranışı 5000-ID'lik vektörle kilitli:
    assert len(validator._evicted_streams) == 3000 - 1024


def test_a3f1_evicted_ledger_evicts_oldest_beyond_cap() -> None:
    """Cap üstü flood (5000 ID): en eski tombstone'lar düşer, tavan korunur."""
    profile = _p1_profile()
    validator = E2ESafetyValidator()
    packager = E2ESafetyPackager()
    from src.core.models.can_frame import CanFrame as _CF

    for arb_id in range(5000):
        raw = _CF(
            channel_id="can0",
            arbitration_id=arb_id,
            dlc=8,
            data=bytes(8),
            is_extended=True,
        )
        sealed = packager.package(raw, profile)
        validator.validate(sealed, profile)
    assert len(validator._evicted_streams) == validator.MAX_TOMBSTONE_STREAMS
    assert ("can0", 0) not in validator._evicted_streams


def test_probe_asc_classic_dlc8_silently_truncates() -> None:
    """Classic .asc satırında DLC>8: CanFrame ValueError → bütün load ölür mü?

    parse_file_iter ValueError'ı satırda yakalıyor — ama parse_line DLC=9
    + 9 bayt veriyi CanFrame'e geçiriyor ve __post_init__ patlatıyor mu?
    Probe, satırın atlanıp load'un devam ettiğini kilitler.
    """
    from src.hal.replay.parsers import VectorAscParser

    bad_line = "   0.001250 1  123       Rx   d 9 01 02 03 04 05 06 07 08 09"
    frame = None
    try:
        frame = VectorAscParser.parse_line(bad_line, 1)
    except ValueError:
        frame = None  # satır reddedildi — beklenen fail-closed
    assert frame is None


def test_probe_asc_fd_dlc_len_mismatch() -> None:
    """CANFD .asc satırında decl_len != gerçek veri: ne oluyor?"""
    from src.hal.replay.parsers import VectorAscParser

    # DLC=9 (12 bayt) ilan edip 2 bayt veren satır
    line = "   0.002500 CANFD 1 Rx 123 1 0 9 12 01 02"
    try:
        frame = VectorAscParser.parse_line(line, 1)
    except ValueError:
        frame = None
    # Fail-closed kilit: decl_len != gerçek veri → ValueError ile satır
    # reddedilir, frame None kalır; asla kısmi/uydurma frame dönmez.
    assert frame is None


# ---------------------------------------------------------------------------
# Probe 3: packager DLC desync (P1-10/M-3 bölgesi)
# ---------------------------------------------------------------------------


def test_probe_packager_rx_tx_dlc_agreement() -> None:
    """package() → validate(): DLC kodu iki tarafta aynı CRC'ye girmeli."""
    profile = _p1_profile()
    packager = E2ESafetyPackager()
    validator = E2ESafetyValidator()
    raw = _frame(bytes([0x11, 0x22, 0x33, 0x00, 0x00, 0x00, 0x00, 0x00]), arb_id=0x300)
    sealed = packager.package(raw, profile)
    result = validator.validate(sealed, profile)
    assert result.verdict in (E2EStatus.OK, E2EStatus.INITIAL)
    assert result.is_crc_valid is True


# ---------------------------------------------------------------------------
# Probe 4: ReplayBus TX-callback filtering — B-06 structural fix
# ---------------------------------------------------------------------------


def test_probe_replay_lambda_forwarding_to_send_is_always_filtered() -> None:
    """B-06: lambda ile sarılmış bus.send bile artık filtresiz frame göremez.

    Eski HAL-11 sezgiseli (``_is_tx_callback``) yalnızca bound method'ları
    yakalıyordu; ``lambda fr: bus.send(fr)``, module fonksiyonu, callable
    object ve ``functools.partial`` sarmalayıcıları guard'ı atlatıyordu
    (belgeli residual). B-06 bunu YAPISAL olarak kapattı: play() artık HER
    frame'i bir ReplaySafetyFilter'dan geçirir (verilmezse default
    ReplaySafetyFilter()); hiçbir callback şekli filtresiz frame alamaz.

    Bu test, eski xfail probe'un yerini alır ve yeni sözleşmeyi kilitler:
    unsafe J1939 DM11 (PGN 0x18FED300) ve Address Claim (0x18EEFF00)
    frame'leri sarmalanmış ``bus.send``'e ASLA ulaşmaz; yalnızca güvenli
    frame'ler geçer.
    """
    from src.hal.replay.player import ReplayBus
    from src.hal.virtual import VirtualBus

    bus = VirtualBus()
    bus.connect()
    fwd = lambda fr: bus.send(fr)  # noqa: E731

    # Heuristik hâlâ kör (bilgilendirme amaçlı, güvenlik kapısı DEĞİL)...
    assert ReplayBus._is_tx_callback(fwd) is False
    # ...ama güvenlik artık sezgisel değil: play() yapısal olarak filtreler.
    unsafe_dm11 = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18FED300,
        data=b"\x01" * 8,
        is_extended=True,
        timestamp_ns=0,
    )
    unsafe_addr_claim = CanFrame.create(
        channel_id="can0",
        arbitration_id=0x18EEFF00,
        data=b"\x01" * 8,
        is_extended=True,
        timestamp_ns=1_000_000,
    )
    safe = CanFrame.create(
        channel_id="can0", arbitration_id=0x100, data=b"\x01", timestamp_ns=2_000_000
    )

    replay = ReplayBus([unsafe_dm11, unsafe_addr_claim, safe])
    replay.play(callback=fwd, speed=100.0)

    # Yalnızca güvenli frame TX yoluna ulaştı; unsafe frame'ler düştü.
    assert [f.arbitration_id for f in bus.sent_frames] == [0x100]
    assert replay.filtered_frames == 2

