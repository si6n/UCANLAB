# PROVENANCE — data/diagnostics Kaynak ve Doğrulama Kaydı

Bu klasördeki teşhis bilgi tabanlarının kaynak zinciri, doğrulama yöntemi ve
güncelleme geçmişi burada tutulur. `data/dbc/LICENSES.md` ile aynı disiplini
izler: içerik üretilmez, kamuya açık kaynaklar ve resmî belgeler referans alınır.

## j1939_spn_fmi_database (JSON + CSV)

### v1.2.0 — 2026-09-06 (5 mevcut kayıtta PGN düzeltmesi)

İlk 53 kaydın SPN↔PGN etiketleri, canboat `database/j1939/pgns` YAML'ları ve
bu depodaki `data/dbc/heavy_duty/j1939_canboat.dbc` (aynı upstream'in DBC
dönüşümü — canlı çözücü davranışının aynası) ile satır satır çapraz kontrol
edildi. Düzeltilenler:

| SPN | Eski | Yeni | Kanıt |
|---|---|---|---|
| 92 Engine Percent Load | 61444 (EEC1) | **61443 (EEC2)** | canboat YAML + depo içi DBC: sinyal PGN 61443 bit 16'da |
| 105 Intake Manifold Temp | 65270 (ET1) | **65270 (IC1)** | PGN numarası doğruydu; kısaltma aynı PGN'deki 102/106/107/173 ile uyumsuzdu |
| 157 Metering Rail 1 Press. | 65271 (FED1) | **65243 (EFL_P2)** | canboat YAML + ISOBUS SPN dokümanı: 156/157/164 aynı mesajda; 65271 zaten VEP1 (SPN 168) |
| 3246 DPF Outlet Gas Temp | 64948 (A1D1) | **64947 (A1D2)** | canboat YAML + DBC: 64948=giriş çifti (3241/3242), 64947=çıkış çifti (3245/3246) |
| 3251 DPF Diff. Pressure | 64948 (A1D1) | **64946 (A1D3)** | canboat YAML + DBC: 3251, 64946 bit 32'de (3250 ile aynı mesaj) |

