# -*- coding: utf-8 -*-
"""Generate data/diagnostics/operating_scenarios.json (state-aware judgement for the copilot).

The same reading means different things in different operating states: 8 kPa
DPF differential pressure is a clogged filter at idle but normal at full load;
12.3 V is a healthy resting battery but a dead alternator with the engine
running. This file holds:

* battery_voltage: BatteryVoltage bands per system voltage (12 V / 24 V) and
  engine state (running / off / cranking). Values approved by the operator
  (AGENTS.md 2.3: new threshold values only with operator approval).
* scenarios: rules "in state S, signal X above/below V means Y". Each one adds
  a candidate (or only a note) with its reason and first check; ``codes`` are
  the codes whose causes the scenario confirms (their graph causes gain it as
  evidence when those codes are active). The limits
  come from the knowledge base records named in ``source`` or from the
  operator-approved battery bands.
"""
import json
import os

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))  # run from repo root

P = "data/diagnostics/operating_scenarios.json"

# [low inclusive, high exclusive, status] for a 12 V system; 24 V doubles every limit
BATTERY_12V = {
    "running": [[None, 12.0, "critical_low"], [12.0, 13.2, "low"], [13.2, 13.8, "below_nominal"],
                [13.8, 14.4, "normal"], [14.4, 15.0, "above_nominal"], [15.0, 15.5, "high"], [15.5, None, "critical_high"]],
    "off": [[None, 10.5, "critical_low"], [10.5, 12.0, "low"], [12.0, 12.4, "below_nominal"],
            [12.4, 12.9, "normal"], [12.9, None, "above_nominal"]],
    "cranking": [[None, 9.6, "low"], [9.6, None, "normal"]],
}

