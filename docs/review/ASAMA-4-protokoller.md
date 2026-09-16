# ASAMA 4: Protokol Katmanları Raporu
Denetçi: chassis [Hermes] | Tarih: 2026-09-15
Kapsam: `src/protocols/j1939/transport.py` (1505 satır), `src/protocols/uds/isotp.py` (1386 satır), `src/protocols/uds/flasher.py` (853 satır), `src/protocols/uds/client.py` (848 satır). Bağlam için okunan ek dosyalar: `src/safety/gateway.py`, `src/ui/desktop_app.py` (composition root), `src/protocols/uds/services.py`, `src/protocols/uds/nrc.py`, `src/core/models/can_frame.py`, `src/core/exceptions.py`, `src/engine/pipeline/reassembly_pipeline.py`, `AGENTS.md`, `tests/unit/test_flasher.py`, `tests/safety/test_e2e_safety_audit.py`.

> **Doğrulama yöntemi:** Yalnızca statik kaynak kod okuması (çalışma zamanı yok). Bu oturumda **hiçbir test çalıştırılmadı**, hiçbir bulgu saha/laboratuvarda yeniden üretilmedi. Her bulguda "Kanıt" gerçek `dosya:satır` + kod alıntısıdır; "Senaryo" kod okumasından türetilmiş bir tetiklenme yoludur. Satır numaraları 1 tabanlıdır ve rapor yazıldığı andaki dosya içeriğiyle birebir doğrulanmıştır.
>
> **Severity ölçütü (bu rapor için):** KRİTİK = mevcut üretim wiring'inde doğrudan tetiklenebilir. YÜKSEK = invariant ihlali / fail-open (tetiklenmesi ek koşul gerektirir). ORTA = savunma katmanı zayıflığı / latent (üretimde çağırıcısı yok) DoS-OOM. DÜŞÜK = sınırlı etki veya fail-closed yönde. BİLGİ = not.
>
> **Kapsam notu (AGENTS.md §2.7):** `EcuFlashingEngine` AGENTS.md'de "NOT wired / LATENT defect lock" listesindedir, ancak `src/ui/desktop_app.py:2036` (flash_start → `EcuFlashingEngine`) onu gerçek fiziksel dala bağlar. Bu çelişki AŞAMA-1 W-1'de de not edilmiştir; bu raporda flasher **üretimde bağlı** varsayılmış, ancak her bulgunun gerçekten tetiklenebilirliği ayrıca doğrulanmıştır.

## 1. Yönetici Özeti

Dört protokol modülü, önceki REVIEW turlarının (REVIEW 1/2/3, P0/P1/P2 düzeltmeleri) izlerini taşıyor ve **J1939-21 transport katmanı ile ISO-TP senkron/kirli (reassembly) katmanı genel olarak fail-closed ve sıkılaştırılmış** durumda: J1939 RX oturumları PGN-kapsamlı 4-tuple anahtarla ayrıştırılmış, per-SA kota (4) + global tavan (512) + T1/T4 reap uygulanıyor, BAM/RTS uzunluk ve paket-sayısı tutarlılığı (`(total_bytes+6)//7`) doğrulanıyor, `MAX_CTS_PACKET_COUNT=16` ile CTS taşması sınırlanıyor; ISO-TP'de FF/CF/FC durum makineleri uzunluk ve PCI-tip doğrulamalarıyla korunuyor, `segment_message` `MAX_UDS_PAYLOAD_FD`/`MAX_UDS_PAYLOAD_CLASSIC` ile fail-closed sınırlanıyor, `handle_rx_frame` belirsiz paralel oturumda DT'yi düşürüyor. UDS istemcisi de yanıt SID + servise-özel echo baytı doğrulaması (`_response_echo_matches`) yapıyor.

Buna karşın **1 KRİTİK ve 2 YÜKSEK** bulgu tespit edildi:

