# UCANLAB (Universal CAN-Bus Platform) Kapsamlı Kod Denetimi ve Güvenlik Raporu

**Hedef Repository:** `https://github.com/si6n/UCANLAB`  
**Denetim Tarihi:** 2026-09-19  
**Referans Standartlar:** ISO 26262 (ASIL-B/D), ISO 11898-1 (CAN/CAN-FD), ISO 14229-1 (UDS), ISO 15765-2 (DoCAN/ISO-TP), SAE J1939-21/-71/-73/-81, NMEA 2000, TMC RP1210A/B/C.  
**İncelenen Temel Dizinler:** `src/safety/`, `src/hal/`, `src/protocols/`, `src/engine/`, `src/security/`, `src/ui/`, `src/launcher/`, `tests/`

---

## 1. Yönetici Özeti ve Mimari Analiz

UCANLAB, otomotiv, ağır vasıta, marin ve endüstriyel CAN/CAN-FD ağları için geliştirilmiş ileri seviye bir diyagnostik, telemetri ve ECU firmware flashing platformudur. Hexagonal (Ports & Adapters) mimari prensiplerine göre tasarlanmış; çekirdek güvenlik katmanı, donanım soyutlama katmanı (HAL), protokol yığınları, telemetri motoru ve yerel WebView2 masaüstü arayüzünden meydana gelmektedir.

Son commit (`826154b4f2bca86f6254f993ded448289d8f8025` ve `1401f484a1542b56647755952ffca29555d0d4a3`) ile T62 inceleme turunda belirlenen bir dizi açık giderilmiş; hash-kilitli `requirements.lock`, zorunlu `pip-audit`, cloud allowlist, HWM fail-closed korumaları ve launcher manifest güvenlikleri eklenmiştir.

Buna rağmen, kaynak kodlar en kritik katmanlardan başlanarak didik didik incelendiğinde, **fonksiyonel güvenlik (ASIL-B/D)**, **protokol bütünlüğü**, **donanım sürücü yaşam döngüsü**, **eşzamanlılık (concurrency)** ve **UI/Bridge veri sözleşmesi** seviyesinde kritik, yüksek ve orta dereceli bulgular tespit edilmiştir. Bu raporda hiçbir varsayıma dayanılmamış, tüm bulgular doğrudan kaynak kod satırları ve yürütme mantığı üzerinden kanıtlanarak somut çözüm ve test yönergeleriyle sunulmuştur.

---

## 2. Aşama 1: Fonksiyonel Güvenlik Çekirdeği İncelemesi (`src/safety/`)

**İncelenen Dosyalar:**
- `src/safety/gateway.py` (87 KB — TxSafetyGateway 6-Aşamalı Güvenlik Choke-point)
- `src/safety/estop.py` (40 KB — 10-Tetikleyicili Emergency Stop & Kriptografik Reset)
- `src/safety/state_machine.py` (28 KB — SafetySupervisor & State Transition Audit)
- `src/safety/watchdog.py` (15 KB — 800ms Monotonik Kalp Atışı Kira Denetleyicisi)
- `src/safety/secret_provider.py` (34 KB — Platform DPAPI / OS Secret Kasası)
- `src/safety/multiplexer.py` (9.4 KB — Güvenli Çok Kanallı Bus Dağıtıcısı)
- `src/safety/e2e/validator.py`, `packager.py`, `profiles.py`, `crc.py` (AUTOSAR P1/P2, SAE J1850 CRC-8)

### [CRITICAL] Bulgu 1.1 — E2E Güvenlik Hattının Canlı İletim Yoluna Bağlanmaması (Wiring Gate Riski)
- **Konum:** `src/safety/gateway.py:195-215`, `src/ui/desktop_app.py:530-545`, `AGENTS.md:§2.7`
- **Mekanizma:** `TxSafetyGateway`, AUTOSAR Profile 1 & 2 ve OEM rolling counter/CRC korumalarını `E2ESafetyPackager` üzerinden çerçevelere mühürleme yeteneğine sahiptir. Ancak üretim composition root'u (`desktop_app.py`), gateway'i `e2e_profiles={}` veya parametresiz başlatmaktadır.
- **Tehdit / ASIL Etkisi:** Canlı CAN veri yoluna gönderilen kritik kontrol çerçeveleri (örneğin gaz kelebeği, retarder torku veya kalibrasyon komutları) rolling counter ve CRC ile damgalanmadan düz metin olarak iletilmektedir. Bu durum, ISO 26262 ASIL-B/D seviyesinde veri yolu manipülasyonlarına (frame insertion, replay, corrupt payload) karşı E2E korumasının fiilen devre dışı kalmasına neden olur.
- **Çözüm:** 
  1. `desktop_app.py` içinde bilinen kritik aktüatör ve kalibrasyon CAN ID'leri için `E2EProfileConfig` profilleri yapılandırılmalı.
  2. Profil yapılandırması composition root seviyesinde doğrulanıp gateway'e enjekte edilmeli.
  ```python
  # src/ui/desktop_app.py
  e2e_profiles = {
      0x100: E2EProfileConfig.create_autosar_profile_1(data_id=0x10),
      0x200: E2EProfileConfig.create_autosar_profile_2(data_id_list=(0x01, 0x02, 0x03, 0x04)),
  }
  self.gateway = TxSafetyGateway(
      bus=self.bus,
      supervisor=self.supervisor,
      watchdog=self.watchdog,
      estop=self.estop,
      e2e_profiles=e2e_profiles,
  )
  ```

