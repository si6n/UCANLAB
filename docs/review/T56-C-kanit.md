# T56-C — Kanıt: UDS NRC 0x78 Mutlak Sınır + ISO-TP Yan Bulgular

**Commit:** `6436ed2` — `fix(protocols): T56-C U-1/I-1/I-2 UDS NRC 0x78 mutlak sinir + STmin clamp + ISO-TP payload cap`
**Tarih:** 2026-09-16
**Kaynak bulgular:** `docs/review/ASAMA-4-protokoller.md` — U-1 (KRİTİK), I-1 (ORTA), I-2 (ORTA)
**Test dosyası:** `tests/unit/test_t56c_uds_pending_bound.py` (12 test, tümü PASS)

---

## U-1 — NRC 0x78 sınırsız döngü (KRİTİK)

**Dosya:satır (fix sonrası):** `src/protocols/uds/client.py:47-49` (`MAX_PENDING_ABS_S`/`MAX_NRC_78_COUNT`, eklenen), `:715-728` (`_send_and_receive` mutlak duvar), `:789-822` (0x78 dalı: sayac cap + clamp + tükenişte UDS_TIMEOUT).

**Kök neden:** `max_pending_timeout_s` varsayılanı `None` iken `max_absolute_deadline = None` kalıyordu; her 0x78'de `deadline = now + 5s` ileri itiliyordu. `timeout_s` dış duvar **değildi** (yorum yanlış güvence veriyordu).

**Fix:**
- `MAX_PENDING_ABS_S: ClassVar[float] = 30.0` — duvar her zaman sonlu; çağıranın `max_pending_timeout_s`'i override eder, asla devre dışı bırakamaz.
- `MAX_NRC_78_COUNT: ClassVar[int] = 200` — 0x78 sayacı üst limiti.
- `deadline = min(now + P2_STAR_TIMEOUT_S, max_absolute_deadline)`.
- Pending bütçesi tükendiğinde (duvar veya sayac) artık son 0x78'i "yanıt" gibi döndürmek yerine `UDS_TIMEOUT` fırlatır.

### ÖNCE (sonsuz döngü) — kanıt
- Pre-fix `client.py` (git HEAD) ile sürekli 0x78 gönderen sahte ECU'ya karşı koşum:
  ```
  PYTHONPATH=. timeout 20 py -3.13 <before-harness>
  EXIT=124   ← 20 s duvar kill etti; çağrı HİÇ dönmedi (sonsuz döngü)
  ```
- TDD kırmızı fazı: `test_t56c_u1_pending_spam_hits_absolute_bound` → `Failed: DID NOT RAISE ProtocolError`.

### SONRA (UDS_TIMEOUT) — kanıt
- Fix sonrası aynı sahte ECU'ya karşı koşum:
  ```
  RAISED ProtocolError UDS_TIMEOUT after 0.001 s; pendings served: 201
  ```
  (201 pending sonrası `MAX_NRC_78_COUNT=200` sayac tavanı devreye girdi; `_operation_lock` serbest kaldı.)
- TDD yeşil fazı: `tests/unit/test_t56c_uds_pending_bound.py` → **12 passed**.

**Test adları:** `test_t56c_u1_pending_spam_hits_absolute_bound`, `test_t56c_u1_nrc_78_count_cap_is_enforced`, `test_t56c_u1_default_absolute_bound_is_finite`.

---

## I-1 — `decode_st_min` rezerve 0xFA-0xFF → 127 ms (ORTA)

**Dosya:satır:** `src/protocols/uds/isotp.py:110-131` (`decode_st_min`), `:943-947` (`_apply_st_min` rezerve dalı).

**Fix:** rezerve 0xFA-0xFF ve 0x80-0xF0 artık **10 ms** spoof cap'e clamp; async `_apply_st_min` da aynı cap'i bekler.

**ÖNCE:** `decode_st_min(0xFA) == 127.0`; async sender sahte FC (STmin=0xFA) karşısında CF başına 127 ms bekliyordu.
**SONRA:** `decode_st_min(0xFA) == 10.0`; 20-baytlık transfer < 0.2 s (öncesi CF başına 127 ms).

**Test adları:** `test_t56c_i1_reserved_stmin_fa_ff_clamped` (6 parametre), `test_t56c_i1_sender_does_not_stall_127ms_on_reserved_stmin`.
**Güncellenen mevcut testler (eskiden hatalı davranışı doğruluyordu):** `tests/unit/test_isotp.py::test_isotp_decode_st_min_parametric`, `tests/e2e/test_phase1_e2e.py::test_tier1_isotp_fc_stmin_decoding_matrix`, `tests/e2e/test_phase1_e2e.py::test_tier2_isotp_stmin_reserved_clamped_to_spoof_cap`, `tests/e2e/test_challenger_safety_transport.py::test_isotp_decode_st_min_full_domain_and_clamping`.

---

## I-2 — `IsoTpSender.send` payload boyut sınırı yok (ORTA)

**Dosya:satır:** `src/protocols/uds/isotp.py:987-1005` (send başına eklenen cap kontrolü).

**Fix:** `send` artık `MAX_UDS_PAYLOAD_CLASSIC` (4096) / `MAX_UDS_PAYLOAD_FD` (1 MiB) cap'ini `IsoTpPayloadTooLargeError` ile uygular — `segment_message` ile aynı fail-closed sınır.

**ÖNCE:** `send` boyut kontrolsüzdü → >4096 baytlık (FD boyutlu) payload classic yolda kabul edilir, sınırsız CF akışı riski.
**SONRA:** cap üzeri payload hiçbir kare göndermeden reddedilir.

**Test adları:** `test_t56c_i2_sender_rejects_oversized_classic_payload`, `test_t56c_i2_sender_accepts_payload_at_cap`.
**Güncellenen test:** `tests/unit/test_adversarial_phase1_gate.py::test_async_roundtrip_boundaries_classic` parametrizasyonu 16384'ü classic yoldan kaldırdı (classic cap 4096; 16384 zaten FD testinde).

---

## Doğrulama
- `py -3.13 -m pytest tests/unit/test_t56c_uds_pending_bound.py -q` → **12 passed**
- `py -3.13 -m pytest tests/{unit/test_uds_client,unit/test_isotp,e2e/test_phase1_e2e,e2e/test_challenger_safety_transport,unit/test_adversarial_phase1_gate}.py -q` → **312 passed**
- `ruff check` (değişen 7 dosya) → **All checks passed**
- Tam suite: kalan kırmızılar **bu görevin kapsamı dışında** ve önceden mevcut — kardeş görevlerin (T56-A/T57-A/T57-C/T57-F/T58-A/T58-C) henüz fix'lenmemiş TDD-kırmızı dosyaları + veri/config drift (j1939 SPN sayısı 3937≠3910, rolling-disk, whitellist frozenset). UDS/ISO-TP kapsamında 0 kırmızı.
