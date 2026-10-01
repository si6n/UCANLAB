# UCANLAB Tam Denetim Raporu — 2026-10-01

Dal: `audit/full-2026-10-01` (taban: `main` @ `e16d48a`). Açık kalan işler: `docs/audit/BACKLOG.md`.

## 1. Yönetici özeti

- **KRİTİK bulgu yok.** TX yolunun tek choke-point'ten (`TxSafetyGateway`) geçtiği doğrulandı;
  HEAD'de sır sızıntısı, güvensiz deserialization veya shell injection bulunmadı.
- **En önemli sorun: üretimde kritik özellikler çalışmıyordu.** Masaüstü uygulaması geçide her
  zaman bir onay sırrı (confirmation secret) bağlıyor. Bu geçitte **ECU programlama (flash)**,
  **DTC silme**, **oturum değiştirme**, **ECU reset** ve **rutin başlatma** her seferinde
  reddediliyor ya da `TypeError` ile çöküyordu. Sistem güvenli yönde kilitleniyordu (istenmeyen
  TX yok), ama özellikler kullanılamıyordu. Birim testleri sahte (stub) issuer ve geçit
  kullandığı için bunu göstermiyordu. Düzeltildi ve gerçek geçit + simüle ECU ile tam bir
  imzalı flash uçtan uca doğrulandı.
- **Canlı veri kayıpları:** J1939 VIN hiç algılanmıyordu. NMEA 2000 vites (PGN 127493) veya
  "veri yok" değeri taşıyan her çerçeve, o turdaki 200'e kadar çerçeveyi ve kara kutu kaydını
  düşürüyordu. Düzeltildi.
- **Tip denetimi hiç koşmuyordu:** `pyproject.toml` mypy strict tanımlıyor ama hiçbir CI adımı
  çalıştırmıyordu; 190 hata vardı ve bunların bir kısmı gerçek `AttributeError` hatalarıydı.
  Hatalar 0'a indirildi, mypy (Linux + Windows hedefi) CI'a engelleyici adım olarak eklendi.
- **Sürüm CI'ı kırıktı:** sürüm (release) işi 39 karakterlik, çözülmeyen bir action SHA'sına sabitlenmişti.
  Etiketli bir sürümde (`refs/tags/v*`) iş bu adımda kırılacaktı. Düzeltildi.

## 2. Ölçümler (önce / sonra)

Hepsi bu konteynerde, Python 3.12.3 / Linux üzerinde koşuldu. "Önce" = `origin/main` temiz
worktree. CI ile aynı iki zamanlama testi coverage altında hariç tutuldu.

