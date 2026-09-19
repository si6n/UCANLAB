# UCANLAB Kod Review — Aşama 3

- **İncelenen revizyon:** `011b83599836c7f5b9b51c0f946565684d505ca8`
- **Kapsam:** UDS/ISO-TP, J1939 taşıma katmanı, NMEA 2000 Fast Packet, replay parser/player/filter ve reassembly pipeline.
- **Yaklaşım:** Bulgularda kullanılan kaynak dosyalarının tamamı okundu; şüpheli akışlar küçük izole probe'larla doğrulandı; kaynak kod değiştirilmedi.

## Yönetici özeti

Bu aşamada 2 yüksek, 2 orta önem seviyesinde bulgu tespit edildi. En kritik konu, UI replay akışının `ReplaySafetyFilter`'ı hiç çağırmadan canlı ingest hattına bağlanmasıdır. Replay edilen sahte/zararlı TP trafiği, fiziksel hatta protokol cevabı üretilmesine neden olabilir.

## Bulgular

### H1 — UI replay akışı güvenlik filtresini atlıyor ve replay trafiği fiziksel TX cevabı tetikleyebiliyor

**Önem:** HIGH  
**Dosyalar:**

- `src/ui/desktop_app.py:2328-2344`
- `src/ui/desktop_app.py:3170-3218`
- `src/hal/replay/safety_filter.py:17-395`

**Kanıt:**

`start_replay()` doğrudan `ReplayBus.play(callback=self._ingest_live_frame)` çağırıyor. `_ingest_live_frame()` frame'i router'a veriyor, ardından `j1939_tp.handle_rx_frame(frame)` çalıştırıyor ve oluşan cevabı `gateway.validate_and_transmit(...)` ile fiziksel TX yoluna gönderiyor.

`ReplaySafetyFilter` `src` altında tanımlı olmasına rağmen `desktop_app.py` içinde import edilmiyor ve replay başlangıcında `filter_frame()`/`filter_sequence()` çağrılmıyor. Dolayısıyla filtre tek başına doğru davranıyor olsa da uygulamanın gerçek replay giriş noktasında zorunlu değil.

Bu, replay dosyasının doğrudan fiziksel frame göndermesi anlamına gelmez; fakat sahte bir RTS/FF/diagnostic frame'i uygulamayı canlı hatta CTS/ACK/FC gibi cevaplar göndermeye ikna edebilir. Ayrıca replay edilmiş sensör frame'leri ingest/decoder zincirini etkileyebilir.

**Çözüm:**

1. Replay başlatılırken yeni ve varsayılan olarak fail-closed bir `ReplaySafetyFilter` oluşturun.
2. `ReplayBus.play()` callback'ini bir sarmalayıcıya bağlayın:
   - `filter_frame(frame)` çağrılmalı;
   - `None` dönerse frame router'a ve protocol responder'a hiç verilmemeli;
   - yalnızca güvenli frame `_ingest_live_frame()`'e aktarılmalı.
