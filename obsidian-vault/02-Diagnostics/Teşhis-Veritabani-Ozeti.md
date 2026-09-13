---
tags: [diagnostics, veritabani, referans]
created: 2026-09-13
---

# 🧪 Teşhis Veritabanı Özeti

Konum: `Universal-CAN-BUS-Tool/data/diagnostics/`

## Veritabanları

| Dosya | Kayıt | Açıklama |
|---|---|---|
| `dtc_database.json` | **14.166** | P/B/C/U arıza kodları |
| `j1939_spn_fmi_database.json` | **865** | Ağır vasıta SPN/FMI |
| `uds_did_database.json` | 68 | UDS data identifier |
| `obd_mode06_database.json` | 38 MID + 9 Class2 | Mode $06 monitor |
| `extended_pid_database.json` | 112 | Genişletilmiş PID |
| `nhtsa_can_recalls_database.json` | 282 | NHTSA geri çağırma |

## Veri Kaynakları (lisanslı)

| Kaynak | Lisans | Katkı |
|---|---|---|
| Wal33D/dtc-database | MIT | +4.866 DTC kodu |
| STAS63-bit/sitrak-error-codes | CC BY 4.0 | 3.402 SPN (çeviri bekliyor) |
| Eaton ServiceRanger PIM | Açık | 97 SPN prosedürü |
| dtcsearch.com | Açık | 171 başlık onarımı |
| wiki.ross-tech.com | Açık | VAG P-code |

Provenance: `data/diagnostics/PROVENANCE.md`

## İlgili

- [[Kaynak-Envanteri]]
- [[Proje-Haritasi]]