SCENARIOS = [
 {"id": "alternator-not-charging", "codes": ["P0562"],
  "when": {"engine": ["idle", "running", "load"], "signal": "BatteryVoltage", "lt": 13.2, "per_12v": True},
  "title_tr": "Alternatör şarj etmiyor (kayış, regülatör, şarj kablosu)",
  "title_en": "Alternator not charging (belt, regulator, charge cable)",
  "rationale_tr": "motor çalışırken akü gerilimi şarj seviyesinin altında; çalışan motorda alternatör aküyü 13.8 - 14.4 V'ta tutmalı",
  "rationale_en": "battery voltage is below charging level with the engine running; a running engine's alternator holds 13.8 - 14.4 V",
  "step_tr": "Alternatör kayışını, B+ kablosunu ve şasesini kontrol edin; alternatör çıkışında gerilimi ölçün (motor çalışırken).",
  "step_en": "Check the alternator belt, the B+ cable and its ground; measure voltage at the alternator output with the engine running.",
  "source": "operating_scenarios#battery_voltage"},
 {"id": "regulator-overcharging", "codes": ["P0563"],
  "when": {"engine": ["idle", "running", "load"], "signal": "BatteryVoltage", "gt": 15.0, "per_12v": True},
  "title_tr": "Voltaj regülatörü aşırı şarj ediyor",
  "title_en": "Voltage regulator overcharging",
  "rationale_tr": "motor çalışırken gerilim 15.0 V üstünde; regülatör sınırlamıyor, akü ve elektronik zarar görebilir",
  "rationale_en": "voltage is above 15.0 V with the engine running; the regulator is not limiting, battery and electronics can be damaged",
  "step_tr": "Alternatör regülatörünü kontrol edin; akü kutup başında ve alternatör çıkışında gerilimi karşılaştırın.",
  "step_en": "Check the alternator regulator; compare voltage at the battery and at the alternator output.",
  "source": "operating_scenarios#battery_voltage"},
 {"id": "weak-battery-cranking",
  "when": {"engine": ["cranking"], "signal": "BatteryVoltage", "lt": 9.6, "per_12v": True},
  "title_tr": "Akü zayıf veya marş hattında gerilim düşümü",
  "title_en": "Weak battery or voltage drop on the starter circuit",
  "rationale_tr": "marş sırasında gerilim 9.6 V altına düşüyor; sağlam akü marşta bu sınırın üstünde kalır",
  "rationale_en": "voltage drops below 9.6 V while cranking; a healthy battery stays above it",
  "step_tr": "Akü yük testi yapın; marş sırasında akü kutbu ile marş motoru arasındaki gerilim düşümünü ölçün.",
  "step_en": "Load test the battery; measure the voltage drop between the battery post and the starter while cranking.",
  "source": "operating_scenarios#battery_voltage"},
 {"id": "battery-flat-at-rest",
  "when": {"engine": ["off"], "signal": "BatteryVoltage", "lt": 12.0, "per_12v": True},
  "title_tr": "Akü boşalmış (kaçak akım veya yetersiz şarj)",
  "title_en": "Battery discharged (parasitic drain or undercharging)",
  "rationale_tr": "motor dururken gerilim 12.0 V altında; dinlenmiş dolu bir akü 12.4 V üstünde olur",
  "rationale_en": "resting voltage is below 12.0 V; a rested full battery reads above 12.4 V",
  "step_tr": "Aküyü şarj edip yük testi yapın; kontak kapalı ve araç kilitliyken uyku akımını ölçün.",
  "step_en": "Charge and load test the battery; measure the sleep current with the ignition off and the car locked.",
  "source": "operating_scenarios#battery_voltage"},
 {"id": "surface-charge-reading",
  "when": {"engine": ["off"], "signal": "BatteryVoltage", "gt": 12.9, "per_12v": True},
  "note_only": True,
  "rationale_tr": "motor dururken gerilim 12.9 V üstünde: yüzey şarjı (motor yeni durdu veya akü yeni şarj edildi); bu okuma akünün durumunu göstermez, 1 saat bekleyip yeniden ölçün",
  "rationale_en": "resting voltage above 12.9 V: surface charge (engine just stopped or battery just charged); this reading does not show the battery's state, re-measure after 1 hour",
  "source": "operating_scenarios#battery_voltage"},
 {"id": "dpf-loaded-at-idle", "codes": ["P2002", "P2463"],
  "when": {"engine": ["idle"], "signal": "DPFDiffPressure", "gt": 2.0},
  "title_tr": "DPF dolu: rölantide fark basıncı temiz filtre sınırının üstünde",
  "title_en": "DPF loaded: idle differential pressure above the clean-filter limit",
  "rationale_tr": "temiz DPF rölantide 0.5 - 2.0 kPa gösterir; aynı değer tam yükte normal olabilir, rölantide tıkanma demektir",
  "rationale_en": "a clean DPF reads 0.5 - 2.0 kPa at idle; the same value can be normal at full load but means loading at idle",
  "step_tr": "Uzun otoyol sürüşü veya servis rejenerasyonu yapın; sonra rölantide fark basıncını yeniden okuyun.",
  "step_en": "Do a long motorway drive or a service regeneration; then re-read the idle differential pressure.",
  "source": "diagnostic_copilot.py SPN3251 measurement"},
 {"id": "underboost-under-load", "codes": ["P0299"],
  "when": {"engine": ["load"], "signal": "BoostPressure", "lt": 2.2},
  "title_tr": "Yükte turbo basıncı düşük (basınç kaçağı, wastegate/VNT, turbo)",
  "title_en": "Low boost under load (boost leak, wastegate/VNT, turbo)",
  "rationale_tr": "tam yükte nominal turbo basıncı 2.2 - 3.2 bar; yük altında bunun altı gerçek basınç eksikliğidir (rölantide düşük basınç normaldir)",
  "rationale_en": "nominal full-load boost is 2.2 - 3.2 bar; below it under load is a real boost shortfall (low boost at idle is normal)",
  "step_tr": "Şarj havası hortumlarını ve intercooler'ı kaçak için kontrol edin; wastegate/VNT hareketini test edin.",
  "step_en": "Check the charge air hoses and intercooler for leaks; test wastegate/VNT movement.",
  "source": "diagnostic_copilot.py SPN102 measurement"},
 {"id": "thermostat-open-warm-engine", "codes": ["P0128"],
  "when": {"thermal": ["warm"], "engine": ["running", "load"], "signal": "CoolantTemp", "lt": 70.0},
  "title_tr": "Termostat açık kalmış: ısınmış ve çalışan motorda su sıcaklığı düşük",
  "title_en": "Thermostat stuck open: coolant temperature low on a warmed-up running engine",
  "rationale_tr": "ısınmış motor 82 - 95 °C aralığında çalışır; sürüşte 70 °C altında kalması termostatın açık kaldığını gösterir",
  "rationale_en": "a warm engine runs at 82 - 95 °C; staying under 70 °C while driving shows the thermostat is stuck open",
  "step_tr": "Termostatı değiştirin; değişimden önce ECT okumasını bir kızılötesi termometreyle doğrulayın.",
  "step_en": "Replace the thermostat; before that, confirm the ECT reading with an infrared thermometer.",
  "source": "diagnostic_copilot.py SPN 110 measurement"},
 {"id": "overheat-at-idle", "codes": ["P0217"],
  "when": {"engine": ["idle"], "signal": "CoolantTemp", "gt": 103.0},
  "title_tr": "Rölantide hararet: fan veya radyatör hava akışı yetersiz",
  "title_en": "Overheating at idle: radiator fan or airflow insufficient",
  "rationale_tr": "rölantide araç rüzgârı yoktur, soğutma fana kalır; rölantide yükselip yolda düşen hararet fan/hava akışı sorunudur",
  "rationale_en": "at idle there is no ram air and cooling depends on the fan; overheating at idle that drops on the road is a fan/airflow problem",
  "step_tr": "Radyatör fanının devreye girdiğini, fan kumandasını ve radyatör peteklerinin temizliğini kontrol edin.",
  "step_en": "Check that the radiator fan engages, its control, and that the radiator fins are clean.",
  "source": "diagnostic_copilot.py SPN 110 measurement"},
 {"id": "overheat-under-load", "codes": ["P0217"],
  "when": {"engine": ["load"], "signal": "CoolantTemp", "gt": 103.0},
  "title_tr": "Yükte hararet: soğutma kapasitesi yetersiz (radyatör tıkanıklığı, su pompası, termostat)",
  "title_en": "Overheating under load: cooling capacity insufficient (radiator blockage, water pump, thermostat)",
  "rationale_tr": "yükte ısı üretimi en yüksektir; yükte yükselen hararet radyatör, su pompası veya termostat kapasitesini işaret eder",
  "rationale_en": "heat output peaks under load; overheating under load points at radiator, water pump or thermostat capacity",
  "step_tr": "Radyatör iç tıkanıklığını (giriş/çıkış sıcaklık farkı), su pompasını ve termostat açılmasını kontrol edin.",
  "step_en": "Check radiator internal blockage (inlet/outlet temperature difference), the water pump and thermostat opening.",
  "source": "diagnostic_copilot.py SPN 110 measurement"},
 {"id": "lean-confirmed-by-trims", "codes": ["P0171", "P0174"],
  "when": {"engine": ["idle", "running", "load"], "sum": ["ShortTermFuelTrimB1", "LongTermFuelTrimB1"], "gt": 20.0},
  "title_tr": "Fakir karışım doğrulandı: yakıt düzeltmeleri toplamı +%20 üstünde (hava kaçağı, MAF, düşük yakıt basıncı)",
  "title_en": "Lean mixture confirmed: fuel trims add up to over +20 % (air leak, MAF, low fuel pressure)",
  "rationale_tr": "STFT + LTFT toplamı +%20'yi aşıyorsa ECU fazla yakıt ekleyerek fakir karışımı telafi ediyordur",
  "rationale_en": "when STFT + LTFT exceed +20 % the ECU is adding fuel to make up for a lean mixture",
  "step_tr": "Emme tarafında duman testi yapın, MAF okumasını ve yakıt basıncını kontrol edin.",
  "step_en": "Smoke test the intake, check the MAF reading and the fuel pressure.",
  "source": "dtc_database P0171 steps[1] (STFT + LTFT > +20 %)"},
 {"id": "rich-confirmed-by-trims", "codes": ["P0172", "P0175"],
  "when": {"engine": ["idle", "running", "load"], "sum": ["ShortTermFuelTrimB1", "LongTermFuelTrimB1"], "lt": -20.0},
  "title_tr": "Zengin karışım doğrulandı: yakıt düzeltmeleri toplamı -%20 altında (damlatan enjektör, purge valfi, yüksek yakıt basıncı)",
  "title_en": "Rich mixture confirmed: fuel trims add up to below -20 % (leaking injector, purge valve, high fuel pressure)",
  "rationale_tr": "STFT + LTFT toplamı -%20'nin altındaysa ECU yakıtı keserek zengin karışımı telafi ediyordur",
  "rationale_en": "when STFT + LTFT fall below -20 % the ECU is cutting fuel to make up for a rich mixture",
  "step_tr": "Enjektör kaçağını, EVAP purge valfini ve yakıt basınç regülatörünü kontrol edin.",
  "step_en": "Check for injector leakage, the EVAP purge valve and the fuel pressure regulator.",
  "source": "canonical_symptoms rich-condition-bank1 (trims pulling towards -20 %)"},
 {"id": "oil-pressure-engine-off",
  "when": {"engine": ["off"], "signal": "EngineOilPressure", "gt": 0.5},
  "title_tr": "Yağ basınç sensörü/okuması güvenilmez: motor dururken basınç gösteriyor",
  "title_en": "Oil pressure sensor/reading unreliable: shows pressure with the engine stopped",
  "rationale_tr": "duran motorda yağ pompası dönmez, basınç sıfıra yakın olmalı",
  "rationale_en": "with the engine stopped the oil pump does not turn and pressure should be near zero",
  "step_tr": "Yağ basınç sensörünü ve kablosunu kontrol edin; basıncı mekanik saatle doğrulayın.",
  "step_en": "Check the oil pressure sensor and wiring; verify pressure with a mechanical gauge.",
  "source": "diagnostic_copilot.py SPN100 measurement"},
]


