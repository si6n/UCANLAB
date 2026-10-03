# Denetim Backlog'u — 2026-10-01 (+ 2026-10-03 aşamalı denetim)

Bu dosya, `docs/audit/AUDIT_REPORT.md` denetiminde bulunup bu dalda **düzeltilmeyen**
maddeleri listeler. Her madde için kanıt ve önerilen düzeltme verilir. Öncelik sırası:
YÜKSEK → ORTA → DÜŞÜK.

## YÜKSEK

Açık YÜKSEK madde yok. Bulunan YÜKSEK bulguların hepsi düzeltildi (rapordaki tabloya bakın).

## ORTA

### B-01 — Üretim DBC kataloğunda opendbc test dosyası
- **Kanıt:** `data/dbc/passenger/test.dbc` (`CM_ "This DBC is used for the CAN parser and packer tests."`),
  `data/dbc/catalog.json:1653`, `data/dbc/manifest.json:1653`. Dosya `0xE4 STEERING_CONTROL`
  (Honda adresi) gibi gerçek araç kimlikleriyle çakışan test mesajları içeriyor.
- **Etki:** Katalog bu dosyayı gerçek araç DBC'si gibi sunuyor. Yanlış eşleşmede canlı trafiğe
  test sinyal adları basılabilir.
- **Öneri:** Dosyayı katalogdan ve `manifest.json`'dan çıkarın, `lint_dbc.py`'ye "CM_ test fixture"
  reddi ekleyin. Veri silme kararı olduğu için sahibine bırakıldı.

### B-02 — Katman sınırı ihlalleri ve iki God-module
- **Kanıt (üst katmana import):**
  - `src/security/cloud/license_flow.py:86` → `src.ui.desktop_app._app_data_root`
  - `src/launcher/auth.py:84,262` → `src.ui.desktop_app`
  - `src/engine/ai/user_kb.py:126,131` → `src.launcher.paths`, `src.ui.desktop_app`
  - `src/protocols/j1939/oem/*.py` → `src.engine.decoder.dbc_decoder.DecodedSignal`
  - `src/hal/virtual.py:12-14` → `src.protocols.uds.*` (simüle ECU HAL içinde)
  - `src/safety/multiplexer.py:16` → `src.engine.router`
- **God-module:** `src/ui/desktop_app.py` (~7000 satır), `src/engine/ai/diagnostic_copilot.py` (~7300 satır).
- **Öneri:** `_app_data_root` / `_resolve_cloud_base_url` / loopback auth sunucusunu `src/core`
  veya `src/launcher/paths.py` altına taşıyın. `DecodedSignal`'ı `src/core/models`'e alın.
  `UdsServerEcu`'yu `src/hal/virtual.py`'den `src/engine/connection/` altına taşıyın.
  `desktop_app.py`'yi köprü (bridge), bağlantı, teşhis eylemleri ve flash olarak bölün.
  Bu davranış değişikliği olmayan ama geniş bir refaktör; ayrı PR'larda yapılmalı.

### B-03 — UI uçtan uca testleri CI'da hiç koşmuyor
- **Kanıt:** `tests/ui_e2e/test_mechanic_ui.py:26`, `tests/ui_e2e/test_workbench_ui.py:24` Playwright
  yoksa atlanıyor. CI (`.github/workflows/ci.yml`) Playwright kurmuyor. `test_t2_7_attribution_render.py`
  (5 test) `node_modules` yoksa atlanıyor; Python işleri `npm ci` çalıştırmıyor.
- **Öneri:** Linux işine `npm ci` + `pip install playwright` ekleyin. Ortamda önceden kurulu
  Chromium varsa `executablePath` ile kullanın.

### B-04 — Ön yüzün birim testi yok
- **Kanıt:** `src/ui/frontend/package.json` içinde `test` script'i yok; `src/ui/frontend/src` altında `*.test.*` yok.
- **Öneri:** Vitest + React Testing Library ile köprü (`services/bridge.ts`) ve onay akışlarını kapsayın.

### B-05 — Veri koşullu testler mevcut veriyle boşta kalıyor
- **Kanıt:** `test_t66_procedures_full_wiring.py:236`, `test_t67_exhaustive_answers.py:233,270`,
  `test_t69_multi_source_procedures.py:252`, `test_t69g_multi_dtc_vehicle_context.py:193`,
  `test_t80e_geekobd_revert.py:120,131,230` "kayıt bulunamadı" diye atlanıyor.
- **Etki:** Bu yollar şu anki veriyle hiç doğrulanmıyor.
- **Öneri:** Her test için küçük bir sentetik fixture kullanın; gerçek veriye bağımlı kalmasın.

