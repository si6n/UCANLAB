# -*- coding: utf-8 -*-
"""Expand data/diagnostics/j1939_spn_fmi_database.json with 34 field-verified SPNs.

Sources (verified 2026-09-06):
- canboat/canboat database/j1939/pgns/*.yaml (Apache-2.0) - SPN/PGN association,
  field names, resolution/offset (scaling) facts.
- NHTSA TSB MC-10141869 (SS 1033423, J-1939 Fault Code Source Address) - official
  SPN name cross-check.

Idempotent: skips SPNs already present. Run once; re-run is a no-op.
"""

from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "diagnostics"
JSON_PATH = DATA_DIR / "j1939_spn_fmi_database.json"
CSV_PATH = DATA_DIR / "j1939_spn_fmi_database.csv"

T_HIGH = "-273.0 - 1735.0 °C"     # TEMPERATURE_UFIX16_J1939: 0.03125 °C/bit, -273 offset
T_U8 = "-40.0 - 210.0 °C"         # TEMPERATURE_UINT8_OFFSET: 1 °C/bit, -40 offset
TORQUE_PCT = "-125.0 - 125.0 %"   # PERCENTAGE_INT8 (J1939 torque offset -125)
PEDAL_PCT = "0.0 - 100.4 %"       # 0.4 %/bit uint8
RPM_HR = "0.0 - 8031.875 rpm"     # ROTATION_UFIX16_RPM_HIGHRES: 0.125 rpm/bit

# ---------------------------------------------------------------------------
# New SPN entries. Every name/PGN pair below was cross-checked against the
# NHTSA MC-10141869 SPN list and canboat J1939 PGN YAMLs.
# ---------------------------------------------------------------------------