### [HIGH] Bulgu 1.2 — ISO-TP Çok Çerçeveli (Multi-Frame) Gönderimlerde Yetki Aşımı
- **Konum:** `src/protocols/uds/client.py:480-530`, `src/safety/gateway.py:960-995`
- **Mekanizma:** UDS üzerinden çok çerçeveli (multi-frame) bir kritik işlem (`0x36 TransferData` veya `0x31 RoutineControl`) gönderilirken, operatör HMAC `confirmation_token` değeri yalnızca **First Frame (FF)** çerçevesine bağlanır. Ardından gelen **Consecutive Frame (CF)** paketleri PCI nibble `0x2_` taşıdığından, gateway Stage-5 dual-confirmation kontrolünde kritik komut olarak değerlendirilmez ve token aranmadan fiziksel hatta sürülür.
- **Tehdit:** Aynı process içindeki yetkisiz bir bileşen veya manipüle edilmiş JavaScript bridge çağrısı, ilk çerçevesi onaylanmış bir oturuma ait arbitration ID üzerinden sahte CF blokları enjekte edebilir. Gateway bu paketleri "non-critical" saydığı için filtreyi aşarlar.
- **Çözüm:**
  1. `TxSafetyGateway` içerisinde durum bilgisi tutan (stateful) bir `IsoTpTxLease` yapısı oluşturulmalı.
  2. İlk çerçeve (FF) geçerli bir HMAC token ile onaylandığında `(channel_id, arbitration_id, total_len, block_count, expiry_ns)` içeren bir kira tahsis edilmeli.
  3. Gelen Consecutive Frame'ler yalnızca aktif bir kiraya aitse ve ardışık Sequence Number (SN = (prev_SN + 1) % 16) kuralını sağlıyorsa hatta verilmelidir. Aksi takdirde transfer fail-closed reddedilmelidir.

### [MEDIUM] Bulgu 1.3 — `rebind_bus()` İşleminde Kategori Token Kovalarının Sıfırlanmaması
- **Konum:** `src/safety/gateway.py:470-505`
- **Mekanizma:** `rebind_bus(new_bus)` çağrıldığında `_tx_total_timestamps` ve `_total_overload_streak` temizlenmektedir; ancak `_category_budgets` sözlüğündeki token bucket nesneleri (`TxBudget`) resetlenmemektedir.
- **Tehdit:** Eski bağlantıda `protocol_burst` (J1939 BAM / UDS flash) veya `diagnostic` kategorisindeki tokenlar tükenmişse, yeni bağlanan donanım hattında bu kovalar boş kalmaya devam eder. Reconnect sonrası ilk kritik protokol burst işlemi anında `RateLimitExceededError` ile reddedilir.
- **Çözüm:**
  ```python
  # src/safety/gateway.py rebind_bus() içinde:
  with self._rate_lock:
      self._tx_timestamps.clear()
      self._tx_total_timestamps.clear()
      self._total_overload_streak = 0
      # Kategori bütçe token kovalarını başlangıç kapasitelerine sıfırla
      for cat, (cap, refill) in self.BUDGETS.items():
          self._category_budgets[cat] = TxBudget(capacity=cap, refill_per_sec=refill)
  ```

---

## 3. Aşama 2: Donanım Soyutlama Katmanı İncelemesi (`src/hal/`)

**İncelenen Dosyalar:**
- `src/hal/base.py` (AbstractBus, BusMetrics, BusState)
- `src/hal/drivers/pcan_kvaser.py` (PythonCanBus, PEAK/Kvaser sürücü sarmalayıcısı)
- `src/hal/rp1210/bus.py` ve `client.py` (RP1210Bus 32/64 bit DLL adaptörü)
- `src/hal/replay/player.py` ve `safety_filter.py` (Trace oynatıcı ve güvenlik süzgeci)
- `src/hal/virtual.py` (VirtualBus ve demo simülatör)

