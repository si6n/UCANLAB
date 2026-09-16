# T56-B — KANIT (A3-1 + A3-2)

Durum: **kapsam HEAD'de doğrulandı** (bu run kod yazmadı; bkz. §4).

| Bulgu | Dosya | Bulgu (önce) | Düzeltme |
|---|---|---|---|
| A3-1 | `src/ui/desktop_app.py` | bridge `spn`/`fmi`'yi sabit `None` geçiyordu → motorun `d.get("spn")` eşleşmesi hiç kurulmuyor, 3.937 kayıtlık J1939 SPN DB'si oturum yolunda ölü | `SPN <n> FMI <m>` kodundan `_SPN_FMI_RE` ile `spn`/`fmi` ayıklanıp copilot payload'ına dolduruluyor; üretilen engineering report `"report"` anahtarıyla dışarı veriliyor |
| A3-2 | `src/engine/ai/anomaly_detector.py` | `synthetic = name.startswith("OP:")` yalnız isim eşleşmesi sağlanırsa ulaşılıyordu → `OP:EngineOilPressure` ne bulgu ne sentetik bayrak üretiyordu, FAZ 3.2 yarı-ağırlığı ölü koddu | `base = name[3:] if is_synthetic else name`; `entry = thresholds.get(base) or thresholds.get(_camelize(base))`; `synthetic = is_synthetic` |

## 1. A3-1 — kod kanıtı (`src/ui/desktop_app.py`, mevcut HEAD)

Modül seviyesi ayıklayıcı (satır ~75):

```python
_SPN_FMI_RE = re.compile(r"SPN\s+(\d+)(?:\s+FMI\s+(\d+))?", re.I)
```

`get_diagnostic_analysis()` içinde `dtc_payload` üretimi (satır ~1278-1289):

```python
dtc_payload = []
for e in session.events:
    if e.status != "ACTIVE":
        continue
    match = _SPN_FMI_RE.search(e.code or "")
    dtc_payload.append(
        {
            "code": e.code,
            "spn": int(match.group(1)) if match else None,
            "fmi": int(match.group(2)) if match and match.group(2) is not None else None,
        }
    )
```

