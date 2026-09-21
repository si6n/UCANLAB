# UCANLAB İYİLEŞTİRME PLANI

**Dayanak:** `UCANLAB_BULGU_DOGRULAMA_RAPORU_35.md` (35 bulgunun bağımsız doğrulaması)
**Tarih:** 2026-09-21
**Durum:** ⏸️ **ONAY BEKLİYOR** — bu belge bir plandır, henüz hiçbir kod değiştirilmedi.
**Kapsam ilkesi:** Yalnız **doğrulanmış gerçek** kusurlar plana alındı. Raporun yanlış/stale olan
24 bulgusu ve fail-closed invariantını zayıflatan önerileri **kapsam dışı** bırakıldı.

---

## 0. Yönetici Özeti

35 bulgunun doğrulanması sonucunda **canlı HIGH kusur bulunmadı**. Uygulanabilir iş listesi
**3 doğrulanmış kusur + 2 işaretsiz hardening + 1 düşük öncelikli nit**'e indi (toplam 6 madde,
hepsi HIGH değil). Bu plan bunları öncelik sırasına göre fazlara ayırır.

| Faz | İçerik | Risk | Tahmini Etki | Dosya Sayısı |
|---|---|---|---|---|
| **FAZ 1** | #6 Router per-frame lock darboğazı | Düşük | RX throughput ↑ (davranış aynı) | 1 |
| **FAZ 2** | #5 Flasher kilitsiz durum (dar yarış) | Düşük-Orta | Cancel/config yarışı kapanır | 1 |
| **FAZ 3** | #3 CAN-FD payload taban tutarlılığı | Orta | FD çerçeve invariantı sıkılaşır | 1 |
| **FAZ 4** | Cloud cert pinning + license `issued_at` | Düşük | Hardening (yeni yetenek) | 2 |
| **FAZ 5** | #25/#34 Negatif mask reddi (nit) | Çok düşük | Hardening | 1 |
| **FAZ 6** | Regresyon + doğrulama | — | Tüm fazları kilitler | testler |

**Toplam:** 6 kaynak dosya + 1 yeni test dosyası. Hiçbiri ISO 26262 invariantını değiştirmez.

---

## 1. FAZ 1 — `#6` Router per-frame lock darboğazı  **[ÖNCELİK: P2, ilk uygulanacak]**

### Sorun (doğrulandı)
`src/engine/router.py:153-165`:
```python
def route_frame(self, frame: CanFrame) -> int:
    subscribers = self._route_snapshot
    with self._stats_lock:  # RT-1: routed uses the same lock as dropped
        self._total_routed += 1
```
Her gelen çerçevede **global** `threading.Lock` alınıp tek bir `int` artırılıyor. Fan-out kilitsiz
(immutable snapshot) olduğu hâlde tüm RX ingest thread'leri bu tek mutex'te serialize oluyor.
Yüksek yükte gerçek throughput darboğazı. **Yarış/bug değil** — davranış doğru.

### Neden "RT-1" yorumu var (dikkat edilmesi gereken)
`stats()` (:298-306) `total_routed` ve `total_dropped`'ı **aynı** `_stats_lock` altında okuyor ki
iki sayaç torn-pair okunmasın. Çözüm bu tutarlılığı **bozmamalı**.

### Önerilen çözüm (tek seçenek değil, seçilecek)
**Seçenek A (önerilen): thread-local sayaç + periyodik flush.**
- Her thread kendi `threading.local()` içinde yerel sayacı biriktirir.
- Flush eşiğe ulaşınca (örn. 256) veya `stats()` çağrısında `_stats_lock` altında toplanır.
- `stats()` çağrısı önce tüm thread'lerin yerel sayaçlarını toplar (register edilmiş sayaç listesi),
  böylece tutarlı snapshot korunur.
- Tradeoff: `stats()` biraz daha karmaşık; sayaç okuması "yaklaşık" olur (flush sonrası kesin).

**Seçenek B (daha basit): atomik/serialize'siz sayaç.**
- `itertools.count()` yerine, CPython GIL'i altında `+= 1` zaten büyük ölçüde atomiktir — ancak
  bu bir implementasyon detayıdır ve "RT-1 torn-pair" gerekçesini zayıflatır. **Önerilmez**
  (portabilite + okunabilirlik kaybı).