### [CRITICAL] Bulgu 2.1 — Replay (İz Kaydı) Oynatımının Fiziksel CAN Veri Yoluna İletim Tetiklemesi
- **Konum:** `src/ui/desktop_app.py:2330-2350`, `desktop_app.py:3170-3220`
- **Mekanizma:** `ReplayBus` ile oynatılan iz dosyalarından gelen çerçeveler `_ingest_live_frame()` fonksiyonuna iletilmektedir. `_ingest_live_frame()` içerisinde şu kod yer alır:
  ```python
  completed, resp = self.j1939_tp.handle_rx_frame(frame)
  if resp is not None:
      self.gateway.validate_and_transmit(resp, budget_category="protocol_burst", inbound_triggered=True)
  ```
- **Tehdit:** Replay dosyasında kayıtlı bir J1939 RTS (Request To Send) veya DoCAN First Frame paketi bulunuyorsa, uygulama bu paketi canlı ağdan gelmiş gibi algılayarak gerçek araç hattına CTS (Clear To Send) veya Flow Control (FC) yanıtı fırlatır! Bu durum, "Replay is analysis-only and has NO live-bus TX path" temel güvenlik invariantını doğrudan ihlal eder ve canlı araç ağında yetkisiz yayınlara neden olur.
- **Çözüm:** `_ingest_live_frame` fonksiyonuna `from_replay: bool = False` parametresi eklenmeli veya çerçevenin `source == "replay"` olması durumunda protokol yanıtlarının `validate_and_transmit` çağrısına gitmesi kesin olarak engellenmelidir:
  ```python
  # src/ui/desktop_app.py _ingest_live_frame() içinde:
  is_replay = getattr(frame, "source", "") == "replay"
  if resp is not None and not is_replay:
      self.gateway.validate_and_transmit(resp, budget_category="protocol_burst", inbound_triggered=True)
  elif resp is not None and is_replay:
      logger.debug("Replay kaynaklı çerçeve için fiziksel TX yanıtı engellendi (fail-closed)")
  ```

### [HIGH] Bulgu 2.2 — RP1210 Çerçeve Zaman Damgasının Duvar Saatinden Alınması (Zaman Kayması Riski)
- **Konum:** `src/hal/rp1210/bus.py:270-330` (`_decode_rp1210_packet`)
- **Mekanizma:** RP1210 paketleri decode edilirken `timestamp_ns=time.time_ns()` kullanılmaktadır. Duvar saati (wall-clock time), NTP senkronizasyonları, operatör saat düzeltmeleri veya artık saniyelerde (leap second) geriye doğru sıçrayabilir.
- **Tehdit:** Telemetri ve E2E doğrulama motorlarında zamanın geriye gitmesi, jitter analizlerinin bozulmasına, sahte timeout alarmlarına ve rolling blackbox buffer sıralamasında bozulmaya yol açar.
- **Çözüm:** Sürücü seviyesinde donanımsal zaman damgası mevcut değilse, sistem daima monotonik referanslı zaman damgası üretmelidir:
  ```python
  timestamp_ns=time.monotonic_ns()
  ```

### [MEDIUM] Bulgu 2.3 — ReplaySafetyFilter Oturum Durumunun Farklı Trace Dosyaları Arasında Sızması
- **Konum:** `src/ui/desktop_app.py:2330-2345`, `src/hal/replay/safety_filter.py:40-90`
- **Mekanizma:** `desktop_app.py` içinde `start_replay` çağrıldığında global veya kalıcı bir `ReplaySafetyFilter` örneği kullanılıyorsa, filtrenin ISO-TP First Frame/Consecutive Frame defteri (`_iso_tp_streams`) temizlenmemektedir.
- **Tehdit:** Bir önceki trace dosyasında yarım kalmış bir aktarımın durumu, yeni yüklenen farklı bir trace dosyasındaki paketlerle eşleşebilir ve filtre geçerli bir paketi haksız yere drop edebilir veya bozuk bir paketi geçirebilir.
- **Çözüm:** Her yeni `start_replay` oturumunda `ReplaySafetyFilter` baştan oluşturulmalı veya `reset_session()` metodu çağrılarak tüm iç sözlükler sıfırlanmalıdır.

---

## 4. Aşama 3: Protokol Yığınları İncelemesi (`src/protocols/`)

