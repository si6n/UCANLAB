# T57-D — Kanıt: Flasher İmza Zinciri (F-1/F-2/F-4) + J1939 TP DoS Sınırları (U-2/J-1/J-2/J-5)

**Commit:** `85e56d3` — `fix(protocols): T57-D F-1/F-2/F-4 flasher imza zorunlu + J1939 TP DoS sinir`
**Tarih:** 2026-09-16
**Kaynak bulgular:** `docs/review/ASAMA-4-protokoller.md` — F-1 (YÜKSEK), U-2 (ORTA), J-1 (ORTA), J-2 (ORTA), J-5 (ORTA), F-4 (ORTA)
**Kapsam (yalnız):** `src/protocols/uds/flasher.py`, `src/protocols/uds/client.py`, `src/protocols/j1939/transport.py`
**Test dosyaları:**
- `tests/unit/test_t57d_flasher_firmware_trust.py` (14 test — F-1/F-2/F-4)
- `tests/unit/test_t57d_j1939_tp_dos.py` (9 test — U-2/J-1/J-2/J-5)

---

## ÖNCE / SONRA — TDD kırmızı→yeşil

Yeni testler, fix öncesi kaynakla (git `d964038`) koşturuldu:

```
ÖNCE (d964038 kaynağı):  22 failed, 1 passed   (EXIT=1)
SONRA (85e56d3 kaynağı): 23 passed             (EXIT=0)
```

Temsili kırmızı assertion'lar (önce):
```
test_f1_missing_signature_fails_closed_before_any_tx   → DID NOT RAISE (imza kapısı yoktu)
test_f4_target_identity_mandatory_by_default           → DID NOT RAISE (kimlik kapısı yoktu)
test_u2_flow_control_response_is_inbound_triggered     → assert False  (FC inbound_triggered değildi)
test_j1_tx_session_count_is_bounded                    → assert len(_tx_sessions) <= 256  (sınırsız)
test_j2_repeated_hold_cts_eventually_aborts            → assert aborted  (HOLD seli oturumu canlı tutuyordu)
test_j5_legit_reassembly_completes_despite_parallel    → assert msg2 is not None  (paralel PGN tüm DT'yi düşürüyordu)
```

---

## F-1 (YÜKSEK) — Firmware imzası kriptografik doğrulama + anti-rollback

**Dosya:satır (fix sonrası):** `src/protocols/uds/flasher.py:106-107` (`trusted_pubkey`/`require_signature`),
`:488-521` (Ed25519 fail-closed doğrulama bloğu), `:640-668` (`min_version` anti-rollback karşılaştırması),
`:249-266` (`_parse_ecu_version`).

**Kök neden:** Eski blok yalnızca `len(config.firmware_signature)` kontrol ediyor ve `min_version`'ı
sadece **log**luyordu — imajla kriptografik doğrulama yoktu, anti-rollback tabanı hiç karşılaştırılmıyordu
(fail-open). Kurcalanmış imaj flash edilebiliyordu.

**Fix:**
- `require_signature: bool = True` (varsayılan zorunlu) + `trusted_pubkey: Ed25519PublicKey | None`.
- İmza `trusted_pubkey.verify(config.firmware_signature, bytes(config.data))` ile **imaj baytlarının
  tamamı** üzerinde doğrulanır; `InvalidSignature` → `FLASH_SIGNATURE_INVALID`.
- Ayrı fail-closed kodları: `FLASH_SIGNATURE_MISSING` (imza yok/boş), `FLASH_TRUST_ANCHOR_MISSING`
  (trust anchor yok). Üçü de **hiçbir frame ECU'ya gitmeden** reddeder.
- `min_version > 0` iken ECU sürümü (`version_did=0xF189`) okunur; okunamaz/ayrıştırılamazsa
  `FLASH_ROLLBACK_UNVERIFIABLE`, ECU sürümü < taban ise `FLASH_ROLLBACK_DENIED`.
- `require_signature=False` yalnızca açık opt-in ile ve UYARI logu ile geçer.

**Testler:** `test_f1_missing_signature_fails_closed_before_any_tx`,
`test_f1_missing_trust_anchor_fails_closed`, `test_f1_invalid_signature_fails_closed`,
`test_f1_tampered_image_fails_closed`, `test_f1_valid_signature_accepted`,
`test_f1_explicit_require_signature_false_is_the_only_optout`, `test_f1_min_version_rollback_denied`,
`test_f1_min_version_satisfied_allows_flash`, `test_f1_min_version_unverifiable_fails_closed`.

---

