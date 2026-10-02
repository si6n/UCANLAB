# -*- coding: utf-8 -*-
"""Generate data/diagnostics/symptom_checks.json (answer effects for canonical symptom questions).

Each check turns one of a symptom's ``initial_questions`` (canonical_symptoms.json)
into a question the copilot can take an answer to. An answer only moves the
symptom's OWN subsystems (by index into its ``subsystems`` list) or its OWN
candidate codes up ("favor") or down ("rule_out"), and carries one short,
curated explanation. No new symptom, code or subsystem is introduced; the
validator and the unit tests enforce that every target exists.

Target notation: an int is an index into the symptom's ``subsystems``; a string
is one of the symptom's ``candidate_dtcs``.
"""
import json
import os

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))  # run from repo root

P = "data/diagnostics/symptom_checks.json"


def prov(sid):
    return {
        "provenance_id": f"prv-chk-{sid}",
        "target": {"record_id": sid, "field": "checks"},
        "activity": "curate_answer_effects",
        "activity_version": "copilot-checks-1",
        "agent": {"type": "curator", "id": "ucanlab-copilot-curation", "role": "author"},
        "source": {"title": f"canonical_symptoms.json symptom '{sid}' (initial_questions, subsystems, candidate_dtcs)",
                   "path": f"data/diagnostics/canonical_symptoms.json#{sid}", "type": "internal_kb"},
        "transform": {"rule_id": "answer-effect-over-existing-question"},
        "confidence": "single_source",
    }


def yn(q, en, yes=None, no=None, tr=None):
    """Yes/no check. ``yes``/``no`` = (favor, rule_out, note_tr, note_en)."""
    out = {"q": q, "kind": "yes_no", "question_en": en, "outcomes": {}}
    if tr:
        out["question_tr"] = tr
    for key, eff in (("yes", yes), ("no", no)):
        if eff:
            fav, rule, ntr, nen = eff
            out["outcomes"][key] = {"favor": fav, "rule_out": rule, "note_tr": ntr, "note_en": nen}
    return out


def meas(q, en, unit, bands, text_keys=(), tr=None):
    """Measurement check. ``bands`` = [(lo, hi, favor, rule_out, note_tr, note_en)], lo <= v < hi."""
    out = {"q": q, "kind": "measurement", "unit": unit, "question_en": en, "text_keys": list(text_keys),
           "bands": [{"min": lo, "max": hi, "favor": fav, "rule_out": rule, "note_tr": ntr, "note_en": nen}
                     for lo, hi, fav, rule, ntr, nen in bands]}
    if tr:
        out["question_tr"] = tr
    return out