### B-06 — ISO-TP TX kapasitesi 4096 bayt
- **Kanıt:** `src/protocols/uds/isotp.py` "classic cap 4096 (fail-closed)". ISO 15765-2:2016,
  klasik CAN'de de 32-bit FF_DL ile 4095 baytın üstüne izin verir.
- **Öneri:** Politika gereği ise belgeleyin; değilse FF escape yolunu TX için de açın.

### B-07 — Gerçek donanımla doğrulanmamış yollar
- PCAN / Kvaser / RP1210 / Vector sürücüleri, DPAPI ve WebView2 bu denetimde **çalıştırılamadı**
  (Linux konteyneri, donanım yok). ECU flash yolu yalnızca simüle ECU (`UdsServerEcu`) ile uçtan
  uca doğrulandı. Gerçek bir ECU üzerinde `docs/product/HARDWARE_TEST_CHECKLIST.md` uygulanmalı.

## DÜŞÜK

### B-08 — Uygulama veri kökü iki farklı yere çözülüyor
- **Kanıt:** `src/engine/ai/user_kb.py:126` dondurulmuş (frozen) modda `src.launcher.paths.app_data_root()`
  kullanıyor; bu `%LOCALAPPDATA%\UCANLab Launcher` (`src/launcher/paths.py:48`). Masaüstü uygulamasının
  geri kalanı `%LOCALAPPDATA%\UniversalCAN` (`src/ui/desktop_app.py:_app_data_root`) kullanıyor.
- **Öneri:** Operatör geri bildirimini `_app_data_root()` altına alın (B-02 ile birlikte).

### B-09 — J1939 CMDT alıcı bekleme süresi
- **Kanıt:** `src/protocols/j1939/transport.py:1028-1030` alıcı tarafta CTS sonrası T4 (1050 ms)
  kullanıyor. SAE J1939-21'de CTS gönderen alıcı için T2 = 1250 ms, paketler arası T1 = 750 ms.
- **Öneri:** Gerçek ECU kayıtlarıyla doğrulayıp T1/T2 ayrımına geçin.

