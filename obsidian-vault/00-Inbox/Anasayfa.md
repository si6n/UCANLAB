---
tags: [moc, anasayfa]
created: 2026-09-13
---

# 🏠 Universal CAN Bus Tool — Bilgi Tabanı

Bu vault, **Universal-CAN-BUS-Tool** projesinin dokümantasyon, teşhis verisi ve ajan notlarını tutar.

> [!info] Hermes entegrasyonu
> `OBSIDIAN_VAULT_PATH` tanımlı → Hermes `obsidian` skill'i bu vault'a doğrudan yazabilir.
> Tüm profiller (pitboss + 7 ajan) ortak kullanır.

## 📂 Klasör Yapısı

| Klasör | Amaç |
|---|---|
| `00-Inbox` | Yakalanan hızlı notlar, işlenmemiş fikirler |
| `01-Projeler` | Aktif proje notları (CAN-DTC-Collector, Universal-CAN-BUS-Tool) |
| `02-Diagnostics` | Teşhis veritabanı referansları (DTC, J1939 SPN, UDS DID, Mode06) |
| `03-Kaynaklar` | Kaynak envanterleri, toplanan veri, PDF'ler |
| `04-Ajan-Notlari` | Hermes ajanlarının tur raporları |
| `05-Gunluk` | Günlük çalışma kayıtları |
| `templates` | Not şablonları |

## 🔗 Hızlı Erişim

- [[Proje-Haritasi]] — Mimari ve bileşenler
- [[Teşhis-Veritabani-Ozeti]] — Veritabanı kayıt sayıları
- [[Ajan-Tur-Kayitlari]] — Tur geçmişi
- [[Kaynak-Envanteri]] — Veri kaynakları ve kara liste

## 🚗 Proje Bileşenleri

- **Universal-CAN-BUS-Tool** — Ana uygulama (Python + TypeScript/React)
- **CAN-DTC-Collector** — Veri toplama ve zenginleştirme hattı
- **Hermes Ajanları** — pitboss, marshal, tuner, scout, cockpit, telemetry, uplink, chassis

## 📊 Son Durum (2026-09-13)

| Veritabanı | Kayıt |
|---|---|
| DTC | 14.166 |
| J1939 SPN | 865 |
| UDS DID | 68 |
| Mode06 | 38 MID + 9 Class2 |
| Extended PID | 112 |
| NHTSA Recall | 282 |
