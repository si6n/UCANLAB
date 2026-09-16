# ASAMA 3: Çevrimdışı AI Motoru Raporu

Denetçi: Scout (Claude) | Tarih: 2026-09-15
Kapsam: `src/engine/ai/diagnostic_copilot.py` (4038 satır), `src/engine/ai/evidence_gate.py` (137 satır), `src/engine/ai/anomaly_detector.py` (225 satır), `src/engine/ai/hypothesis_engine.py` (255 satır). Bağlam için okunan dosyalar: `src/engine/ai/golden_similarity.py`, `src/engine/ai/session_report.py`, `src/engine/ai/user_report_composer.py`, `src/engine/ai/discriminating_tests.py`, `src/core/models/diagnostics.py`, `src/ui/desktop_app.py` (bridge + session plumbing), `data/diagnostics/telemetry_thresholds.json`, `data/diagnostics/root_cause_graph.json`, `data/diagnostics/dtc_database.json`, `data/diagnostics/j1939_spn_fmi_database.json`, `tests/unit/test_anomaly_detector.py`.

> **Doğrulama yöntemi:** Statik kaynak kod okuması + veri dosyalarının salt-okunur şema/envanter sayımı. **Hiçbir test çalıştırılmadı**, hiçbir bulgu çalışma zamanında yeniden üretilmedi. Veri tespitleri tek seferlik read-only probe betikleriyle yapıldı ve betikler silindi (repoda iz bırakılmadı). Satır numaraları 1 tabanlıdır ve dosyayla birebir doğrulandı.
>
> **Severity ölçütü (bu rapor için):** KRİTİK = mevcut üretim wiring'inde doğrudan tetiklenebilir (bridge → motor çağrı yolu; DM1/oturum akışı). YÜKSEK = invariant ihlali veya fail-open; tetiklenmesi ek koşul gerektirir. ORTA = savunma katmanı zayıflığı / determinizm sapması / belgelenmiş-ama-bağlanmamış yarım bırakılmış yol. DÜŞÜK = sınırlı etki veya kod kalitesi. BİLGİ = not.

## 1. Yönetici Özeti

Aşama 3 motoru, istenen üç temel hedefte de **yapısal olarak sağlam**: (a) **Çevrimdışı izolasyon tamdır** — dört kapsam dosyasında `socket`/`requests`/`urllib`/`httpx`/`aiohttp`/`ssl`/`smtplib`/`ftplib` yok; `subprocess`/`eval`/`exec`/`__import__` yok; yalnız `json`, `re`, `sys`, `threading`, `time`, `pathlib`, `dataclasses`, `typing`, `enum` stdlib importları var. Tek ağ izi `src/ui/desktop_app.py:14` (`import urllib.parse`) olup kapsam dışıdır ve **yalnız URL ayrıştırma** için kullanılır (`urlsplit`, satır 362/416/2348 — ağ çağrısı yok). Bu, AGENTS.md §2.3 ve PROMPT-3 air-gap gereksinimini karşılar. (b) **Determinizm disiplini iyidir** — anomali taraması `sorted(per_signal)` üzerinden, hipotez sıralaması `(-score, id)` ile, DTC sıralaması `(timestamp_ns, code)` ile deterministiktir; kural/veri tabanlı DTC-SPN eşleşmesi `EXPERT_KNOWLEDGE_BASE` ve J1939 DB'ye karşı yapılır ve **eşleşme yoksa uydurma yerine dürüst "bulunamadı" metni** döner (diagnostic_copilot.py:2704-2875). (c) **Fail-closed veri doğrulama tutarlıdır** — `anomaly_detector._validate_threshold_payload` ve `hypothesis_engine._validate_graph_payload` bilinmeyen alanı reddeder, `source_ref` zorunlu kılar ("uydurma yok"); `VehicleSession`/`SignalSample`/`DiagnosticEvent` non-finite değeri ve geçersiz durumu constructor'da reddeder (diagnostics.py:58, 84).

