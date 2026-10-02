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

# ---- numbered families: one question shape per family, numbered per member ----
for n in range(1, 11):
    C[f"misfire-cylinder-{n}"] = [
        yn(0, f"When the cylinder {n} coil/plug is swapped with another cylinder, does the fault code move with it?",
           yes=([0], [1, 2], "Arıza bobin/bujiyle birlikte taşındı: o bobin veya buji arızalı.",
                "The fault moved with the coil/plug: that coil or plug has failed."),
           no=([1, 2], [0], "Arıza silindirde kaldı: enjektör veya kompresyon tarafı.",
               "The fault stayed in the cylinder: injector or compression side.")),
        yn(2, f"Did the cylinder {n} compression or leakdown test come out low?",
           tr=f"{n}. silindir kompresyon veya kaçak (leakdown) testi düşük mü çıktı?",
           yes=([2], [], "Kompresyon düşük: supap, segman veya conta; mekanik onarım gerekir.",
                "Compression low: valve, rings or gasket; mechanical repair needed."),
           no=([], [2], "Kompresyon yeterli.", "Compression adequate.")),
    ]
for n in range(1, 9):
    C[f"injector-circuit-cylinder-{n}"] = [
        yn(0, f"Is the cylinder {n} injector's internal resistance outside its nominal range (solenoid 12 - 16 Ohm, piezo 180-220 kOhm)?",
           tr=f"{n}. silindir enjektörünün iç direnci nominal aralığın dışında mı (selenoid 12 - 16 Ohm, piezo 180-220 kOhm)?",
           yes=([0], [], "Enjektör bobini arızalı: enjektörü değiştirin.", "The injector coil has failed: replace the injector."),
           no=([1], [0], "Enjektör sağlam: kablo demeti veya ECM sürücü devresi.",
               "The injector is fine: wiring harness or the ECM driver circuit.")),
        yn(1, "Is the harness chafed to ground or broken by vibration?",
           yes=([1], [], "Kablo demeti hasarlı: onarın ve kodu silip yeniden deneyin.",
                "Harness damaged: repair it, clear the code and retest.")),
    ]
for n in range(1, 9):
    C[f"ignition-coil-circuit-cylinder-{n}"] = [
        yn(0, f"With the ignition on, does the cylinder {n} coil connector get 12V supply and a good ground?",
           yes=([0, 1], [], "Besleme ve şase var: bobin veya tetikleme sinyali; bobini başka silindirle değiştirip deneyin.",
                "Supply and ground present: the coil or its trigger signal; swap the coil to another cylinder."),
           no=([], [0], "Besleme veya şase yok: sigorta, röle ve kablo demeti.", "No supply or ground: fuse, relay and harness.")),
        yn(1, "Are there white arc tracks or cracks on the coil body?",
           yes=([0], [], "Bobin gövdesinde ark izi: bobini değiştirin.", "Arc tracks on the coil: replace the coil.")),
    ]
for n in range(1, 7):
    C[f"transmission-gear-{n}-incorrect-ratio"] = [
        yn(0, f"When gear {n} engages, does the engine rpm flare without the vehicle accelerating?",
           yes=([0], [], f"Devir fırlıyor: {n}. vites debriyaj paketi kaydırıyor.", f"Rpm flares: the gear {n} clutch pack is slipping."),
           no=([1], [], "Kaydırma hissedilmiyor: giriş/çıkış hız sensörü okumalarını karşılaştırın.",
               "No slip felt: compare the input/output speed sensor readings.")),
    ]