Dönüş payload'ı `"report"` anahtarını da taşıyor (satır ~1299-1316): `likely_causes`,
`affected_subsystems`, `troubleshooting_steps`, `telemetry_correlations`,
`raw_dtc_count`, `ai_model_used`. Önceden hesaplanıp atılıyordu → J1939 zenginleştirmesi
çağırana görünmezdi (test'in `KeyError: 'report'` ile kırmızı olmasının nedeni).

## 2. A3-2 — kod kanıtı (`src/engine/ai/anomaly_detector.py`)

```python
is_synthetic = name.startswith("OP:")
base = name[3:] if is_synthetic else name
entry = thresholds.get(base) or thresholds.get(_camelize(base))
if entry is None:
    continue
...
synthetic = is_synthetic
```

## 3. ÖNCE / SONRA davranışı

### ÖNCE — fix'ler diskten geri alınmışken (kaynak metin in-memory revert)

```
FAILED tests/unit/test_desktop_evidence.py::TestDm1BridgeSpnFmi::test_dm1_analysis_enriches_from_j1939_kb
       tests/unit/test_desktop_evidence.py:161: AssertionError
       likely_causes = ["CAN veri yolunda aktif diagnostik hata kodları kaydedildi."]
       → SPN/FMI None olduğu için KB dalı hiç çalışmıyor
FAILED tests/unit/test_anomaly_detector.py::TestAnomalyDetection::test_operator_measurement_marked_synthetic
       assert len(findings) == 1   →   0 == 1
       → "OP:EngineOilPressure" hiçbir threshold anahtarına eşleşmiyor, bulgu yok
FAILED tests/unit/test_anomaly_detector.py::TestAnomalyDetection::test_operator_declared_oil_pressure_scores_lower
       AssertionError: operator declaration produced no finding
3 failed, 1 passed in 1.61s
```

### SONRA — mevcut HEAD

```
QT_QPA_PLATFORM=offscreen py -3.13 -m pytest \
  tests/unit/test_desktop_evidence.py::TestDm1BridgeSpnFmi tests/unit/test_anomaly_detector.py
17 passed in 1.53s
```

Ölçülen sonuçlar:

- A3-1 (`SPN 100 FMI 1` DM1 olayı):
  - `report["likely_causes"]` → `SPN_100.title_tr` = **"Motor Yağ Basıncı"** (J1939 KB),
  - `report["affected_subsystems"]` → `SPN_100.subsystem` = **"Motor Yağlama Sistemi (J1939)"**,
  - `report["troubleshooting_steps"]` → KB `diagnostic_action` + `steps` kaynaklı adımlar.
  - Ayrıştırılamayan kod (`"P0217 Motor aşırı ısındı"`) dürüstçe `spn=None, fmi=None` kalır
    → `TestDm1BridgeSpnFmi::test_unparseable_code_yields_none_spn_fmi` (uydurma yok).
- A3-2:
  - `OP:EngineOilPressure` → 1 bulgu, `synthetic is True`, metinde `"operatör beyanı"`.
  - `OP:EngineCoolantTemp` yarı ağırlıkla ayrımı siliyor: `thermostat-stuck == coolant-sensor-open`
    (düz `EngineCoolantTemp` ise ayrışıyor) → FAZ 3.2 canlı.

### Test adları

| Test | Ne kilitliyor |
|---|---|
| `tests/unit/test_desktop_evidence.py::TestDm1BridgeSpnFmi::test_dm1_analysis_enriches_from_j1939_kb` | A3-1: canlı DM1 → J1939 KB alt-sistem + neden + adım |
| `tests/unit/test_desktop_evidence.py::TestDm1BridgeSpnFmi::test_unparseable_code_yields_none_spn_fmi` | A3-1 falsifier: SPN formu olmayan kodda `(None, None)` |
| `tests/unit/test_anomaly_detector.py::TestAnomalyDetection::test_operator_measurement_marked_synthetic` | A3-2: `OP:` öneki bulgu + `synthetic=True` |
| `tests/unit/test_anomaly_detector.py::TestAnomalyDetection::test_operator_prefix_resolves_to_threshold_key` | A3-2: bilinmeyen sinyal `OP:` ile de dürüst sessiz geçiş |
| `tests/unit/test_anomaly_detector.py::TestAnomalyDetection::test_operator_declared_oil_pressure_scores_lower` | A3-2: yarı-ağırlık hipotez skorunu düşürüyor |

## 4. Commit durumu / bu run'ın katkısı

İki commit, bu run başlamadan ~1 saniye önce **eşzamanlı bir ikinci run** tarafından landlendi:

| Bulgu | Commit | Saat |
|---|---|---|
| A3-1 | `b192d7e` — `fix(ui): T56-B A3-1 DM1 SPN/FMI bridge - J1939 KB yolu canli` | 13:59:39 |
| A3-2 | `bd97508` — `fix(engine): T56-B A3-2 OP: oneki cozuldu - operator beyani yari agirlik canli` | 13:51:00 |

Bu nedenle bu run **ek kod/commit üretmedi**: kapsam zaten `HEAD`'de olduğu için ikinci bir
commit ya no-op olurdu ya da paralel ajanların commit edilmemiş işini (`src/safety/gateway.py`,
`src/safety/watchdog.py`, `src/launcher/app.py`, `src/main.py`) süpürürdü.

Bu run'ın katkısı: yukarıdaki bağımsız **önce/sonra doğrulaması** (fix'ler geri alınarak
yeniden üretildi) ve kanıtın kalıcılaştırılması.

## 5. Not — şablon uyuşmazlığı

`b192d7e` commit mesajı `"roport"` anahtarını anıyor; kodda gerçek anahtar `"report"`
(`grep -n '"report"' src/ui/desktop_app.py` → satır 1299) ve testler `"report"` okuyor.
Davranış doğru, commit mesajı metni hatalı.
