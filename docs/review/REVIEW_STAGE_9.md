# UCANLAB Kapsamlı Review — Aşama 9

**İncelenen revizyon:** `011b83599836c7f5b9b51c0f946565684d505ca8`

**Kapsam:** AI/evidence analiz katmanı, cloud client/lisans/telemetry upload akışları ve virtual channel hesap motoru.

**Kısıt:** Bu aşamada yalnızca review yapıldı. Kaynak koda veya testlere düzeltme uygulanmadı.

## Doğrulama yöntemi

- Bulguyla ilişkili dosyaların tamamı okundu: `src/engine/virtual_channels/channel_engine.py`, `src/security/cloud/telemetry_uploader.py`, `src/security/cloud/client.py`, `src/security/cloud/license_flow.py`, `src/ui/desktop_app.py`, `src/ui/frontend/src/services/bridge.ts` ve `src/ui/frontend/src/components/reports/ReportsExportView.tsx`.
- AI evidence gate, hypothesis, anomaly, drive-safety ve rapor katmanlarında yeni doğrulanabilir bir güvenlik bypass’ı tespit edilmedi.
- Hedef testler çalıştırıldı: **147 passed**
  - `test_virtual_channels.py`
  - `test_cloud_client.py`
  - `test_license_validator.py`
  - `test_ai_copilot.py`
  - `test_offline_ai_reasoning.py`
  - `test_hypothesis_engine.py`
  - `test_t57c_ai_review.py`
  - `test_t58b_ai_analysis_quality.py`
- Ruff: **All checks passed**.
- Statik sözleşme probe’u:

```text
frontend_raw_upload_accepts_consent_arg: False
reports_passes_vehicle_vin: True
python_raw_upload_default_consent_false: True
virtual_nominal_finite_guard_present: False
```

- Virtual channel sınır probe’u:

```text
nan (nan, nan, nan) finite= False
inf (inf, inf, inf) finite= False
-inf (-inf, -inf, -inf) finite= False
```

## Bulgular

### M1 — VIN’li cloud upload akışı consent parametresi taşımadığı için fail-closed olarak sürekli reddediliyor

**Önem:** Orta-yüksek — telemetry upload özelliği UI üzerinden kullanılamıyor.

#### Kanıt

- `src/security/cloud/telemetry_uploader.py:123-136`, `vehicle_vin` değeri `None` değilse `user_consented=True` zorunlu kılıyor.
- Python bridge `src/ui/desktop_app.py:835-840` aynı korumayı koruyor; `user_consented` varsayılanı `False`.
- Frontend bridge sözleşmesi `src/ui/frontend/src/services/bridge.ts:522-526` `cloudUploadRawContent(filename, content, vehicleVin)` ile yalnızca üç argüman taşıyor; `userConsented` parametresi yok.
- `src/ui/frontend/src/components/reports/ReportsExportView.tsx:89` varsayılan olarak dolu olan `vinInput` değerini upload çağrısına iletiyor, ancak consent argümanı gönderemiyor.

#### Etki

UI’den “Seansı Buluta Yükle” seçildiğinde VIN değeri bulunduğu için upload backend’e ulaşsa bile `TELEMETRY_CONSENT_REQUIRED` ile reddedilir. Bu davranış privacy açısından fail-closed olsa da frontend’de açık bir consent kontrolü veya kullanıcıya bunu sağlama yolu yoktur. Sonuç olarak UI’nin ana cloud telemetry özelliği başarılı olamaz.

#### Çözüm önerisi

- Frontend’de VIN gönderimini açıkça onaylayan bir checkbox/confirmation adımı ekleyin.
- Consent değerini TypeScript bridge sözleşmesine ve Python bridge çağrısına açıkça taşıyın.
- Consent verilmemişse VIN’i hiç göndermeyin; anonim upload için `vehicle_vin=None` kullanın.
- Test ekleyin: consent yokken VIN’li upload reddedilmeli; consent varken upload çağrısı `user_consented=True` ile ilerlemeli; kullanıcı VIN alanını boşaltsa bile API sözleşmesi net kalmalı.

---

### M2 — Virtual torque/power hesabı geçersiz nominal tork parametresini kabul ediyor

**Önem:** Orta — NaN/sonsuz değerler türetilmiş telemetriye sızabilir.

#### Kanıt

`src/engine/virtual_channels/channel_engine.py:46-58` içinde `rpm` ve `actual_torque_percent` için `math.isfinite(...)` kontrolü bulunuyor; ancak `nominal_torque_nm` için sonlu, pozitif veya makul aralık kontrolü yok.

Bu nedenle geçersiz public API girdileri doğrudan çıktıya taşınıyor:

```text
calculate_torque_and_power(1500.0, 50.0, float('nan')) -> (nan, nan, nan)
calculate_torque_and_power(1500.0, 50.0, float('inf')) -> (inf, inf, inf)
calculate_torque_and_power(1500.0, 50.0, -float('inf')) -> (-inf, -inf, -inf)
```

#### Etki

Bu hesaplar daha sonra telemetry, grafik, anomaly veya export katmanına bağlanırsa IEEE-754 geçersiz değerler oluşabilir. Bu durum grafik/JSON/export sonuçlarını bozabilir ve “veri yok” yerine fiziksel olarak anlamsız bir torque/power değeri üretebilir. Mevcut repository kullanımında motor doğrudan kullanılmıyor; bulgu public hesap API’sinin sınır davranışına ilişkindir.

#### Çözüm önerisi

- `nominal_torque_nm` için `math.isfinite(...)` ve domain kuralı uygulayın; en azından sıfırdan büyük olmasını zorunlu kılın.
- Türetim sonrası torque, power ve horsepower değerlerini de sonlu oldukları yönünden son kontrolden geçirin.
- NaN, ±Inf, sıfır ve negatif nominal tork için negatif testler ekleyin.

---

## Bulgusuz incelenen alanlar

- AI copilot’ın offline olma iddiası ve dış API çağrısı içermeyen aktif analiz yolu incelendi.
- Evidence gate’in düşük örnek sayısı, stale signal, DISCOVERED oranı ve confidence sınırları incelendi.
- Hypothesis engine’in graph schema doğrulaması, DTC/anomaly kanıt birleşimi ve deterministik sıralaması incelendi.
- Cloud client’ta embedded lisans key ring’i, HTTPS/loopback scheme kontrolü, redirect credential stripping ve telemetry chunk session ID sanitizasyonu incelendi.

Bu alt alanlarda bu aşamada yeni, bağımsız ve kontrollü olarak doğrulanabilir bir bypass tespit edilmedi. Önceki aşamalarda raporlanan HWM ve cloud endpoint bulguları burada yeniden uygulanmadı.

## Sonuç

Aşama 9’da iki yeni bulgu tespit edildi:

- **1 orta-yüksek:** VIN’li cloud upload için consent parametresi frontend’den taşınmıyor; upload sürekli reddediliyor.
- **1 orta:** Virtual torque/power hesabı geçersiz nominal torkla NaN/Inf üretebiliyor.

Hedef testler **147 passed**, Ruff **temiz**. Hiçbir bulgu uygulanmadı.

**Aşama 9 tamamlandı ve burada duruyorum.**
