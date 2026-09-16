# Review Bulguları — Doğrulama ve İyileştirme Planı

**Tarih:** 2026-09-16
**Kaynak:** `docs/review/ASAMA-{1..6}*.md` (Claude/ajan review, 2026-09-15)
**Doğrulama:** Her bulgu mevcut kodda **satır satır** kontrol edildi + testler çalıştırıldı.

---

## 1. Özet

| Durum | Sayı |
|---|---|
| ✅ **Zaten düzeltilmiş** (T47-B) | **16** |
| ❌ Açık | 49 |
| ⚠️ Kısmi | 1 |
| **TOPLAM** | **66** |

> **Kritik bağlam:** Review raporları **T47-B commit'i (7571b07) ÖNCESİ** koda dayanıyor.
> T47-B, ASAMA-1'in en ağır bulgularını (G-1/G-2/G-3/E-1/G-5/E-2/S-2/G-7...) **zaten kapatmış**.
> Rapor satır numaraları bu yüzden kaymıştır — kanıt için **kod metni** esas alınmalı.

**Test kanıtı:** `test_t47_tx_review_fixes.py` → **35/35 PASS**
(ilgili 134 test PASS)

---

## 2. ✅ Zaten Düzeltilmiş (T47-B) — Aksiyon Gerekmez

| # | Bulgu | Kanıt |
|---|---|---|
| G-1 | Sentetik hız interlock'u eziyor | `_physical_speed_kmh`/`_display_speed_kmh` ayrıldı; 4 test |
| G-2 | Kritiklik çağıran bayrağı | `_frame_is_critical()` + `CRITICAL_UDS_SIDS`; 5 test |
| G-3 | Kriptografik kapılar kapalı | `confirmation_secret`+`auth_secret` wiring; `test_g3_*` |
| E-1 | Mint/verify ayrımı nominal | `EStopResetAuthority` zorunlu ayrı provider; 4 test |
| G-5 | Rate lane çağıran kontrolünde | Global TX rate bound; 1 test |
| E-2 | Secret I/O kilit altında | `_load_secret` kilit dışı; 2 test |
| S-2 | `_verify_arm_token` TOCTOU | Kilit altına alındı; 1 test |
| G-7 | Test-mode bypass korumasız | Read-only property; 2 test |
| G-8 | NaN sentetik freshness sıfırlar | Display-only; 1 test |
| G-9 | Whitelist eşiği yanlış | Superset allow; 1 test |
| S-3 | Constructor tip kontrolsüz | `is_engaged` kontrolü; 2 test |
| S-4 | `expiry` OverflowError | → `SafetyError`; 2 test |
| E-3 | Backoff kozmetik | Önce reddet; 1 test |
| E-4 | Ölü raw-nonce API | Kaldırıldı; 1 test |
| E-5 | Ephemeral fallback sessiz | CRITICAL log; 1 test |
| F-2 | Flash token plumbing yok | `confirmation_token` eklendi |

---

## 3. 🔴 Açık Bulgular — Öncelik Sırası

### P0 — KRİTİK (4 bulgu, ticari yayın engeli)

| # | Dosya | Sorun | Efor |
|---|---|---|---|
| **L-1** | `launcher/app.py:133` | `has_valid_license` **launch kapısında yok** → lisanssız çalışır | 1g |
| **A3-1** | `desktop_app.py:1107` | Bridge `spn`/`fmi` sabit `None` → J1939 KB yolu ölü | 1g |
| **U-1** | `uds/client.py:51` | `max_pending_timeout_s=None` → NRC 0x78 **sonsuz döngü** | 1g |
| **C-1** | `desktop_app.py:2012` | `load_replay` **allowlist yok** → UNC/SMB kimlik sızıntısı | 1g |

### P1 — YÜKSEK (10 bulgu)

| # | Dosya | Sorun |
|---|---|---|
| S-1 | `state_machine.py:508` | `trigger_fault` FAULT geçişini **düşürüyor** (fail-open) |
| L-2 | `launcher/auth.py:94` | Launcher HWM kalıcı değil → rollback tespiti sıfırlanır |
| L-3 | `license_flow.py:142` | `_hmac_key` hata yutup yeni anahtar → mühür uyuşmaz |
| L-4 | `validator.py:83` | Wildcard frozen kontrolü yok (latent) |
| L-5 | `validator.py:362` | Token tarafı sentinel kapıdan geçmiyor (latent) |
| L-6 | `license_flow.py:406` | `is_offline` beyana bağlı; `exp`/`offline_until` ayrı |
| A3-2 | `anomaly_detector.py:169` | `OP:` öneki çözülmüyor → operatör beyanı ölü |
| A3-3 | `hypothesis_engine.py:210` | Benzer-vaka kanıt eşiği yok → kanıtsız hipotez |
| A3-4 | `diagnostic_copilot.py:3879` | Geniş `except Exception: pass` |
| F-1 | `flasher.py:401` | Firmware imzası doğrulanmıyor |
| H-2 | `desktop_app.py:333` | Heartbeat token'sız (liveness legacy yol) |