| Ölçüm | Önce (`main`) | Sonra (bu dal) |
|---|---|---|
| pytest geçen | 4599 | 4621 (+22 yeni regresyon testi) |
| pytest kalan | 1 (`test_h1_escape_hatch_is_refused_in_a_frozen_build`, bkz. AUD-13) | 0 |
| pytest atlanan (skip) | 27 (hepsi platform/bağımlılık/veri koşullu, bkz. §5) | 27 (aynı testler) |
| xfail / xpass | 0 / 0 | 0 / 0 |
| Satır kapsamı (`--cov=src`) | %84 | %84 (4577/29086 satır kapsanmıyor; önce 4663/28992) |
| ruff | temiz | temiz |
| mypy strict (`src/`) | 190 hata / 40 dosya, CI'da yok | 0 hata (Linux ve `--platform win32`), CI'da engelleyici |
| bandit `-ll` | 0 | 0 |
| pip-audit (kilitli bağımlılıklar) | 0 açık | 0 açık |
| npm audit (high) | 0 | 0 (ön yüz değişmedi) |
| Ön yüz typecheck / lint / build / offline kontrol | geçti | geçti (ön yüz değişmedi) |
| gitleaks HEAD (repo yapılandırması ve varsayılan kurallar) | temiz | temiz |
| gitleaks geçmiş (allowlist'siz) | 4 eski bulgu, izinli listede (B-10) | aynı |
| Veri bütünlüğü (`scripts/data_integrity_audit.py`) | FAIL 0, WARN 1, INFO 1 | aynı |

Coverage dışı zamanlama testleri (CI'nin ayrı adımı) bu dalda da geçti: 2/2.

Flaky test gözlemi: `TestCopilotSession::test_analysis_latency_under_50ms` coverage altında
313 ms ölçtü (limit 50 ms). CI bu testi bilerek coverage'sız ayrı adımda koşuyor, flaky sayılmadı.

## 3. Bulgular

Kanıtlar `dosya:satır` olarak `main` @ `e16d48a` üzerindedir. "Test" sütunu
`tests/unit/test_audit_2026_10_01.py` içindeki regresyon testidir. Bu testler düzeltme öncesi
kodda kırmızı, sonrasında yeşildir.

| ID | Önem | Alan | Bulgu | Durum |
|---|---|---|---|---|
| AUD-05 | YÜKSEK | Güvenlik/safety | Bağlama (context) içeren onay token'ı payload hash'i olmadan basılıyordu: `TxSafetyGateway.issue_confirmation_token` içinde `bytes(None)` → `TypeError` (`src/safety/gateway.py:711`). VIN/seri numarası adlandıran her flash (varsayılan) ve DTC silme / oturum / reset eylemleri (`src/ui/desktop_app.py:3738,4078,4150,4216`) üretim geçidinde çöküyordu. | Düzeltildi |
| AUD-06 | YÜKSEK | Protokol (ISO-TP) | UDS istemcisi tek kullanımlık token'ı her Consecutive Frame'de yeniden sunuyordu (`src/protocols/uds/client.py:809-818`). Geçit ilk CF'yi "token already consumed" ile reddediyordu; çok çerçeveli hiçbir kritik istek (0x36 TransferData) tamamlanamıyordu. | Düzeltildi |
| AUD-07 | YÜKSEK | Protokol (UDS) | Flasher, RoutineControl sonucunu (0x31 0x03) kanıtsız sorguluyordu (`src/protocols/uds/flasher.py:1055`). Geçit 0x31'i kritik sınıflıyor; sağlama toplamı adımı reddediliyordu. Masaüstü "rutin başlat" eylemi de token'sızdı (`src/ui/desktop_app.py:4182`). | Düzeltildi |
| AUD-01 | YÜKSEK | Doğruluk | `_handle_reassembled_message`, `ReassembledMessage.bytes` alanını okuyordu; bu alan yok (`src/ui/desktop_app.py:4559`). J1939 VIN (PGN 65260) hiç algılanmıyordu. Düzeltmeyle aktifleşecek log satırı tam VIN basacaktı; artık maskeli basıyor. | Düzeltildi |
| AUD-08 | YÜKSEK | Performans/veri | `_record_signal_sample`, enum metni ("neutral") veya `None` aldığında `math.isfinite` `TypeError` fırlatıyordu. Telemetri döngüsü bunu tur başına yakaladığı için tüm tur (200'e kadar çerçeve ve kara kutu yazımı) düşüyordu. Tetikleyiciler: NMEA 2000 PGN 127493, "veri yok" sıvı seviyesi, OEM sentinel sinyalleri (`src/ui/desktop_app.py:2799-2826,6600-6640`). | Düzeltildi |
| AUD-09 | YÜKSEK | CI/CD | Release işi `actions/attest-build-provenance@ef244556…460107b` 39 karakterlik bir SHA'ya sabitlenmişti; bu SHA çözülmüyor (`.github/workflows/ci.yml:449`). | Düzeltildi |
| AUD-10 | YÜKSEK | Test/CI | mypy strict yapılandırılmış ama hiçbir yerde çalıştırılmıyordu; 190 hata vardı. AUD-01..03 tip denetiminin yakalayacağı gerçek hatalardı. | Düzeltildi |
| AUD-02 | ORTA | Doğruluk | `start_obd_polling`, var olmayan `self.safe_bus`'ı kullanıyordu; her çağrı `AttributeError` atıyor ve router aboneliği sızıyordu (`src/ui/desktop_app.py:4613`). Köprüden çağrılabilir; mevcut ön yüz kullanmıyor. TX artık geçitten gidiyor. | Düzeltildi |
| AUD-03 | ORTA | Doğruluk | `_handle_obd_pid_result`, var olmayan `ObdPidResult.success` alanını okuyordu; canlı RPM/hız/soğutucu kaydedilmiyordu (`src/ui/desktop_app.py:4633`). | Düzeltildi |
| AUD-04 | ORTA | Protokol (J1939-73) | 8 baytlık tek çerçeve DM1'in iki 0xFF dolgu baytı "bozuk kuyruk" sayılıyordu. Her normal DM1 `malformed=True` işaretleniyor ve uyarı loglanıyordu (`src/protocols/j1939/diagnostics.py:232`). | Düzeltildi |
| AUD-11 | ORTA | Paketleme | Sürüm 6 yerde elle yazılıydı (pyproject, package.json, installer.iss, launcher, updater, license_flow). `src/version.py` tek kaynak yapıldı ve test eklendi. | Düzeltildi |
| AUD-12 | ORTA | UX | 13 kullanıcı mesajında çift kodlanmış UTF-8 vardı ("❌" yerine "âŒ") (`src/ui/desktop_app.py:4108-4294,5539-5641`). | Düzeltildi |
| AUD-13 | ORTA | Test kalitesi | Frozen-mod testleri Linux'ta gerçek `~/.local/state` altına lisans HWM dosyası yazıyordu (`tests/unit/test_review_phase2to6_fixes.py:263-266`). Paketi ikinci kez koşmak `test_h1_escape_hatch_is_refused_in_a_frozen_build`'i kırıyordu; temiz `main` tabanında yeniden üretildi. | Düzeltildi |
| AUD-14 | ORTA | Test kalitesi | I-03 flasher testi her kwargs'ı kabul eden sahte bir issuer kullanıyordu (`tests/unit/test_safety_core_fixes.py:212-228`); AUD-05 çökmesini gizliyordu. Gerçek bağlamayı doğrulayacak şekilde güncellendi. | Düzeltildi |
| AUD-15 | ORTA | Doküman | README "Python 3.12+" diyordu, `pyproject` `>=3.11`, CI ise yalnızca 3.12/3.13 test ediyordu. README "60 FPS cockpit" diyordu; kodda döngü 20 Hz, canlı trafik görünümü 4 Hz. Test sayısı "3,500+" yazıyordu, ölçülen 4,600+. | Düzeltildi |
| AUD-16 | DÜŞÜK | Veri | `data/diagnostics/root_cause_graph.json.t21_before` artık dosyası hiçbir yerden referans almıyordu. | Silindi (geçmişte duruyor) |
| B-01…B-13 | ORTA/DÜŞÜK | çeşitli | Test DBC'si katalogda, katman ihlalleri, UI e2e CI'da yok, ön yüz testi yok, donanım doğrulaması vb. | `BACKLOG.md` |

### Doğrulanan ve sorun bulunmayan alanlar

- **TX choke-point:** `src/` içinde `AbstractBus.send` / `privileged_send` yalnızca
  `TxSafetyGateway` (`src/safety/gateway.py:1886`) ve sürücülerin kendi iç çağrısı
  (`src/hal/drivers/pcan_kvaser.py:453`) tarafından çağrılıyor. Replay oynatıcısı TX metotlarını
  açıkça reddediyor (`src/hal/replay/player.py:42`). Protokoller `TxPort` üzerinden gidiyor.
- **Protokol hesapları:** ISO-TP (klasik + FD, 1…4096 bayt) ve J1939 BAM (9…1785 bayt) gidiş-dönüş
  testleri geçti. OBD-II Mode 01 formülleri (0x0A, 0x10, 0x22, 0x23, 0x32, 0x44, 0x54, 0x5D, 0x5E vb.)
  SAE J1979 ile karşılaştırıldı ve uyumlu. J1939 SPN/FMI (CM=0) ayrıştırması doğru.
- **Uygulama güvenliği:** pickle/yaml/eval/`shell=True` yok. Güncelleme manifesti ve paket Ed25519
  ile doğrulanıyor, yönlendirme kapalı, host allowlist var. Masaüstü auth geri çağrısı PKCE + state
  kullanıyor. Ön yüz sunucusu yalnızca loopback'te, CSP ve path-traversal korumalı.

## 4. Yapılan değişiklikler (commit sırasıyla)

1. `fix(ui)` — VIN, OBD poller ve PID işleyicisi (AUD-01..03), VIN logu maskeli.
2. `fix(j1939)` — DM1 0xFF dolgusu (AUD-04).
3. `fix(safety)` — Onay token'ları:
   - Bağlamlı token artık istemcinin segmentasyondan sonra gördüğü **gerçek SF/FF baytlarına**
     bağlanıyor. Çağıran taraf istemciye bir "minter" fonksiyonu veriyor.
   - Payload hash'siz bağlamlı token açıkça `SafetyError CONFIRMATION_PAYLOAD_UNBOUND` ile reddediliyor.
   - CF'ler token'sız, düz protokol trafiği olarak gidiyor. E-Stop, watchdog, whitelist ve rate
     aşamaları her CF'de hâlâ uygulanıyor.
   - 0x31 0x03 sonuç sorgusu token taşıyor (AUD-05..07, AUD-14).
   - **Güvenlik modeli gevşetilmedi:** payload + bağlam bağlama (I-03, R2-G5/EN3) artık gerçekten uygulanıyor.
   - Kalan pencere: bir blok içindeki CF'ler hız kilidi kontrolünden geçmiyor. FF kontrol ediliyor;
     sonraki her 0x36 bloğunun FF'i yeniden kontrol ediliyor.
4. `ci` — attest-build-provenance SHA (AUD-09).
5. `fix(ui)` — Telemetri dayanıklılığı, çerçeve başına hata yalıtımı (AUD-08).
6. `refactor(types)` — mypy 190 → 0 (AUD-10). Davranış değişikliği yok, yalnızca şu sıkılaştırmalar var:
   - golden-case serbest metin alanları artık fail-closed;
   - sırsız token ayrıştırma açık hata veriyor;
   - `SecretProvider.protection_level` temel sınıfa eklendi.
7. `fix(ui)` — Rutin başlatma token'ı (AUD-07) ve mojibake (AUD-12).
8. `refactor` — `src/version.py` (AUD-11).
9. `ci` — mypy strict, Linux + win32 hedefi, engelleyici adım.
10. `docs` — Python tabanı 3.12, README oranları/sayıları, artık veri dosyası (AUD-15, AUD-16).
11. `test` — Test ev dizini yalıtımı (AUD-13).
12. `docs(audit)` — bu rapor ve backlog.

## 5. Yapılmayanlar ve sınırlar

- **Donanım:** PCAN, Kvaser, RP1210, Vector, SocketCAN, DPAPI ve WebView2 burada
  **çalıştırılmadı** (Linux konteyneri, donanım yok). Flash yolu yalnızca simüle ECU ile uçtan uca
  doğrulandı. Gerçek ECU testi gerekiyor (B-07).
- **Windows / Python 3.13:** Testler yalnızca Linux + 3.12'de koşuldu. Windows ve 3.13 sonuçları
  PR'daki CI'dan gelecek. mypy Windows hedefi `--platform win32` ile denetlendi.
- **Atlanan 27 test:**
  - 11'i yalnızca Windows'ta anlamlı;
  - 2'si Playwright, 1'i PySide6, 5'i `node_modules` gerektiriyor;
  - 2'si derlenmiş `dist` gerektiriyor;
  - 6'sı mevcut veride ilgili kayıt bulunmadığı için atlanıyor (B-05).
  Hiçbir test silinmedi veya skip/xfail yapılmadı.
- **Geri alınamaz işlemler yapılmadı:** git geçmişi yeniden yazılmadı (LFS göçü, eski anahtar
  temizliği) ve test DBC'si katalogdan silinmedi. Bunlar sahibinin kararına bırakıldı (B-01, B-10, B-11).
- **Mimari refaktör** (God-module bölme, katman ihlalleri) davranış riski taşıdığı için bu
  PR'a alınmadı (B-02).