**Seçenek C (en muhafazakâr):** lock'u bırak, `stats()`'ı "eventually consistent" yap ve `total_dropped`
ile birlikte okunması gereken tek yer varsa orada ayrı bir snapshot kullan.

### Kabul kriteri / test
- `tests/unit/` mevcut router testleri **değişmeden** geçer.
- Yeni test: çok-thread'li `route_frame` sonrası `stats()["total_routed"]` **tam** beklenen değere eşit
  (flush davranışı doğru).
- Yeni test: `stats()` torn-pair üretmez (routed ≥ dropped her zaman).
- **Ölçüm:** 4 thread × 100k çerçeve mikro-benchmark; lock contention süresi öncesi/sonrası raporlanır.

### Geri alma
Tek fonksiyon/alan değişikliği → `git revert` tek commit.

---

## 2. FAZ 2 — `#5` Flasher kilitsiz durum  **[ÖNCELİK: P3]**

### Sorun (doğrulandı)
`src/protocols/uds/flasher.py`:
- `self.current_step: FlashingStep = FlashingStep.IDLE` (:239) — **kilit yok**
- `self._is_cancelled = False` (:240) — `cancel()` :334-337 yazar, `_check_cancelled` :339-343 okur
- `self._active_config: FlashingConfig | None = None` (:244) — `execute_flash` :498 yazar,
  `_confirmation_token` :267 okur

Eşzamanlı ikinci `execute_flash` ya da UI thread'inden `cancel()`, bu üç alanda interleave olabilir.
Keep-alive/`FirmwareContainer` yolunda kilit eksikliği **yok** (o kısım `threading.Event` ile doğru).

### Önerilen çözüm
1. `self._state_lock = threading.RLock()` ekle (RLock: `_confirmation_token` iç çağrıları reentrant).
2. Üç alanın tüm okuma/yazmalarını bu kilit altına al:
   - `current_step` → **property** yap (setter/getter kilitli); `FlashingStep` enum olduğu için
     tek-yazma/tek-okuma atomikliği yeterli ama property tek nokta sağlar.
   - `cancel()` / `_check_cancelled()` → `_is_cancelled` okuma-yazması kilit altında.
   - `_active_config` → kilit altında oku/yaz.
3. **Re-entrancy uyarısı:** `execute_flash` uzun süren bir akış; kilidi **tüm flash boyunca tutmak
   yanlış** olur (cancel() bloke olur). Kilit yalnız *alan erişimleri* için kısa tutulmalı —
   yani "kilidi bırak, I/O yap, tekrar al" deseni.

### Kritik uyarı (AGENTS.md uyumu)
`flasher.py` **WIRED** bir TX yoludur (AGENTS.md §2.7). Değişiklik:
- `TxSafetyGateway` choke-point'ini **atlamamalı**,
- step-token üretimini (`create_confirmation_issuer`) **bozmamalı**,
- P2-7/P2-8/H-KA notlarını **ihlal etmemelidir**.
Bu nedenle FAZ 2 yalnız *state erişim kilitleme* ile sınırlıdır; TX akışına dokunulmaz.

### Kabul kriteri / test
- `tests/unit/test_flasher.py` ve `tests/unit/test_t57d_flasher_firmware_trust.py` geçer.
- Yeni test: flash sürerken başka thread'den `cancel()` → cancellation **görülür** (yarış simülasyonu,
  `threading.Barrier` ile deterministik).
- Yeni test: eşzamanlı iki `execute_flash` → ikincisi fail-closed (busy) reddedilir.

---

## 3. FAZ 3 — `#3` CAN-FD payload taban tutarlılığı  **[ÖNCELİK: P3, dikkatli]**

