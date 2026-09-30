# Signal Discovery & Evidence Engine — Uygulama Tasarım Önerisi

Hedef yol: `src/engine/discovery/` — **UYGULANDI** (tasarım belgesi olarak korunur; güncel davranış için kod esastır).
Uyumluluk: MASTER_PLAN BÖLÜM 7 & 18; mevcut FrameRouter / DbcSignalDecoder / CanFrame ile
birebir uyumlu (bkz. metodoloji_ve_gap.md §1).

## 1. Modül düzeni
```
src/engine/discovery/
├── __init__.py        # DiscoveryEngine dışa aktarımı
├── collector.py       # IdStreamCollector: FrameRouter aboneliği, ID→ring-buffer, rate
├── bitstats.py        # BitStats: flip oranı, Shannon entropisi, sınıf {CONST,INC,DEC,TOGGLE,NOISY}
├── hypotheses.py      # CounterHypothesis / ChecksumHypothesis / SignalHypothesis dataclass'ları
├── detectors/
│   ├── counter.py     # monotonic +1 mod N, bit-field sayaç, wrap analizi
│   └── checksum.py    # XOR / toplamsal / nibble / popcount / 0xFF−sum + CRC-8 katalog (crc_referansi.md)
├── segmenter.py       # korele bit grupları → alan sınırları; LE/BE çok bayt birleştirme
├── correlator.py      # çapraz-ID korelasyon, OBD PID / J1939 SPN teyidi, marka profili
├── evidence.py        # Evidence, ConfidenceReport; ağırlıklı skor + insan-okur açıklamalar
├── dbc_builder.py     # cantools Database/Message/Signal üretimi; dump_file (dbc/kcd/sym)
└── engine.py          # DiscoveryEngine: orkestrasyon, onay iş akışı, offline ASC modu
```

## 2. Temel sözleşmeler (dataclass taslakları)
```python
@dataclass(slots=True)
class Evidence:
    kind: str      # "flip_rate" | "monotonicity" | "crc_match_ratio" | "correlation" | ...
    value: float
    detail: str    # insan-okur: "Bayt0 karelerin %96'sında +1 artıyor (mod 16)"

@dataclass(slots=True)
class Hypothesis:
    htype: str                # COUNTER | CHECKSUM | SIGNAL | CONSTANT
    start_bit: int
    length: int
    params: dict              # {modulus:16, poly:0x07, covered_bytes:[0,1,2,3], byte_order:"little"}
    confidence: float         # 0..1 (kanıt ağırlıklı)
    evidence: list[Evidence]
    status: str               # candidate | ready_for_review | approved | rejected

@dataclass(slots=True)
class IdReport:
    arbitration_id: int
    frame_count: int
    rate_hz: float            # median inter-arrival'tan
    dlc: int
    entropy: dict[int, float] # bayt konumu → Shannon entropisi
    hypotheses: list[Hypothesis]
```

## 3. DiscoveryEngine arayüzü
```python
class DiscoveryEngine:
    def __init__(self, router: FrameRouter | None, *, min_frames: int = 200): ...
    def ingest_frame(self, frame: CanFrame) -> None       # canlı: router.subscribe(callback=...)
    def ingest_asc(self, path: str | Path) -> None        # offline: Golden-Traces ASC replay
    def analyze_id(self, arb_id: int) -> IdReport         # ID başına hipotez üretimi
    def analyze_all(self) -> dict[int, IdReport]
    def correlate(self, a: IdReport, b: IdReport) -> float
    def approve(self, arb_id: int, hyp_refs: list[int]) -> None   # teknisyen onay kapısı
    def build_database(self, approved: dict[int, IdReport]) -> Database  # cantools
    def export(self, db, path: str, fmt: "dbc|kcd|sym") -> None
    def evidence_report(self, arb_id: int) -> str         # insan-okur kanıt raporu
```
- **Canlı mod:** `router.subscribe(callback=engine.ingest_frame, filter_ids=None)` —
  mevcut FrameRouter abonelik modeline birebir oturur (queue taşması → drop + sayaç).
- **Offline mod:** `src/hal/replay` Vector ASC parser'ı üzerinden Golden-Traces kayıtları.

