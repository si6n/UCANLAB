---
tags: [ajanlar, tur-kaydi, hermes]
created: 2026-09-13
---

# 🤖 Hermes Ajan Tur Kayıtları

## Ajan Kadrosu

| Ajan | Rol | Uzmanlık |
|---|---|---|
| **pitboss** | Şef / Orkestratör | Koordinasyon, veri pipeline |
| **marshal** | Güvenlik | TxSafetyGateway, fail-closed |
| **tuner** | Geliştirme | UDS, firmware, buffer |
| **scout** | Keşif | DBC, discovery |
| **cockpit** | Arayüz | UI, bridge, export |
| **telemetry** | Veri | J1939, NMEA, Mode06 |
| **uplink** | Bulut | Telemetry upload, updater |
| **chassis** | HAL | RP1210, replay |

## Tur Kayıtları

### Tur-25 (2026-09-12/13) — 0 kredi
| Ajan | Çıktı |
|---|---|
| marshal | **Wal33D keşfi** (+4.866 kod), 28 kaynak taraması, kara liste |
| telemetry | ServiceRanger: 949 doküman, 97 SPN prosedürü |
| scout | Class2 CID: +9 monitor / 54 test |
| chassis | VAG kök neden analizi, 5 P-code zenginleştirme |

Detay: `CAN-DTC-Collector/spn_gap_hunter/HANDOFF_TUR25.md`

### Tur-24 (2026-09-11)
REVIEW 2/3 bulgularının dağıtımı — 7 ajan, tüm bulgular kapatıldı.

## Notlar

> [!tip] Görev dağıtımı
> Görevler `hermes -p <profil> chat --in ~ -c "Bot Chat" --create-if-missing -Q --query-file <dosya>` ile arka planda çalıştırılır.