1. **KRİTİK — UDS istemcisinde NRC 0x78 (responsePending) sınırsız uzatma:** `_send_and_receive` her 0x78'de `deadline`'ı "şimdi + 5 s" yapıyor; `max_pending_timeout_s` varsayılanı `None` olduğundan **mutlak üst sınır yok**. Bir ECU'nun 5 s'den sık 0x78 göndermesi çağrıyı **sonsuz döngüye** sokar ve `_operation_lock` tutulu kalır (diyagnostik thread + flasher TesterPresent keep-alive bloke). Kodun kendi yorumu (satır 704-710) "dış duvar `timeout_s` ile sınırlı" diyerek **yanlış güvence** veriyor.
2. **YÜKSEK — Flasher'da firmware imza/anti-rollback doğrulaması yok (fail-open):** `firmware_signature` yalnızca uzunluk olarak kontrol ediliyor, imajla kriptografik doğrulanmıyor; `min_version` hiç uygulanmıyor. Tek bütünlük kontrolü forgeable CRC32 ve ECU'nun kendi rutini. (Şu an ek bir savunma — Stage-5 HMAC token kapısı — flash'ı step-2'de fail-closed reddettiği için doğrudan istismar engelleniyor; bkz. bulgu F-2.)
3. **YÜKSEK — Flasher ve UDS istemcisi kritik adımlarda gateway HMAC onay token'ını hiç sunmuyor:** `flasher` 0x10/0x34/0x36/0x37/0x31/0x11 çağrılarında `confirmation_token` yok; `client.py`'nin bazı kritik metodları bu parametreyi hiç kabul etmiyor. Üretim gateway'i `confirmation_secret` ile kurulduğu için (desktop_app.py:759) Stage-5 bu çağrıları **fail-closed reddeder** (flash kullanılamaz); `allow_legacy_boolean_confirm` bayrağı açılırsa Stage-5 boolean'a düşer ve T41/G-3'ün kapattığı bypass geri gelir.

Ayrıca 7 ORTA (çoğu latent/DoS sınıfı), 4 DÜŞÜK ve 2 BİLGİ bulgu var. ASIL-B/D duruşu: J1939-21 ve ISO-TP çekirdek mekanizmaları hedefle uyumlu görünüyor; ancak (a) UDS NRC yönetimindeki sınırsız döngü, (b) flasher'ın imza doğrulamasını "fail-log" olarak bırakması ve (c) kritik yazma yolu için onay token'ının hiç bağlanmaması, izlenebilirlik ve fail-closed iddiasını zayıflatıyor. Öncelik: U-1 (mutlak sınır) ve F-2 (token plumbing) regresyon testleriyle birlikte; F-1 için imza doğrulama zorunlu hale getirilmeli.

## 2. Bulgu Tablosu