3. Filtre oturum durumunu (özellikle ISO-TP FF/CF ledger'ını) her yeni replay oturumunda sıfırlayın; aynı filtreyi farklı trace'ler arasında taşımayın.
4. Bu kontrolü yalnızca UI'da değil, replay-to-live entegrasyonunun tek choke-point'inde uygulayın. Böylece yeni bir caller filtreyi unutamaz.
5. Filtrelenen frame, sebep ve replay oturum kimliği için audit metriği/log'u ekleyin; filtreleme hatasında replay fail-closed durmalı.

**Ek testler:**

- `start_replay()` üzerinden replay edilmiş J1939 RTS/UDS FF frame'inin `_ingest_live_frame()`'e ulaşmadığını doğrulayın.
- Filtrelenmiş RTS/FF'nin `gateway.validate_and_transmit()` çağrısı oluşturmadığını doğrulayın.
- Güvenli bir telemetry frame'inin replay sırasında hâlâ decoder'a ulaştığını doğrulayın.

---

### H2 — Ambiguous J1939 TP.DT frame'i yanlış PGN oturumuna fail-open yönlendirilebiliyor

**Önem:** HIGH (aynı SA/DA/channel üzerinde paralel oturum açabilen güvenilmeyen bus modeli için)  
**Dosya:** `src/protocols/j1939/transport.py:907-952`

**Kanıt:**

TP.DT frame'i kendi içinde PGN taşımaz. Kod önce `(SA, DA, channel)` adaylarını ve beklenen sequence numarasını kullanıyor. Birden fazla oturum aynı sequence numarasını bekliyorsa:

```python
chosen = max(candidates, key=lambda ks: ks[1].last_activity_time)
```

ile en son aktif oturumu seçiyor. Bu, veri parçasının gerçek PGN sahibi yerine başka bir paralel oturuma eklenmesine izin veriyor. Sonuç, reassembled diagnostic/application payload'ın bozulması veya saldırganın seçimini etkilemesi olabilir.

İzole probe sonucu:

```text
ambiguous_lookup: (16, 249, 'ch0', 65261) 0xfeed
```

Aynı `(SA=0x10, DA=0xF9, channel=ch0, expected_sequence=1)` değerine sahip `0xFEEE` ve `0xFEED` oturumlarında lookup, son aktif olan `0xFEED` oturumunu seçti. Mevcut test yalnızca sequence numaraları farklı olduğunda doğru eşleşmeyi doğruluyor; iki adayın da aynı sequence beklediği güvenlik testi bulunmuyor.

**Çözüm:**

- Sequence eşleşmesi birden fazla aday döndürüyorsa frame'i fail-closed drop edin; ilgili oturumları abort/reap politikanıza göre sonlandırın.
- Daha iyi çözüm: aynı `(SA, DA, channel)` prefix'i altında paralel CMDT oturumlarını kabul etmeyin veya protokolün ayırt edebildiği ek bir transaction identity olmadan ikinci RTS'yi reddedin.
- Telemetriye `ambiguous_dt_drop` metriği ekleyin; “most-recent wins” davranışını kaldırın.
- Test ekleyin: iki aday aynı sequence beklerken TP.DT'nin hiçbir payload'a eklenmediğini, yanlış PGN tamamlanmadığını ve kaynakların temizlendiğini doğrulayın.

---

### M1 — `start_tp_bam_paced()` SAE 50–200 ms kuralına aykırı olarak `interval_s=0` kabul ediyor

**Önem:** MEDIUM  
**Dosya:** `src/protocols/j1939/transport.py:1201-1232`

**Kanıt:**

Docstring ve açıklamalar BAM için TP.DT frame'leri arasında **50–200 ms** pacing gerektiğini söylüyor. Ancak doğrulama şu aralığı kabul ediyor:

```python
0.0 <= interval <= 0.200
```

Probe sonucu `interval_s=0.0` için tüm DT frame'lerinin due timestamp farkı sıfır oldu:

```text
zero_interval_due_deltas: [0.0, 0.0]
```

Bu API'nin çıktısı scheduler tarafından aynen kullanılırsa BAM burst olarak gönderilebilir; yavaş ECU/bridge üzerinde paket kaybı ve tüm transferin sessizce düşmesi riski doğar. Ayrıca `start_tp_bam()` zaten pacing içermeyen ham frame listesi döndürüyor; güvenli fiziksel gönderim yolu bunu doğrudan yayınlamamalı.

**Çözüm:**

- Normal üretim API'si için `0.050 <= interval <= 0.200` zorunlu yapın.
- Sıfır aralıklı davranış yalnızca açıkça adlandırılmış test/simülasyon API'sinde bulunmalı; fiziksel TX path'ine bağlanmamalı.
- `start_tp_bam()` için ham frame listesi yerine scheduler'a bağlı bir gönderim API'si veya en azından “unpaced frames are simulation-only” guard'ı kullanın.
- `interval_s=0`, `0.049`, `0.050`, `0.200` ve `0.201` için sınır testleri ekleyin.

---

### M2 — Replay safety filter, CAN-FD escape Single Frame'de geçersiz `SF_DL=1..7` değerlerini güvenli kabul ediyor

**Önem:** MEDIUM  
**Dosya:** `src/hal/replay/safety_filter.py:177-189`

**Kanıt:**

FD escape Single Frame formatı, classic nibble `0` kullanıldığında 8 byte'tan uzun payload için kullanılmalı. Filtrede şu kontrol var:

```python
if sf_dl < 1 or len(frame.data) < sf_dl + 2:
    return self._UNKNOWN_SID
```

Burada `sf_dl=1..7` de reddedilmeli; mevcut kod bunları geçerli sayıyor. İzole probe sonucu:

```text
malformed_fd_sf_filter: (True, '')
```

Örnek frame, 9 byte CAN-FD data içinde escape `SF_DL=1` taşımasına rağmen filtre tarafından güvenli kabul edildi. ISO-TP receiver tarafında benzer malformed frame'ler reddedilirken replay safety filter'ın farklı davranması parser-policy ayrışması oluşturuyor.

**Çözüm:**

- FD escape SF için `8 <= sf_dl <= 62` koşulunu açıkça zorunlu yapın.
- Koşul sağlanmıyorsa `_UNKNOWN_SID` döndürün; mevcut fail-closed caller bunu bloklasın.
- Filtre ile receiver'ın ortak ISO-TP framing validator kullanması, aynı PCI kurallarının iki yerde farklı uygulanmasını önler.
- `SF_DL=0`, `1`, `7`, `8`, `62`, `63` sınır testleri ekleyin.

---

## Doğrulama

Çalıştırılan hedef testler:

```text
83 passed in 1.00s
```

Çalıştırılan lint:

```text
ruff check ...
All checks passed!
```

Not: Mevcut testlerin geçmesi, özellikle H1 ve H2'nin uygulama entegrasyonu/ambiguous-session senaryolarını kapsadığı anlamına gelmiyor; bu aşamada yeni test eklenmedi, yalnızca izole doğrulama yapıldı.

## Öncelikli uygulama sırası

1. **H1:** Replay filtrelemesini gerçek UI replay giriş noktasında zorunlu hale getirin.
2. **H2:** Ambiguous TP.DT için “most-recent wins” fail-open davranışını kaldırın.
3. **M1:** Fiziksel BAM gönderiminde minimum 50 ms pacing'i enforce edin.
4. **M2:** CAN-FD escape SF uzunluk kontrolünü düzeltin.

Bu aşamada kaynak kodda değişiklik yapılmadı; yalnızca `REVIEW_STAGE_3.md` oluşturuldu.