def prov(rid, approved=False):
    return {
        "provenance_id": f"prv-scenario-{rid}",
        "target": {"record_id": rid, "field": "scenario"},
        "activity": "operator_approved_threshold" if approved else "curate_operating_scenario",
        "activity_version": "copilot-scenarios-1",
        "agent": {"type": "curator", "id": "ucanlab-copilot-curation", "role": "author"},
        "source": {"title": ("Operator approval 2026-10-03 (battery voltage bands, 12 V / 24 V)" if approved else
                             "Knowledge-base measurement texts and operator-approved battery bands"),
                   "path": "data/diagnostics/operating_scenarios.json", "type": "internal_kb"},
        "transform": {"rule_id": "state-aware-judgement"},
        "confidence": "single_source",
    }


out = {
    "schema_version": 1,
    "title": "Copilot operating-state scenarios (state-aware judgement of readings)",
    "_rule": ("Engine state (off / cranking / idle / running / load), thermal state (cold / warm) and system voltage "
              "(12 V / 24 V) change what a reading means. Battery bands are operator-approved; scenario limits "
              "come from knowledge-base measurement texts. No reading is ever invented."),
    "battery_voltage": {
        "bands_12v": BATTERY_12V,
        "bands_24v": {k: [[None if lo is None else round(lo * 2, 1), None if hi is None else round(hi * 2, 1), st]
                          for lo, hi, st in v] for k, v in BATTERY_12V.items()},
        "provenance": [prov("battery_voltage", True)]},
    "scenarios": [dict(s, provenance=[prov(s["id"])]) for s in SCENARIOS],
}
with open(P, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
    fh.write("\n")
print(len(SCENARIOS))
