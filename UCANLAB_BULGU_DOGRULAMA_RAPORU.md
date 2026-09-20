# UCANLAB Comprehensive Code Review — BULGU DOĞRULAMA RAPORU

**Denetlenen rapor:** `UCANLAB_COMPREHENSIVE_CODE_REVIEW.md`
**Doğrulama tarihi:** 2026-09-19
**Repository HEAD:** `1401f48` (raporun atıfta bulunduğu `1401f484a1542b56647755952ffca29555d0d4a3` ile eşleşiyor)
**Doğrulama yöntemi:** Her bulgu için ilgili kaynak dosya satırları okundu; şüpheli olanlar için **çalıştırılabilir kanıt** (Python repro) üretildi.

> **ÖNEMLİ NOT — Rapor satır/dosya referansları büyük ölçüde kaymış.**
> Rapor `src/ui/desktop_app.py` yerine `desktop_app.py:2330-2350` gibi yollar veriyor; gerçek dosya
> `src/ui/desktop_app.py` ve satırlar farklı. Ayrıca `src/engine/ai` yolları, `src/security/cloud`
> yolları doğru; fakat içerik referanslarının bir kısmı eski (stale) koda ait. Bu, raporun
> kısmen **eski bir snapshot** üzerinden ya da başka bir repo (github.com/si6n/UCANLAB) üzerinden
> üretildiğini gösteriyor.

---

## 1. Yönetici Özeti — Doğruluk Tablosu

| # | Rapor Bulgusu | Rapor Önem | **GERÇEK VERDİCT** | Kanıt |
|---|---|---|---|---|
| 1.1 | E2E hattı canlı yola bağlı değil | HIGH | ✅ **DOĞRU (tasarım gereği)** | `gateway.py:15-19` açıkça "WIRING-GATED" diyor |
| 1.2 | ISO-TP CF'lerde token boşluğu | HIGH | ❌ **YANLIŞ** | `client.py:743-752` CF'lere token+flag geçiyor; `gateway.py:1289-1296` fail-closed |
| 1.3 | `rebind_bus` kategori bütçelerini sıfırlamıyor | MEDIUM | ✅ **DOĞRU** | `gateway.py:499-508` `_budgets` reset YOK |
| 2.1 | Replay → fiziksel CAN TX | CRITICAL | ⚠️ **KISMEN DOĞRU** | Replay `_ingest_live_frame`'e gidiyor + TX üretiyor; ama **default filtre TP.CM'i blokluyor** |
| 2.2 | RP1210 wall-clock timestamp | HIGH | ✅ **DOĞRU** | `rp1210/bus.py:343,382` `time.time_ns()` |
| 2.3 | ReplaySafetyFilter state sızması | MEDIUM | ❌ **YANLIŞ** | `desktop_app.py:2699` her seansta **yeni** filtre |
| 3.1 | Flash UI/backend kopukluğu + sıfır dolgu | CRITICAL | ⚠️ **KISMEN DOĞRU** | Sıfır dolgu var (`:2944`) ama **canlıda erişilemez** (fail-closed) |
| 3.2 | AddressClaimEngine bağlı değil | HIGH | ✅ **DOĞRU** | `desktop_app.py`'de hiç referans yok |
| 3.3 | ISO-TP FF bellek DoS | HIGH | ❌ **YANLIŞ** | `isotp.py:617-623` `max_buffer_size` cap + OVERFLOW |
| 4.1 | `DbcBuilder` `is_extended` yok sayıyor | HIGH | ✅ **DOĞRU** | `dbc_builder.py:125` `> 0x7FF` |
| 4.2 | VIN regex case-sensitivity | HIGH | ✅ **DOĞRU** | `diagnostic_copilot.py:28` `re.IGNORECASE` yok |
| 4.3 | `route_frame` `_stats_lock` darboğazı | MEDIUM | ✅ **DOĞRU** | `router.py:164-165` her çerçevede kilit |
| 5.1 | Cloud VIN rıza parametresi eksik | MEDIUM | ✅ **DOĞRU** | `bridge.ts:535` `user_consented` yok |
| 5.2 | 1 MiB tavan vs 5 MB vaadi | MEDIUM | ✅ **DOĞRU** | `desktop_app.py:902,931` + `ReportsExportView.tsx:144` |
| 6.1 | Flash token firmware'e bağlı değil | CRITICAL | ❌ **YANLIŞ** | `desktop_app.py:2137-2142, 2810` `params_hash` firmware alanlarını kapsıyor |
| 6.2 | Telemetri döngüsü HOL bloklaması | HIGH | ✅ **DOĞRU (tasarım borcu)** | `desktop_app.py:3888-3894` senkron ingest |
| 6.3 | Google Fonts CDN bağımlılığı | LOW | ❌ **YANLIŞ (stale)** | `src/ui/frontend/index.html:8` "Air-gapped: Local & System Fonts" |

