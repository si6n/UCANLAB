# -*- coding: utf-8 -*-
"""Generate data/diagnostics/reasoning_rules.json (common-cause rules over several active codes).

Only shared causes the root-cause graph does not already name are listed (the
graph has "lean on both banks" for P0171 + P0174). When several codes are active at once, one shared fault often explains all of
them better than a separate fault per code (a shorted 5 V sensor reference
sets every sensor's "circuit low" code; a CAN backbone fault sets every "lost
communication" code). Each rule names that shared cause, the evidence pattern
that points to it and the first check that confirms or rules it out.

Matching (all on the ACTIVE, KNOWN codes of one request):
  codes_regex   regex on the code key ("P0301", "SPN 110 FMI 4")
  title_regex   regex on the code's folded Turkish + English title
  min_codes     how many distinct matching codes trigger the rule
  required      codes that must all be active (optional)
  signal_low    a measured signal whose low reading counts as one match and can stand in for ``required``
  explains_all  the shared cause explains every active code (otherwise only the matching ones)
The rule text is curated workshop knowledge; it adds a candidate, never a value.
"""
import json
import os

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))  # run from repo root

P = "data/diagnostics/reasoning_rules.json"

RULES = [
 {
  "id": "shared-sensor-reference",
  "title_tr": "Ortak 5 V sensör referans hattında kısa devre / besleme kaybı",
  "title_en": "Shared 5 V sensor reference shorted / supply lost",
  "rationale_tr": "farklı sensörlerin hepsi aynı anda 'devre düşük' gösteriyor; sensörler genelde ortak 5 V referansı paylaşır",
  "rationale_en": "different sensors all read 'circuit low' at once; sensors usually share one 5 V reference",
  "step_tr": "Sensör soketlerinde kontak açıkken 5 V referansı ölçün; sensörleri tek tek ayırıp referansın geri gelip gelmediğine bakın (kısa devre yapan sensörü bulur).",
  "step_en": "Measure the 5 V reference at the sensor connectors with the ignition on; unplug the sensors one by one and see whether the reference returns (finds the shorting sensor).",
  "match": {"title_regex": r"(sensor|pressure|position|temperature|sensoru|basinc|konum|sicaklik).*(circuit low|devre dusuk|low input)|sensor reference voltage|referans voltaj",
            "codes_regex": r"^[PU]", "min_codes": 2},
 },
 {
  "id": "shared-sensor-ground",
  "title_tr": "Ortak sensör şase (sinyal dönüş) hattında kopukluk",
  "title_en": "Shared sensor ground (signal return) open",
  "rationale_tr": "farklı sensörlerin hepsi aynı anda 'devre yüksek' gösteriyor; ortak sinyal şasesi kopunca her sensör yüksek okur",
  "rationale_en": "different sensors all read 'circuit high' at once; when the shared signal ground opens every sensor reads high",
  "step_tr": "Sensör şase pinleri ile ECU şasesi arasındaki direnci ölçün (birkaç ohm'dan fazla olmamalı); ortak şase noktasını ve ECU konnektörünü kontrol edin.",
  "step_en": "Measure resistance between the sensor ground pins and ECU ground (no more than a few ohms); check the shared ground point and the ECU connector.",
  "match": {"title_regex": r"(sensor|pressure|position|temperature|sensoru|basinc|konum|sicaklik).*(circuit high|devre yuksek|high input)",
            "codes_regex": r"^P", "min_codes": 2},
 },
 {
  "id": "network-backbone",
  "title_tr": "Ortak CAN hattı / ağ geçidi / modül beslemesi arızası",
  "title_en": "Shared CAN backbone / gateway / module supply fault",
  "rationale_tr": "birden çok modülle iletişim aynı anda kesilmiş; her modülün ayrı ayrı bozulması olası değil",
  "rationale_en": "communication with several modules is lost at once; each module failing separately is unlikely",
  "step_tr": "Akü ayrıkken OBD 6 ve 14. pinler arasında yaklaşık 60 Ohm olduğunu doğrulayın; ardından ortak sigortaları ve modül şaselerini kontrol edin.",
  "step_en": "With the battery disconnected confirm about 60 Ohm between OBD pins 6 and 14; then check the shared fuses and module grounds.",
  "match": {"title_regex": r"lost communication|iletisim kaybi|communication bus|haberlesme", "codes_regex": r"^U", "min_codes": 2},
 },
 {
  "id": "low-supply-voltage",
  "title_tr": "Düşük besleme gerilimi (akü / şarj) diğer kodları tetikliyor",
  "title_en": "Low supply voltage (battery / charging) is setting the other codes",
  "rationale_tr": "sistem gerilimi düşük; düşük gerilimde modüller ve sensörler sahte kod üretir",
  "rationale_en": "system voltage is low; at low voltage modules and sensors set false codes",
  "step_tr": "Önce aküyü ve şarjı düzeltin (rölantide 13.8 - 14.4 V), kodları silip yeniden okuyun; kalan kodlar gerçek arızadır.",
  "step_en": "Fix the battery and charging first (13.8 - 14.4 V at idle), clear and re-read the codes; the codes that remain are real faults.",
  "match": {"required": ["P0562"], "min_codes": 2, "signal_low": "BatteryVoltage", "explains_all": True},
 },
 {
  "id": "multi-cylinder-misfire",
  "title_tr": "Birden çok silindirde tekleme: ortak neden (yakıt basıncı, emme kaçağı, krank sinyali, bobin beslemesi)",
  "title_en": "Misfire on several cylinders: a shared cause (fuel pressure, intake leak, crank signal, coil supply)",
  "rationale_tr": "tekleme birden çok silindirde; her silindirin bujisi/bobini aynı anda bozulmuş olması zayıf olasılık",
  "rationale_en": "the misfire is on several cylinders; every cylinder's plug/coil failing at once is unlikely",
  "step_tr": "Yakıt basıncını ve yakıt düzeltmelerini (STFT/LTFT) okuyun, emme tarafında duman testi yapın; ancak bunlar temizse bobinleri tek tek değiştirerek deneyin.",
  "step_en": "Read fuel pressure and fuel trims (STFT/LTFT) and smoke test the intake; only if those are clean, swap coils one by one.",
  "match": {"codes_regex": r"^P03(0[1-9]|1[0-2])$", "min_codes": 2},
 },
 {
  "id": "crank-cam-together",
  "title_tr": "Krank ve eksantrik sinyali birlikte: zamanlama (triger) veya ortak sensör beslemesi",
  "title_en": "Crank and cam signal faults together: timing (belt/chain) or the shared sensor supply",
  "rationale_tr": "krank ve eksantrik kodları birlikte; iki sensörün aynı anda bozulmasından çok zamanlama kayması veya ortak besleme olası",
  "rationale_en": "crank and cam codes together; timing slip or a shared supply is likelier than two sensors failing at once",
  "step_tr": "Krank ve eksantrik sinyallerini osiloskopla birlikte çekip faz ilişkisini kontrol edin; sensör beslemesini ölçün.",
  "step_en": "Scope the crank and cam signals together and check their phase relationship; measure the sensor supply.",
  "match": {"codes_regex": r"^P(033[5-9]|034[0-9]|036[5-9]|001[6-9])$", "min_codes": 2,
            "title_regex": r"crank|cam|krank|eksantrik|correlation|korelasyon"},
 },
]

def prov(rid):
    return {
        "provenance_id": f"prv-rule-{rid}",
        "target": {"record_id": rid, "field": "rule"},
        "activity": "curate_common_cause_rule",
        "activity_version": "copilot-rules-1",
        "agent": {"type": "curator", "id": "ucanlab-copilot-curation", "role": "author"},
        "source": {"title": "Workshop common-cause diagnosis practice (shared reference, ground, network, supply)",
                   "path": "docs/COPILOT.md#3-1-2", "type": "internal_kb"},
        "transform": {"rule_id": "common-cause-over-active-codes"},
        "confidence": "single_source",
    }

out = {
 "schema_version": 1,
 "title": "Copilot common-cause reasoning rules (several active codes, one shared fault)",
 "_rule": "Each rule adds ONE candidate that explains several active codes at once. No value or code is invented.",
 "rules": [dict(r, provenance=[prov(r["id"])]) for r in RULES],
}
with open(P, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
    fh.write("\n")
print(len(RULES))
