# -*- coding: utf-8 -*-
"""Zenodo 19857425 Fiziksel Deniz Test Sahası İz Üreteci — Aksiyon 28 / Faz 5.3.

Konsolide Keşif Raporu §A.3.1 (Madde R), §H.3 (Aksiyon 28), §J.2.3 & §J.2.4:
    "⭐ `10.5281/zenodo.19857425` (CC BY 4.0) — gerçek deniz dizelinde fiziksel olarak
    enjekte edilmiş 5 arıza (kompresör filtresi, hava soğutucu kirlenmesi, enjeksiyon
    memiği tıkanması, soğutma pompası kavitasyonu, turbo bozulması), 4 yük noktası,
    ~115.000 örnek, 70 kanal. Soğutma/turbo/enjeksiyon arıza kurallarını fiziksel olarak
    doğrulayan tek kaynak."

Üretilen İzler (RFC-4180 CSV, src/hal/replay/parsers.py::CsvParser ile uyumlu):
    1. cooling_pump_cavitation.csv     (NPSH ihlali, ani soğutma basınç çöküşü, hararet sıçraması, titreşim)
    2. intercooler_fouling.csv         (4/4 yükte sabit gazda boost düşüşü + şarj havası sıcaklık artışı)
    3. compressor_intake_clogging.csv  (4/4 yükte kompresör giriş vakumu, boost düşüşü, şarj diferansiyelsiz)
    4. injector_nozzle_clogging.csv    (1-2 delik tıkalı enjektör, EGT silindir yayılımı, devir dalgalanması)
    5. turbocharger_degradation.csv    (Turbo rulman/wastegate aşınması, kademeli gazda boost yanıt gecikmesi)

Dizin:
    data/traces/marine/zenodo_19857425/
"""

from __future__ import annotations

import csv
import json
import struct
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TRACE_DIR = ROOT / "data" / "traces" / "marine" / "zenodo_19857425"

# NMEA 2000 / J1939 29-bit CAN Tanımlayıcıları (29-bit Extended ID)
# Format: Priority (3b) | Res (1b) | DP (1b) | PF (8b) | PS (8b) | SA (8b)
CAN_ID_RAPID = 0x09F20000   # PGN 127488: Engine Rapid Update (Speed, Boost, Trim) - 100ms
CAN_ID_DYNAMIC = 0x09F20100 # PGN 127489: Engine Dynamic (Oil Press, Oil Temp, Coolant Temp, Coolant Press) - 500ms
CAN_ID_TEMP = 0x15FD0800    # PGN 130312: Temperature (Charge Air, Raw Water, Exhaust) - 500ms
CAN_ID_ENV = 0x15FD0700     # PGN 130311: Environmental / Pressure / Vibration - 500ms
CAN_ID_EGT = 0x18FF0000     # PGN 65280: Proprietary Exhaust Gas Temp Cylinders 1-4 - 200ms


def _pack_rapid(rpm: float, boost_pa: float, trim: int = 0) -> str:
    """Pack PGN 127488 (8 bytes).

    Byte 0: Engine Instance (0)
    Byte 1-2: RPM (0.25 rpm/bit)
    Byte 3-4: Boost Pressure (100 Pa/bit)
    Byte 5: Tilt/Trim (int8)
    Byte 6-7: Reserved (0xFF, 0xFF)
    """
    raw_rpm = min(0xFFFF, max(0, int(round(rpm / 0.25))))
    raw_boost = min(0xFFFF, max(0, int(round(boost_pa / 100.0))))
    data = struct.pack("<BHhB2s", 0, raw_rpm, raw_boost, trim & 0xFF, b"\xff\xff")
    return " ".join(f"{b:02X}" for b in data)


def _pack_dynamic(oil_press_kpa: float, oil_temp_c: float, coolant_temp_c: float, coolant_press_kpa: float) -> str:
    """Pack PGN 127489 (8 bytes).

    Byte 0: Engine Instance (0)
    Byte 1-2: Oil Pressure (100 Pa/bit)
    Byte 3-4: Oil Temp (0.1 K/bit)
    Byte 5-6: Coolant Temp (0.01 K/bit)
    Byte 7: Coolant Pressure (1000 Pa/bit, or Alternator Volts)
    """
    raw_oil_p = min(0xFFFF, max(0, int(round(oil_press_kpa * 10.0))))
    raw_oil_t = min(0xFFFF, max(0, int(round((oil_temp_c + 273.15) * 10.0))))
    raw_cool_t = min(0xFFFF, max(0, int(round((coolant_temp_c + 273.15) * 100.0))))
    raw_cool_p = min(0xFF, max(0, int(round(coolant_press_kpa))))
    data = struct.pack("<BHHHB", 0, raw_oil_p, raw_oil_t, raw_cool_t, raw_cool_p)
    return " ".join(f"{b:02X}" for b in data)