C = {
 "crank-no-start": [
  yn(0, "Does the starter crank the engine briskly?",
     yes=([], [2], "Marş canlı dönüyor: akü ve marş beslemesi büyük olasılıkla yeterli.",
          "Starter cranks briskly: battery and starter supply are probably adequate."),
     no=([2], [], "Marş ağır dönüyor: önce akü gerilimi ve marş kablo bağlantıları.",
         "Starter cranks slowly: check battery voltage and starter cable connections first.")),
  yn(2, "Is there enough fuel and can the fuel pump be heard priming?",
     yes=([], [1], "Yakıt var ve pompa çalışıyor: besleme tarafı daha az olası.",
          "Fuel present and pump running: the supply side is less likely."),
     no=([1, "P0627"], [], "Pompa sesi yok: yakıt pompası, sigortası ve kumanda devresi (P0627) önce.",
         "No pump sound: fuel pump, its fuse and control circuit (P0627) first.")),
 ],
 "black-smoke": [
  yn(0, "Is there a clear loss of pull or a whistle under acceleration?",
     yes=([0], [], "Çekiş düşüşü veya ıslık: hava tarafı (turbo, şarj hortumu) önce.",
          "Loss of pull or whistle: air side (turbo, boost hose) first."),
     no=([], [0], "Çekiş normal: hava tarafı daha az olası.", "Pull is normal: the air side is less likely.")),
  yn(2, "Are live MAF and boost pressure at nominal values?",
     yes=([1], [0], "Hava akışı ve şarj basıncı normal: fazla yakıt (enjektör) tarafı öne çıkar.",
          "Air flow and boost are normal: excess fuel (injectors) comes forward."),
     no=([0, "P0101", "P0299"], [], "Hava akışı veya şarj basıncı düşük: MAF (P0101) ve turbo (P0299) önce.",
         "Air flow or boost low: MAF (P0101) and turbo (P0299) first.")),
 ],
 "engine-overheating": [
  yn(0, "Is the coolant level in the expansion tank below Min?",
     yes=([0], [], "Soğutma suyu eksik: hortum, radyatör ve su pompasında kaçak arayın; tamamlamadan çalıştırmayın.",
          "Coolant is low: look for leaks at hoses, radiator and water pump; do not run it before topping up."),
     no=([], [0], "Seviye yerinde: dış kaçak daha az olası.", "Level is fine: an external leak is less likely.")),
  yn(1, "Does the radiator fan switch on when the engine is hot?",
     yes=([], [1], "Fan devreye giriyor: fan kumandası daha az olası.", "Fan switches on: fan control is less likely."),
     no=([1], [], "Fan devreye girmiyor: fan motoru, rölesi ve sıcaklık kumandası önce.",
         "Fan does not switch on: fan motor, relay and temperature control first.")),
  yn(2, "Does the heater blow hot air?",
     tr="Kalorifer sıcak hava üflüyor mu?",
     no=([1, 2], [], "Hararetle birlikte kalorifer soğuk: termostat kapalı kalmış, su eksik veya sistemde hava/gaz (conta) olabilir.",
         "Heater cold while overheating: stuck-closed thermostat, low coolant or air/gas in the system (head gasket).")),
 ],
 "rough-idle-vibration": [
  yn(2, "Is the check engine lamp flashing?",
     tr="Arıza lambası (Check Engine) yanıp sönüyor mu?",
     yes=([0], [], "Lamba yanıp sönüyor: aktif tekleme var, katalizör zarar görebilir; ateşleme (buji/bobin) önce.",
          "Lamp flashing: active misfire, the catalyst can be damaged; ignition (plugs/coils) first."),
     no=([], [], "Lamba sabit veya sönük: kodları okuyup tekleme sayaçlarına bakın.",
         "Lamp steady or off: read the codes and check the misfire counters.")),
 ],
 "low-oil-pressure": [
  yn(0, "Is the dipstick level below the Min mark?",
     tr="Yağ çubuğunda seviye Min çizgisinin altında mı?",
     yes=([0], [], "Yağ eksik: tamamlamadan motoru çalıştırmayın; kaçak veya yağ yakma arayın.",
          "Oil is low: do not run the engine before topping up; look for leaks or oil burning."),
     no=([1, 2], [0], "Seviye yerinde ama basınç düşük: yağ pompası, filtre veya yatak boşluğu; basıncı mekanik saatle doğrulayın.",
         "Level fine but pressure low: oil pump, filter or bearing clearance; confirm with a mechanical gauge.")),
  yn(1, "Is there a mechanical knock or bearing noise from the engine?",
     yes=([2], [], "Vuruntu sesi: yatak hasarı olabilir; motoru çalıştırmayın.",
          "Knocking: bearing damage is possible; do not run the engine.")),
  yn(2, "Is there a visible leak at the oil filter or sump?",
     yes=([0], [], "Görünür kaçak: yağ kaybının kaynağı; gidermeden çalıştırmayın.",
          "Visible leak: source of the oil loss; fix before running.")),
 ],
 "dpf-regeneration-failed": [
  yn(0, "Is the engine warning lamp on together with the DPF lamp?",
     yes=([1, 2], [], "DPF lambasıyla birlikte arıza lambası: rejenerasyonu engelleyen sensör/devre arızası olabilir; kodları okuyun.",
          "Engine lamp with the DPF lamp: a sensor or circuit fault may be blocking regeneration; read the codes."),
     no=([0], [], "Yalnız DPF lambası: filtre dolmuş; uzun otoyol sürüşü veya servis rejenerasyonu.",
         "DPF lamp only: the filter is loaded; a long motorway drive or a service regeneration.")),
 ],
 "def-scr-adblue-warning": [
  yn(0, "Is the AdBlue tank full with fluid of reliable quality?",
     yes=([], [], "Sıvı yerinde: dozaj ve sensör tarafına bakın.", "Fluid is fine: look at dosing and sensors."),
     no=([0], [], "AdBlue eksik veya kalitesi şüpheli: tankı ISO 22241 onaylı sıvıyla doldurun.",
         "AdBlue low or doubtful: refill with ISO 22241 approved fluid.")),
  yn(2, "Is there crystallisation or a leak at the AdBlue injector or hoses?",
     yes=([0], [], "Enjektör veya hortumda kristal: dozaj valfi tıkanması; temizlik veya değişim.",
          "Crystals at the injector or hoses: dosing valve blockage; clean or replace.")),
 ],
 "transmission-slip-limp": [
  yn(1, "Is the transmission fluid low, dark or burnt-smelling?",
     tr="Şanzıman yağı eksik, koyu renkli veya yanık kokulu mu?",
     yes=([1, 2], [], "Yağ eksik veya yanık: önce seviye; iç aşınma (konvertör, kavramalar) olabilir.",
          "Fluid low or burnt: level first; internal wear (converter, clutches) is possible."),
     no=([0, 2], [], "Yağ temiz ve yerinde: elektrik ve kumanda (TCU, solenoidler) öne çıkar.",
         "Fluid clean and full: electrical control (TCU, solenoids) comes forward.")),
  yn(2, "Does the rpm flare during a shift and then engage harshly?",
     yes=([2, "P0750"], [], "Devir fırlayıp sert geçiş: hat basıncı veya vites solenoidi (valf gövdesi).",
          "Rpm flare then harsh engagement: line pressure or shift solenoid (valve body).")),
 ],
 "abs-esp-traction-fault": [
  yn(0, "Does the ABS pulse in the pedal even on dry asphalt?",
     yes=([1], [], "Kuru zeminde ABS devreye giriyor: hatalı tekerlek hızı sinyali (sensör, halka, boşluk).",
          "ABS cuts in on dry road: a wrong wheel speed signal (sensor, tone ring, air gap).")),
  yn(1, "Are tyre pressures and sizes equal on all four wheels?",
     no=([1], [], "Lastik ebat veya basınç farkı: tekerlek hızları farklı okunur; önce lastikleri eşitleyin.",
         "Tyre size or pressure mismatch: wheel speeds read differently; equalise the tyres first.")),
  yn(2, "With the wheel straight, does the steering angle sensor read 0 degrees?",
     yes=([], [2], "Açı sensörü düzgün okuyor: kalibrasyon kaybı daha az olası.",
          "Angle sensor reads correctly: lost calibration is less likely."),
     no=([2], [], "Düz konumda açı 0 değil: direksiyon açı sensörü kalibrasyonu gerekli.",
         "Angle not 0 when straight: the steering angle sensor needs calibration.")),
 ],
 "turbo-underboost-power-loss": [
  yn(0, "Is there an air hiss or whistle when accelerating?",
     yes=([1], [], "Fıslama: basınç hattında kaçak (hortum, kelepçe, intercooler).",
          "Hiss: a leak on the pressure side (hose, clamp, intercooler).")),
  yn(1, "Is there a split intercooler hose or a loose clamp?",
     yes=([1], [], "Yırtık hortum veya gevşek kelepçe: kaçak bulundu; giderip tekrar deneyin.",
          "Split hose or loose clamp: the leak is found; fix it and retest."),
     no=([], [1], "Hortumlar sağlam: basınç kaçağı daha az olası.", "Hoses intact: a boost leak is less likely.")),
  yn(2, "Does the VNT lever move freely by hand?",
     yes=([], [2], "VNT kolu serbest: aktüatör sıkışması daha az olası.", "VNT lever free: a stuck actuator is less likely."),
     no=([2], [], "VNT kolu sıkı: kanatlarda kurum veya aktüatör arızası.", "VNT lever stiff: soot on the vanes or a failed actuator.")),
 ],
 "turbo-overboost": [
  yn(2, "Does the N75 / boost control valve work in the live test?",
     yes=([2], [1], "Kontrol valfi çalışıyor: MAP sensörü okumasını gerçek basınçla karşılaştırın.",
          "Control valve works: compare the MAP reading with the real pressure."),
     no=([1], [], "Kontrol valfi çalışmıyor: wastegate solenoidi ve hortumları önce.",
         "Control valve does not work: wastegate solenoid and its hoses first.")),
 ],
 "battery-drain-parasitic": [
  meas(0, "What is the sleep current with the ignition off and the car locked (mA)?", "mA", [
     (None, 50, [0], [1], "Uyku akımı 50 mA altında: kaçak yok; akünün kendisi veya şarj yetersizliği öne çıkar.",
      "Sleep current under 50 mA: no drain; the battery itself or undercharging comes forward."),
     (50, None, [1], [], "Uyku akımı 50 mA üstü: sigortaları tek tek çekerek kaçak devreyi bulun; uyanık kalan modül olabilir.",
      "Sleep current over 50 mA: pull fuses one by one to find the circuit; a module may be staying awake."),
  ], text_keys=["uyku akimi", "kacak akim", "sleep current", "parasitic draw"]),
  yn(1, "Is there an aftermarket alarm, audio system or tracker fitted?",
     yes=([], [], "Sonradan takılan cihaz: önce o cihazın sigortasını çekip uyku akımını yeniden ölçün.",
          "Aftermarket device: pull its fuse first and measure the sleep current again.")),
  meas(2, "What is the charging voltage at idle (V)?", "V", [
     (None, 13.8, [2], [], "Şarj gerilimi 13.8 V altında: alternatör veya regülatör yetersiz şarj ediyor.",
      "Charging voltage under 13.8 V: the alternator or regulator is undercharging."),
     (13.8, 14.4, [], [2], "Şarj gerilimi 13.8 - 14.4 V aralığında: alternatör büyük olasılıkla sağlam.",
      "Charging voltage within 13.8 - 14.4 V: the alternator is probably fine."),
     (14.4, None, [2], [], "Şarj gerilimi 14.4 V üstü: regülatör yüksek şarj ediyor olabilir.",
      "Charging voltage over 14.4 V: the regulator may be overcharging."),
  ], text_keys=["sarj gerilimi", "sarj voltaji", "charging voltage"],
     tr="Alternatör şarj gerilimi rölantide kaç V? (13.8V - 14.4V aralığında olmalı)"),
 ],
 "can-bus-communication-loss": [
  meas(0, "What is the resistance between OBD pins 6 and 14 with the battery disconnected (Ohm)?", "Ohm", [
     (None, 50, [0], [], "50 Ohm altında: hatlar arası kısa devre veya fazladan sonlandırma direnci.",
      "Under 50 Ohm: a short between the lines or an extra terminating resistor."),
     (50, 70, [], [2], "Yaklaşık 60 Ohm: iki sonlandırma direnci yerinde.", "About 60 Ohm: both terminating resistors are present."),
     (70, 140, [2], [], "Yaklaşık 120 Ohm: sonlandırma dirençlerinden biri devre dışı veya hat bir uçta kopuk.",
      "About 120 Ohm: one terminating resistor is missing or the line is open at one end."),
     (140, None, [0], [], "Çok yüksek direnç: CAN hattı kopuk.", "Very high resistance: the CAN line is open."),
  ], text_keys=["sonlandirma direnci", "6 ve 14", "pin 6", "termination resistance"],
     tr="Akü bağlantısı ayrıkken OBD portu 6. ve 14. pinler arasındaki direnç kaç Ohm? (60 Ohm olmalı)"),
  yn(1, "Is CAN_H or CAN_L shorted to ground or +12 V?",
     yes=([0], [], "Hat kısa devrede: kablo demetini bölüm bölüm ayırarak kısa devreyi bulun.",
          "Line shorted: split the harness section by section to find the short."),
     no=([], [0], "Kısa devre yok: hat fiziksel olarak sağlam görünüyor.", "No short: the line looks physically sound.")),
  yn(2, "Do several modules drop off the bus at the same time?",
     yes=([0, 1], [], "Birden çok modül birlikte: ortak hat veya ağ geçidi (gateway).",
          "Several modules together: the shared line or the gateway."),
     no=([], [1], "Tek modül: o modülün beslemesi, şasesi ve konnektörü önce.",
         "One module only: that module's supply, ground and connector first.")),
 ],
 "fuel-rail-pressure-drop": [
  yn(1, "Is the injector leak-off (return) quantity excessive?",
     tr="Enjektör geri dönüş (leak-off) miktarı aşırı mı?",
     yes=([0], [], "Aşırı geri dönüş: iç kaçağı olan enjektör rail basıncını düşürüyor.",
          "Excess leak-off: an injector with internal leakage is dropping rail pressure."),
     no=([1, 2], [], "Geri dönüş normal: yüksek basınç pompası veya basınç regülatörü öne çıkar.",
         "Leak-off normal: high-pressure pump or pressure regulator comes forward.")),
  yn(2, "Is the fuel filter blocked by wax or water, or long overdue?",
     tr="Yakıt filtresi parafin/su nedeniyle tıkalı veya değişimi gecikmiş mi?",
     yes=([0], [], "Filtre şüpheli: önce filtreyi değiştirip suyu boşaltın.", "Filter suspect: replace it and drain water first.")),
 ],
 "glow-plug-cold-start": [
  yn(0, "Does it start without trouble on the first crank when warm?",
     yes=([0, 1], [], "Sıcakken sorunsuz: ön ısıtma sistemi önce.", "Fine when warm: the pre-heating system first."),
     no=([], [0, 1], "Sıcakken de zor: sorun ön ısıtmada değil; yakıt ve kompresyon tarafına bakın.",
         "Hard when warm too: not pre-heating; look at fuel and compression.")),
  yn(1, "Does the glow plug relay send 12 V to the plugs when the ignition is switched on?",
     yes=([0], [1], "Röle besleme gönderiyor: bujileri tek tek ölçün.", "Relay supplies the plugs: measure the plugs one by one."),
     no=([1], [], "Bujilere besleme yok: röle, sigorta veya kumandası.", "No supply to the plugs: relay, fuse or its control.")),
  meas(2, "What is the internal resistance of each glow plug (Ohm)?", "Ohm", [
     (None, 0.5, [0], [], "0.5 Ohm altında: bujide kısa devre.", "Under 0.5 Ohm: the plug is shorted."),
     (0.5, 1.5, [], [0], "0.5 - 1.5 Ohm: buji sağlam.", "0.5 - 1.5 Ohm: the plug is fine."),
     (1.5, None, [0], [], "1.5 Ohm üstü veya sonsuz: buji arızalı (açık devre).", "Over 1.5 Ohm or open: the plug has failed."),
  ], text_keys=["buji direnci", "glow plug resistance"],
     tr="Kızdırma bujilerinin iç omik direnci kaç Ohm? (0.5 - 1.5 Ohm olmalı)"),
 ],
 "ebs-air-brake-leak": [
  yn(0, "With the engine off, is there an audible hiss when the brake is applied?",
     yes=([0], [], "Frenle birlikte fıslama: fren devresinde (körük, ventil, hortum) kaçak.",
          "Hiss with the brake applied: a leak in the brake circuit (chamber, valve, hose).")),
  yn(1, "Does the air dryer purge valve blow off at short intervals?",
     yes=([1], [], "Sık tahliye: kompresör sürekli dolduruyor; sistem kaçağı veya kompresör/regülatör arızası.",
          "Frequent purge: the compressor keeps charging; a system leak or a compressor/governor fault.")),
  yn(2, "Does water or oil come out of the tank drain valves?",
     yes=([1], [], "Tankta su/yağ: kurutucu kartuşu doymuş veya kompresör yağ atıyor.",
          "Water or oil in the tanks: the dryer cartridge is saturated or the compressor passes oil.")),
 ],
 "marine-raw-water-cooling": [
  yn(0, "Is raw water discharging steadily and strongly from the exhaust?",
     yes=([], [1], "Su akışı düzenli: ham su pompası büyük olasılıkla çalışıyor.", "Steady flow: the raw water pump is probably working."),
     no=([1, 2], [], "Egzozdan su az veya yok: motoru durdurun; kinseft, deniz suyu filtresi ve impeller.",
         "Little or no water from the exhaust: stop the engine; check seacock, strainer and impeller.")),
  yn(1, "Is the seacock fully open?",
     no=([], [], "Kinseft kapalı veya yarı açık: vanayı tam açıp akışı yeniden kontrol edin.",
         "Seacock closed or half open: open it fully and recheck the flow.")),
  yn(2, "Are the raw water pump impeller vanes intact?",
     yes=([], [1], "Impeller sağlam.", "Impeller intact."),
     no=([1], [], "Impeller kanatları kırık: değiştirin ve kopan parçaları soğutma hattında arayın.",
         "Impeller vanes broken: replace it and find the missing pieces in the cooling circuit.")),
 ],
 "misfire-random-multiple": [
  yn(1, "Did a smoke test find a leak on the intake side?",
     tr="Duman testinde emme tarafında kaçak bulundu mu?",
     yes=([0], [], "Emme kaçağı bulundu: giderip yakıt düzeltmelerini yeniden kontrol edin.",
          "Intake leak found: fix it and recheck the fuel trims."),
     no=([], [0], "Emme kaçağı yok.", "No intake leak.")),
  yn(2, "Are the short and long term fuel trims above +15 %?",
     yes=([0, 1], [2], "Karışım fakir: hava kaçağı veya düşük yakıt basıncı.", "Running lean: an air leak or low fuel pressure."),
     no=([2], [0, 1], "Yakıt düzeltmeleri normal: krank sinyali ve ateşleme öne çıkar.",
         "Fuel trims normal: crank signal and ignition come forward.")),
 ],
 "lean-condition-bank1": [
  yn(1, "Do the fuel trims move towards zero when the engine is revved?",
     yes=([1], [], "Devirde düzeltme sıfıra yaklaşıyor: vakum kaçağı (rölantide etkisi en büyük).",
          "Trims move to zero with rpm: a vacuum leak (largest effect at idle)."),
     no=([0, 2], [1], "Her devirde fakir: MAF düşük okuyor veya yakıt basıncı yetersiz.",
         "Lean at all rpm: MAF reading low or fuel pressure too low.")),
  yn(2, "Is there a leak in the brake booster vacuum hose?",
     yes=([1], [], "Servo hortumunda kaçak: değiştirip düzeltmeleri yeniden okuyun.",
          "Booster hose leaking: replace it and reread the trims.")),
 ],
 "coolant-thermostat-stuck-open": [
  yn(0, "At steady motorway speed, does the gauge drop from 90 °C towards 70 °C?",
     yes=([0], [], "Yolda sıcaklık düşüyor: termostat açık kalmış.", "Temperature drops on the road: the thermostat is stuck open.")),
  yn(1, "On a cold start, does the upper radiator hose warm up straight away?",
     yes=([0], [], "Üst hortum hemen ısınıyor: termostat açık kalmış.", "Upper hose warms at once: the thermostat is stuck open."),
     no=([1], [0], "Hortum normal sürede ısınıyor: termostat çalışıyor olabilir; sıcaklık sensörü (ECT) okumasını kontrol edin.",
         "Hose warms normally: the thermostat may be working; check the temperature sensor (ECT) reading.")),
  yn(2, "Does the heater still blow only lukewarm after 10-15 minutes of driving?",
     yes=([0], [], "Kalorifer ılık: motor çalışma sıcaklığına çıkmıyor; termostat.",
          "Heater lukewarm: the engine does not reach operating temperature; thermostat."),
     no=([1], [0], "Kalorifer sıcak ama gösterge düşük: sıcaklık sensörü (ECT) veya gösterge.",
         "Heater hot but gauge low: the temperature sensor (ECT) or the gauge.")),
 ],
 "coolant-fan-control-circuit": [
  yn(0, "Does the radiator fan start at low speed when the A/C is switched on?",
     yes=([1], [0], "Fan klima ile çalışıyor: fan motoru sağlam; sıcaklık kumandası veya kademe rölesi.",
          "Fan runs with the A/C: the motor is fine; temperature control or the speed relay.")),
  yn(1, "Does the fan run when 12 V is applied directly to its connector?",
     yes=([1], [0], "Doğrudan beslemede dönüyor: röle, PWM modülü veya kumanda.", "Runs on direct 12 V: relay, PWM module or control."),
     no=([0], [], "Doğrudan 12 V verildiğinde dönmüyor: fan motoru arızalı.", "Does not run on direct 12 V: the fan motor has failed.")),
  yn(2, "Has the fan fuse or main supply relay blown?",
     yes=([1], [], "Sigorta veya röle atmış: değiştirin; tekrar atarsa fan motoru sıkışık veya kısa devrede olabilir.",
          "Fuse or relay blown: replace it; if it blows again the motor may be seized or shorted.")),
 ],
 "alternator-overcharging": [
  meas(0, "What is the voltage across the battery terminals with the engine running (V)?", "V", [
     (None, 14.4, [], [0], "Şarj gerilimi 14.4 V altında: aşırı şarj yok.", "Charging voltage under 14.4 V: no overcharging."),
     (14.4, 15.5, [0], [], "Şarj gerilimi 14.4 - 15.5 V: sınırda yüksek; regülatörü izleyin.",
      "Charging voltage 14.4 - 15.5 V: borderline high; watch the regulator."),
     (15.5, None, [0], [], "Şarj gerilimi 15.5 V üstü: regülatör arızalı; akü ve elektronik zarar görebilir.",
      "Charging voltage over 15.5 V: the regulator has failed; battery and electronics can be damaged."),
  ], text_keys=["aku voltaji", "aku gerilimi", "sarj voltaji", "battery voltage", "charging voltage"],
     tr="Motor çalışırken akü kutup başlarında kaç V ölçülüyor?"),
  yn(1, "Is there an acid smell or acid overflow from the battery?",
     yes=([1], [], "Akü kaynamış: aküyü kontrol edin veya değiştirin; nedeni aşırı şarj.",
          "Battery boiled: check or replace it; the cause is overcharging.")),
  yn(2, "Do the headlights get brighter with rpm and do bulbs blow?",
     yes=([0], [], "Işıklar devirle parlıyor: regülatör gerilimi sınırlamıyor.", "Lights brighten with rpm: the regulator is not limiting voltage.")),
 ],
 "starter-relay-circuit-open": [
  yn(0, "Is there a mechanical click from the starter relay when the key is turned?",
     yes=([2], [0], "Röle tıklıyor: kumanda tarafı çalışıyor; marş selenoidi veya kalın besleme kablosu.",
          "Relay clicks: the control side works; starter solenoid or the main supply cable."),
     no=([0, 1], [], "Röleden ses yok: röle, kontak termiği veya park/nötr şalteri.",
         "No click: relay, ignition switch or park/neutral switch.")),
  yn(1, "Does the starter turn when pins 30 and 87 of the relay socket are bridged?",
     yes=([0, 1], [2], "Köprüyle dönüyor: marş motoru sağlam; röle veya kumandası.",
          "Turns when bridged: the starter is fine; the relay or its control."),
     no=([2], [0], "Köprüyle de dönmüyor: marş selenoidi, marş motoru veya kalın besleme kablosu.",
         "Does not turn when bridged: starter solenoid, starter motor or main supply cable.")),
  yn(2, "Is the automatic selector fully in P (Park)?",
     no=([], [], "Vitesi tam P konumuna alın (manuelde debriyaja tam basın): güvenlik şalteri marşı keser.",
         "Put the selector fully in P (manual: press the clutch fully): the safety switch blocks cranking.")),
 ],
 "ac-refrigerant-pressure-low": [
  yn(0, "Does the compressor clutch engage when the A/C button is pressed?",
     yes=([], [1], "Kavrama kilitleniyor: kavrama devresi sağlam.", "Clutch engages: the clutch circuit is fine."),
     no=([1, 0], [], "Kavrama kilitlenmiyor: gaz düşükse sistem kavramayı keser; önce statik basıncı ölçün.",
         "Clutch does not engage: the system blocks it when gas is low; measure the static pressure first.")),
  meas(1, "What is the static refrigerant pressure on the manifold gauge (bar)?", "bar", [
     (None, 2, [2], [], "Statik basınç 2 bar altında: gaz çok az; kaçak var.", "Static pressure under 2 bar: very little gas; there is a leak."),
     (2, 4, [], [], "Statik basınç sınırda: ortam sıcaklığına göre değerlendirin.",
      "Static pressure borderline: judge it against the ambient temperature."),
     (4, None, [0, 1], [2], "Statik basınç yeterli: kavrama veya basınç sensörü öne çıkar.",
      "Static pressure adequate: the clutch or the pressure sensor comes forward."),
  ], text_keys=["statik basinc", "gaz basinci", "static pressure"]),
  yn(2, "Are there UV dye leak traces on the A/C pipes or condenser?",
     yes=([2], [], "UV iz bulundu: kaçak yeri belli; onarıp vakumlayarak dolum yapın.",
          "UV trace found: the leak is located; repair, evacuate and recharge.")),
 ],
 "immobilizer-key-transponder-missing": [
  yn(2, "Does the engine start with the spare key?",
     yes=([1], [0, 2], "Yedek anahtarla çalışıyor: asıl anahtarın çipi arızalı.", "Starts with the spare key: the main key's chip has failed."),
     no=([0, 2], [1], "Yedek anahtarla da çalışmıyor: okuyucu anten bobini veya gösterge/BCM.",
         "Does not start with the spare either: the reader coil or the cluster/BCM.")),
 ],
 "egr-valve-stuck-open": [
  yn(1, "On a cold engine at idle, does the EGR pipe get hot straight away?",
     yes=([0], [], "Rölantide boru hemen ısınıyor: valf açık kalmış, egzoz gazı geçiyor.",
          "Pipe heats at once at idle: the valve is stuck open and passing exhaust gas."),
     no=([1], [0], "Boru soğuk kalıyor: valf kapalı; pozisyon sensörü okumasını kontrol edin.",
         "Pipe stays cool: the valve is closed; check the position sensor reading.")),
  yn(2, "With the EGR removed, is the flap held open by soot?",
     yes=([0], [], "Klape kurumla açık kalmış: temizlik veya değişim.", "Flap held open by soot: clean or replace it.")),
 ],
 "evap-purge-valve-stuck-open": [
  yn(0, "Right after a full refuel, does the engine need a long crank to start?",
     yes=([0], [], "Yakıt alınca uzun marş: purge valfi açık kalmış olabilir.", "Long crank after refuelling: the purge valve may be stuck open.")),
  yn(1, "Does the valve pass vacuum even when not energised?",
     yes=([0], [], "Enerjisizken hava geçiriyor: purge valfi açık kalmış; değiştirin.", "Passes air unpowered: the purge valve is stuck open; replace it."),
     no=([], [0], "Valf kapalı tutuyor: purge valfi büyük olasılıkla sağlam.", "Valve seals: the purge valve is probably fine.")),
 ],
 "electronic-parking-brake-stuck": [
  yn(0, "Can the caliper motors be heard running when the switch is pressed?",
     tr="Düğmeye basıldığında arka kaliperlerden motor sesi geliyor mu?",
     yes=([0], [], "Motor çalışıyor ama açmıyor: kaliper mekanizması sıkışmış olabilir.",
          "Motor runs but does not release: the caliper mechanism may be seized."),
     no=([1, 0], [], "Ses yok: sigorta, konnektör, EPB modülü veya kaliper motoru.", "No sound: fuse, connector, EPB module or caliper motor.")),
 ],
 "transmission-fluid-temperature-high": [
  meas(0, "What transmission fluid temperature (TFT) is read when shifting is restricted (°C)?", "°C", [
     (None, 130, [1], [], "TFT 130 °C altında ama koruma var: sıcaklık sensörü (TFT) okumasını doğrulayın.",
      "TFT under 130 °C but protection active: verify the temperature sensor (TFT) reading."),
     (130, None, [0], [], "TFT 130 °C üstü: gerçek aşırı ısınma; yağ seviyesi ve soğutucu hattı.",
      "TFT over 130 °C: real overheating; fluid level and cooler circuit."),
  ], text_keys=["sanziman yagi sicakligi", "tft", "transmission temperature"],
     tr="Vites büyütme kısıtlandığında şanzıman yağı sıcaklığı (TFT) kaç °C?"),
  yn(1, "Are the transmission cooler lines blocked or kinked?",
     yes=([0], [], "Soğutucu hattı tıkalı veya bükülmüş: akışı düzeltin.", "Cooler line blocked or kinked: restore the flow.")),
 ],
 "engine-oil-temperature-high": [
  meas(0, "What is the oil temperature while the coolant is at 90 °C (°C)?", "°C", [
     (None, 135, [1], [], "Yağ 135 °C altında: gösterge veya yağ sıcaklık sensörü okumasını doğrulayın.",
      "Oil under 135 °C: verify the gauge or the oil temperature sensor."),
     (135, None, [0], [], "Yağ 135 °C üstü: yağ soğutucusu yetersiz.", "Oil over 135 °C: the oil cooler is not coping."),
  ], text_keys=["yag sicakligi", "oil temperature"],
     tr="Su sıcaklığı 90°C iken yağ sıcaklığı kaç °C?"),
  yn(1, "Is there sludge in the oil cooler's water or oil passages?",
     yes=([0], [], "Soğutucuda çamur: soğutucuyu temizleyin veya değiştirin.", "Sludge in the cooler: clean or replace it.")),
 ],
 "clutch-pedal-position-switch": [
  yn(0, "Has the plastic stop that the clutch switch rests on broken off?",
     yes=([0], [], "Takoz kırık: müşür pedalı algılamıyor; takozu değiştirin.", "Stop broken: the switch cannot sense the pedal; replace the stop."),
     no=([0, 1], [], "Takoz yerinde: müşürü ve marş güvenlik devresini ölçün.", "Stop in place: measure the switch and the start interlock.")),
 ],
 "hd-fuel-in-oil-dilution": [
  yn(0, "Is the dipstick level above Max with a clear smell of diesel?",
     yes=([0, 1], [], "Yağa mazot karışmış: motoru çalıştırmayın; kaynağı bulmadan yağı değiştirmek yetmez.",
          "Diesel in the oil: do not run the engine; changing the oil without finding the source is not enough.")),
  yn(1, "Have DPF post-injection regenerations been aborted often?",
     yes=([], [], "İptal edilen rejenerasyonlarda son-enjeksiyon yakıtı yağa geçer; yağı ve DPF'yi birlikte ele alın.",
          "Aborted regenerations wash post-injection fuel into the oil; handle oil and DPF together.")),
  yn(2, "Is there internal leakage at the injector return lines under the head?",
     yes=([0], [], "Enjektör geri dönüşünde iç sızıntı: o-ringleri değiştirin.", "Internal leak at the injector returns: replace the o-rings.")),
 ],
 "ev-charging-interlock-fault": [
  yn(0, "Does the mechanical lock pin engage when the charging gun is plugged in?",
     yes=([0], [1], "Kilit pimi kilitleniyor: kilit sağlam; dahili şarj cihazı (OBC) tarafı.", "Lock pin engages: the lock is fine; the on-board charger side."),
     no=([1], [], "Kilit pimi kilitlenmiyor: şarj portu kilit aktüatörü.", "Lock pin does not engage: the charge port lock actuator.")),
  yn(1, "Are the Control Pilot (CP) PWM amplitude and duty cycle nominal?",
     no=([0], [], "CP sinyali bozuk: başka bir istasyonda deneyin; sorun sürerse araç tarafı (OBC).",
         "CP signal faulty: try another station; if it persists, the vehicle side (OBC).")),
 ],
 "fuel-pump-driver-module-offline": [
  yn(0, "Is the fuel pump driver module under the rear wing or spare wheel corroded through?",
     yes=([0], [], "Modül korozyonla delinmiş: FPDM'yi değiştirin.", "Module corroded through: replace the FPDM.")),
  yn(1, "Is the PWM supply to the in-tank pump zero on the oscilloscope?",
     yes=([0], [1], "Pompaya besleme yok: FPDM veya beslemesi.", "No supply to the pump: the FPDM or its supply."),
     no=([1], [0], "Besleme var ama basınç yok: depo içi pompa.", "Supply present but no pressure: the in-tank pump.")),
 ],
 "crank-sensor-signal-missing": [
  yn(0, "Does the rpm needle move while cranking?",
     tr="Marşa basılırken devir göstergesi (RPM) kımıldıyor mu?",
     yes=([], [0], "Marşta devir okunuyor: krank sinyali var.", "Rpm reads while cranking: the crank signal is present."),
     no=([0, 1, "P0335"], [], "Marşta devir sıfır: krank sinyali yok; sensör, konnektör veya volan dişlisi (P0335).",
         "Rpm zero while cranking: no crank signal; sensor, connector or flywheel teeth (P0335).")),
  yn(1, "Does it stall when hot and restart once cooled down?",
     yes=([0], [], "Isınınca kesiliyor: krank sensörünün ısıl arızası.", "Cuts out when hot: thermal failure of the crank sensor.")),
 ],
 "ev-dc-dc-converter-failure": [
  meas(0, "What is the 12 V battery voltage in READY (V)?", "V", [
     (None, 13.8, [0], [], "READY konumunda 13.8 V altında: DC-DC 12 V aküyü şarj etmiyor.",
      "Under 13.8 V in READY: the DC-DC is not charging the 12 V battery."),
     (13.8, 14.5, [1], [0], "13.8 - 14.5 V: DC-DC çalışıyor; 12 V akünün kendisi öne çıkar.",
      "13.8 - 14.5 V: the DC-DC works; the 12 V battery itself comes forward."),
     (14.5, None, [0], [], "14.5 V üstü: DC-DC çıkışı yüksek.", "Over 14.5 V: DC-DC output is high."),
  ], text_keys=["12v aku", "12 v aku", "ready", "12v battery"],
     tr="Araç 'READY' konumundayken 12V akü kutup başlarında kaç V var?"),
  yn(1, "Are coolant flow and temperature normal in the DC-DC cooling circuit?",
     no=([0], [], "DC-DC soğutması yetersiz: pompa ve soğutma sıvısı seviyesi.", "DC-DC cooling inadequate: pump and coolant level.")),
 ],
 "marine-fuel-polishing-water-detected": [
  yn(0, "Is a separated water layer visible at the bottom of the clear bowl?",
     yes=([0], [1], "Haznede su var: boşaltın ve tank dibini kontrol edin.", "Water in the bowl: drain it and check the tank bottom."),
     no=([1], [0], "Haznede su yok ama alarm var: su tespit probu (WIF) veya kablosu.",
         "No water in the bowl but alarm on: the water-in-fuel probe or its wiring.")),
  yn(1, "Does the alarm clear after draining water from the drain cock?",
     yes=([], [1], "Alarm söndü: prob doğru çalışıyor.", "Alarm cleared: the probe works correctly."),
     no=([1], [], "Boşaltıldı ama alarm sürüyor: su tespit probu (WIF).", "Drained but alarm persists: the water-in-fuel probe.")),
 ],
}

out = {
 "schema_version": 1,
 "title": "Copilot symptom checks (answer effects for canonical symptom questions)",
 "_rule": ("Every check points at an EXISTING canonical_symptoms.json question (initial_questions[q]). "
           "An answer only moves that symptom's own subsystems (int = index into subsystems) or its own "
           "candidate_dtcs (string) up ('favor') or down ('rule_out'). Notes are fixed curated text. "
           "Validated by scripts/validate_copilot_data.py and tests/unit/test_copilot_checks.py."),
 "symptoms": {
   sid: {"checks": [dict(c, id=f"q{c['q']}") for c in checks], "provenance": [prov(sid)]}
   for sid, checks in C.items()
 },
}
with open(P, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
    fh.write("\n")
print(len(C), sum(len(v) for v in C.values()))