C.update({
 "wheel-speed-sensor-missing-fl": [
  yn(0, "Is the magnetic encoder ring on the hub bearing cracked or rusted?",
     yes=([1], [], "Encoder halkası hasarlı: poyra/halka değişimi.", "Encoder ring damaged: replace the hub or ring.")),
  yn(1, "Does the oscilloscope show a square/sine signal when the wheel is turned by hand?",
     yes=([1], [0], "Sinyal var: sensör çalışıyor; halka boşluğu veya aralıklı kopukluk.",
          "Signal present: the sensor works; ring gap or an intermittent break."),
     no=([0], [], "Sinyal yok: sensör veya kablosu.", "No signal: the sensor or its wiring.")),
 ],
 "o2-sensor-heater-circuit-bank1-sensor1": [
  meas(0, "What is the internal resistance between the heater pins (Ohm)?", "Ohm", [
     (None, 4, [0], [], "4 Ohm altında: ısıtıcıda kısa devre.", "Under 4 Ohm: the heater is shorted."),
     (4, 15, [1], [0], "4 - 15 Ohm: ısıtıcı sağlam; besleme, sigorta ve röle.", "4 - 15 Ohm: the heater is fine; supply, fuse and relay."),
     (15, None, [0], [], "15 Ohm üstü veya sonsuz: ısıtıcı açık devre; sensörü değiştirin.",
      "Over 15 Ohm or open: the heater is open; replace the sensor."),
  ], text_keys=["isitici direnci", "heater resistance"],
     tr="Isıtıcı pinleri arasındaki iç omik direnç kaç Ohm? (genelde 4 - 15 Ohm)"),
  yn(1, "With the ignition on, does the sensor connector get 12V heater supply?",
     yes=([0], [1], "Besleme var: ısıtıcı elemanı.", "Supply present: the heater element."),
     no=([1], [], "Besleme yok: ısıtıcı sigortası ve rölesi.", "No supply: the heater fuse and relay.")),
 ],
 "rich-condition-bank1": [
  yn(1, "Is the EVAP purge valve stuck open when it should be closed?",
     yes=([1], [], "Purge valfi açık kalmış: zengin karışımın kaynağı.", "Purge valve stuck open: the source of the rich mixture.")),
  yn(2, "Are the spark plugs heavily sooted or wet with fuel?",
     yes=([0], [], "Bujiler ıslak/kurumlu: enjektör damlatıyor olabilir.", "Plugs wet or sooted: an injector may be dripping.")),
 ],
 "maf-sensor-range-performance": [
  yn(0, "Is there oil or dust on the MAF hot film?",
     yes=([0], [], "MAF filmi kirli: temizleyin veya değiştirin.", "MAF film dirty: clean or replace it.")),
  yn(2, "With the sensor unplugged (default map), does the engine run better?",
     yes=([0], [], "Fişsiz daha iyi: MAF yanlış ölçüyor.", "Better unplugged: the MAF is measuring wrong."),
     no=([1], [0], "Fişsiz de aynı: hava filtresi kutusu veya giriş kanalında kaçak/tıkanma.",
         "Same unplugged: a leak or blockage in the air box or intake duct.")),
 ],
 "map-sensor-rationality": [
  yn(0, "With the ignition on and the engine off, does the MAP sensor read atmospheric pressure (~101 kPa)?",
     yes=([1], [0], "Atmosferik basıncı doğru okuyor: sensör sağlam; vakum portu ve hortumu.",
          "Reads atmospheric correctly: the sensor is fine; the vacuum port and hose."),
     no=([0], [], "Atmosferik basıncı yanlış okuyor: MAP sensörü.", "Reads atmospheric wrong: the MAP sensor.")),
 ],
 "throttle-position-discrepancy": [
  yn(0, "Does TPS1 + TPS2 stay equal to 5.0 Volt at every pedal position?",
     yes=([0], [1], "Potansiyometre toplamı doğru: kelebek motoru veya mekanik takılma.",
          "Sum is correct: the throttle motor or mechanical sticking."),
     no=([1], [], "Toplam 5.0 Volt değil: TPS potansiyometre izi bozuk.", "Sum is not 5.0 Volt: a worn TPS track.")),
  yn(1, "Is the throttle flap sticking mechanically due to carbon in the bore?",
     yes=([0], [], "Kelebek kurumla takılıyor: temizleyip öğrenme (adaptasyon) yapın.",
          "Flap sticking with carbon: clean it and run the adaptation.")),
 ],
 "cam-crank-correlation-fault": [
  yn(0, "Is there a metallic chain rattle from the front of the engine on first start?",
     yes=([0], [], "İlk çalıştırmada zincir şakırtısı: triger zinciri uzamış veya gergi arızalı.",
          "Chain rattle on first start: the timing chain has stretched or the tensioner has failed.")),
  yn(2, "Do the timing locking tools fit fully?",
     yes=([1, 2], [0], "Zamanlama doğru: krank/eksantrik sensörü ve sinyal tekerleri.",
          "Timing correct: the crank/cam sensors and their tone wheels."),
     no=([0], [], "Kitleme aparatları oturmuyor: zamanlama kaymış; triger seti.",
         "Locking tools do not fit: timing has slipped; timing kit.")),
 ],
 "knock-sensor-circuit-fault": [
  yn(0, "Was the knock sensor bolt tightened to exactly 20 Nm with a torque wrench?",
     yes=([0], [1], "Tork doğru: sensör veya kablosu.", "Torque correct: the sensor or its wiring."),
     no=([1], [], "Tork yanlış: sensörü 20 Nm ile yeniden sıkın.", "Wrong torque: retighten the sensor to 20 Nm.")),
 ],
 "catalyst-efficiency-below-threshold-bank1": [
  yn(0, "Does the rear (post-cat) O2 voltage switch like the front sensor?",
     yes=([0], [], "Arka sensör ön sensör gibi dalgalanıyor: katalizör oksijen depolamıyor.",
          "Rear sensor switches like the front: the catalyst no longer stores oxygen."),
     no=([1], [0], "Arka sensör sabit: katalizör çalışıyor olabilir; arka O2 sensörünü doğrulayın.",
         "Rear sensor steady: the catalyst may work; verify the rear O2 sensor.")),
  yn(1, "Is there an exhaust leak or crack before the catalyst?",
     yes=([], [], "Katalizör öncesi kaçak O2 okumasını bozar: önce kaçağı giderin.",
          "A leak before the catalyst corrupts the O2 reading: fix the leak first.")),
 ],
 "evap-gross-leak-detected": [
  yn(0, "Is the fuel filler cap seal torn or the cap not fully latched?",
     yes=([0], [], "Kapak contası yırtık veya kapak açık: kapağı değiştirin/kapatın, kodu silip izleyin.",
          "Cap seal torn or cap open: replace or close it, clear the code and monitor.")),
  yn(2, "In the EVAP smoke test, does smoke escape at the filler neck?",
     yes=([0], [], "Dolum boynundan kaçak: kapak veya boyun.", "Leak at the filler neck: cap or neck."),
     no=([1, 2], [0], "Dolum boynu sağlam: kanister, hortumlar ve havalandırma valfi.",
         "Filler neck sound: canister, hoses and vent valve.")),
 ],
 "egr-flow-insufficient": [
  yn(0, "In the active test, does MAP pressure rise as expected when the EGR opens?",
     yes=([], [0, 1], "EGR açılınca basınç yükseliyor: akış var.", "Pressure rises with EGR open: there is flow."),
     no=([0, 1], [], "EGR açılınca akış yok: EGR kanalları veya soğutucu kurumla tıkalı.",
         "No flow with EGR open: EGR passages or cooler blocked with soot.")),
 ],
 "hd-vgt-actuator-stuck": [
  yn(1, "Is the nozzle ring hard to turn by hand because of soot?",
     yes=([1], [], "Nozul halkası kurumla sıkışmış: turbo temizliği veya revizyonu.",
          "Nozzle ring seized with soot: turbo cleaning or overhaul.")),
  yn(2, "Are there broken or stripped teeth in the VGT motor gearbox?",
     yes=([0], [], "Aktüatör dişlisi kırık: aktüatörü değiştirip kalibre edin.",
          "Actuator gear broken: replace and calibrate the actuator.")),
 ],
 "hd-crankcase-pressure-high": [
  yn(1, "Is the crankcase ventilation centrifugal filter blocked?",
     yes=([0], [], "Havalandırma filtresi tıkalı: filtreyi değiştirin.", "Ventilation filter blocked: replace it."),
     no=([1], [0], "Filtre açık: segman veya gömlek kaçağı öne çıkar; kompresyon testi.",
         "Filter clear: ring or liner leakage comes forward; compression test.")),
  yn(2, "When the oil filler cap is loosened, does air pressure push it up?",
     yes=([1], [], "Kapak basınçla zıplıyor: ciddi kompresyon kaçağı (blow-by).", "Cap pushed up: heavy blow-by.")),
 ],
 "air-suspension-compressor-timeout": [
  yn(0, "After an overnight park, does one corner drop to the ground?",
     yes=([1], [], "Tek köşe çöküyor: o körük veya valf bloğunda kaçak.", "One corner drops: a leak in that bag or the valve block.")),
  yn(2, "Was a leak found with soapy water at the bag folds?",
     yes=([1], [], "Sabunlu suyla kaçak bulundu: körüğü değiştirin.", "Soapy-water leak found: replace the bag.")),
 ],
})

