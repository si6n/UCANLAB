# T58-C — ORTA veri yolu guvenilirlik: KANIT RAPORU

**Gorev:** t_31b84145 (T58-C) — RD-1, RD-2, RB-1, RT-1, RT-2, DB-1, DB-2
**Kapsam (yalniz):** `src/engine/buffer/rolling_disk.py`, `src/engine/buffer/ring_buffer.py`,
`src/engine/router.py`, `src/engine/decoder/dbc_decoder.py`
**Test dosyasi:** `tests/unit/test_t58_c_bus_reliability.py` (18 test)
**Yontem:** TDD — once basarisiz test, sonra fix. (Asagida "once" kaniti: fix'siz
src ile 12/18 test FAIL.)

---

## RD-1 — HMAC anahtar kaybinda format filtresiz legacy sweep

**Bulgu:** `_get_hmac_key` anahtar kaybindaki yeni anahtari sessizce uretiyordu;
`_migrate_legacy_chunks` yalniz eski pickle formatini (0x80) tariyordu → eski
anahtarla imzali **zstd chunk'lari** (magic 0x28 0xB5) aktif depoda kaliyor,
sonradan "tampered" olarak rapor ediliyordu ("legacy sweep" sozlesmesi ihlali).

**Cozum:**
- `rolling_disk.py:60` — `_get_hmac_key` → `tuple[bytes, bool]` (`was_minted`).
- `rolling_disk.py:356-358` — constructor mint tespit ederse
  `_quarantine_unverifiable_chunks()` cagirir.