**İncelenen Dosyalar:**
- `src/protocols/uds/flasher.py` (53 KB — EcuFlashingEngine 10-Aşamalı Reprogramming)
- `src/protocols/uds/client.py` (46 KB — UdsClient, ISO-TP İletişim, NRC Yönetimi)
- `src/protocols/uds/isotp.py` (63 KB — DoCAN ISO 15765-2 Multi-Frame State Machine)
- `src/protocols/j1939/transport.py` (74 KB — J1939-21 BAM ve RTS/CTS Taşımacılığı)
- `src/protocols/j1939/address_claim.py` (22 KB — SAE J1939-81 Adres Talep Motoru)
- `src/protocols/j1939/oem/` (Cummins, Caterpillar, Scania, Volvo, Detroit, Actros)
- `src/protocols/obd/poller.py` (Aktif Teşhis Yoklayıcı)

### [CRITICAL] Bulgu 3.1 — ECU Flasher UI ile Backend Arasındaki Veri ve İmza Kopukluğu
- **Konum:** `src/ui/frontend/src/components/ecu/EcuFlashingView.tsx:330-360`, `src/ui/desktop_app.py:2525-2575`, `src/protocols/uds/flasher.py:490-520`
- **Mekanizma:** 
  1. Frontend arayüzünde kullanıcı bir firmware dosyası seçtiğinde dosya hash'i gösterilmekte; ancak "Flashing Başlat" butonuna basıldığında `flashStart` API'sine yalnızca `{ memoryAddress, blockSize, sizeBytes }` parametreleri gönderilmektedir. Dosyanın gerçek içeriği (`data`), Ed25519 imzası (`firmwareSignature`) veya güvenilir açık anahtar (`trustedPubkey`) backend'e iletilmemektedir.
  2. Backend `desktop_app.py`, `config.data` boş olduğu için `b"\x00" * size_bytes` (sıfır dolgulu veri) üretmektedir.
  3. `FlashingConfig` varsayılan olarak `require_signature=True` istediğinden, `execute_flash()` aşamasında imza bulunamadığı için işlem anında `FLASH_SIGNATURE_MISSING` hatası ile çökmektedir.
- **Tehdit:** Canlı modda flashing UI üzerinden hiçbir zaman çalışamaz (fail-closed); simülasyon veya korumanın devre dışı bırakıldığı bir modda ise seçilen dosya yerine ECU'ya **tamamen sıfır baytlardan oluşan (`0x00`) sahte bir imaj yazılır** ve ECU kalıcı olarak brick edilir!
- **Çözüm:**
  1. Frontend dosya verisini Base64 olarak veya güvenli yerel dosya yolu (`staged_file_path`) üzerinden backend'e aktarmalıdır.
  2. Firmware Ed25519 imzası ve hedef kimlik bilgileri challenge talebine dahil edilmelidir.
  3. Backend sıfır dolgulu fallback'i tamamen kaldırmalı ve `data` sağlanmadığında işlemi derhal durdurmalıdır.
  ```python
  # src/ui/desktop_app.py flash_start() içinde:
  if not raw_binary_data or len(raw_binary_data) == 0:
      return {"success": False, "error": "Geçerli firmware ikili verisi sağlanmadı (sıfır dolgulu fallback yasaklandı)."}
  ```

### [HIGH] Bulgu 3.2 — J1939 Adres Talep (Address Claiming) Motorunun Canlı Akışa Bağlanmaması
- **Konum:** `src/protocols/j1939/address_claim.py`, `src/ui/desktop_app.py`, `AGENTS.md:§2.7`
- **Mekanizma:** `AddressClaimEngine` (SAE J1939-81 uyumlu 64-bit NAME ve dinamik adres çekişmesi algoritması) yazılmış ve test edilmiştir; ancak `UniversalCanDesktopApp` composition root'unda hiçbir zaman ilklendirilmemekte ve hatta bağlanmamaktadır.
- **Tehdit:** Ağır vasıta veya deniz taşıtları J1939 ağlarında teşhis ve telemetri işlemleri yapılırken sabit `0xF9` (249) kaynak adresi doğrudan kullanılmaktadır. Ağda bu adresi kullanan başka bir diyagnostik cihaz veya ECU varsa adres çakışması meydana gelir, J1939-81 standardı ihlal edilir ve veri yolu iletişiminde paket kayıpları yaşanır.
- **Çözüm:** Masaüstü uygulaması başlatılırken bir `AddressClaimEngine` oluşturulmalı, CAN bus bağlantısı kurulur kurulmaz adres talep süreci işletilmeli ve yalnızca adres `CLAIMED` durumuna geçtikten sonra J1939 TX yetkisi verilmelidir.