## F-4 (ORTA) — VIN/serial hedef doğrulaması varsayılan zorunlu

**Dosya:satır (fix sonrası):** `src/protocols/uds/flasher.py:117` (`require_target_identity=True`),
`:529-534` (fail-closed ön kapı), `:609-632` (VIN/serial DID karşılaştırması).

**Kök neden:** Hedef kimlik doğrulaması opsiyoneldi ve varsayılan **kapalı**ydı → çok-ECU bus'ta
yanlış ECU'ya flash riski.

**Fix:** `require_target_identity=True` varsayılan; `expected_vin`/`expected_serial` ikisi de yoksa
`FLASH_TARGET_IDENTITY_REQUIRED` ile TX öncesi reddedilir. Kimlik verilince DID (0xF190 / serial)
okunur, uyuşmazlıkta fail-closed reddedilir.

**Testler:** `test_f4_target_identity_mandatory_by_default`, `test_f4_vin_verified_when_supplied`,
`test_f4_vin_mismatch_fails_closed`.

---

## F-2 (plumbing) — Gateway HMAC token her kritik adımda sunulur

**Dosya:satır (fix sonrası):** `src/protocols/uds/flasher.py:217-246` (`_confirmation_token`),
`:118-121` (`confirmation_token_factory`); çağrı noktaları: `:598`, `:679`, `:752`, `:770`, `:841`, `:872`, `:939`.
`src/protocols/uds/client.py` — kritik metodlara (`change_session`/`request_download`/`transfer_data`/
`request_transfer_exit`/`start_routine`/`ecu_reset`/`write_did`) `confirmation_token` parametresi eklendi.

**Kök neden:** T47-B token plumbing'i client'a eklemişti ama flasher composition root'tan token
almıyordu → `confirmation_secret` wired üretimde flash step-2'de reddedilirdi (kullanılamaz).

