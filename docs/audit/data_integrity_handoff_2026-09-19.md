# data/ Bütünlük Denetimi — Devir Notu (2026-09-19)

Bu not, `docs/audit/data_integrity_2026-09-19.md` raporunun insan-okur eşidir:
ne yapıldı, ne açık kaldı, kim ne yapmalı. Tüm iddialar ölçüm/commit kanıtıyla verilir;
ölçülemeyen durum **belirsiz** olarak işaretlenir (uydurma yok).

## 1. Onarılan: CSV ikizleri boştu (sessiz veri kaybı)

`f8bb892` commit'i üç CSV'nin veri satırlarını boşalttı (yalnız başlık kaldı):

| CSV | önce (f80eb36) | f8bb892 → HEAD | onarım sonrası |
|---|---|---|---|
| `dtc_database.csv` | 1.889 dolu satır | **0** dolu / 14.166 boş | **14.352** |
| `j1939_spn_fmi_database.csv` | 1.437 dolu satır | **0** dolu / 3.710 boş | **11.128** |
| `uds_did_database.csv` | 37 dolu satır | **0** dolu / 68 boş | **68** |

Onarılan araç: `scripts/rebuild_csv_exports.py` (JSON → CSV, atomik yazım, idempotent).
Kalıcı kapı: `tests/unit/test_data_integrity.py` (21 test) + `scripts/data_integrity_audit.py`.
Kayıt: `data/diagnostics/PROVENANCE.md` → "CSV ikizleri (tüm tablolar) — v-R1".

## 2. Açık işler (durum: tamamlandı)

### İŞ-1 — Kırpık metinler — ✅ ONARILDI (FAIL: 0)
Kaynak metinler yerel `procedures_full` ve canlı Detroit Diesel servis referansından kurtarıldı (Veri-Mimarisi §5.1):
- `SPN_1231.steps[1]`: `procedures_full[1]['steps']['B']` (Eaton TS0950FC116) kaynağından tam metin (910 karakter) geri yüklendi.
- `SPN_522.steps[6]`: `procedures_full[0]['steps']['B']` (Eaton TS4080FC26) kaynağından eksik tork/parça adımları tamamlandı (901 karakter).
- `SPN_3364.causes[2]`: Upstream Detroit DD13/DD15 SPN 3364 FMI 1 kaynağından doğrulanıp cümle noktalaması tamamlandı.
- `SPN_629.steps[2]`: Eaton GMWODMMR el kitabıyla doğrulanıp Step V yönlendirmesi noktalaması tamamlandı.
- `SPN_639`, `SPN_70`, `SPN_560`: Eaton kaynaklarıyla %100 birebir tam metin olduğu doğrulandı.

### İŞ-2 — FMI başlığı boş 18 satır — ✅ ONARILDI (boş: 0)
- 14 adet FMI 13 satırına SAE J1939-73 standardına uygun `{title_tr} - Kalibrasyon Dışı` başlığı atandı.
- `SPN_3719` (FMI 1, 3, 4) ve `SPN_5246` (FMI 3) için standart Türkçe başlıklar dolduruldu.
- `scripts/rebuild_csv_exports.py` ile CSV ikizi atomik olarak yeniden üretildi (11.128 satır, 0 boş başlık).

### İŞ-3 — DBC kataloğu kapsamı — ✅ ONARILDI (eksik tekil: 0)
- 5 tekil DBC (`LeafPowertrainBus.dbc`, `ThinkCity.dbc`, `bms.dbc`, `test.dbc`, `canboat.dbc`) `data/dbc/catalog.json` ve `manifest.json` içine eklendi.
- Toplam kataloglu dosya sayısı 181 → 186'ya yükseldi (15.826 mesaj, 77.602 sinyal).
- `dbc_sync_report.json` diskteki gerçek sayıyla (218) eşitlendi.

### İŞ-4 — Doküman/Vault sayı sapması — ✅ GÜNCELLENDİ
`obsidian-vault/03-Kaynaklar/Veri-Mimarisi.md`, `02-Diagnostics/Teshis-Veritabani-Ozeti.md` ve `00-Inbox/Anasayfa.md` güncel gerçek değerlerle eşitlendi (4.253 SPN, %29,0 prosedür, %6,1 semptom, 11.128 arıza matrisi).

### İŞ-5 — Golden-Traces `trace_ref` — ℹ️ İNCELENDİ (sıfır uydurma korunuyor)
55 vakanın tamamı şema açısından geçerlidir (54 kalibrasyon uygun, 1 taslak). Disk üzerinde ilgili araçların ham fiziksel CAN logları bulunmadığından, AGENTS.md §2.3 kuralı gereğince `trace_ref` değeri `null` olarak bırakılmış, sahte kanıt üretilmemiştir.

## 3. Doğrulama (tekrar üretilebilir)

```bash
python scripts/rebuild_csv_exports.py --check   # önce/sonra satır sayısı raporu
python scripts/rebuild_csv_exports.py           # idempotent yeniden üretim
python scripts/data_integrity_audit.py          # tam denetim (FAIL>0 ise çıkış kodu 1)
python -m pytest tests/unit/test_data_integrity.py -q
python -m ruff check .
```

## 4. Yeni kural

Bilgi tabanı (JSON) güncellendiğinde **CSV ikizi aynı commit'te yeniden üretilir**; CI
(`tests/unit/test_data_integrity.py`) satır sayısı, kolon genişliği ve boş-satır uyumunu
zorunlu tutar. Ham tarama çıktıları `spn_gap_hunter/output/` altında kalır.