| # | dosya:satır | severity | sorun | senaryo | düzeltme |
|---|---|---|---|---|---|
| U-1 | `client.py:51,99,702-790` | KRİTİK | NRC 0x78 her geldiğinde `deadline = now + 5s`; `max_pending_timeout_s` varsayılan `None` → mutlak üst sınır yok, döngü sınırsız | ECU bir okumaya `03 7F 22 78`'i <5 s'de bir gönderir → `_send_and_receive` sonsuz döner, `_operation_lock` tutulur, TesterPresent ve tüm sonraki UDS op'ları bloke | Mutlak üst sınırı varsayılan yap (`MAX_PENDING_ABS_S`, ör. 30 s) ve 0x78 sayacına üst limit koy |
| F-1 | `flasher.py:98-99,399-417` | YÜKSEK | `firmware_signature` yalnız uzunluk kontrolü, imajla kriptografik doğrulama yok; `min_version` anti-rollback uygulanmıyor (fail-open) | İmzasız/kurcalanmış firmware flash edilir; tek bütünlük kontrolü forgeable CRC32 | İmzayı gömülü güvenilir açık anahtarla imaj üzerinde doğrula; `min_version`'ı fail-closed uygula |
| F-2 | `flasher.py:474-476,518-520,603-607,642,676-678,702-706,770-772`; `client.py:225-232,285-304,306-333,335-356,378-385` | YÜKSEK | Kritik flash adımları gateway `confirmation_token`'ı sunmuyor; `client.py`'nin bazı kritik metodları parametreyi hiç almıyor | Üretim gateway'inde secret wired → step-2'de `DualConfirmationRequiredError` (flash kullanılamaz); `allow_legacy_boolean_confirm=True` ise Stage-5 boolean'a düşer | Tüm kritik metodlara `confirmation_token` ekle; flasher composition root'tan tek-kullanımlık HMAC token alsın |
| U-2 | `client.py:741,840` | ORTA | İstemcinin ürettiği ISO-TP FC/CF yanıtları `inbound_triggered=True` ile gönderilmiyor | Hostile ECU çok-parçalı yanıtla FC seli → global 400 msg/s envelope aşımı + E-Stop (uzak self-DoS); J1939 yolu bunu işaretliyor (desktop_app.py:2673) | `send_sync(..., inbound_triggered=True)` ekle |
| I-1 | `isotp.py:110-130,943-945` | ORTA | `decode_st_min` rezerve 0xFA–0xFF → 127 ms; async `_apply_st_min` bu değeri CF başına bekliyor | Sahte FC (STmin=0xFA) async transferi CF başına 127 ms'ye yavaşlatır (senkron yol 10 ms'e clamp ediyor) | Rezerve aralıkları 10 ms'e clamp et / reddet |
| I-2 | `isotp.py:987-1075` | ORTA | `IsoTpSender.send` payload boyutuna üst sınır koymuyor (`segment_message` sınırlı) | Büyük payload → milyonlarca CF (bus flood + RAM) | `send`'de de `MAX_UDS_PAYLOAD_FD`/classic kontrolü |
| J-1 | `transport.py:1207-1244,1457-1477` | ORTA | `start_cmdt_transfer` TX oturumlarını sınırsız açar; `poll_cmdt_timeouts`'un üretim çağırıcısı yok | CMDT TX oturumu açılıp advance/poll çağrılmazsa `_tx_sessions` sızar | TX oturum üst sınırı + otomatik reap |
| J-2 | `transport.py:1379-1395` | ORTA | `_handle_tx_cm` HOLD (packet_count=0) ve `next_seq` rewind kabul eder; aktivite her seferinde tazelenir | Sahte peer sürekli HOLD CTS → oturum hiç reap olmaz; rewind CTS → DT yeniden yayını (TX amplifikasyon) | HOLD sayısı/penceresi ve rewind derinliğine sınır |
| J-5 | `transport.py:862-885,539-556` | ORTA | Bir (SA,DA,channel) için PGN-kapsamlı paralel oturumlar → `_lookup_dt_session` belirsizlikte tüm DT'yi düşürür | Yavaş saldırgan (RTS'ler arası >1 s) aynı SA için 4 farklı-PGN oturum açar → o SA'nın meşru reassembly'si hiç tamamlanmaz (DoS) | Belirsizlikte en-yeni oturumu seç veya paralel PGN oturumunu engelle |
| F-4 | `flasher.py:484-508` | ORTA | VIN/serial hedef doğrulaması opsiyonel (varsayılan kapalı) | Çok-ECU bus'ta yanlış ECU'ya flash riski | Varsayılan zorunlu ya da en az uyarı+onay |
| J-3 | `transport.py:577-589` | DÜŞÜK | `old_bam.target_pgn != target_pgn` dalı erişilemez (anahtar PGN-kapsamlı 4-tuple) | Ölü kod / yanıltıcı log | Dalı kaldır (çapraz-PGN yolu `live_prefix` ile ele alınıyor) |
| J-4 | `transport.py:335,818-838` | DÜŞÜK | `_rts_prefix_marks` hiç reap edilmiyor | Uzun ömürde ~256 (SA) kaydı birikir (`reset_sessions` temizliyor) | Reap veya LRU sınırı |
| I-3 | `client.py:496-566`; `isotp.py:947-985` | DÜŞÜK | FC kanal eşleşmesi "incoherent" topolojide düşürmüyor (`coherent` False iken `continue` atlanır) | Sahte/çapraz-kanal FC transferi yönlendirebilir | Topoloji tutarsızken de çapraz-kanal FC'yi düşür |
| F-3 | `flasher.py:829` | DÜŞÜK | `_best_effort_recovery` 0x37'yi `user_confirmed=False` (varsayılan) çağırıyor | Kurtarma 0x37 her zaman Stage-5'te reddedilir; "en temiz çıkış" hiç alınmaz | Kurtarmada onayı config'ten ilet (K-08) |
| B-1 | `flasher.py:94-99` | BİLGİ | `firmware_signature`/`min_version` "gelecek adım" olarak belgelenmiş | İzlenebilirlik | — |
| B-2 | `transport.py:475-483` | BİLGİ | J1939-22 ETP (PGN 51200/50944) uygulanmamış (loglanıp düşürülüyor) | Standart kapsam boşluğu, açıkça loglanıyor | — |

*Severity: KRİTİK | YÜKSEK | ORTA | DÜŞÜK | BİLGİ*

## 3. Detaylı Bulgular (Tüm KRİTİK ve YÜKSEK Seviyeler)

