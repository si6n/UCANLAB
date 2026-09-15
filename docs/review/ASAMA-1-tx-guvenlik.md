# ASAMA 1: TX Güvenlik Çekirdeği Raporu
Denetçi: Claude Fable 5.1 (GitLab Duo) | Tarih: 2026-09-15
Kapsam: `src/safety/gateway.py` (956 satır), `src/safety/estop.py` (795 satır), `src/safety/state_machine.py` (559 satır). Bağlam için okunan ek dosyalar: `src/safety/watchdog.py`, `src/safety/multiplexer.py`, `src/safety/secret_provider.py`, `src/hal/base.py`, `src/core/contracts/ports.py`, `src/ui/desktop_app.py` (composition root), `tests/unit/test_safety_gateway.py`, `conftest.py`.

> **Doğrulama yöntemi:** Yalnızca statik kaynak kod okuması (`main` dalı). Bu oturumda **hiçbir test çalıştırılmadı**, hiçbir bulgu çalışma zamanında yeniden üretilmedi. Her bulguda "Kanıt" satır referansı ile kod alıntısıdır; "Senaryo" kod okumasından türetilmiş bir tetiklenme yoludur, saha/laboratuvar doğrulaması değildir. Satır numaraları 1 tabanlıdır.
>
> **Severity ölçütü (bu rapor için):** KRİTİK = mevcut üretim wiring'inde doğrudan tetiklenebilir güvenlik bypass'ı. YÜKSEK = AGENTS.md §2 invariant ihlali veya fail-open tasarım, tetiklenmesi ek koşul/çağıran gerektirir. ORTA = savunma katmanı zayıflığı / TOCTOU / belgelenmiş-ama-bağlanmamış mitigasyon. DÜŞÜK = sınırlı etki, fail-closed yönde veya kod kalitesi. BİLGİ = not.

## 1. Yönetici Özeti

