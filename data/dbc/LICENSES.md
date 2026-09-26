# LICENSES.md — DBC-Knowledge-Pack Atıf ve Lisans Zinciri

Bu paket **erişim lisansı vermez**; yalnızca kamuya açık kaynakların lisanslarını
korur. Entegrasyon sırasında bu dosya `data/dbc/` yanına kopyalanmalıdır.

## Kabul edilen kaynaklar ve lisansları

| Kaynak | Lisans | Telif | Kullanım | Dosyalar |
|---|---|---|---|---|
| canboat/canboat | Apache-2.0 | © canboat authors | J1939 + N2K PGN YAML → DBC dönüşümü | `curated/heavy_duty/j1939_canboat.dbc`, `curated/marine/n2k_canboat.dbc`, `curated/heavy_duty/j1939_ccvs1_extra.dbc` |
| commaai/opendbc | MIT | © comma.ai | 57 statik + 39 generator-çıktısı binek DBC | `curated/passenger/*` (opendbc kaynaklı tümü) |
| chris-youngblut-solutions/opendbc-ag | MIT | © Chris Youngblut Solutions | ISO 11783/VDMA/ag-J1939 DBC (J1939 ID'ye çevrildi) | `curated/agriculture/isobus_vdma.dbc`, `j1939_isobus_ag.dbc`, `j1939_isobus_vdma_all.dbc` |
| Open-Agriculture/AgIsoStack-plus-plus | MIT | © Open-Agriculture | PGN sabitleri — doğrulama referansı (dağıtım yok) | — |
| ISOBUS Data Dictionary (isobus.net, AEF/ISO 11783-11 ODB) | Ücretsiz kamuya açık export (telif: AEF/ISO; site kullanım koşulları) | AEF | `SPNs and PGNs.csv` → `curated/isobus_dd11783.dbc` (102 msg / 1341 sig) | `curated/agriculture/isobus_dd11783.dbc` |
| nberlette/canbus + nberlette/bmw-dbc | MIT | © 2021–2022 Nicholas Berlette | BMW E39 DBC + OBD-II multiplexed PID referansı | `curated/passenger/bmw-e39.dbc`, `curated/diagnostics/OBD2.dbc` |
| berumiya/CAN_DBC_6thGenMazda | **CC-BY-4.0** | © berumiya | Mazda 6th-gen HSCAN/MSCAN — **atıf zorunlu**: "Contains data from berumiya/CAN_DBC_6thGenMazda (CC-BY-4.0)" | `curated/passenger/MX5ND_6thGenMazda_HSCAN.dbc`, `MX5ND_6thGenMazda_MSCAN.dbc` |
| shemps/byd-atto3-openpilot-port | MIT | © shemps | BYD Atto3 araç + CAN-FD radar DBC | `curated/passenger/byd_atto3.dbc`, `byd_radar_fd.dbc`, `curated/ev_bms/byd_atto3.dbc` |
| juanmaus/geely-geometry-c-dbc | MIT | © geely-geometry-c-dbc contributors | Geely Geometry C IF-CAN DBC | `curated/passenger/geely_geometry_c_if_can.dbc` |
| andrewdodd/decoda | MIT | © 2022 Andrew Dodd | J1939DA/ISOBUS DD → JSON dönüştürücü — yalnızca **referans/altyapı** incelemesi, kod dağıtımı yok | — |
| SAE J1939-71 yaygın yerleşim (CCVS1 SPN 84) | Standart (üç bağımsız kaynakla teyitli yerleşim) | SAE | `raw/pgn-extras/065265-ccvs1.yaml` | `curated/heavy_duty/j1939_ccvs1_extra.dbc` |
| Üretici-özel J1939 proprietary (Cummins/Scania/Volvo) | Paket içi üretim (projenin OEM decoder tanımlarından) | — | — | `curated/heavy_duty/*_j1939_proprietary.dbc` |
| projectgus/kona-ev-dbc | MIT | © projectgus + katkıda bulunanlar | Hyundai Kona Electric 4 bus DBC (PCAN/BCAN/CCAN/COMP-CAN; küratörlükte bozuk NS_ başlığı temizlendi) | `curated/passenger/hyundai_kona_ev_*.dbc`, `curated/ev_bms/hyundai_kona_ev_pcan.dbc` |
| fsfarmscaper/jaguar-xf-x250-can | **CC-BY-SA-4.0** (DBC/dokümantasyon) + MIT (scriptler) | © fsfarmscaper | Jaguar XF X250 MS/HS bus DBC — **atıf zorunlu + share-alike** | `curated/passenger/jaguar_xf_x250_ms_bus.dbc`, `jaguar_xf_x250_hs_bus.dbc` |
| dalathegreat/leaf_can_bus_messages | **GPL-3.0** | © dalathegreat + baradhili (orijinal tablo) | Nissan LEAF ZE0/AZE0/ZE1 EV/CAR/AV/QC bus DBC'leri (GPL viral lisans; paket dağıtımında lisans metni korunur) | `curated/passenger/nissan_leaf_{ev_can_ze0,ev_can_aze0,ev_can_ze1,car_can_aze0,av_can,qc_can}.dbc` + `curated/ev_bms/` kopyaları |
| joshwardell/model3dbc | MIT | © Josh Wardell | Tesla Model 3/Y topluluk decode DBC (Model3CAN) | `curated/passenger/tesla_model3_model3can.dbc` |
| autti/abraham | MIT | © autti + katkıda bulunanlar | Lincoln MKZ / Ford Fusion crowdsourced DBC | `curated/passenger/lincoln_mkz.dbc` |
| qwec01/ARCFOX_dbc | **GPL-3.0** | © qwec01 | ARCFOX IBUS1/IBUS2/EVBUS + GB/T 27930-2023 (GB18030 → UTF-8 çevrildi, eski adlandırma gb27830_2015) | `curated/passenger/arcfox_{evbus,ibus1,ibus2}.dbc`, `curated/ev_bms/arcfox_{evbus,ibus2}.dbc`, `gbt27930_2023.dbc` |
| open-vehicle-control-system/dbc | MIT | © OVCS katkıda bulunanlar | Tesla iBooster Gen 2 fren aktüatörü DBC | `curated/passenger/tesla_ibooster_gen2.dbc` |
| alan707/openOBD2 | MIT | © alan707 | Standart OBD-II PID DBC — küratörlükte PID 0x09 adı J1979'a göre Bank1→Bank2 düzeltildi + 2 çakışan sinyal strict-hattıyla düşüldü | `curated/diagnostics/obd2_open_pids.dbc` |
| BogGyver/opendbc (tesla_unity_dev dalı) | MIT (commaai/opendbc fork'u) | © comma.ai + BogGyver katkıda bulunanlar | 28 committed DBC: statik 7 (hyundai_kia_generic, stellantis_dasm, tesla_can_pre1916, vw_golf_mk4, vw_mqb_2010, chrysler_pacifica_2017_hybrid, tesla_radar) + generated 21 (Lexus ×6, Honda ×6, Toyota ×9). Küratörlükte tesla_can_pre1916'nın kesik `VAL_ 921 DAS_lssState` numaralandırmasındaki sarkan değeri düşürüldü | `curated/passenger/*` (Batch-4: boggyver kaynaklı tümü) |

## Copyleft ve Paylaşımlı Lisans İzolasyonu (GPL-3.0 & CC-BY-SA-4.0)

Bu depoda yer alan bazı DBC dosyaları copyleft (GPL-3.0) veya paylaşımlı (CC-BY-SA-4.0) lisanslar altındadır:
- **GPL-3.0-only**: `nissan_leaf_*` (dalathegreat), `arcfox_*` (qwec01), `gbt27930_2023.dbc` (eski adı `gb27830_2015.dbc`). Lisans metni: `LICENSES/GPL-3.0-only.txt`.
- **CC-BY-SA-4.0**: `jaguar_xf_x250_ms_bus.dbc`, `jaguar_xf_x250_hs_bus.dbc` (fsfarmscaper). Lisans metni: `LICENSES/CC-BY-SA-4.0.txt`.

### İzolasyon ve Lisans Sınırı (Air-Gap)
1. **Veri Ayrımı:** Bu dosyalar yazılım koduna statik olarak gömülmez veya derlenmez; çalışma zamanında `cantools` kütüphanesi üzerinden dinamik veri yükü (data payload) olarak okunur.
2. **Ticari / Kapalı Dağıtım Temizliği:** Katı ticari veya kapalı kod dağıtımlarında GPL veya CC-BY-SA copyleft etkilenimini tamamen ortadan kaldırmak için bu dosyalar `data/dbc/` dizininden silinebilir veya dağıtım paketinden hariç tutulabilir. Çekirdek teşhis, J1939, OBD-II ve diğer MIT/Apache-2.0 lisanslı DBC'ler bağımsız çalışmaya devam eder.
3. **REUSE 3.0 / SPDX Uyumu:** Tüm lisans ve atıf eşlemeleri depo kökündeki `REUSE.toml` dosyasında tescil edilmiştir.

## DO-NOT-INTEGRATE — lisanssız / riskli dosyalar

Bu dosyalar `raw/` altında **araştırma kaydı** olarak durur; lisans durumu
çözülene kadar `curated/`a veya `data/dbc/`ye **kopyalanamaz**:

| Dosya | Kaynak | Sorun |
|---|---|---|
| `raw/vehicle-dbc/renault-mascott-2009.dbc` | robin-thoni/dbc | Lisans belirtilmemiş |
| `dragz/egmpdbc` (içerik indirilmedi) | Hyundai/Kia E-GMP Ioniq5 | Lisans yok |
| `Sterlingarcher2525/ioniq5-can` (içerik indirilmedi) | Ioniq5 RE notları | Lisans yok |
| `krsche/renault-megane-3-rs-can-dbc` (içerik indirilmedi) | Megane 3 RS | Lisans yok |
| `jackm/j1939decode` (içerik indirilmedi) | J1939 C lib | Lisans yok |
| Konik-ai/j1939_dbc (Batch-3 taraması) | MIT etiketli ama nberlette/canbus fork'u; `j1939.dbc` resmî SAE J1939 standardından türetilmiş ([nberlette issue #6](https://github.com/nberlette/canbus/issues/6) telif iddiası) | SAE telif riski |
| uhi22/IoniqMotorCAN (Batch-3 taraması) | Hyundai Ioniq 28 motor CAN (`Traces/*.dbc`) | Lisans yok |
| icecube45/Dash_InfinitiG37 (Batch-3 taraması) | Infiniti G37 2011 DBC | Lisans yok |
| Trueffelwurm/Car-CAN-Message-DB (Batch-3 taraması) | Opel Astra H — DBC değil (Markdown tablo) + lisans yok | Lisans yok + format uyumsuz |

## Ücretli / kayıtlı alternatifler (kayıt amaçlı)

- CSS Electronics J1939/ISOBUS/N2K DBC paketleri — ücretli; ücretsiz setimiz
  aynı PGN'leri açık kaynaklarla karşılıyor.
- Heavy-Duty Data Pack (CSS) — e-posta kaydı şartlı ücretsiz; MF4 format.

## Sorumluluk reddi

DBC içerikleri topluluk tarafından tersine mühendislikle üretilmiştir; araç
üreticilerinin resmî verisi değildir. Güvenlik-ilişkili (ASIL) kullanımda
yalnızca referans olarak değerlendirilmelidir.
