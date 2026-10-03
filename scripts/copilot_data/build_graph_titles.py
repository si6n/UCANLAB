# -*- coding: utf-8 -*-
"""Generate data/diagnostics/graph_title_i18n.json (TR/EN display titles for root-cause graph nodes).

The root-cause graph mixes Turkish and English node titles, so a Turkish
answer showed "Low coolant due to leak in cooling system" and an English one
showed "Motor aşırı sıcaklık". This file gives a display title in the other
language. It is a translation of the SAME cause, never a new cause:

* CURATED: whole-title translations for the nodes the copilot reaches most
  (the candidate codes of the canonical symptoms), keyed by node id.
* TEMPLATES: the OEM-tagged phrasings ("[Kia] Faulty X", "[Kia] X harness is
  open or shorted", "[Kia] X circuit poor electrical connection") are
  translated by pattern, and only when the component X is in COMPONENTS.

A node without an entry keeps its original title.
"""
import json
import os
import re

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))  # run from repo root

P = "data/diagnostics/graph_title_i18n.json"

# node id -> (tr, en); None keeps the original title in that language
CURATED = {
 # ---- English originals -> Turkish
 "p0299-failed-or-damaged-turbocharger-sticking-binding-etc": ("Arızalı veya hasarlı turbo (sıkışma, takılma vb.)", None),
 "p0299-low-fuel-pressure-condition-isuzu": ("Düşük yakıt basıncı durumu (Isuzu)", None),
 "p0217-inoperative-cooling-fan-s": ("Soğutma fanı (fanları) çalışmıyor", None),
 "p0217-low-coolant-due-to-leak-in-cooling-system": ("Soğutma sistemindeki kaçak nedeniyle soğutma suyu eksik", None),
 "p0217-restriction-debris-in-the-a-c-condenser-or-radiator": ("Klima kondenserinde veya radyatörde tıkanma/kir", None),
 "p0217-restriction-debris-in-the-cooling-system": ("Soğutma sisteminde tıkanma/tortu", None),
 "p0217-thermostat-stuck-closed-or-faulty": ("Termostat kapalı takılı veya arızalı", None),
 "p0117-faulty-or-damaged-connectors": ("Arızalı veya hasarlı konnektörler (ECT devresi)", None),
 "p0117-loose-terminals-at-ect-or-pcm": ("ECT sensöründe veya PCM'de gevşek terminal", None),
 "p0117-short-to-ground-on-ect-signal-circuit": ("ECT sinyal devresinde şasiye kısa devre", None),
 "p0117-wiring-harness-damaged": ("Kablo demeti hasarlı (ECT devresi)", None),
 "p2263-the-boost-pressure-sensor-may-have-failed": ("Turbo basınç sensörü arızalanmış olabilir", None),
 "p0088-defective-fuel-pressure-regulator": ("Arızalı yakıt basınç regülatörü", None),
 "p0088-faulty-fuel-rail-pressure-sensor": ("Arızalı yakıt rayı basınç sensörü", None),
 "p0088-shorted-or-open-wiring-and-or-connectors-in-the-fuel": ("Yakıt rayı basınç sensörü devresinde kısa devre veya kopuk kablo/konnektör", None),
 "p0201-bad-injector-this-is-usually-the-cause-of-this-code": ("Arızalı enjektör (en sık neden; diğer nedenleri dışlamaz)", None),
 "p0201-open-in-the-wiring-to-the-injector": ("Enjektör kablosunda kopukluk", None),
 "p0201-short-in-the-wiring-to-the-injector": ("Enjektör kablosunda kısa devre", None),
 "p0a7d-defective-hv-battery-cell-or-battery-pack": ("Arızalı HV batarya, hücre veya batarya paketi", None),
 "p0a7d-hv-battery-pack-fans-not-working-properly": ("HV batarya paketi fanları düzgün çalışmıyor", None),
 "p0174-fuel-problem-rail-injectors-pump-regulator": ("Yakıt sorunu (ray/enjektörler/pompa/regülatör)", None),
 "p0174-the-maf-mass-air-flow-sensor-is-dirty-or-faulty-if-e": ("MAF (hava akış) sensörü kirli veya arızalı", "The MAF (mass air flow) sensor is dirty or faulty"),
 "p0174-there-could-be-a-vacuum-leak-downstream-of-the-maf-s": ("MAF sensöründen sonra vakum kaçağı olabilir", None),
 "p0175-the-maf-mass-air-flow-sensor-is-dirty-or-faulty-note": ("MAF (hava akış) sensörü kirli veya arızalı", "The MAF (mass air flow) sensor is dirty or faulty"),
 "p0175-there-could-be-a-fuel-pressure-or-delivery-problem": ("Yakıt basıncı veya besleme sorunu olabilir", None),
 "p0175-there-could-be-a-vacuum-leak": ("Vakum kaçağı olabilir", None),
 "p0102-dirty-or-contaminated-mass-air-flow-sensor": ("Kirli veya kirlenmiş hava akış (MAF) sensörü", None),
 "p0102-maf-sensor-electrical-harness-or-wiring-problem-open": ("MAF sensörü kablo demeti veya tesisat sorunu (kopuk, kısa devre, yıpranmış, kötü bağlantı)", None),
 "p0106-ground-problem-due-to-corrosion-causing-intermittent": ("Korozyon kaynaklı şase sorunu, aralıklı sinyal kaybı", None),
 "p0108-engine-vacuum-leak": ("Motorda vakum kaçağı", None),
 "p0108-leak-in-vacuum-supply-line-to-map-sensor": ("MAP sensörüne giden vakum hattında kaçak", None),
 "p0108-short-on-reference-voltage-wire-from-pcm": ("PCM referans voltaj kablosunda kısa devre", None),
 "p0108-short-on-signal-wire-to-pcm": ("PCM'ye giden sinyal kablosunda kısa devre", None),
 "p0097-excessively-high-intake-air-temperatures": ("Emme havası sıcaklığı aşırı yüksek", None),
 "p0112-intake-air-leaks-the-accuracy-of-the-iat-sensor-can": ("Emme kaçağı: ölçülmeyen hava IAT okumasını bozar", "Intake air leak: unmeasured air corrupts the IAT reading"),
 "p0098-iat-harness-and-or-wiring-routed-too-close-to-high-v": ("IAT kablosu yüksek gerilim kablolarına (alternatör, buji kabloları) çok yakın döşenmiş", None),
 "p0098-open-in-iat-ground-circuit-or-signal-circuit": ("IAT şase veya sinyal devresinde kopukluk", None),
 "p0098-short-to-voltage-in-iat-signal-circuit-or-reference": ("IAT sinyal veya referans devresinde gerilime kısa devre", None),
 "p0113-faulty-connection-at-iat-sensor": ("IAT sensöründe hatalı bağlantı", None),
 "p0113-internally-failed-iat-sensor": ("IAT sensörü içten arızalı", None),
 "p0016-tone-ring-on-camshaft-slipped-broken": ("Eksantrik sinyal halkası kaymış/kırık", None),
 "p0016-tone-ring-on-crankshaft-slipped-broken": ("Krank sinyal halkası kaymış/kırık", None),
 "p0011-failed-timing-valve-control-solenoid-stuck-open": ("Zamanlama kontrol solenoidi arızalı (açık takılı)", None),
 "p0011-incorrect-camshaft-timing": ("Eksantrik zamanlaması yanlış", None),
 "p0011-wiring-problems-harness-wiring-in-intake-timing-cont": ("Emme zamanlama kontrol solenoidi tesisatında kablo sorunu", "Wiring problem in the intake timing control solenoid circuit"),
 "p0325-the-knock-sensor-is-faulty-and-needs-to-be-replaced": ("Vuruntu sensörü arızalı, değişmeli", "The knock sensor is faulty and needs to be replaced"),
 "p0325-there-is-a-wiring-short-fault-in-the-knock-sensor-ci": ("Vuruntu sensörü devresinde kablo kısa devresi/arızası", "Wiring short/fault in the knock sensor circuit"),
 "p0326-knock-sensor-circuit-is-open-or-shorted-to-ground": ("Vuruntu sensörü devresi kopuk veya şasiye kısa devre", None),
 "p0326-knock-sensor-circuit-is-shorted-to-voltage": ("Vuruntu sensörü devresi gerilime kısa devre", None),
 "p0326-knock-sensor-connector-is-damaged": ("Vuruntu sensörü konnektörü hasarlı", None),
 "p0326-knock-sensor-has-failed": ("Vuruntu sensörü arızalı", None),
 "p0328-loose-knock-sensor": ("Vuruntu sensörü gevşek", None),
 "p0197-faulty-engine-oil-temperature-sensor": ("Arızalı motor yağ sıcaklık sensörü", None),
 "p0135-internal-short-or-open-in-the-heater-element": ("Isıtıcı elemanında iç kısa devre veya kopukluk", None),
 "p0135-o2-heater-circuit-wiring-high-resistance": ("O2 ısıtıcı devresi kablosunda yüksek direnç", None),
 "p0135-o2-heater-element-resistance-is-high": ("O2 ısıtıcı elemanı direnci yüksek", None),
 "p0218-restriction-debris-in-transmission-cooler": ("Şanzıman yağ soğutucusunda tıkanma/tortu", None),
 "p0218-transmission-cooler-or-lines-restricted": ("Şanzıman soğutucusu veya hatları tıkalı", None),
 "p0340-the-camshaft-position-sensor-may-have-failed": ("Eksantrik konum sensörü arızalanmış olabilir", "The camshaft position sensor may have failed"),
 "p0133-the-oxygen-sensor-is-faulty": ("Oksijen sensörü arızalı", None),
 # ---- Turkish originals -> English
 "p0101-maf-sensoru-sicak-telinde-yag-toz-veya-partikul-kirl": (None, "Oil, dust or particle contamination on the MAF hot wire"),
 "charge-air-leak": (None, "Torn intercooler hose / compressor wheel damage (low boost)"),
 "egr-low-flow": (None, "Low EGR flow + low turbo boost (shared air path)"),
 "egr-valve-stuck": (None, "EGR valve/system insufficient flow"),
 "vgt-stuck": (None, "VGT vanes stuck with soot / wastegate fault (overboost)"),
 "engine-overtemp": (None, "Engine overheating"),
 "coolant-sensor-open": (None, "ECT sensor wire open (ECU reads a false -40°C)"),
 "thermostat-stuck": (None, "Thermostat stuck closed / viscous fan not locking / radiator blocked"),
 "misfire-ignition": (None, "Spark plug/coil ignition leak or blocked injector (misfires)"),
 "lean-bank-1": (None, "Bank 1 lean mixture (vacuum leak / MAF)"),
 "lean-both-banks": (None, "Lean mixture on both banks (vacuum leak / MAF)"),
 "injector-circuit": (None, "Cylinder injector circuit open/short (SPN 651..656)"),
 "oil-pressure-sensor": (None, "Oil pressure sensor range/performance problem"),
 "oil-pump-wear": (None, "Oil pump wear / low oil in the sump (SPN 100 FMI 1)"),
 "oil-sensor-shorts": (None, "Oil pressure sensor wiring short / open (SPN 100 FMI 3/4)"),
 "can-power-ground": (None, "Supply/ground fault (CAN communication + alternator control loss)"),
 "dpf-diff-pressure": (None, "DPF blockage (efficiency + differential pressure sensor)"),
 "dpf-efficiency": (None, "DPF efficiency below threshold (Bank 1)"),
 "dpf-soot-overload": (None, "DPF soot load exceeded (>35 kPa delta-P)"),
 "def-level-low": (None, "AdBlue (DEF) level low / quality unsuitable"),
 "scr-nox-dosing": (None, "SCR dosing / fuel quality problem (NOx + SCR system)"),
 "p0700-sanziman-kontrol-modulunde-tcm-aktif-bir-mekanik-vey": (None, "An active mechanical or hydraulic fault code stored in the TCM"),
 "p0700-tcm-ile-motor-kontrol-modulu-ecm-arasinda-can-bus-ha": (None, "CAN bus communication delay between the TCM and the ECM"),
 "fuel-rail-low": (None, "High-pressure fuel pump wear / filter blockage"),
 "fuel-rail-regulator": (None, "Fuel rail pressure regulator / pump fault"),
 "rail-pressure-sensor": (None, "Common rail pressure sensor / data"),
 "ebs-air-circuit1": (None, "Brake circuit 1 pneumatic leak / compressor fault (<5.5 bar)"),
 "hv-isolation-loss": (None, "Low HV isolation resistance (moisture ingress / winding damage)"),
 "hvil-open": (None, "HVIL interlock loop broken (MSD/bridge open)"),
 "battery-soh-degradation": (None, "EV battery pack SOH / cell delta degradation"),
 "inverter-coolant-pump": (None, "Inverter cooling system performance (EV)"),
 "crank-sync-loss": (None, "Crank/cam phase synchronisation loss (tooth gap / angular offset)"),
 "vvt-oil-sludge": (None, "VVT solenoid blocked by oil sludge / timing chain stretch"),
 "p0128-motor-sogutma-suyu-sicaklik-ect-sensoru-direnc-kayma": (None, "Engine coolant temperature (ECT) sensor resistance drift"),
 "p0128-radyator-sogutma-fani-motor-sogukken-dahi-surekli-de": (None, "Radiator fan runs continuously even with a cold engine"),
 "oil-temp-high": ("Motor yağ sıcaklığı yüksek", "Engine oil temperature high"),
 "coolant-level-low": ("Motor soğutma suyu seviyesi düşük", "Engine coolant level low"),
 "p0420-downstream-katalizor-sonrasi-sensor-2-oksijen-sensor": ("Katalizör sonrası (sensör 2) oksijen sensörünün yaşlanması", "Ageing of the downstream (post-catalyst, sensor 2) oxygen sensor"),
 "p0420-katalizor-oncesi-egzoz-manifoldunda-catlak-veya-cont": (None, "Crack or gasket leak in the exhaust manifold before the catalyst"),
 "p0442-yakit-depo-kapaginin-gevsek-takilmasi-contanin-catla": (None, "Fuel cap loose, seal cracked or wrong cap"),
 "p0442-yakit-deposu-basinc-sensoru-ftp-kalibrasyon-hatasi": (None, "Fuel tank pressure (FTP) sensor calibration error"),
}