TX güvenlik çekirdeği genel olarak **fail-closed** bir tasarım disiplini sergiliyor: her politika aşaması istisna fırlatıyor (sessiz `return True`/fail-open bir `except` yolu **bulunmadı**), E-Stop TOCTOU penceresi için fence-generation + `tx_send_lock` mekanizması (CRITICAL-1) doğru kurgulanmış, kilit sıralaması tutarlı (`tx_send_lock → gateway._lock → estop._lock`; `estop.trigger()` callback'leri kilit dışında çalıştırıyor), HMAC karşılaştırmaları `compare_digest`, E-Stop reset nonce/epoch/TTL zinciri sağlam, `for_testing()` bypass'ı constructor imzasında değil. Rate limiter (`TxBudget`) kendi kilidine sahip ve counter taşması riski yok (float token, monotonik ns).

Buna karşın **5 YÜKSEK** seviye bulgu var; hiçbiri mevcut composition root'ta *doğrudan* istismar edilebilir bir KRİTİK bypass oluşturmuyor, ancak üçü AGENTS.md §2 invariant'larını kod düzeyinde ihlal ediyor:

1. **Hız interlock provenance (§2.6) gateway API'sinde kırılabilir (G-1):** sentetik hız çağrısı fiziksel freshness'ı korurken interlock'un okuduğu hız *değerini* eziyor; fiziksel 50 km/h + sentetik 0 km/h = kritik komut geçer. Mevcut desktop wiring'i bu yolu çağırmıyor (latent).
2. **Kritiklik sınıflandırması çağırana bırakılmış (G-2):** `is_critical_command` bayrağı gateway tarafından türetilmiyor; varsayılan `False` ile gönderilen herhangi bir whitelist'li kare (ör. 0x7E0 üzerinde UDS 0x11 ECUReset) Stage 4/5'e hiç uğramıyor.
3. **Üretim wiring'i kriptografik kapıları devre dışı bırakıyor (G-3, kapsam dışı dosya):** `TxSafetyGateway` `confirmation_secret` olmadan, `SafetySupervisor` `auth_secret` olmadan kuruluyor; T41/G-1/S-1 "fail-closed HMAC" düzeltmeleri üretimde pasif. Renderer hem `arm_tx`'i onaysız çağırabiliyor hem de kendi onay token'ını üretip tüketebiliyor.
4. **P1-1 mint/verify ayrımı nominal (E-1):** `EStopResetAuthority(estop)` varsayılan olarak `estop._secret_provider`'ı kullanıyor ve `EmergencyStopSystem.secret_provider` public; estop referansı tutan her kod token basabilir. Renderer'a bridge üzerinden açık değil (§2.5 korunuyor), ancak koddaki "cannot forge a reset token anymore" iddiası doğru değil.
5. **`trigger_fault` rate limiter FAULT geçişini sessizce düşürüyor (S-1):** güvenlik-iptal isteğinin atılması tasarım gereği fail-open; geçiş idempotent olduğu için limiter'ın güvenlik faydası yok.

ASIL-B/D duruşu: çekirdek mekanizmalar (fence, whitelist, E-Stop kriptografisi) ASIL-B hedefiyle uyumlu görünüyor; ancak (a) güvenlik kararının çağıran bayraklarına dayanması, (b) üretim wiring'inde kriptografik kapıların kapalı olması ve (c) belgelenmiş-ama-bağlanmamış abort-hook mitigasyonu (G-6) bağımsızlık/izlenebilirlik açısından ASIL-D iddiasını desteklemiyor. Öncelik: G-1 ve G-2 için gateway içi düzeltme + regresyon testi; G-3 için composition root'ta secret wiring.

## 2. Bulgu Tablosu

| # | dosya:satır | severity | sorun | senaryo | düzeltme |
|---|---|---|---|---|---|
| G-1 | `gateway.py:399-409`, `436`, `456`, `607` | YÜKSEK | Sentetik hız `_current_vehicle_speed_kmh`'yi ezer, fiziksel `_last_speed_update_ns` korunur → interlock sentetik değeri okur | Fiziksel CCVS 50 km/h (fresh) → `record_synthetic_speed(0.0)` → Stage 4 threshold `0.0 <= 0.5` geçer | Sentetik değeri ayrı alanda tut (`_display_speed_kmh`); interlock sadece `_physical_speed_kmh` okusun |
| G-2 | `gateway.py:460-467`, `594`, `632`; `ports.py` TxPort | YÜKSEK | Kritiklik çağıran bayrağı; gateway ID/servis tabanlı sınıflandırma yapmaz | `send_sync(frame)` varsayılanlarla 0x7E0 üzerinde `11 01` (ECUReset) → Stage 4/5 atlanır | Gateway içi `CriticalPolicy` (ID + ilk payload baytları: UDS 0x10/0x11/0x14/0x27/0x2E/0x31/0x34-0x37, J1939 DM11/DM3 vb.) ile `is_critical = caller_flag OR policy(frame)` |
| G-3 | `desktop_app.py:705`, `721-728`, `1244`; bridge `arm_tx`/`request_diagnostic_challenge`/`execute_diagnostic_action` | YÜKSEK (kapsam dışı wiring) | `confirmation_secret`/`auth_secret` verilmiyor → Stage 5 boolean'a, `arm_tx` legacy WARNING yoluna düşer; renderer onay token'ını kendisi üretip tüketebilir | WebView'de çalışan script: `arm_tx()` → `request_diagnostic_challenge(a)` → `execute_diagnostic_action(a, token)` → UDS ECUReset canlı hatta | Composition root'ta `SecretProvider`'dan `GATEWAY_CONFIRM_SECRET`/`ARM_AUTH_SECRET` türet; token üretimini OS-native onay diyaloğuna/ikinci kanala taşı; bridge `arm_tx`'e onay zorunluluğu |
| E-1 | `estop.py:743`, `285-287`, `760-770`; iddia `estop.py:336-341` | YÜKSEK | Reset authority, enforcement nesnesinin secret provider'ına düşer; provider public property | `EStopResetAuthority(app.estop).mint_reset_token()` → `estop.reset(tok)`; estop referansı yeterli | `secret_provider` parametresini zorunlu yap (fallback yok), `EmergencyStopSystem.secret_provider` property'sini kaldır/özelleştir, authority için ayrı key adı |
| S-1 | `state_machine.py:466-485` | YÜKSEK | `trigger_fault` 5/s limitini aşan çağrıyı **düşürür**; FAULT geçişi yapılmaz (fail-open) | 1 s içinde ≥5 fault + otomatik re-arm döngüsü; 6. gerçek fault (ör. watchdog, estop bağlı değilse) sessizce yutulur, TX ARMED kalır | Limiter yalnız log'u kıssın; `transition_to(FAULT)` her zaman çağrılsın (idempotent) |
| G-4 | `gateway.py:322-330` | ORTA | `_consumed_confirmations` bir `set`; `.pop()` rastgele eleman siler ("evict oldest" yorumu yanlış); süresi dolanlar temizlenmez | >1024 tüketim sonrası yeni tüketim, 1 s önce yakılmış TTL'li token'ı silebilir → aynı token tekrar geçer | `OrderedDict`/`deque` + expiry'ye göre prune; `popitem(last=False)` |
| G-5 | `gateway.py:666-667`, `697-787`, `467`, `723` | ORTA | Rate lane'i çağıranın string'i; yalnız `simulation` korumalı (M-23); global toplam limit yok; `inbound_triggered` çağıran kontrolünde | Herhangi TxPort çağıranı `protocol_burst`+`inbound_triggered=True` ile 255 burst / 100 msg/s ve E-Stop eskalasyonsuz; lane toplamı ≈215 msg/s sürekli | Global agregat pencere (bus starvation limiti), lane'i çağıran kimliğine bağla (protokol motoru handle'ı), `inbound_triggered`'ı sadece J1939/ISO-TP responder'a ver |
| G-6 | `gateway.py:835-849`; `estop.py:429-436`, `508-512` | ORTA | Fence-check ile `privileged_send` arası kalan pencere için belgelenen abort/flush hook mitigasyonu **bağlanmamış**: gateway hiç `register_abort_hook` çağırmıyor | E-Stop, fence check'ten sonra sürücü yazımı sırasında gelir → kare hatta çıkar; belgelenen HAL iptali gerçekleşmez | Gateway init'te sürücü `flush_tx_buffer`/abort varsa hook kaydet; yoksa kalan pencereyi zaman sınırıyla resmi "kabul edilen risk" olarak belgele |
| E-2 | `estop.py:675`, `289-297`, `627-635`; `secret_provider.py LinuxSecretBackend.get_secret` | ORTA | `_get_secret()` `estop._lock` altında dosya I/O + AES-GCM/DPAPI; raw-signature yolu ucuz ön kontrollerle adım 7'ye ulaşır | Hatalı/kötü niyetli reset denemeleri veya yavaş/asılı secret store → `is_engaged` ve `trigger()` aynı kilitte bekler; E-Stop devreye girme gecikir | Secret'ı init'te belleğe al ya da kilit dışında çek + kilit içinde yeniden doğrula |
| S-2 | `state_machine.py:367-404`, `434` | ORTA | `_verify_arm_token` `self._lock` dışında; `payload in deque` + `append` atomik değil (TOCTOU) | İki thread aynı token ile eşzamanlı `arm_tx` → her ikisi de geçer (tek-kullanım sözleşmesi kırılır) | Doğrulamayı `with self._lock` içine al |
| G-7 | `gateway.py:348-354`; `state_machine.py:114-119`; `conftest.py:24` | ORTA | Bypass'lar `UCANLAB_TEST_MODE` env ile açılır; `_whitelist_bypass_for_testing` korumasız plain attribute | Env miras alan bir launcher/CI/dev kabuğu üretim sürecine sızarsa whitelist bypass ve onaysız arm kullanılabilir hale gelir | Env yerine derleme zamanı flag'i / frozen build'de reddet; attribute'u `__slots__`/property ile kilitle |
| W-1 | `desktop_app.py:2036`; AGENTS.md §2.7 | ORTA (kapsam dışı) | `EcuFlashingEngine` canlı-bus dalında kuruluyor ve `execute_flash` çağrılıyor; AGENTS.md onu "NOT wired / LATENT defect lock" listesinde tutuyor | Wiring gate'in aşıldığı ya da AGENTS.md'nin güncel olmadığı; P2-7/P2-8 defect notları bu aşamada doğrulanmadı | Ya AGENTS.md'yi güncelle ya da `flash_start` gerçek dalını feature-flag arkasına al; AŞAMA 3 (flasher) denetiminde kapat |
| G-8 | `gateway.py:394-397` | DÜŞÜK | NaN/negatif **sentetik** hız, fiziksel freshness'ı sıfırlar | Simülatör bozuk değer → kritik komutlar DoS (fail-closed yön) | Sentetik kaynak için sadece display alanını NaN'la |
| G-9 | `gateway.py:558-565`, `106-110` | DÜŞÜK | Yorum "threshold currently 1" ↔ `WHITELIST_ESTOP_AFTER=5`; streak her isabette sıfırlanır | 4 miss + 1 hit deseni hiç latch etmez (kareler yine reddedilir) | Yorumu düzelt; streak yerine pencere içi miss oranı |
| G-10 | `gateway.py:581`, `597`, `612`, `729`; docstring `470-472` | DÜŞÜK | `estop.trigger()` gateway RLock **tutulurken** çağrılıyor; callback zinciri (supervisor → UI/watchdog, abort hook'lar) kilit altında | Fault fırtınasında diğer sender'lar ve `update_physical_speed` bloke → interlock feed'i yapay olarak bayatlar | Trigger kararını kilit içinde al, `estop.trigger()`'ı kilit dışında çalıştır (snapshot-then-release) |
| G-11 | `gateway.py:937-944`, `146`, `555-556` | DÜŞÜK | Async `send()` `inbound_triggered`'ı iletmez; `whitelist_ids` public mutable set; mask doğrulaması yok (`mask=0` her ID'yi geçirir) | Async responder RTS floodunda E-Stop'a eskalasyon; `gw.whitelist_ids.add()` ile runtime genişleme | Parametreyi ilet; `frozenset` + `rebind_whitelist()`; `value & ~mask == 0 and mask != 0` kontrolü |
| S-3 | `state_machine.py:93-106`, `260` | DÜŞÜK | Constructor `estop` tip kontrolsüz; `getattr(..., "is_engaged", False)` | `is_engaged` olmayan nesne verilirse E-Stop guard sessizce devre dışı | `bind_estop` ile aynı `hasattr` kontrolünü constructor'da yap |
| S-4 | `state_machine.py:379-387` | DÜŞÜK | Negatif/çok büyük `expiry` → `to_bytes` `OverflowError` (SafetyError değil); token operasyona/instance'a bağlı değil | `SafetyError` yakalayan çağıran beklenmedik istisna alır; arm token'ı activate için kullanılabilir | `OverflowError`'ı yakala; payload'a `operation` ve supervisor `id`/epoch ekle |
| E-3 | `estop.py:687-693` | DÜŞÜK | Backoff yalnız hata mesajını değiştirir; her deneme tam HMAC hesaplar | Online guessing pratikte 256-bit HMAC nedeniyle imkânsız; "rate-limit" kozmetik | Backoff aktifken ön kontrolden önce reddet (CPU) veya belgeyi düzelt |
| E-4 | `estop.py:781-795` | DÜŞÜK | `EStopResetAuthority.compute_reset_token(nonce=...)` ham nonce'u (hex string'in utf-8'i) imzalar; `reset()` bunu hiç doğrulamaz | Ölü/yanıltıcı API, fail-closed | Metodu kaldır veya `EmergencyStopSystem.compute_reset_token` ile aynı yapısal payload'ı imzala |
| E-5 | `estop.py:186-195` | BİLGİ | Secret kalıcılaştırma başarısızsa WARNING ile ephemeral fallback | Reset secret süreç-yerel olur; işlevsel, denetim izi zayıf | Fallback'te `protection_level` düşüşünü CRITICAL log + UI banner |