# ---- EV / heavy duty / marine / remaining passenger complaints ----
C.update({
 "marine-lanyard-kill": [
  yn(0, "Is the kill-switch lanyard clip fully on the control lever?",
     yes=([], [0], "Kordon takılı: emniyet kilidi daha az olası.", "Lanyard fitted: the safety interlock is less likely."),
     no=([0], [], "Kordon klipsini takın: kordon takılı değilken motor çalışmaz.", "Fit the lanyard clip: the engine will not run without it.")),
  yn(1, "Is the throttle lever fully in NEUTRAL?",
     no=([1], [], "Gaz kolunu tam nötre alın: nötr güvenlik anahtarı marşı keser.",
         "Put the lever fully in neutral: the neutral safety switch blocks cranking.")),
  yn(2, "Is the bilge battery main switch on 'BOTH' or '1'?",
     yes=([], [2], "Akü şalteri açık.", "Battery switch is on."),
     no=([2], [], "Akü ana şalterini 1 veya BOTH konumuna alın.", "Turn the battery main switch to 1 or BOTH.")),
 ],
 "ev-hv-isolation-warning": [
  yn(1, "Is the manual service disconnect (MSD) fully seated?",
     no=([1], [], "Servis soketi tam oturmamış: HVIL açık kalır; yalnız yetkili HV teknisyeni oturtsun.",
         "Service disconnect not seated: the HVIL stays open; only a qualified HV technician may seat it.")),
  yn(2, "Is there moisture or corrosion at the AC or DC charging port?",
     yes=([0], [], "Şarj portunda nem/korozyon: izolasyon kaybının olası kaynağı; aracı şarja takmayın, HV yetkilisi kontrol etsin.",
          "Moisture/corrosion at the charge port: a likely source of the isolation loss; do not charge, have an HV technician check it.")),
 ],
 "ev-hvil-circuit-open": [
  yn(0, "Are the HV cover and charge flap micro-switches fully pressed?",
     yes=([], [2], "Kapak anahtarları basılı.", "Cover switches pressed."),
     no=([2], [], "Kapak mikro anahtarı açık: kapağı tam kapatın; HVIL döngüsü bu yüzden kesik.",
         "Cover micro-switch open: close the cover fully; that is why the HVIL loop is broken.")),
  yn(2, "Is the manual service lock pin locked in place?",
     yes=([], [1], "Servis pini kilitli.", "Service pin locked."),
     no=([1], [], "Servis kilit pini kilitli değil: yetkili HV teknisyeni kilitlesin.", "Service lock pin not locked: a qualified HV technician must lock it.")),
 ],
 "ev-contactor-welded": [
  yn(0, "With the vehicle OFF, is DC bus voltage still present at the inverter input?",
     yes=([0], [], "Kapalıyken barada gerilim var: kontaktör yapışmış; HV tehlikesi, dokunmayın.",
          "Bus voltage present when OFF: a contactor has welded; HV hazard, do not touch.")),
  yn(2, "Has the pre-charge resistor gone into over-temperature protection?",
     yes=([1], [], "Ön şarj direnci aşırı ısınmış: ön şarj devresi.", "Pre-charge resistor overheated: the pre-charge circuit.")),
 ],
 "ev-battery-cell-imbalance": [
  yn(2, "Is there a specific module whose voltage collapses under load?",
     yes=([0], [1], "Belirli modül yükte çöküyor: o modülde zayıf hücre.", "One module sags under load: a weak cell in that module."),
     no=([1], [0], "Modüller yükte dengeli: hücre dengeleme devresi.", "Modules even under load: the cell balancing circuit.")),
 ],
 "ev-inverter-resolver-offset": [
  yn(1, "Are the inverter's 3-phase current readings asymmetric?",
     yes=([0], [], "Faz akımları dengesiz: invertör güç modülü.", "Phase currents unbalanced: the inverter power module.")),
  yn(2, "Are the resolver sine/cosine winding resistances balanced?",
     yes=([], [1], "Resolver sargıları dengeli.", "Resolver windings balanced."),
     no=([1], [], "Resolver sargıları dengesiz: rotor konum sensörü; değişim sonrası açı kalibrasyonu gerekir.",
         "Resolver windings unbalanced: the rotor position sensor; angle calibration is needed after replacement.")),
 ],
 "ev-precharge-circuit-timeout": [
  yn(0, "During pre-charge, does the inverter bus voltage reach 95% of the battery voltage?",
     yes=([0], [1, 2], "Bara gerilimi yükseliyor: ön şarj rölesi veya kontaktör sırası.", "Bus voltage rises: the pre-charge relay or contactor sequence."),
     no=([1, 2], [], "Bara gerilimi yükselmiyor: ön şarj direnci veya giriş kapasitörleri/kısa devre.",
         "Bus voltage does not rise: the pre-charge resistor or input capacitors/short.")),
  yn(1, "Is the inverter DC bus shorted to chassis or to the negative line?",
     yes=([2], [], "DC barada kısa devre: invertör giriş tarafı.", "DC bus shorted: the inverter input side.")),
  yn(2, "Has the pre-charge resistor gone open circuit?",
     yes=([1], [], "Ön şarj direnci açık devre: direnci değiştirin.", "Pre-charge resistor open: replace it.")),
 ],
 "ev-regen-braking-restricted": [
  yn(0, "Is the battery state of charge at 100% or the battery temperature below 0°C?",
     yes=([0], [], "Batarya dolu veya soğuk: rejeneratif fren normal olarak kısıtlanır; arıza değil.",
          "Battery full or cold: regenerative braking is normally limited; not a fault.")),
  yn(2, "Are the brake pedal position sensor and stroke simulator within calibration?",
     no=([1], [], "Pedal sensörü kalibrasyon dışı: elektronik fren servosu kalibrasyonu.",
         "Pedal sensor out of calibration: calibrate the electronic brake booster.")),
 ],
 "hd-pneumatic-compressor-unloader-leak": [
  yn(0, "Does the compressor take more than 3 minutes to go from 0 to 8 bar?",
     yes=([0], [], "Dolum yavaş: kompresör başlığı veya boşaltma valfi kaçırıyor.", "Slow build-up: the compressor head or unloader leaks.")),
  yn(1, "Is there an air leak on the governor signal line?",
     yes=([1], [], "Regülatör sinyal hattında kaçak: hattı onarın.", "Leak on the governor signal line: repair it.")),
  yn(2, "Is there heavy oil at the compressor discharge?",
     yes=([0], [], "Çıkışta yağ: kompresör yağ atıyor (segman/valf).", "Oil at the discharge: the compressor passes oil (rings/valves).")),
 ],
 "hd-air-dryer-purge-valve-stuck": [
  yn(0, "With the engine running, does air escape continuously from the dryer purge port?",
     yes=([0], [], "Tahliyeden sürekli hava: kurutucu tahliye valfi açık kalmış.", "Continuous air from the purge: the dryer purge valve is stuck open.")),
  yn(1, "Does the dryer heater connector get 24V (anti-freeze)?",
     yes=([], [1], "Isıtıcı beslemesi var.", "Heater supply present."),
     no=([1], [], "Isıtıcıya besleme yok: soğukta tahliye valfi donup açık kalabilir.", "No heater supply: the purge valve can freeze open in the cold.")),
 ],
 "hd-wet-tank-water-accumulation": [
  yn(0, "When the drain valve is pulled, does milky emulsion or a lot of water come out?",
     yes=([1], [], "Tankta çok su/emülsiyon: kurutucu kartuşu doymuş; değiştirin ve tankları boşaltın.",
          "Lots of water/emulsion: the dryer cartridge is saturated; replace it and drain the tanks.")),
  yn(2, "Could the dryer cartridge be at the end of its life?",
     yes=([1], [], "Kartuş ömrü bitmiş: kartuşu değiştirin.", "Cartridge spent: replace it.")),
 ],
 "hd-retarder-overheating": [
  meas(1, "What does the retarder oil temperature sensor read (°C)?", "°C", [
     (None, 140, [0], [], "140 °C altında ama kısıtlama var: retarder sıcaklık sensörünü doğrulayın.",
      "Under 140 °C but limited: verify the retarder temperature sensor."),
     (140, None, [1], [], "140 °C üstü: gerçek aşırı ısınma; soğutma suyu oransal valfi ve ısı eşleyici.",
      "Over 140 °C: real overheating; the coolant proportional valve and heat exchanger."),
  ], text_keys=["retarder yag sicakligi", "retarder sicakligi", "retarder temperature"],
     tr="Retarder yağ sıcaklık sensörü kaç °C okuyor?"),
 ],
 "hd-spring-brake-drag": [
  yn(0, "When the park brake is released, does at least 6.5 bar release air reach the chambers?",
     yes=([0], [1], "Çözme havası yeterli ama sürtüyor: körük yayı veya mekanizması.", "Release air adequate but dragging: the chamber spring or mechanism."),
     no=([1], [], "Çözme havası yetersiz: el freni kumanda valfi veya hattı.", "Release air too low: the park brake valve or its line.")),
  yn(1, "Is the hub or rim overheating with smoke or a smell?",
     yes=([0], [], "Balata sürtüyor: aracı durdurun, poyra soğumadan sürmeyin (yangın riski).",
          "Lining dragging: stop the vehicle and do not drive before the hub cools (fire risk).")),
 ],
 "hd-trailer-plc-communication-lost": [
  yn(0, "Is the 7-pin coiled EBS cable between tractor and trailer intact?",
     yes=([1], [0], "Kablo sağlam: dorse EBS modülatörü.", "Cable intact: the trailer EBS modulator."),
     no=([0], [], "7 pinli kablo hasarlı: kabloyu veya soketi değiştirin.", "7-pin cable damaged: replace the cable or socket.")),
 ],
 "hd-scr-dosing-valve-clogged": [
  yn(0, "With the dosing valve removed from the exhaust, are white urea crystals visible?",
     yes=([0], [], "Üre kristali: dozaj enjektörü tıkalı; temizleyin veya değiştirin.", "Urea crystals: the dosing injector is blocked; clean or replace it.")),
  yn(2, "Can the DEF line pressure reach 9 bar?",
     yes=([0], [1], "Hat basıncı yeterli: enjektör tarafı.", "Line pressure adequate: the injector side."),
     no=([1], [], "Hat basıncı 9 bar'a çıkmıyor: dozaj pompası veya basınç sensörü.", "Line pressure does not reach 9 bar: the dosing pump or pressure sensor.")),
 ],
 "lean-condition-bank2": [
  yn(0, "Is the long term fuel trim above +20% only on Bank 2?",
     yes=([0, 1], [], "Yalnız Bank 2 fakir: o sıraya özgü kaçak veya enjektör (ortak MAF/yakıt basıncı değil).",
          "Only Bank 2 lean: a leak or injector on that bank (not a shared MAF/fuel pressure cause)."),
     no=([], [0, 1], "İki sıra da fakir: ortak neden (MAF, yakıt basıncı, emme kaçağı).", "Both banks lean: a shared cause (MAF, fuel pressure, intake leak).")),
  yn(1, "Is there an air leak before the Bank 2 exhaust manifold gasket?",
     yes=([0], [], "Manifold contasında kaçak: O2 sensörü sahte fakir okuyor.", "Leak at the manifold gasket: the O2 sensor reads falsely lean.")),
 ],
 "rich-condition-bank2": [
  yn(0, "Do the Bank 2 spark plugs smell of fuel or carry soot?",
     yes=([0], [], "Bujiler ıslak/kurumlu: Bank 2 enjektörü damlatıyor olabilir.", "Plugs wet/sooted: a Bank 2 injector may be dripping."),
     no=([1], [], "Bujiler temiz: Bank 2 lambda sensörü okumasını doğrulayın.", "Plugs clean: verify the Bank 2 lambda sensor reading.")),
 ],
 "fuel-pressure-high-rail": [
  yn(1, "With the regulator connector unplugged, does pressure fall to the mechanical relief value?",
     yes=([1], [0], "Regülatör mekanik olarak çalışıyor: basınç sensörü okumasını doğrulayın.",
          "The regulator works mechanically: verify the pressure sensor reading."),
     no=([0], [], "Basınç düşmüyor: basınç regülatör valfi (PCV) sıkışmış.", "Pressure does not fall: the pressure control valve (PCV) is stuck.")),
 ],
 "iat-sensor-circuit-high": [
  yn(1, "Are 5V reference and ground present at the sensor connector?",
     yes=([0], [1], "Referans ve şase var: IAT sensörü arızalı.", "Reference and ground present: the IAT sensor has failed."),
     no=([1], [], "Referans veya şase yok: kablo tesisatı.", "No reference or ground: the wiring.")),
 ],
 "vvt-solenoid-sluggish": [
  yn(0, "Is the VVT solenoid's small oil screen blocked with metal or sludge?",
     yes=([2, 0], [], "Süzgeç tıkalı: süzgeci temizleyin, yağı değiştirin; talaş varsa kaynağını arayın.",
          "Screen blocked: clean it and change the oil; if there is swarf, find its source.")),
  yn(1, "In the active test, does the cam angle respond immediately when the solenoid is triggered?",
     yes=([2], [0, 1], "Eksantrik anında tepki veriyor: solenoid ve faz dişlisi çalışıyor; yağ basıncı/kalitesi.",
          "Cam responds at once: solenoid and phaser work; oil pressure/quality."),
     no=([0, 1], [], "Tepki yok veya gecikmeli: VVT solenoidi veya faz dişlisi.", "No or slow response: the VVT solenoid or the phaser.")),
  yn(2, "Do the oil viscosity and change interval match the manufacturer's specification?",
     no=([2], [], "Yağ uygun değil: doğru viskozitede yağla değiştirin ve yeniden deneyin.", "Wrong oil: change to the correct viscosity and retest.")),
 ],
 "catalyst-efficiency-below-threshold-bank2": [
  yn(0, "Is the Bank 2 rear O2 voltage switching in step with the front sensor?",
     yes=([0], [], "Arka sensör ön sensörle senkron: Bank 2 katalizörü oksijen depolamıyor.", "Rear in step with front: the Bank 2 catalyst no longer stores oxygen."),
     no=([1], [0], "Arka sensör sabit: katalizör çalışıyor olabilir; arka lambdayı doğrulayın.", "Rear steady: the catalyst may work; verify the rear lambda.")),
 ],
 "o2-sensor-heater-circuit-bank1-sensor2": [
  yn(0, "Is the rear sensor wiring broken or melted against the exhaust?",
     yes=([0], [], "Kablo kopuk/erimiş: kabloyu onarın veya sensörü değiştirin.", "Wiring broken/melted: repair it or replace the sensor.")),
 ],
 "torque-converter-clutch-stuck-on": [
  yn(0, "When coming to a stop, does the engine stall with a jolt like a manual left in gear?",
     yes=([0, 1], [], "Dururken kilit açılmıyor: TCC solenoidi veya kilit sürgüsü.", "Lock-up does not release at a stop: the TCC solenoid or lock-up valve.")),
 ],
 "transmission-shift-solenoid-a-electrical": [
  meas(0, "What is solenoid A's internal resistance measured at the solenoid (Ohm)?", "Ohm", [
     (None, 10, [0], [], "10 Ohm altında: solenoid sargısında kısa devre.", "Under 10 Ohm: the solenoid winding is shorted."),
     (10, 25, [1], [0], "10 - 25 Ohm: solenoid sağlam; dahili kablo tesisatı veya soket.", "10 - 25 Ohm: the solenoid is fine; internal wiring or connector."),
     (25, None, [0], [], "25 Ohm üstü veya sonsuz: solenoid açık devre.", "Over 25 Ohm or open: the solenoid is open."),
  ], text_keys=["solenoid direnci", "solenoid resistance"],
     tr="Solenoid A iç omik direnci (karter sökülerek) kaç Ohm? (nominal 10 - 25 Ohm)"),
 ],
 "marine-heat-exchanger-fouling": [
  yn(1, "Could eroded zinc anodes be blocking the heat exchanger tubes?",
     yes=([0], [], "Tutya parçaları boruları tıkıyor: boru demetini temizleyin.", "Zinc debris blocks the tubes: clean the tube bundle.")),
  yn(2, "Does the temperature creep up slowly even at idle?",
     yes=([0], [], "Rölantide de yükseliyor: ısı eşleyici verimi düşmüş.", "Rises even at idle: the heat exchanger has lost efficiency.")),
 ],
 "marine-exhaust-elbow-clogged": [
  yn(0, "Is the exhaust hose very hot to the touch?",
     yes=([1], [], "Egzoz hortumu çok sıcak: su enjeksiyonu kesik; motoru durdurun, ham su akışını kontrol edin.",
          "Exhaust hose very hot: water injection has stopped; stop the engine and check raw water flow.")),
  yn(1, "With the elbow removed, are the internal water passages narrowed by rust and soot?",
     yes=([0], [], "Dirsek kanalları daralmış: karışım dirseğini değiştirin.", "Elbow passages narrowed: replace the mixing elbow.")),
 ],
 "marine-generator-hunting": [
  yn(1, "Are there air bubbles in the fuel filter or a micro leak in the fuel line?",
     yes=([1], [], "Yakıtta hava: sızıntıyı giderip sistemin havasını alın.", "Air in the fuel: fix the leak and bleed the system.")),
  yn(2, "Is there mechanical play or spring fatigue on the governor actuator lever?",
     yes=([0], [], "Governor kolunda boşluk/yay yorgunluğu: aktüatörü ayarlayın veya değiştirin.", "Play/spring fatigue on the governor: adjust or replace the actuator.")),
 ],
 "marine-shore-power-reverse-polarity": [
  yn(0, "When shore power is plugged in, does the red 'Reverse Polarity' lamp light on the main panel?",
     yes=([0], [], "Ters polarite: sahil fişini çekin, marina kablosunu ve prizi kontrol ettirin.",
          "Reverse polarity: unplug shore power and have the marina cable and socket checked.")),
  yn(1, "Is stray DC/AC voltage measured between the hull and the sea water?",
     yes=([1], [], "Kaçak gerilim: galvanik izolatör ve topraklama.", "Stray voltage: the galvanic isolator and grounding.")),
  yn(2, "Are the zincs eroding 3-4 times faster than normal?",
     yes=([1], [], "Tutyalar hızlı eriyor: galvanik kaçak; izolatörü kontrol edin.", "Zincs eroding fast: galvanic leakage; check the isolator.")),
 ],
 "marine-hydraulic-steering-air-ingress": [
  yn(1, "Is hydraulic oil leaking from the steering cylinder seals?",
     yes=([1], [], "Silindir keçeleri kaçırıyor: keçeleri değiştirin; kaçak yerinden hava girer.", "Cylinder seals leaking: replace them; air enters where oil leaks.")),
  yn(2, "Has the system been bled at the highest bleed nipple?",
     no=([1], [], "Sistemin havasını en yüksek noktadaki nipelden alın.", "Bleed the system at the highest nipple.")),
 ],
 "marine-propeller-slip": [
  yn(1, "At nominal cruising rpm, is boat speed 20% below the design value?",
     yes=([0], [], "Hız düşük: pervane veya şaft (kirlenme, eğilme, kayma).", "Speed low: propeller or shaft (fouling, bending, slip).")),
  yn(2, "Are the propeller blades fouled with barnacles or weed, or bent?",
     yes=([0], [], "Pervane kirli veya eğik: temizleyin veya tamir ettirin.", "Propeller fouled or bent: clean or repair it.")),
 ],
 "marine-rudder-angle-error": [
  yn(1, "Is the steering hydraulic fluid below the minimum line?",
     yes=([0], [], "Hidrolik sıvı eksik: kaçağı bulun, tamamlayıp havasını alın.", "Fluid low: find the leak, top up and bleed.")),
 ],
 "marine-bilge-rising-fast": [
  yn(1, "Could the bilge pump automatic float switch be stuck?",
     yes=([1], [], "Flatör takılmış olabilir: pompayı elle çalıştırın, flatörü serbest bırakın.", "Float may be stuck: run the pump manually and free the float.")),
  yn(2, "Is there an active leak at the shaft seal, seacocks or exhaust hose?",
     yes=([], [], "Aktif su girişi: kaynağı bulun; pompaların çalıştığını doğrulayın.", "Active water ingress: find the source; confirm the pumps are running.")),
 ],
 "marine-intercooler-fouling": [
  yn(0, "At steady throttle, does charge air temperature rise while boost falls?",
     yes=([0], [], "Boost düşerken hava sıcaklığı artıyor: intercooler verimi düşmüş.", "Air temperature rises as boost falls: intercooler efficiency has dropped.")),
  yn(2, "Are there salt or carbon deposits on the intercooler sea water side?",
     yes=([0], [], "Deniz suyu tarafında tortu: intercooler'ı temizleyin.", "Deposits on the sea water side: clean the intercooler.")),
 ],
 "marine-compressor-clogging": [
  yn(1, "Has the compressor inlet vacuum gauge reached the red zone?",
     yes=([0], [], "Emiş vakumu kırmızıda: hava filtresi tıkalı.", "Inlet vacuum in the red: the air filter is blocked.")),
  yn(2, "Is there oil vapour or salt crystal build-up on the air filter?",
     yes=([0], [], "Filtrede yağ/tuz: filtreyi değiştirin.", "Oil/salt on the filter: replace it.")),
 ],
 "marine-injector-nozzle-clogging": [
  yn(0, "Does the EGT difference between cylinders exceed 50°C?",
     yes=([0], [], "Silindirler arası EGT farkı büyük: soğuk silindirin enjektörü.", "Large EGT spread: the injector of the cold cylinder.")),
  yn(2, "Was carbon or hole blockage found on the injector nozzles?",
     yes=([0], [], "Memelerde karbon: enjektörleri temizleyin veya değiştirin.", "Carbon on the nozzles: clean or replace the injectors.")),
 ],
 "marine-turbo-degradation": [
  yn(1, "Is there a shift in the turbo whistle pitch or a metallic rubbing noise?",
     yes=([0], [], "Metalik sürtünme: turbo rulmanı aşınmış; turboyu çalıştırmaya devam etmeyin.",
          "Metallic rubbing: the turbo bearing is worn; do not keep running it.")),
 ],
 "marine-pump-cavitation": [
  yn(0, "Is there a gravel-like crackle or cavitation noise from the pump body?",
     yes=([0], [], "Kavitasyon sesi: emiş tarafında kısıtlama (filtre, vana, hortum) veya pompa aşınması.",
          "Cavitation noise: a restriction on the suction side (strainer, valve, hose) or pump wear.")),
 ],
})