### [U-1] NRC 0x78 her pending'de mutlak sınırı ileri itiyor — sınırsız döngü / DoS
- **Konum:** `src/protocols/uds/client.py:51` (`max_pending_timeout_s: float | None = None`), `99` (`self.max_pending_timeout_s = max_pending_timeout_s`), `702-790` (`_send_and_receive` ana döngüsü), özellikle `775-789`.
- **Etki / Risk:** ISO 14229 P2* uzatması, sunucunun her `0x78` (RequestCorrectlyReceived-ResponsePending) yanıtında pencereyi yeniden açmasına izin verir. Ancak burada **hiçbir mutlak üst sınır uygulanmıyor**: `max_absolute_deadline`, `max_pending_timeout_s` verilmediğinde (varsayılan) `None` kalır ve her `0x78`'de `deadline = time.monotonic() + 5.0` atanır. ECU `0x78`'i 5 s'den daha sık gönderdiği sürece `remaining` hiç sıfırlanmaz → döngü **sonsuza kadar** döner. `_operation_lock` (satır 694) bu süre boyunca tutulduğundan, aynı istemciyi paylaşan **flasher TesterPresent keep-alive thread'i** (`flasher.py:372-376`) ve sonraki tüm senkron UDS operasyonları bloke olur; flash ortasında oluşan bir kilitlenme ECU'yu programlama oturumunda bırakabilir. Kodun kendi yorumu yanlış güvence veriyor: *"an outer wall remains bounded by the per-request timeout_s"* — `deadline` her pending'de yeniden atandığı için `timeout_s` dış duvar **değildir**.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # client.py:702-715
  start_time = time.monotonic()
  deadline = start_time + timeout_s
  max_absolute_deadline = (
      start_time + self.max_pending_timeout_s
      if self.max_pending_timeout_s is not None
      else None                       # <-- varsayılan: mutlak sınır YOK
  )
  # client.py:718-726
  while True:
      now = time.monotonic()
      remaining = deadline - now
      if remaining <= 0 or (max_absolute_deadline is not None and now >= max_absolute_deadline):
          raise ProtocolError(... code="UDS_TIMEOUT" ...)
  # client.py:775-789
  if (
      not resp.is_positive
      and resp.nrc == UdsNrc.REQUEST_CORRECTLY_RECEIVED_RESPONSE_PENDING
      and (max_absolute_deadline is None or time.monotonic() < max_absolute_deadline)
  ):
      nrc_78_count += 1
      new_deadline = time.monotonic() + P2_STAR_TIMEOUT_S   # 5.0 s
      if max_absolute_deadline is not None:
          new_deadline = min(new_deadline, max_absolute_deadline)
      deadline = new_deadline          # <-- her pending'de ileri itilir
      continue
  ```
  Negatif yanıtın `handle_rx_frame`'den geçmesi mümkündür: `03 7F 22 78` tek-kare (SF) olarak `bytes(data[1:4])` → `parse_response` `is_positive=False`, `nrc=0x78` döndürür (`services.py:335-347`).
- **İstismar / Tetiklenme Senaryosu:** Üretimde `desktop_app`'in tüm UDS okuma/yazma işlemleri `create_uds_client()` üzerinden `_send_and_receive` çağırır. Bozuk veya düşmanca bir ECU, örneğin `0x22` DID okumasına 4 saniyede bir `03 7F 22 78` yanıtı verir. `deadline` her seferinde +5 s ileri gider, döngü asla çıkmaz; UI thread'i ve `_operation_lock` kilitli kalır. (Çalışma zamanında yeniden üretilmedi.)
- **Düzeltme (Remediation):**
  ```python
  # client.py
  MAX_PENDING_ABS_S: ClassVar[float] = 30.0     # sabit üst duvar (varsayılan)
  MAX_NRC_78_COUNT: ClassVar[int] = 200
  ...
  max_absolute_deadline = start_time + (
      self.max_pending_timeout_s if self.max_pending_timeout_s is not None
      else self.MAX_PENDING_ABS_S
  )
  ...
  if (not resp.is_positive
      and resp.nrc == UdsNrc.REQUEST_CORRECTLY_RECEIVED_RESPONSE_PENDING
      and nrc_78_count < self.MAX_NRC_78_COUNT
      and time.monotonic() < max_absolute_deadline):
      nrc_78_count += 1
      deadline = min(time.monotonic() + P2_STAR_TIMEOUT_S, max_absolute_deadline)
      continue
  ```
  Regresyon testi: ECU `0x78`'i sürekli gönderirken `_send_and_receive` `MAX_PENDING_ABS_S` sonunda `UDS_TIMEOUT` fırlatmalı.

### [F-1] Flasher firmware imza ve anti-rollback doğrulamasını yapmıyor (fail-open)
- **Konum:** `src/protocols/uds/flasher.py:98-99` (`firmware_signature`/`min_version` alanları), `399-417` (`execute_flash` doğrulama bloğu).
- **Etki / Risk:** `FlashingConfig.firmware_signature` doluysa yalnızca **boş olup olmadığı** kontrol edilir; imza hiçbir zaman `config.data` üzerinde kriptografik olarak doğrulanmaz (imzalama anahtarı/algoritması yok). `min_version` anti-rollback tabanı da yalnızca loglanır, hiçbir karşılaştırma yapılmaz. Bu nedenle bir saldırganın/hatalı sürecin sağladığı **kurcalanmış veya yetkisiz firmware** ECU'ya yazılır. Mevcut tek bütünlük kontrolü `zlib.crc32` (`flasher.py:388`) ve SHA-256 (`393`) olup CRC32 forgeable'dır ve doğrulama yalnızca **ECU'nun kendi rutinine** (0x31 0x0202) bırakılmıştır — tool tarafında kimlik/authenticity kanıtı yoktur. Bu, "fail-log, not fail-mandatory yet" olarak **belgelenmiş ama kapatılmamış** bir bütünlük kapısıdır (AGENTS.md §2 fail-closed ilkesiyle çelişir).
- **Kanıt / Zafiyet Analizi:**
  ```python
  # flasher.py:98-99
  firmware_signature: bytes | None = None
  min_version: int = 0
  # flasher.py:399-417
  if config.firmware_signature is None:
      self._log(
          "UYARI: firmware_signature yok — imza doğrulaması atlandı "
          "(fail-log; tam zorunluluk ileride).",
          "warning",
      )
  else:
      sig_len = len(config.firmware_signature)
      if sig_len == 0:
          raise SafetyError("Boş firmware_signature ile flash reddedildi (fail-closed).",
                            code="FLASH_SIGNATURE_INVALID")
      self._log(f"İmza alanı mevcut (uzunluk={sig_len}, min_version={config.min_version}).", "info")
  ```
  `firmware_signature` hiçbir yerde `config.data` ile karşılaştırılmaz (`search` ile doğrulandı: yalnızca `len()` kullanılıyor). `min_version` yalnızca log satırında geçiyor.
- **İstismar / Tetiklenme Senaryosu:** `flash_start` bridge metodu renderer'a açıktır (`desktop_app.py:263-266`); renderer `request_diagnostic_challenge({"action_type": "ecu_flash"})` ile kendi onay token'ını alıp `flash_start({"data": "<keyfi hex>", "action_type": "ecu_flash"}, token)` çağırabilir. İmza doğrulaması olmadığından kurcalanmış imaj reddedilmez. **Ancak** şu an ek bir savunma devrede: flasher kritik adımlarda gateway HMAC token'ı sunmadığı için (bkz. F-2) üretim gateway'i step-2'de `DualConfirmationRequiredError` ile flash'ı durdurur; bu nedenle bulgu bugün *doğrudan* istismar edilemez, ama F-2 düzeltildiği anda doğrudan istismar edilebilir hale gelir. Çalışma zamanında yeniden üretilmedi.
- **Düzeltme (Remediation):**
  ```python
  # FlashingConfig'e zorunlu, güvenilir doğrulama anahtarı ekle
  trusted_pubkey: ed25519.Ed25519PublicKey | None = None
  require_signature: bool = True
  ...
  # execute_flash başında (fail-closed)
  if config.require_signature:
      if config.firmware_signature is None:
          raise SafetyError("firmware_signature zorunlu (fail-closed).", code="FLASH_SIGNATURE_MISSING")
      if config.trusted_pubkey is None:
          raise SafetyError("Doğrulama açık anahtarı yapılandırılmamış.", code="FLASH_TRUST_ANCHOR_MISSING")
      try:
          config.trusted_pubkey.verify(config.firmware_signature, bytes(config.data))
      except Exception as exc:
          raise SafetyError("Firmware imzası geçersiz — flash reddedildi.",
                            code="FLASH_SIGNATURE_INVALID") from exc
  if config.min_version and current_ecu_version < config.min_version:
      raise SafetyError("Anti-rollback: imaj sürümü tabanın altında.", code="FLASH_ROLLBACK_DENIED")
  ```

### [F-2] Kritik flash/UDS adımları gateway HMAC onay token'ını sunmuyor (fail-closed; legacy bayrağıyla fail-open)
- **Konum:** `src/protocols/uds/flasher.py:474-476` (0x10 EXTENDED), `518-520` (0x10 PROGRAMMING), `603-607` (0x34), `642` (+`188-217` `_call_transfer_data`) (0x36), `676-678` (0x37), `702-706` (0x31 checksum), `770-772` (0x11 reset); `src/protocols/uds/client.py:225-232` (`write_did`), `285-304` (`request_download`), `306-333` (`transfer_data`), `335-356` (`request_transfer_exit`), `378-385` (`start_routine`) — bu metodlar `confirmation_token` parametresini **hiç kabul etmiyor**.
- **Etki / Risk:** Üretim composition root'u gateway'i bir `confirmation_secret` ile kurar (`desktop_app.py:718-721, 757-759`; `_derive_secret` başarısız olursa uygulama fail-closed açılmaz) ve `allow_legacy_boolean_confirm` **vermez** (varsayılan `False`). Gateway Stage-5 (`gateway.py:771-785`) secret varken token'sız kritik komutu reddeder. Flasher hiçbir kritik adımda token sunmadığı için **flash üretimde step-2'de (0x10 0x03) `DualConfirmationRequiredError` ile durur** — yani flash işlevi kullanılamaz (fail-closed, güvenlik açığı değil ama işlevsel kusur). Daha kritik olan: bu plumbing "düzeltilirken" kolayca `allow_legacy_boolean_confirm=True` açılırsa, Stage-5 en kritik yazma yolu (0x34/0x36/0x37) için `user_confirmed` boolean'ına düşer ve T41/G-3'ün kapattığı **onay bypass'ı** geri gelir. Ayrıca `client.py`'nin kritik metodlarının bir kısmı token parametresini almıyor olması, `desktop_app`'in `uds_routine` çağrısının (`client.start_routine(rid, user_confirmed=True)`, `desktop_app.py:1826`) da üretimde fail-closed reddedilmesine yol açar.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # flasher.py:474-476 — token yok
  resp = self.uds_client.change_session(
      DiagnosticSessionType.EXTENDED_DIAGNOSTIC_SESSION, user_confirmed=True
  )
  # flasher.py:204-210 — 0x36 yolu da token'sız
  return self.uds_client.transfer_data(
      block_sequence=block_sequence, data=data,
      is_critical_command=True, user_confirmed=user_confirmed,
  )
  # gateway.py:771-785 (Stage 5)
  if is_critical_command:
      if self._confirmation_secret is not None and confirmation_token is not None:
          self._verify_confirmation_token(confirmation_token, frame)
      if not user_confirmed:
          raise DualConfirmationRequiredError(...)
      if self._confirmation_secret is not None and confirmation_token is None:
          if not self._allow_legacy_boolean_confirm:
              raise DualConfirmationRequiredError(
                  "Critical command rejected: confirmation token required "
                  "(a confirmation secret is configured on this gateway)")
  ```
  Karşılaştırma: `desktop_app.execute_diagnostic_action` 0x14 için `confirmation_token=self._confirm_token_for(...)` iletir (`desktop_app.py:1725`), ancak 0x31 rutin çağrısı iletmez (`1826`). Yani plumbing tutarsız.