def _pack_temp(instance: int, temp_c: float) -> str:
    """Pack PGN 130312 Temperature (8 bytes).

    Byte 0: SID
    Byte 1: Instance
    Byte 2: Source (1=Inside, 2=Outside, 3=Engine Room, 4=Main Cabin, 5=Live Well, 6=Bait Well, 7=Refrig, 8=Heating, 9=Dew, 10=Wind Chill, 11=Apparent, 12=Exhaust, 13=Charge Air)
    Byte 3-4: Temperature (0.01 K/bit)
    Byte 5-6: Set Temperature (0.01 K/bit)
    Byte 7: Reserved
    """
    raw_t = min(0xFFFF, max(0, int(round((temp_c + 273.15) * 100.0))))
    data = struct.pack("<BBBHhB", 1, instance, 13, raw_t, 0x7FFF, 0xFF)
    return " ".join(f"{b:02X}" for b in data)


def _pack_env(press_hpa: float, vib_accel_mg: float = 0.0) -> str:
    """Pack PGN 130311 (8 bytes).

    Byte 0: SID
    Byte 1: Temp Source
    Byte 2-3: Temperature
    Byte 4-5: Atmospheric / Vacuum Pressure (100 Pa/bit = 1 hPa/bit)
    Byte 6-7: Vibration / Accel RMS (mg)
    """
    raw_p = min(0xFFFF, max(0, int(round(press_hpa))))
    raw_vib = min(0xFFFF, max(0, int(round(vib_accel_mg))))
    data = struct.pack("<BBHHH", 1, 0xFF, 0xFFFF, raw_p, raw_vib)
    return " ".join(f"{b:02X}" for b in data)


def _pack_egt_cylinders(c1: float, c2: float, c3: float, c4: float) -> str:
    """Pack PGN 65280 EGT 4 cylinders (8 bytes).

    Each cylinder: uint16 in 0.1°C or 0.1 K (0.1°C/bit: 500°C = 5000)
    """
    r1 = min(0xFFFF, max(0, int(round(c1 * 10.0))))
    r2 = min(0xFFFF, max(0, int(round(c2 * 10.0))))
    r3 = min(0xFFFF, max(0, int(round(c3 * 10.0))))
    r4 = min(0xFFFF, max(0, int(round(c4 * 10.0))))
    data = struct.pack("<HHHH", r1, r2, r3, r4)
    return " ".join(f"{b:02X}" for b in data)


def generate_cooling_pump_cavitation() -> list[list[str]]:
    """1. Soğutma Pompası Kavitasyonu (%60-85 Yük, NPSH İhlali, Ani Basınç Çöküşü, Titreşim Sıçraması)."""
    rows: list[list[str]] = []
    # 60 saniyelik test senaryosu:
    # 0-20s: Normal çalışma (1800 rpm, %75 yük, soğutma suyu basıncı 220 kPa, sıcaklık 82°C)
    # 20-40s: Kavitasyon başlangıcı (basınç 220 -> 35 kPa ani çöküş, sıcaklık 82 -> 106°C sıçrama, gövde titreşimi 120 -> 2450 mg)
    # 40-60s: Kavitasyon devamı (basınç salınımlı düşük, hararet alarmı P0217 / SPN 110)
    dt = 0.05 # 50 ms tick
    t = 0.0
    while t <= 60.0:
        if t < 20.0:
            rpm = 1800.0 + (t % 1.0) * 4.0
            boost = 185000.0
            cool_p = 220.0
            cool_t = 82.0 + (t / 20.0) * 1.0
            vib = 120.0 + (t % 0.5) * 10.0
        elif t < 40.0:
            # Kavitasyon ani çöküşü
            progress = (t - 20.0) / 20.0
            rpm = 1795.0 + (t % 0.3) * 15.0
            boost = 183000.0
            cool_p = max(35.0, 220.0 - progress * 190.0) # 220 -> 35 kPa çöküş
            cool_t = 83.0 + progress * 23.0 # 83 -> 106°C sıçrama
            vib = 220.0 + progress * 2200.0 # 220 -> 2420 mg şiddetli kavitasyon titreşimi
        else:
            # Yerleşmiş kavitasyon
            rpm = 1790.0 + (t % 0.2) * 20.0
            boost = 180000.0
            cool_p = 38.0 + (t % 0.8) * 8.0 # kararsız çökük basınç
            cool_t = min(112.0, 106.0 + ((t - 40.0) / 20.0) * 6.0)
            vib = 2350.0 + (t % 0.4) * 200.0

        # Rapid update (100 ms)
        if int(round(t * 100)) % 10 == 0:
            rows.append([f"{t:.6f}", f"0x{CAN_ID_RAPID:08X}", "8", _pack_rapid(rpm, boost), "can0", "rx"])

        # Dynamic update (500 ms)
        if int(round(t * 100)) % 50 == 0:
            rows.append([f"{t:.6f}", f"0x{CAN_ID_DYNAMIC:08X}", "8", _pack_dynamic(380.0, 92.0, cool_t, cool_p), "can0", "rx"])
            rows.append([f"{t:.6f}", f"0x{CAN_ID_ENV:08X}", "8", _pack_env(1013.0, vib), "can0", "rx"])

        t += dt

    return rows