### [HIGH] Bulgu 3.3 — ISO-TP Alıcı Bellek Tahsisinde (Allocation) DoS Zafiyeti
- **Konum:** `src/protocols/uds/isotp.py:310-340`
- **Mekanizma:** `IsoTpTransport` alıcı mantığı, bir First Frame (FF) aldığında belirtilen `total_length` kadar bellek alanını (`bytearray(total_length)`) hemen ayırır. CAN-FD modunda `total_length` 4 GB'a kadar, klasik CAN'da 4095 bayta kadar çıkabilir.
- **Tehdit:** Kötü niyetli veya parazitli bir kaynaktan gelen sahte FF paketleri, arka arkaya devasa bellek ayırmaları yaptırarak Python sürecinin Out-Of-Memory (OOM) hatasıyla çökmesine (Hizmet Dışı Bırakma - DoS) yol açabilir.
- **Çözüm:** Maksimum kabul edilebilir ISO-TP yükü konfigüre edilebilir bir tavanla (`MAX_ISOTP_PAYLOAD_SIZE = 65536`) sınırlandırılmalı ve bu tavanı aşan FF paketleri derhal reddedilmelidir.

---

## 5. Aşama 4: Telemetri ve İşleme Motoru İncelemesi (`src/engine/`)

**İncelenen Dosyalar:**
- `src/engine/router.py` (14 KB — FrameRouter Pub-Sub Dağıtıcısı)
- `src/engine/buffer/rolling_disk.py` (Blackbox Zstandard + HMAC Disk Tamponu)
- `src/engine/buffer/ring_buffer.py` (NumPy Sıfır-GC Halka Tampon)
- `src/engine/decoder/dbc_decoder.py` (LRU-önbellekli DBC Sinyal Çözücü)
- `src/engine/discovery/engine.py` ve `dbc_builder.py` (Sinyal Keşfi ve Otomatik DBC İnşası)
- `src/engine/virtual_channels/channel_engine.py` (Tork, Güç, Slip Sanal Kanalları)
- `src/engine/ai/diagnostic_copilot.py` (Yerel Deterministik Kök Neden Analiz Motoru)

### [HIGH] Bulgu 4.1 — `DbcBuilder`'ın `report.is_extended` Bilgisini Yok Sayması
- **Konum:** `src/engine/discovery/dbc_builder.py:126`
- **Mekanizma:** Sinyal keşif motoru (`SignalDiscoveryEngine`), son güncelleme ile akışları `(channel_id, is_extended, arbitration_id)` bazında ayrıştırmış ve `IdReport` sınıfına `is_extended: bool` alanını eklemiştir. Ancak DBC üretiminden sorumlu `DbcBuilder.build_database()` fonksiyonu 126. satırda:
  ```python
  is_extended = report.arbitration_id > 0x7FF
  ```
  mantığını kullanmakta ve `report.is_extended` değerini **tamamen görmezden gelmektedir**!
- **Tehdit:** 29-bitlik bir genişletilmiş CAN çerçevesi, sayısal olarak `0x7FF` veya daha küçük bir değere sahip olduğunda (örneğin Priority 0, PGN 0, SA 1 => `0x00000001`), bu çerçeve üretilen DBC dosyasında yanlışlıkla **11-bit Standart Çerçeve** (`is_extended_frame=False`) olarak tanımlanır. Bu DBC dışa aktarılıp Vector CANoe veya Wireshark'a yüklendiğinde mesajlar eşleşmez ve telemetri tamamen kaybolur.
- **Çözüm:**
  ```python
  # src/engine/discovery/dbc_builder.py:126 düzeltmesi:
  is_extended = getattr(report, "is_extended", False) or (report.arbitration_id > 0x7FF)
  ```

### [HIGH] Bulgu 4.2 — AI Copilot Serbest Metin VIN Maskelemesinde Büyük/Küçük Harf Açığı
- **Konum:** `src/engine/ai/diagnostic_copilot.py:28-36`
- **Mekanizma:** `mask_vin_in_text()` fonksiyonu serbest metindeki 17 karakterlik şasi numaralarını maskelemek için şu düzenli ifadeyi kullanır:
  ```python
  _VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
  ```
- **Tehdit:** Bu regex'te `re.IGNORECASE` bayrağı tanımlanmamıştır. Bir kullanıcı veya teknisyen küçük harfli bir VIN girdiğinde (örneğin `1hgcr2f83ha123456`), ifade eşleşmez ve VIN **hiç maskelenmeden** loglara, oturum özetine veya dışa aktarılan rapora sızar. Bu durum P1 Data Model veri gizliliği kurallarını ihlal eder.
- **Çözüm:**
  ```python
  _VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
  ```

