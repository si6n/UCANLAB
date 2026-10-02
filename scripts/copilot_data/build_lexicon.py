# -*- coding: utf-8 -*-
"""Generate data/diagnostics/symptom_lexicon.json (curated TR/EN phrasings for existing canonical symptoms)."""
import json
import os

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))  # run from repo root

P = "data/diagnostics/symptom_lexicon.json"
def prov(sid):
    return {
        "provenance_id": f"prv-lex-{sid}",
        "target": {"record_id": sid, "field": "phrases"},
        "activity": "curate_paraphrase",
        "activity_version": "copilot-upgrade-1",
        "agent": {"type": "curator", "id": "ucanlab-copilot-curation", "role": "author"},
        "source": {"title": f"canonical_symptoms.json symptom '{sid}' (name_tr/name_en/keywords)",
                   "path": f"data/diagnostics/canonical_symptoms.json#{sid}", "type": "internal_kb"},
        "transform": {"rule_id": "paraphrase-of-existing-symptom-definition"},
        "confidence": "single_source",
    }
E = [
 ("engine-overheating", ["motor isiniyor","motor ısınıyor","hararet yapiyor","hararet yapıyor","su sicakligi yuksek","su sıcaklığı yükseldi","motor sicak","motor kaynadi","hararet lambasi","harareti var","radyator kaynatiyor"], ["overheating","engine running hot","temperature gauge high","engine overheat","coolant temperature high","coolant keeps dropping","losing coolant","coolant loss"]),
 ("dpf-regeneration-failed", ["dpf lambasi","dpf lambası yandı","dpf isigi","partikul filtresi","partikül filtresi doldu","dizel partikul filtresi","dpf doldu","dpf tikali","rejenerasyon yapmiyor"], ["dpf light","dpf warning","particulate filter","diesel particulate filter","dpf full","regen failed"]),
 ("def-scr-adblue-warning", ["adblue lambasi","adblue uyarisi","adblue bitti","ure lambasi","scr arizasi","nox sensoru"], ["adblue light","def warning","def fluid low","scr warning","nox sensor fault"]),
 ("low-oil-pressure", ["yag lambasi yandi","yağ lambası yandı","yag ikaz lambasi","yag basinci yok","yag basinci dustu"], ["oil pressure low","oil warning light","low oil pressure light"]),
 ("turbo-underboost-power-loss", ["guc kaybi","güç kaybı","motor cekmiyor","motor çekmiyor","turbo calismiyor","hizlanmiyor","guc yok","arac cekmiyor","araba cekmiyor","turbo islik","turbodan islik sesi"], ["loss of power","lack of power","no power","turbo not working","slow acceleration","turbo whistle"]),
 ("turbo-overboost", ["turbo fazla basiyor","asiri turbo basinci"], ["overboost","boost too high"]),
 ("rough-idle-vibration", ["motor tekliyor","rolanti dalgalaniyor","motor sarsiliyor","motor titriyor"], ["engine misfiring","rough running","engine shaking","idle unstable"]),
 ("misfire-random-multiple", ["silindir tekletiyor","ateşleme hatası","atesleme hatasi","motor lambasi yanip sonuyor","motor ariza lambasi yanip sonuyor","check engine yanip sonuyor","motor isigi yanip sonuyor"], ["misfire","engine misfire","flashing check engine light"]),
 ("crank-no-start", ["motor calismiyor","motor çalışmıyor","mars aliyor calismiyor","calismiyor mars donuyor","arac calismiyor","arac hic calismiyor"], ["engine wont start","won't start","no start condition","cranks no start"]),
 ("battery-drain-parasitic", ["aku bitti","akü bitti","aku zayif","aku sarj olmuyor","mars tik tik","marş tık tık","aku lambasi yaniyor","sarj lambasi yaniyor","kontak acinca hicbir sey olmuyor","kontak acilinca hicbir sey olmuyor"], ["battery dead","battery flat","weak battery","clicks but no crank","battery light on","charging light on","battery keeps dying","battery drains overnight","starter clicks","nothing happens when i turn the key"]),
 ("starter-relay-circuit-open", ["mars basmiyor","marş basmıyor","mars donmuyor","mars motoru donmuyor","kontak acinca hicbir sey olmuyor","kontak acilinca hicbir sey olmuyor"], ["no crank","starter not turning","starter does not turn","nothing happens when i turn the key","no click when starting"]),
 ("alternator-overcharging", ["sarj voltaji yuksek","voltaj cok yuksek"], ["charging voltage too high","overcharging"]),
 ("abs-esp-traction-fault", ["abs isigi","abs ışığı yandı","esp isigi","abs arizasi","cekis kontrol lambasi"], ["abs warning light","abs fault","stability control light","traction control fault"]),
 ("ebs-air-brake-leak", ["havali fren kacak","fren hava basinci dusuk","hava basinci dusuyor"], ["air pressure low","brake air leak","low air pressure warning","air pressure dropping","losing air pressure"]),
 ("electronic-parking-brake-stuck", ["el freni arizasi","el freni acilmiyor"], ["parking brake fault","parking brake will not release","parking brake stuck","parking brake won't release"]),
 ("transmission-slip-limp", ["sanziman arizasi","vites atmiyor","acil durum modu","limp mod","vites kutusu","limp moduna girdi","acil moda girdi","koruma moduna girdi"], ["limp home","transmission fault","gearbox fault","wont shift","won't shift","gearbox won't shift","transmission won't shift"]),
 ("can-bus-communication-loss", ["haberlesme hatasi","haberleşme yok","ecu baglanti yok","modul cevap vermiyor"], ["no communication","lost communication","module not responding","network error"]),
 ("ev-hv-isolation-warning", ["izolasyon arizasi","izolasyon uyarisi","yuksek voltaj uyarisi","yüksek voltaj arızası","hv arizasi"], ["isolation fault","insulation fault","high voltage warning","hv system fault"]),
 ("ev-thermal-runaway-early-warning", ["batarya duman","batarya dumani","batarya yaniyor","batarya cok isindi","batarya asiri sicak"], ["battery smoke","battery overheating","battery fire","battery venting"]),
 ("ev-battery-cell-imbalance", ["hucre dengesizligi","hücre voltaj farkı"], ["cell imbalance","cell voltage deviation"]),
 ("ev-charging-interlock-fault", ["sarj etmiyor","şarj olmuyor","sarj kilidi"], ["will not charge","charging fault","not charging","won't charge","car won't charge"]),
 ("hd-engine-derate-inducement", ["guc sinirlamasi","güç sınırlaması","tork dusurme","motor kisitlandi","guc kisitlamasi"], ["derate","power derate","engine limited"]),
 ("black-smoke", ["siyah duman atiyor","kara duman atıyor"], ["black smoke from exhaust","smoking black"]),
 ("glow-plug-cold-start", ["soguk calismiyor","kizdirma bujisi","sabah zor calisiyor","mars zor aliyor","zor calisiyor"], ["hard cold start","glow plug fault","hard to start"]),
 ("fuel-rail-pressure-drop", ["yakit basinci dusuk","rail basinci dusuk"], ["low fuel pressure","rail pressure low"]),
 ("steering-angle-sensor-uncalibrated", ["direksiyon lambasi","direksiyon arizasi"], ["steering warning light","steering fault"]),
 ("ac-refrigerant-pressure-low", ["klima calismiyor","klima soguk uflemiyor","klima sogutmuyor","klima sicak hava veriyor"], ["ac not cooling","air conditioning not working","ac blowing warm","ac not blowing cold","air con not cold"]),
 ("coolant-fan-control-circuit", ["fan calismiyor","fan donmuyor","fan devreye girmiyor","fan surekli calisiyor"], ["radiator fan not working","cooling fan not working","fan always on"]),
 ("coolant-thermostat-stuck-open", ["kalorifer isitmiyor","kalorifer ilik","motor isinmiyor","motor gec isiniyor"], ["heater not hot","engine not warming up","engine runs cold","heater blowing cold","no heat from heater"]),
 ("crank-sensor-signal-missing", ["motor stop ediyor","seyir halinde stop etti","seyirde stop ediyor","durduk yere stop ediyor","motor durduk yere duruyor"], ["engine stalls","stalls while driving","engine cuts out","car dies while driving","engine dies while driving"]),
 ("fuel-pump-driver-module-offline", ["seyir halinde stop etti","yakit pompasi calismiyor","yakit pompasi sesi yok"], ["fuel pump not working","no fuel pump sound","car dies while driving"]),
 ("immobilizer-key-transponder-missing", ["anahtari tanimiyor","immobilizer lambasi yanip sonuyor","immobilizer arizasi","anahtar okumuyor"], ["key not recognised","immobilizer light flashing","immobiliser fault","key not detected","key not recognized"]),
 ("marine-raw-water-cooling", ["tekne motorundan su gelmiyor","egzozdan su gelmiyor","deniz suyu gelmiyor"], ["no raw water flow","no water from exhaust"]),
 ("airbag-squib-resistance-high-driver", ["srs lambasi yaniyor","hava yastigi lambasi"], ["airbag light on","srs light on","airbag warning light"]),
]
lex = {
 "schema_version": 1,
 "title": "Copilot symptom lexicon (extra TR/EN phrasings for canonical symptoms)",
 "_rule": ("Every entry points at an EXISTING canonical_symptoms.json symptom_id and only adds "
           "alternative everyday phrasings (TR/EN) for the same complaint. No new symptom, cause, "
           "code or value is introduced. Validated by scripts/validate_copilot_data.py."),
 "entries": [
   {"symptom_id": s, "phrases_tr": tr, "phrases_en": en, "provenance": [prov(s)]} for s, tr, en in E
 ],
 "safety_terms": {
   "_rule": "Words that trigger a safety banner. Banner text is a fixed template; terms only route.",
   "high_voltage": {"tr": ["yuksek voltaj","yüksek gerilim","izolasyon","turuncu kablo","hvil","bms","batarya paketi","cekis bataryasi"], "en": ["high voltage","isolation","orange cable","hvil","bms","traction battery","battery pack"]},
   "fire": {"tr": ["yangin","yanık kokusu","yanik kokusu","alev","duman cikiyor","batarya duman","kivilcim","yakit kokusu","yakit kacagi"], "en": ["fire","flames","burning smell","smoke coming","sparks","fuel smell","fuel leak","thermal runaway"]},
   "brakes": {"tr": ["fren tutmuyor","fren pedali","fren bosa","fren arizasi","abs","balata","fren hava","fren hidrolik"], "en": ["brakes not working","brake pedal","brake failure","abs","brake fluid","air brake"]},
   "steering": {"tr": ["direksiyon","dumen"], "en": ["steering","power steering","helm"]},
   "provenance": [{
     "provenance_id": "prv-lex-safety-terms",
     "target": {"record_id": "safety_terms", "field": "terms"},
     "activity": "curate_safety_routing_terms",
     "agent": {"type": "curator", "id": "ucanlab-copilot-curation", "role": "author"},
     "source": {"title": "README.md design principle 3 (brakes, steering, high voltage, fire risk warned first)",
                "path": "README.md#product-purpose", "type": "internal_kb"},
     "confidence": "single_source"}]
 }
}
with open(P, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(lex, fh, ensure_ascii=False, indent=1)
    fh.write("\n")
print(len(E))