NEW_SPNS: dict[str, dict] = {
    "SPN_29": {
        "spn": 29, "name": "Accelerator Pedal Position 2",
        "title_tr": "Gaz Pedalı Pozisyonu 2",
        "subsystem": "Elektronik Gaz Kelebeği & Sürücü Talebi",
        "associated_pgn": 61443, "pgn_acronym": "EEC2",
        "range": PEDAL_PCT, "unit": "%",
        "description": "Second channel of the redundant accelerator pedal position sensor (0.4 %/bit). Cross-checked by the ECU against SPN 91 for plausibility.",
        "fault_matrix": {
            "2": ("Data Erratic, Intermittent Or Incorrect", "Pedal kanal 1 (SPN 91) ile kanal 2 (SPN 29) değerleri arasındaki sapma toleransı aşıyor (çift kanal uyumsuzluğu).", "Kanal 1 ve kanal 2 sinyal pinlerini osiloskopla birlikte izleyin; pedalın tam üst/alt konumlarında 2 kanal arasındaki oransal uyumu kontrol edin. Uyumsuzluk kablo demetinde tek kanal kırığına işaret eder.", "MEDIUM"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "Gaz Pedalı Pozisyonu 2 - Voltaj normalin üzerinde veya yüksek kaynağa (+5V) kısa devre", "Sensör soketini ayırın; ayrık halde sinyal pini hala 5V görüyorsa kablo demeti besleme hattına temas ediyor demektir.", "MEDIUM"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "Gaz Pedalı Pozisyonu 2 - Voltaj normalin altında veya şasiye (GND) kısa devre", "Sensör soketini ayırın; sinyal pini şasi direncini ölçün (<1.0 Ω ise şasi kısa devresi). Pedal sensörünün 5V besleme ve GND hatlarını multimetrele doğrulayın.", "MEDIUM"),
        },
    },
    "SPN_51": {
        "spn": 51, "name": "Throttle Position",
        "title_tr": "Gaz Kelebeği Pozisyonu",
        "subsystem": "Elektronik Gaz Kelebeği & Sürücü Talebi",
        "associated_pgn": 65266, "pgn_acronym": "LFE1",
        "range": PEDAL_PCT, "unit": "%",
        "description": "Throttle position sensor input reported in the fuel economy (LFE1) message, 0.4 %/bit. Legacy gateway for mechanically actuated throttle bodies.",
        "fault_matrix": {
            "2": ("Data Erratic, Intermittent Or Incorrect", "Gaz Kelebeği Pozisyonu - Veri tutarsız, aralıklı veya hatalı", "Rölanti ve tam gaz noktalarında TPS voltaj eğrisini osiloskopla çizin; potansiyometre yüzeyinde ölü nokta (drop-out) var mı kontrol edin. Klemens korozyonunu temizleyin.", "MEDIUM"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "Gaz Kelebeği Pozisyonu - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "TPS sinyal hattını +5V referansına karşı ölçün; gaz kelebeği gövdesi konnektöründe pin bükülmesi/kaynaşmasını kontrol edin.", "MEDIUM"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "Gaz Kelebeği Pozisyonu - Voltaj normalin altında veya şasiye kısa devre", "TPS toprak hattı direncini ölçün ve gaz kelebeği gövdesi toprak noktasını (gövde üzerinde) yeniden sıkın.", "MEDIUM"),
        },
    },
    "SPN_52": {
        "spn": 52, "name": "Engine Intercooler Temperature",
        "title_tr": "Motor Şarj Hava Soğutucu (Intercooler) Sıcaklığı",
        "subsystem": "Hava Emiş & Turboşarj (CAC)",
        "associated_pgn": 65262, "pgn_acronym": "ET1",
        "range": T_U8, "unit": "°C",
        "description": "Charge air cooler (intercooler) outlet temperature, 1 °C/bit with -40 °C offset. Used for charge density correction and CAC efficiency monitoring.",
        "fault_matrix": {
            "0": ("Data Valid But Above Normal Range - Most Severe Level", "Motor Şarj Hava Soğutucu (Intercooler) Sıcaklığı - Veri geçerli fakat normal çalışma aralığının üzerinde (Kritik Yüksek)", "CAC hava kanallarını dış çamur/böcek filtresi tıkanıklığına karşı temizleyin; radyatör önü hava akışını ve turbo wastegate performansını kontrol edin. Yüksek intercooler sıcaklığı emiş yoğunluğunu düşürerek güç kaybı yapar.", "MEDIUM"),
            "1": ("Data Valid But Below Normal Range - Most Severe Level", "Motor Şarj Hava Soğutucu (Intercooler) Sıcaklığı - Veri geçerli fakat normal çalışma aralığının altında (Kritik Düşük)", "Soğuk hava koşullarında beklenen değerdir; sensör okumasını el termometresi ile turbo çıkışı üzerinde karşılaştırın. Uçuk düşük değer sensör kaymasına işaret eder.", "LOW"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "Motor Şarj Hava Soğutucu (Intercooler) Sıcaklığı - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "NTC sensör soketini ayırıp sinyal pinini ölçün; ayrik halde okuma +40 °C civarına düşmüyorsa hat +5V'a kısa devre.", "MEDIUM"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "Motor Şarj Hava Soğutucu (Intercooler) Sıcaklığı - Voltaj normalin altında veya şasiye kısa devre", "Sensör direncini sıcaklığa göre tabloyla doğrulayın (NTC). Hat şasiye kısa devreyi sinyal-GND direnciyle tespit edin.", "MEDIUM"),
        },
    },
    "SPN_106": {
        "spn": 106, "name": "Air Inlet Pressure",
        "title_tr": "Hava Giriş Basıncı",
        "subsystem": "Hava Emiş & Turboşarj (CAC)",
        "associated_pgn": 65270, "pgn_acronym": "IC1",
        "range": "0.0 - 510.0 kPa", "unit": "kPa",
        "description": "Absolute air inlet pressure upstream of the turbocharger compressor inlet, 2 kPa/bit. Baseline for boost pressure ratio (SPN 102 / 106) calculation.",
        "fault_matrix": {
            "0": ("Data Valid But Above Normal Range - Most Severe Level", "Hava Giriş Basıncı - Veri geçerli fakat normal çalışma aralığının üzerinde (Kritik Yüksek)", "Sensör, turbo kompresör çıkışına mı emiş tarafına mı bağlanmış kontrol edin; yanlış taplama boost oranını bozar. Barometrik referans (SPN 108) ile karşılaştırın.", "MEDIUM"),
            "1": ("Data Valid But Below Normal Range - Most Severe Level", "Hava Giriş Basıncı - Veri geçerli fakat normal çalışma aralığının altında (Kritik Düşük)", "Emiş hattında aşırı vakum (tıkalı hava filtresi / çöken hortum) var mı manometreyle doğrulayın; hava filtresi servis göstergesini kontrol edin.", "LOW"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "Hava Giriş Basıncı - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "Sensör sinyal hattını ayrik halde ölçün; +5V kısa devresi ve soket pin eğilmesini kontrol edin.", "MEDIUM"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "Hava Giriş Basıncı - Voltaj normalin altında veya şasiye kısa devre", "Sinyal hattı şasi direncini ölçün; MAP tipi sensörün referans hattını doğrulayın.", "MEDIUM"),
        },
    },
    "SPN_107": {
        "spn": 107, "name": "Engine Air Filter 1 Differential Pressure",
        "title_tr": "Hava Filtresi Diferansiyel Basıncı",
        "subsystem": "Hava Emiş & Turboşarj (CAC)",
        "associated_pgn": 65270, "pgn_acronym": "IC1",
        "range": "0.0 - 12.75 kPa", "unit": "kPa",
        "description": "Air filter intake restriction measured as differential pressure across the filter element, 0.05 kPa/bit. Primary maintenance indicator for the air intake path.",
        "fault_matrix": {
            "0": ("Data Valid But Above Normal Range - Most Severe Level", "Hava Filtresi Diferansiyel Basıncı - Veri geçerli fakat normal çalışma aralığının üzerinde (Kritik Yüksek)", "Hava filtresi elemanını değiştirin; filtre kutusu ile turbo girişi arasındaki hortumda çökme/yabancı madde kontrolü yapın. Tıkalı filtre duman artışı ve güç kaybının en yaygın sebebidir.", "MEDIUM"),
            "1": ("Data Valid But Below Normal Range - Most Severe Level", "Hava Filtresi Diferansiyel Basıncı - Veri geçerli fakat normal çalışma aralığının altında (Kritik Düşük)", "Diferansiyel hortumlarında tıkanma/kondens suyu kontrol edin; yeni filtre sonrası düşük değer normaldir.", "LOW"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "Hava Filtresi Diferansiyel Basıncı - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "Diferansiyel basınç sensörünün sinyal hattını ve referans taplama noktalarını kontrol edin.", "LOW"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "Hava Filtresi Diferansiyel Basıncı - Voltaj normalin altında veya şasiye kısa devre", "Sensör soketi nem/korozyon kontrolü yapın; hattı şasiye karşı ölçün.", "LOW"),
        },
    },
    "SPN_127": {
        "spn": 127, "name": "Transmission 1 Oil Pressure",
        "title_tr": "Şanzıman 1 Yağ Basıncı",
        "subsystem": "Otomatik / AMT Şanzıman Kontrolü",
        "associated_pgn": 65272, "pgn_acronym": "TF1",
        "range": "0.0 - 4080.0 kPa", "unit": "kPa",
        "description": "Transmission lubrication/control oil pressure circuit 1, 16 kPa/bit. Low value indicates pump wear, filter clogging or internal leakage.",
        "fault_matrix": {
            "0": ("Data Valid But Above Normal Range - Most Severe Level", "Şanzıman 1 Yağ Basıncı - Veri geçerli fakat normal çalışma aralığının üzerinde (Kritik Yüksek)", "Basınç regülatör valfinin sıkışmasını ve yağ soğutucu hattının tıkanıklığını kontrol edin; TCU'da vites kalite adaptasyonu loglarını inceleyin.", "MEDIUM"),
            "1": ("Data Valid But Below Normal Range - Most Severe Level", "Şanzıman 1 Yağ Basıncı - Veri geçerli fakat normal çalışma aralığının altında (Kritik Düşük)", "Şanzıman yağ seviyesini doğru prosedürle (motor çalışır, 40-60 °C) kontrol edin. Yağ pompası aşınması, filtre tıkanıklığı ve iç kaçakları test portundan manometre ile doğrulayın. Düşük yağ basıncı dişli seti ve kavrama paketleri için acı müdahale gerektirir - aracı durdurun.", "CRITICAL_STOP"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "Şanzıman 1 Yağ Basıncı - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "Basınç sensörü soketini ayırın; sinyal pini hala yüksekse kablo demeti besleme hattına ezilmiş demektir.", "MEDIUM"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "Şanzıman 1 Yağ Basıncı - Voltaj normalin altında veya şasiye kısa devre", "Sensör sinyal hattı ile şasi arasındaki direnci ölçün; TCU konnektöründe nem/korozyon kontrolü yapın.", "MEDIUM"),
        },
    },
    "SPN_132": {
        "spn": 132, "name": "Engine Intake Air Mass Flow Rate",
        "title_tr": "Motor Emme Havası Kütle Debisi (MAF)",
        "subsystem": "Hava Emiş & Turboşarj (CAC)",
        "associated_pgn": 61450, "pgn_acronym": "EGFR1",
        "range": "0.0 - 3276.75 kg/h", "unit": "kg/h",
        "description": "Intake air mass flow measured by the mass air flow sensor, 0.05 kg/h/bit. Cross-reference for EGR flow (SPN 2659) and smoke limitation.",
        "fault_matrix": {
            "2": ("Data Erratic, Intermittent Or Incorrect", "Motor Emme Havası Kütle Debisi (MAF) - Veri tutarsız, aralıklı veya hatalı", "MAF sıcak tel yüzeyini özel spreyle temizleyin (mekanik temas yok); sabit devirde debi salınımını izleyin. Salınım, sensör kontaminasyonu veya emiş kaçağına işaret eder.", "MEDIUM"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "Motor Emme Havası Kütle Debisi (MAF) - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "MAF soketini ayırıp sinyal pinini ölçün; besleme hattına kısa devreyi ve +12V sızıntısını kontrol edin.", "MEDIUM"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "Motor Emme Havası Kütle Debisi (MAF) - Voltaj normalin altında veya şasiye kısa devre", "MAF besleme ve toprak hatlarını multimetrele doğrulayın; konnektör pinlerinde gerilme/kırık teli kontrol edin.", "MEDIUM"),
        },
    },
    "SPN_156": {
        "spn": 156, "name": "Engine Fuel 1 Injector Timing Rail 1 Pressure",
        "title_tr": "Motor Yakıt Enjektör Zamanlama Rayı 1 Basıncı",
        "subsystem": "Yüksek Basınç Yakıt Enjeksiyonu",
        "associated_pgn": 65243, "pgn_acronym": "EFL_P2",
        "range": "0.0 - 255.99 MPa", "unit": "MPa",
        "description": "Injector timing rail 1 pressure (1/256 MPa/bit). On some platforms the second rail-pressure channel alongside SPN 157/164.",
        "fault_matrix": {
            "0": ("Data Valid But Above Normal Range - Most Severe Level", "Motor Yakıt Enjektör Zamanlama Rayı 1 Basıncı - Veri geçerli fakat normal çalışma aralığının üzerinde (Kritik Yüksek)", "Basınç kontrol valfinin (PCV/MPROP) sıkışmasını ve basınç sensörü okumasının mekanik göstergeyle uyumunu doğrulayın. Aşırı ray basıncı enjektörlere ve yüksek basınç pompasına zarar verir - motoru derhal durdurun.", "CRITICAL_STOP"),
            "1": ("Data Valid But Below Normal Range - Most Severe Level", "Motor Yakıt Enjektör Zamanlama Rayı 1 Basıncı - Veri geçerli fakat normal çalışma aralığının altında (Kritik Düşük)", "Yakıt filtresini ve besleme pompası emiş tarafını kontrol edin; yüksek basınç pompası çıkışını, regülatör valfi kaçaklarını ve enjektör geri dönüş (leak-off) debilerini ölçün. Ray basıncı inşa edilemiyorsa motor çalışmayı keser.", "CRITICAL_STOP"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "Motor Yakıt Enjektör Zamanlama Rayı 1 Basıncı - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "Ray basınç sensörü soketini ayırın; sinyal hattı +5V'a kısa devreyi kontrol edin. Sensör besleme pin voltajını doğrulayın.", "MEDIUM"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "Motor Yakıt Enjektör Zamanlama Rayı 1 Basıncı - Voltaj normalin altında veya şasiye kısa devre", "Sensör sinyal hattı şasi direncini ölçün; kontaktor konnektöründe korozyon/pin gevşekliği arayın.", "MEDIUM"),
        },
    },
    "SPN_164": {
        "spn": 164, "name": "Engine Fuel Injection Control Pressure",
        "title_tr": "Motor Yakıt Enjeksiyon Kontrol Basıncı (Common Rail)",
        "subsystem": "Yüksek Basınç Yakıt Enjeksiyonu",
        "associated_pgn": 65243, "pgn_acronym": "EFL_P2",
        "range": "0.0 - 255.99 MPa", "unit": "MPa",
        "description": "Common rail fuel injection control pressure (1/256 MPa/bit). The primary rail pressure channel for many HEUI/common-rail platforms.",
        "fault_matrix": {
            "0": ("Data Valid But Above Normal Range - Most Severe Level", "Motor Yakıt Enjeksiyon Kontrol Basıncı (Common Rail) - Veri geçerli fakat normal çalışma aralığının üzerinde (Kritik Yüksek)", "Basınç regülatör valfi devresini ve sürücü çıkışını kontrol edin; sensör okuması ile gerçek basınç arasında sapma varsa sensörü değiştirin. Ray basıncı limit üstündeyse motoru durdurun.", "CRITICAL_STOP"),
            "1": ("Data Valid But Below Normal Range - Most Severe Level", "Motor Yakıt Enjeksiyon Kontrol Basıncı (Common Rail) - Veri geçerli fakat normal çalışma aralığının altında (Kritik Düşük)", "Şu sırayla izleyin: yakıt seviyesi/filtre -> besleme pompası çıkış basıncı -> yüksek basınç pompası geri kaçak -> regülatör valfi sızdırmazlık -> enjektör leak-off testi. Sabit rölantide hedef/gölge basınç sapmasını ServiceRanger/INSITE benzeri araçla izleyin.", "CRITICAL_STOP"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "Motor Yakıt Enjeksiyon Kontrol Basıncı (Common Rail) - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "Ray basınç sensörü 3 pinli konnektörünü kontrol edin; besleme (+5V), sinyal ve GND pin girilimlerini doğrulayın.", "MEDIUM"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "Motor Yakıt Enjeksiyon Kontrol Basıncı (Common Rail) - Voltaj normalin altında veya şasiye kısa devre", "Sinyal hattını şasiye karşı ölçün; sensör gövdesinde fiziksel hasar ve kablo demetinde sıkışma izlerini kontrol edin.", "MEDIUM"),
        },
    },
    "SPN_166": {
        "spn": 166, "name": "Engine Rated Power",
        "title_tr": "Motor Anma Gücü",
        "subsystem": "Motor Yönetimi & Tork Talebi",
        "associated_pgn": 65214, "pgn_acronym": "EEC4",
        "range": "0.0 - 32767.5 kW", "unit": "kW",
        "description": "Engine rated (maximum) power configuration broadcast in EEC4, 0.5 kW/bit. Calibration identity parameter used to validate correct ECU rating map.",
        "fault_matrix": {
            "2": ("Data Erratic, Intermittent Or Incorrect", "Motor Anma Gücü - Veri tutarsız, aralıklı veya hatalı", "Anma gücü değeri sabit bir konfigürasyon parametresidir; dalgalanma ECU yazılım/kalibrasyon eşleşmezliğine işaret eder. Araç kimlik kartındaki gücü (0.5 kW adımlarla) yayınlanan değerle karşılaştırın.", "LOW"),
            "12": ("Bad Intelligent Device Or Component", "Motor Anma Gücü - Hatalı akıllı aygıt veya bileşen", "Yayınlanan değer 0 kW veya plaka gücüyle çelişiyorsa ECU kalibrasyonunun doğru yüklendiğini (yanlış rating dosyası riski) doğrulayın.", "LOW"),
        },
    },
    "SPN_173": {
        "spn": 173, "name": "Engine Exhaust Temperature",
        "title_tr": "Egzoz Gazı Sıcaklığı",
        "subsystem": "Egzoz Gazı Sıcaklık İzleme",
        "associated_pgn": 65270, "pgn_acronym": "IC1",
        "range": T_HIGH, "unit": "°C",
        "description": "Exhaust gas temperature (0.03125 °C/bit, -273 °C offset) broadcast in Inlet/Exhaust Conditions 1. Manifold-side thermal load indicator for turbo and DPF protection.",
        "fault_matrix": {
            "0": ("Data Valid But Above Normal Range - Most Severe Level", "Egzoz Gazı Sıcaklığı - Veri geçerli fakat normal çalışma aralığının üzerinde (Kritik Yüksek)", "Turbo şaft ve DPF giriş sıcaklıklarını izleyin; enjektör sızıntısı, EGR kaçağı veya geç yanma kaynaklı aşırı egzoz sıcaklığını doğrulayın. Sıcaklık ~750 °C üstünde turbonun ve DPF'nin termal ömrü hızla tükenir - yükü azaltın.", "MEDIUM"),
            "1": ("Data Valid But Below Normal Range - Most Severe Level", "Egzoz Gazı Sıcaklığı - Veri geçerli fakat normal çalışma aralığının altında (Kritik Düşük)", "Sensörün doğru taplama noktasında olduğunu (manifold vs DOC girişi) teyit edin; termokupl/RTD bağlantı direncini ölçün. DPF rejenerasyonu başlatmak için gereken minimum sıcaklığın altında kalıyorsa rejenerasyon engellenir.", "LOW"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "Egzoz Gazı Sıcaklığı - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "Termokupl sinyal hatlarının (+/- kutup) doğru bağlandığını ve +5V beslemeye temas etmediğini kontrol edin; konnektörde nem köprüsünü arayın.", "MEDIUM"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "Egzoz Gazı Sıcaklığı - Voltaj normalin altında veya şasiye kısa devre", "Sensör hattı şasi direncini ölçün; egzoz manifoldu üzerindeki kablo klipslerinin eriyip hattı gövdeye değdirmediğini kontrol edin.", "MEDIUM"),
        },
    },
    "SPN_177": {
        "spn": 177, "name": "Transmission Oil Temperature 1",
        "title_tr": "Şanzıman 1 Yağ Sıcaklığı",
        "subsystem": "Otomatik / AMT Şanzıman Kontrolü",
        "associated_pgn": 65272, "pgn_acronym": "TF1",
        "range": T_HIGH, "unit": "°C",
        "description": "Transmission oil sump temperature circuit 1 (0.03125 °C/bit, -273 °C offset). Governs TCU clutch thermal derate strategy.",
        "fault_matrix": {
            "0": ("Data Valid But Above Normal Range - Most Severe Level", "Şanzıman 1 Yağ Sıcaklığı - Veri geçerli fakat normal çalışma aralığının üzerinde (Kritik Yüksek)", "Şanzıman yağ soğutucu hattını ve by-pass valfini kontrol edin; yağ seviyesi ve yağ tipinin (ATF spesifikasyonu) doğru olduğunu doğrulayın. TCU termal derate devrede olabilir - yükü azaltıp soğumaya bırakın.", "MEDIUM"),
            "1": ("Data Valid But Below Normal Range - Most Severe Level", "Şanzıman 1 Yağ Sıcaklığı - Veri geçerli fakat normal çalışma aralığının altında (Kritik Düşük)", "Soğuk çalıştırmada beklenir; sensör okumasını el termometresiyle karter yüzeyinden karşılaştırın.", "LOW"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "Şanzıman 1 Yağ Sıcaklığı - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "Sensör soketini ayırın; sinyal hattı beslemeye kısa devreyi kontrol edin.", "MEDIUM"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "Şanzıman 1 Yağ Sıcaklığı - Voltaj normalin altında veya şasiye kısa devre", "Sensör direncini sıcaklık tablosuyla karşılaştırın; hattı şasiye karşı ölçün, TCU konnektörünü korozyona karşı inceleyin.", "MEDIUM"),
        },
    },
    "SPN_182": {
        "spn": 182, "name": "Engine Trip Fuel",
        "title_tr": "Motor Sefer (Trip) Yakıt Tüketimi",
        "subsystem": "Yakıt Ekonomisi & Telemetri",
        "associated_pgn": 64777, "pgn_acronym": "FC1",
        "range": "0.0 - 2147483647.5 L", "unit": "L",
        "description": "Accumulated trip fuel consumption, 0.5 litre/bit. Fleet trip-report parameter; resets on trip reset via SPN 182 reset command.",
        "fault_matrix": {
            "2": ("Data Erratic, Intermittent Or Incorrect", "Motor Sefer (Trip) Yakıt Tüketimi - Veri tutarsız, aralıklı veya hatalı", "Sayaç değerinin SPN 183 (anlık yakıt tüketimi) integraliyle tutarlılığını kontrol edin; sıçrama, sayacın ECU'da sıfırlanmadan yeniden başlatıldığına işaret eder.", "LOW"),
            "9": ("Abnormal Update Rate", "Motor Sefer (Trip) Yakıt Tüketimi - Anormal güncelleme hızı", "PGN 64777 yayın periyodunu sniffer'da izleyin; kesintiye uğrayan yayın ECU veri yoluna erişim problemidir.", "LOW"),
            "19": ("Received Network Data In Error", "Motor Sefer (Trip) Yakıt Tüketimi - Alınan ağ verisi hatalı", "J1939-21 taşırma protokolü (BAM) frame kayıplarını ve bus-load durumunu kontrol edin; hatalı CRC'li çerçeveler fiziksel katman problemine işaret eder.", "LOW"),
        },
    },
    "SPN_184": {
        "spn": 184, "name": "Instantaneous Fuel Economy",
        "title_tr": "Anlık Yakıt Ekonomisi",
        "subsystem": "Yakıt Ekonomisi & Telemetri",
        "associated_pgn": 65266, "pgn_acronym": "LFE1",
        "range": "0.0 - 127.99 km/l", "unit": "km/l",
        "description": "Instantaneous fuel economy, 1/512 km/l per bit. Derived by the ECU from fuel rate (SPN 183) and wheel-based vehicle speed (SPN 84).",
        "fault_matrix": {
            "2": ("Data Erratic, Intermittent Or Incorrect", "Anlık Yakıt Ekonomisi - Veri tutarsız, aralıklı veya hatalı", "SPN 183 (yakıt debisi) ve SPN 84 (tekerlek bazlı hız) kaynak değerlerini ayrı ayrı doğrulayın; türetilmiş değerin sapması kaynak sensörlerden birindedir.", "LOW"),
            "9": ("Abnormal Update Rate", "Anlık Yakıt Ekonomisi - Anormal güncelleme hızı", "PGN 65266 yayın periyodunu izleyin; 100 ms nominal periyodun dışına çıkan yayın ECU yüklenmesine işaret eder.", "LOW"),
        },
    },
    "SPN_185": {
        "spn": 185, "name": "Engine Average Fuel Economy",
        "title_tr": "Motor Ortalama Yakıt Ekonomisi",
        "subsystem": "Yakıt Ekonomisi & Telemetri",
        "associated_pgn": 65266, "pgn_acronym": "LFE1",
        "range": "0.0 - 127.99 km/l", "unit": "km/l",
        "description": "Average (lifetime/trip) fuel economy, 1/512 km/l per bit. Fleet benchmarking parameter computed by the ECU.",
        "fault_matrix": {
            "2": ("Data Erratic, Intermittent Or Incorrect", "Motor Ortalama Yakıt Ekonomisi - Veri tutarsız, aralıklı veya hatalı", "Toplam yakıt (SPN 250) ve toplam mesafe (hız integrali) tutarlılığını kontrol edin; tekerlek çapı parametresi hatalıysa ortalama ekonomi sistematik sapar.", "LOW"),
            "9": ("Abnormal Update Rate", "Motor Ortalama Yakıt Ekonomisi - Anormal güncelleme hızı", "PGN 65266 yayınını sniffer'da izleyin; ECU yanıt gecikmesini ve bus hata sayacını kontrol edin.", "LOW"),
        },
    },
    "SPN_189": {
        "spn": 189, "name": "Engine Rated Speed",
        "title_tr": "Motor Anma Devri",
        "subsystem": "Motor Yönetimi & Tork Talebi",
        "associated_pgn": 65214, "pgn_acronym": "EEC4",
        "range": RPM_HR, "unit": "rpm",
        "description": "Engine rated speed configuration, 0.125 rpm/bit. Governor reference parameter; must match the calibration identity (e.g. 1900/2100/2300 rpm ratings).",
        "fault_matrix": {
            "2": ("Data Erratic, Intermittent Or Incorrect", "Motor Anma Devri - Veri tutarsız, aralıklı veya hatalı", "Anma devri sabit bir kalibrasyon parametresidir; dalgalanma ECU yazılım uyumsuzluğuna işaret eder. Motor plakasındaki devirle yayınlanan değeri karşılaştırın.", "LOW"),
            "12": ("Bad Intelligent Device Or Component", "Motor Anma Devri - Hatalı akıllı aygıt veya bileşen", "Yayınlanan değer 0 rpm ise ECU kalibrasyonunun bozulduğunu düşünün; kalibrasyon kimlik çiftini (SPN 166 + 189) birlikte doğrulayın.", "LOW"),
        },
    },
    "SPN_235": {
        "spn": 235, "name": "Engine Total Idle Hours",
        "title_tr": "Motor Toplam Rölanti Çalışma Saati",
        "subsystem": "Yakıt Ekonomisi & Telemetri",
        "associated_pgn": 65244, "pgn_acronym": "IDLE_OP",
        "range": "0.0 - 214748364.75 h", "unit": "h",
        "description": "Accumulated engine idle operation hours, 0.05 hour/bit. Fleet idle-reduction KPI; ratio to SPN 247 drives maintenance interval policy.",
        "fault_matrix": {
            "2": ("Data Erratic, Intermittent Or Incorrect", "Motor Toplam Rölanti Çalışma Saati - Veri tutarsız, aralıklı veya hatalı", "Rölanti saatinin toplam çalışma saatine (SPN 247) oranını kontrol edin; rölanti tanımı ECU kalibrasyonuna bağlıdır, sayaç sıfırlaması ECU değişiminde kaybolur.", "LOW"),
            "9": ("Abnormal Update Rate", "Motor Toplam Rölanti Çalışma Saati - Anormal güncelleme hızı", "PGN 65244 yayın periyodunu izleyin; kesintili yayın ECU çökme/yeniden başlama döngüsüne işaret edebilir.", "LOW"),
            "19": ("Received Network Data In Error", "Motor Toplam Rölanti Çalışma Saati - Alınan ağ verisi hatalı", "DM1 ağ hatalarını ve bus istatistiklerini kontrol edin; tekrarlayan çerçeve hatası terminasyon/sinyal bütünlüğü problemidir.", "LOW"),
        },
    },
    "SPN_236": {
        "spn": 236, "name": "Engine Total Idle Fuel Used",
        "title_tr": "Motor Toplam Rölanti Yakıt Tüketimi",
        "subsystem": "Yakıt Ekonomisi & Telemetri",
        "associated_pgn": 65244, "pgn_acronym": "IDLE_OP",
        "range": "0.0 - 2147483647.5 L", "unit": "L",
        "description": "Accumulated fuel consumed during idle operation, 0.5 litre/bit. Idle-reduction business case metric (SPN 236 / SPN 250 ratio).",
        "fault_matrix": {
            "2": ("Data Erratic, Intermittent Or Incorrect", "Motor Toplam Rölanti Yakıt Tüketimi - Veri tutarsız, aralıklı veya hatalı", "Rölanti yakıt sayacının anlık debi (SPN 183) integraliyle tutarlılığını kontrol edin; PTO çalışması rölanti tanımına dahil olabilir, kalibrasyonu doğrulayın.", "LOW"),
            "9": ("Abnormal Update Rate", "Motor Toplam Rölanti Yakıt Tüketimi - Anormal güncelleme hızı", "PGN 65244 yayınını sniffer'da izleyin; boş 0xFFFF değerleri ECU'nun parametreyi desteklemediğini gösterir.", "LOW"),
            "19": ("Received Network Data In Error", "Motor Toplam Rölanti Yakıt Tüketimi - Alınan ağ verisi hatalı", "Fiziksel katman hatalarını (bus-off, CRC) kontrol edin; DM1 ağ hata kayıtlarıyla eşleştirin.", "LOW"),
        },
    },
    "SPN_250": {
        "spn": 250, "name": "Engine Total Fuel Used",
        "title_tr": "Motor Toplam Yakıt Tüketimi",
        "subsystem": "Yakıt Ekonomisi & Telemetri",
        "associated_pgn": 64777, "pgn_acronym": "FC1",
        "range": "0.0 - 2147483647.5 L", "unit": "L",
        "description": "Total fuel consumed by the engine since manufacture/last reset, 0.5 litre/bit. Primary fuel-fraud detection and benchmarking parameter.",
        "fault_matrix": {
            "2": ("Data Erratic, Intermittent Or Incorrect", "Motor Toplam Yakıt Tüketimi - Veri tutarsız, aralıklı veya hatalı", "Toplam yakıt değerini yakıt pompası fiş/raporlarıyla karşılaştırın; sistem sapması için SPN 183 (anlık debi) kalibrasyonunu doğrulayın. Sayaç gerilemesi ECU değişimi veya kalibrasyon sıfırlamasıdır.", "LOW"),
            "9": ("Abnormal Update Rate", "Motor Toplam Yakıt Tüketimi - Anormal güncelleme hızı", "PGN 64777 yayın periyodunu sniffer'da izleyin; 1 s nominal periyodun sapması ECU yüklenmesine işaret eder.", "LOW"),
            "19": ("Received Network Data In Error", "Motor Toplam Yakıt Tüketimi - Alınan ağ verisi hatalı", "J1939-21 BAM taşırma çerçevelerinin bütünlüğünü ve bus hata sayaçlarını kontrol edin.", "LOW"),
        },
    },
    "SPN_411": {
        "spn": 411, "name": "Engine Exhaust Gas Recirculation 1 Differential Pressure",
        "title_tr": "EGR 1 Diferansiyel Basıncı",
        "subsystem": "EGR Sistemi (Egzoz Gazı Geri Dönüşümü)",
        "associated_pgn": 65188, "pgn_acronym": "ET2",
        "range": "-250.0 - 261.99 kPa", "unit": "kPa",
        "description": "EGR 1 loop differential pressure across the venturi/orifice (1/128 kPa/bit, -250 kPa offset). Governs EGR mass flow calculation (SPN 2659).",
        "fault_matrix": {
            "0": ("Data Valid But Above Normal Range - Most Severe Level", "EGR 1 Diferansiyel Basıncı - Veri geçerli fakat normal çalışma aralığının üzerinde (Kritik Yüksek)", "Venturi/orifikatta kurum birikmesini kontrol edin; EGR akış hesabı basınç farkından türetildiği için yanlış fark, aşırı EGR ve duman artışı yapar. Basınç taplama noktalarını kurumdan temizleyin.", "MEDIUM"),
            "1": ("Data Valid But Below Normal Range - Most Severe Level", "EGR 1 Diferansiyel Basıncı - Veri geçerli fakat normal çalışma aralığının altında (Kritik Düşük)", "Diferansiyel hatlarında kondens suyu/kurum tıkanıklığını kontrol edin; sıfıra sabitlenen fark sensör sıfır kayması veya tıkalı taplama demektir.", "MEDIUM"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "EGR 1 Diferansiyel Basıncı - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "Diferansiyel sensör soketini ayırıp sinyal pinini ölçün; +5V kısa devresini ve pin korozyonunu kontrol edin.", "MEDIUM"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "EGR 1 Diferansiyel Basıncı - Voltaj normalin altında veya şasiye kısa devre", "Sensör sinyal hattını şasiye karşı ölçün; egzoz tarafındaki hattın ısıl hasarını kontrol edin.", "MEDIUM"),
        },
    },
    "SPN_412": {
        "spn": 412, "name": "Engine Exhaust Gas Recirculation 1 Temperature",
        "title_tr": "EGR 1 Gaz Sıcaklığı",
        "subsystem": "EGR Sistemi (Egzoz Gazı Geri Dönüşümü)",
        "associated_pgn": 65188, "pgn_acronym": "ET2",
        "range": T_HIGH, "unit": "°C",
        "description": "EGR 1 loop gas temperature (0.03125 °C/bit, -273 °C offset). EGR cooler effectiveness and condensation protection parameter.",
        "fault_matrix": {
            "0": ("Data Valid But Above Normal Range - Most Severe Level", "EGR 1 Gaz Sıcaklığı - Veri geçerli fakat normal çalışma aralığının üzerinde (Kritik Yüksek)", "EGR soğutucu etkinliğini doğrulayın: soğutucu su giriş/çıkış sıcaklıklarını ölçün, soğutucu içi kurum tıkanıklığını kontrol edin. Etkisiz soğutucu NOx artışı ve emiş vanası ısıl hasarı yapar.", "MEDIUM"),
            "1": ("Data Valid But Below Normal Range - Most Severe Level", "EGR 1 Gaz Sıcaklığı - Veri geçerli fakat normal çalışma aralığının altında (Kritik Düşük)", "Sensörün EGR soğutucusunun sıcak (egzoz) tarafında mı soğuk (emme) tarafında mı ölçtüğünü teyit edin; taplama yeri şemayla karşılaştırın.", "LOW"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "EGR 1 Gaz Sıcaklığı - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "Sensör soketini ayırın; sinyal hattı +5V kısa devresini ve konnektör nem köprüsünü kontrol edin.", "MEDIUM"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "EGR 1 Gaz Sıcaklığı - Voltaj normalin altında veya şasiye kısa devre", "Sensör direncini sıcaklık tablosuyla karşılaştırın; hat şasi kısa devresini ölçün.", "MEDIUM"),
        },
    },
    "SPN_512": {
        "spn": 512, "name": "Driver's Demand Engine - Percent Torque",
        "title_tr": "Sürücü Talebi - Motor Yüzde Torku",
        "subsystem": "Elektronik Gaz Kelebeği & Sürücü Talebi",
        "associated_pgn": 61444, "pgn_acronym": "EEC1",
        "range": TORQUE_PCT, "unit": "%",
        "description": "Driver's demanded engine percent torque (1 %/bit, -125 offset) broadcast in EEC1. Pedal position translated to torque demand after driveline arbitration.",
        "fault_matrix": {
            "2": ("Data Erratic, Intermittent Or Incorrect", "Sürücü Talebi - Motor Yüzde Torku - Veri tutarsız, aralıklı veya hatalı", "SPN 91 (pedal pozisyonu) ile SPN 512'nin oransal eşleşmesini sabit pedal konumlarında izleyin; sapma, pedal adaptasyonunun (pedal tarama/öğrenme) bozulduğunu gösterir - prosedürü tekrarlayın.", "MEDIUM"),
            "12": ("Bad Intelligent Device Or Component", "Sürücü Talebi - Motor Yüzde Torku - Hatalı akıllı aygıt veya bileşen", "Pedal talebi sabit -125 % (0 talep) veya 125 %'ye kilitliyse ECU içi talep hesaplama hattını kontrol edin; yazılım güncellemesi ve pedal kalibrasyonunu yeniden yapın.", "MEDIUM"),
        },
    },
    "SPN_514": {
        "spn": 514, "name": "Nominal Friction - Percent Torque",
        "title_tr": "Nominal Sürtünme - Yüzde Tork",
        "subsystem": "Motor Yönetimi & Tork Talebi",
        "associated_pgn": 65247, "pgn_acronym": "EEC3",
        "range": TORQUE_PCT, "unit": "%",
        "description": "Engine's nominal friction percent torque (1 %/bit, -125 offset) broadcast in EEC3. Internal friction estimate subtracted from indicated torque.",
        "fault_matrix": {
            "2": ("Data Erratic, Intermittent Or Incorrect", "Nominal Sürtünme - Yüzde Tork - Veri tutarsız, aralıklı veya hatalı", "Sürtünme torku yağ viskozitesi ve sıcaklığıyla değişir; SPN 175 (yağ sıcaklığı) ile birlikte izleyin. Soğukta yüksek, ısınmış motorda düşük değer normaldir.", "LOW"),
            "12": ("Bad Intelligent Device Or Component", "Nominal Sürtünme - Yüzde Tork - Hatalı akıllı aygıt veya bileşen", "Değer sürekli -125 % veya uçuk yüksekse ECU sürtünme modeli kalibrasyonunu kontrol edin; yağ sınıfı değişimi model sapması yapabilir.", "LOW"),
        },
    },
    "SPN_515": {
        "spn": 515, "name": "Engine's Desired Operating Speed",
        "title_tr": "Motor İstenen Çalışma Devri",
        "subsystem": "Motor Yönetimi & Tork Talebi",
        "associated_pgn": 65247, "pgn_acronym": "EEC3",
        "range": RPM_HR, "unit": "rpm",
        "description": "Engine's desired operating speed setpoint (0.125 rpm/bit) broadcast in EEC3. Cruise control, PTO and remote-throttle governor reference.",
        "fault_matrix": {
            "2": ("Data Erratic, Intermittent Or Incorrect", "Motor İstenen Çalışma Devri - Veri tutarsız, aralıklı veya hatalı", "Hız sabitleyici (SPN 371 cruise aktif) kapalıyken değer rölanti devrine dönmelidir; devirde dalgalanma uzaktan gaz (PTO) potansiyometresi gürültüsünden gelebilir - hat taramasını kontrol edin.", "MEDIUM"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "Motor İstenen Çalışma Devri - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "PTO/uzaktan gaz potansiyometresi sinyal hattını +5V'a karşı ölçün; soket pin gevşekliğini kontrol edin.", "MEDIUM"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "Motor İstenen Çalışma Devri - Voltaj normalin altında veya şasiye kısa devre", "Potansiyometre hatlarını şasiye karşı ölçün; PTO konsol konnektöründe korozyon arayın.", "MEDIUM"),
        },
    },
    "SPN_523": {
        "spn": 523, "name": "Transmission Current Gear",
        "title_tr": "Şanzıman Mevcut Vitesi",
        "subsystem": "Otomatik / Robotize Şanzıman (TCU)",
        "associated_pgn": 61445, "pgn_acronym": "ETC2",
        "range": "-125 - 125", "unit": "-",
        "description": "Transmission current gear, offset -125 (1/bit); negative values are reverse gears, 0 is neutral. Broadcast in ETC2 for driveline coordination.",
        "fault_matrix": {
            "2": ("Data Erratic, Intermittent Or Incorrect", "Şanzıman Mevcut Vitesi - Veri tutarsız, aralıklı veya hatalı", "SPN 523 (mevcut vites) ile SPN 522/524 (seçili/istenen vites) geçiş zamanlamasını izleyin; senkron gecikmesi kavrama/senkronizatör aşınmasına işaret eder. Vites sensörü (range sensor) kalibrasyonunu doğrulayın.", "MEDIUM"),
            "12": ("Bad Intelligent Device Or Component", "Şanzıman Mevcut Vitesi - Hatalı akıllı aygıt veya bileşen", "Vites değeri -125'e kilitliyse (0 = nötr) vites aralığı sensörünü ve TCU referans voltajlarını kontrol edin; sensör kalibrasyon prosedürünü tekrarlayın.", "MEDIUM"),
            "19": ("Received Network Data In Error", "Şanzıman Mevcut Vitesi - Alınan ağ verisi hatalı", "PGN 61445 çerçeve bütünlüğünü ve TCU kaynak adresinin (SPN 1483 benzeri) doğru olduğunu kontrol edin; DM1 ağ hatalarıyla eşleştirin.", "MEDIUM"),
        },
    },
    "SPN_975": {
        "spn": 975, "name": "Engine Fan 1 Estimated Percent Speed",
        "title_tr": "Motor Fanı 1 Tahmini Yüzde Hızı",
        "subsystem": "Motor Soğutma & Termal Yönetim",
        "associated_pgn": 65213, "pgn_acronym": "FDC",
        "range": PEDAL_PCT, "unit": "%",
        "description": "Estimated cooling fan percent speed, 0.4 %/bit. Fan clutch engagement feedback for thermal management diagnosis.",
        "fault_matrix": {
            "2": ("Data Erratic, Intermittent Or Incorrect", "Motor Fanı 1 Tahmini Yüzde Hızı - Veri tutarsız, aralıklı veya hatalı", "Fan hızını SPN 647 (fan sürücü durumu) ve soğutma suyu sıcaklığıyla (SPN 110) birlikte izleyin; tahmini hız ile fiziksel fan devri (stroboskop/foto takometre) arasındaki sapma kavrama kaymasına işaret eder.", "LOW"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "Motor Fanı 1 Tahmini Yüzde Hızı - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "Fan sürücü valfi/sensör hattını +5V'a karşı ölçün; hidrolik fan konnektörünü yağ kaçağına karşı kontrol edin.", "LOW"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "Motor Fanı 1 Tahmini Yüzde Hızı - Voltaj normalin altında veya şasiye kısa devre", "Fan sürücü hattını şasiye karşı ölçün; fan klutc solenoid bobin direncini ölçün.", "LOW"),
        },
    },
    "SPN_2432": {
        "spn": 2432, "name": "Engine Demand - Percent Torque",
        "title_tr": "Motor Talebi - Yüzde Tork",
        "subsystem": "Motor Yönetimi & Tork Talebi",
        "associated_pgn": 61444, "pgn_acronym": "EEC1",
        "range": TORQUE_PCT, "unit": "%",
        "description": "Engine demanded percent torque (1 %/bit, -125 offset) broadcast in EEC1. Final torque command after all limitation/protection arbitration.",
        "fault_matrix": {
            "2": ("Data Erratic, Intermittent Or Incorrect", "Motor Talebi - Yüzde Tork - Veri tutarsız, aralıklı veya hatalı", "SPN 512 (sürücü talebi) ile SPN 2432 arasındaki farkı izleyin; fark, aktif bir kısıtlama (SPN 3644 derate, retarder, hız limiti SPN 1437) tarafından yaratılıyor olabilir - kısıt kaynaklarını listeleyin.", "MEDIUM"),
            "12": ("Bad Intelligent Device Or Component", "Motor Talebi - Yüzde Tork - Hatalı akıllı aygıt veya bileşen", "Talep sürekli -125 %'ye kilitliyse ECU içi tork arbitrajını kontrol edin; yazılım sürümü ve kalibrasyon kimliğini doğrulayın.", "MEDIUM"),
        },
    },
    "SPN_2659": {
        "spn": 2659, "name": "Engine Exhaust Gas Recirculation 1 Mass Flow Rate",
        "title_tr": "EGR 1 Kütle Debisi",
        "subsystem": "EGR Sistemi (Egzoz Gazı Geri Dönüşümü)",
        "associated_pgn": 61450, "pgn_acronym": "EGFR1",
        "range": "0.0 - 3276.75 kg/h", "unit": "kg/h",
        "description": "Recirculated exhaust gas mass flow through EGR 1, 0.05 kg/h/bit. Derived from venturi differential pressure (SPN 411) and gas temperature (SPN 412).",
        "fault_matrix": {
            "2": ("Data Erratic, Intermittent Or Incorrect", "EGR 1 Kütle Debisi - Veri tutarsız, aralıklı veya hatalı", "Debi, SPN 411 (basınç farkı) ve SPN 412 (sıcaklık) türevi olduğundan önce bu ikisini doğrulayın; debi sıfıra sabitlenmişse venturi kurum tıkanıklığını kontrol edin.", "MEDIUM"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "EGR 1 Kütle Debisi - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "Debi hesabı ECU içi olduğundan fiziksel hat arızası beklenmez; kaynak basınç sensörü hattını kontrol edin.", "LOW"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "EGR 1 Kütle Debisi - Voltaj normalin altında veya şasiye kısa devre", "Kaynak diferansiyel basınç sensörü hattını şasiye karşı ölçün; EGR debisinin türetilmiş olduğunu unutmayın.", "LOW"),
        },
    },
    "SPN_3250": {
        "spn": 3250, "name": "Aftertreatment 1 Diesel Particulate Filter Intermediate Temperature",
        "title_tr": "AFT1 DPF Ara (Intermediate) Sıcaklığı",
        "subsystem": "Egzoz & DPF Termal Rejenerasyonu",
        "associated_pgn": 64946, "pgn_acronym": "A1D3",
        "range": T_HIGH, "unit": "°C",
        "description": "Aftertreatment 1 diesel particulate filter mid-bed (ceramic) temperature, 0.03125 °C/bit. Regeneration thermal-zone verification parameter.",
        "fault_matrix": {
            "0": ("Data Valid But Above Normal Range - Most Severe Level", "AFT1 DPF Ara (Intermediate) Sıcaklığı - Veri geçerli fakat normal çalışma aralığının üzerinde (Kritik Yüksek)", "Rejenerasyon sırasında seramik sıcaklığı taşarsa DPF çatlaması/ergime riski vardır; rejenerasyonu iptal edin, dozaj valfi (SPN 3361) debisini ve DOC çıkış sıcaklığını (SPN 4766) kontrol edin.", "MEDIUM"),
            "1": ("Data Valid But Below Normal Range - Most Severe Level", "AFT1 DPF Ara (Intermediate) Sıcaklığı - Veri geçerli fakat normal çalışma aralığının altında (Kritik Düşük)", "Rejenerasyon başlamış ama seramik sıcaklığı 550 °C'ye ulaşmıyorsa yetersiz yakıt dozajı veya DOC etkinlik kaybı vardır; pasif rejenerasyon sıklığını izleyin.", "LOW"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "AFT1 DPF Ara (Intermediate) Sıcaklığı - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "DPF üzerindeki termokupl konnektörünü kontrol edin; sinyal hattı +5V kısa devresini ve egzoz sıcaklığındaki klips erimelerini inceleyin.", "MEDIUM"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "AFT1 DPF Ara (Intermediate) Sıcaklığı - Voltaj normalin altında veya şasiye kısa devre", "Termokupl süreklilik testi yapın (açık devre = -273 °C okuması); hattı şasiye karşı ölçün.", "MEDIUM"),
        },
    },
    "SPN_3609": {
        "spn": 3609, "name": "Aftertreatment 1 Diesel Particulate Filter Intake Pressure",
        "title_tr": "AFT1 DPF Giriş Basıncı",
        "subsystem": "Egzoz & DPF Kurum Yönetimi",
        "associated_pgn": 64908, "pgn_acronym": "AT1GASP",
        "range": "0.0 - 6553.5 kPa", "unit": "kPa",
        "description": "Aftertreatment 1 DPF inlet absolute pressure, 0.1 kPa/bit. Soot load estimation input (delta with SPN 3610 drives SPN 3251 differential).",
        "fault_matrix": {
            "0": ("Data Valid But Above Normal Range - Most Severe Level", "AFT1 DPF Giriş Basıncı - Veri geçerli fakat normal çalışma aralığının üzerinde (Kritik Yüksek)", "DPF giriş basıncı çıkışa (SPN 3610) göre yüksek fark gösteriyorsa kurum yükü kritiktir; SPN 3719 (kurum yükü) ve SPN 3251 (diferansiyel) ile birlikte değerlendirin. Zorunlu rejenerasyon gerekebilir.", "MEDIUM"),
            "1": ("Data Valid But Below Normal Range - Most Severe Level", "AFT1 DPF Giriş Basıncı - Veri geçerli fakat normal çalışma aralığının altında (Kritik Düşük)", "Basınç taplama hattının kurum/su ile tıkanmadığını kontrol edin; tıkalı hat okumayı atmosfere sabitler ve kurum yükünü yanlış düşük gösterir.", "LOW"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "AFT1 DPF Giriş Basıncı - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "Basınç sensörü soketini ayırın; sinyal hattı +5V kısa devresini kontrol edin, DPF üzerindeki taplama hortumlarının erimesine bakın.", "MEDIUM"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "AFT1 DPF Giriş Basıncı - Voltaj normalin altında veya şasiye kısa devre", "Sensör hattını şasiye karşı ölçün; taplama hortumunda çatlak/sızıntı okumayı düşürür.", "MEDIUM"),
        },
    },
    "SPN_3610": {
        "spn": 3610, "name": "Aftertreatment 1 Diesel Particulate Filter Outlet Pressure",
        "title_tr": "AFT1 DPF Çıkış Basıncı",
        "subsystem": "Egzoz & DPF Kurum Yönetimi",
        "associated_pgn": 64908, "pgn_acronym": "AT1GASP",
        "range": "0.0 - 6553.5 kPa", "unit": "kPa",
        "description": "Aftertreatment 1 DPF outlet absolute pressure, 0.1 kPa/bit. Reference leg of the DPF differential pressure (SPN 3251) soot-load calculation.",
        "fault_matrix": {
            "0": ("Data Valid But Above Normal Range - Most Severe Level", "AFT1 DPF Çıkış Basıncı - Veri geçerli fakat normal çalışma aralığının üzerinde (Kritik Yüksek)", "Çıkış basıncının kendisi yüksekseniz tıkanıklık DPF sonrasıdır (muffler/katalizör); SPN 3609 ile farkı izleyin - düşük farkla yüksek mutlak basınç egzoz çıkışı kısıtına işaret eder.", "MEDIUM"),
            "1": ("Data Valid But Below Normal Range - Most Severe Level", "AFT1 DPF Çıkış Basıncı - Veri geçerli fakat normal çalışma aralığının altında (Kritik Düşük)", "Çıkış basınç hattının tıkanıklığını ve sızıntısını kontrol edin; hatalı düşük çıkış okuması diferansiyeli şişirerek sahte kurum yükü alarmı üretir.", "LOW"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "AFT1 DPF Çıkış Basıncı - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "Sensör soketini ayırıp sinyal pinini ölçün; +5V besleme kısa devresini kontrol edin.", "MEDIUM"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "AFT1 DPF Çıkış Basıncı - Voltaj normalin altında veya şasiye kısa devre", "Sensör hattı şasi direncini ölçün; taplama hortumunda kondens suyu birikimini boşaltın.", "MEDIUM"),
        },
    },
    "SPN_3644": {
        "spn": 3644, "name": "Engine Derate Request",
        "title_tr": "Motor Güç Düşürme (Derate) Talebi",
        "subsystem": "Motor Yönetimi & Tork Talebi",
        "associated_pgn": 64914, "pgn_acronym": "EOI",
        "range": PEDAL_PCT, "unit": "%",
        "description": "Percent torque derate requested by engine protection strategy, 0.4 %/bit. Active value with SPN 3644 FMI 0/31 means torque limiting is engaged.",
        "fault_matrix": {
            "0": ("Data Valid But Above Normal Range - Most Severe Level", "Motor Güç Düşürme (Derate) Talebi - Veri geçerli fakat normal çalışma aralığının üzerinde (Kritik Yüksek)", "Derate talebini tetikleyen aktif DM1 arızalarını listeleyin (SPN 3216 NOx verimliliği, SPN 3251 kurum yükü, DEF kalitesi SPN 3364 gibi); kök arızayı çözmeden derate kalkmaz. Aracı güvenli alana alın.", "CRITICAL_STOP"),
            "31": ("Condition Exists", "Motor Güç Düşürme (Derate) Talebi - Durum mevcut", "Derate koşulu aktif: mevcut DM1/DM2 arıza listesini (SPN-FMI çiftleriyle) çıkarın ve emisyon/thermal koruma kaynaklı kısıtları önceliklendirin; sıralı kalibrasyon bazlı kalkanma prosedürünü uygulayın.", "MEDIUM"),
        },
    },
    "SPN_4765": {
        "spn": 4765, "name": "Aftertreatment 1 Diesel Oxidation Catalyst Intake Gas Temperature",
        "title_tr": "AFT1 DOC Giriş Gaz Sıcaklığı",
        "subsystem": "Egzoz & DPF Termal Rejenerasyonu",
        "associated_pgn": 64800, "pgn_acronym": "AT1DOC",
        "range": T_HIGH, "unit": "°C",
        "description": "Aftertreatment 1 DOC inlet gas temperature, 0.03125 °C/bit. Reference input for DOC conversion efficiency (SPN 4766 - 4765) and dosing strategy.",
        "fault_matrix": {
            "0": ("Data Valid But Above Normal Range - Most Severe Level", "AFT1 DOC Giriş Gaz Sıcaklığı - Veri geçerli fakat normal çalışma aralığının üzerinde (Kritik Yüksek)", "Manifold egzoz sıcaklığı (SPN 173) ile karşılaştırın; normalden yüksek giriş, motor tarafında aşırı sıcaklık demektir. Geç yanma/enjektör kaçaklarını kontrol edin.", "MEDIUM"),
            "1": ("Data Valid But Below Normal Range - Most Severe Level", "AFT1 DOC Giriş Gaz Sıcaklığı - Veri geçerli fakat normal çalışma aralığının altında (Kritik Düşük)", "Rejenerasyon için gereken minimum DOC giriş sıcaklığına (yaklaşık 250 °C) ulaşılamıyorsa dozaj başlamaz; düşük yük koşullarında beklenen durumdur.", "LOW"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "AFT1 DOC Giriş Gaz Sıcaklığı - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "Termokupl kutup bağlantılarını ve +5V besleme temasını kontrol edin; konnektör nemini kurutun.", "MEDIUM"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "AFT1 DOC Giriş Gaz Sıcaklığı - Voltaj normalin altında veya şasiye kısa devre", "Termokupl süreklilik ve izolasyon direncini ölçün; egzoz hattındaki kablo klipslerini ısıl hasara karşı kontrol edin.", "MEDIUM"),
        },
    },
    "SPN_4766": {
        "spn": 4766, "name": "Aftertreatment 1 Diesel Oxidation Catalyst Outlet Gas Temperature",
        "title_tr": "AFT1 DOC Çıkış Gaz Sıcaklığı",
        "subsystem": "Egzoz & DPF Termal Rejenerasyonu",
        "associated_pgn": 64800, "pgn_acronym": "AT1DOC",
        "range": T_HIGH, "unit": "°C",
        "description": "Aftertreatment 1 DOC outlet gas temperature, 0.03125 °C/bit. During regeneration the exothermic rise over SPN 4765 indicates DOC/DPF health.",
        "fault_matrix": {
            "0": ("Data Valid But Above Normal Range - Most Severe Level", "AFT1 DOC Çıkış Gaz Sıcaklığı - Veri geçerli fakat normal çalışma aralığının üzerinde (Kritik Yüksek)", "Rejenerasyon sırasında DOC çıkışında beklenen ekzotermik artış (girişe göre +100..200 °C) aşırıysa dozaj valfi debisini ve HC sızıntısını kontrol edin; DPF termal hasar riski vardır.", "MEDIUM"),
            "1": ("Data Valid But Below Normal Range - Most Severe Level", "AFT1 DOC Çıkış Gaz Sıcaklığı - Veri geçerli fakat normal çalışma aralığının altında (Kritik Düşük)", "Çıkış sıcaklığı giriş (SPN 4765) ile aynıysa DOC dönüşüm etkinliği kaybolmuş olabilir; katalizör kurum/kirlenme durumunu değerlendirin.", "LOW"),
            "3": ("Voltage Above Normal, Or Shorted To High Source", "AFT1 DOC Çıkış Gaz Sıcaklığı - Voltaj normalin üzerinde veya yüksek kaynağa kısa devre", "Sensör soketini ayırın; sinyal hattı +5V kısa devresini kontrol edin.", "MEDIUM"),
            "4": ("Voltage Below Normal, Or Shorted To Low Source", "AFT1 DOC Çıkış Gaz Sıcaklığı - Voltaj normalin altında veya şasiye kısa devre", "Termokupl açık devre testini yapın; hattı şasiye karşı ölçün.", "MEDIUM"),
        },
    },
}


def _check() -> int:
    """L-17 (P3-13): --check mode — validate the script's invariants without
    writing anything (usable from CI). Returns the number of violations."""
    db = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    fmi_defs = db["fmi_definitions"]
    violations = 0
    for key, entry in NEW_SPNS.items():
        if key in db["spns"]:
            continue
        if str(entry["spn"]) != key.split("_")[1]:
            print(f"VIOLATION: {key} key/syn mismatch (spn={entry['spn']})")
            violations += 1
        for fmi in entry["fault_matrix"]:
            if fmi not in fmi_defs:
                print(f"VIOLATION: {key} references unknown FMI {fmi}")
                violations += 1
    return violations


def _atomic_write(path, data: bytes) -> None:
    """L-17 (P3-13): tmp + os.replace so a crash mid-write can never
    truncate the knowledge base the copilot loads at startup."""
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}-{time.monotonic_ns()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def apply() -> None:
    db = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    fmi_defs = db["fmi_definitions"]
    spns: dict = db["spns"]

    added = 0
    for key, entry in NEW_SPNS.items():
        if key in spns:
            print(f"skip (exists): {key}")
            continue
        # L-17: real exceptions instead of assert — `python -O` strips
        # asserts and let a corrupt expansion reach the DB.
        if str(entry["spn"]) != key.split("_")[1]:
            raise ValueError(f"{key}: key/syn mismatch (spn={entry['spn']})")
        for fmi in entry["fault_matrix"]:
            if fmi not in fmi_defs:
                raise ValueError(f"{key}: FMI {fmi} not in fmi_definitions")
        # Expand FMI tuples into the canonical object shape.
        entry["fault_matrix"] = {
            fmi: {
                "fmi_name": fmi_name,
                "fault_title": fault_title,
                "diagnostic_action": action,
                "severity": severity,
            }
            for fmi, (fmi_name, fault_title, action, severity) in entry["fault_matrix"].items()
        }
        spns[key] = entry
        added += 1

    db["metadata"]["version"] = "1.1.0"
    db["metadata"]["total_spns"] = len(spns)
    db["metadata"]["last_updated"] = "2026-09-06"
    db["metadata"]["sources"] = [
        "canboat/canboat database/j1939/pgns YAML (Apache-2.0) - SPN/PGN association, field names, scaling",
        "NHTSA TSB MC-10141869 (SS 1033423) - official SPN name cross-check",
        "ISO 11783 / J1939-71 public scaling tables - temperature/pressure bit encodings",
    ]

    # L-17: LF line endings preserved (the old \n→\r\n rewrite churned the
    # whole file in every diff) and written atomically.
    text = json.dumps(db, indent=2, ensure_ascii=False)
    _atomic_write(JSON_PATH, text.encode("utf-8"))
    print(f"JSON: +{added} SPNs -> total {len(spns)}")

    # Regenerate the CSV twin in the existing column layout.
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        header = next(csv.reader(f))
    rows = []
    for key in sorted(spns, key=lambda k: int(k.split("_")[1])):
        e = spns[key]
        for fmi, fm in sorted(e["fault_matrix"].items(), key=lambda kv: int(kv[0])):
            rows.append([
                e["spn"], int(fmi), fm["fault_title"], e["subsystem"],
                f"{e['associated_pgn']} ({e['pgn_acronym']})", fm["fmi_name"],
                fm["severity"], fm["diagnostic_action"],
            ])
    import io
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    _atomic_write(CSV_PATH, buf.getvalue().encode("utf-8-sig"))
    print(f"CSV: {len(rows)} fault rows")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Expand the J1939 SPN/FMI database")
    parser.add_argument("--check", action="store_true", help="Validate invariants without writing (CI mode)")
    args = parser.parse_args()
    if args.check:
        bad = _check()
        print(f"check: {bad} violations")
        raise SystemExit(1 if bad else 0)
    apply()
