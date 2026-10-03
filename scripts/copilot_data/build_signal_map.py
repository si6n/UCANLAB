# -*- coding: utf-8 -*-
"""Generate data/diagnostics/signal_measurement_map.json from the shipped DBs.

Every SPN/PGN/PID field is READ from j1939_spn_fmi_database.json /
extended_pid_database.json (fail if absent) — nothing typed by hand except the
everyday phrases used to recognise the signal in text.
"""
import json
import os

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))  # run from repo root

D = "data/diagnostics/"
spns = json.load(open(D + "j1939_spn_fmi_database.json", encoding="utf-8"))["spns"]
pids = json.load(open(D + "extended_pid_database.json", encoding="utf-8"))["pids"]
thr = json.load(open(D + "telemetry_thresholds.json", encoding="utf-8"))["signals"]

def pid_row(service, pid):
    rows = [p for p in pids if str(p.get("service", "")).zfill(2) == service and
            (f"{p['pid']:02X}" if isinstance(p.get("pid"), int) else str(p.get("pid", "")).upper().zfill(2)) == pid]
    std = [r for r in rows if r.get("confidence") == "standard"] or rows
    assert std, (service, pid)
    return std[0]

S = [
 # canonical, unit, aliases, tr phrases, en phrases, threshold_key, spn, (service,pid)
 ("CoolantTemp", "°C", ["EngineCoolantTemp", "coolant", "ECT"], ["su sicakligi", "sogutma suyu sicakligi", "motor suyu sicakligi", "motor sicakligi", "antifriz sicakligi"], ["coolant temperature", "coolant temp", "engine temperature", "engine temp"], "EngineCoolantTemp", 110, ("01", "05")),
 ("EngineSpeed", "rpm", ["RPM"], ["motor devri", "devir"], ["engine speed", "rpm"], "EngineSpeed", 190, ("01", "0C")),
 ("ShortTermFuelTrimB1", "%", ["STFT", "STFT_B1"], ["kisa donem yakit duzeltmesi", "stft"], ["short term fuel trim", "stft"], None, None, ("01", "06")),
 ("LongTermFuelTrimB1", "%", ["LTFT", "LTFT_B1"], ["uzun donem yakit duzeltmesi", "ltft"], ["long term fuel trim", "ltft"], None, None, ("01", "07")),
 ("EngineOilPressure", "bar", ["OilPressure", "oil_pressure"], ["yag basinci"], ["oil pressure"], "EngineOilPressure", 100, None),
 ("BoostPressure", "bar", ["TurboBoost", "Boost"], ["turbo basinci", "takviye basinci", "boost basinci"], ["boost pressure", "turbo pressure", "boost"], "TurboBoost", 102, ("01", "0B")),
 ("BatteryVoltage", "V", ["SystemVoltage", "ModuleVoltage"], ["aku voltaji", "aku gerilimi", "sarj voltaji", "sistem voltaji", "aku"], ["battery voltage", "system voltage", "charging voltage"], None, 168, ("01", "42")),
 ("VehicleSpeed", "km/h", ["VSS"], ["arac hizi"], ["vehicle speed"], "VehicleSpeed", 84, ("01", "0D")),
 ("EngineLoad", "%", ["EngineLoadPct"], ["motor yuku"], ["engine load"], "EngineLoad", 92, ("01", "04")),
 ("EngineTorque", "%", ["EngineTorquePct"], ["motor torku"], ["engine torque"], "EngineTorque", 513, None),
 ("FuelPressure", "kPa", ["FuelRailPressure", "RailPressure"], ["rail basinci", "yakit basinci", "common rail basinci"], ["rail pressure", "fuel pressure", "fuel rail pressure"], None, 157, ("01", "23")),
 ("DPFDiffPressure", "kPa", ["DPFPressure"], ["dpf fark basinci", "dpf basinci"], ["dpf differential pressure", "dpf pressure"], "DPFDiffPressure", 3251, None),
 ("DPFSootLoad", "%", ["SootLoad"], ["kurum orani", "dpf doluluk", "is yuku"], ["soot load", "dpf soot"], None, 3719, None),
 ("EngineOilTemp", "°C", ["OilTemp"], ["yag sicakligi"], ["oil temperature", "oil temp"], None, 175, ("01", "5C")),
 ("IntakeAirTemp", "°C", ["IAT"], ["emme havasi sicakligi"], ["intake air temperature", "intake temp"], None, 105, ("01", "0F")),
 ("EngineAirFlow", "g/s", ["MAF", "MassAirFlow"], ["hava debisi", "maf degeri"], ["mass air flow", "maf"], None, 132, ("01", "10")),
 ("DEFTankLevel", "%", ["AdBlueLevel"], ["adblue seviyesi", "def seviyesi"], ["def level", "adblue level"], None, 1761, None),
 ("FuelLevel", "%", [], ["yakit seviyesi"], ["fuel level"], None, 96, ("01", "2F")),
 ("CatalystTemperature", "°C", [], ["katalizor sicakligi"], ["catalyst temperature"], None, None, ("01", "3C")),
 ("ExhaustTemp", "°C", ["EGT"], ["egzoz sicakligi"], ["exhaust temperature", "egt"], None, 173, None),
 ("AirPressureCircuit1", "kPa", ["BrakeCircuit1Pressure"], ["fren hava basinci devre 1"], ["brake air pressure circuit 1"], "AirPressureCircuit1", 1087, None),
 ("AirPressureCircuit2", "kPa", ["BrakeCircuit2Pressure"], ["fren hava basinci devre 2"], ["brake air pressure circuit 2"], "AirPressureCircuit2", 1088, None),
 ("ThrottlePosition", "%", ["TPS"], ["gaz kelebegi konumu"], ["throttle position"], None, 51, ("01", "11")),
 ("EGRPosition", "%", [], ["egr konumu", "egr valf konumu"], ["egr position"], None, 27, None),
 ("IsolationResistance", "kΩ", ["HVIsolationResistance"], ["izolasyon direnci"], ["isolation resistance", "insulation resistance"], None, None, None),
 ("HVPackVoltage", "V", ["PackVoltage", "HVBusVoltage"], ["batarya paket voltaji", "paket voltaji", "yuksek voltaj bara gerilimi"], ["pack voltage", "hv bus voltage", "battery pack voltage"], None, None, None),
]