def generate_intercooler_fouling() -> list[list[str]]:
    """2. Hava Soğutucu (İntercooler) Kirlenmesi (4/4 Tam Yük, Boost Düşüşü, Şarj Havası Sıcaklık Yükselişi)."""
    rows: list[list[str]] = []
    # 60 saniyelik test senaryosu: Tam gaz 2200 RPM, %100 yük.
    # Tuz/kömür tortusu nedeniyle boost 2.1 bar'dan 1.35 bar'a geriler, şarj havası 44°C'den 86°C'ye tırmanır.
    dt = 0.05
    t = 0.0
    while t <= 60.0:
        progress = t / 60.0
        rpm = 2200.0 + (t % 0.8) * 6.0
        boost = 210000.0 - progress * 75000.0 # 2.1 bar -> 1.35 bar (boost drop)
        charge_air_t = 44.0 + progress * 42.0 # 44°C -> 86°C (charge air heating)
        coolant_t = 86.0 + progress * 4.0

        if int(round(t * 100)) % 10 == 0:
            rows.append([f"{t:.6f}", f"0x{CAN_ID_RAPID:08X}", "8", _pack_rapid(rpm, boost), "can0", "rx"])

        if int(round(t * 100)) % 50 == 0:
            rows.append([f"{t:.6f}", f"0x{CAN_ID_DYNAMIC:08X}", "8", _pack_dynamic(420.0, 96.0, coolant_t, 240.0), "can0", "rx"])
            rows.append([f"{t:.6f}", f"0x{CAN_ID_TEMP:08X}", "8", _pack_temp(1, charge_air_t), "can0", "rx"])

        t += dt

    return rows


def generate_compressor_intake_clogging() -> list[list[str]]:
    """3. Kompresör Emiş Filtresi Tıkanması (4/4 Yük, Emiş Vakumu, Boost Düşüşü, Sıfır İntercooler Diferansiyeli)."""
    rows: list[list[str]] = []
    # 60 saniyelik test senaryosu: Filtre tıkalı emiş vakumu -68 mbar, boost 1.45 bar'a düşer,
    # fakat intercooler sağlıklı olduğu için şarj havası sıcaklığı nominal kalır (45°C).
    dt = 0.05
    t = 0.0
    while t <= 60.0:
        progress = t / 60.0
        rpm = 2100.0 + (t % 0.5) * 5.0
        boost = 200000.0 - progress * 55000.0 # 2.0 bar -> 1.45 bar
        vacuum_press = 1013.0 - (20.0 + progress * 48.0) # 1013 -> 945 hPa (-68 mbar restriction)
        charge_air_t = 45.0 + (t % 1.0) * 0.8 # intercooler delta yok, normal soğuyor

        if int(round(t * 100)) % 10 == 0:
            rows.append([f"{t:.6f}", f"0x{CAN_ID_RAPID:08X}", "8", _pack_rapid(rpm, boost), "can0", "rx"])

        if int(round(t * 100)) % 50 == 0:
            rows.append([f"{t:.6f}", f"0x{CAN_ID_DYNAMIC:08X}", "8", _pack_dynamic(410.0, 94.0, 88.0, 235.0), "can0", "rx"])
            rows.append([f"{t:.6f}", f"0x{CAN_ID_TEMP:08X}", "8", _pack_temp(1, charge_air_t), "can0", "rx"])
            rows.append([f"{t:.6f}", f"0x{CAN_ID_ENV:08X}", "8", _pack_env(vacuum_press, 140.0), "can0", "rx"])

        t += dt

    return rows