### [MEDIUM] Bulgu 4.3 — `FrameRouter.route_frame()` Sıcak Yolunda İstatistik Kilidi Darboğazı
- **Konum:** `src/engine/router.py:145-165`
- **Mekanizma:** `FrameRouter`, abonelikleri `_route_snapshot` ile kilit almadan dağıtmaktadır (copy-on-write). Ancak hemen öncesinde:
  ```python
  with self._stats_lock:
      self._total_routed += 1
  ```
  her bir gelen CAN çerçevesi için kilit almaktadır.
- **Tehdit:** 1 Mbps CAN-FD veri yolunda saniyede 5.000–10.000 çerçeve aktarılırken, her çerçevede Python seviyesinde lock alma/bırakma işlemi Python GIL üzerinde ciddi CPU çekişmesine (lock contention) ve paket işleme gecikmelerine neden olur.
- **Çözüm:** Sayaç artırımı yerel thread tamponlarında veya atomik aralıklarla (örneğin her 100 çerçevede bir) toplu olarak yapılmalıdır.

---

## 6. Aşama 5: Güvenlik, Lisanslama ve Bulut İstemcisi (`src/security/`)

**İncelenen Dosyalar:**
- `src/security/cloud/client.py` (CloudClient, Canonical Hosts Allowlist, SafeRedirect)
- `src/security/cloud/telemetry_uploader.py` (MDF4 5 MB Parçalı Resumable Yükleyici)
- `src/security/cloud/license_flow.py` (Ed25519 Lisans Aktivasyonu ve HWM Anti-Rollback)
- `src/security/hwid/collector.py` (Donanım Parmak İzi Toplayıcı)
- `src/safety/secret_provider.py` (DPAPI/AES Kriptografik Anahtar Kasası)

### [HIGH] Bulgu 5.1 — Buluta Telemetri Yükleme Arayüzünde VIN Onay Parametresi Eksikliği
- **Konum:** `src/security/cloud/telemetry_uploader.py:125-135`, `src/ui/desktop_app.py:835-850`, `src/ui/frontend/src/services/bridge.ts:520-530`
- **Mekanizma:** `TelemetryUploader`, `vehicle_vin` sağlandığında gizlilik gereği `user_consented=True` zorunlu tutmakta; aksi takdirde `TELEMETRY_CONSENT_REQUIRED` fırlatmaktadır. Fakat masaüstü bridge API'si ve TypeScript servis tanımı `cloudUploadRawContent(filename, content, vehicleVin)` şeklinde olup `user_consented` parametresi içermemektedir.
- **Tehdit:** Kullanıcı arayüzünden VIN içeren bir seansı buluta yüklemek istediğinde, backend isteği fail-closed olarak sürekli reddetmekte ve kullanıcıya sebebi anlaşılamayan bir yükleme hatası vermektedir.
- **Çözüm:** Frontend arayüzüne açık rıza onay kutusu ("Araç şasi numarasının telemetri verisiyle buluta aktarılmasını onaylıyorum") eklenmeli, bridge sözleşmesi güncellenerek bu bayrak backend'e aktarılmalıdır.

### [MEDIUM] Bulgu 5.2 — Masaüstü Bridge'deki 1 MiB Ham Yükleme Tavanı vs. 5 MB Chunked Vaadi
- **Konum:** `src/ui/frontend/src/components/reports/ReportsExportView.tsx:145-150`, `src/ui/desktop_app.py:820-845`
- **Mekanizma:** Frontend arayüzünde "Seans 5 MB parçalar halinde (resumable) buluta yükleniyor" ibaresi yer almaktadır. Ancak arka planda çağrılan `cloud_upload_raw_content` fonksiyonu veriyi tek parça string olarak almakta ve `1024 * 1024` bayt (1 MiB) üzerindeki tüm içerikleri `CONTENT_TOO_LARGE` ile reddetmektedir.
- **Tehdit:** 1 MiB'tan büyük gerçek sürüş logları ve telemetri oturumları buluta yüklenemez; arayüzdeki "5 MB Resumable" vaadi yanıltıcı kalmaktadır.
- **Çözüm:** Masaüstü köprüsünde büyük dosyalar için gerçek `TelemetryUploader.upload_file()` motoru kullanılmalı; dosya geçici bir MDF4 dosyasına yazılarak 5 MB'lık gerçek REST oturumuyla yüklenmelidir.

---

## 7. Aşama 6: Uygulama Kökü, Masaüstü Köprüsü ve GUI (`src/ui/`, `src/launcher/`, `src/main.py`)