Buna karşın **üretim wiring'i ile motor arasında ciddi bir kopukluk** tespit edildi. Bridge, DM1'den gelen aktif arızaları `DiagnosticEvent.code = "SPN 84 FMI 4"` biçiminde üretir (desktop_app.py:992) ve `analyze_session`'a **yalnız `{"code": ..., "spn": None, "fmi": None}`** geçirir (desktop_app.py:1071-1072). Motorun oturum yolu ise SPN eşleşmesini `d.get("spn")` üzerinden kurar (diagnostic_copilot.py:3887, 3893) — bu her zaman `None` olduğundan **J1939 DB'ye düşen yol hiç çalışmaz** ve canlı DM1 kodları için zengin KB (3.910 SPN) sessizce kaybedilir. PROMPT-3'ün "veri tüketim eksikliği" odak noktasıyla doğrudan örtüşen bu bulgu, kodun kendi yorumuyla (satır 3889-3891: "canli DM1 akisindan gelen SPN'ler … J1939 DB'sine dus") **çelişir** — belgelenen davranış kod düzeyinde doğru değil.

İkinci yüksek-etkili konu, operatör beyanı (`OP:` öneki) işaretlemesinin **üretimde ölü kod** olmasıdır. `anomaly_detector` `synthetic = name.startswith("OP:")` ile sentetik bayrağı yalnızca **isim eşleşmesi** (`thresholds.get(name) or thresholds.get(_camelize(name))`) sağlandığında set eder; `OP:EngineOilPressure` hiçbir eşleşme üretmediğinden bulgu da sentetik bayrak da oluşmaz — test bunu birebir doğrular (`test_anomaly_detector.py:108-117`). Sonuç: operatör beyanı ne anomali raporuna ne de hipotez yarı-ağırlıklandırmasına ulaşabilir; FAZ 3.2 "operatör beyanı yarı ağırlık" mekanizması (hypothesis_engine.py:191-195) hiç tetiklenmez. Ağırlıklandırma kodu doğru; onu besleyen giriş bağlı değil.

Ayrıca hipotez başlığının **kanıt olmadan sıfır skorla** listeye girmesi (hypothesis_engine.py:211-213), `diagnostic_copilot.evaluate_diagnostic_query` semptom yollarının veritabanında **olmayan DTC'ler için de rapor gövdesi** üretmesi (satır 2884 `P0AA1`, 2902-2907 `N2K_*`, 2911-2912 `CAN_*` — bu anahtarlar DB'de yoksa `KeyError` ya da "bulunamadı" yolu) ve `except` bloklarıyla `sysexit` sessizce yutulması gibi ORTA seviye konular mevcuttur.

ASIL-B/D duruşu: teşhis motoru **kontrollü/rehberlik katmanıdır, aktüasyon değildir** — ürettiği çıktı yalnız rapor ve (onay gerektiren) öneri butonlarıdır; TX güvenlik kapısı `src/safety/` katmanındadır ve bu motoru bypass etmez. Bu nedenle bulguların çoğu "yanlış/eksik teşhis" riskidir, doğrudan araç güvenliği değil. Ancak motorun ürettiği `CopilotActionTrigger`'lar (ör. `make_uds_ecu_reset_action`, `make_j1939_dm11_action`) UI üzerinden canlı hatta komut tetiklediğinden, **yanlış kod eşleşmesi → yanlış aksiyon butonu** zinciri izlenebilirlik açısından ASIL-B hedefini zayıflatır. Öncelik sırası: A3-1 ve A3-2 (üretim wiring'i — doğrudan), ardından A3-3/A3-4 (fail-open/kanıtsız skor).

## 2. Bulgu Tablosu

