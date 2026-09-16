# ASAMA 5: Veri Yolu ve Tamponlar Raporu
Denetçi: scout (Hermes) | Tarih: 2026-09-15
Kapsam: `src/engine/buffer/ring_buffer.py` (306 satır), `src/engine/buffer/rolling_disk.py` (723 satır), `src/engine/decoder/dbc_decoder.py` (474 satır), `src/engine/router.py` (274 satır). Bağlam için okunan ek dosyalar: `src/core/models/can_frame.py`, `src/protocols/j1939/sentinel.py`, `src/safety/multiplexer.py`, `src/engine/pipeline/reassembly_pipeline.py`, `src/ui/desktop_app.py` (composition root: wiring satır 783-798, ingest 2645-2660, telemetry loop 2861-2901, close 3100-3104).

> **Doğrulama yöntemi:** Yalnızca statik kaynak kod okuması (`main` dalı, commit `f151a9f`). Bu oturumda **hiçbir test çalıştırılmadı**, hiçbir bulgu çalışma zamanında yeniden üretilmedi. Her bulguda "Kanıt" satır referansı ile kod alıntısıdır; "Senaryo" kod okumasından türetilmiş bir tetiklenme yoludur. Satır numaraları 1 tabanlıdır ve dosyayla birebir doğrulanmıştır.
>
> **Severity ölçütü (bu rapor için):** KRİTİK = mevcut üretim wiring'inde doğrudan tetiklenebilir veri kaybı/bozulması veya güvenlik ihlali. YÜKSEK = invariant ihlali veya fail-open/fail-silent yol; tetiklenmesi ek koşul (anahtar kaybı, yarış, overload) gerektirir. ORTA = savunma katmanı zayıflığı / metrik tutarsızlığı / sınırlı etki. DÜŞÜK = sınırlı etki veya kod kalitesi. BİLGİ = not.

## 1. Yönetici Özeti

Veri yolu katmanının dört bileşeni de **fail-closed ve kayıt-koruyucu** bir disiplinle yazılmış; önceki denetim turlarının (C-8, C-9, E-C-001, E-C-003, F-33/F-34, L-13, L-16, REVIEW 3) izleri kodda net biçimde görülüyor. Öne çıkan güçlü yönler:

- **HMAC'li, sürüm-kontrollü chunk formatı** (`HEADER_FMT`/`FRAME_FMT`, `CHUNK_VERSION=2`) ve `hmac.compare_digest` sabit-zamanlı karşılaştırma (rolling_disk.py:272) — manipülasyon tespiti sağlam.
- **Atomik, dayanıklı yazma zinciri:** `O_CREAT|O_EXCL` + `.tmp` + `fsync` + `Path.replace` (rolling_disk.py:499-518); ani güç kesintisi bozuk dosya bırakmaz.
- **Ring buffer TOCTOU düzeltmesi** (E-C-001): `get_latest_view(copy=True)` varsayılanı, `n` ve `base_seq`'i **aynı kritik bölümdeki** `total_written` snapshot'ından türetiyor (ring_buffer.py:203-243). Wrap-around dilim mantığını elle izledim (contiguous / `e0==0` / wrapped üç dal), **doğru** çalışıyor.
- **Router copy-on-write snapshot** (C-8): `route_frame()` kilit almadan immutable tuple üzerinden fan-out yapıyor (router.py:142); yavaş tüketici demote mekanizması (M-7) ve callback-only trip sayacı (REVIEW2 #2) RX thread'ini HOL bloklamaya karşı koruyor.
- **DBC decoder J1939 PGN eşleştirme** doğru: PGN maskesi `(id>>8)&0x3FFFF`, PDU1 (`pf<240`) için PS/DA maskeleme `0x3FF00` (dbc_decoder.py:427-429), SA/DA skorlu aday seçimi (dbc_decoder.py:433-467) ve **format uyumu kontrolü** (11-bit mesajın 29-bit kareye eşleşmesini engelleyen `msg_is_ext != is_extended`, dbc_decoder.py:415-420).

Buna karşın **kayıt (black-box) kanalında iki YÜKSEK** ve **eşzamanlılık/metrik yolunda birkaç ORTA** bulgu var; KRİTİK seviyede *mevcut üretim wiring'inde doğrudan tetiklenen* bir bulgu bulamadım. Bunun başlıca nedeni: `RollingDiskBuffer.read_all_stored_frames()` (rolling_disk.py:642) — yani HMAC/bozuk-chunk hatalarının yüzeye çıktığı **tek okuma yolu** — üretim `src/` kodunda **hiçbir yerden çağrılmıyor** (yalnızca `tests/unit/test_remediation_suite_complete.py:274` çağırıyor). Dolayısıyla RD-1/RD-2 bulguları latent'tir ve YÜKSEK olarak sınıflandırılmıştır.

Ana riskler:

1. **RD-1 — HMAC anahtar kaybı sözleşmesi kodla uyuşmuyor (YÜKSEK):** `_get_hmac_key` anahtar kaybolunca sessizce **yeni** anahtar üretir (rolling_disk.py:70-78), ancak eski anahtarla imzalanmış chunk'ları karantinaya alan bir "legacy taraması" **yalnızca eski pickle formatını** (`prefix[0]==0x80`) hedef alır (rolling_disk.py:364-372). Eski-anahtarlı zstd chunk'lar (`0x28 0xB5` sihirli bayt) süpürülmez; sonradan "bozuk/kurcalanmış" muamelesi görür.
2. **RD-2 — Tek bozuk chunk tüm kaydı okunamaz kılar (YÜKSEK, fail-closed DoS):** `read_all_stored_frames(quarantine_corrupt=False)` varsayılanıyla ilk `SecurityError`'da döngüyü `raise` ile keser (rolling_disk.py:662-675). Tek bir legacy/kesilmiş bit, 600 saniyelik kara kutu kaydının **tamamını** erişilemez hale getirir.
3. **RB-1 — `_get_channel_str` kilitsiz sözlük okuması (ORTA):** `_rev_channel_map` `get_latest_frames` içinde kilit dışında okunuyor (ring_buffer.py:74, 260); eşzamanlı `clear()` (ring_buffer.py:287-288) ile yarışta kareler yanlış kanal adıyla (`ch_<n>` fallback) yeniden inşa edilir.
4. **RT-1 — Router metrik sayaçları iki farklı kilitle korunuyor (ORTA):** `_total_routed` `_stats_lock` altında (router.py:143-144), `_total_dropped` ise `_lock` altında (router.py:216-217) artırılırken `stats` her ikisini `_lock` altında okuyor (router.py:269-273) — tutarsız/torn sayaç.

AŞAMA-5 duruşu: Veri bütünlüğü ve tek-yazıcı sıralaması disiplini güçlü; asıl açık **anahtar-kaybı yaşam döngüsünün belgelenen sözleşmeyi uygulamaması** ve **tek-kare arızasının tüm kaydı düşürmesi**dir. Öncelik: RD-1 ve RD-2 için fail-safe kurtarma (eski-anahtar chunk'larını karantinaya alan süpürme + chunk-başına hata izolasyonu), ardından RB-1 için rev-map'i kilit altında snapshot'la.

## 2. Bulgu Tablosu

| # | dosya:satır | severity | sorun | senaryo | düzeltme |
|---|---|---|---|---|---|
| RD-1 | `rolling_disk.py:70-78`, `364-372`, `60-68` | YÜKSEK | HMAC anahtar kaybı sessizce yeni anahtar üretir; eski-anahtarlı chunk'ları süpüren tarama YOK (yalnız eski pickle formatı `0x80` hedefleniyor) → belgelenen "legacy-file sweep" sözleşmesi uygulanmıyor | Vault reset/reinstall → `get_secret` KeyError → yeni anahtar; önceki oturumun zstd chunk'ları (`0x28 0xB5`) diskte kalır → sonraki okumada HMAC mismatch → "kurcalanmış" sanılır | `_get_hmac_key`'e "key epoch" ekle; anahtar ilk kez üretilirken mevcut `chunk_*.bin.zst` dosyalarını doğrulanamaz işaretle ve `legacy_unauthenticated/` altına taşı (format filtresi olmadan) |
| RD-2 | `rolling_disk.py:642-682`, özellikle `662-675`, `676-680` | YÜKSEK | `read_all_stored_frames` (`quarantine_corrupt=False`) ilk `SecurityError`'da `raise` eder → tek bozuk/legacy chunk tüm kayıt okumasını düşürür (fail-closed DoS) | Tek chunk dosyasında bit çürümesi / yarım kalmış replace / eski anahtar → 600 s'lık kara kutunun TAMAMI erişilemez | Varsayılanı `quarantine_corrupt=True` yap; `OSError` dalındaki gibi **chunk başına izolasyon** uygula, sağlıklı chunk'ları döndür, bozukları `*.corrupt` olarak kenara al ve `unreadable_chunks` sayacını raporla |
| RB-1 | `ring_buffer.py:72-74`, `254-277`, `281-289` | ORTA | `_rev_channel_map` kilit dışında okunur; `clear()`/`_intern_channel_unlocked` kilit altında değiştirir → yarışta yanlış kanal adı | `get_latest_frames` snapshot sonrası (kilit bırakıldıktan sonra) `clear()` çalışır → `_get_channel_str` fallback `ch_{n}` döner; kara kutu/dışa aktarımda kanal kimliği yanlış etiketlenir | `get_latest_view` kritik bölümünde `(channel_int → isim)` eşlemesini de snapshot'la; ya da `get_latest_frames` boyunca `self._lock` tut |
| RT-1 | `router.py:143-144`, `216-217`, `269-273` | ORTA | `_total_routed` (`_stats_lock`) ve `_total_dropped` (`_lock`) farklı kilitlerle korunur; `stats` ikisini `_lock` altında okur → tutarsız/torn sayaç | Eşzamanlı route + drop altında `stats()` `total_routed`ı serileştirilmemiş okur; saha telemetrisinde "routed vs dropped" toplamı tutmaz | Tüm metrik mutasyonlarını ve okumalarını tek `_stats_lock` altında topla (kilit sırası: `_lock` → `_stats_lock` korunmalı) |
| RT-2 | `router.py:61`, `183-186`, `197-199`, `245-254` | ORTA | Yorum "immutable copy-on-write snapshot" derken `Subscription` nesneleri **yerinde** mutasyona uğruyor (`callback=None`, `is_demoted=True`, `restore_callback`) — snapshot aynı nesneye referans tuttuğu için COW değil | Demote/restore anında `route_frame` iterasyonu aynı nesneyi gördüğü için yarı-tutarlı durum; snapshot'ın "immutable" garantisi yanlış dokümante edilmiş | Demote/restore'da yeni `Subscription` (dataclass `replace`) üret ve `_rebuild_snapshot_locked()` çağır; ya da yorumu düzeltip yerinde mutasyonun kabul edilen davranış olduğunu belgele |
| DB-1 | `dbc_decoder.py:274-278` | ORTA | `frame_len < msg_def.length` durumunda sessizce `None` döner — sayaç/log yok; sessiz discard | Kesilmiş/kısa CAN karesi (ID doğru, payload kısa) fark edilmeden düşer; "phantom signal yok" iyi ama adli iz yok | Reddedilen kare sayacı (`_truncated_frames`) ve rate-limited WARN ekle; `DecodedMessage` yerine red sebebini çağırana ilet |
| DB-2 | `dbc_decoder.py:312-347`, özellikle `318-320`, `331-347` | ORTA | J1939-71 MSB sentinel kuralı **yalnızca `frame.is_extended`** ile uygulanıyor; NMEA2000 / ISO-TP-üzeri 29-bit kareler de bu yola girer → meşru 29-bit özel sinyaller yanlışlıkla NA/ERROR işaretlenir | 29-bit N2K/özel çerçevedeki 8-bit sinyal 0xFE/0xFF taşırsa "physical max" yerine NOT_AVAILABLE döner (fail-closed ama veri kaybı) | Kapıyı protokole bağla: J1939 (PGN eşleşmeli) veya `frame.source`+DBC mesaj meta verisine göre uygula; N2K için ayrı politika |
| RD-3 | `rolling_disk.py:386-427`, `429-482` | ORTA | Çift-tampon (chunk) modelinde `flush()` çağrısı `self._lock` dışında `_get_hmac_key`+`_serialize_chunk` yapar; `append` `should_flush` kararını kilit içinde verip `flush`'u kilit dışında çağırır → iki thread aynı anda flush edebilir (sıra garantisi `_chunk_index` ile korunur ama dosya adı çakışma penceresi teorik) | İki üretici thread eşzamanlı threshold'a ulaşır → ikisi de `flush` → `_chunk_index` artırımı kilit içinde olduğundan benzersiz; ancak `frames[0].timestamp_ns` boş liste riski yok (kilit içi kontrol) | Belge/iddia netleştir; `flush`'a girişte tek-yazıcı mutex (ayrı `_flush_lock`) ekleyip serileştirmeyi de serileştir |
| RD-4 | `rolling_disk.py:580-590` | DÜŞÜK | `_drain_flush_queue` `unfinished_tasks` üzerinde 5 ms'lik busy-poll; 30 s'ye kadar CPU yakabilir | Yoğun yazımda `read_all_stored_frames`/`flush(drain=True)` yolunda gereksiz CPU | `queue.join()` yerine `threading.Condition`/`Event` ile sinyal tabanlı bekleme |
| RD-5 | `rolling_disk.py:592-623` | DÜŞÜK | `_enforce_retention` dosyaları mtime ile seçer; `stat` başarısız olursa dosya sessizce atlanır (hiç sayılmaz) | İzin hatası olan chunk ne silinir ne sayılır → disk bütçesi sessizce aşılabilir | Başarısız `stat`'ları `total_bytes`'a bilinmeyen olarak ekle/logla; operatör raporu üret |
| RB-2 | `ring_buffer.py:101-105`, `111` | ORTA | `_store_frame_unlocked` `len(data) > 64` ise **sessizce kırpar** (admission'da `CanFrame` zaten DLC'e göre doğruladığından erişilebilmesi için monkey-patch gerekir); truncation sayacı yok | `__post_init__` sonrası mutasyona uğramış/monkey-patch'li kare 64B'ye kırpılır, kayıp görünmez | Kırpma yerine `ValueError` fırlat (tutarlı fail-closed) ya da kırpma sayacı + WARN ekle |
| RB-3 | `ring_buffer.py:281-289` | DÜŞÜK | `clear()` `_head`/`_total_written`'ı sıfırlar → `sequence` etiketleri yeniden başlar; devam eden okuyucu `base_seq` ile tutarsız etiket üretebilir | Canlı akışta `clear()` (desktop_app.py:2470) sırasında eşzamanlı `get_latest_frames` → negatif/çakışan `sequence` | `clear()` sonrası monotonik bir "epoch" sayacı tut; `sequence = epoch*K + base_seq + offset` |
| RT-3 | `router.py:120-130`, `81-118` | DÜŞÜK | `unsubscribe` çalışırken in-flight `route_frame` snapshot'ı eski `Subscription`'ı tutmaya devam eder (belgelenen davranış) → unsubscribe sonrası birkaç kare teslim edilebilir | Abone iptal edildikten sonra callback'e 1-2 kare daha gelir; kuyruğa ise `put_nowait` hâlâ çalışabilir | Kabul edilen davranış ise belgeyi netleştir; değilse unsubscribe'ta `sub.retired=True` bayrağı koyup route_frame atsın |
| DB-3 | `dbc_decoder.py:96-104`, `469-471` | DÜŞÜK | `_message_cache` `msg=None` sonucunu da önbelleğe alır — bu doğru (negatif önbellek); ancak `_lookup_message` fallback lineer taraması (402-407) büyük birleşik DBC'lerde O(messages) ve yalnız **ilk** miss'te pahalı | Çok-DBC senaryoda bilinmeyen ID her oturum başında lineer tarama; sonra LRU ile hızlanır | Kabul edilebilir; büyük DBC'de frame-id indeksini `add_dbc_file` sonrası yeniden kur (B-22 notu ile uyumlu) |
| RT-4 | `router.py:207-209` | BİLGİ | Her in-budget callback'te `if self._callback_trip_counts:` kontrolü ve gerekirse `_lock` alımı — sıcak yolda ekstra dallanma | — | Trip sayacı hiç dolu değilse dallanma ucuz; not düzeyinde |
| RD-6 | `rolling_disk.py:713-723` | BİLGİ | `clear()` önce `_drain_flush_queue` bekler ama timeout halinde worker hâlâ yazabilir; glob ile silinen dosya sonradan "yeniden doğabilir" | 10 s drain timeout + yavaş disk → `clear()` sonrası bir chunk geri gelir | `clear()`'da `_closed` benzeri bir "clearing" bayrağı ile worker'ın o pencere yazımlarını düşürmesini sağla |

*Severity: KRİTİK | YÜKSEK | ORTA | DÜŞÜK | BİLGİ*

## 3. Detaylı Bulgular (Tüm KRİTİK ve YÜKSEK Seviyeler)

> Bu aşamada KRİTİK seviyede bulgu **yoktur**. Aşağıda iki YÜKSEK bulgu ayrıntılı ele alınmıştır.

### [RD-1] HMAC anahtar kaybı sözleşmesi kodla uyuşmuyor — eski-anahtarlı chunk'lar süpürülmüyor
- **Konum:** `src/engine/buffer/rolling_disk.py:60-68` (`_get_hmac_key` docstring), `70-78` (anahtar üretimi), `364-372` (`_migrate_legacy_chunks` format filtresi), `338` (init çağrısı).
- **Etki / Risk:** Docstring açıkça "the legacy-file sweep at startup moves them out of the active store instead of serving them" der. Ancak gerçek süpürme, **birinci çözülmüş bayt çifti** `0x80, ≤0x05` olan dosyaları (eski Python-pickle formatı) hedefler. HMAC **anahtar kaybından** (reinstall/vault reset) sonra eski anahtarla imzalanmış yeni-format chunk'lar zstd sihirli sayısı `0x28 0xB5` ile başlar; bu filtreden **geçemez**, yani süpürülmez. Sonuç: anahtar kaybında recorded telemetry "kurcalanmış" olarak sınıflandırılır ve (RD-2 ile birlikte) tüm okuma düşer. Bu, belgelenen kayıt-bütünlüğü sözleşmesinin uygulanmamasıdır.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # rolling_disk.py:70-78  — anahtar kaybı SESSİZCE yeni anahtar üretir
  try:
      key = secret_provider.get_secret(HMAC_KEY_NAME)
  except (KeyError, SecurityError):
      key = os.urandom(32)                       # eski anahtar gitti, yeni anahtar
      try:
          secret_provider.store_secret(HMAC_KEY_NAME, key)
      except Exception as store_err:
          logger.warning("Failed to store rolling disk HMAC key", ...)
      logger.info("Initialized rolling disk HMAC key")
  ...
  # rolling_disk.py:364-372  — "legacy sweep" YALNIZCA eski pickle formatını hedefliyor
  def _migrate_legacy_chunks(self) -> None:
      legacy_dir = self.storage_dir / "legacy_pickle"
      for path in sorted(self.storage_dir.glob("chunk_*.bin.zst")):
          try:
              prefix = self._read_uncompressed_prefix(path, 2)
          except (OSError, zstd.ZstdError):
              continue
          if len(prefix) != 2 or prefix[0] != 0x80 or prefix[1] > 5:
              continue                            # <- eski-anahtarlı zstd chunk BURADA atlanır
  ```
  Yani `_hmac_key` yeniden üretildiğinde eski chunk'lar süpürülmez; `_deserialize_chunk` (rolling_disk.py:271-273) HMAC mismatch'te `SecurityError` fırlatır.
- **Tetiklenme Senaryosu:** (1) Kara kutu kaydı çalışırken bir oturum zstd chunk'ları eski anahtarla yazar. (2) OS/vault anahtarı kaybolur (uygulama yeniden kurulur, secret store sıfırlanır). (3) `RollingDiskBuffer.__init__` → `_migrate_legacy_chunks` (eski chunk'lar `0x28` prefix'iyle atlanır) → `_get_hmac_key` yeni anahtar üretir. (4) `read_all_stored_frames` eski chunk'ı okur → HMAC mismatch → `SecurityError`. Bu, RD-2 ile birleşince tüm kayıt erişilemez olur.
- **Düzeltme (Remediation):**
  ```python
  def _get_hmac_key(secret_provider: SecretProvider) -> tuple[bytes, bool]:
      """Returns (key, key_was_minted)."""
      try:
          key = secret_provider.get_secret(HMAC_KEY_NAME)
          return key, False
      except (KeyError, SecurityError):
          key = os.urandom(32)
          secret_provider.store_secret(HMAC_KEY_NAME, key)
          return key, True

  # init'te: yeni anahtar üretildiyse MEVCUT tüm chunk'ları (format filtresi OLMADAN)
  # doğrulanamaz olarak karantinaya al:
  def _quarantine_unverifiable_chunks(self) -> None:
      legacy_dir = self.storage_dir / "unauthenticated_prior_key"
      for path in sorted(self.storage_dir.glob("chunk_*.bin.zst")):
          legacy_dir.mkdir(parents=True, exist_ok=True)
          dest = legacy_dir / path.name
          i = 1
          while dest.exists():
              dest = legacy_dir / f"{path.name}.{i}"; i += 1
          path.replace(dest)
          logger.warning("Quarantined chunk signed by a previous HMAC key", extra={"file": str(path)})
  ```
  Anahtar-epoch etiketi (ör. chunk header'a imzalanan anahtar parmak izi) eklenirse süpürme kesinleşir; aksi halde format filtresi tamamen kaldırılıp "anahtar ilk kez üretildiyse tüm mevcut chunk'ları taşı" kuralı uygulanmalı.

### [RD-2] Tek bozuk/legacy chunk tüm kara kutu okumasını düşürür (fail-closed DoS)
- **Konum:** `src/engine/buffer/rolling_disk.py:642-682`, özellikle `662-675` (`quarantine_corrupt` dalı) ve `676-680` (`OSError` izolasyonu).
- **Etki / Risk:** `read_all_stored_frames(quarantine_corrupt=False)` varsayılanıyla ilk `SecurityError`'da `raise` eder. Tek bir chunk dosyasındaki bit çürümesi, yarım kalan `replace` (RD-3/güç kesintisi penceresi), veya RD-1'deki eski-anahtar durumu → 600 saniyelik kara kutu kaydının **tamamı** okunamaz. `OSError` dalı ise chunk-başına izolasyon uygular (loglayıp devam eder) — yani kod, bütünlük hatası ile IO hatası arasında **asimetrik** davranır ve bütünlük hatasını daha sert (tüm kaydı öldüren) ele alır. Adli inceleme (post-incident forensic) tam da bu okuma yoluna bağlı olduğundan, tek-kare arızasının tüm kanıtı düşürmesi kabul edilemez.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # rolling_disk.py:653-680
  for file in sorted(self.storage_dir.glob("chunk_*.bin.zst")):
      try:
          if file.stat().st_size > self.MAX_STORED_FILE_BYTES:
              raise SecurityError(...)
          raw_bytes = self._decompress_bounded(file.read_bytes())
          all_frames.extend(_deserialize_chunk(raw_bytes, key))
      except SecurityError as sec_exc:
          if quarantine_corrupt:
              logger.critical("Quarantining corrupt or untrusted rolling disk chunk", ...)
              ...
              continue
          # Re-raise to ensure tamper detection tests pass
          raise                                  # <- VARSAYILAN: tüm okuma ölür
      except OSError as exc:
          logger.error("Failed to read rolling disk chunk", ...)   # <- bu yol İZOLE eder
  ```
  Not: Bu yol üretim `src/` kodunda **çağrılmıyor** (yalnız `tests/unit/test_remediation_suite_complete.py:274`); latent. Yine de kayıt bütünlüğü invariant'ı (§ "Recorded telemetry is never silently trusted") hem fazla agresif (tek chunk → tümü gider) uygulanıyor.
- **Tetiklenme Senaryosu:** (1) Disk yüzeyinde tek chunk dosyasında tek-bit çürüme veya kesilme. (2) Operatör/otomasyon kara kutu dışa aktarımı için `read_all_stored_frames()` çağırır. (3) İlk bozuk chunk `SecurityError` → `raise` → elde tek bir sağlıklı kare bile yok. Adli kanıt kaybı.
- **Düzeltme (Remediation):**
  ```python
  def read_all_stored_frames(self, quarantine_corrupt: bool = True) -> list[CanFrame]:
      ...
      for file in sorted(self.storage_dir.glob("chunk_*.bin.zst")):
          try:
              if file.stat().st_size > self.MAX_STORED_FILE_BYTES:
                  raise SecurityError("... exceeds MAX_STORED_FILE_BYTES", code="SECURITY_ERROR")
              raw_bytes = self._decompress_bounded(file.read_bytes())
              all_frames.extend(_deserialize_chunk(raw_bytes, key))
          except SecurityError as sec_exc:
              # Her zaman izole et: bozuk chunk'ı kenara al, sağlıklıları döndür.
              logger.critical("Quarantining corrupt or untrusted chunk",
                              extra={"file": str(file), "error": str(sec_exc)})
              self._unreadable_chunks += 1
              try:
                  file.replace(file.with_suffix(file.suffix + ".corrupt"))
              except OSError:
                  pass
          except OSError as exc:
              logger.error("Failed to read rolling disk chunk", extra={"file": str(file), "error": str(exc)})
              self._unreadable_chunks += 1
      if self._unreadable_chunks:
          logger.error("Rolling disk read completed with unreadable chunks",
                       extra={"unreadable": self._unreadable_chunks})
      return all_frames
  ```
  Tamper-detection testleri çağrı tarafında `quarantine_corrupt=False` geçerek katı davranışı açıkça isteyebilir; üretim okuma yolu ise izole etmeli.

## 4. Doğrulanamayan / Dış Bağımlılıklar

- **Test çalıştırılmadı.** `tests/unit/test_remediation_suite_complete.py` (ve `tests/` altındaki ilgili ring/router/decoder testleri) yalnızca okundu; geçtikleri veya bulguları yakaladıkları iddia edilmiyor. RD-1, RD-2, RB-1, RT-1 için önerilen regresyon testleri henüz yazılmadı.
- **`read_all_stored_frames` üretim çağrısı bulunamadı:** `grep -rn "read_all_stored_frames" src/` yalnız `rolling_disk.py` tanımını verdi; üretim dışa aktarım yolunun bu metodu mu yoksa ayrı bir reader'ı mı kullandığı `src/engine/exporters/**` içinde incelenmedi. RD-2'nin fiili etkisi buna bağlı (latent olduğu varsayımı bu grep'e dayanır).
- **HMAC anahtar store davranışı:** `src/safety/secret_provider.py` içindeki `get_secret`'in hangi koşullarda `KeyError` vs `SecurityError` fırlattığı ve "key epoch"/parmak izi desteğinin olup olmadığı bu aşamada incelenmedi; RD-1 düzeltmesinin uygulanabilirliği buna bağlı.
- **zstd `stream_reader` bozuk-veri semantiği:** `_decompress_bounded` (rolling_disk.py:625-640) `reader.read(self._max_chunk_bytes + 1)` sonucu ve `ZstdError` yükseltmesi statik okumayla doğrulandı; kesilmiş bir zstd stream'inin `read()` sırasında mı yoksa `read(1)` ikinci çağrısında mı hata verdiği çalıştırılmadı (RD-2 senaryosunun hangi istisnaya düştüğünü etkiler).
- **NMEA2000 / genişletilmiş çerçeve protokol ayrımı (DB-2):** `frame.is_extended`'ın J1939 dışı 29-bit trafiği (N2K, ISO-TP-on-extended) içerip içermediği `src/protocols/nmea2000/**` ve canlı yolda `_decode_j1939_signal` (desktop_app.py:2609) incelenerek kesinleştirilmedi; bu nedenle DB-2 ORTA olarak bırakıldı.
- **`Subscription` yerinde mutasyonunun (RT-2) fiili etkisi:** Demote/restore anında yarı-tutarlı durumun pratik zararı, frontend/protokol abonelerinin `restore_callback` çağrı sıklığına bağlı; `src/ui/frontend/**` ve `desktop_app.py`'nin `restore_callback` çağrıları incelenmedi.
- **`get_latest_view` wrap mantığı** üç dal için elle izlendi (contiguous, `e0==0` tam-dolu, wrapped) ve doğru bulundu; ancak `capacity=1` gibi sınır ve `count=capacity` tam-dolu durumu **çalıştırılarak** doğrulanmadı (yalnız akıl yürütme). `n==0` erken dönüşü (ring_buffer.py:207-208) doğru.
- **Performans sınırları:** `_drain_flush_queue` busy-poll maliyeti, `route_frame` sıcak yol maliyeti ve `_lookup_message` lineer fallback süresi ölçülmedi; yalnız kod okumasına dayanır.