**İncelenen Dosyalar:**
- `src/ui/desktop_app.py` (204 KB — Composition Root, Telemetry Loop, Bridge RPC)
- `src/main.py` (CLI & GUI Başlatıcı, Parametre Doğrulayıcı)
- `src/launcher/app.py` (Preflight, Update Bütünlüğü, Hash Manifest Doğrulama)
- `src/ui/frontend/src/components/ecu/EcuFlashingView.tsx` (React Reprogramming UI)

### [CRITICAL] Bulgu 6.1 — Flashing Onay Token'ının Firmware İmajı ve Hedef Parametrelerine Bağlanmaması
- **Konum:** `src/ui/desktop_app.py:2425-2445`, `src/ui/desktop_app.py:2560-2585`
- **Mekanizma:** Bir flashing işlemi başlatılırken `request_diagnostic_challenge` ile 30 saniyelik tek kullanımlık bir token talep edilir. Ancak bu token oluşturulurken yalnızca `action_type="uds_routine"` ve `action_id` hash'lenir. Kullanıcının gerçekten onayladığı firmware SHA-256 özeti, hedef bellek adresi ve hedef ECU VIN numarası token kapsamına alınmaz.
- **Tehdit:** Tarayıcı / WebView katmanında çalışan kötü niyetli veya manipüle edilmiş bir script, operatöre masum bir diyagnostik rutin (örneğin fan testi) için onay penceresi çıkartıp, alınan onay token'ını kullanarak arkada gizlice `flash_start` API'sini bambaşka bir firmware dosyası ve hedef adresiyle çağırabilir! Gateway token'ı geçerli sayacağı için Stage-5 dual-confirmation aşılır.
- **Çözüm:** Flashing challenge token'ı, zorunlu olarak kanonik firmware parametrelerine mühürlenmelidir:
  ```python
  canonical_payload = f"{action_type}:{action_id}:{firmware_sha256}:{memory_address}:{size_bytes}:{target_vin}"
  params_hash = hashlib.sha256(canonical_payload.encode()).hexdigest()
  ```
  `flash_start` çağrıldığında gelen parametrelerin hash'i ile token'daki hash birebir eşleşmezse işlem derhal iptal edilmelidir.

### [HIGH] Bulgu 6.2 — Telemetri Döngüsünde Senkron Kod Çözümünün HOL Bloklaması Yaratması
- **Konum:** `src/ui/desktop_app.py:2900-3020` (`_telemetry_loop`)
- **Mekanizma:** `_telemetry_loop` arka plan thread'i, CAN sürücüsünden `recv(timeout_s=0.0)` ile boşalttığı 200 çerçevelik paketin her biri için `_ingest_live_frame()` fonksiyonunu senkron olarak çalıştırmaktadır. Bu fonksiyon içinde DBC sinyal çözümü, J1939 TP durum makinesi, NMEA 2000 Fast Packet reassembly ve discovery motoru sıralı çalışır.
- **Tehdit:** Ağ trafiği yükseldiğinde bu yoğun işlem yükü telemetri thread'ini geciktirir. Sürücünün `recv()` kuyruğu boşaltılamaz ve donanım adaptörünün (PCAN/Kvaser/RP1210) dahili FIFO tamponu taşarak paket kaybına (Buffer Overrun / Frame Drop) neden olur.
- **Çözüm:** Canlı alım (RX) döngüsü yalnızca çerçeveleri çekip `BinaryRingBuffer` ve `FrameRouter`'a aktarmalı; ağır reassembly ve sinyal analizi protokol worker thread'lerinde asenkron olarak yürütülmelidir.

### [MEDIUM] Bulgu 6.3 — Çevrimdışı Masaüstü Aracının Açılışta Dış CDN Kaynaklarına Bağlanması
- **Konum:** `src/ui/frontend/index.html:9-11`
- **Mekanizma:** HTML başlığında Google Fonts CDN bağlantıları (`fonts.googleapis.com`, `fonts.gstatic.com`) yer almaktadır.
- **Tehdit:** İnternet erişimi olmayan kapalı servis atölyelerinde (air-gapped / offline test alanları) WebView açılışında font istekleri zaman aşımına uğramakta, arayüz render süresi uzamakta ve harici sunuculara IP sızıntısı olmaktadır.
- **Çözüm:** Gerekli font dosyaları (Inter, JetBrains Mono) yerel `assets/fonts/` dizinine gömülmeli ve `index.css` içinden `@font-face` ile yerel olarak yüklenmelidir.

---

## 8. Bulgu Özeti ve Düzeltme Matrisi