*Severity: KRİTİK | YÜKSEK | ORTA | DÜŞÜK | BİLGİ*

## 3. Detaylı Bulgular (Tüm KRİTİK ve YÜKSEK Seviyeler)

### [G-1] Sentetik hız feed'i interlock'un okuduğu değeri ezer (AGENTS.md §2.6 ihlali, latent fail-open)
- **Konum:** `src/safety/gateway.py:399-409` (`update_vehicle_speed`), `436` (`is_speed_fresh_and_safe`), `456` (`speed_interlock_state`), `607` (Stage 4 threshold).
- **Etki / Risk:** §2.6 "Synthetic/simulated speed feeds ... can never authorize critical commands" invariant'ı gateway API düzeyinde sağlanmıyor. Fiziksel feed taze ve araç hareketliyken tek bir sentetik `0.0` çağrısı Stage 4'ü geçirir; UDS 0x11/0x34 gibi kritik komutlar hareketli araca gider. ASIL-B için interlock'un provenance'ı **tek gerçek kaynak** olmalı.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # gateway.py:399-409
  if source != "physical":
      # ... "The interlock then keeps evaluating against the last physical sample"  <- YANLIŞ
      self._current_vehicle_speed_kmh = float(speed_kmh)   # değer ezilir
      return                                               # freshness korunur
  self._current_vehicle_speed_kmh = float(speed_kmh)
  self._last_speed_update_ns = time.monotonic_ns()
  # gateway.py:607
  if self._current_vehicle_speed_kmh > self.SPEED_NOISE_THRESHOLD_KMH:  # sentetik değeri okur
  ```
  Mevcut test `tests/unit/test_safety_gateway.py::test_safety_gateway_synthetic_speed_does_not_overwrite_physical_freshness` fiziksel 0.0 → sentetik 25.0 sonrası `_current_vehicle_speed_kmh == 25.0` ve `is_speed_fresh_and_safe() is False` olduğunu **doğruluyor**; yani sentetik değerin interlock değerlendirmesine girdiği testle sabit. Ters yön (fiziksel hareketli → sentetik 0) test edilmemiş.
- **İstismar / Tetiklenme Senaryosu:** (1) `_decode_j1939_signal` CCVS'ten `update_vehicle_speed(50.0, source="physical")` çağırır. (2) 1 s içinde herhangi bir kod `gateway.record_synthetic_speed(0.0)` çağırır. (3) `validate_and_transmit(frame, is_critical_command=True, user_confirmed=True)` → L596 freshness OK, L607 `0.0 > 0.5` False → geçer. **Not:** `src/ui/desktop_app.py` okumasında sentetik yolu çağıran kod bulunmadı (DEMO döngüsü P0-2 sonrası hız beslemiyor); bulgu latent'tir.
- **Düzeltme (Remediation):**
  ```python
  # ayrı alanlar
  self._physical_speed_kmh: float = float("nan")
  self._display_speed_kmh: float = float("nan")
  def update_vehicle_speed(self, speed_kmh, *, source="physical"):
      with self._lock:
          if source != "physical":
              self._display_speed_kmh = speed_kmh if math.isfinite(speed_kmh) and speed_kmh >= 0 else float("nan")
              return                       # fiziksel alanlara DOKUNMA
          if not math.isfinite(speed_kmh) or speed_kmh < 0.0:
              self._physical_speed_kmh = float("nan"); self._last_speed_update_ns = 0; return
          self._physical_speed_kmh = float(speed_kmh)
          self._last_speed_update_ns = time.monotonic_ns()
  # Stage 4 / is_speed_fresh_and_safe / speed_interlock_state -> yalnız _physical_speed_kmh
  ```
  Regresyon testi: fiziksel 50 → sentetik 0 → kritik komut `SpeedInterlockError` fırlatmalı.

### [G-2] Kritiklik sınıflandırması çağıranın bayrağına bırakılmış (Stage 4/5 opt-in)
- **Konum:** `src/safety/gateway.py:460-467` (imza, `is_critical_command: bool = False`), `594` ve `632` (`if is_critical_command:`); `src/core/contracts/ports.py` `TxPort.send_sync` varsayılanları.
- **Etki / Risk:** Hız interlock'u ve dual-confirmation yalnızca çağıran `is_critical_command=True` derse çalışır. Whitelist ID tabanlıdır (0x7DF, 0x7E0-0x7E7); aynı ID üzerinde ReadDID ile ECUReset ayrımı yapılmaz. Bu, "tek denetlenmiş choke-point" (§2.1) ilkesinin kritiklik boyutunu çağıran katmana devreder; hatalı/yeni bir protokol motoru interlock'u bilmeden atlar.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # gateway.py:594
  if is_critical_command:        # gateway frame içeriğine bakmaz
      ... speed interlock ...
  # gateway.py:632
  if is_critical_command:
      ... dual confirmation ...
  ```
  Gateway'de arbitration ID veya payload'a göre kritiklik türeten hiçbir tablo/politika yok. `desktop_app.py` UDS çağrılarında `user_confirmed=True` sabit geçiriliyor (`client.clear_dtc(group, user_confirmed=True)`, `client.ecu_reset(..., user_confirmed=True)`, DM11: `is_critical_command=True, user_confirmed=True`); `UdsClient`'ın her servis için bayrağı doğru set ettiği **bu aşamada doğrulanmadı** (Bölüm 4).