# ---- remaining chassis / body / EV / HD complaints ----
C.update({
 "torque-converter-clutch-stuck-off": [
  yn(1, "Can the transmission control unit lock the TCC slip to 0 rpm?",
     yes=([0], [1], "Kilit 0 d/d tutabiliyor: aralıklı solenoid kumandası.", "Lock-up can hold 0 rpm: intermittent solenoid control."),
     no=([1, 0], [], "Kilit kaymayı sıfırlayamıyor: konvertör balatası aşınmış veya TCC solenoidi.",
         "Lock-up cannot reach zero slip: worn converter lining or the TCC solenoid.")),
 ],
 "transfer-case-4wd-shift-fault": [
  yn(0, "When 4H or 4L is pressed, is there a clicking sound from the actuator motor?",
     yes=([1], [], "Motor çalışıyor: konum kodlayıcı sensör veya mekanik sıkışma.", "Motor runs: the position encoder or a mechanical bind."),
     no=([0], [], "Motordan ses yok: vites motoru veya beslemesi.", "No sound from the motor: the shift motor or its supply.")),
  yn(1, "Does the transfer case position sensor report 2H, 4H and 4L correctly?",
     yes=([0], [1], "Konum sensörü doğru: vites motoru.", "Position sensor correct: the shift motor."),
     no=([1], [], "Konum yanlış bildiriliyor: konum kodlayıcı sensör.", "Position reported wrong: the position encoder.")),
 ],
 "abs-hydraulic-pump-motor-circuit": [
  yn(0, "When the ABS pump motor is triggered from the active test menu, can it be heard running?",
     yes=([], [0], "Pompa çalışıyor: motor sağlam.", "Pump runs: the motor is fine."),
     no=([0, 1], [], "Pompa çalışmıyor: pompa motoru veya rölesi/sigortası.", "Pump does not run: the pump motor or its relay/fuse.")),
  yn(1, "Do the ABS module's main supply cables carry 12V battery voltage and a good ground?",
     yes=([0], [1], "Besleme ve şase var: pompa motoru.", "Supply and ground present: the pump motor."),
     no=([1], [], "Besleme veya şase yok: ABS motor rölesi ve güç sigortası.", "No supply or ground: the ABS motor relay and main fuse.")),
 ],
 "steering-angle-sensor-uncalibrated": [
  yn(0, "With the wheel straight, is the angle between 0.0 ± 1.5 degrees?",
     yes=([], [0], "Açı doğru: kalibrasyon kaybı daha az olası.", "Angle correct: lost calibration is less likely."),
     no=([0], [], "Açı 0 değil: direksiyon açı sensörü temel ayarını yapın.", "Angle not 0: run the steering angle sensor basic setting.")),
  yn(1, "After a wheel alignment, was the SAS basic setting reset?",
     no=([0], [], "Rot ayarından sonra temel ayar yapılmamış: SAS kalibrasyonunu yapın.", "Basic setting not done after alignment: calibrate the SAS.")),
 ],
 "yaw-rate-sensor-rationality": [
  yn(1, "Is the sensor module mounted loose or at the wrong angle?",
     yes=([1], [], "Montaj gevşek veya açılı: sensörü doğru yönde sıkıca bağlayın.", "Mounting loose or angled: fit the sensor firmly and the right way round.")),
 ],
 "airbag-squib-resistance-high-driver": [
  yn(0, "When the wheel is turned lock to lock, does the airbag lamp briefly go out and come back?",
     yes=([0], [], "Lamba direksiyonla değişiyor: zemberek (clockspring) arızalı.", "Lamp changes with steering: the clockspring has failed.")),
  yn(1, "Have the horn and steering wheel buttons stopped working at the same time?",
     yes=([0], [1], "Korna ve tuşlar da çalışmıyor: zemberek arızalı; airbag modülü daha az olası.",
          "Horn and buttons dead too: the clockspring has failed; the airbag module is less likely.")),
 ],
 "hvac-blend-door-actuator-stuck": [
  yn(0, "With the ignition on, is there a periodic plastic gear clicking behind the dash for 10-15 seconds?",
     yes=([0], [], "Dişli tıkırtısı: klape motorunun dişlisi kırık.", "Gear clicking: the blend door actuator gear is broken."),
     no=([1, 0], [], "Tıkırtı yok: klape motoru beslemesi veya klima kontrol paneli.", "No clicking: the actuator supply or the climate control panel.")),
 ],
 "wheel-speed-sensor-missing-fr": [
  yn(0, "Is the front right hub bearing magnetic ring corroded or impact damaged?",
     yes=([1], [], "Manyetik halka hasarlı: poyra/halka değişimi.", "Magnetic ring damaged: replace the hub or ring."),
     no=([0], [1], "Halka sağlam: sağ ön sensör veya kablosu.", "Ring intact: the front right sensor or its wiring.")),
 ],
 "wheel-speed-sensor-missing-rl": [
  yn(0, "Has the rear left sensor harness been stretched and broken by suspension movement?",
     yes=([0], [], "Sensör kablosu kopuk: kabloyu onarın veya sensörü değiştirin.", "Sensor wire broken: repair it or replace the sensor."),
     no=([0, 1], [], "Kablo sağlam: sensör ucu ve manyetik halka.", "Wiring intact: the sensor tip and the magnetic ring.")),
 ],
 "wheel-speed-sensor-missing-rr": [
  yn(0, "Is there moisture or corrosion in the rear right hub bearing connector?",
     yes=([0], [], "Sokette nem/korozyon: soketi temizleyin veya değiştirin.", "Moisture/corrosion in the connector: clean or replace it.")),
 ],
 "tpms-sensor-battery-fl": [
  yn(0, "Does the front left valve transmitter respond to the activation tool?",
     yes=([], [0], "Verici cevap veriyor: alıcıda ID kaydını kontrol edin.", "Transmitter responds: check its ID in the receiver."),
     no=([0], [], "Verici cevap vermiyor: pil bitmiş veya sensör arızalı; sensörü değiştirip tanıtın.",
         "No response: dead battery or failed sensor; replace and register it.")),
 ],
 "tpms-sensor-battery-fr": [
  yn(0, "Is the front right tyre pressure sensor's RF transmission received?",
     yes=([], [0], "RF yayını alınıyor: alıcıda ID kaydını kontrol edin.", "RF received: check its ID in the receiver."),
     no=([0], [], "RF yayını yok: pil bitmiş veya sensör arızalı.", "No RF: dead battery or failed sensor.")),
 ],
 "tpms-sensor-battery-rl": [
  yn(0, "Is the rear left tyre pressure sensor battery critical?",
     yes=([0], [], "Pil kritik: sensörü değiştirip tanıtın.", "Battery critical: replace and register the sensor.")),
 ],
 "tpms-sensor-battery-rr": [
  yn(0, "Is the rear right sensor ID registered in the receiver module?",
     yes=([0], [], "ID kayıtlı: sensör pili veya sensör.", "ID registered: the sensor battery or the sensor."),
     no=([], [], "ID kayıtlı değil: sensörü alıcıya tanıtın (öğretme).", "ID not registered: teach the sensor to the receiver.")),
 ],
 "transmission-shift-solenoid-b-electrical": [
  yn(0, "Is solenoid B's resistance at the connector within its nominal range?",
     yes=([1], [0], "Solenoid direnci doğru: TCU sürücü katı veya tesisat.", "Solenoid resistance correct: the TCU driver or wiring."),
     no=([0], [], "Direnç aralık dışında: solenoid B veya dahili kablosu.", "Resistance out of range: solenoid B or its internal wiring.")),
 ],
 "transmission-pressure-control-solenoid-a": [
  yn(0, "Does the vehicle jolt hard when shifting from P or N into D or R?",
     yes=([0, 1], [], "Vites takarken sert vuruntu: hat basıncı yüksek; basınç kontrol solenoidi (EPC) veya regülatör.",
          "Harsh engagement: line pressure too high; the pressure control solenoid (EPC) or regulator.")),
 ],
 "ev-cell-overvoltage": [
  yn(1, "Is the passive balancing circuit of that cell module open circuit?",
     yes=([1], [], "Dengeleme devresi açık: hücre dengeleme dirençleri.", "Balancing circuit open: the cell balancing resistors."),
     no=([0], [1], "Dengeleme sağlam: hücre gerilim ölçüm devresi (CSC) okumasını doğrulayın.",
         "Balancing fine: verify the cell voltage sensing circuit (CSC) reading.")),
 ],
 "ev-battery-cooling-pump-failure": [
  yn(0, "When DC fast charging starts, does the battery coolant pump start circulating?",
     yes=([1], [0], "Pompa çalışıyor: soğutma devresi (seviye, hava, tıkanma).", "Pump runs: the cooling circuit (level, air, blockage)."),
     no=([0], [], "Pompa devreye girmiyor: batarya soğutma pompası veya beslemesi.", "Pump does not start: the battery coolant pump or its supply.")),
  yn(1, "Is the battery coolant expansion tank level normal?",
     no=([1], [], "Soğutma sıvısı eksik: kaçağı bulup tamamlayın ve havasını alın.", "Coolant low: find the leak, top up and bleed.")),
 ],
 "ev-fast-charge-ccs-comm-timeout": [
  yn(1, "Does the vehicle charge normally on AC Type 2?",
     yes=([0, 1], [], "AC şarj çalışıyor: DC iletişim tarafı (EVCC, PLC modemi).", "AC charging works: the DC communication side (EVCC, PLC modem)."),
     no=([], [], "AC şarj da yok: CP/PP pinleri ve dahili şarj cihazı; 'şarj olmuyor' semptomuna bakın.",
         "AC charging fails too: CP/PP pins and the on-board charger; see the 'will not charge' symptom.")),
  yn(2, "Is the CP pin in the charging port corroded or pushed back?",
     yes=([1], [], "CP pini hasarlı: şarj portunu onarın veya değiştirin.", "CP pin damaged: repair or replace the charge port.")),
 ],
 "hd-service-brake-treadle-valve-leak": [
  yn(0, "With the brake pedal held, does air leak continuously from the exhaust port under the cab?",
     yes=([0], [], "Basılıyken tahliyeden sürekli hava: ayak fren ventili iç kaçağı.", "Continuous air from the exhaust while held: internal leak in the treadle valve.")),
  yn(1, "With the pedal held, do circuit 1 and 2 gauges show a steady drop?",
     yes=([1], [], "Devre basıncı düşüyor: o devrede (körük, hortum, ventil) kaçak.", "Circuit pressure drops: a leak in that circuit (chamber, hose, valve).")),
 ],
 "hd-j1939-backbone-termination-resistor-missing": [
  meas(0, "With the ignition off, what resistance is measured between CAN_H and CAN_L (Ohm)?", "Ohm", [
     (None, 50, [0], [], "50 Ohm altında: hatlar arası kısa devre veya fazladan direnç.", "Under 50 Ohm: a short between the lines or an extra resistor."),
     (50, 70, [], [1], "Yaklaşık 60 Ohm: iki sonlandırma direnci yerinde.", "About 60 Ohm: both terminating resistors present."),
     (70, 140, [1], [], "Yaklaşık 120 Ohm: sonlandırma dirençlerinden biri eksik.", "About 120 Ohm: one terminating resistor is missing."),
     (140, None, [0], [], "Çok yüksek: omurga kablosu kopuk.", "Very high: the backbone cable is open."),
  ], text_keys=["can h", "can_h", "sonlandirma direnci", "termination resistance"],
     tr="Kontak kapalıyken CAN_H ve CAN_L arasında kaç Ohm ölçülüyor? (60 Ohm olmalı)"),
  yn(1, "Is the terminating resistor plug at the rear of the chassis broken off or water-logged?",
     yes=([1], [], "Sonlandırma direnci soketi hasarlı: direnci yenileyin.", "Terminator plug damaged: renew the resistor.")),
 ],
 "hd-low-coolant-level-probe-fault": [
  yn(0, "Is the low coolant warning on even though the expansion tank is full?",
     yes=([0], [1], "Tank dolu ama ikaz var: seviye probu arızalı.", "Tank full but warning on: the level probe has failed."),
     no=([1], [], "Seviye gerçekten düşük: kaçağı bulun.", "Level really is low: find the leak.")),
  yn(1, "When removed, are the probe electrodes coated with scale and rust?",
     yes=([0], [], "Prob kireçli: temizleyin veya değiştirin.", "Probe scaled: clean or replace it.")),
 ],
 "cam-position-sensor-bank1-intake": [
  yn(0, "Does cranking take longer, with the engine starting only after 3-4 seconds?",
     yes=([0, 1], [], "Uzun marş: eksantrik sinyali gelmiyor; sensör ve kablosu.", "Long crank: no cam signal; the sensor and its wiring.")),
 ],
 "o2-sensor-slow-response-bank1-sensor1": [
  yn(1, "Is the sensor tip poisoned by silicone or phosphorus from oil burning?",
     yes=([0], [], "Sensör zehirlenmiş: sensörü değiştirin ve yağ yakmanın nedenini bulun.",
          "Sensor poisoned: replace it and find why oil is burning.")),
 ],
 "evap-small-leak-detected": [
  yn(0, "Does the fuel cap seal have fine cracks?",
     yes=([0], [], "Kapak contası çatlak: kapağı değiştirin.", "Cap seal cracked: replace the cap."),
     no=([0, 1], [], "Kapak sağlam: duman testiyle hortum ve kanisteri tarayın.", "Cap sound: smoke test the hoses and canister.")),
 ],
 "brake-light-switch-rationality": [
  yn(0, "Do the brake lights stay on without the pedal pressed?",
     yes=([0], [], "Fren lambaları sürekli yanıyor: pedal anahtarı ayarı bozuk veya arızalı.", "Brake lights stay on: the pedal switch is misadjusted or faulty.")),
  yn(1, "In live data, does brake contact 2 show the opposite of contact 1?",
     yes=([0], [], "Kontaklar uyumsuz: fren pedal anahtarı.", "Contacts disagree: the brake pedal switch."),
     no=([1], [], "Kontaklar uyumlu: fren lambası devresi.", "Contacts agree: the brake lamp circuit.")),
 ],
 "ambient-air-temperature-sensor": [
  yn(0, "Could the sensor connector under the front bumper or right mirror have been left unplugged after bumper repair?",
     yes=([0], [], "Soket takılmamış olabilir: sensör soketini takın.", "Connector may be unplugged: plug the sensor in.")),
  yn(1, "Is the A/C compressor refusing to engage because outside air reads -40°C?",
     yes=([0], [], "Dış sıcaklık -40°C okunuyor: sensör açık devre; klima bu yüzden kapalı.",
          "Outside air reads -40°C: the sensor is open circuit; that is why the A/C is off.")),
 ],
})

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