- `rolling_disk.py:406` — yeni metot: **format filtresi OLMADAN** tum
  `chunk_*.bin.zst` dosyalarini `unauthenticated_prior_key/` altina tasir
  (silmez; collision-index'li hedef ad).

**Testler:** `test_rd1_minted_key_quarantines_all_prior_key_chunks`,
`test_rd1_no_mint_leaves_existing_chunks_in_active_store`,
`test_rd1_minted_key_when_store_is_empty_dir_creates_no_quarantine`

**Once/Sonra:** Eski-anahtarli zstd chunk + bos vault →
**once:** `chunk_*.bin.zst` yerinde kalir (test FAIL: `old_chunk.exists()` True) →
**sonra:** dosya `unauthenticated_prior_key/<ad>` altinda, aktif depoda yok.

---

## RD-2 — Tek bozuk chunk tum kaydi okunamaz kiliyordu (fail-closed DoS)

**Bulgu:** `read_all_stored_frames(quarantine_corrupt=False)` varsayilani ILK
`SecurityError`'da raise ediyordu → TEK bozuk chunk 600 sn'lik karakutu kaydinin
TAMAMINI okunamaz kiliyordu.

**Cozum:**
- `rolling_disk.py:697` — varsayilan `quarantine_corrupt: bool = True`
  (izolasyon). Chunk-basina `try/except`, `unreadable` sayaci.
- `rolling_disk.py:734` — strict mod (`False`) isteyen cagiran icin raise
  korunur ve `_unreadable_chunks` raise oncesi set edilir.
- `rolling_disk.py:761` — `unreadable_chunks` property (okuma basina sifirlanir).
- OSError yolu da sayaca eklenir (`:749`).

**Testler:** `test_rd2_default_read_isolates_corrupt_chunk`,
`test_rd2_strict_mode_still_raises`,
`test_rd2_unreadable_chunks_counter_is_not_global_state`
(ayrica `tests/unit/test_rolling_disk.py` strict-opt-in'a guncellendi).

**Once/Sonra:** 1 saglikli + 1 bit-curumeli chunk →
**once:** raise → HIC kare donmez (test FAIL) →
**sonra:** saglikli kareler doner, `unreadable_chunks == 1`, bozuk chunk
`*.corrupt`'a tasinir.

---

## RB-1 — `_rev_channel_map` kilit disinda okunuyordu (clear() yarisi)

**Bulgu:** `get_latest_frames` kanal adlarini kilitsiz `_rev_channel_map`'ten
okuyordu; eszamanli `clear()` map'i bosaltinca kareler `ch_<n>` fallback'ine
dusuyordu (yanlis kanal adi).

**Cozum:**
- `ring_buffer.py:61,128` — append sirasinda **kilit altinda** coherent
  `_rev_channel_snapshot = dict(self._rev_channel_map)`.
- `ring_buffer.py:274,286` — materialization snapshot'tan okur.
- `ring_buffer.py:317` — `clear()` snapshot'i da temizler (tutarlilik).

**Test:** `test_rb1_rev_channel_map_is_snapshotted_under_lock`

**Once/Sonra:** materialization sirasinda `clear()` yarisi →
**once:** `channel_id == "ch_0"` (test FAIL) →
**sonra:** `channel_id == "engine0"` (snapshot'tan).

---

## RT-1 — `_total_routed`/`_total_dropped` farkli kilitlerle korunuyordu

**Bulgu:** `_total_routed` `_stats_lock`, `_total_dropped` `_lock` altindaydi →
`stats` torn/tutarsiz cift (dropped > routed) gorebiliyordu.

**Cozum:**
- `router.py:164` — routed artisi `_stats_lock`.
- `router.py:245` — dropped artisi `_lock` → **`_stats_lock`**.
- `router.py:302` — `stats` ikisini ayni kilit altinda okur; `active_subscriptions`
  snapshot'tan (`_lock` tutmadan, kilit sirasi degismez: `_lock`→`_stats_lock`;
  nested degil → deadlock yok).

**Testler:** `test_rt1_stats_counters_share_one_lock`,
`test_rt1_stats_reads_are_consistent_under_concurrency` (3 uretici + 2 gozlemci
thread; `dropped <= routed` invarianti).

---

## RT-2 — `Subscription` yerinde mutasyona ugruyordu ("immutable COW" iddiasi)

**Bulgu:** `callback=None` / `is_demoted=True` nesneleri YERINDE degistiriyordu,
fakat snapshot "immutable COW" olarak iddia ediliyordu; in-flight `route_frame`
iterasyonu yarim-mutasyonlu nesne gorebiliyordu.

**Cozum:**
- `router.py:23` — `@dataclass(slots=True, frozen=True)`.
- `router.py:37,133` — `filter_ids: frozenset[int]` (frozen ile uyumlu).
- `router.py:90` — `_replace_subscription_locked` (`dataclasses.replace` +
  `_rebuild_snapshot_locked`).
- Cagri yerleri: demote (`:208`), trip (`:224`), restore (`:280`) — hepsi COW.

**Testler:** `test_rt2_demote_replaces_subscription_object`,
`test_rt2_snapshots_are_immutable_objects_in_route_snapshot`

**Once/Sonra:** demote sonrasi eski snapshot nesnesi →
**once:** `armed.callback is None` (yerinde mutasyon; test FAIL) →
**sonra:** `armed.callback is slow_callback` (eski nesne bozulmamis), yeni nesne
`callback=None, is_demoted=True`.

---

## DB-1 — `frame_len < msg_def.length` sessizce yutuluyordu

**Bulgu:** Kesilmis kare sessizce `None` donuyordu; sayac/log yoktu.

**Cozum:**
- `dbc_decoder.py:145,149` — `_truncated_frames` sayaci +
  `TRUNCATED_LOG_INTERVAL: ClassVar[int] = 100`.
- `dbc_decoder.py:171` — `truncated_frames` property.
- `dbc_decoder.py:173` — `_record_truncated_frame`: sayac artisi + **rate-limited
  WARN** (100'de 1). Frame basina WARN bus hizinda log'u doldurur, bu yuzden
  ozet kaydi.
- `dbc_decoder.py:407` — decode yolunda cagrilir.

**Testler:** `test_db1_truncated_frame_counted_and_logged`,
`test_db1_truncated_warn_is_rate_limited`,
`test_db1_valid_frame_does_not_increment_counter`

**Once/Sonra:** 4-byte kare / 8-byte mesaj →
**once:** `None`, sayac yok, log yok (test FAIL: AttributeError) →
**sonra:** `None` + `truncated_frames == 1` + WARN (`frame_bytes=4`,
`expected_bytes=8`). 250 karede sayac 250, WARN 1..249 (rate limit).

---

## DB-2 — J1939-71 MSB sentinel kapisi protokole bagli degildi

**Bulgu:** Kapi yalniz `frame.is_extended` idi; NMEA2000 / ISO-TP-uzeri 29-bit
kareler de girer → mesru 29-bit sinyaller yanlis NA/ERROR isaretleniyordu.

**Cozum:**
- `dbc_decoder.py:52` — `_is_j1939_identifier`: DP (bit 24) ve EDP (bit 25)
  **ayri bit** olarak test edilir (`& 0x01`; `& 0x03` DEGIL — repo'nun otoriter
  parser'i `src/protocols/j1939/oem/registry.py:97-98` ile ayni) + PF != 0xFF.
- `dbc_decoder.py:196` — `_is_j1939_frame` (`is_extended and _is_j1939_identifier`).
- `dbc_decoder.py:407` — sentinel kapisi `_is_j1939_frame(frame)`.
- `dbc_decoder.py:511-530` — sayfa tutarliligi: DP **ve** EDP ayri karsilastirilir;
  DP=1 tanim DP=0 kareyle (ve tersi) eslesmez.

**Testler:** `test_db2_nmea2000_29bit_frame_is_not_sentinel_gated`,
`test_db2_page1_dbc_does_not_decode_page0_frame`,
`test_db2_isotp_extended_addressing_pf_is_not_reserved`,
`test_db2_j1939_pdu1_ps_is_destination_not_datapage`

**Once/Sonra:** N2K 0x19F00400, sinyal `0xFF00` →
**once:** ham deger NA sentinel sayilir; `is_valid` False / status
NOT_AVAILABLE (test FAIL: `_is_j1939_frame` yok) →
**sonra:** `sig.raw_value == 0xFF00`, `is_valid is True`, `status == VALID`.

> **Not (durustluk):** DB-2'de DP/EDP'yi `& 0x03` yerine ayri bit olarak ifade
> etmek **davranis farki uretmez** (reject-on-nonzero mantiginda `dp|edp`
> esdegerdir). Bu degisiklik kod-metni dogrulugu icin yapildi, "fix" olarak
> abartilmadi ve gozlemlenebilir fark olmadigi icin ayri test eklenmedi.

---

## TDD KANITI (once bASARISIZ)

Fix uygulanmadan **once**, dort src dosyasi HEAD'e dondurulup test dosyasi
calistirildi:

```
12 failed, 6 passed   (tests/unit/test_t58_c_bus_reliability.py)
```

Basarisizlar: RD-1 (1), RD-2 (2), RB-1 (1), RT-2 (2), DB-1 (3), DB-2 (3).
Gecen 6: karsi-kanit ve davranis testleri (fix olmadan da dogru).

Fix geri yuklendikten sonra:

```
18 passed   (tests/unit/test_t58_c_bus_reliability.py)
```

---

## SUITE + LINT

- Ilgili testler: `tests/unit/test_t58_c_bus_reliability.py` +
  `test_rolling_disk.py` + `test_ring_buffer.py` + `test_router.py`
  → **63 passed**.
- Tam suite: `QT_QPA_PLATFORM=offscreen py -3.13 -m pytest -q`
  → **1971 passed, 7 failed**.
  **7 hata ONCEDEN VAR** (bu gorevle ilgisiz): `test_j1939_v190_load.py`,
  `test_t48_t44_recovery.py`, `test_benchmark_ai_copilot.py`,
  `test_e2e_safety_audit.py` — hepsi **diagnostic DB JSON icerigi** (SPN sayisi
  3937 vs 3910 vb.; baska gorevlerin `data/diagnostics/*.json` degisiklikleri)
  ile ilgili. **Bu dosyalarin hicbiri benim 4 modulumu import etmiyor.** Kanit:
  src degisikliklerim `git stash` ile geri alinip ayni testler tekrar calistirildi
  → **ayni 7 FAIL**.
- `ruff check` (benim dosyalarim): **All checks passed.**
  Depo genelindeki kalan 72 hata `scripts/*.py` (izlenmeyen scraper'lar) ve
  `tests/unit/test_t57a_*.py` (baska ajanin dosyasi) icinde — kapsam disi.

---

## DEGISEN DOSYALAR

| Dosya | Bulgu |
|---|---|
| `src/engine/buffer/rolling_disk.py` | RD-1, RD-2 |
| `src/engine/buffer/ring_buffer.py` | RB-1 |
| `src/engine/router.py` | RT-1, RT-2 |
| `src/engine/decoder/dbc_decoder.py` | DB-1, DB-2 |
| `tests/unit/test_t58_c_bus_reliability.py` | 18 regresyon testi (yeni) |
| `tests/unit/test_rolling_disk.py` | RD-2 strict-opt-in guncellemesi |