**İstatistik (rapor 3C/7H/5M/1L = 16 iddia diyor):**

| Verdikt | Adet |
|---|---|
| ✅ Tam doğru | 9 |
| ⚠️ Kısmen doğru (mekanizma var, etki abartılı) | 2 |
| ❌ Yanlış / stale | 5 |

**Sonuç:** Raporun "hiçbir hayali bulgu üretilmemiş, hepsi kaynak kod satırlarıyla kanıtlı" iddiası
**doğru değil**. 16 bulgunun 5'i yanlış, 2'si abartılı. Ancak düzeltilmesi gereken **gerçek ve
değerli** 9 açık tespit edilmiştir.

---

## 2. Bulgu-Bulgu Doğrulama Detayı

### ✅ 1.1 — E2E hattı canlı yola bağlı değil → **DOĞRU (ama "kör nokta" değil, dokümante edilmiş karar)**

`gateway.py:15-19`:
```
R2-N1 / AGENTS.md §2.7 (Wiring Gate): the E2E stage is WIRING-GATED and
experimental — the production composition root wires NO e2e_profiles, so the
stage passes frames through unstamped. Do NOT wire profiles into the live
TX path without addressing the P2-7/P2-8 remediation notes...
```
`desktop_app.py:1096-1110` composition root'ta `e2e_profiles=` parametresi **yok**.