### Sorun (doğrulandı)
`src/core/models/can_frame.py:177-188`:
```python
expected_len = DLC_TO_LENGTH[self.dlc]
if self.is_fd and self.dlc >= 9:
    if not (0 < len(self.data) <= expected_len):   # ← yalnız ÜSTTEN sınır
        raise ValueError(...)
elif len(self.data) != expected_len:               # Classic: tam eşitlik
    raise ValueError(...)
```
FD dalı yalnız **üstten** sınırlıyor; `is_fd=True, dlc=15, len(data)=1` **geçer**. Classic dalı
tam eşitlik istediği için asimetri var. Alt sınır eksik.

### Risk değerlendirmesi (neden "dikkatli")
CAN-FD'de kısa payload **meşru** olabilir (FD padding opsiyoneldir; DLC kapasiteyi, len(data) gerçek
yükü belirtir). Dolayısıyla "taban" zorlamak **meşru FD çerçevelerini kırabilir**. Bu yüzden:
- **Önerilen:** alt sınırı **agresif** koyma. Bunun yerine invariantı *belgele* ve **açık** hale getir:
  ya `len(data) >= 1` (mevcut) korunur, ya da **tutarlılık** kuralı eklenir: FD DLC 9..15 için
  `len(data)` yalnız izinli FD uzunluklarından biri olmalı (0..64 arası, DLC kapasitesine ≤).
- **Alternatif:** bu davranışın kasıtlı olduğunu belgeleyen yorum + test ekle; kod **değiştirme**.

### Karar gereken nokta (kullanıcıya sorulacak)
Bu madde **ya "sıkılaştır" ya "dokümante et"** olabilir. Sniffer/analiz bağlamında FD çerçevelerini
**kabul etmek** doğru olabilir (ham yakalama). Bu nedenle FAZ 3, FAZ 1/2'den **sonra** ve
**opsiyonel** olarak konumlandırıldı.

### Kabul kriteri / test
- `tests/unit/` + `tests/safety/` mevcut CAN-FD testleri geçer (kırılmamalı).
- Karar "dokümante et" ise: `__post_init__`'e FD için alt-sınır gerekçesi yorumu + invariant testi.

---

## 4. FAZ 4 — İşaretsiz hardening (raporda YOK, doğrulamada bulundu)  **[ÖNCELİK: P4]**

### 4a. Cloud sertifika pinning
- **Dosya:** `src/security/cloud/client.py`
- **Durum:** `urllib` varsayılan SSL context kullanıyor → **SPKI/cert pinning yok**. HTTPS zorunlu,
  host allowlist var, redirect'te credential strip var, ama pinning yok.
- **Öneri:** Opsiyonel `ssl_context` enjeksiyonu (`CloudConfig.pinned_spki_sha256: list[str] | None`)
  ekle; verilirse doğrulayıcı bir `ssl.SSLContext` kurup pin kontrolü yap. **Varsayılan: kapalı**
  (geriye dönük uyum).
- **Risk:** Düşük. Yeni opsiyonel parametre; default `None` → mevcut davranış aynı.
- **Test:** pin eşleşince başarılı; pin uyuşmazsa `SecurityError`. Mock SSL nesnesiyle.

### 4b. License `issued_at` doğrulaması
- **Dosya:** `src/security/license/validator.py`
- **Durum:** `issued_at` şemada zorunlu (:359-366) ama **parse edilip `now` ile karşılaştırılmıyor**
  → ileri tarihli token kabul edilebilir.
- **Öneri:** `if payload.issued_at > now + ISSUED_AT_TOLERANCE_S: raise LicenseError(code="LICENSE_FUTURE_DATED")`.
  Tolerans saat kayması için (örn. 300 s).
- **Risk:** Düşük — ama **meşru** saat kayması olan makineleri reddedebilir → tolerans şart.
- **Test:** ileri tarihli token reddedilir; tolerans içindeki token kabul edilir.

---

## 5. FAZ 5 — `#25`/`#34` Negatif mask reddi  **[ÖNCELİK: P5, nit]**