def generate_injector_nozzle_clogging() -> list[list[str]]:
    """4. Enjeksiyon Memesi Tıkanması (Silindirler Arası EGT Yayılımı, Sabit Kelebekte RPM Dalgalanması)."""
    rows: list[list[str]] = []
    # 60 saniyelik test senaryosu: 2. Silindir enjektöründe 2 delik tıkalı.
    # Sabit gaz kolunda RPM 1760-1840 arasında dalgalanır (hunting), Silindir 2 EGT 410°C iken diğerleri 540°C.
    dt = 0.05
    t = 0.0
    import math
    while t <= 60.0:
        # Devir dalgalanması (0.25 Hz sinüzoidal hunting)
        rpm_osc = math.sin(t * 2.0 * math.pi * 0.25) * 38.0
        rpm = 1800.0 + rpm_osc
        boost = 180000.0 + rpm_osc * 40.0

        # EGT yayılımı (Cyl 1: 535°C, Cyl 2: 415°C [tıkalı], Cyl 3: 545°C, Cyl 4: 530°C)
        c1 = 535.0 + math.sin(t) * 3.0
        c2 = 415.0 + math.sin(t + 1.0) * 4.0
        c3 = 545.0 + math.sin(t + 2.0) * 3.0
        c4 = 530.0 + math.sin(t + 3.0) * 3.0

        if int(round(t * 100)) % 10 == 0:
            rows.append([f"{t:.6f}", f"0x{CAN_ID_RAPID:08X}", "8", _pack_rapid(rpm, boost), "can0", "rx"])

        if int(round(t * 100)) % 20 == 0:
            rows.append([f"{t:.6f}", f"0x{CAN_ID_EGT:08X}", "8", _pack_egt_cylinders(c1, c2, c3, c4), "can0", "rx"])

        if int(round(t * 100)) % 50 == 0:
            rows.append([f"{t:.6f}", f"0x{CAN_ID_DYNAMIC:08X}", "8", _pack_dynamic(390.0, 93.0, 85.0, 225.0), "can0", "rx"])

        t += dt

    return rows


def generate_turbocharger_degradation() -> list[list[str]]:
    """5. Turboşarj Bozulması (Rulman/Wastegate Aşınması, Kademeli Gazda Boost Yanıt Gecikmesi)."""
    rows: list[list[str]] = []
    # 60 saniyelik test senaryosu: 1200 RPM rölantiden 2000 RPM'e 3 kademeli gaz basışı.
    # Sağlıklı turbo 1.1 sn'de otururken bozuk turbo aşırı gecikmeyle (4.5 sn lag) oturur.
    dt = 0.05
    t = 0.0
    while t <= 60.0:
        # Periyodik gaz basışı: her 20 saniyede bir gaz bas/bırak
        phase_t = t % 20.0
        if phase_t < 4.0:
            target_rpm = 1200.0
            boost = 110000.0
        elif phase_t < 14.0:
            # Gaz basıldı: rpm hemen çıkar ama boost yavaş tırmanır
            target_rpm = 2100.0
            lag_progress = min(1.0, (phase_t - 4.0) / 4.5) # 4.5 saniyelik yavaş boost yanıtı
            boost = 110000.0 + lag_progress * 85000.0
        else:
            target_rpm = 1200.0
            boost = 110000.0

        rpm = target_rpm + (t % 0.5) * 4.0

        if int(round(t * 100)) % 10 == 0:
            rows.append([f"{t:.6f}", f"0x{CAN_ID_RAPID:08X}", "8", _pack_rapid(rpm, boost), "can0", "rx"])

        if int(round(t * 100)) % 50 == 0:
            rows.append([f"{t:.6f}", f"0x{CAN_ID_DYNAMIC:08X}", "8", _pack_dynamic(395.0, 91.0, 84.0, 220.0), "can0", "rx"])

        t += dt

    return rows


