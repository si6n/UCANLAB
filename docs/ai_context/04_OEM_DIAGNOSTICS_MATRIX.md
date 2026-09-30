---
title: "AI Context: OEM Diagnostics & Decoders Matrix"
tags:
  - ai-context
  - oem
  - j1939
  - decoders
  - cummins
  - caterpillar
  - scania
  - volvo
  - detroit
  - actros
updated: 2026-09-01
---

# OEM Diagnostics & Decoders Matrix Context Card

Projedeki `src/protocols/j1939/oem/` altında yer alan ticari araç ve motor üreticilerine özel tescilli (Proprietary A/B - PGN 61184 & 65280..65535) sinyal dekoderlerinin haritasıdır.

## 1. Desteklenen OEM Motor & Araç Listesi

| OEM Üretici | Modül Konumu | Temel Çözümlenen Sinyaller | Özel PGN'ler |
|---|---|---|---|
| **Cummins** | `src/protocols/j1939/oem/cummins.py` | DPF Soot Mass, Active Regen State, DEF Dosing Rate, Cylinder Balancing, Rail Pressure | `0xEF00` (PropA), `0xFF00..0xFFFF` |
| **Caterpillar** | `src/protocols/j1939/oem/caterpillar.py` | CAT Engine Diagnostics, Cylinder Cutout, Compression Retarder Stages, Fuel Trimming | `0xEF00`, Cat Proprietary |
| **Scania** | `src/protocols/j1939/oem/scania.py` | Scania EMS, AdBlue/DEF Dosing, DPF Soot Load, Retarder Torque Steps, Cylinder Balance | `0xEF00`, Scania Specific PGNs |
| **Volvo Trucks** | `src/protocols/j1939/oem/volvo.py` | V-MAC / EMS / D13 DPF Soot, VEB Engine Brake Retarder Stages, DEF Level & Quality | `0xEF00`, Volvo Proprietary |
| **Detroit Diesel** | `src/protocols/j1939/oem/detroit.py` | DD13/DD15 DPF Soot & Ash Load, DEF Dosing Pressure, Cylinder Power Balance | `0xEF00`, Detroit Diesel PGNs |
| **Mercedes Actros**| `src/protocols/j1939/oem/actros.py` | OM471/Actros Retarder Braking Levels, AdBlue Injection Rate, DPF Soot, Cylinder Trim | `0xEF00`, Actros Specific |

## 2. OEM Dekoder Tasarım Standardı

Tüm OEM dekoderleri şu prensipleri takip etmelidir:
1. **Giriş:** Ham `CanFrame` (veya `ReassemblyPipeline` tarafından birleştirilmiş çok paketli bayt dizisi).
2. **Hata Dayanıklılığı:** Paket eksik veya bozuksa exception fırlatıp tüm telemetri akışını çökertmek yerine güvenli fallback (`None` veya `SignalQuality.INVALID`) üretmelidir.
3. **Fiziksel Birimler:** Tüm dönüştürmeler SI / standart birimlerde olmalıdır (`mg`, `kPa`, `mg/stroke`, `Nm`, `%`, `°C`).