| # | dosya:satır | severity | sorun | senaryo | düzeltme |
|---|---|---|---|---|---|
| A3-1 | `desktop_app.py:1071-1072` + `diagnostic_copilot.py:3887,3893` | KRİTİK | Bridge `spn`/`fmi`'yi sabit `None` geçirir; motorun J1939 fallback yolu (`d.get("spn")`) hiç açılmaz → canlı DM1 SPN'leri KB/DB'ye ulaşmaz | Canlı DM1'de `SPN 84 FMI 4` → `code="SPN 84 FMI 4"`, `spn=None` → `spn_candidate=""`, `code_candidate` KB'de yok → j1939_entry `None` → hiçbir KB zenginliği basılmaz | `dtc_payload`'a `e.code`'dan SPN/FMI ayıkla (`"SPN <n> FMI <m>"` regex) ve `spn`,`fmi` doldur |
| A3-2 | `anomaly_detector.py:149,169` | YÜKSEK | `synthetic = name.startswith("OP:")` yalnız isim eşleşmesi sağlanınca ulaşılır; `OP:` öneki camelize/DB anahtarıyla eşleşmez → beyan hiç bulgu/sentetik üretmez | `OP:EngineOilPressure` örnekleri → `entry is None` → `continue`; FAZ 3.2 yarı-ağırlık (hypothesis_engine.py:193) tetiklenmez; operatör beyanı rapora sızmaz | Öneki çöz: `base = name[3:] if name.startswith("OP:") else name`; `entry = thresholds.get(base) or thresholds.get(_camelize(base))`; bulguyu `signal=name, synthetic=True` ile üret |
| A3-3 | `hypothesis_engine.py:210-213` | YÜKSEK | Benzer-vaka katkısı **kanıt eşiği olmadan** eklenir: sıfır DTC/anomali kanıtı olan düğüm de pozitif skor alır | Aktif kod/anomali düğümü eşleşmese de `similar_cases` boş değilse her düğüme `≤0.20` skor → ilgisiz hipotez tabloya girer | Vaka katkısını yalnız DTC veya sinyal eşleşmesi **varsa** ekle (`if dtc_hits or signal_hits: score += ...`) |
| A3-4 | `diagnostic_copilot.py:3879-3880` | YÜKSEK (fail-open) | İlişkili-küme/rezerve-kod bloğu geniş bir `except Exception: pass` ile sarılı; hata sessizce yutulur | Kümeleme/DB erişiminde beklenmedik hata → rezerve-kod uyarısı ve ortak-kök-neden korelasyonu sessizce kaybolur, rapor "yok" der | `except (OSError, ValueError, KeyError) as exc:` ile daralt + `logger.warning` |
| A3-5 | `diagnostic_copilot.py:2884,2902-2912` | ORTA | Semptom dalları `EXPERT_KNOWLEDGE_BASE`'de **koşulsuz** anahtar okur (`P0AA1`, `N2K_*`, `CAN_*`); anahtar yoksa `KeyError` → rapor üretilemez | DB'de `P0AA1` yok (root_cause_graph'ta da yok) + "precharge/kontaktor" sorgusu → `_format_4stage_technician_report("P0AA1")` → `KeyError` | Dalları `if key in EXPERT_KNOWLEDGE_BASE` ile koru veya `get_reserved_code_notice` benzeri fail-safe yola düşür |
| A3-6 | `diagnostic_copilot.py:215` | ORTA | Action-trigger parse `except Exception: pass` — bozuk/uyumsuz `<!--ACTIONS:-->` JSON'u sessizce kaybolur ve `extract_action_triggers` fallback'i devreye girer | Elle düzenlenmiş/bozulmuş rapor metni → JSON parse hatası → tüm yapısal aksiyon blokları düşer, serbest-metin taraması yanlış buton üretebilir | `except json.JSONDecodeError as exc:` ile daralt + `logger.warning` |
| A3-7 | `anomaly_detector.py:143-144` | ORTA | RPM bağlamı **son** `EngineSpeed` örneğidir; `_band_for` eşleşen band bulamazsa `None` döner → tüm sinyal sessizce atlanır | 900 RPM (800-1800 arası boşluk) → `_band_for` `None` → yağ basıncı anomali taraması hiç yapılmaz ("veri yok" ≠ "anomali yok" ama rapor ayırt etmez) | Eşleşmezse en yakın bandı seç veya "band-belirsiz, değerlendirilmedi" gap'i rapora ekle |
| A3-8 | `anomaly_detector.py:113-117` | ORTA | `rpm is None` iken **ilk band** kullanılır (yorumda "ponytail: rpm-join upgrade path"); kayıtta `EngineSpeed` yoksa yanlış bantla karşılaştırma | `EngineSpeed` hiç kaydedilmemiş oturumda yağ basıncı → her zaman 1.0 bar bandı; 2000 RPM'de 2.5 bar gerçek basınç "normal" sayılır | RPM yoksa band-bağımsız en geniş aralığı kullan veya bulguyu "rpm bağlamı yok" notuyla işaretle |
| A3-9 | `evidence_gate.py:106-109` | ORTA | DISCOVERED oranı 0.5 **üzeri** olmalı; tam %50 sınırı geçer; ortalama-güven kontrolü ayrı ve ikisi de `> 0.5`/`< 0.5` sınırında eşitlik kayırır | %50 DISCOVERED + ortalama güven 0.5 → hiçbir gap yok → `anomaly_sufficient=True`, düşük-güvenli veri yeterli sayılır | Sınırları `>=` / `<=` yönünde fail-closed seç (güven için `mean_conf <= MIN`) |
| A3-10 | `diagnostic_copilot.py:2447` | ORTA | Levenshtein mesafesi yalnız **uzunluk farkı > 1** için erken çıkış; `_levenshtein_distance` transpozisyon dahil tam mesafe hesaplar ama çağrı `<= 1` eşiğiyle domain skoruna `0.8` katkı verir | Tek harf/transpozisyon yazım hatası ("misfire"↔"misfrie") beklenmeyen domain eşleşmesi → yanlış semptom raporu | Eşiği gözden geçir; transpozisyonu yalnız iki tam eşleşme arasında kabul et |
| A3-11 | `diagnostic_copilot.py:3799` | DÜŞÜK | Boost normalizasyonu `raw_boost > 10.0` eşiğiyle kPa/bar ayrımı yapar; 10 kPa (0.1 bar) altındaki değer "bar" sanılır | Nadir düşük kPa girdisi yanlış bant → overheat/turbo senaryosu tetiklenmez | Birim alanını telemetri ile taşı (kPa/bar ayrımını implicit eşikten kaldır) |
| A3-12 | `diagnostic_copilot.py:3015,3069,3088` | DÜŞÜK | `_format_multi_dtc_combined_report`/`_format_4stage_technician_report` içindeki metinler ham KB alanını 90/160/220 karakterde keser; kesme `[:90]` sonrası yarım Türkçe kelime/UTF-8 sınırı | Uzun `title`/`description` → cümle ortasında kesilmiş metin, okunabilirlik düşer | Kesmeyi kelime sınırında yap (`textwrap.shorten` benzeri) |
| A3-13 | `diagnostic_copilot.py:1544` | DÜŞÜK | `get_mode06_database` ve benzeri 5 accessor hiçbir çağrı yolu tarafından kullanılmıyor (ölü API) | `obd_mode06_database.json` (88 KB) hiç tüketilmez — model zenginleştirme fırsatı kaçar | Karar: ya bir rapor/analiz yoluna bağla ya da ölü kodu kaldır |
| A3-14 | `diagnostic_copilot.py:2696-2701` gibi | BİLGİ | Bazı telemetri dalları `telemetry.get(...) is None` → "Canlı ölçüm bekleniyor" dürüst metni basar; uydurma yok (olumlu) | — | — |
| A3-15 | `hypothesis_engine.py:234` | BİLGİ | Normalizasyon `best = max(r ...) or 1.0`; `max==0.0` senaryosu `score<=0` filtresi (satır 215) nedeniyle erişilemez — savunma amaçlı doğru | — | — |

*Severity: KRİTİK | YÜKSEK | ORTA | DÜŞÜK | BİLGİ*

## 3. Detaylı Bulgular (Tüm KRİTİK ve YÜKSEK Seviyeler)

### [A3-1] Bridge, SPN/FMI alanlarını sabit `None` geçirir — motorun J1939 KB yolu üretimde ölü
- **Konum:** `src/ui/desktop_app.py:1071-1072` (payload üretimi), `src/ui/desktop_app.py:992` (event kodu), `src/engine/ai/diagnostic_copilot.py:3886-3900` (oturum yolunun SPN çözümü).
- **Etki / Risk:** Canlı DM1 akışından kaydedilen her aktif arıza, motorun `analyze_session` yolunda **hiçbir zengin KB/DB bilgisiyle eşleşmez**. `EXPERT_KNOWLEDGE_BASE` (14.352 DTC) ve `j1939_spn_fmi_database` (3.910 SPN) yalnızca sorgu (chat) yolunda çalışır; oturum/rapor yolu (FAZ 2..6, `get_diagnostic_analysis`) zenginleştirmeden yoksun kalır. Teşhis kalitesi ve izlenebilirlik (ASIL-B: kanıt zinciri) düşer; kullanıcı kartı jenerik metne döner.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # desktop_app.py:971-996  (DM1 -> event)
  session.events.append(
      DiagnosticEvent(
          timestamp_ns=ts,
          code=f"SPN {spn} FMI {fmi}",      # <- tek bilgi kaynağı: code string
          domain=DiagnosticDomain.HEAVY_DUTY,
          severity=severity,
          status="ACTIVE",
      )
  )
  # desktop_app.py:1071-1075  (analyze_session payload)
  dtc_payload = [
      {"code": e.code, "spn": None, "fmi": None}   # <- spn/fmi KAYBEDİLİR
      for e in session.events
      if e.status == "ACTIVE"
  ]
  report = self.copilot.analyze_session(dtc_payload, {}, [])
  ```
  ```python
  # diagnostic_copilot.py:3886-3900  (oturum yolu)
  code_candidate = str(d.get("code", "")).upper()          # "SPN 84 FMI 4" — KB anahtarı DEĞİL
  spn_candidate = f"SPN{d.get('spn')}" if d.get("spn") else ""   # d.get("spn") is None -> ""
  match_key = code_candidate if code_candidate in EXPERT_KNOWLEDGE_BASE else (spn_candidate if spn_candidate in EXPERT_KNOWLEDGE_BASE else None)
  j1939_entry = None
  if match_key is None and d.get("spn"):                   # None -> hiç girmez
      ...
  ```
  `DiagnosticEvent` modeli ayrı `spn`/`fmi` alanı **taşımaz** (diagnostics.py:70-87; yalnız `code: str  # \"P0201\" | \"SPN 84 FMI 4\" | ...`). Yani bilgi yalnız `code` metnindedir ve hiçbir yerde ayrıştırılmaz. Kodun kendi yorumu (satır 3889-3891) bu yolun çalıştığını iddia eder; iddia **doğru değildir**.
- **İstismar / Tetiklenme Senaryosu:** (1) Canlı hat, PGN 65226 (DM1) üzerinden `SPN 84 FMI 4` yayınlar. (2) `_record_dm1_events` → `DiagnosticEvent(code="SPN 84 FMI 4", status="ACTIVE")`. (3) `get_diagnostic_analysis()` → `dtc_payload=[{"code":"SPN 84 FMI 4","spn":None,"fmi":None}]`. (4) `analyze_session` → `_analyze_local_expert`: `code_candidate="SPN 84 FMI 4"` KB'de yok, `spn_candidate=""`, `j1939_entry=None` → KB dalı atlanır → `likely_causes` boş → jenerik "CAN veri yolunda aktif hata kodları kaydedildi" fallback'i (satır 3965-3974). 3.910 kayıtlık SPN bilgisi (causes/steps/fmi anlamı) hiç görünmez. Çalışma zamanında yeniden üretilmedi; kod okumasıyla zincir doğrulandı.
- **Düzeltme (Remediation):**
  ```python
  # desktop_app.py — kayıp alanları code'dan geri kazan
  _SPN_RE = re.compile(r"SPN\s+(\d+)(?:\s+FMI\s+(\d+))?", re.I)
  def _spn_fmi(code: str) -> tuple[int | None, int | None]:
      m = _SPN_RE.search(code or "")
      return (int(m.group(1)), int(m.group(2)) if m and m.group(2) else None) if m else (None, None)
  dtc_payload = []
  for e in session.events:
      if e.status != "ACTIVE":
          continue
      spn, fmi = _spn_fmi(e.code)
      dtc_payload.append({"code": e.code, "spn": spn, "fmi": fmi})
  ```
  Regresyon testi: kaydedilmiş bir DM1 event'i için `analyze_session` çıktısında `affected_subsystems` J1939 alt-sistem adını ve `likely_causes` DB nedenini içermeli.

### [A3-2] Operatör beyanı (`OP:`) sentetik işaretlemesi üretimde ulaşılamaz — FAZ 3.2 yarı-ağırlık ölü
- **Konum:** `src/engine/ai/anomaly_detector.py:149` (anahtar çözümü), `:169` (`synthetic` bayrağı); tüketicisi `src/engine/ai/hypothesis_engine.py:191-195` (yarı ağırlık); kayıt yolu `src/ui/desktop_app.py:1006-1041`.
- **Etki / Risk:** `anomaly_detector` `synthetic` bayrağını yalnızca **isim eşleşmesi sağlandıktan sonra** set eder; `OP:EngineOilPressure` hiçbir DB anahtarına (`EngineOilPressure` / `_camelize` çıktısı) eşleşmez. Dolayısıyla operatör beyanı (a) anomali bulgusu üretmez, (b) `AnomalyFinding(synthetic=True)` hiç oluşmadığı için hipotez motoru yarı-ağırlık indirimini (satır 193, `SYNTHETIC_EVIDENCE_FACTOR`) uygulayamaz. FAZ 3.2 "operatör beyanı = yarı kanıt" kuralı ve plan §Riskler (beyanın teşhisi domine etmemesi) üretimde etkisizdir.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # anomaly_detector.py:147-152
  for name in sorted(per_signal):
      samples = per_signal[name]
      entry = thresholds.get(name) or thresholds.get(_camelize(name))   # "OP:EngineOilPressure" -> None
      if entry is None:
          continue                                                       # bulgu üretilmeden çıkılır
  # anomaly_detector.py:169
  synthetic = name.startswith("OP:")   # buraya yalnız eşleşme varsa gelinir -> OP: asla
  ```
  Kilitli test bunu birebir sabitler (`tests/unit/test_anomaly_detector.py:108-117`): `OP:EngineOilPressure` için `assert findings == []` ve yorum: *"OP-prefixed oil pressure is not mapped; the honest behavior is NO finding"*. Kayıt yolu ise öneki üretir (`desktop_app.py:1026`: `prefixed = clean if clean.startswith("OP:") else f"OP:{clean}"`). Üretici (`OP:` ekle) ile tüketici (base adıyla eşleştir) **aynı anahtarı paylaşmaz**.
- **İstismar / Tetiklenme Senaryosu:** Teknisyen chat'ten "yağ basıncı 0.2 bar ölçtüm" der → `record_operator_measurement("EngineOilPressure", 0.2)` → örnek `OP:EngineOilPressure` adıyla kaydedilir. `detect_anomalies` bu sinyali atlar. Sonuç: tehlikeli derecede düşük basınç **anomali olarak görünmez** ve beyan, hipotez skorunda yarı ağırlıkla değil — yok olarak — yer alır. (Rapordaki "operatör beyanı" etiketi hiçbir zaman basılmaz.) Çalışma zamanında yeniden üretilmedi; test + kod okumasıyla doğrulandı.
- **Düzeltme (Remediation):**
  ```python
  # anomaly_detector.py — öneki çöz, sonra eşleştir; synthetic bayrağını KORU
  for name in sorted(per_signal):
      samples = per_signal[name]
      is_synthetic = name.startswith("OP:")
      base = name[3:] if is_synthetic else name
      entry = thresholds.get(base) or thresholds.get(_camelize(base))
      if entry is None:
          continue
      ...
      synthetic = is_synthetic      # name.startswith("OP:") yerine
  ```
  Böylece beyan hem bulgu üretir hem de `hypothesis_engine` yarı-ağırlık indirimini uygular. Regresyon testi: `OP:EngineOilPressure` 5 örnek + RPM bağlamı → `findings[0].synthetic is True` ve skoru aynı sinyalin `EngineOilPressure` adıyla kaydından **daha düşük** olmalı.

### [A3-3] Benzer-vaka katkısı kanıt eşiği olmadan eklenir — kanıtsız hipotez skor kazanır
- **Konum:** `src/engine/ai/hypothesis_engine.py:210-213`; karşılaştırma için erken çıkışlar `:184-195` ve `:215`.
- **Etki / Risk:** Bir düğümün DTC eşleşmesi ve sinyal eşleşmesi **olmasa bile**, `similar_cases` listesi boş değilse döngü her düğüme `WEIGHT_CASE_MATCH * similarity ≤ 0.20` katkı ekler. Bu, "her doğrulanmış benzer vaka, aynı arızayı işaret ederse pozitif" tasarımını **pratikte "herhangi bir benzer vaka varsa her düğüm pozitif"** haline getirir. Kullanıcı raporu/hipotez tablosu, aktif DTC ile nedensel bağı olmayan düğümlerle dolar; "kanıtsız hipotez rapora sızar" riski (PROMPT-3 Evidence Gate odak noktası) gerçekleşir.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # hypothesis_engine.py:178-213
  for node in graph:
      support, contradict, score = [], [], 0.0
      dtc_hits = node.norm_dtcs & active_codes
      if dtc_hits:
          score += WEIGHT_DTC_MATCH                       # koşullu
      signal_hits = sorted(set(node.evidence_signals) & anomaly_signals)
      if signal_hits:
          score += WEIGHT_SIGNAL_MATCH * factor           # koşullu
      ...
      for case_id, match in sorted(case_faults.items()):
          score += WEIGHT_CASE_MATCH * min(1.0, match.similarity)   # KOŞULSUZ
          support.append(f"benzer doğrulanmış vaka: {case_id} (%{match.similarity * 100:.0f})")
          break
      if score <= 0.0:
          continue
  ```
  `case_faults` yalnız `similar_cases` (doğrulanmış golden vakalar) dolduğunda doludur ve `find_similar_cases` (desktop_app.py:1066) bunu her analizde çağırır. Yani tek bir benzer vaka, **hiçbir aktif DTC/anomali eşleşmesi olmayan düğümleri** de tabloya taşır.
- **İstismar / Tetiklenme Senaryosu:** Oturumda aktif DTC `P0300` (misfire); golden corpus'ta yakın bir vaka var. `misfire-ignition` düğümü doğru şekilde `0.40` alır; ancak örneğin `hvil-open` düğümü DTC ve sinyal eşleşmesi sıfır olmasına rağmen `WEIGHT_CASE_MATCH * similarity` kadar skor alır (örn. `0.20 * 0.9 = 0.18`) → normalizasyon sonrası tabloda "Yüksek elektrik izolasyon kaybı" hipotezi belirir. Kullanıcı, aktif kodla ilgisiz bir kök-neden önerisiyle karşılaşır. Çalışma zamanında yeniden üretilmedi.
- **Düzeltme (Remediation):**
  ```python
  # hypothesis_engine.py — vaka katkısını yalnız BAĞIMSIZ kanıt varsa ekle
  has_independent_evidence = bool(dtc_hits) or bool(signal_hits)
  if has_independent_evidence:
      for case_id, match in sorted(case_faults.items()):
          score += WEIGHT_CASE_MATCH * min(1.0, match.similarity)
          support.append(...)
          break
  ```
  Regresyon testi: yalnız `similar_cases` dolu, DTC/anomali boş → `rank_hypotheses(...) == []` (veya yalnız `dtc_hits/signal_hits` olan düğümler).

### [A3-4] İlişkili-küme/correlation bloğu geniş `except Exception: pass` ile fail-open sarılı
- **Konum:** `src/engine/ai/diagnostic_copilot.py:3841-3880` (özellikle `:3879-3880`).
- **Etki / Risk:** Aktif kod kümesinin ortak alt-sistem/PGN kümelemesi ve **rezerve kod uyarısı** bu tek `try` içindedir. Beklenmedik bir hata (`KeyError`, `TypeError`, DB erişimi) oluşursa blok tamamen atlanır ve `pass` ile yutulur: korelasyonlar ve rezerve-kod uyarısı **sessizce** kaybolur. Kullanıcı raporu "ilgili küme yok / rezerve kod yok" olarak basar — bu, raporun *yanlış negatif* üretmesidir (fail-open). AGENTS.md §2.3 ruhuna (kanıtsızlığı "yok" diye sunma) aykırıdır.
- **Kanıt / Zafiyet Analizi:**
  ```python
  # diagnostic_copilot.py:3841-3880
  try:
      ...
      for grp, desc in _RELATED_CODE_GROUPS:
          if grp <= active_set:
              ...
      _clusters = analyze_active_dtc_clusters(active_dtcs)
      if len(_clusters["codes"]) >= 2:
          for _sub, _members in _clusters["subsystem_groups"][:5]:
              ...
          for _pgn, _members in _clusters["pgn_groups"][:4]:
              ...
      for _rc in _clusters["reserved_codes"][:8]:
              ...
  except Exception:      # <- tüm blok sessizce düşer
      pass
  ```
  `analyze_active_dtc_clusters` içinde `EXPERT_KNOWLEDGE_BASE.get(c)` ve `_t42_lookup_dtc_fields` gibi DB okumaları vardır (satır 2263-2315); bunlar hata verirse tek bir log satırı dahi üretilmez. Aynı dosyada başka bir yer (satır 3149-3175) bilinçli olarak dar `except Exception as exc: logger.debug(...)` kullanır — yani sessiz-yutma tek bu blokta tutarsızdır.
- **İstismar / Tetiklenme Senaryosu:** Bozulmuş/eksik `j1939_spn_fmi_database.json` (bu depoda `.bak_*` yedekleri ve sık güncellenen DB mevcut) → `_t42_lookup_dtc_fields` içinde beklenmedik tip → exception → `pass`. Sonuç: çoklu-DTC oturumunda paylaşılan PGN/subsystem kümeleri ve **rezerve (üreticiye özel) kod uyarısı** kaybolur; kullanıcı genel-geçer sanıp parça değişimine yönelebilir — tam da P2-6'nın önlemek istediği yanlış teşhis. Çalışma zamanında yeniden üretilmedi.
- **Düzeltme (Remediation):**
  ```python
  except (OSError, KeyError, TypeError, ValueError) as exc:
      logger.warning("DTC cluster/correlation enrichment skipped", extra={"error": str(exc)})
  ```
  Ya da her alt blok kendi dar `except`'i ile korunmalı ve rezerve-kod uyarısı, bağımsız bir fail-safe yol olarak `analyze_active_dtc_clusters`'tan ayrılmalı (tek hata tüm zenginleştirmeyi düşürmesin).

## 4. Doğrulanamayan / Dış Bağımlılıklar

- **Testler çalıştırılmadı.** `tests/unit/test_ai_copilot.py`, `test_anomaly_detector.py`, `test_evidence_gate.py`, `test_hypothesis_engine.py`, `test_offline_ai_reasoning.py`, `test_desktop_evidence.py` yalnızca okundu/varlığı görüldü; geçtikleri iddia edilmiyor. Bu ortamda `python -c`/`python -m` çalıştırma kısıtlıydı ve `src.engine` paketi `zstandard` modülü olmadan import edilemedi (`src/engine/buffer/rolling_disk.py:16`) — motorun tam import zinciri bu ortamda **doğrulanamadı**; bu nedenle bulgular yalnız statik okumaya dayanır. Veri envanteri/sayımları bağımsız read-only probe'larla yapıldı ve probe dosyaları silindi.
- **`UdsClient` / flasher tarafı:** `src/protocols/uds/client.py` ve flasher'ın `is_critical_command`/`user_confirmed` bayrak disiplini incelenmedi (AŞAMA 1 kapsamı; burada yalnız motorun ürettiği `CopilotActionTrigger`'lar gözlendi).
- **Frontend (`src/ui/frontend/**`):** `CopilotActionTrigger`'ların renderer'da nasıl gösterildiği ve onay akışının ikinci kanala bağlı olup olmadığı incelenmedi; A3-1'in "yanlış buton" etkisi bu yüzeye bağlı.
- **Golden corpus (`golden_cases.py`, `golden_similarity.find_similar_cases`):** A3-3'ün fiili etkisi, depodaki doğrulanmış vaka sayısına ve `similarity` dağılımına bağlıdır; bu dosyalar ve `data/` altındaki golden trace içerikleri **sayılmadı/incelenmedi**.
- **`_UDS_KNOWN_SIDS` kapsamı:** `explain_can_packet`'ın SID çözümünde (satır 386-413) yer alan SID listesi ile gerçek ECU desteklediği servisler karşılaştırılmadı; yanlış SID eşleşmesi olasılığı **belirsiz**.
- **Veri tüketim oranları (PROMPT-3 §2 Veri Tüketim):** `dtc_database.json` (14.352 kayıt) alan envanteri çıkarıldı (title/subsystem/severity/causes/steps %100; symptoms 14.166; measurement/uds_routine 9.300; oem_variants 2.911; nhtsa_evidence 233; j1939_spn_fmi 697; is_reserved 390). `j1939_spn_fmi_database.json` (3.910 SPN): fault_matrix %100, causes 3.710, steps 3.444, procedures_full 59. **Ancak motorun her alanı hangi oranda okuduğu satır-satır eşlenmedi**; yalnız `EVIDENCE_URL`/`_source` gibi provenance alanlarının rapora girmediği gözlendi. Tam tüketim matrisi bu aşamada üretilmedi — A3-13 yalnızca açıkça ölü accessor'ları işaretler.
- **`sys._MEIPASS` / frozen build yolu:** `_resolve_external_data_dir`, `_resolve_thresholds_path`, `_resolve_graph_path` üçlüsünün PyInstaller paketindeki gerçek dosya düzeninde doğru çalıştığı **doğrulanamadı** (frozen build artefaktı incelenmedi).
- **`diagnostic_copilot.py:2884 P0AA1` anahtarının DB'de varlığı:** root_cause_graph'ta yok; `dtc_database.json` içinde olup olmadığı **belirsiz** (14.352 kayıt anahtar listesinin tamamı `SPN` içermeyen P/B/U/C kodları; `P0AA1` bir EV kodudur ve inline KB'de tanımlı görünmedi). A3-5'in fiili tetiklenmesi buna bağlıdır; iddia "koşullu" olarak işaretlenmiştir.
- **Eşzamanlılık:** `AiDiagnosticCopilot` singleton değeri `StatisticsReport` benzeri mutasyon içermez; ancak `_CACHED_*` global cache'lerin çok-thread'li bridge çağrılarında yarış durumu (lock yok) **test edilmedi** — `_DTC_DB_LOAD_LOCK` yalnız DTC DB'si için mevcut, diğer 5 cache için `data_path is None` yolunda kilit yok.