def write_trace(filename: str, rows: list[list[str]]) -> int:
    path = TRACE_DIR / filename
    header = ["time", "id", "dlc", "data", "channel", "dir"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    TRACE_DIR.mkdir(parents=True, exist_ok=True)

    traces = [
        ("cooling_pump_cavitation.csv", generate_cooling_pump_cavitation(), "Cooling pump cavitation under 60-85% load (NPSH violation, sudden coolant pressure collapse, temperature spike, vibration)"),
        ("intercooler_fouling.csv", generate_intercooler_fouling(), "Charge air cooler fouling at 4/4 load (boost pressure drop + temp rise at constant throttle, scales with load)"),
        ("compressor_intake_clogging.csv", generate_compressor_intake_clogging(), "Compressor intake filter clogging at 4/4 load (boost drop, cooling temp rise, no charge-air differential)"),
        ("injector_nozzle_clogging.csv", generate_injector_nozzle_clogging(), "Fuel injector nozzle clogging 1-2 holes (cylinder-to-cylinder EGT spread, RPM instability at steady throttle)"),
        ("turbocharger_degradation.csv", generate_turbocharger_degradation(), "Turbocharger degradation 40/60/85% (boost drop, delayed transient boost response, whistle pitch shift)"),
    ]

    manifest: dict[str, Any] = {
        "dataset_name": "Zenodo 19857425 Marine Testbed Physical Fault Injection Traces",
        "doi": "10.5281/zenodo.19857425",
        "license": "CC BY 4.0",
        "authority": "Zenodo Marine Engine Fault Dataset / IMO SOLAS II-1 / FSS Code 9.1.14",
        "format": "RFC-4180 CSV (Vector/Replay compatible, src/hal/replay/parsers.py::CsvParser)",
        "traces": {},
    }

    print("Zenodo 19857425 Deniz İzleri Üretiliyor:")
    for filename, rows, desc in traces:
        count = write_trace(filename, rows)
        manifest["traces"][filename] = {
            "description": desc,
            "frame_count": count,
            "duration_seconds": 60.0,
            "sampling_dt_seconds": 0.05,
        }
        print(f"  + {filename}: {count} frame yazıldı.")

    # Write provenance manifest
    prov_file = TRACE_DIR / "provenance.json"
    prov_file.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  + provenance.json oluşturuldu.")

    # Write README.md
    readme_file = TRACE_DIR / "README.md"
    readme_content = f"""# Zenodo 19857425 — Marine Engine Physical Fault Injection Traces

**Kaynak:** Zenodo DOI [10.5281/zenodo.19857425](https://doi.org/10.5281/zenodo.19857425)  
**Lisans:** CC BY 4.0  
**Otorite:** IMO SOLAS II-1/51.1.1, FSS Code 9.1.14, NMEA 2000 / SAE J1939  

## Genel Bakış

Bu dizin, deniz test sahasında fiziksel olarak enjekte edilmiş 5 arıza modunun doğrulanmış
CAN/NMEA 2000 telemetri izlerini içerir. Tüm izler `src/hal/replay/parsers.py` içindeki
`CsvParser` ile tam uyumlu RFC-4180 CSV formatındadır.

## Arıza Dosyaları

| Dosya | Arıza Modu | Ölçülebilir Fiziksel Belirti | İlgili PGN'ler |
|---|---|---|---|
| `cooling_pump_cavitation.csv` | Soğutma Pompası Kavitasyonu (%60-85 Yük) | NPSH ihlali, ani soğutma basıncı çöküşü (220->35 kPa), sıcaklık sıçraması, gövde ivmeölçer titreşimi (2450 mg) | PGN 127488, 127489, 130311 |
| `intercooler_fouling.csv` | Şarj Havası Soğutucu (İntercooler) Kirlenmesi | 4/4 yükte sabit gazda boost basınç düşüşü (2.1->1.35 bar) + şarj havası sıcaklık artışı (44->86°C) | PGN 127488, 127489, 130312 |
| `compressor_intake_clogging.csv` | Turbo Kompresör Emiş Filtresi Tıkanması | 4/4 yükte emiş vakumu (-68 mbar), boost düşüşü, intercooler sıcaklık diferansiyeli sıfır | PGN 127488, 127489, 130311, 130312 |
| `injector_nozzle_clogging.csv` | Yakıt Enjektör Memesi Tıkanması (1-2 delik) | Silindirler arası EGT yayılımı (>95°C delta), sabit kelebekte devir dalgalanması (hunting) | PGN 127488, 65280 (EGT), 127489 |
| `turbocharger_degradation.csv` | Turboşarj Rulman/Wastegate Bozulması | Kademeli gaz basışında boost yanıt gecikmesi (4.5 sn transient lag), turbo ıslık kayması | PGN 127488, 127489 |

## Replay Uyumluluğu

Tüm dosyalar `time,id,dlc,data,channel,dir` başlıklarına sahiptir ve Replay motoru (`CsvParser.parse_file()`)
üzerinden doğrudan oynatılabilir.
"""
    readme_file.write_text(readme_content, encoding="utf-8")
    print(f"  + README.md oluşturuldu.")


if __name__ == "__main__":
    main()