# OEM-tagged templates: "[Make] <pattern>" with a known component
COMPONENTS = {
 "camshaft position sensor": ("eksantrik konum sensörü", "camshaft position sensor"),
 "camshaft position": ("eksantrik konum sensörü", "camshaft position sensor"),
 "crankshaft position sensor": ("krank konum sensörü", "crankshaft position sensor"),
 "crankshaft position": ("krank konum sensörü", "crankshaft position sensor"),
 "crankshaft position (ckp) sensor": ("krank konum (CKP) sensörü", "crankshaft position (CKP) sensor"),
 "intake ('a') camshaft position (cmp) sensor": ("emme ('A') eksantrik konum (CMP) sensörü", "intake ('A') camshaft position (CMP) sensor"),
 "exhaust ('b') camshaft position (cmp) sensor": ("egzoz ('B') eksantrik konum (CMP) sensörü", "exhaust ('B') camshaft position (CMP) sensor"),
 "valve timing control (vtc)": ("supap zamanlama kontrolü (VTC)", "valve timing control (VTC)"),
 "intake valve timing control solenoid valve": ("emme supap zamanlama kontrol solenoid valfi", "intake valve timing control solenoid valve"),
 "intake camshaft position actuator solenoid": ("emme eksantrik konum aktüatör solenoidi", "intake camshaft position actuator solenoid"),
 "exhaust camshaft position actuator solenoid": ("egzoz eksantrik konum aktüatör solenoidi", "exhaust camshaft position actuator solenoid"),
 "camshaft actuator": ("eksantrik aktüatörü", "camshaft actuator"),
 "camshaft timing oil control valve assembly": ("eksantrik zamanlama yağ kontrol valfi", "camshaft timing oil control valve assembly"),
 "camshaft timing oil control valve assembly (for intake camshaft)": ("eksantrik zamanlama yağ kontrol valfi (emme eksantriği)", "camshaft timing oil control valve assembly (intake camshaft)"),
 "engine control module (ecm)": ("motor kontrol modülü (ECM)", "engine control module (ECM)"),
 "driver air bag": ("sürücü hava yastığı", "driver air bag"),
 "spiral cable": ("spiral kablo (zemberek)", "spiral cable (clockspring)"),
 "abs actuator and electric unit (control unit)": ("ABS aktüatörü ve elektronik ünitesi (kontrol ünitesi)", "ABS actuator and electric unit (control unit)"),
 "mass air flow sensor": ("hava akış (MAF) sensörü", "mass air flow sensor"),
 "throttle position sensor": ("gaz kelebeği konum sensörü", "throttle position sensor"),
 "knock sensor": ("vuruntu sensörü", "knock sensor"),
 "fuel pump": ("yakıt pompası", "fuel pump"),
 "ignition coil": ("ateşleme bobini", "ignition coil"),
 "fuel injector": ("yakıt enjektörü", "fuel injector"),
 "wheel speed sensor": ("tekerlek hız sensörü", "wheel speed sensor"),
}
TEMPLATES = [
 (re.compile(r"^\[(?P<make>[^\]]+)\] Faulty (?P<c>.+?)\.?$"), "[{make}] {c_tr} arızalı", "[{make}] Faulty {c_en}"),
 (re.compile(r"^\[(?P<make>[^\]]+)\] (?P<c>.+?) (?:harness|circuit) is open or shorted(?: circuit)?\.?$"),
  "[{make}] {c_tr} kablo demeti kopuk veya kısa devre", "[{make}] {c_en} harness open or shorted"),
 (re.compile(r"^\[(?P<make>[^\]]+)\] (?P<c>.+?) circuit open or shorted\.?$"),
  "[{make}] {c_tr} devresi kopuk veya kısa devre", "[{make}] {c_en} circuit open or shorted"),
 (re.compile(r"^\[(?P<make>[^\]]+)\] (?P<c>.+?) circuit poor electrical (?:connection|connector)\.?$"),
  "[{make}] {c_tr} devresinde zayıf elektrik bağlantısı", "[{make}] {c_en} circuit poor electrical connection"),
]