**Verdict:** Raporun teknik gözlemi doğru. Fakat bu bir "ihmal" değil, `AGENTS.md §2.7` tarafından
**bilinçli olarak** yasaklanmış (P2-7/P2-8 remediation tamamlanmadan wire edilmemesi gereken)
bir durumdur. Raporun önerdiği düzeltmeyi (keyfi CAN ID'ler için profil ekleme) uygulamak
**AGENTS.md §2.7'yi İHLAL EDER** — yani raporun "çözümü" kurumsal kurallara aykırıdır.

**Doğru aksiyon:** Kod değişikliği DEĞİL. Mevcut durum korunmalı; uygulanacaksa P2-7/P2-8 notları
önce ele alınmalı.

---

### ❌ 1.2 — ISO-TP CF token boşluğu → **YANLIŞ**

Rapor: CF paketleri PCI `0x2_` taşıdığı için Stage-5'te kritik sayılmaz, token aranmadan sürülür.

**Gerçek kod:**
- `client.py:743-752`: CF döngüsü, FF ile **aynı** `is_critical_command` ve `confirmation_token`'ı
  her CF'ye geçiriyor.
- `gateway.py:1276-1296`: `if is_critical_command:` bloğunda, bir secret konfigüre edilmişse ve
  token `None` ise → `DualConfirmationRequiredError` (fail-closed). Yani boolean tek başına yetmez.
- `gateway.py:987-991` (`_frame_is_critical` docstring): CF'lerin kasten escalate EDİLMEDİĞİ
  belirtiliyor — bu doğru tasarım (CF SID taşımaz), ve **caller flag ile tighten edilebilir**.

Rapordaki "CF token'sız sürülür" senaryosu, ancak gateway API'sini **doğrudan** ve
`is_critical_command=False` ile çağıran bir bileşen için geçerli — ki bu, UDS client'ın yaptığı iş
değil. Rapordaki "stateful IsoTpTxLease" önerisi zaten mevcut davranışın (FF onaylanınca CF'ler
aynı token ile gider ve secret varsa zorunludur) gereksiz bir yeniden inşasıdır.

**Verdict: YANLIŞ.**

---

### ✅ 1.3 — `rebind_bus` bütçe kovalarını sıfırlamıyor → **DOĞRU**

`gateway.py:499-508`:
```python
self._tx_timestamps.clear()
self._rate_overload_streak = 0
self._whitelist_miss_streak = 0
self._tx_total_timestamps.clear()
self._total_overload_streak = 0
```
`self._budgets` (satır 321-322'de `BUDGETS`'ten kurulur) **reset edilmiyor**. Raporun değişken adı
`_category_budgets` yanlış (gerçek adı `_budgets`), ama mekanizma doğru: eskimiş bağlantıda tükenen
`protocol_burst` kovası yeni hatta da boş kalır → reconnect sonrası ilk kritik burst reddedilir.

**Verdict: DOĞRU** (değişken adı düzeltilerek).

---

### ⚠️ 2.1 — Replay → fiziksel CAN TX → **KISMEN DOĞRU (mekanizma doğru, severity bağlama bağlı)**

**Doğrulanan (kod):**
- `desktop_app.py:2691-2705` `start_replay`: replay callback → `ReplaySafetyFilter.filter_frame`
  → geçerse `self._ingest_live_frame(filtered)`.
- `desktop_app.py:3641-3651` `_ingest_live_frame`: `j1939_tp.handle_rx_frame(frame)` ve
  dönen `resp` için `self.gateway.validate_and_transmit(...)`.
- `replay_bus` (`:1190`) **aynı değil** `self.bus` (`:1039-1045`) → gateway TX **fiziksel** hatta.

**Çalıştırılabilir kanıt (Python repro):**
```
RTS arb=0x18ECF900 data=10140003eefe00f9      (J1939 TP.CM RTS → DA=0xF9)

--- A: ReplaySafetyFilter (default) ---
  is_frame_safe: (False, 'BLOCKED_TP_TUNNEL: 60665 (0x0ECF9)')
  filter_frame -> None                        ← FİLTRE BLOKLUYOR

--- B: filtre devre dışı iken TP motoru ---
  resp=0x1CEC00F9 data=110301fffffe00f9       ← GERÇEK TX ÜRETİYOR (CTS!)

--- C: analysis mode (block_transport_tunneling=False) ---
  is_frame_safe: (True, '')                   ← FİLTRE GEÇİRİYOR
```

**Değerlendirme:** Raporun iddia ettiği zincir **gerçek** (replay → `_ingest_live_frame` → TX),
ve bir RTS gerçekten CTS yanıtı doğuruyor. **ANCAK** rapor "replay dosyasında RTS varsa uygulama
CTS fırlatır!" derken, **default `ReplaySafetyFilter`'ın TP.CM/TP.DT PGN'lerini zaten blokladığını**
(`safety_filter.py:55-58, 327-330`) atlıyor. Filtre varsayılan olarak açık ve
`bridge` onu **sadece default'larla** kuruyor (`:2699`), kapatma yolu yok.

**Dolayısıyla:** Kritik TX yolu **şu an** pratikte korunuyor, ama **savunma-derinliği zayıf**:
tek bir `ReplaySafetyFilter(block_transport_tunneling=False)` çağrısı ya da gelecekteki bir
filtre bypass'ı doğrudan canlı TX'e yol açar. Raporun önerdiği `source == "replay"` kontrolü
**doğru ve değerli** bir "defense-in-depth" katmanıdır (replay çerçeveleri `parsers.py`'de
`source="replay"` taşıyor — doğrulandı).

**Verdict: KISMEN DOĞRU → savunma-derinliği düzeltmesi olarak uygulanmalı.**

---

### ✅ 2.2 — RP1210 wall-clock timestamp → **DOĞRU**

`rp1210/bus.py:343` ve `:382`: `timestamp_ns=time.time_ns()` — monotonik değil, NTP/leap-second
ile geri sıçrayabilir. Karşılaştırma: `pcan_kvaser.py:353` donanım timestamp'i
(`msg.timestamp`) tercih edip `time.time_ns()`'e yalnızca fallback olarak düşüyor. RP1210
paket decode'unda donanım zaman damgası yok.

**Verdict: DOĞRU.**

---

### ❌ 2.3 — ReplaySafetyFilter state sızması → **YANLIŞ**

Rapor: "start_replay global/kalıcı bir ReplaySafetyFilter kullanıyorsa `_iso_tp_streams`
temizlenmiyor."

**Gerçek kod:** `desktop_app.py:2698-2700`:
```python
self._replay_stop_event.clear()
safety_filter = ReplaySafetyFilter()   # ← HER SEANSTA YENİ ÖRNEK
self.replay_safety_filter = safety_filter
```
Her `start_replay` **taze** bir filtre kurar; `_iso_tp_pending` defteri de yeni örnekle boştur.
Raporun bahsettiği `_iso_tp_streams` adlı bir alan dahi yok (gerçek adı `_iso_tp_pending`).

**Verdict: YANLIŞ.**

---

### ⚠️ 3.1 — Flash UI/backend kopukluğu + sıfır dolgu → **KISMEN DOĞRU**

**Doğrulanan:**
- `EcuFlashingView.tsx:380-391`: frontend `data: hex`, `memoryAddress`, `blockSize`, `sizeBytes`,
  `expectedVin` gönderiyor — **ama `firmwareSignature`/`trustedPubkey` GÖNDERMİYOR**.
- `desktop_app.py:2737-2740` `_validate_flash_prerequisites`: signature/pubkey yoksa **senkron
  fail-closed** reddediyor (arm_tx'ten ÖNCE).
- `desktop_app.py:2944`: `else: payload_bytes = b"\x00" * int(config.get("sizeBytes", 1024))`

**Raporun İDDİASI:** "ECU'ya sıfır baytlı sahte imaj yazılır, ECU brick olur!"
**GERÇEK:** Bu yol **canlı modda erişilemez** — `_validate_flash_prerequisites` signature
olmadan `flash_start`'ı reddeder. Sıfır dolgu **yalnızca simulation branch'inde** (`:2839+`)
erişilebilir, ki orada hiçbir bus yazımı yok (sadece `time.sleep` ile progress simülasyonu).

**Dolayısıyla:** "ECU brick riski" **abartılıdır**. Gerçek kusur, raporun da belirttiği gibi
**UI ↔ backend signature sözleşme kopukluğudur** (canlı flashing UI'dan asla çalışmaz). Bu gerçek
bir HIGH'dur ama **CRITICAL brick değildir**.

**Verdict: KISMEN DOĞRU → sözleşme düzeltmesi (signature aktarımı) uygulanmalı; brick iddiası reddedilmeli.**

---

### ✅ 3.2 — AddressClaimEngine bağlı değil → **DOĞRU**

`src/ui/desktop_app.py` genelinde `AddressClaim`/`address_claim` için **tek bir referans yok**
(grep: 0 eşleşme). `AGENTS.md §2.7`'de bu bileşen "verified-but-unwired" olarak listeli.
Sabit SA 0xF9 kullanımı J1939-81 çakışma riski taşır.

**Verdict: DOĞRU** (ve AGENTS.md tarafından zaten bilinen/belgelenmiş bir latent defect).

---

### ❌ 3.3 — ISO-TP FF bellek DoS → **YANLIŞ**

Rapor: "`bytearray(total_length)` hemen ayrılır, 4 GB'a kadar OOM."

**Gerçek kod** (`isotp.py`):
- `:170` `max_buffer_size: int = 1048576` (1 MiB varsayılan tavan)
- `:617-623`: FF geldiğinde `if total_len > self.max_buffer_size:` → log + `FS_OVERFLOW`
  FlowControl yanıtı + session reddi (fail-closed).
- `:679` `received_bytes=bytearray(first_chunk)` — **gelen chunk kadar**, `total_length` kadar DEĞİL.
- Ayrıca `MAX_RX_SESSIONS=512` (`:107`) + stale reap (`:664`).
- Aynı cap TX tarafında da var (`:310-317` segment, `:1000-1007` async send).

**Verdict: YANLIŞ.** Raporun önerdiği `MAX_ISOTP_PAYLOAD_SIZE` **zaten mevcut**
(`max_buffer_size`, default 1 MiB).

---

### ✅ 4.1 — `DbcBuilder` `is_extended` yok sayıyor → **DOĞRU**

`dbc_builder.py:125`:
```python
is_extended = report.arbitration_id > 0x7FF
```
`report.is_extended` mevcut olmasına rağmen kullanılmıyor. 29-bit ID'si sayısal olarak ≤ 0x7FF
olan bir çerçeve (örn. Priority 0, PGN 0, SA 1 → `0x00000001`) DBC'ye yanlışlıkla 11-bit
standart olarak yazılır. Raporun düzeltmesi doğru.

**Verdict: DOĞRU.**

---

### ✅ 4.2 — VIN regex case-sensitivity → **DOĞRU**

`diagnostic_copilot.py:28`:
```python
_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")   # ← re.IGNORECASE YOK
```
Küçük harfli VIN maskelenmez. Raporun düzeltmesi doğru.

**Verdict: DOĞRU.**

---

### ✅ 4.3 — `route_frame` `_stats_lock` darboğazı → **DOĞRU**

`router.py:164-165`: her gelen çerçevede `with self._stats_lock: self._total_routed += 1`.
Yüksek yükte GIL çekişmesi. Raporun önerisi (yerel tampon / toplu flush) makul.

**Verdict: DOĞRU** (performans borcu; davranışsal değil).

---

### ✅ 5.1 — Cloud VIN rıza parametresi eksik → **DOĞRU**

- `bridge.ts:535`: `cloudUploadRawContent(filename, content, vehicleVin?)` — `user_consented` YOK.
- `ReportsExportView.tsx:89`: `DesktopBridge.cloudUploadRawContent(name, content, vinInput)`.
- Backend `desktop_app.py:923` `user_consented=False` default alıyor ve `:971` uploader'a geçiyor.
- `telemetry_uploader.py:136-139`: `if vehicle_vin is not None and not user_consented:` →
  `TELEMETRY_CONSENT_REQUIRED`.

Sonuç: UI'dan VIN'li yükleme **her zaman** fail-closed reddedilir; kullanıcı sebebini anlamaz.

**Verdict: DOĞRU.**

---

### ✅ 5.2 — 1 MiB tavan vs 5 MB vaadi → **DOĞRU**

- `desktop_app.py:902` `_RAW_UPLOAD_MAX_BYTES = 1 * 1024 * 1024`; `:931-933` oversize reddi.
- `ReportsExportView.tsx:144`: "Telemetri oturumu **5 MB parçalar halinde** bulut arşivine aktarılır".
- `telemetry_uploader.py` gerçek 5 MB chunked/resumable motoru **mevcut** ama `cloud_upload_raw_content`
  onu 1 MiB'lik ham string üzerinden besliyor ve öncesinde reddediyor.

**Verdict: DOĞRU.**

---

### ❌ 6.1 — Flash token firmware'e bağlı değil → **YANLIŞ**

Rapor: token yalnızca `action_type` + `action_id` hash'liyor; firmware/address/VIN kapsam dışı.

**Gerçek kod:**
- `desktop_app.py:2131-2148` `_compute_action_params_hash`: kritik anahtarlar arasında
  **`memoryAddress`, `blockSize`, `sizeBytes`, `fileName`, `expectedVin`, `expectedSerial`,
  `data`, `id`** var (satır 2137-2142).
- `:2167` challenge mint edilirken `params_hash = self._compute_action_params_hash(action)`.
- `:2806-2811` `flash_start`, token'ı `action_payload=config` ile doğruluyor.
- `:2231-2234` `_verify_and_consume_diagnostic_token`: hash uyuşmazsa →
  "Onay token'ı aksiyon parametreleri ile uyuşmuyor (parametreler değiştirilmiş)".

Yani token, firmware parametrelerine **zaten bağlıdır**. Rapordaki "başka firmware/hedef ile
çağrılabilir" saldırısı engellidir. (Nüans: `firmwareSignature`'ın kendisi kritik anahtar
listesinde **yok**, ama `data` + `sizeBytes` + `memoryAddress` + `expectedVin` var; bu da imajı
ve hedefi bağlar.)

**Verdict: YANLIŞ.**

---

### ✅ 6.2 — Telemetri döngüsü HOL bloklaması → **DOĞRU (tasarım borcu)**

`desktop_app.py:3888-3894`: `while drained < 200:` içinde `self._ingest_live_frame(frame)`
**senkron** çağrılıyor; bu fonksiyon router fan-out, J1939 TP, NMEA Fast Packet, discovery ve
rolling disk işlemlerini sırayla yapar. Yüksek yükte `recv()` kuyruğu boşaltılamaz.

**Verdict: DOĞRU** (mimari iyileştirme; davranışsal hata değil).

---

### ❌ 6.3 — Google Fonts CDN → **YANLIŞ (stale build artifact)**

Rapor: `src/ui/frontend/index.html:9-11` Google Fonts CDN içeriyor.

**Gerçek:** `src/ui/frontend/index.html:8`:
```html
<!-- Air-gapped: Local & System Fonts (system-ui, Segoe UI, sans-serif) -->
```
Kaynak dosyada **hiç CDN referansı yok** (grep: 0 eşleşme).

CDN yalnızca **eski build çıktısı** `src/ui/frontend/dist/index.html:9-11`'de duruyor — bu
derlenmiş bir artifact'tır, kaynak değil; bir sonraki `npm run build` ile temizlenir.

**Verdict: YANLIŞ.** (Rapor kaynağı değil, eski `dist/` çıktısını denetlemiş.)

---

## 3. Gerçek Düzeltme Planı (uygulanabilir bulgular)

Öncelik sırası: **güvenlik → doğruluk → performans → UX**.

### FAZ A — Güvenlik (savunma-derinliği)
| # | Dosya | Düzeltme | Test |
|---|---|---|---|
| A1 | `src/ui/desktop_app.py` `_ingest_live_frame` | Replay kaynaklı çerçeveler için (`frame.source == "replay"`) J1939 TP TX yanıtını **üretme/engelle** | Replay RTS → bus'a hiç TX yok |
| A2 | `src/protocols/j1939/transport.py` | (A1'e alternatif/kalıcı) `handle_rx_frame(..., allow_tx_response: bool)` benzeri; replay yolunda `False` | Replay'de `pending_tx_frames` boş |
| A3 | `src/safety/gateway.py` `rebind_bus` | `self._budgets` kovalarını `BUDGETS`'ten yeniden kur | rebind sonrası `protocol_burst` dolu |

### FAZ B — Doğruluk / Veri Bütünlüğü
| # | Dosya | Düzeltme | Test |
|---|---|---|---|
| B1 | `src/engine/discovery/dbc_builder.py:125` | `is_extended = getattr(report,"is_extended",False) or report.arbitration_id > 0x7FF` | 29-bit küçük ID → `is_extended_frame=True` |
| B2 | `src/engine/ai/diagnostic_copilot.py:28` | `re.IGNORECASE` ekle | küçük harfli VIN maskelenir |
| B3 | `src/hal/rp1210/bus.py:343,382` | RP1210 decode'a monotonik zaman kaynağı enjekte et (`ClockProvider`/`time.monotonic_ns()` + offset) | timestamp geri sıçramaz |

### FAZ C — UX / Sözleşme
| # | Dosya | Düzeltme | Test |
|---|---|---|---|
| C1 | `EcuFlashingView.tsx` + `desktop_app.py` | Frontend'den `firmwareSignature`+`trustedPubkey` (+`expectedVin`) gönder; backend'de sıfır-dolgu fallback'i **kaldır** (fail-closed) | Real modda imzasız flash reddi |
| C2 | `bridge.ts` + `Repo rtsExportView.tsx` + `desktop_app.py` | `cloudUploadRawContent(..., userConsented)` parametresi ekle + UI onay kutusu | VIN'li yükleme onayla başarılı, onaysız reddedilir |
| C3 | `desktop_app.py` `cloud_upload_raw_content` | >1 MiB için gerçek `upload_file()` yolunu kullan (5 MB chunked) | 5 MB dosya yüklenebilir |

### FAZ D — Performans
| # | Dosya | Düzeltme | Test |
|---|---|---|---|
| D1 | `src/engine/router.py:164` | Thread-local sayaç + toplu flush | davranış aynı, lock sayısı ↓ |
| D2 | `src/ui/desktop_app.py` `_telemetry_loop` | Ağır reassembly/decode'u worker'a taşı | throughput ↑ (opsiyonel, büyük refactor) |

### YAPILMAYACAKLAR (rapor yanlış / AGENTS.md yasaklıyor)
- ❌ 1.1 için `e2e_profiles` wire etme → `AGENTS.md §2.7` ihlali.
- ❌ 1.2 için `IsoTpTxLease` → gereksiz, mevcut davranış zaten fail-closed.
- ❌ 3.3 için `MAX_ISOTP_PAYLOAD_SIZE` → zaten `max_buffer_size` mevcut.
- ❌ 6.1 için token hash değişikliği → firmware parametreleri zaten bağlı.
- ❌ 6.3 için font gömme → kaynak zaten air-gapped; sadece stale `dist/` rebuild.

---

## 4. UYGULAMA DURUMU (tamamlandı — FAZ A + FAZ B)

Kullanıcı onayı ile **FAZ A (Güvenlik) + FAZ B (Doğruluk)** uygulandı.
FAZ C (UX/sözleşme) ve FAZ D (performans) kapsam dışı bırakıldı.

| Bulgu | Dosya | Değişiklik | Durum |
|---|---|---|---|
| 2.1 | `src/protocols/j1939/transport.py` | `handle_rx_frame(..., suppress_tx_responses=False)` eklendi; PDU gövdesi `_handle_rx_frame_pdu`'ya ayrıldı, replay'de tüm TX yanıtları (direkt + kuyruk) düşürülür | ✅ |
| 2.1 | `src/ui/desktop_app.py` | `_ingest_live_frame`: `source=="replay"` ise suppression; geriye-dönük uyum için `TypeError` fallback (caller-side `resp=None`) + kuyruk temizliği | ✅ |
| 1.3 | `src/safety/gateway.py` | `rebind_bus`: `self._budgets` kovaları `BUDGETS`'ten yeniden kurulur (`_lock` altında) | ✅ |
| 4.1 | `src/engine/discovery/dbc_builder.py` | `is_extended = getattr(report,"is_extended",False) or arbitration_id > 0x7FF` | ✅ |
| 4.2 | `src/engine/ai/diagnostic_copilot.py` | `_VIN_RE` → `re.IGNORECASE` | ✅ |
| 2.2 | `src/hal/rp1210/bus.py` | `_monotonic_timestamp_ns()` (wall-clock anchor + monotonic delta, non-decreasing); her iki decode çağrısı güncellendi | ✅ |

### Test Kanıtı
- **Yeni regresyon dosyası:** `tests/unit/test_ucanlab_review_verification_fixes.py` (11 test).
- **TDD red→green doğrulaması:** Her 5 düzeltme tek tek geri alındı; ilgili test **kırmızıya
  döndü**, geri konunca yeşile döndü. (Kanıt çıktısı: 5/5 "RED (good)".)
- **Regresyon:** `tests/unit` + `tests/safety` → **1997 passed**, 1 failed.
  Tek başarısız test `tests/unit/test_hwid.py::test_generate_hardware_fingerprint_structure`
  **önceden var olan ve ortam kaynaklıdır** (`INDETERMINATE_HARDWARE` — sandbox <2 donanım
  bileşeni sunuyor; `src/security/hwid/` değiştirilmedi).
- **Lint:** `ruff check` (6 kaynak + 1 test dosyası) → **All checks passed!**

### Not: Rapor Referans Kayması
Rapor `desktop_app.py:2330-2350` gibi yollar veriyor; gerçek konumlar farklı
(`src/ui/desktop_app.py:2691-2705`, `:3641-3666`). Bulgular doğrulanırken **gerçek kod**
esas alındı.