- **İstismar / Tetiklenme Senaryosu:** ARMED_TX durumunda herhangi bir `TxPort` tüketicisi `gateway.send_sync(CanFrame(0x7E0, b"\x02\x11\x01"))` çağırır → Stage 1-3 geçer, Stage 4/5 hiç değerlendirilmez, Stage 6 default lane → ECUReset hatta çıkar; araç hızı hiç kontrol edilmez.
- **Düzeltme (Remediation):**
  ```python
  CRITICAL_UDS_SIDS = frozenset({0x10, 0x11, 0x14, 0x27, 0x28, 0x2E, 0x2F, 0x31, 0x34, 0x35, 0x36, 0x37, 0x85})
  def _frame_is_critical(self, frame: CanFrame) -> bool:
      if frame.arbitration_id in self.whitelist_ids and len(frame.data) >= 2:
          pci = frame.data[0] >> 4
          sid = frame.data[1] if pci in (0x0, 0x1) else None   # SF / FF
          if sid in CRITICAL_UDS_SIDS: return True
      pgn = (frame.arbitration_id >> 8) & 0x3FFFF
      return frame.is_extended and pgn in {65235, 65228}         # DM11, DM3
  # validate_and_transmit içinde:
  is_critical_command = is_critical_command or self._frame_is_critical(frame)
  ```
  Çağıran bayrağı yalnızca sıkılaştırabilir, gevşetemez.