- **İstismar / Tetiklenme Senaryosu:** (a) Varsayılan: operatör flash başlatır → bridge token'ı doğrular → flasher step-2 `change_session` token'sız gider → Stage-5 reddeder → flash hiç başlamaz (fail-closed). (b) Eğer bir geliştirici "flash çalışmıyor" diye `allow_legacy_boolean_confirm=True` açarsa, 0x36 flash-yazma adımı yalnızca `user_confirmed` boolean'ına dayanır; renderer bu boolean'ı kendisi üretebildiği için (§2.5 kapsamı dışında kalan onay akışı) kritik yazma kapısı çöker.
- **Düzeltme (Remediation):**
  ```python
  # client.py: her kritik metoda confirmation_token ekle ve ilet
  def request_download(self, memory_address, memory_size, data_format_identifier=0x00,
                       address_and_length_format_identifier=0x44, user_confirmed=False,
                       confirmation_token: bytes | str | None = None) -> UdsResponse:
      ...
      return self._send_and_receive(req_payload, is_critical_command=True,
                                    user_confirmed=user_confirmed,
                                    confirmation_token=confirmation_token)
  # flasher.py: composition root'tan tek-kullanımlık token al (Flasher'a enjekte edilir)
  self._confirm_token = gateway.issue_confirmation_token(client.tx_id, ttl_s=30.0) \
      if gateway._confirmation_secret is not None else None
  resp = self.uds_client.change_session(EXTENDED, user_confirmed=True,
                                        confirmation_token=self._confirm_token)
  ```
  Ayrıca `allow_legacy_boolean_confirm` üretim wiring'inde asla açılmamalı; flasher için token plumbing'i tamamlayıp regresyon testi ekle.