**Fix:** `_confirmation_token()` her kritik adımda taze (single-use, arb-id'ye bağlı) token üretir;
`config.confirmation_token_factory` (composition-root) → gateway `issue_confirmation_token` sırasıyla
çözülür; secret yoksa (legacy wiring) `None` döner ve parametre atlanır (yanlış token uydurulmaz).

**Testler:** `test_f2_gateway_token_presented_on_every_critical_step`,
`test_f2_no_secret_gateway_omits_token`.

---

## U-2 (ORTA) — ISO-TP FC/CF yanıtları `inbound_triggered=True`

**Dosya:satır (fix sonrası):** `src/protocols/uds/client.py:800-806` (`_send_and_receive` FC yolu),
`:924-930` (`send_functional` FC yolu).

**Kök neden:** İstemcinin **gelen** çok-parçalı isteğe verdiği ISO-TP FC/CF yanıtları
`inbound_triggered=True` ile gönderilmiyordu → hostile ECU'nun FC seli global 400 msg/s envelope'ı
aşar + E-Stop (uzak self-DoS). J1939 yolu zaten bu bayrağı işaretliyordu.

**Fix:** İki yolda da `send_sync(..., budget_category="protocol_burst", inbound_triggered=True)`;
`TypeError` fallback'i kademeli (önce `inbound_triggered`, sonra eski imza) — geriye uyum korunur.

**Testler:** `test_u2_flow_control_response_is_inbound_triggered`,
`test_u2_send_functional_flow_control_is_inbound_triggered`.

---

## J-1 (ORTA) — TX (CMDT sender) oturum üst sınırı + otomatik reap

**Dosya:satır (fix sonrası):** `src/protocols/j1939/transport.py:320` (`MAX_TX_SESSIONS=256`),
`:545-568` (`_reap_stale_tx_sessions`), `:1313-1332` (`start_cmdt_transfer` içinde reap + cap/evict),
`:1589-1601` (`poll_cmdt_timeouts` delegasyonu).

**Kök neden:** `start_cmdt_transfer` TX oturumlarını **sınırsız** açıyordu; `poll_cmdt_timeouts`'un
üretim çağırıcısı yoktu → `_tx_sessions` process ömrü boyunca sızıyordu.

**Fix:** `_reap_stale_tx_sessions` her `start_cmdt_transfer`'da süresi dolmuş (T2/T3) oturumları
Abort (reason 3) ile boşaltır; tablo `MAX_TX_SESSIONS`'a ulaşınca en bayat oturum tahliye edilir
(Abort kuyruklanır). `poll_cmdt_timeouts` aynı helper'a delege eder.

**Testler:** `test_j1_tx_session_count_is_bounded`, `test_j1_stale_tx_session_auto_reaped_on_new_transfer`.

---

## J-2 (ORTA) — HOLD (packet_count=0) ve `next_seq` rewind sınırları

**Dosya:satır (fix sonrası):** `src/protocols/j1939/transport.py:324-325` (`MAX_HOLD_CTS=8`,
`HOLD_WINDOW_S=2.0`), `:329` (`MAX_REWIND_DEPTH=2`), `:1470-1516` (`_handle_tx_cm` HOLD + rewind
denetimi), `:293-299` (`CmdtSenderSession.hold_count`/`hold_window_start`).

**Kök neden:** `_handle_tx_cm` sınırsız HOLD CTS (packet_count=0) kabul ediyor ve her birinde
`last_activity_time` tazeliyordu → sahte peer oturumu hiç reap ettiremiyordu. Derin `next_seq` rewind
zaten gönderilmiş DT paketlerini yeniden yayınlatıyordu (TX amplifikasyon).

**Fix:** HOLD sayısı kayan pencerede (`HOLD_WINDOW_S`) `MAX_HOLD_CTS` ile sınırlı; aşımda Abort
(`ABORT_REASON_UNEXPECTED_CONTROL`) + oturum kapanır. `next_seq` rewind derinliği `MAX_REWIND_DEPTH`
ile sınırlı; aşımda Abort. Gerçek CTS'te HOLD sayaçları sıfırlanır.

**Testler:** `test_j2_repeated_hold_cts_eventually_aborts`, `test_j2_deep_rewind_aborts`,
`test_j2_shallow_rewind_still_allowed`.

---

## J-5 (ORTA) — Paralel PGN oturumunda tüm DT'yi düşürme yerine sahiplik çözümü

**Dosya:satır (fix sonrası):** `src/protocols/j1939/transport.py:907-954` (`_lookup_dt_session`,
`seq_num` parametresi + en-yeni oturum seçimi), `:969` (`_handle_tp_dt` çağrısı).

**Kök neden:** Aynı (SA, DA, channel) için PGN-kapsamlı paralel oturumlar oluşunca
`_lookup_dt_session` belirsizlikte **TÜM** DT'yi düşürüyordu → yavaş saldırgan paralel oturum açıp
meşru reassembly'yi DoS edebiliyordu.

**Fix:** DT'nin `seq_num`'ı, `expected_sequence` eşleşen oturuma yönlendirir; hâlâ belirsizse
en-yeni (canlı) oturum seçilir — blanket drop yok. Cross-PGN payload karışımı yine engellenir
(seçim tek oturum üzerinden).

**Testler:** `test_j5_ambiguous_lookup_selects_matching_session`,
`test_j5_legit_reassembly_completes_despite_parallel_session`.

---

## Doğrulama

| Adım | Komut | Sonuç |
|------|-------|-------|
| TDD kırmızı | pre-fix kaynak + yeni testler | 22 failed, 1 passed |
| TDD yeşil | `py -3.13 -m pytest tests/unit/test_t57d_*.py -q` | **23 passed** |
| Regresyon (UDS/ISO-TP/flasher) | `py -3.13 -m pytest tests/unit/test_flasher.py tests/unit/test_isotp.py tests/unit/test_protocol_binary_conformance.py tests/unit/test_j1939_transport.py -q` | yeşil |
| Lint | `ruff check src/protocols/uds/flasher.py src/protocols/uds/client.py src/protocols/j1939/transport.py` | **All checks passed** |
| Tüm suite | `QT_QPA_PLATFORM=offscreen py -3.13 -m pytest -q` | 7 failed, **1994 passed** |

**Not (kapsam dışı 7 kırmızı):** Tümü veri-kayması kaynaklı, T57-D dosyalarıyla ilgisiz:
`j1939_spn_fmi_database.json` SPN sayısı 3937 (dondurulmuş 3910) → `test_j1939_v190_load.py` (3),
`test_t48_t44_recovery.py` (2), `test_benchmark_ai_copilot.py` (1), `test_e2e_safety_audit.py::test_audit_database_integrity_and_scale` (1).
T57-D kapsamındaki tüm testler yeşil.

**İlişkili düzeltme:** `tests/safety/test_e2e_safety_audit.py::test_audit_flasher_fail_closed_contract`
fix öncesi flasher'a imzasız config verdiği için yeni zorunlu kapıya takılıyordu; test imzalı config
(`trusted_pubkey`+`firmware_signature`+`require_target_identity=False`) ile güncellendi — assertion
gevşetilmedi.