**Bilinçli olarak korununanlar:** SPN 3242/3246'nın adları "…Intake/Outlet Gas
**Temperature**" olarak bırakıldı — canboat bu SPN'lere "Pressure 2" dese de
[Scania DM1 dokümanı](https://www.scania.com), [Cummins 3316/3255 çapraz
referansı](https://otrperformance.com/blogs/quick-tips/cummins-fault-code-3255-spn-3246-fmi-0-or-16-a-critical-dpf-sensor-issue)
ve Detroit Diesel saha kodları sıcaklığı teyit ediyor (canboat adlandırması
bu iki SPN'de sapıtıyor). SPN 1087/1088 (AIR1), 641 (VGT1), 1072 (EBC1),
651-656 (FED2), 628-630 (SFT) gibi canboat j1939 klasöründe bulunmayan
kayıtların etiketlerine bağımsız kaynak bulunamadığı için dokunulmadı.

### v1.1.0 — 2026-09-06 (34 yeni SPN eklendi, 53 → 87)

Eklenecek SPN'ler `canboat/canboat` deposunun `database/j1939/pgns/*.yaml`
dosyalarından taranarak seçildi (mevcut tabloda olmayanlar); her aday ikinci bir
bağımsız resmî kaynakla çapraz doğrulandı:

| Kaynak | Lisans / Durum | Katkısı |
|---|---|---|
| [canboat/canboat — database/j1939/pgns](https://github.com/canboat/canboat/tree/master/database/j1939/pgns) | Apache-2.0 | SPN↔PGN eşleşmesi, alan adları, çözünürlük/offset (ölçekleme) |
| [NHTSA TSB MC-10141869 (SS 1033423, "J-1939 Fault Code Source Address")](https://static.nhtsa.gov/odi/tsbs/2018/MC-10141869-9999.pdf) | ABD resmî belgesi | Resmî SPN ad çapraz doğrulaması (ör. SPN 164 "Engine Fuel Injection Control Pressure") |
| [DEIF J1939 measurements dokümanı](https://documentation.deif.com/r/ie-150-agc-150-engine-communication-4189341302-uk/generic-j1939/general/j1939-measurements) | Üretici dokümanı | SPN 173 ölçekleme teyidi (0.03125 °C/bit, −273 °C offset, PGN 65270) |
| [Detroit Diesel EOBD raporları (vanderhaags.com)](https://pix.vanderhaags.com/original/engine-assembly-dd15-detroit-86492157c2.pdf) | Saha kaydı | SPN 164/3563 gerçek saha FMI örnekleri |
| ISO 11783-11 / SAE J1939-71 yaygın ölçekleme tabloları | Standart fact'leri | Sıcaklık/basınç bit kodlamaları (TEMPERATURE_UFIX16_J1939 vb.) |

**Doğrulanmayan / reddedilen adaylar:** SPN 3241/3245/5862 gibi "preliminary
FMI" türevleri iki kaynak arasında ad çelişkisi taşıdığı için eklenmedi;
SPN 102/105/157/108 gibi mevcut kayıtların PGN etiketlerine dokunulmadı.
Türkçe başlık, alt sistem, arıza matrisi ve saha adımları bu depoda yazılmıştır
(mevcut kayıtların editoryal stilini izler) ve kaynak lisanslarından
etkilenmez; canboat verisi Apache-2.0 atfıyla kullanılır.

### v1.0.0 — ilk yayın (53 SPN + 20 FMI tanımı)

İlk küratörlük kaydı bu depo geçmişinde bulunur.

## dtc_database (JSON + CSV)

### v1.3.0 — 2026-09-06 (Üreticiye Özel P1 Serisi Kodlar Eklendi, 1816 → 1840 kod)

Ford, GM, Volkswagen/Audi (VAG), Toyota/Lexus ve BMW platformlarına ait 24 adet kritik üretici P1 kodu (`P1000`, `P1130`, `P1131`, `P1151`, `P1260 PATS İmmobilizer`, `P1345 CKP-CMP Korelasyonu`, `P1349 VVT`, `P1450 EVAP Purge`, `P1516 TAC`, `P1602 Terminal 30`, `P1682 Kontak Voltajı`, `P1744 TCC` vb.) fabrika servis kılavuzları ve resmi OBD standartları referans alınarak entegre edildi. Her kod için OEM multimetre toleransları, UDS rutinleri ve 4 aşamalı saha teşhis rehberi eklendi.

## nhtsa_can_recalls_database (JSON + CSV)

### v1.0.0 — 2026-09-06 (İlk Yayın — 282 Tekil Güvenlik Geri Çağırma & TSB Kampanyası)

- **Kaynak:** ABD Ulusal Karayolu Trafik Güvenliği İdaresi (NHTSA) Açık API (`https://api.nhtsa.gov/recalls/recallsByVehicle`).
- **Lisans / Durum:** ABD Federal Hükümeti Resmî Kamu Verisi (Public Domain / US Gov Open Data).
- **Kapsam:** 2020–2024 model yılları arasındaki 82 modern araç platformu (Ford F-150/Mach-E/Explorer, Tesla Model 3/Y/S/X, GM Silverado/Bolt, Toyota RAV4/Prius, VW ID.4, BMW 3-Serisi/i4/iX, Hyundai Ioniq 5, Kia EV6, Jeep Wrangler vb.).
- **Filtreleme & Küratörlük:** 708 toplam kampanya taranarak CAN-Bus iletişim kaybı, Central Gateway (CGW), BCM, Yüksek Voltaj Batarya / BMS / Kontaktör / Pyrofuse, Elektronik Fren / Direksiyon ve OTA (Uzaktan Yazılım Güncelleme) ile ilişkili 282 tekil güvenlik bülteni seçildi ve kategorize edildi.

## Diğer tablolar

- `obd_mode06_database.json/csv` — SAE J1979 Mode $06 izleyici tablosu (MIDs, TIDs, CIDs).
- `uds_did_database.json/csv` — ISO 14229 DID kataloğu (standart 0xF1xx + OEM VAG, BMW, Ford, Tesla).