## 4. Doğrulanamayan / Dış Bağımlılıklar

- **Test çalıştırılmadı.** `tests/unit/test_flasher.py`, `tests/unit/test_isotp.py`, `tests/unit/test_j1939_transport.py`, `tests/safety/test_e2e_safety_audit.py`, `tests/e2e/test_challenger_safety_transport.py` yalnızca okundu; geçtikleri iddia edilmiyor. U-1, F-1, F-2 için önerilen regresyon testleri henüz yazılmadı.
- **CAN-FD FF `dlc=15` / `pad_payload` eşlemesi:** `isotp.py` FD çok-kare yollarında `dlc=15` (64 B) kullanıyor ve `length_to_dlc`/`dlc_to_length` (`can_frame.py:14-108`) ile tutarlı görünüyor; ancak gerçek HAL/driver'ın FD DLC kodlamasını (ör. 64 B için 0xF) doğru yorumladığı bu aşamada doğrulanmadı (HAL kapsam dışı).
- **`SafeMultiplexedBus`/`FrameRouter` kanal semantiği:** `client.py`'nin `channel_id` karşılaştırmaları ve `_await_flow_control_sync` "coherent topoloji" mantığı, `SafeMultiplexedBus.channel_id`'nin reconnect sonrası canlı bus'tan türetilmesine (`multiplexer.py:78-81`) dayanır; çok-kanallı topolojilerde gerçek `channel_id` dağılımı ölçülmedi (I-3 için belirleyici).
- **Gateway Stage-5/6 davranışı:** `confirmation_token`/`allow_legacy_boolean_confirm` ve `inbound_triggered` semantiği `gateway.py` okumasına dayanır; F-2 ve U-2'nin fiili etkisi gateway'in üretimdeki tam yapılandırmasına (secret wired, legacy bayrak) bağlıdır ve bu, composition root koduyla **doğrulandı** ama çalışma zamanı davranışı test edilmedi.
- **J1939 TX (CMDT) yolları:** `start_cmdt_transfer`/`advance_cmdt_transfer`/`poll_cmdt_timeouts` için üretim çağırıcısı bulunamadı (`desktop_app.py` yalnızca RX `handle_rx_frame` + `take_pending_tx_frames` kullanıyor); bu nedenle J-1/J-2 **latent** sayıldı. Başka bir ajan/modülün bu API'leri çağırıp çağırmadığı depo genelinde arandı, çağırıcı görülmedi.
- **Firmware imza altyapısı:** `src/protocols/uds/firmware.py` içinde `get_continuous_binary` dışında imza/doğrulama API'si bulunamadı; F-1'in düzeltmesi için güven kökü (embedded public key) ve imzalama akışının ürün gereksinimi olarak tanımlanması gerekiyor (bu aşamada belirsiz).
- **AGENTS.md §2.7 ↔ `flash_start` çelişkisi:** `EcuFlashingEngine` AGENTS.md'de "NOT wired" listesinde ama `desktop_app.py:2036` onu gerçek dala bağlıyor; hangisinin güncel olduğu bu aşamada belirlenemedi (AŞAMA-1 W-1 ile aynı).
- **ETP (J1939-22):** `transport.py:475-483` ETP.CM/ETP.DT'yi tanıyıp WARNING ile düşürüyor; tam durum makinesi kasıtlı olarak uygulanmamış (B-2). Bu bir güvenlik açığı değil, belgelenmiş kapsam boşluğu.