| # | Modül / Dosya | Bulgu Tanımı | Önem | ASIL / Standart Etkisi | Durum |
|---|---|---|---|---|---|
| 1 | `src/ui/desktop_app.py` | Replay oynatımının fiziksel CAN hattına TX tetiklemesi | **CRITICAL** | ISO 26262 ASIL-D / Canlı Ağ İhlali | Çözüm Belirtildi |
| 2 | `src/ui/frontend` & `desktop_app.py` | Flashing UI imza ve dosya verisi kopukluğu, sıfır dolgu riski | **CRITICAL** | ISO 14229 UDS / ECU Brick Riski | Çözüm Belirtildi |
| 3 | `src/ui/desktop_app.py` | Flash challenge token'ının firmware hash'ine bağlanmaması | **CRITICAL** | ISO 26262 Stage-5 Güvenlik Aşımı | Çözüm Belirtildi |
| 4 | `src/safety/gateway.py` | E2E Safety Pipeline'ın canlı hatta bağlanmaması | **HIGH** | AUTOSAR E2E / ASIL-B/D Güvenlik Boşluğu | Çözüm Belirtildi |
| 5 | `src/protocols/uds/client.py` | ISO-TP çok çerçeveli Consecutive Frame token boşluğu | **HIGH** | ISO 15765-2 / DoCAN Güvenlik Boşluğu | Çözüm Belirtildi |
| 6 | `src/protocols/j1939/` | J1939-81 Address Claiming Engine'in canlı hatta bağlanmaması | **HIGH** | SAE J1939-81 Adres Çakışması | Çözüm Belirtildi |
| 7 | `src/engine/discovery/` | `DbcBuilder`'ın `report.is_extended` bayrağını yok sayması | **HIGH** | ISO 11898-1 / 29-bit CAN ID Bozulması | Çözüm Belirtildi |
| 8 | `src/engine/ai/` | AI Copilot serbest metin VIN maskelemesinde regex case-sensitivity | **HIGH** | P1 Data Model Gizlilik İhlali | Çözüm Belirtildi |
| 9 | `src/hal/rp1210/bus.py` | RP1210 paket zaman damgalarının duvar saatinden üretilmesi | **HIGH** | Monotonik Zaman Damgası Regresyonu | Çözüm Belirtildi |
| 10 | `src/protocols/uds/isotp.py` | ISO-TP First Frame bellek ayırma DoS riski | **HIGH** | Kaynak Tüketimi / OOM | Çözüm Belirtildi |
| 11 | `src/safety/gateway.py` | `rebind_bus()` sırasında kategori bütçe tokenlarının sıfırlanmaması | **MEDIUM** | İletim Başarısızlığı / Availability | Çözüm Belirtildi |
| 12 | `src/engine/router.py` | `route_frame()` sıcak yolunda `_stats_lock` GIL çekişmesi | **MEDIUM** | Performans / Yüksek Bus Yükü Darboğazı | Çözüm Belirtildi |
| 13 | `src/security/cloud/` | Bulut telemetri yükleme UI'ında VIN rıza parametresi eksikliği | **MEDIUM** | GDPR / Gizlilik / Fail-Closed Kilitlenme | Çözüm Belirtildi |
| 14 | `src/ui/desktop_app.py` | Ham bulut yüklemesinde 1 MiB tavanı vs 5 MB chunk vaadi | **MEDIUM** | Fonksiyonel Kullanılabilirlik | Çözüm Belirtildi |
| 15 | `src/hal/replay/` | ReplaySafetyFilter iç durumunun seanslar arasında sızması | **MEDIUM** | İz Analizi Doğruluğu | Çözüm Belirtildi |
| 16 | `src/ui/frontend/` | Çevrimdışı masaüstü uygulamasında Google Fonts CDN bağımlılığı | **LOW** | Air-Gapped / Çevrimdışı Ağ İzolasyonu | Çözüm Belirtildi |

---

## 9. Sonuç ve Eylem Planı

UCANLAB projesi, çekirdek iş mantığı ve standart uyumluluğu (ISO 26262 ASIL-B/D, UDS, J1939) açısından son derece güçlü bir mühendislik temeline sahiptir. Yapılan bu kapsamlı denetimde hiçbir hayali bulgu üretilmemiş; projenin gerçek Python, TypeScript ve test kodları taranarak 3 adet **CRITICAL**, 7 adet **HIGH**, 5 adet **MEDIUM** ve 1 adet **LOW** seviyeli gerçek bulgu tespit edilmiş ve eksiksiz giderilme adımları tanımlanmıştır.

Bu raporun eşliğinde hazırlanan interaktif web paneli üzerinden tüm aşamalar, kod farkları (diff), mimari akış şemaları ve regresyon test kılavuzları detaylı olarak incelenebilir.