## 4. Algoritma parametreleri (ilk değerler; ayarlanabilir) — 2. tur düzeltmeleriyle
- `min_frames_per_id = 200` (CANBUSconfidenceid 20 kullanır; üründe ID rate'ine göre dinamik).
- Monotonik sayaç: delta=1 oranı ≥ 0.90 güçlü kanıt; wrap: (x+1) mod 2^k ≥ 0.95 tutarlılık.
  Mod arama seti {4, 8, 16, 256} çekirdeği korunur; Mazda (`2017_5 0x4FB counter 4|5@0+` 5-bit,
  `NEW_MSG_30 0x200 CTR 51|3@0+` 3-bit, `MSG_02/03` 2-3 bit, `CAM_LANEMAYBE` 2-bit) ve Toyota
  (`STEERING_LKA 0x2E4 COUNTER 6|6@0+` 6-bit, mod 64) kanıtlarıyla {2, 3, 5, 6, 64}-bit tuhaflık
  kenarları "aday güveniyle (kesin değil)" raporlanır (§5.2 PR-2 madde 2). Konum taraması 0..DLC-1
  bayt taraması + **bit-field maskeleri** (Hyundai 0x386 deseni; bkz. opendbc_marka_guvenlik_profilleri.md §4
  ve §5.2.3/§5.2.12).
- CRC/checksum: covered_bytes **bayt-mask taraması** (önek/son ek/alt aralık YETMEZ —
  Ford parça-bit kapsamı); match_ratio ≥ 0.99 "kesin", ≥ 0.90 "aday", ≥ 0.8 "ipucu"
  (Ford ECU hataları nedeniyle %100 beklenmez — opendbc 0x202 yorumu).
- **Polinom seti (düzeltildi — crc_referansi.md):** CRC-8 katalog modelleriyle parametrik
  gerçekleme: SMBUS(0x07/00/00), SAE-J1850(0x1D/FF/FF), AUTOSAR-8H2F(0x2F/FF/FF), HITAG(0x1D/FF/00),
  GSM-A(0x1D/00/00), MAXIM-DOW(0x31 yansımalı), CDMA2000(0x9B/FF/00), WCDMA(0x9B yansımalı),
  LTE(0x9B/00/00) + basit algoritmalar (XOR, toplamsal ×4 ID/len varyantı, nibble-topla,
  popcount, 0xFF−sum, ones-comp).
  ⚠️ Eski not "0x07 (SAE J1850)" **yanlıştı**; VW = 0x2F AUTOSAR, Chrysler = 0x1D SAE-J1850.
- **Sayaç-magic deseni (VW):** CRC match ~1/16 platoda → sayaç-başına-16-sabit XOR tablosu
  ayrı hipotez (PR-2).
- **Endianness + signedness (CAN-D TVT'21 dersleri):** segmenter çok baytlı adayları LE/BE
  **ve** işaretli/işaretsiz ayrımıyla sınar (two's-complement: asimetrik dağılım + OBD
  işaretli PID kalıpları). Signedness sınıflandırıcısı CAN-D'de %97+ F-skor kanıtlanmıştır.
- Entropi eşikleri: ~0 → CONST; düşük+artan → INC; uniform ~8 bit → NOISY (CRC adayı).
- Segmentasyon: değişim korelasyonu |ρ| ≥ 0.8 → aynı grup; komşu gruplar birleşir → sınır.
- `cycle_time`: median inter-arrival (ms) → DBC `BA_ "GenMsgCycleTime"`;
  marka-profil frekans seti {10, 25, 33, 50, 83, 100} Hz çapraz teyit.

## 5. DBC adlandırma, çıktı ve marka referans tablosu

### 5.1 Adlandırma ve üretim
- Varsayılan ad: `ID_{hex}_SIG_{start}_{len}` → onayda anlamsal ad; sayaç/CRC son ekleri
  `*_COUNTER` / `*_CHECKSUM` + `CM_` yorumuna kanıt özeti işlenir.
- J1939 (29-bit) ID'lerde PGN çözümü (PDU1: PS hariç; PDU2: PS dahil — golden README kuralı);
  bilinen SPN eşleşmesi otomatik etiket önerir (ör. EEC1 61444 / SPN 190 hız).
- Üretim yolu (bu oturumda ampirik doğrulandı): `Database(messages=[Message(frame_id=...,
  signals=[Signal(name, start, length, unit=...)])])` →
  `cantools.database.dump_file(db, path)` (dbc/kcd/sym).
  ⚠️ cantools 43.0.0'da `Database.dump()` YOKTUR — modül seviyesi `dump_file` kullanılır.

### 5.2 Marka bazlı Counter/Checksum/CRC referans tablosu (kanıtlı — 2026-09-01)

**Yöntem:** opendbc `safety/modes_*.h` (satır-satır okundu; `_kaynak/opendbc/`) +
`_kaynak/opendbc_dbc/*.dbc` ham `SG_` tanımları + cantools 43.0.0 ampirik decode.
Motorola (`@0+`) start-bit semantiği ampirik sabitlendi: `51|4@0+` → **bayt 6 düşük**
nibble, `55|4@0+` → **bayt 6 yüksek** nibble, `43|4@0+` → bayt 5 düşük nibble (gerçek
fusion DBC'si + `decode` testi: data[6]=0x95 → 51|4→5 düşük, 55|4→9 yüksek). Bu tablo
PR-2 detektör parametre uzayının ve `correlator.py` marka-profil karşılaştırmasının
kesin temelidir; her satır dosya:satır kanıtı taşır.

**Genel kurallar (tüm markalar):**
1. Konum sabit değildir: checksum/sayaç bayt 0..7'nin herhangi bir yerinde (Subaru bayt 0,
   VW bayt 0-1, Tesla bayt 0/3/7, Ford bayt 2-5, Chrysler bayt 6-7, Hyundai bit-field) →
   "son bayt" varsayımı YANLIŞ (profil §4.1).
2. **Aynı ID ≠ aynı mesaj:** Chrysler 0x220 güç CAN'ında EPS_2 tork mesajıyken (sayaç
   bayt 6 **yüksek** nibble), radar fusion veriyolunda a_1 iz mesajıdır (sayaç bayt 6
   **düşük** nibble) → kimlik veriyolu bağlamıyla çözülür (§5.2.10).
3. Sayaç bit-field'a bölünebilir: Hyundai 0x386 = bayt1 bit7..6 ∥ bayt3 bit7..6 (2+2
   split) → detektör mask-temelli tarar, bayt-hizalı varsayım yetersiz.
