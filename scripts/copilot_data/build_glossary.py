# -*- coding: utf-8 -*-
"""Generate data/diagnostics/copilot_glossary.json — one-sentence TR/EN jargon explanations.

README design principle 2 ("No jargon: terms such as DTC, SPN/FMI or PGN come
with a one-sentence explanation"). Each entry is explanatory text written by
the curator; it states what a term MEANS, never a value, limit or cause.
"""
import json
import os

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))  # run from repo root

T = {
 "DTC": (["dtc", "ariza kodu", "fault code", "trouble code"], "Arıza kodu: kontrol ünitesinin bir sorunu tespit ettiğinde hafızasına yazdığı kısa kod (ör. P0101).", "Diagnostic trouble code: a short code a control unit stores when it detects a problem (e.g. P0101).", "SAE J2012 / ISO 15031-6"),
 "SPN": (["spn"], "SPN (J1939 parametre numarası): ağır vasıtalarda hangi sensör/değerin arızalı olduğunu söyleyen numara.", "SPN (J1939 suspect parameter number): the number that says which sensor or value is at fault on heavy-duty vehicles.", "SAE J1939-71 / -73"),
 "FMI": (["fmi"], "FMI (arıza tipi): SPN'deki sorunun türü — ör. değer çok yüksek, kısa devre, sinyal yok.", "FMI (failure mode identifier): the kind of fault on the SPN — e.g. value too high, short circuit, no signal.", "SAE J1939-73"),
 "PGN": (["pgn"], "PGN: J1939 hattında bir grup ölçümü taşıyan mesajın numarası.", "PGN: the number of the J1939 message that carries a group of measurements.", "SAE J1939-21"),
 "DM1": (["dm1"], "DM1: J1939 araçlarda aktif arıza kodlarını ve uyarı lambalarının durumunu yayınlayan mesaj.", "DM1: the J1939 message that broadcasts active fault codes and warning-lamp status.", "SAE J1939-73"),
 "OBD": (["obd", "obd ii", "obd2"], "OBD-II: binek araçlarda arıza kodlarını ve canlı verileri okumaya yarayan standart teşhis bağlantısı.", "OBD-II: the standard diagnostic connection on passenger cars for reading fault codes and live data.", "SAE J1979 / ISO 15031"),
 "PID": (["pid"], "PID: OBD-II üzerinden okunan tek bir canlı verinin numarası (ör. 05 = soğutma suyu sıcaklığı).", "PID: the number of one live value read over OBD-II (e.g. 05 = coolant temperature).", "SAE J1979"),
 "ECU": (["ecu", "ecm", "kontrol unitesi", "beyin"], "ECU: motoru veya bir sistemi yöneten elektronik kontrol ünitesi ('beyin').", "ECU: the electronic control unit that manages the engine or a system (the 'brain').", "ISO 14229"),
 "MIL": (["mil", "motor ariza lambasi", "check engine"], "MIL: gösterge panelindeki motor arıza (check engine) lambası.", "MIL: the check-engine (malfunction indicator) lamp on the dashboard.", "SAE J1979"),
 "DPF": (["dpf", "partikul filtresi"], "DPF: dizel egzozundaki isi (kurumu) tutan partikül filtresi; dolunca yakılarak temizlenir.", "DPF: the diesel particulate filter that traps soot; it is cleaned by burning the soot off (regeneration).", "SAE J1939-71"),
 "REGEN": (["rejenerasyon", "regeneration", "regen"], "Rejenerasyon: DPF'de biriken kurumun yüksek egzoz sıcaklığıyla yakılarak temizlenmesi.", "Regeneration: burning the soot collected in the DPF off with high exhaust temperature.", "SAE J1939-71"),
 "EGR": (["egr"], "EGR: egzoz gazının bir kısmını emişe geri gönderen ve NOx'u azaltan sistem.", "EGR: the system that sends part of the exhaust gas back to the intake to reduce NOx.", "SAE J2012"),
 "SCR": (["scr", "adblue", "def", "ure"], "SCR/AdBlue: egzoza üre (AdBlue/DEF) püskürterek NOx gazını azaltan sistem.", "SCR/AdBlue: the system that injects urea (AdBlue/DEF) into the exhaust to reduce NOx.", "SAE J1939-71"),
 "NOX": (["nox"], "NOx: motorun ürettiği zararlı azot oksit gazları; sensörlerle ölçülür.", "NOx: harmful nitrogen-oxide gases produced by the engine, measured by sensors.", "SAE J1939-71"),
 "DERATE": (["derate", "guc sinirlama", "tork dusurme"], "Derate (güç sınırlama): motorun kendini korumak veya yasal zorunlulukla gücünü kısması.", "Derate: the engine reducing its own power to protect itself or to comply with regulations.", "SAE J1939-71"),
 "HV": (["yuksek voltaj", "yuksek gerilim", "high voltage", "hv"], "Yüksek voltaj (HV): elektrikli/hibrit araçlarda öldürücü olabilen yüksek gerilimli sistem; turuncu kablolarla taşınır.", "High voltage (HV): the potentially lethal high-voltage system in electric/hybrid vehicles, carried by orange cables.", "UN R100 / ISO 6469"),
 "BMS": (["bms"], "BMS: yüksek voltaj bataryasının hücrelerini, sıcaklığını ve güvenliğini izleyen kontrol ünitesi.", "BMS: the control unit that monitors the HV battery's cells, temperature and safety.", "ISO 6469"),
 "HVIL": (["hvil"], "HVIL: yüksek voltaj soketleri açıldığında sistemi kapatan güvenlik devresi.", "HVIL: the safety loop that shuts the high-voltage system down when an HV connector is opened.", "ISO 6469-3"),
 "ISOLATION": (["izolasyon", "isolation", "insulation"], "İzolasyon direnci: yüksek voltaj devresinin araç gövdesinden ne kadar iyi yalıtıldığını gösteren değer.", "Isolation resistance: how well the high-voltage circuit is insulated from the vehicle body.", "UN R100"),
 "MAF": (["maf", "hava debisi"], "MAF: motora giren hava miktarını ölçen sensör.", "MAF: the sensor that measures how much air enters the engine.", "SAE J1979"),
 "MAP": (["map sensoru", "manifold pressure"], "MAP: emme manifoldundaki basıncı ölçen sensör.", "MAP: the sensor that measures intake-manifold pressure.", "SAE J1979"),
 "BOOST": (["boost", "turbo basinci"], "Boost (turbo basıncı): turbonun motora bastığı hava basıncı.", "Boost: the air pressure the turbocharger pushes into the engine.", "SAE J1939-71"),
 "RAIL": (["common rail", "rail basinci", "rail pressure"], "Common rail: enjektörlere yüksek basınçlı yakıt dağıtan ortak boru; basıncı sensörle izlenir.", "Common rail: the shared high-pressure pipe feeding the injectors; its pressure is monitored by a sensor.", "SAE J1939-71"),
 "CKP": (["ckp", "krank sensoru", "krank mili konum"], "CKP: krank milinin konumunu/devrini ölçen sensör; arızalanırsa motor çalışmayabilir.", "CKP: the sensor that reads crankshaft position/speed; if it fails the engine may not run.", "SAE J2012"),
 "CMP": (["cmp", "eksantrik sensoru", "kam mili konum"], "CMP: eksantrik (kam) milinin konumunu ölçen sensör.", "CMP: the sensor that reads camshaft position.", "SAE J2012"),
 "TPS": (["tps", "gaz kelebegi"], "TPS: gaz kelebeğinin ne kadar açık olduğunu ölçen sensör.", "TPS: the sensor that measures how far the throttle is open.", "SAE J2012"),
 "MISFIRE": (["misfire", "tekleme"], "Tekleme (misfire): bir silindirde yakıtın düzgün yanmaması; motor sarsılır.", "Misfire: fuel not burning properly in a cylinder; the engine shakes.", "SAE J2012"),
 "CANBUS": (["can bus", "can hatti", "can-bus"], "CAN hattı: araçtaki kontrol ünitelerinin birbiriyle konuştuğu iki telli veri hattı.", "CAN bus: the two-wire data network the vehicle's control units use to talk to each other.", "ISO 11898"),
 "UCODE": (["u kodu", "u-code", "u0"], "U kodu: kontrol üniteleri arasındaki haberleşme kaybını bildiren arıza kodu ailesi.", "U-code: the fault-code family that reports lost communication between control units.", "SAE J2012"),
 "FREEZE": (["freeze frame", "donmus kare"], "Freeze frame: arıza kodu oluştuğu andaki motor verilerinin kaydı.", "Freeze frame: the snapshot of engine data taken when the fault code was set.", "SAE J1979"),
 "THERMOSTAT": (["termostat", "thermostat"], "Termostat: motor ısınınca soğutma suyunu radyatöre açan valf.", "Thermostat: the valve that opens coolant flow to the radiator once the engine is warm.", "SAE J2012"),
 "O2": (["oksijen sensoru", "lambda", "o2 sensor"], "Oksijen (lambda) sensörü: egzozdaki oksijeni ölçerek yakıt karışımını ayarlatan sensör.", "Oxygen (lambda) sensor: measures oxygen in the exhaust so the fuel mixture can be adjusted.", "SAE J1979"),
}
out = {}
for key, (match, tr, en, std) in T.items():
    out[key] = {
        "match": match, "tr": tr, "en": en,
        "provenance": [{
            "provenance_id": f"prv-gloss-{key.lower()}",
            "target": {"record_id": key, "field": "tr/en"},
            "activity": "curate_plain_language_definition",
            "agent": {"type": "curator", "id": "ucanlab-copilot-curation", "role": "author"},
            "source": {"title": f"Term as used in {std}", "path": "docs/COPILOT.md#terim-sozlugu", "type": "internal_kb"},
            "confidence": "single_source",
        }],
    }
doc = {"schema_version": 1, "title": "Copilot plain-language glossary (TR/EN)",
       "_rule": "Definitions only: no values, limits, causes or procedures. README principle 2.", "terms": out}
with open("data/diagnostics/copilot_glossary.json", "w", encoding="utf-8", newline="\n") as fh:
    json.dump(doc, fh, ensure_ascii=False, indent=1)
    fh.write("\n")
print(len(out))