def prov(canon, spn, pid):
    srcs = []
    if spn is not None:
        srcs.append(f"j1939_spn_fmi_database.json['spns']['SPN_{spn}']")
    if pid is not None:
        srcs.append(f"extended_pid_database.json service {pid[0]} pid {pid[1]}")
    return [{
        "provenance_id": f"prv-sigmap-{canon.lower()}",
        "target": {"record_id": canon, "field": "j1939/obd"},
        "activity": "derive_from_internal_db",
        "activity_version": "copilot-upgrade-1",
        "agent": {"type": "automated", "id": "build_signal_measurement_map", "role": "generator"},
        "source": {"title": "; ".join(srcs) or "curator note (no SAE J1939/J1979 standard parameter in the shipped DBs)",
                   "path": "data/diagnostics/" + ("j1939_spn_fmi_database.json" if spn is not None else "extended_pid_database.json" if pid else "signal_measurement_map.json"),
                   "type": "internal_kb"},
        "transform": {"rule_id": "copy-spn-pgn-pid-fields"},
        "confidence": "single_source" if (spn is not None or pid) else "unverified",
    }]

def promoted_entries():
    """Aliases scripts/promote_intake.py added from retired intake records.

    The intake records are deleted once promoted, so this generator cannot
    re-derive them; it carries them (and their provenance) forward from the
    current file. Without this, regenerating silently dropped every promoted
    alias.
    """
    try:
        current = json.load(open(D + "signal_measurement_map.json", encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    kept = {}
    for sig in current.get("signals", []):
        for entry in sig.get("provenance") or []:
            if entry.get("activity") == "promote_from_intake":
                kept.setdefault(sig["canonical"], []).append(entry)
    return kept

PROMOTED = promoted_entries()

LABEL_TR = {"ShortTermFuelTrimB1": "Kısa dönem yakıt düzeltmesi (Sıra 1)",
            "LongTermFuelTrimB1": "Uzun dönem yakıt düzeltmesi (Sıra 1)",
            "CatalystTemperature": "Katalizör sıcaklığı", "IsolationResistance": "HV izolasyon direnci",
            "HVPackVoltage": "HV batarya paket voltajı"}
out = []
for canon, unit, aliases, tr, en, tkey, spn, pid in S:
    if tkey is not None:
        assert tkey in thr, tkey
    rec = {"canonical": canon, "unit": unit, "aliases": aliases, "phrases_tr": tr, "phrases_en": en}
    if tkey:
        rec["threshold_key"] = tkey
    rec["label_tr"] = spns[f"SPN_{spn}"]["title_tr"] if spn is not None else LABEL_TR[canon]
    rec["label_en"] = spns[f"SPN_{spn}"]["name"] if spn is not None else en[0].capitalize()
    if spn is not None:
        r = spns[f"SPN_{spn}"]
        rec["j1939"] = {"spn": spn, "name": r["name"], "title_tr": r.get("title_tr"), "pgn": r.get("associated_pgn"),
                        "pgn_acronym": r.get("pgn_acronym"), "unit": r.get("unit")}
    if pid is not None:
        row = pid_row(*pid)
        rec["obd"] = {"service": pid[0], "pid": pid[1], "name": row["name"], "unit": row.get("unit")}
    if canon in ("IsolationResistance", "HVPackVoltage"):
        rec["hv_only"] = True
        rec["how_to_tr"] = ("Yalnız yüksek gerilim eğitimi almış yetkili teknisyen, araç üreticisinin servis aracıyla "
                            "BMS canlı verisinden okur. Turuncu kablolara dokunmayın.")
        rec["how_to_en"] = ("Only an HV-trained, authorised technician reads this from BMS live data with the OEM "
                            "service tool. Do not touch orange cables.")
    rec["provenance"] = prov(canon, spn, pid)
    for entry in PROMOTED.get(canon, []):
        names = [v for v in entry.get("verbatim", "").split("; ") if v]
        rec["aliases"] += [v for v in names if v not in rec["aliases"]]
        rec["provenance"].append(entry)
    out.append(rec)

doc = {
    "schema_version": 1,
    "title": "Signal measurement map (canonical signal -> J1939 SPN/PGN, OBD-II PID, unit)",
    "_rule": ("SPN name/PGN/acronym/unit and OBD PID name/unit are COPIED from the shipped databases by "
              "the generator (see provenance). Only recognition phrases are curated. Used by the copilot to "
              "say which measurement is missing and how to take it. scripts/validate_copilot_data.py re-checks "
              "every SPN/PID against the databases."),
    "signals": out,
}
with open(D + "signal_measurement_map.json", "w", encoding="utf-8", newline="\n") as fh:
    json.dump(doc, fh, ensure_ascii=False, indent=1)
    fh.write("\n")
print(len(out))