with open("data/diagnostics/root_cause_graph.json", encoding="utf-8") as fh:
    nodes = json.load(fh)["nodes"]
ids = {n["id"] for n in nodes}
missing = sorted(set(CURATED) - ids)
if missing:
    raise SystemExit(f"curated ids not in the graph: {missing}")

titles = {}
for n in nodes:
    nid, title = n["id"], " ".join(str(n["title"]).split())
    if nid in CURATED:
        tr, en = CURATED[nid]
        entry = {k: v for k, v in (("tr", tr), ("en", en)) if v}
        if entry:
            titles[nid] = entry
        continue
    for pattern, tr_t, _en_t in TEMPLATES:  # English originals are kept as they are
        m = pattern.match(title)
        comp = COMPONENTS.get(m.group("c").lower()) if m else None
        if m and comp:
            c_tr = comp[0][0].upper() + comp[0][1:]
            titles[nid] = {"tr": tr_t.format(make=m.group("make"), c_tr=c_tr)}  # English original stays
            break

out = {
 "schema_version": 1,
 "title": "TR/EN display titles for root-cause graph nodes",
 "_rule": ("A display translation of the SAME node title (curated by node id, or by template for OEM-tagged "
           "phrasings with a known component). No new cause, code or value. Nodes without an entry keep their "
           "original title."),
 "titles": dict(sorted(titles.items())),
 "provenance": [{
   "provenance_id": "prv-graph-title-i18n",
   "target": {"record_id": "graph_title_i18n", "field": "titles"},
   "activity": "translate_labels",
   "activity_version": "copilot-graph-titles-1",
   "agent": {"type": "curator", "id": "ucanlab-copilot-curation", "role": "author"},
   "source": {"title": "root_cause_graph.json node titles", "path": "data/diagnostics/root_cause_graph.json",
              "type": "internal_kb"},
   "transform": {"rule_id": "label-translation"},
   "confidence": "single_source",
 }],
}
with open(P, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
    fh.write("\n")
print(len(titles))