### [G-3] Üretim composition root'u kriptografik kapıları kapatıyor; renderer onayı kendi kendine üretebiliyor (kapsam dışı dosya, bağlantı bulgusu)
- **Konum:** `src/ui/desktop_app.py:705` (`SafetySupervisor(initial_state=..., estop=self.estop)` — `auth_secret` yok), `721-728` (`TxSafetyGateway(...)` — `confirmation_secret` yok), `1244` (`self.supervisor.arm_tx(reason=reason)` — `auth_token` yok); `DesktopApiBridge.arm_tx`, `request_diagnostic_challenge`, `execute_diagnostic_action`.
- **Etki / Risk:** `gateway.py:633-651` ve `state_machine.py:415-438`'deki T41/G-1/S-1 fail-closed HMAC yolları yalnızca secret verildiğinde aktif; üretimde her ikisi de `None` → Stage 5 salt `user_confirmed` boolean'ına, `arm_tx` "legacy unauthenticated path" WARNING'ine düşer. Bridge tarafında onay token'ı in-memory rastgele string (`secrets.token_hex(16)`), üreten ve tüketen aynı renderer. Bu, REVIEW C-1'de kaldırılan `estop_reset_local` ile aynı "mint+consume tek taraf" deseni; §2.5 E-Stop için korunmuş, ancak kritik komut onayı için korunmamış.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # desktop_app.py:705
  self.supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP, estop=self.estop)
  # desktop_app.py:721-728
  self.gateway = TxSafetyGateway(bus=self.bus, estop=self.estop, supervisor=self.supervisor,
                                 watchdog=self.watchdog, whitelist_ids=_diag_ids, whitelist_masks=...)
  # state_machine.py:435-438 (bu wiring'de her arm'da çalışır)
  logger.warning("%s without auth_secret configured (legacy unauthenticated path)", operation)
  ```
- **İstismar / Tetiklenme Senaryosu:** WebView içinde çalışan script (XSS/devtools): (1) `pywebview.api.arm_tx()` → hız interlock'u "ok" ve simülatör kapalıysa ARMED_TX. (2) `request_diagnostic_challenge({action_type:"uds_ecu_reset"})` → token. (3) `execute_diagnostic_action({action_type:"uds_ecu_reset"}, token)` → `client.ecu_reset(..., user_confirmed=True)` → canlı hatta ECUReset. Operatörün fiziksel ikinci onayı hiçbir aşamada gerekmedi. Kalan koruma: whitelist, fiziksel hız interlock'u, E-Stop.
- **Düzeltme (Remediation):**
  ```python
  sp = get_default_secret_provider()
  for k in ("GATEWAY_CONFIRM_SECRET", "ARM_AUTH_SECRET"):
      if not sp.has_secret(k): sp.store_secret(k, os.urandom(32))
  self.supervisor = SafetySupervisor(initial_state=SafetyState.STARTUP, estop=self.estop,
                                     auth_secret=sp.get_secret("ARM_AUTH_SECRET"))
  self.gateway = TxSafetyGateway(..., confirmation_secret=sp.get_secret("GATEWAY_CONFIRM_SECRET"))
  ```
  Token üretimi (`issue_arm_token`/`issue_confirmation_token`) bridge'e **açılmamalı**; OS-native onay diyaloğu (pywebview `create_confirmation_dialog`) veya out-of-band kanal içinde çağrılmalı. `DesktopApiBridge.arm_tx` aynı onayı zorunlu kılmalı.

### [E-1] P1-1 mint/verify ayrımı yalnızca konvansiyonel; estop referansı token basmaya yeter
- **Konum:** `src/safety/estop.py:743` (`self._secret_provider = secret_provider or estop._secret_provider`), `285-287` (public `secret_provider` property), `760-770` (`mint_reset_token`), iddia: `336-341` (`create_reset_token` docstring).
- **Etki / Risk:** ISO 26262 bağımsızlık gerekçesiyle eklenen ayrım (enforcement nesnesi basamaz), `EStopResetAuthority`'nin aynı provider/aynı key'e otomatik düşmesiyle anlamsızlaşır. `_get_secret` "private — no public accessor by design" (`estop.py:290`) derken `secret_provider` property aynı provider'ı public verir. §2.5 (renderer) korunuyor çünkü pywebview yalnız bridge metodlarını açar; ancak süreç içi her modül (flasher, retry döngüsü, plugin) E-Stop'u çözebilir.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # estop.py:743
  self._secret_provider = secret_provider or estop._secret_provider
  # estop.py:285-287
  @property
  def secret_provider(self) -> SecretProvider:
      return self._secret_provider
  # estop.py:336-341 (docstring) — "Any code holding a plain EmergencyStopSystem reference ... cannot forge a reset token anymore"  <- doğru değil
  ```