### Sorun (doğrulandı — dar)
`src/safety/gateway.py:187-203` `_validate_mask_breadth` yalnız `mask == 0` reddediyor; negatif mask
konstrüksiyondan geçer. Ancak negatif mask ile **geniş fail-open erişilemez** (non-negatif
`arbitration_id` gerçek ID'lerle eşleşmez) → gerçek zafiyet değil, hardening nit'i.

### Önerilen çözüm
```python
for value, mask in masks:
    if mask <= 0:                      # mask == 0 fail-open; mask < 0 anlamsız
        raise ValueError(f"whitelist maskesi pozitif olmali, alindi: {mask}")
```
(Yorum + `WHITELIST_MASK_INVALID` koduyla `SafetyError` de tercih edilebilir.)

### Kabul kriteri / test
- Yeni test: negatif mask → konstrüksiyonda `ValueError`. Mevcut mask testleri geçer.

---

## 6. FAZ 6 — Regresyon ve doğrulama

Her faz sonunda **zorunlu**:
1. `ruff check` (değişen dosyalar) → temiz.
2. `pytest tests/unit tests/safety -q` → mevcut **1997 passed** korunur
   (bilinen ortam-sınırlı `test_hwid.py::test_generate_hardware_fingerprint_structure` hariç).
3. Yeni testler **TDD red→green** kanıtlanır (fix geri alınınca kırmızı).
4. `tests/safety/test_ai_tx_isolation.py` yeşil kalır (AI katmanına dokunulmuyor).
5. Obsidian vault'a oturum raporu (`04-Ajan-Notlari/`, rol etiketi: `tuner`/`chassis`/`uplink`).

---

## 7. UYGULANMAYACAKLAR (gerekçeli ret listesi)

| Rapor # | Öneri | Ret gerekçesi |
|---|---|---|
| #35 | NaN'da timestamp reset'i kaldır | G-8 fail-closed invariantını zayıflatır; 5 mevcut testi kırar; E-Stop latch zaten doğru |
| #33/#24 | `reset_authority_provider` cache'le | ISO 26262 mint/verify ayrımını bozar (`ESTOP_AUTHORITY_NOT_INDEPENDENT` korumasını anlamsızlaştırır) |
| #32/#23 | `deque(maxlen=1000)` | Mevcut `maxlen=10_000`'den regresyon |
| #30/#21 | Watchdog token SHA-256 | No-op; in-process paylaşılan sır hash'lenince de bellekte kalır |
| #26 | `TxBudget.reset()` | Gereksiz; bucket `rebind_bus`'ta zaten taze kuruluyor |
| #1/#15/#18/#27/#7/#5(tam) | Çeşitli HIGH | İddia edilen kod mevcut değil (stale) |

---

## 8. Önerilen Yürütme Sırası ve Onay Noktaları

```
FAZ 1 (router)  ──┐
FAZ 2 (flasher) ──┼──► her faz sonunda test+lint ──► ara rapor
FAZ 3 (can_frame, OPSİYONEL/KARAR) ──┘
FAZ 4 (cloud/license hardening) ──► opsiyonel
FAZ 5 (mask nit) ──► opsiyonel
FAZ 6 (regresyon) ──► her fazda zaten uygulanır
```

**Karar noktaları (kullanıcıdan talimat beklenir):**
1. **Kapsam:** Yalnız FAZ 1+2 mi, yoksa FAZ 1–5 tamamı mı?
2. **FAZ 3:** "sıkılaştır" mı "dokümante et" mi? (FD çerçeve kabul politikası)
3. **FAZ 4:** Hardening (cloud pinning / `issued_at`) isteniyor mu, yoksa kapsam dışı mı?
4. **Commit stratejisi:** Faz başına ayrı commit mi, tek PR mı?

---

## 9. Özet

Bu plan, **35 bulgunun yalnızca doğrulanmış gerçek olanlarını** içerir. Rapor "5 HIGH acil PR" derken,
gerçek iş listesi **6 düşük/orta maddelik hardening + 1 performans + 1 dar yarış + 1 opsiyonel
invariant**'tır. Hiçbir madde ISO 26262 fail-closed invariantını değiştirmez; aksine, raporun
önerdiği 5 "düzeltme"nin **reddedilmesi** fail-closed bütünlüğünü korur.

**⏸️ Bu noktadan sonra talimat bekleniyor. Onay olmadan hiçbir kod değiştirilmeyecektir.**