### P2 — ORTA (aşağıdaki görevlerde toplu)

- **ASAMA 1:** G-4 (set.pop), G-6 (abort hook), G-10 (estop.trigger kilit), G-11 (whitelist mutable), W-1 (flash wiring)
- **ASAMA 2:** G-1t (enforce çağrılmıyor), V-1, B-1, B-2, B-3, A-1
- **ASAMA 3:** A3-5..A3-10, A3-13 (ölü accessor)
- **ASAMA 4:** U-2, I-1, I-2, J-1, J-2, J-5, F-4
- **ASAMA 5:** RD-1, RD-2, RB-1, RT-1, RT-2, DB-1, DB-2
- **ASAMA 6:** U-1u, H-1, H-3, O-1, O-2, D-3, D-4, D-5

---

## 4. İyileştirme Planı — Ajan Dağıtımı

| Görev | Ajan | Kapsam | Bulgular | Efor |
|---|---|---|---|---|
| **T56-A** | tuner | Lisans launch kapısı (KRİTİK) | L-1 | 1g |
| **T56-B** | tuner | AI motoru bridge SPN/FMI (KRİTİK) | A3-1, A3-2 | 1g |
| **T56-C** | tuner | UDS NRC 0x78 mutlak sınır (KRİTİK) | U-1, I-1, I-2 | 1g |
| **T56-D** | tuner | UI replay path allowlist (KRİTİK) | C-1, O-5, D-4 | 1g |
| **T57-A** | tuner | TX güvenlik kalanları | S-1, G-4, G-6, G-10, G-11, W-1 | 1.5g |
| **T57-B** | tuner | Lisans HWM + wildcard + sentinel | L-2, L-3, L-4, L-5, L-6, V-1 | 1.5g |
| **T57-C** | tuner | AI motoru fail-open + kanıt | A3-3, A3-4, A3-5, A3-6, A3-9 | 1.5g |
| **T57-D** | tuner | Flasher imza + UDS/ISO-TP | F-1, U-2, J-1, J-2, J-5, F-4 | 1.5g |
| **T57-E** | tuner | Veri yolu HWM + izolasyon | RD-1, RD-2, RB-1, RT-1, RT-2 | 1.5g |
| **T57-F** | tuner | UI/IPC token + watchdog | H-1, H-2, H-3, O-1, O-2, D-3, D-5 | 1.5g |
| **T58-A** | telemetry | Anti-tamper + HWID sağlamlaştırma | G-1t, B-1, B-2, B-3, A-1 | 1g |
| **T58-B** | scout | AI motoru analiz kalitesi | A3-7, A3-8, A3-10, A3-13 | 1g |
| **T58-C** | chassis | DBC decoder gözlemlenebilirlik | DB-1, DB-2 | 0.5g |
| **T59** | marshal | **Bağımsız denetim** (T56-T58) | tüm fix'ler | 2g |

**Sıra:** T56 (kritik) → T57 (yüksek) → T58 (orta) → T59 (denetim)

---

## 5. Kabul Kriterleri (her görev)

```
1) Her bulgu için önce BAŞARISIZ test yaz (TDD)
2) Fix uygula
3) Test geçsin: py -3.13 -m pytest <dosya> -q
4) Tam suite: QT_QPA_PLATFORM=offscreen py -3.13 -m pytest -q
5) ruff check . temiz
6) Commit mesajı: fix(<katman>): T56-X <bulgu-no> <açıklama>
7) Kanıt: dosya:satır + test adı + önce/sonra davranış
```

**Yasaklar:**
- Test assertion'ını gevşeterek "geçirme"
- Bulguyu "yanlış pozitif" sayıp atlama — gerekçe + kanıt yaz
- Toplu `git reset`/`clean`
- Davranışı değiştirmeden yalnız yorum düzeltme (belirtilmedikçe)

---

## 6. Notlar

- **Rapor satır numaraları kaymış.** T47-B sonrası kod değişti. Fix yaparken **kod metnini** ara, satır numarasına güvenme.
- **F-2 düzeltildi ama flasher kullanılamaz.** Token plumbing eklendi; flasher'ın composition root'tan token alması gerekiyor (F-1 ile birlikte ele alınmalı).
- **ASAMA 2'nin iki modülü üretimde bağlı değil** (`LicenseValidator`, `AntiTamperGuard`). L-4/L-5 latent; L-1/L-2/L-3/L-6 **gerçek**.
- **A3-1 KRİTİK ama "yanlış teşhis"** riski — araç güvenliği değil, ürün kalitesi. Yine de ticari değer için P0.

## İlgili
- `docs/review/ASAMA-{1..6}*.md` — kaynak raporlar
- `spn_gap_hunter/output/review_verification.json` — makine-okunur doğrulama
- `tests/unit/test_t47_tx_review_fixes.py` — düzeltilmiş bulguların testleri