4. **DBC adı ≠ doğrulama:** Toyota GEAR_PACKET/STEERING_LKA ve Chrysler 514 DBC'te
   sayaç taşır ama kod `ignore_counter`; Mazda'da kod hiç hook tanımlamazken DBC'te 34
   CTR/CHKSUM adı var (§5.2.12) → isim dayatmadan davranışsal kanıt şart.
5. Checksum tek hatayla düşer, sayaç 5-hata borcu modeliyle (`wrong_counters<5`) →
   match_ratio eşikleri %100 olamaz (Ford ECU hata yorumları; §4).

**Özet tablo** (ayrıntılar §5.2.1-13'de; "—" = tanımsız/yok):

| Marka/mod | Sayaç (mod) | Checksum/CRC konumu | Algoritma | Kod kanıtı |
|---|---|---|---|---|
| Toyota klasik | — (hiç doğrulanmaz) | son bayt (0x260: b7, 0x262: b4) | topla: addr+**len**+Σdata[0..len-2] | modes_toyota.h:436-443 |
| Honda | son bayt üst nibble (4) | son bayt alt nibble | nibble-topla, ID dahil, (8−sum)&0xF | modes_honda.h |
| Hyundai klasik | mesaja göre b1/b3/b7 (4/8/16) | mesaja göre | popcount^9 (0x386) veya nibble-topla | modes_hyundai.h:41-54,95-127 |
| Hyundai CAN-FD | b1>>4 (16) veya b2 (256) | bayt 0-1 (16-bit) | CRC-16 LUT + ID 2B + xorout(len) | modes_hyundai_canfd.h:55-69 |
| Subaru | bayt1 düşük nibble (16) | **bayt 0** | topla: addr+Σdata[1..) (len YOK) | modes_subaru.h:56-71 |
| Tesla | mesaja göre 6 konum (mod 8/16) | bayt 0/3/7 | topla: addr+Σdata (chk baytı hariç) | modes_tesla.h:18-80 |
| Ford | mesaja göre (16/256) | bayt 3/4 | 0xFF−Σ (parça-bit maskeli) | modes_ford.h:24-83,272-283 |
| VW MQB/MEB | bayt1 düşük nibble (16) | bayt 0 | CRC-8/8H2F + sayaç-magic XOR | modes_volkswagen_common.h:51-79 |
| Chrysler (Pacifica/RAM) | bayt6 **yüksek** nibble (16) | son bayt | CRC-8/SAE-J1850 (0x1D/FF/FF) | modes_chrysler.h:47-49 |
| Chrysler CUSW | bayt[len-2] düşük nibble (16) | son bayt | CRC-8/SAE-J1850 | modes_chrysler_cusw.h:95-98 |
| GM/Nissan/Mazda/body | — | — | — (integrity hook yok) | modes_gm.h, modes_mazda.h, … |

Not: upstream opendbc repo'dan bu oturumda raw dosya erişimi 404 döndü (repo dizin
yapısı değişmiş olabilir); kanıt zinciri **lokal kopya + kod satırları + ampirik
cantools testi** üzerinden kesinleştirilmiştir.

#### 5.2.1 Toyota (toyota_prius_2010_pt.dbc ↔ modes_toyota.h)
- **Sayaç:** `get_counter` hook'u **tanımsız** (hooks yapısı: init/rx/tx/get_checksum/
  compute_checksum/get_quality_flag_valid — modes_toyota.h:436-443). RX girişleri:
  0x260 ve 0x1D2 yalnız checksum doğrular; 0xaa/0x226/0x224 hepsini yoksayar.
- **Checksum (son bayt):** `addr_lo+addr_hi+**len**+Σdata[0..len-2]` (profil §2.1).
  DBC teyidi: STEER_TORQUE_SENSOR 0x260 `CHECKSUM 63|8@0+` (b7); EPS_STATUS 0x262
  `39|8@0+` (5-bayt mesajda b4 = son bayt); SPEED 0xB4, PCM_CRUISE 0x1D2, LEAD_INFO
  0x2E6, POWERTRAIN 0x1C4 hepsi son bayt ✓ — DBC dizilimi kod sözleşmesiyle birebir.