### B-10 — Git geçmişinde döndürülmüş API anahtarı
- **Kanıt:** gitleaks (varsayılan kural seti, allowlist'siz) geçmişte 4 `generic-api-key` buluyor:
  `.clinerules`, `.orca.md`, `scripts/obsidian_api.py` (commit `801b01c2`), `scripts/verify_auth_callback_fix.py`
  (commit `4d25c781`). `.gitleaks.toml` bunları "döndürüldü" notuyla izinli listeye alıyor.
  HEAD temiz. Döndürmenin gerçekten yapıldığı bu denetimde **doğrulanamadı**.
- **Öneri:** Anahtar sahibinin döndürmeyi teyit etmesi yeterli. Geçmişi yeniden yazmak geri
  alınamaz bir işlem olduğu için yapılmadı.

### B-11 — Depo boyutu ve Git LFS
- **Kanıt:** Çalışma ağacında `data/` 435 MB; en büyükleri `data/traces/marine/canboat_samples/*.raw|*.all`
  (77 MB, 45 MB, 25 MB). Paketlenmiş `.git` 68 MB. `scripts/check_corpus_size_sentry.py` PASS veriyor.
- **Öneri:** Yeni büyük izler için `.gitattributes` ile LFS kuralı ekleyin. Mevcut geçmişi LFS'e taşımak
  geçmişi yeniden yazar; geri alınamaz olduğu için yapılmadı.

### B-12 — Lint kapsamı dar
- **Kanıt:** `pyproject.toml [tool.ruff.lint] select = ["E","W","F","I","B"]`. `S` (bandit), `UP`, `SIM`
  kuralları kapalı; bandit ayrı koşuyor ama yalnızca `-ll` (orta ve üstü).
- **Öneri:** `UP` ve `RUF` kurallarını kademeli açın.

### B-13 — Seçmeli RoutineControl çağrıları
- **Kanıt:** `UdsClient.stop_routine` 0x31'i kritik bayrağı olmadan gönderiyor
  (`src/protocols/uds/client.py`). Geçit (gateway) 0x31'i içerikten kritik sayıyor; üretim
  geçidinde operatör onayı olmadan reddedilir (güvenli yön). Şu an uygulamada çağıran yok.
- **Öneri:** Kullanılacaksa `start_routine` ile aynı token parametrelerini ekleyin.

## 2026-10-03 aşamalı denetimden ertelenenler

Kaynak: `docs/audit/AUDIT_2026-10-03.md`. Kimlikler raporla aynı.

### B-14 (YÜKSEK) — Terfiden sonra kırmızı kalan 13 test (B0-09)
- **Kanıt:** `29a4465` 9 SPN ekledi (4291 → 4300) ve intake'teki Wal33D OEM ile SPN referans
  kayıtlarını emekliye ayırdı. Kırmızı testler:
  `test_t48_t44_recovery.py::TestProductionDatabase::test_spn_count_frozen`,
  `test_t80k_j1939_desc_source.py::TestDescriptionProvenance::test_spn_count_frozen`,
  `test_benchmark_ai_copilot.py::TestCatalogShape::test_production_catalog_sizes_match_contract`,
  `tests/safety/test_e2e_safety_audit.py::test_audit_database_integrity_and_scale`,
  `test_j1939_v190_load.py` (`test_cold_load_latency_and_memory`, `test_explicit_path_same_content`,
  `test_all_spns_have_name_and_title_tr`), `test_intake_kb_defects.py::test_readiness_split_is_consistent`,
  `test_intake_scan_sources.py` (`test_staged_batch_passes_the_intake_gate_and_never_writes_the_kb`,
  `test_staged_oem_divergences_are_re_measured_by_the_gate`, `test_oem_divergence_record_rejects_a_count_mismatch`,
  `test_validator_rejects_two_sources_without_corroborated_confidence`).
- **Neden ertelendi:** düzeltme ya test beklentisini değiştirmek (sayı 4300, emekliye ayrılmış
  dosya yerine sahte ağaç/başka kayıt) ya da 9 SPN için Türkçe başlık (`title_tr`) yazmak
  demek. Bu oturumda test beklentisi değiştirme izni verilmedi.
- **Öneri:** sahibi sayıyı 4300'e çeksin (mevcut "T_cd363b33: 4282->4291" yorum düzenini izleyerek),
  9 SPN'e `title_tr` eklesin, intake testlerini emekli kayıtlar yerine `_fake_wal33d` benzeri
  geçici ağaçlarla kursun.

### B-15 (ORTA) — E-Stop yeniden tetiklenince challenge korunuyor (S1-04)
- **Kanıt:** `src/safety/estop.py:640-654`; davranış `tests/unit/test_remediation_suite_complete.py:69-73`
  ile kilitli.
- **Etki:** ilk arıza için basılmış reset token'ı sonraki arızayı da siler; operatör yeni nedeni görmeden onaylamış olur.
- **Öneri:** her tetikte yeni challenge (fail-closed). Testin beklentisi sahibinin onayıyla güncellenmeli.

### B-16 (DÜŞÜK) — Safety gizli kusurları (S1-07…S1-11)
- S1-07: kritiklik türetimine Extended/Mixed ISO-TP adresleme desteği (ya da bu modlar için TX reddi).
- S1-08: E2E mühürlemesini kritiklik kontrolünden önce yap ya da mühürlenmiş çerçeveyi yeniden sınıfla.
- S1-09: AUTOSAR E2E P1 varyant eşlemesini spesifikasyonla doğrula (şüpheli).
- S1-10: reset yetkisini ayrı süreçten (araç/servis) sağla; `reset_authority_provider` belgeli istisna.
- S1-11: onay ve arm token TTL'ine üst sınır (ör. 120 s).

### B-17 (YÜKSEK) — Kod imzalama anahtarı lisans anahtarından ayrılmalı (S2-01, S2-07)
- **Kanıt:** `src/launcher/app.py:178,193-201` güncelleme ikilisini ve manifesti
  `DEFAULT_EMBEDDED_CLOUD_PUBLIC_KEY_B64` / `TRUSTED_CLOUD_PUBLIC_KEYS_B64` ile doğruluyor;
  aynı halka lisans ticket'larını doğruluyor. Bulutta özel anahtar API sürecinin ortam
  değişkeninde (`backend/app/core/config.py:32`).
- **Öneri:** çevrim dışı tutulan ayrı bir kod imzalama anahtarı ve ayrı bir gömülü halka;
  bulutta `/updates/latest` ucu (şu an yok) bu anahtarla önceden imzalanmış manifesti
  yalnız servis etsin. Bulut sözleşmesi değişir, iki repo birlikte ele alınmalı.

### B-18 (DÜŞÜK) — Launcher/lisans gizli kusurları (S2-03…S2-06, S2-08)
- S2-03: hedef ikiliyi yazma-paylaşımsız tutamaçla doğrula ve çalıştır.
- S2-04: manifeste `expires_at` / artan `sequence` ekle (bulut sözleşmesi).
- S2-05: ölü `LicenseValidator`, knowledge pack ve evidence chain kodunu kaldır ya da
  `LicenseFlow`'un fail-closed HWM kurallarına eşitle.
- S2-06: HWM'ye artan sayaç; mümkünse sunucu tarafı son görülme zamanı.
- S2-08: TX/flash yeteneklerini lisans katmanına (tier/feature) bağlama kararı.

### B-19 (KRİTİK) — RP1210 mesaj biçimini RP1210C'ye göre yeniden yaz (S3-04)
- **Kanıt:** `src/hal/rp1210/bus.py:397-403` (TX), `:560-654` (RX). Standart biçim için
  bağımsız referans: `github.com/dfieschko/RP1210` `RP1210/J1939.py`.
- **Şu anki durum:** frozen sürüm RP1210'u reddediyor (`RP1210_WIRE_FORMAT_UNVERIFIED`);
  kaynak kod koşumunda eski davranış (testler için) sürüyor.
- **Öneri:** J1939 protokolünde 29-bit ID ↔ (PGN, öncelik, SA, DA) dönüşümü; okuma tarafında
  4 bayt zaman damgası + echo; CAN protokolünde tip + big-endian ID. J1939 modunda adaptörün
  kendisi TP yapar ve adres koruma (`Protect J1939 Address` komutu) ister — uygulamanın kendi
  TP/adres-talebi katmanıyla çakışmayı tasarla. `tests/unit/test_rp1210.py` ve ilgili testler
  yeni biçime göre güncellenmeli; `docs/product/HARDWARE_TEST_CHECKLIST.md` ile gerçek
  adaptörde doğrulanmadan frozen engeli kaldırılmamalı.

### B-20 (DÜŞÜK) — HAL/protokol gizli kusurları (S3-06…S3-11)
- S3-06: aracın J1939 NAME işlev kodunu J1939 Ek B'ye göre seç (teşhis aracı, motor değil).
- S3-07: sıfır seed → anahtar gönderme, "zaten açık" diye devam et.
- S3-08: kurtarmada `0x10 0x01` için de token bas.
- S3-09: ayrılmış STmin için 127 ms (ISO 15765-2:2016 §9.6.5.5); testlerle birlikte.
- S3-10: replay filtresinin J1939 kümelerini `criticality.py`'den türet.
- S3-11: `send_functional` çok çerçeveli yükü reddetsin.

### B-21 (ORTA) — Copilot/engine kalan kusurları (S4-02…S4-04)
- S4-02: eski `ask_copilot` köprü uç noktasını kaldır ya da `query_copilot_structured`'a
  yönlendir; `CausalBayesianInferenceEngine` sorgu-anahtar kelimesinden DTC uydurmasın.
- S4-03: "ölçülmedi" ile "ölçülen 0 / eksi değer"i ayrı tut (`_live_telemetry_snapshot`).
- S4-04: kara kutu parçalarına sıra numarası ve önceki parçanın MAC'iyle zincir.

### B-22 (DÜŞÜK) — Çekirdek adlandırma ve sabitler (S5-02, S5-03)
- `src/core/exceptions.py` → `src/core/transport_errors.py` (eski adı uyumluluk için yeniden dışa aktararak).
- Bulut User-Agent'ını `__version__`'dan üret (sunucu UA kontrolü yapmıyorsa).

### B-23 (YÜKSEK) — Flash güven çapası kullanıcıdan gelmemeli (S6-02)
- **Kanıt:** `src/ui/desktop_app.py:5233-5242` (`_parse_flash_pubkey` köprü yapılandırmasından),
  `src/ui/frontend/src/components/workbench/EcuView.tsx:133-134`.
- **Öneri:** atölye/OEM anahtarlarını uygulama dışında (yönetici adımı, imzalı paket) sabitlenmiş
  bir depoya ekle; `trustedPubkey` yalnız bu depodaki bir anahtarın parmak izi olabilsin; native
  onay penceresi imajın SHA-256'sını ve anahtarın sahibini/parmak izini göstersin.

### B-24 (ORTA) — Geliştirme bağımlılıkları için hash kilidi (S7-01)
- `requirements-dev.txt` artık üst sınırlı, ama release işi hâlâ hash'siz paket kuruyor.
- **Öneri:** `scripts/generate_lock.py` ile `requirements-dev.lock` üret, CI'da
  `--require-hashes` ile kur; release işinde test araçlarını derleme ortamından ayır.