- **İstismar / Tetiklenme Senaryosu:** Herhangi bir süreç-içi kod: `EStopResetAuthority(app.estop).mint_reset_token()` → `app.estop.reset(token)`; ya da `hmac.new(app.estop.secret_provider.get_secret("ESTOP_HMAC_SECRET"), challenge.serialize_for_signature(), sha256)`. `desktop_app.py:698-703` yorumu bu riski (in-process authority) kabul ediyor ama sınıf düzeyinde kapı açık.
- **Düzeltme (Remediation):**
  ```python
  class EStopResetAuthority:
      def __init__(self, estop, secret_provider: SecretProvider, key_name: str = "ESTOP_RESET_AUTHORITY_SECRET"):
          if secret_provider is estop._secret_provider:
              raise SafetyError("authority must not share the enforcement provider", code="ESTOP_AUTHORITY_NOT_INDEPENDENT")
          ...
  # EmergencyStopSystem: `secret_provider` property'sini kaldır; doğrulama için ayrı verify key'i tut
  ```
  Ayrıca `reset()` imza doğrulamasını authority key'i ile yap (enforcement nesnesi imzalama secret'ını hiç tutmasın).

### [S-1] `trigger_fault` rate limiter güvenlik-iptal isteğini sessizce düşürür
- **Konum:** `src/safety/state_machine.py:466-485` (`trigger_fault`), `244` (`FAULT_RATE_LIMIT_PER_SEC = 5`).
- **Etki / Risk:** FAULT'a geçiş TX yetkisini iptal eden tek supervisor yoludur. `transition_to` aynı duruma geçişte no-op olduğundan (L250-251) fault fırtınası zaten ucuzdur; limiter'ın tek etkisi **gerçek** bir fault isteğini atmaktır → fail-open tasarım. `gateway._on_estop_triggered` (gateway.py:360) ve watchdog (`watchdog.py`) bu API'ye bağımlı; watchdog `estop` verilmeden kurulursa ikinci yol yok.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # state_machine.py:476-485
  if len(self._fault_timestamps_ns) >= self.FAULT_RATE_LIMIT_PER_SEC:
      logger.warning("trigger_fault rate-limited (fault storm suppressed)", ...)
      return                      # FAULT geçişi YAPILMAZ
  self._fault_timestamps_ns.append(now_ns)
  ...
  self.transition_to(SafetyState.FAULT, reason=reason)
  ```
- **İstismar / Tetiklenme Senaryosu (ön koşullar dar):** (1) 1 s içinde ≥5 `trigger_fault` çağrısı (ör. UI/otomasyon FAULT→PASSIVE→ARMED_TX döngüsü ile birlikte hata tekrarları). (2) Sistem yeniden ARMED_TX. (3) Aynı saniyede gerçek watchdog/ hardware fault → `trigger_fault` düşer, TX ARMED kalır. E-Stop'un ayrıca tetiklendiği yollarda gateway Stage 2 yine bloke eder; **sadece supervisor'a dayanan** fault kaynakları için açık kalır. Çalışma zamanında yeniden üretilmedi.
- **Düzeltme (Remediation):**
  ```python
  def trigger_fault(self, reason="Safety fault detected"):
      now_ns = time.monotonic_ns()
      with self._lock:
          while self._fault_timestamps_ns and (now_ns - self._fault_timestamps_ns[0]) >= 1_000_000_000:
              self._fault_timestamps_ns.popleft()
          suppressed_log = len(self._fault_timestamps_ns) >= self.FAULT_RATE_LIMIT_PER_SEC
          self._fault_timestamps_ns.append(now_ns)
      if not suppressed_log:
          logger.warning("Safety FAULT triggered", extra={"reason": _sanitize_reason(reason)})
      self.transition_to(SafetyState.FAULT, reason=reason)   # her zaman
  ```

## 4. Doğrulanamayan / Dış Bağımlılıklar

- **Test çalıştırılmadı.** `tests/unit/test_safety_gateway.py`, `test_safety_estop.py`, `test_estop.py`, `test_safety_state_machine.py`, `test_t41_arm_tx_auth.py` yalnızca okundu/varlığı görüldü; geçtikleri veya bulguları yakaladıkları iddia edilmiyor. G-1, G-4, S-1, S-2 için önerilen regresyon testleri henüz yazılmadı.
- **`UdsClient` / `EcuFlashingEngine` bayrak disiplini:** hangi UDS servislerinde `is_critical_command=True` geçirildiği (`src/protocols/uds/client.py`, `flasher.py`) incelenmedi; G-2'nin fiili etkisi buna bağlı. AŞAMA 3 kapsamı.
- **HAL `privileged_send` override'ları:** `src/hal/drivers/pcan_kvaser.py`, `src/hal/rp1210/**`, `src/hal/virtual.py` incelenmedi; `AbstractBus.privileged_send` (`hal/base.py`) varsayılanı `send`'e delege ediyor, sürücülerin "gateway-exempt low-level access" için override edip etmediği ve bir TX flush/abort API'si sunup sunmadığı (G-6 için) bilinmiyor. `VirtualBus.interface` attribute'unun varlığı (gateway.py:684 M-23 kontrolü) doğrulanmadı.
- **Secret provider gecikmesi (E-2):** Windows DPAPI `CryptUnprotectData` ve Linux AES-GCM dosya okuma süreleri ölçülmedi; `trigger()` üzerindeki gerçek gecikme etkisi nicel olarak bilinmiyor.
- **Frontend:** `src/ui/frontend/**` içinde `arm_tx`, `request_diagnostic_challenge`, `execute_diagnostic_action` çağrı sırası ve herhangi bir ek onay diyaloğu olup olmadığı incelenmedi (G-3 renderer senaryosu bridge yüzeyine dayanır).
- **Watchdog (`watchdog.py`)** kapsam dışı olduğu için yalnızca çağrı ilişkileri için okundu; `heartbeat(caller_token=None)` legacy yolunun bridge `heartbeat()` tarafından token'sız çağrıldığı görüldü (`desktop_app.py` `DesktopApiBridge.heartbeat`) — ayrı aşamada ele alınmalı.
- **AGENTS.md §2.7 ↔ `flash_start` çelişkisi (W-1):** hangisinin güncel olduğu bu aşamada belirlenemedi.
- **Kilit sıralaması:** `tx_send_lock → gateway._lock → estop._lock` ve `estop.trigger()`'ın callback'leri kilit dışında çalıştırdığı kod okumasıyla tutarlı bulundu; UI/`desktop_app` callback'lerinin (`_on_upload_progress`, `_push_*`) supervisor kilidi altında çağrılmadığı varsayımı `_dispatch_callbacks`'in snapshot-then-release yapısına dayanır, `evaluate_js` yeniden giriş davranışı test edilmedi.