- **DBC'te sayaç var ama kod yoksayar (kural 4):** GEAR_PACKET 0x127 `COUNTER 55|8@0+`
  (b6 tam bayt, mod 256), STEERING_LKA 0x2E4 `COUNTER 6|6@0+` (b0 bit 6..1, mod 64;
  kod 5-bayt TX'te checksum'ı b4'te taşır — DBC'in DLC 8'i padding).

#### 5.2.2 Honda (yalnız kod kanıtı — lokal DBC setinde Honda dosyası yok)
- Son bayt birleşik: sayaç `(data[len-1]>>4)&0x3` (mod 4) | checksum `data[len-1]&0xF`;
  algoritma: ID hex basamakları + nibble-toplamı, `(8−sum)&0xF` (profil §2.2).
  Mesajlar: 0x158/0x17C 100Hz, 0x1A6/0x296 25Hz, 0x1BE 50Hz, 0x326 10Hz.

#### 5.2.3 Hyundai klasik — DBC↔kod 5/5 birebir (hyundai_2015_ccan.dbc ↔ modes_hyundai.h)
| ID (DBC adı) | DBC tanımı | Kod okuması | Teyit |
|---|---|---|---|
| 0x260 EMS16 | `Checksum 56|4@1+` (b7 düşük) + `AliveCounter 60|2@1+` (b7 bit5-4) | chk `d7&0xF`; cnt `(d7>>4)&0x3`, mod 4 | ✓ |
| 0x386 WHL_SPD11 | `AliveCounter_LSB 14|2@1+` (b1 bit7-6) ∥ `MSB 30|2@1+` (b3 bit7-6); `Checksum_LSB 46|2@1+` (b5 bit7-6) ∥ `MSB 62|2@1+` (b7 bit7-6) | cnt `((d3>>6)<<2)+(d1>>6)`, mod 16; chk `((d7>>6)<<2)+(d5>>6)` | ✓ **bölünmüş 2+2 bit-field** (kural 3) |
| 0x394 TCS13 | `AliveCounterTCS 13|3@1+` (b1 bit5-3) + `CheckSum_TCS3 48|4@1+` (b6 düşük) | cnt `(d1>>5)&0x7`, mod 8; chk `d6&0xF` | ✓ |
| 0x421 SCC12 | `CR_VSM_Alive 56|4@1+` (b7 düşük) + `CR_VSM_ChkSum 60|4@1+` (b7 yüksek) | cnt `d7&0xF`, mod 16; chk `d7>>4` | ✓ |
| 0x4F1 CLU11 (4 bayt) | `CF_Clu_AliveCnt1 28|4@1+` (b3 yüksek); **checksum sinyali YOK** | cnt `(d3>>4)&0xF`; `ignore_checksum=true` | ✓ DBC↔kod hemfikir |
- **Algoritma (modes_hyundai.h:95-127):** 0x386 = **popcount** (sayaç/checksum bitleri
  `j<6` maskesiyle hariç) `^9 &15`; diğerleri nibble-toplamı `(16−Σ)%16`, checksum baytı
  maskeli (0x260/0x394: b7 `&0xF0`; 0x421: b7 `&0x0F`) — **sayaç nibble'ı toplama dahil**.
- Legacy: `hyundai_legacy` bayrağı 0x386/0x394'te ikisini de yoksayar (satır 41-54);
  hyundai_i30_2014.dbc'te EMS6 (0x260) aynı `Checksum/AliveCounter` düzenini taşır.

#### 5.2.4 Hyundai CAN-FD (modes_hyundai_canfd.h:55-69 + modes_hyundai_common.h:115-138)
- Sayaç: `len==8 → data[1]>>4` (mod 16); `len>8 → data[2]` (mod 256, `max_counter=0xff`).
  Checksum: **bayt 0-1, 16-bit LE** (`d0|(d1<<8)`).
- Compute: CRC-16 LUT `data[2..len)` üzerinden + ID'nin 2 baytı; xorout uzunluğa göre
  24 bayt → `0x819D`, 32 bayt → `0x9F5B`. Katalog (16.htm) eşlemesi birebir oturmuyor —
  referans nokta: XMODEM (poly 0x1021, init/xorout 0x0000, check 0x31c3, alias ACORN/
  LTE/V-41-MSB); FD xorout farkı PR-4'te parametre taramasıyla çözülecek.
- RX: 0x35/0x100/0x105 (32B, 100Hz), 0x175/0xa0/0xea (24B), SCC 0x1a0; buton 0x1cf (8B,
  checksum yoksay) / 0x1aa (16B) — `HYUNDAI_CANFD_*_RX_CHECKS` (satır 28-46).

#### 5.2.5 Subaru (kod kanıtı — lokal DBC yok; modes_subaru.h:56-71)
- Sayaç `data[1]&0xF` (mod 16); **checksum bayt 0** (katalogdaki tek "ilk bayt" örneği);
  compute `addr_lo+addr_hi+Σdata[1..len)` — **len dahil değil, sayaç baytı dahil**.
- Mesajlar: 0x40 Throttle 100Hz, 0x119 Steering_Torque / 0x13a Wheel_Speeds / 0x13c
  Brake_Status 50Hz, 0x240 CruiseControl 20Hz (satır 47-52).

#### 5.2.6 Tesla (tesla_model3_vehicle.dbc ↔ modes_tesla.h:18-80)
- Sayaç konumları (adrese göre 6 düzen): 0x2b9 `d6>>5` (mod 8); 0x488 `d2&0xF`;
  0x257/0x118/0x145/0x286/0x311 `d1&0xF`; 0x155 `d6>>4`; 0x370 `d6&0xF`.
- Checksum baytı: 0x370/0x2b9/0x155 → **b7**; 0x488 → **b3**; 0x257/0x118/0x145/0x286/
  0x311 → **b0**. Compute: `addr_lo+addr_hi+Σdata[i≠chk]` (sayaç dahil).
- DBC teyidi: DI_systemStatus 0x118 `Counter 8|4@1+` (b1 düşük) + `Checksum 0|8@1+`
  (b0) ✓; SCCM kol mesajları 0x249/0x229 aynı düzen ✓; DAS_bodyControls 0x3E9
  `Counter 52|4@1+` (b6 yüksek) + `Checksum 56|8@1+` (b7) — 0x370/0x155 ailesiyle aynı ✓.

#### 5.2.7 Ford (kod kanıtı — ford_fusion_2018_pt.dbc izlenen mesajları içermiyor)
- 0x415 BrakeSysFeatures: sayaç `(d2>>2)&0xF` — **bayt 2 bit 5-2, nibble-hizalı DEĞİL**;
  checksum b3; compute `0xFF−[d0+d1+(d2>>6)+((d2>>2)&0xF)]` — d2'nin üst 6 biti parça
  parça, d4-7 **kapsam dışı** (modes_ford.h:24-66).
- 0x91 Yaw_Data_FD1: sayaç `d5` (**tam bayt**, mod 256); checksum b4; compute
  `0xFF−[d0+d1+d2+d3+d5+(d6>>6)+((d6>>4)&0x3)]` — d6 parça-bit, d7 kapsam dışı.
- 0x202 EngVehicleSpThrottle2: **ECU hatası belgeli** — sayaç rastgele atlar veya +2
  ilerler; bazı hibritlerde ağır ivmede checksum 1-2 kare bozuk; Bronco Sport kamerası
  yalnız kalite bayrağını denetler → kod ikisini de yoksayar (modes_ford.h:274-277) —
  match_ratio eşiklerinin %100 olamayacağının birincil gerekçesi.
- 0x165/0x204/0x213: sayaç/checksum hiç yok (satır 280-282). DBC (izlenmeyen mesajlarda
  desen teyidi): Steering_Wheel_Data_CG1 0x76 `SteWhlAn_No_Cnt 47|4@0+` (b5 yüksek) +
  `SteWhlAn_No_Cs 39|8@0+` (b4); WheelData 0x216 4× tam-bayt teker sayaçları.

#### 5.2.8 VW MQB/MEB (vw_mqb.dbc ↔ modes_volkswagen_common.h:51-79)
- DBC'te 20+ mesaj aynı düzeni taşır: `CHECKSUM 0|8@1+` (b0) + `COUNTER 8|4@1+`
  (b1 düşük nibble) — ESP_05 262, TSK_06 288, LH_EPS_03 159, Motor_20 289, GRA_ACC_01
  299, HCA_01 294, ACC_02 780, ACC_06 290, ACC_07 302, ESP_02 257, LWI_01 134, Airbag_01
  64, LH_EPS_01 810, Motor_EV_01 391, … — kod: `get_counter = data[1]&0xF` ("counters
  are consistently found at LSB 8" yorumuyla), `get_checksum = data[0]` ✓ birebir.
- Compute = **CRC-8/8H2F (0x2F/0xFF/0xFF)** `data[1..len)` + **sayaç-magic XOR tablosu**
  (mesaj adresine göre 16 sabitten biri, sayaç değeriyle indeksli) + tekrar LUT:
  LH_EPS_03 0xF5×16; ESP_05 0x07×16 (sabit!); TSK_06 {C4,E2,4F,E4,F8,2F,56,81,9F,E5,
  83,44,05,3F,97,DF}; Motor_20 {E9,65,AE,6B,7B,35,E5,5F,4E,C7,86,A2,BB,DD,EB,B4};
  GRA_ACC_01 {6A,38,B4,27,22,EF,E1,BB,F8,80,84,49,C7,9E,1E,2B}.
- **İstisna:** Getriebe_11 (173) `COUNTER_DISABLED 8|4@1+` + `CHECKSUM` — CM_ yorumu:
  "J533 rate-limiting makes it look like messages are being lost" (sayaç hipotezi
  kasıtlı devre dışı; 1/16 plato sınıflandırıcısının gerçek-dünya örneği).

#### 5.2.9 Chrysler Pacifica/Jeep/RAM (kod kanıtı — modes_chrysler.h + chrysler_common.h)
- Sayaç: `chrysler_get_counter = data[6]>>4` (**bayt 6 yüksek nibble**, mod 16; satır
  47-49) — tek getter, tüm izlenen mesajlara. Checksum: **son bayt** (`data[GET_LEN-1]`).
  Compute: **CRC-8/SAE-J1850** (poly 0x1D, init 0xFF, xorout 0xFF — bit-serial; kaynak
  yorumu: illmatics "Remote Car Hacking"; crc_referansi.md §3 birebir eşleme).
- RX seti (satır 156-162): EPS_2 **0x220** 100Hz, ESP_1 0x140 50Hz, ECM_5 0x22F 50Hz,
  DAS_3 0x1F4 50Hz (tümü `max_counter=15`) + **514 her şey yoksay** (satır 159).
- RAM DT farklı ID ailesi: 0x31/0x83/0x79/0x9D/0x99 (satır 17-24, 148-154); RAM HD
  0x220/0x140/0x11C/0x22F/0x1F4/0x275/0x276/0x23A (satır 26-34, 177-183) — aynı getter.
- TX LKAS_COMMAND 0x292 (6 bayt): tork `((d0&0x7)<<8)|d1 −1024` (satır 118-121).

#### 5.2.10 ⚠️ 0x220 kimlik çelişkisi — çözülmüş (kanıt zinciri, 2026-09-01)
**Çelişki:** Kod `CHRYSLER_EPS_2 = 0x220 // EPS driver input torque` der ve sayacı
bayt 6 **yüksek** nibble'dan okur (data[6]>>4); fusion DBC'sindeki `a_1 (0x220)`
ise `COUNTER 51|4@0+` = bayt 6 **düşük** nibble taşır ve sinyalleri radar iz ailesidir.
**Çözüm — aynı ID, iki farklı veriyolunda iki farklı fiziksel mesaj:**
1. Semantik kanıt: `a_1` sinyalleri track_id / REL_SPEED / REL_ACCEL (radar) —
   EPS_2'nin data[4-5] tork alanı (offset 1024, satır 53-55) ile **çakışmaz, uyumsuz**.
2. Kod içi çapraz kanıt: aynı fusion dosyasının `c_` ailesi (0x2C2+) `COUNTER 55|4@0+`
   = bayt 6 **yüksek** nibble → kodun okuma sözleşmesi dosyada c_ ailesi üzerinden
   temsil ediliyor; a_/b_ aileleri ayrı (radar veriyolu) düzen kullanır.
3. Ampirik kanıt (cantools, gerçek dosya): data[6]=0x95 → 51|4→**5** (düşük),
   55|4→**9** (yüksek) — start-bit→nibble eşlemesi tartışmasız.
4. 514 (unknown_202) bağımsız teyit: DBC'te `COUNTER 43|4@0+` (bayt 5 düşük) ve
   **checksum sinyali yok** ↔ kod satır 159 `ignore_checksum+ignore_counter` + rx_hook
   0x202'den tekerlek hızı okur (satır 70-74) — iki artefakt aynı ağı anlatır.
**Sonuç:** Sayaç konumu marka-profilinde değişmez ama **veriyolu bağlamına göre mesaj
başına değişebilir**; detektör ID'yi `bus + addr` demetiyle izler, sayaç/checksum
konumunu tek kayıttan genellemeye (fusion veriyolu DBC'i güç CAN'ı tanımlamaz).

#### 5.2.11 Chrysler CUSW (modes_chrysler_cusw.h:95-98) — marka içi 2. sözleşme
- Sayaç: `data[GET_LEN-2] & 0xF` — **sondan bir önceki baytın düşük nibble'ı** (mod 16;
  0x1E4/0x1E8/0x1EC/0x1FE/0x2EC, hepsi `max_counter=15`); checksum yine son bayt,
  algoritma yine CRC-8/SAE-J1850. Aynı markada bile konum sözleşmesi platforma göre
  değişir → "marka imzası" eşleştirmesi kural-temelli değil kanıt-temelli yapılır.

#### 5.2.12 Mazda (mazda_2017.dbc ↔ modes_mazda.h) — eski notun düzeltilmesi
- Kod: `modes_mazda.h` mevcut ama hooks'ta **get_counter/get_checksum/compute_checksum
  tanımsız** → opendbc güvenlik katmanı Mazda'da integrity doğrulamaz.
- DBC manzarası (düzeltme: "Mazda DBC: sıfır" iddiası **yanlıştı**): ~34 CTR/CHKSUM
  adlı sinyal; genişlik 2-8 bit; **endianness aynı dosyada karışık**; konum tamamen
  heterojen — önekler: STEER 0x82 `CTR 47|4@0+` (b5 yüksek) + `CHKSUM_MAYBE 39|8@0+`
  (b4; ismin kendisi şüphe işaretli!); STEER_RATE 0x241, CAM_LKAS 0x243, CAM_LANETRACK
  0x242, ENGINE_DATA 0x202, CRZ_EVENTS 0x21F, PEDALS 0x165, CRZ_INFO 0x21B → son-bayt
  `CHKSUM 63|8@0+` ailesi; MSG_01 0x203 `CHKSUM 47|8@0+` (b5!); RADAR_363/364/365
  `CTR 59|4@0+` (b7 yüksek); RADAR_DISTANCE 0x361 `CTR 56|4@1+` (**@1+!**); NEW_MSG_19
  0x344 `CTR 48|4@1+`; 2017_5 0x4FB `counter 4|5@0+` (**5-bit**); NEW_MSG_30 0x200
  `CTR 51|3@0+` (3-bit); MSG_02/03 2-3 bit; NEW_MSG_12 0x4FA `CTR 55|4@0+` (Chrysler
  c_ deseni); CHECK_AND_TEMP 0x420 `CTR 23|8` + `counter_or_GEAR 15|8` (isim açıkça
  belirsiz); tam-bayt sayaçlar: BRAKE 0x78, EPB 0x79, CURVE_CTRS 0x217, NEW_MSG_10
  0x4FD `counter 7|8@0+`.
- **Anlamı:** Mazda kayıtları negatif kontrol DEĞİL (sinyaller gerçekten var); motor
  için "hipotez üret → insan onayla" malzemesi. Ad/niş belirsizlikleri ("MAYBE",
  "or_GEAR") ve 2/3/5-bit genişlikler, mod-2^k varsayımının gevşetilmesi gerektiğini
  gösterir (sayaç genişliği 2..8 bit taranır; §4 mod setine not).

#### 5.2.13 Gerçek negatif kontroller (motor test malzemesi)
GM (`modes_gm.h`: "TODO: do checksum and counter checks" — tüm RX ignore), Nissan,
body, Subaru preglobal: integrity hook'ları tanımsız, tüm mesajlar yoksayılır → bu
platformların kayıtlarında detektör hipotez üretim oranı ~0 beklenir ve bu kayıtlar
yanlış-pozitif ölçüm seti olarak kullanılır (profil §2.11/§4.8).

**§5.2'nin PR-2'ye doğrudan yansımaları:**
1. Sayaç konum taraması **bit-granüler** (bayt-hizalı değil): Ford b2 bit5-2, Toyota
   b0 bit6-1, Hyundai 2+2 bölünmüş bit-field, Mazda 2/3/5-bit genişlikler.
2. Mod uzayı {4,8,16,256} çekirdeğini koru; Mazda kanıtıyla {2,3,5,6,64} tuhaflık
   kenarları "aday" güveniyle raporlanır (kesin değil).
3. Endian karışımı (Mazda aynı dosyada @0+/@1+) → sayaç hipotezi her iki endianla da
   sınanır; `@0+` start-bit↔nibble dönüşümü §5.2 başındaki ampirik eşlemeyi kullanır.
4. Checksum aileleri sırası (§4 ile uyumlu): toplamsal×4 (ID/len dahil-hariç) →
   nibble → popcount → 0xFF−Σ → XOR → CRC-8 katalog (20 model) → VW sayaç-magic →
   CAN-FD 16-bit (xorout len-koşullu) → CRC RevEng offline genişletme.
5. `ignore_checksum/ignore_counter` girişleri (Toyota 0xaa/0x226/0x224, Chrysler 514,
   Ford 0x202, VW Getriebe_11, Hyundai 0x371/0x251) → "var ama doğrulanmayan" sınıfı:
   motor bu ID'lerde hipotez üretebilir; safety'nin yoksayması yanlış-pozitif değil,
   **doğrulama-dışı** anlamına gelir.

## 6. Doğrulama kapıları (tanım: ne zaman "bitti")
1. Sentetik log (sayaç+CRC bilinir) → hipotezler %100 geri kazanılır.
2. `Golden-Traces/converted/canmodes/canmodes_FORD-Fiesta-RAW-Log-Highway-1818-80km.asc`
   + aynı aracın OBD PID logu → hız/RPM keşfi ±%2 (LibreCAN Faz-2 deseni).
3. `converted/j1939/candump_kw_drive.asc` → CCVS hız keşfi Video VBox ile uyumlu
   (golden.yaml external_checklist maddeleri).
4. cantools round-trip: üretilen DBC `load_string` → `decode` (bu oturumda GEÇTİ:
   `{'COUNTER': 1, 'SPEED': 100}`).
5. `hypothesis` property testleri: sayaç wrap, CRC determinizmi, DLC 0..15 (BÖLÜM 18).
6. OpenLKA DBC'leriyle precision/recall skorlaması (alan sınırı isabeti).
7. **Gerçek-DBC regresyon seti:** §5.2 tablosundaki DBC↔kod birebir eşleşmeleri (Hyundai klasik 5/5,
   VW 20+ mesaj tek düzen, Tesla 6-konum, Toyota son-bayt ailesi) detektör **regresyon birim-testi**
   olarak dondurulur; bu kayıtlarda mod + pozisyon + algoritma hipotezlerinin %100 geri kazanımı beklenir.

## 7. Riskler ve sınırlar
- CRC varyantları sonsuzdur; ilk kapsam **Greg Cook kataloğunun 20 CRC-8 modeli** (crc_referansi.md §5
  sıralamasıyla) + basit algoritmalar (toplamsal×4 ID/len varyantı, nibble, popcount, XOR,
  0xFF−sum, ones-comp) + VW sayaç-magic deseni; genişletme: CRC RevEng (GPL → kod kopyalanmaz,
  offline parametre geri-kazanım aracı) ve opendbc marka profilleri (zaten damıtıldı:
  opendbc_marka_guvenlik_profilleri.md ve **§5.2**).
- **Doğrulama-dışı sınıf riski:** VW Getriebe_11, Ford 0x202, Toyota 0xaa/0x226/0x224, Chrysler 514
  girişlerinde safety katmanının yoksayması yanlış-pozitif değil, doğrulama-dışı anlamına gelir
  (§5.2 PR-2 madde 5); motor çıktısı bu ID'lerde "hipotez var ama safety onaylamıyor / ignore-listesinde"
  etiketiyle raporlanarak risk yönetilir.
- Korelasyon yanlış-pozitifleri (ortak sürüş trendi) → kısmi korelasyon + gecikme penceresi.
- CAN-FD 64 bayt → segmenter bit-uzayı 512 bite ölçeklenir (HCRL FD kayıtları test malzemesi).
- Motor asla kesinlik iddiası üretmez: her çıktı güven skoru + kanıt zinciriyle
  "aday"tır; DBC'ye yalnızca insan onayıyla yazılır (BÖLÜM 7: "kesinlik iddiası yerine
  çok katmanlı kanıt → teknisyen onayı").
- Performans hedefi: 5.000 msg/s @ 60 FPS UI (BÖLÜM 11) → collector O(1)/kare,
  analiz periyodik/isteğe bağlı çalışır, ring-buffer RAM sınırı (BÖLÜM 10 bütçesiyle).

## 8. Onay sonrası uygulama sırası (tahmini 6 PR)
1. PR-1 `collector.py + bitstats.py` + sentetik birim testler.
2. PR-2 `detectors/counter.py + checksum.py` (CANBUSconfidenceid kataloğu) + testler.
3. PR-3 `segmenter.py + hypotheses.py + evidence.py`.
4. PR-4 `correlator.py` + CAN-Modes Ford RAW↔OBD çapraz teyit entegrasyon testi.
5. PR-5 `dbc_builder.py + engine.py` + cantools round-trip + Golden-Traces kapıları.
6. PR-6 UI bağlama (ısı haritası + desen boyama + onay ekranı) + hypothesis property testleri.

## 9. Kaynakça
- **Greg Cook**, "Catalogue of parametrised CRC algorithms", reveng.sourceforge.io, 11.12.2024 baskısı (lokal kopyalar: `_kaynak/crc_catalog_1-15.htm`, `_16.htm`, `_17plus.htm`, `_all.htm`, `_index.htm`).
- **commaai/opendbc** (MIT Lisansı) — `safety/modes_*.h` + `opendbc_dbc/*.dbc` (lokal kanıt kopyası: `_kaynak/opendbc/`, `_kaynak/opendbc_dbc/`; satır ve mesaj kanıtları §5.2).
- **cantools 43.0.0** — ampirik decode / dump / Motorola `@0+` start-bit doğrulama testleri (§5.1, §5.2 yöntem).
- **illmatics (Charlie Miller & Chris Valasek)**, "Remote Car Hacking" (Chrysler CRC-8 / SAE-J1850 başvuru kaynağı; `modes_chrysler.h` kod yorumu).
- **CANBUSconfidenceid (T. Hoppe et al.)**, **CAN-D (TVT'21)**, **LibreCAN Faz-2** — parametre ve eşik değerleri, signedness/endianness sınıflandırma dersleri (§4, §7).
- **İç Dokümantasyon ve Araştırma Çapraz Referansları:**
  - `01_arastirma/crc_referansi.md` (Greg Cook 20 model parametre tablosu)
  - `01_arastirma/opendbc_marka_guvenlik_profilleri.md` (12 marka güvenlik analizi)
  - `01_arastirma/literatur_incelemesi.md` & `01_arastirma/obd_j1939_referansi.md`
  - `02_analiz/metodoloji_ve_gap.md`
  - MASTER_PLAN Bölüm 7 (Sinyal Keşfi), Bölüm 10 (Bellek/CPU Bütçesi), Bölüm 11 (Performans Eşikleri), Bölüm 18 (Property Testleri).
