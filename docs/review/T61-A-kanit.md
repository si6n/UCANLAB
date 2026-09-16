# T61-A — Web kaynak kesfi (scout, 0 kredi)

- **Tarih:** 2026-09-16  •  **BrightData kredisi:** **0**
- **Yontem:** urllib only (no BD); GitHub API + codeload tarballs, Marginalia, direct robots/sitemap probing, site /search/ pages
- **Kural:** SADECE kesif. Veri cekme/merge YOK. DB'ye yazilmadi.

## 0) ONEMLI — arama altyapisi degisti (registry notu BAYAT)

`scripts/scrape_registry.json` icindeki `_discovery_note` "DDG html calisiyor" diyor — **artik yanlis**.

Canli probe sonuclari (0 kredi):

| Arama | Sonuc |
|---|---|
| html.duckduckgo.com / lite.duckduckgo.com | **HTTP 202 'anomaly' (sert blok)** |
| google.com | JS-render, statik HTML'de sonuc yok |
| mojeek.com | 403 |
| yandex / brave / ecosia | BLOCK (403/429/captcha) |
| searx.xyz + 30 searx ornegi | JS `window.location.replace` challenge / bos / 429 |
| baresearch.org | Anubis bot duvari |
| **old-search.marginalia.nu** | **CALISIYOR (tek kelime sorgulari; cok kelimede zayif)** |
| **api.github.com + codeload.github.com** | **CALISIYOR (veri seti kesfi icin en iyi)** |
| **robots.txt / sitemap.xml dogrudan probe** | **CALISIYOR** |

> Sonuc: T61-A icin kesif, GitHub API + codeload tarball + dogrudan sitemap probe + site `/search/` sayfalari ile yapildi.

## 1) KABUL EDILEN YENI KAYNAKLAR

### github:foerbsnavi/OBDex — `ACCEPT (best new DTC source; CC0; 100% causes+repair, 66.6% symptoms)`
- **URL:** https://github.com/foerbsnavi/OBDex
- **Sitemap:** YOK (API/raw ile erisim)
- **Sayi (SAYILMIS):** **9533** — counted: regex '(?m)^- code:' over data/generic/*.yaml in codeload tarball
- **Erisim:** free (public repo, raw.githubusercontent + codeload, no login, no JS)
- **Lisans:** data: CC0-1.0 (LICENSE-DATA); tooling: MIT (LICENSE-CODE)
- **Kalite:** HIGH
- **Icerik:** `{"fields": ["symptoms", "common_causes", "repair", "affected_components", "estimated_cost_eur", "difficulty", "diy_possible", "related_codes", "references", "sources", "emissions_relevant", "limp_mode_possible"], "fill_rate": {"description": 100.0, "symptoms": 66.6, "common_causes": 100.0, "repair": 100.0, "affected_components": 100.0, "related_codes": 65.4, "estimated_cost_eur": 100.0}, "note": "symptom+cause+repair code-specific text, EN+DE"}`
- **Ornek 2 sayfa / veri:**
    - https://raw.githubusercontent.com/foerbsnavi/OBDex/main/data/generic/P0xxx_enriched.yaml
    - https://raw.githubusercontent.com/foerbsnavi/OBDex/main/data/generic/P2xxx_enriched.yaml
- **Karar:** ACCEPT (best new DTC source; CC0; 100% causes+repair, 66.6% symptoms)

### github:Wal33D/dtc-database — `ACCEPT (large code spine; already upstream of MechanicDB)`
- **URL:** https://github.com/Wal33D/dtc-database
- **Sitemap:** YOK (API/raw ile erisim)
- **Sayi (SAYILMIS):** **18805** — counted: SQLite 'SELECT COUNT(*) FROM dtc_definitions' = 18805; distinct codes = 12128
- **Erisim:** free (public repo, MIT, no login)
- **Lisans:** MIT
- **Kalite:** MEDIUM-HIGH (description only; no symptoms/causes)
- **Icerik:** `{"fields": ["code", "manufacturer", "description", "type", "locale", "is_generic", "source_file"], "desc_stats": {"n": 18805, "avg_len": 44.9, "max_len": 497}, "placeholder_hits": 0, "short_desc": 1408, "manufacturers": 34, "note": "broad OE coverage (18805 rows), but descriptions only"}`
- **Ornek 2 sayfa / veri:**
    - https://raw.githubusercontent.com/Wal33D/dtc-database/main/data/dtc_codes.db
- **Karar:** ACCEPT (large code spine; already upstream of MechanicDB)

### idoc.pub — `ACCEPT-CANDIDATE (new free source; needs slug-filter + 2-page fetch per doc; no sitemap -> discovery via /search/ only)`
- **URL:** https://idoc.pub/
- **Sitemap:** YOK (API/raw ile erisim)
- **Sayi (SAYILMIS):** **375** — counted: harvested 800 docs via /search/<q> over 14 queries; strict J1939-relevant 375/800 (46.9%)
- **Erisim:** free, no login, server-rendered HTML (no JS needed)
- **Lisans:** BELIRSIZ (user-uploaded PDFs; ToS: /tos)
- **Kalite:** MEDIUM (real SPN/FMI docs present; ~47% of harvest is false-positive 'SPN'/'FMI' noise)
- **Icerik:** `{"search_route": "https://idoc.pub/search/<url-encoded-query>", "doc_route": "https://idoc.pub/documents/<slug>", "strict_titles": ["cummins spn fmi codes", "spn 625 fmi 9 epa07", "spn 3482 fmi 5 diagnostic", "spn 157 circuit", "spn cpcfault codes", "3226 fmi 13", "cid mid fmi caterpillar", "error mid 128 pid 171 fmi 9", "j1939 failure mode identifier fmi codes", "error code mid 185 psid 20 fmi 0", "fmi 3 9061 brigade troops battalion operations mar 2005", "j1939 13pdf", "sae j1939 training", "j1708 j1939 plug pinout", "j1939 11pdf", "j1939 bam example", "j1939 fault codespdf", "02 datalink j1`
- **Ornek 2 sayfa / veri:**
    - https://idoc.pub/documents/spn-625-fmi-9-epa07-en5k2xveo1no
    - https://idoc.pub/documents/spn-3482-fmi-5-diagnostic-d47egkejd7n2
    - https://idoc.pub/documents/spn-157-circuit-eljm2qgj35l1
    - https://idoc.pub/documents/3226-fmi-13-vlr9910mdjlz
- **Canli dogrulama (6 kayit):**
    - http=200 SPN=36 FMI=31 — spn 625 fmi 9 epa07
    - http=200 SPN=30 FMI=26 — spn 3482 fmi 5 diagnostic
    - http=200 SPN=29 FMI=1 — spn 157 circuit
    - http=200 SPN=5 FMI=26 — 3226 fmi 13
    - http=200 SPN=0 FMI=29 — error mid 128 pid 171 fmi 9
    - http=200 SPN=0 FMI=29 — error code mid 185 psid 20 fmi 0
- **Karar:** ACCEPT-CANDIDATE (new free source; needs slug-filter + 2-page fetch per doc; no sitemap -> discovery via /search/ only)

## 2) KISMI / MEVCUT KAYNAKLAR

- **github:MechanicDB/MechanicDB-public** — PARTIAL (high-quality procedure text but sample-capped at 90 codes) | sayi=89 (counted: distinct codes in dtc_codes.csv = 89; sitemap.xml = 1397 URLs (296 code pages)) | lisans=dataset ODbL-1.0; upstream (Wal33D) MIT | https://github.com/MechanicDB/MechanicDB-public
- **github:STAS63-bit/sitrak-error-codes** — ALREADY KNOWN (in registry free_sources; not new) | sayi=7847 (repo README states 7847 SITRAK codes (CC BY 4.0); already in registry free_sources) | lisans=CC BY 4.0 (NOASSERTION tag ) | https://github.com/STAS63-bit/sitrak-error-codes

## 3) REDDEDILEN / VERI YOK

- **github:EvanL1/voltage-j1939** — REJECT for data (useful decoder library only) | decoder code only (src/database.rs 30KB); no code database | https://github.com/EvanL1/voltage-j1939
- **github:alperunlu/DTCparser** — REJECT for data | converter code (DTC<->SPN/FMI), no embedded database | https://github.com/alperunlu/DTCparser
- **huggingface:search** — VERI YOK (no usable J1939/OBD dataset on HuggingFace at discovery time) | 7 queries (J1939/OBD2 DTC/fault codes/CAN bus/...); only unrelated hits | https://huggingface.co/api/datasets?search=J1939
- **oem:paccar-family** — VERI YOK (no free code-specific content) | counted sitemap locs; 0 SPN/FMI/DTC-bearing URLs | https://www.paccar.com/ ; https://www.peterbilt.com/ ; https://www.kenworth.com/
- **oem:volvo-mack** — VERI YOK (paid diagnostics; no free codes) | counted sitemap locs; only 3+6 'diagnostic' URLs, all are subscription/tool marketing | https://www.volvotrucks.us/ ; https://www.macktrucks.com/
- **oem:freightliner-navistar-cummins-detroit** — VERI YOK (no free diagnostic procedures; quickserve login walled) | counted; 0 SPN/FMI/DTC-bearing URLs | https://www.freightliner.com/ ; https://www.navistar.com/ ; https://www.cummins.com/ ; https://www.detroitdiesel.com/
- **fleetrabbit.com** — REJECT (blog/marketing, no code-specific symptom/cause) | counted sitemap: 4381 URLs total, 62 fault/code/diagnostic URLs | https://fleetrabbit.com/
- **truckdiag.com** — REJECT (too small, no code-specific pages) | counted sitemap: only 9 URLs, 0 fault/code pages (root has SPN=1 FMI=1) | https://truckdiag.com/
- **obd-article-sites** — MOSTLY REJECT (small/explainer; hoffmannbros has 1 SPN page) | counted: hoffmannbros has 1 SPN-specific page (SPN 639 FMI 9 Cummins); others are explainer blogs | https://csselectronics.com/ ; https://hoffmannbros.com/ ; https://calamp.com/ ; https://blueinktech.com/ ; https://truckdiag.com/
- **dead-fault-sites** — VERI YOK (dead or empty) | probed: all HTTP 0 (DNS/dead) or 7-URL placeholder sitemaps with 0 SPN/DTC | https://dieselfaultcodes.com/ ; https://truckfaultcodes.com/ ; https://dieseltruckfaults.com/ ; https://obd-code.com/ ; https://j1939.com/ ; https://daviesmotorservices.com.au/ ; https://enginefaultcode.com/ ; https://dieselpartscanada.ca/
- **poisoned-external-search** — WARNING: registry `_discovery_note` (DDG html works) is now STALE — DDG is hard-blocked. Working free discovery: GitHub API + codeload tarballs, Marginalia (single-word queries only), direct sitemap/robots probing, site /search/ pages. | probed 40+ search endpoints: DDG html/lite -> HTTP 202 'anomaly' (hard block); mojeek 403; searx.xyz JS window.location.replace challenge; 30 searx instances all empty/429 | https://html.duckduckgo.com/html/ ; https://lite.duckduckgo.com/ ; https://www.mojeek.com/search ; https://searx.xyz/search (JS challenge) ; 30 searx instances

## 4) KAPSAM 3 — OEM SERVIS MANUEL ARSIVLERI (ozet)

| OEM sitesi | Sitemap (sayilan loc) | SPN/DTC URL | Erisim | Karar |
|---|---|---|---|---|
| paccar.com | /sitemap.xml = 21 | 0 | ucretsiz web, teshis abonelik | VERI YOK |
| peterbilt.com | /sitemap.xml = 217 | 0 | ucretsiz web | VERI YOK |
| kenworth.com | 404 (robots: xml-sitemap) | 0 | ucretsiz web | VERI YOK |
| volvotrucks.us | /sitemap.xml = 482 | 3 (hepsi abonelik/tool) | ucretsiz web | VERI YOK |
| macktrucks.com | /sitemap.xml = 896 | 6 (hepsi abonelik/tool) | ucretsiz web | VERI YOK |
| freightliner.com | /sitemap.xml = 1524 | 0 | ucretsiz web | VERI YOK |
| navistar.com | robots 200, sitemap 404 | 0 | ucretsiz web | VERI YOK |
| cummins.com | /sitemap.xml 403 | 0 | ucretsiz web | VERI YOK |
| quickserve.cummins.com | login duvari | - | LOGIN | ERISILEMEZ |
| detroitdiesel.com / demanddetroit.com | sitemap 404 | 0 | ucretsiz web | VERI YOK |

**Sonuc:** 5 buyuk OEM'in hicbirinde (Navistar/PACCAR/Cummins/Detroit/Volvo-Mack) ucretsiz, koda ozel teshis metni YOK — hepsi abonelik/login arkasinda. Belirsiz/uydurma yok.

## 5) KAPSAM 4 — SITEMAP KESFI (tarama edilen yeni hostlar)

| Host | Registry | Sitemap | Toplam loc | spn_like | dtc_like | Karar |
|---|---|---|---|---|---|---|
| bigrigfaults.com | scraped | /sitemap.xml | 159 | 129 | 150 | (zaten taranmis) |
| aidieseltech.com | scraped | /sitemap.xml | 2060 | 1400 | 1749 | (zaten taranmis) |
| professionaldieselrepair.com | scraped | /sitemap.xml | 927 | 452 | 447 | (zaten taranmis) |
| fleetrabbit.com | new | /sitemap.xml | 4381 | 0 | 62 | REJECT (blog) |
| freightliner.com | new | /sitemap.xml | 1524 | 0 | 0 | VERI YOK |
| macktrucks.com | new | /sitemap.xml | 896 | 0 | 6 | VERI YOK (abonelik) |
| volvotrucks.us | new | /sitemap.xml | 482 | 0 | 3 | VERI YOK (abonelik) |
| peterbilt.com | new | /sitemap.xml | 217 | 0 | 0 | VERI YOK |
| truckdiag.com | new | /sitemap.xml | 9 | 0 | 0 | REJECT (kucuk) |
| enginefaultcode.com | new | /sitemap.xml | 7 | 0 | 0 | VERI YOK |
| truckfaultcode.com | **blacklist** | — | — | — | — | DOKUNULMADI |

> Not: `manualslib.com`, `manualzz.com`, `dokumen.pub`, `vdocuments.mx` -> 403 (bot duvari). `pdfcoffee.com`, `pdf4pro.com`, `cardsmanual.com`, `servicemanualsonline.com`, `truckmanual.com` -> DNS/dead (0). **`idoc.pub` -> 200 (ACCEPT-CANDIDATE).**

## 6) CANLI DOGRULAMA (>=5 kayit birebir)

### Wal33D/dtc-database — 6 kayit (SQLite, dtc_definitions)
| code | manufacturer | description |
|---|---|---|
| P0101 | GENERIC | Mass or Volume Air Flow Sensor A Circuit Range/Performance |
| P0300 | GENERIC | Random/Multiple Cylinder Misfire Detected |
| P0420 | GENERIC | Catalyst System Efficiency Below Threshold Bank 1 |
| C0031 | GENERIC | Left Front Wheel Speed Sensor |
| U0100 | GENERIC | Lost Communication With ECM/PCM A |
| P2002 | GENERIC | Particulate Filter Efficiency Below Threshold Bank 1 |

### OBDex — 5 kayit (P0xxx_enriched.yaml, common_causes dolu)
- **P0101** symptoms=False causes=True —  common_causes: - id: maf_contamination likelihood: high label: en: MAF sensor contaminated (oil, dust) de: Luftmassenmesser verschmutzt (Öl, Staub) - id: intak
- **P0128** symptoms=False causes=True —  common_causes: - id: thermostat_stuck_open likelihood: high label: en: Thermostat stuck open de: Thermostat hängt offen - id: ect_sensor_low_reading likelihood
- **P0171** symptoms=True causes=True —  common_causes: - id: intake_vacuum_leak likelihood: high label: en: Vacuum leak in intake or PCV system de: Falschluft im Ansaugtrakt oder PCV-System - id: maf
- **P0300** symptoms=True causes=True —  common_causes: - id: spark_plugs_worn likelihood: high label: en: Worn spark plugs de: Verschlissene Zündkerzen - id: vacuum_leak_general likelihood: high labe
- **P0420** symptoms=True causes=True —  common_causes: - id: catalyst_aged likelihood: high label: en: Catalyst aged or contaminated de: Katalysator gealtert oder vergiftet - id: o2_sensor_downstream

### idoc.pub — sayfa dogrulama (SPN/FMI ciftleri gercek)
- http=200 SPN=36 FMI=31 — spn 625 fmi 9 epa07 (https://idoc.pub/documents/spn-625-fmi-9-epa07-en5k2xveo1no)
- http=200 SPN=30 FMI=26 — spn 3482 fmi 5 diagnostic (https://idoc.pub/documents/spn-3482-fmi-5-diagnostic-d47egkejd7n2)
- http=200 SPN=29 FMI=1 — spn 157 circuit (https://idoc.pub/documents/spn-157-circuit-eljm2qgj35l1)
- http=200 SPN=5 FMI=26 — 3226 fmi 13 (https://idoc.pub/documents/3226-fmi-13-vlr9910mdjlz)
- http=200 SPN=0 FMI=29 — error mid 128 pid 171 fmi 9 (https://idoc.pub/documents/error-mid-128-pid-171-fmi-9-klzooeqrmq4g)
- http=200 SPN=0 FMI=29 — error code mid 185 psid 20 fmi 0 (https://idoc.pub/documents/error-code-mid-185-psid-20-fmi-0-6nq996g722lw)

**Toplam dogrulanan:** 6 + 5 + 6 = 17 birebir kayit (>=5 sarti karsilandi).

## 7) OZET + T62 (merge) icin oneri

- **En degerli yeni kaynak:** `foerbsnavi/OBDex` (CC0) — 9.533 kod, %100 causes+repair, %66.6 symptoms. DTC (P/B/C/U) bosluklari icin birinci oncelik.
- **Genis kod omurgasi:** `Wal33D/dtc-database` (MIT) — 18.805 tanim / 12.128 distinct kod, 34 uretici.
- **Prosedur metni (kucuk):** `MechanicDB-public` — 89 kod, ODbL, adim adim talimat + maliyet (ornek sinirli).
- **Yeni web kaynagi:** `idoc.pub` — 375/800 J1939-ilgili PDF dokumani, ucretsiz, login yok; sitemap YOK, `/search/<q>` ile kesfedilir. T62'de slug-filtresi ile cekilmeli.
- **OEM arsivleri:** 5 buyuk OEM'de ucretsiz prosedur YOK (hepsi abonelik).
- **HuggingFace:** VERI YOK.
- DB'ye YAZILMADI (T62 merge gorevi).

*Kaynak: `spn_gap_hunter/output/t61a_web_discovery.json` (16 kaynak kaydi). 0 BrightData kredisi.*


---

## EK — Ham makine-okunur kanit (ozet)

Tam kanit: `spn_gap_hunter/output/t61a_web_discovery.json` (gitignore'da; bu dokuman onun insan-okunur kopyasi).

### Kaynak verdict listesi

| id | scope | count (sayilmis) | erisim | lisans | verdict |
|---|---|---|---|---|---|
| github:foerbsnavi/OBDex | 5-github-dataset | 9533 | free (public repo, raw.githubusercontent | data: CC0-1.0 (LICENSE-DATA); tool | ACCEPT (best new DTC source; CC0; 100% causes+repair, 66.6%  |
| github:Wal33D/dtc-database | 5-github-dataset | 18805 | free (public repo, MIT, no login) | MIT | ACCEPT (large code spine; already upstream of MechanicDB) |
| github:MechanicDB/MechanicDB-public | 5-github-dataset | 89 | free (public repo, but only a 90-code de | dataset ODbL-1.0; upstream (Wal33D | PARTIAL (high-quality procedure text but sample-capped at 90 |
| github:STAS63-bit/sitrak-error-codes | 5-github-dataset | 7847 | free (raw.githubusercontent) | CC BY 4.0 (NOASSERTION tag ) | ALREADY KNOWN (in registry free_sources; not new) |
| github:EvanL1/voltage-j1939 | 5-github-dataset | 0 | free (Apache-2.0) | Apache-2.0 / MIT | REJECT for data (useful decoder library only) |
| github:alperunlu/DTCparser | 5-github-dataset | 0 | free (no license file) | BELIRSIZ (no LICENSE) | REJECT for data |
| huggingface:search | 5-huggingface | 0 | free API | - | VERI YOK (no usable J1939/OBD dataset on HuggingFace at disc |
| idoc.pub | 3-manual-archive | 375 | free, no login, server-rendered HTML (no | BELIRSIZ (user-uploaded PDFs; ToS: | ACCEPT-CANDIDATE (new free source; needs slug-filter + 2-pag |
| oem:paccar-family | 3-oem-archive | 0 | free web, but diagnostics are behind pai | BELIRSIZ (proprietary) | VERI YOK (no free code-specific content) |
| oem:volvo-mack | 3-oem-archive | 0 | free web; diagnostic content is paid (Pr | BELIRSIZ (proprietary) | VERI YOK (paid diagnostics; no free codes) |
| oem:freightliner-navistar-cummins-detroit | 3-oem-archive | 0 | free web (navistar 200); quickserve.cumm | BELIRSIZ (proprietary) | VERI YOK (no free diagnostic procedures; quickserve login wa |
| fleetrabbit.com | 1/2-fault-code-site | 62 | free web (server-rendered) | BELIRSIZ | REJECT (blog/marketing, no code-specific symptom/cause) |
| truckdiag.com | 1-fault-code-site | 0 | free web | BELIRSIZ | REJECT (too small, no code-specific pages) |
| obd-article-sites | 1-fault-code-site | 1 | free web | BELIRSIZ | MOSTLY REJECT (small/explainer; hoffmannbros has 1 SPN page) |
| dead-fault-sites | 1-fault-code-site | 0 | N/A | - | VERI YOK (dead or empty) |
| poisoned-external-search | 4-tooling | 0 | BLOCKED | - | WARNING: registry `_discovery_note` (DDG html works) is now  |

### Kanit dosyalari (spn_gap_hunter/output/)

- `spn_gap_hunter/output/_t61a_dataset_counts.json` — VAR (9834 B)
- `spn_gap_hunter/output/_t61a_quality.json` — VAR (3165 B)
- `spn_gap_hunter/output/_t61a_obdex_mech.json` — VAR (8525 B)
- `spn_gap_hunter/output/_t61a_verify.json` — VAR (3535 B)
- `spn_gap_hunter/output/_t61a_idoc_quality.json` — VAR (2256 B)
- `spn_gap_hunter/output/_t61a_idoc_docs.json` — VAR (194553 B)
- `spn_gap_hunter/output/_t61a_scope34.json` — VAR (64052 B)
- `spn_gap_hunter/output/_t61a_sitemapprobe.json` — VAR (87074 B)
- `spn_gap_hunter/output/_t61a_leads.json` — VAR (6216 B)
- `spn_gap_hunter/output/_t61a_gh_hf_raw.json` — VAR (22032 B)
- `spn_gap_hunter/output/_t61a_sitemap.log` — VAR (4589 B)
- `spn_gap_hunter/output/_t61a_scope34.log` — VAR (3481 B)
- `spn_gap_hunter/output/t61a_web_discovery.json` — VAR (20727 B)

### Registry degisikligi

- `scripts/scrape_registry.json` -> `discovered` listesine **4 yeni kaynak** eklendi (OBDex, Wal33D, MechanicDB-public, idoc.pub). `_discovery_notes`'a 2 not (arama altyapisi + OEM arsivleri).
- `scraped`: 35 (degismedi) • `blacklist`: 9 (degismedi) • `discovered`: 0 -> 4.
- Kara listedeki `truckfaultcode.com`'a **DOKUNULMADI** (probe'da skip edildi).

### Dogrulama komutlari (birebir grep)

```
py -3.13 -X utf8 spn_gap_hunter/output/_t61a_verify.py        # Wal33D 6 + OBDex 5 kayit
py -3.13 -X utf8 spn_gap_hunter/output/_t61a_idoc_quality.py  # idoc.pub 6 sayfa
grep -n 'P0101\|P0300\|P0420\|C0031\|U0100\|P2002' docs/review/T61-A-kanit.md
```
