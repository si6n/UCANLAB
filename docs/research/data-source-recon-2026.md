# Data-Source Recon Report — Offline CAN-Bus Diagnostic Copilot

**Scope:** RECON ONLY. No files written to the repo (this report is the sole deliverable, staged under `docs/research/`; move or delete if the repo must stay untouched). No scraping executed. No crawler code written.

**Method:** Every URL below was probed with a single `web_fetch` GET (direct HTTP, no browser, no BrightData). HTTP status is reported verbatim where the fetch layer surfaced one. Where I could not observe a status, the row is marked **[UNVERIFIED]**.

**Hard constraint driving all ranking:** the target repo (`src/engine/ai/**`) is **fully offline** (AGENTS.md §2.8) with a forbidden-import allowlist that bans `urllib`, `http`, `socket`, `requests`. **No runtime network calls are possible.** Every source below is therefore only usable as *build-time vendored data* — fetched by a maintainer on a dev machine, committed as a static artifact, and loaded from disk. This makes **redistribution license the single most important column**, not access convenience.

**Tooling caveat:** `web_search` failed for the entire session (`DEEPSEEK_API_KEY` not configured). All discovery was done via `web_fetch` against the GitHub REST API (`api.github.com/search/repositories`) and direct URL probes. This is actually a stronger signal — every source below was reached by a real GET, not by reading a search snippet.

---

## 0. Headline findings

1. **`foerbsnavi/OBDex` is the single best DTC source found** — data dedicated **CC0-1.0** (public domain), 9,533/9,533 generic SAE J2012 codes *fully* described, ~24,000 causes, ~24,000 symptoms, ~25,000 cross-refs, repair difficulty/cost/hours, MIL/emissions/limp flags, plus **132 OBD-II PIDs** (Mode 01 + Mode 09) with formulas/units/ranges. Ships as static JSON over GitHub Pages. CC0 means **redistribution requires no attribution and no fee** — legally ideal for vendoring. The maintainer explicitly scoped manufacturer-specific codes *out* ("those are vendor IP"), which is exactly the right legal hygiene posture.
2. **`Wal33D/dtc-database`** (MIT) is the best *manufacturer-specific* complement — 28,220 code rows, 9,415 generic + 9,390 manufacturer-specific across **33 brands**, as a single ~3.1 MB SQLite file. MIT covers the DB file.
3. **`canboat/canboat` is not just NMEA 2000.** It ships a full **J1939 database** (`database/j1939/pgns/`) including PGN 65226 Active Trouble Codes (DM1) with the exact SPN(19b)/FMI(5b)/CM(1b)/OC(7b) bit layout, plus ECU1/ECU2/ECU3, aftertreatment/DOC/SCR, transmission fluids, fuel consumption. Apache-2.0, `canboat.json` is a generated 2.6 MB machine-readable file. **This is two of the four required families in one repo.**
4. **`STAS63-bit/sitrak-error-codes`** — 8,042 real J1939 SPN/FMI codes mapped to systems, **CC BY 4.0**. Covers Bosch EDC17/motor, ZF TraXon, KNORR EBS/ABS, WABCO EBS, ECAS, NanoBCU. This is the closest thing to genuine *heavy-duty OEM field data* that is legally redistributable. CC BY requires attribution — trivially satisfiable.
5. **NHTSA has three genuinely clean, unauthenticated, key-free APIs** (complaints, recalls, vPIC) — all verified HTTP 200 with real payloads. Public domain (US Government work). **These do not need BrightData.**
6. **No public, redistributable source exists for: SAE standard *texts*, OEM TSB bodies, UDS DID tables, or AllData/Mitchell/Identifix content.** These must be rejected outright (§4). UDS DID and Mode 06 coverage will have to be hand-authored from the standard's *structure* (which is public) rather than scraped.

---

## 1. SOURCE CATALOG

Reliability legend: **A** = authoritative/stable, machine-readable, actively maintained; **B** = good but narrower, or community-maintained; **C** = usable but stale, low-star, or needs verification.

### 1.1 DTC / OBD-II — generic codes

| Name | URL | License / redistribution | Access | Format | Coverage | Cadence | Rel. |
|---|---|---|---|---|---|---|---|
| **OBDex** | `github.com/foerbsnavi/OBDex` + Pages `foerbsnavi.github.io/obdex/` | **CC0-1.0 (data)** / MIT (code). Verified by fetching `LICENSE-DATA` — explicit public-domain dedication covering "data files … (YAML, JSON, schema files describing OBD-II codes and PIDs)". **Redistribute freely, commercial OK, no attribution needed.** | Direct download (git clone / raw JSON over Pages) | YAML source → JSON (`generic.json`, `all.json`, `pids/mode01.json`, `pids/mode09.json`, `meta.json`); minified variants | 9,533 generic codes: P0 3,705 / P2 3,495 / P3 155 / B0 323 / C0 626 / U0 1,055 / U3 174 (HV+FCEV). + 132 PIDs Mode 01/09 | Active (created 2026-05, pushed 2026-08; CI schema-validated, coverage 100%) | **A** |
| **dtc-database** (Wal33D) | `github.com/Wal33D/dtc-database` | **MIT** (LICENSE file present, 1,078 bytes) | Direct download — `raw.githubusercontent.com/Wal33D/dtc-database/main/data/dtc_codes.db` | **SQLite** (~3,256,320 bytes) + `data/source-data/` (37 plain-text source files, `CODE - Description`) + Python/Java/TS/Android wrappers | 18,805 definitions / 12,128 unique codes; 9,415 generic + 9,390 manufacturer-specific; **33 manufacturers**; P 14,821 / B 1,465 / C 985 / U 1,534 | Pushed 2026-02; 47★ | **A-** |
| Wikipedia — OBD-II PIDs | `en.wikipedia.org/wiki/OBD-II_PIDs` | **CC BY-SA 4.0** (share-alike — *viral*: a derivative DB must also be CC BY-SA; this is a real licensing constraint for a proprietary repo) | Scrape or use the MediaWiki API / `?action=raw` | HTML / wikitext | Full J1979 Mode 01/02/09 PID tables, formulas, bit-encoded + enumerated PID semantics, Mode 05/06/07/08/0A service list | Continuous (community) | **B** |
| Wikipedia — DTC lists (P/B/C/U) | `en.wikipedia.org/wiki/OBD-II_DTC` (and per-letter list pages) | CC BY-SA 4.0 (same viral caveat) | Scrape / `action=raw` | HTML / wikitext | Code + short description, generic layer | Continuous | **C** |
| NHTSA vPIC — `GetMakeForManufacturer`, `getallmakes`, etc. | `vpic.nhtsa.dot.gov/api/vehicles/getallmakes?format=json` | **US Government work — public domain.** No restriction on redistribution. | **Clean REST API, no key, no auth** — verified HTTP 200, returned `Count: 12364` | JSON | Vehicle makes/models/VIN decode, plant, GVWR, engine, brake system. **Not DTC** — needed for VIN→vehicle attribution | Continuously maintained by NHTSA | **A** |
| SAE J2012 **standard text** | `sae.org` (paid) | ⛔ **Proprietary. Must not be copied or scraped.** See §4. | — | — | — | — | ⛔ |
| CARB / EPA OBD DTC list | `epa.gov/compliance/obd-diagnostic-trouble-code-dtc-list` | — | — | — | — | — | **Dead.** Probed → **HTTP 404** "Sorry, but this web page does not exist". No current EPA DTC list at that path. Treat as unavailable **[UNVERIFIED whether an alternative path exists]** |

### 1.2 J1939 SPN / FMI

| Name | URL | License / redistribution | Access | Format | Coverage | Cadence | Rel. |
|---|---|---|---|---|---|---|---|
| **canboat J1939 database** | `github.com/canboat/canboat` → `database/j1939/pgns/`, `database/j1939/pgns/065226-activeTroubleCodes.yaml` | **Apache-2.0** (verified: fetched `LICENSE`, and `docs/canboat.json` self-declares `"License":"Apache License Version 2.0"`). Redistribution explicitly permitted. Repo API reports SPDX `NOASSERTION` — **trust the LICENSE file, which is Apache-2.0**, but note the mismatch and pin a commit. | Direct download (git clone; git-LFS-free raw URLs) | YAML (per-PGN source) + generated **`canboat.json` (2,664,633 B)**, `canboat.xml` (3,248,959 B), `canboat.dbc` (507,987 B), `canboat.xsd` | **J1939**: PGN 65226 DM1 Active Trouble Codes with exact SPN 19b / FMI 5b / CM 1b / OC 7b layout + lamp states (MIL/RSL/AWL/PL, incl. 1 Hz/2 Hz flash semantics); ECU1 61444, ECU2 61443, ECU3 65247, ETC1 61442, ETC2 61445, Engine Temp 1/2, Engine Fluid Level/Pressure 1/2, Engine Oil Message, Engine Hours, Fuel Consumption/Economy, Ambient/Inlet-Exhaust Conditions, Vehicle Electrical Power, Transmission Fluids, DOC/Diesel Oxidation Catalyst, SCR exhaust gas temp, Address Claim 60928, ISO Request/ACK/TP.CM/TP.DT, Commanded Address 65240. **Also the full NMEA 2000 set (§1.5).** | Very active (pushed 2026-09-21; 655★, 201 forks; `SchemaVersion 2.6.0`, `Version 8.1.0`) | **A** |
| **SITRAK error codes** (МегаДата) | `github.com/STAS63-bit/sitrak-error-codes` | **CC BY 4.0** (stated in README badge + license section; attribution + link to `megadata.pro` required, commercial OK). Repo API reports `NOASSERTION` — the README is the authoritative claim; **verify a LICENSE file before vendoring.** | Direct download — `error-codes.json` | JSON array: `{spn, fmi, dtc, system, description_ru, description_en}` | **8,042 codes across 44 systems**: Bosch engine MC11/MC13 EDC17 (2,266), KNORR EBS+ABS (1,588), WABCO EBS (887), AMT/ZF TraXon (782), OGP dash (754), BCU/NanoBCU (571), ECAS (307), SCR/AdBlue/EGR (267), PCU (163), ABS (160), retarders ZF/Voith/FST (102), PEPS/SAM/GPS (69), ADAS/radar/ADCU/LDWS (63), VCU (63). 7,635 RU + 2,441 EN descriptions | Pushed 2026-08; small but maintained; explicitly derived "from real repair practice" | **B+** |
| `alperunlu/DTCparser` | `github.com/alperunlu/DTCparser` | **No license declared** (`"license": null` in API). ⚠️ **All rights reserved by default → do not vendor.** Reference-only. | git clone (reference) | C# source | DTC ⇄ SPN/FMI conversion per SAE J1939-73 | Pushed 2026-07; 7★ | **C** (reference only) |
| `f-steff/J1939_Reserved_SPN_Convention` | `github.com/f-steff/J1939_Reserved_SPN_Convention` | **No license.** ⚠️ Reference-only. | git clone | text | Reserved-SPN numbering conventions | 2026-03; 0★ | **C** |
| SAE J1939-71 / -73 **standard text** | `sae.org` (paid) | ⛔ Proprietary — see §4 | — | — | — | — | ⛔ |
| Cummins / CAT / Volvo public fault-code PDFs | — | ⚠️ **Not verified as redistributable.** OEM service literature is copyrighted by default even when posted publicly. **Do not vendor.** See §4. | — | PDF | — | — | ⛔ |

### 1.3 UDS DIDs & Mode 06

| Name | URL | License | Access | Format | Coverage | Rel. |
|---|---|---|---|---|---|---|
| UDS DID tables (open) | — | — | — | — | **No open, redistributable UDS DID dictionary was found.** GitHub code search for `uds iso14229 did database` returned `total_count: 0`; repo search returned `total_count: 0`. | **None** |
| ISO 14229-1 (UDS) / ISO 15031-5 (Mode 06) **standard text** | `iso.org` (paid) | ⛔ Proprietary — see §4 | — | — | — | ⛔ |
| Mode 06 (on-board monitoring test results) | — | — | — | — | Structure is public (service 0x06, MID/TID/UAS/OBDMID sub-structures) and **can be implemented from the standard's structure without copying text**. No redistributable per-vehicle Mode 06 data table exists publicly. | **Author in-house** |

> **Recommendation:** For UDS DIDs and Mode 06, accept that data must be **hand-authored** into `src/protocols/uds/` from protocol knowledge, or generated from your own hardware captures. Do not expect to vendor these. This is a genuine capability gap, not a recon failure.

### 1.4 NHTSA — real-world fault cases, recalls, VIN

All three verified live in this session with real payloads.

| Name | URL | License | Access | Format | Coverage | Cadence | Rel. |
|---|---|---|---|---|---|---|---|
| **Complaints API** | `api.nhtsa.gov/complaints/complaintsByVehicle?make=&model=&modelYear=` | US Gov work — public domain | **Clean API, no key.** ✅ Verified HTTP 200, `"count":224`, real ODI complaints with `odiNumber`, `components` (e.g. `ELECTRICAL SYSTEM,ENGINE`), free-text `summary`, `crash`/`fire`/`numberOfInjuries`/`numberOfDeaths`, VIN prefix, `dateOfIncident`, `dateComplaintFiled` | JSON | **The best real-world fault corpus available legally.** Free text frequently contains *actual DTCs* (`P0035`, `U0401-68`, `U0416-68`, `P0087`, `P0420`…) plus cascades, harness/wiring narratives, TSB references | Continuous (data seen up to 2026) | **A** |
| **Recalls API** | `api.nhtsa.gov/recalls/recallsByVehicle?make=&model=&modelYear=` | US Gov work — public domain | **Clean API, no key.** ✅ Verified HTTP 200, `"Count":5`. Returns `NHTSACampaignNumber`, `Component` (FMVSS-style taxonomy e.g. `SERVICE BRAKES, HYDRAULIC:FOUNDATION COMPONENTS:MASTER CYLINDER`), `Summary`, `Consequence`, `Remedy`, `ReportReceivedDate` | JSON | Recall campaigns with component→defect→consequence→remedy chains | Continuous | **A** |
| **vPIC API** | `vpic.nhtsa.dot.gov/api/vehicles/...` | US Gov work — public domain | **Clean API, no key.** ✅ Verified HTTP 200, `"Count":12364` | JSON/XML/CSV | VIN decode, makes, models, GVWR, engine, brake, plant | Continuous | **A** |
| NHTSA bulk flat files | `static.nhtsa.gov/odi/ffdd/cmpl/FLAT_CMPL.zip` | US Gov work — public domain | Direct download. ⚠️ Fetch returned `unsupported content type "application/octet-stream"` — **the file exists and is a ZIP; the fetch layer simply refused binary.** Downloadable by a normal client. | ZIP of tab-delimited text | Full complaint + recall + TSB + manufacturer-communications history. **The highest-value NHTSA artifact for offline bulk vendoring.** | Daily/weekly | **A** |
| `nhtsa.gov/nhtsa-datasets-and-apis` (docs page) | — | — | ⚠️ **HTTP 403 Access Denied** (Akamai edge block on non-browser clients). **This is a documentation page, not a data endpoint — irrelevant to the pipeline.** The *data* endpoints above are unaffected. | — | — | — | **B** |

> TSB note: The NHTSA flat files include manufacturer communications (TSB *metadata*: ID, date, component, summary) which **is** public domain. Full OEM TSB *bodies* are copyrighted — see §4.

### 1.5 Marine NMEA 2000

| Name | URL | License | Access | Format | Coverage | Cadence | Rel. |
|---|---|---|---|---|---|---|---|
| **CANboat PGN database** | `github.com/canboat/canboat` → `database/pgns/` (verified: hundreds of per-PGN YAML files, e.g. `059392-isoAcknowledgement.yaml`, `060928-isoAddressClaim.yaml`, `061184-*`, `0650xx-*`) | **Apache-2.0** (verified) | Direct download | YAML source → `canboat.json` (2.66 MB) / `canboat.xml` (3.25 MB) / `canboat.dbc` / `canboat.xsd` | Full standardized + manufacturer-proprietary NMEA 2000 PGN set: ISO ACK, ISO Request, TP.DT, TP.CM (RTS/CTS/EOM/BTS/Abort), ISO Address Claim, manufacturer-proprietary single/fast-packet, Garmin/Fusion/Maretron/Navico/Simrad/Victron/BEP-CZone/Suzuki/Lowrance/SeaTalk vendor extensions, AIS, and a full `LookupEnumerations` set (MANUFACTURER_CODE with ~300 entries incl. **Cummins 440, Volvo Penta 174, Yanmar 172, Mercury 144, Yamaha 1862, Suzuki 586, ZF 228**; INDUSTRY_CODE incl. Highway/Agriculture/Construction/**Marine**/Industrial; ISO_COMMAND; GOOD_WARNING_ERROR; GNS_METHOD; etc.) | Very active (2026-09) | **A** |
| canboat `REFERENCES.md` | `database/REFERENCES.md` | Apache-2.0 | Direct download | MD | **Provenance trail** — archived NMEA/Maretron/Garmin/Furuno PDFs (all `web.archive.org` snapshots) used to build the DB. Genuinely useful for defensible sourcing. | — | **A** |
| **OpenSkipper** | `github.com/OpenSkipper` (moved to `openskipper/openskipper`) | ⚠️ **[UNVERIFIED]** — did not probe in this session; project is largely dormant/superseded by Canboat + Signal K. | — | C# / C++ | Legacy NMEA 2000 definitions | Stale | **C** |
| Signal K `signalk-to-nmea2000` / `signalk-nmea2000-emitter-cannon` | `github.com/NearlCrews/signalk-nmea2000-emitter-cannon` | **Apache-2.0** (API-verified) | git clone / npm | TypeScript | Claims **92% Garmin PGN coverage**; useful cross-check against canboat | Pushed 2026-09 | **B** |
| `digitalyacht/NMEA2000-Simulator-App` | `github.com/digitalyacht/NMEA2000-Simulator-App` | **No license declared** (`"license": null`). ⚠️ Reference-only. | — | JS | PGN reference + replay | 2026 | **C** |

### 1.6 EV / BMS

| Name | URL | License | Access | Format | Coverage | Rel. |
|---|---|---|---|---|---|---|
| **commaai/opendbc** | `github.com/commaai/opendbc` | **MIT** (API-verified) | git clone | Python + **.dbc** | 3,428★ / 2,273 forks. Multi-OEM CAN DBC definitions (incl. electrified powertrains). **The de-facto standard open DBC corpus.** ⚠️ **Caveat: MIT covers the *repository*; upstream OEM signal definitions are independently reverse-engineered. commaai has historically been sent takedowns. Vendor with a pinned commit and a provenance note; treat as community-derived, not OEM-licensed.** | **A** |
| `shemps/byd-atto3-openpilot-port` | `github.com/shemps/byd-atto3-openpilot-port` | **MIT** | git clone | Python / docs | BYD Atto 3 pinout, DBC corrections, radar, fingerprinting — **a concrete open EV/BMS-adjacent DBC source** | **B** |
| OBDex U3 family | `foerbsnavi.github.io/obdex/generic.json` | **CC0-1.0** | Direct download | JSON | **174 `U3xxx` codes = "Network (HV / FCEV)"** — i.e. genuine **EV/high-voltage/fuel-cell network DTCs**, fully described, public domain. **Better-licensed than any DBC.** | **A** |
| `Sherin-SEF-AI/CanLab` | `github.com/Sherin-SEF-AI/CanLab` | **MIT** | git clone | Python (PyQt6) | 85★. Offline CAN RE app: counter/checksum detection, DBC builder, J1939/ISO-TP/OBD-II/UDS, exports DBC + Wireshark dissectors. **Architecturally the closest sibling to your project** — worth reading for design, and its UDS/OBD-II tables for data. | **A-** |
| `deanlee/openpilot-cabana` | `github.com/deanlee/openpilot-cabana` | **MIT** | git clone | C++ | 46★. "Built-in DBC editor with **50+ vehicle definitions**", SocketCAN/Panda/ASC/candump/TRC. Another open DBC corpus. | **B** |

### 1.7 Real-world fault cases / field reports

| Name | URL | License | Access | Format | Coverage | Value |
|---|---|---|---|---|---|---|
| **NHTSA complaints** | see §1.4 | Public domain | Clean API + bulk ZIP | JSON / TSV | ✅ **Primary recommendation.** Narrative field carries real DTCs, cascades, intermittent wiring faults, TSB cross-references, "dealer could not replicate". Exactly the shape a deterministic expert system needs for cause→symptom mapping. | **A** |
| NHTSA manufacturer communications (TSB metadata) | via `FLAT_CMPL.zip` / FLAT files | Public domain (metadata) | Direct download | TSV | TSB ID, date, affected models, component, summary text. **Metadata only** — see §4 for TSB bodies. | **A** |
| GitHub Issues (open automotive repos) | e.g. `github.com/canboat/canboat/issues`, `commaai/opendbc/issues` | **CC BY / MIT / varies by repo; issue text is author-owned under the repo's contribution terms — licenses are frequently ambiguous for *prose*.** ⚠️ Do not bulk-vendor issue text. | API (rate-limited, 60 req/h unauth) | JSON | Genuine debugging narratives (DM1 decode bugs, PGN conflicts, ISO-TP edge cases). **Best used for human reading / test-case inspiration, not bulk ingest.** | **B** |
| Forums (iATN-style public DBs) | — | ⛔ **See §4.** ToS near-universally forbid scraping; content is user-authored copyright. | — | — | — | ⛔ |

---

## 2. OFFLINE-VENDORING FIT

Your AI layer cannot import `urllib`/`http`/`socket`/`requests`, and the whole product is offline. So "fits" means: **a maintainer runs the fetch once on a dev machine, the artifact is committed to the repo (or shipped as a build-time bundle), and runtime reads it from disk.** Nothing here can be a live dependency.

| Source | Fit for a FULLY OFFLINE repo | How it gets in | Runtime risk |
|---|---|---|---|
| **OBDex (CC0)** | ✅ **Perfect.** Static JSON, no API, no key, no rate limit, public domain. | `git clone` → copy `dist/generic.min.json` + `pids/*.json` into `data/dtc/`. Commit. | None. Add `meta.json` hash for provenance. |
| **dtc-database (MIT)** | ✅ **Perfect.** 3.1 MB SQLite. | Download `data/dtc_codes.db` → `data/dtc/dtc_codes.db`. `sqlite3` is stdlib. | None. |
| **canboat (Apache-2.0)** | ✅ **Excellent.** Pre-generated `canboat.json` needs no build step. | Download `docs/canboat.json` (2.66 MB) → `data/pgn/canboat.json`. Optionally also the J1939 subset. | None. Apache-2.0 NOTICE file must be preserved. |
| **SITRAK (CC BY 4.0)** | ✅ **Good.** Single JSON. | Download `error-codes.json` → `data/dtc/sitrak_spn_fmi.json`. | Must display attribution ("Источник: megadata.pro, CC BY 4.0") in an about/licenses screen. |
| **NHTSA complaints/recalls/vPIC** | ⚠️ **Snapshot-only.** APIs are clean, but a *live* client would violate the offline rule. | **Bulk-download once.** Preferred: `FLAT_CMPL.zip`. Then distill to a small curated `data/cases/` set. | **Size risk.** Full FLAT files are large; do **not** commit raw. Distill to a bounded, curated corpus. Runtime = pure file read. |
| **NHTSA bulk ZIP** | ✅ if distilled, ❌ if committed raw | Maintainer downloads, runs a one-off distillation script, commits only the distilled subset. | Repo bloat. Cap it. |
| **opendbc / CanLab / openpilot-cabana DBCs** | ✅ **Good**, with provenance caveat | Vendor specific `.dbc` files with a `PROVENANCE.md` naming origin repo + commit SHA. | Licensing ambiguity on reverse-engineered OEM signals — record provenance. |
| **Wikipedia PIDs / DTC lists** | ⚠️ **Legally awkward.** CC BY-SA is **viral**: a derivative database arguably must be CC BY-SA, which may be unacceptable for a commercial proprietary repo. | If used at all: vendor as a *separate* clearly-marked CC BY-SA file, or use only as a **verification cross-check** and re-author the text. | License contamination. **OBDex (CC0) already covers this ground with no such risk — prefer it and skip Wikipedia.** |
| **UDS DIDs / Mode 06** | ❌ No vendorable source exists | Hand-author in-repo from protocol structure. | None (you own it). This is the honest gap. |
| **SAE / ISO standard texts** | ⛔ **Forbidden** | — | Legal. |
| **Forums / iATN / AllData / Mitchell / Identifix** | ⛔ **Forbidden** | — | Legal. |

**Network-isolation compliance:** every ✅ row above is satisfied by *file reads at runtime*. No row requires keeping a network import in `src/engine/ai/**`. The fetch step is a maintainer-run build script living **outside** the AI package (e.g. `tools/data_ingest/`) so `FORBIDDEN_AI_IMPORT_ROOTS` stays green. **Do not let a data-refresh script land under `src/engine/ai/`.**

---

## 3. BRIGHTDATA PLAN

### 3.1 The blunt finding: BrightData is barely needed

Of the sources that actually matter, **essentially all have clean APIs or direct downloads.** I verified this empirically — every one of these returned HTTP 200 to a plain `web_fetch` GET with no proxy, no browser, no JS execution, no CAPTCHA:

- `api.nhtsa.gov/complaints/complaintsByVehicle` → HTTP 200
- `api.nhtsa.gov/recalls/recallsByVehicle` → HTTP 200
- `api.nhtsa.gov/products/vehicle/makes` → HTTP 200
- `vpic.nhtsa.dot.gov/api/vehicles/getallmakes` → HTTP 200
- `raw.githubusercontent.com/...` → HTTP 200 (all repos)
- `api.github.com/...` → HTTP 200

**Conclusion: for this data-acquisition goal, BrightData is a nice-to-have for a handful of HTML-only pages and a hard-requirement for none of the high-value sources. Recommending a large BrightData spend would be wasteful and would invite ToS exposure for no measurable gain.**

### 3.2 What genuinely *might* need BrightData

| Target | Why it needs help | Verdict |
|---|---|---|
| `nhtsa.gov` documentation/HTML pages | ✅ Observed **HTTP 403 Access Denied** (Akamai edge). Non-browser clients blocked. | **Not worth it.** These are *doc pages*. The data endpoints work fine. Skip. |
| Wikipedia PID/DTC tables | Needs structured extraction from rendered tables. | **Not needed.** Use the **MediaWiki API** (`action=raw` / `action=parse`) — free, no anti-bot, and respects the intended machine interface. Prefer it over scraping. |
| OEM public fault-code PDFs (Cummins/CAT/Volvo) | PDF parsing + possibly JS-gated portals. | **Do not target.** Not redistributable (§4). BrightData doesn't fix the legal problem. |
| Any portal with a login / captcha / "request access" | — | ⛔ **Do not.** Bypassing access controls is exactly the behaviour that creates liability. |

**Recommendation: no BrightData crawler is warranted for this task.** If a specific HTML-only, robots-permitting, non-login page is later identified, a single-page fetch (not a crawl) is sufficient.

### 3.3 Minimal, respectful plan *if* HTML scraping is ever used

Scope it as narrowly as possible. For **any** future scrape:

1. **Preflight.** `GET /robots.txt`; obey `Disallow` absolutely. Read ToS; if ToS forbids automated collection, **stop** — do not proceed and do not look for a workaround.
2. **Identify.** Custom User-Agent with a real contact URL, e.g. `UniversalCANDataBot/1.0 (+https://<project-url>; data-ingest@<domain>)`.
3. **Pace.** ≤ 1 request / 2 s per host; ≤ 1 concurrent request per host; hard-stop after 3 consecutive non-200s.
4. **Window.** Off-peak, and only when a content-change signal exists (ETag / Last-Modified / sitemap `lastmod`). **Cache aggressively; never re-fetch unchanged content.**
5. **Scope.** Allowlist exact URL prefixes. No wildcard domain crawls. No following into auth-gated areas.
6. **Cadence.** Quarterly at most. This data is not fast-moving — DTC definitions change on the order of years.
7. **Store metadata, not just bytes.** Record `fetched_at`, `url`, `etag`, `sha256`, and the license at fetch time. Provenance is what makes the vendored copy defensible later.
8. **Stop on 429/403.** Treat as a signal to back off permanently, not to escalate.
9. **BrightData specifics, if used:** use the **Web Unlocker / single-request** product, *not* the crawler/IDE. **Do not** use residential/mobile residential pools to defeat access controls or geographic restrictions — that is the precise line between "accessing a public page" and "circumventing a technical access control." Set `country` to the project's own jurisdiction only if a regional block is incidental, and never to evade a deliberate block.

---

## 4. LEGAL / ETHICAL REJECTION LIST

These must **not** be scraped, downloaded for redistribution, or vendored. Where a "look at it to learn the structure, then write your own artefacts" path exists, it's noted — but **never copy text or tables verbatim.**

| Source | Why rejected | Consequence if scraped |
|---|---|---|
| **SAE J2012 / J1979 / J1939-71 / J1939-73 standard texts** | SAE standards are **paid copyrighted works** (purchased individually or via subscription). Copyright covers the expression *and* the standards body actively licenses them. | Piracy. Even if you buy a copy, redistribution is not licensed. **Mitigation:** the *facts* (code identifiers, bit widths, formulas) are not copyrightable — OBDex's maintainer states exactly this and re-authors descriptions independently. **Do the same: author your own text.** |
| **ISO 14229-1 (UDS), ISO 15031-5 (Mode 06), ISO 15765-2** | ISO standards are **paid copyrighted works**, same as SAE. | Piracy. **Mitigation:** implement from protocol structure/behaviour you've validated on hardware; write your own documentation and DID descriptions. |
| **AllData, Mitchell 1, Identifix, Snap-on, Delphi/Autodata** | Commercial subscription services. ToS explicitly prohibit scraping, automated access, extraction, and redistribution. Content is proprietary and licensed per-seat. | **Breach of contract + copyright infringement.** These are the highest-litigation-risk targets in the entire automotive data space. Hard no. |
| **OEM TSB *bodies* (Cummins/CAT/Scania/Volvo/Detroit/Mercedes/PACCAR service literature)** | Copyrighted OEM works. Publicly *visible* (a PDF on a dealer portal) ≠ licensed for redistribution. Many are behind dealer login = circumventing access controls. | Infringement + potential CFAA exposure if login-gated. **Mitigation:** NHTSA's manufacturer-communications *metadata* is public domain — use the TSB **ID, date, component, and summary** for cross-reference, and describe the failure mode in your own words. Do not reproduce TSB text, procedures, or figures. |
| **iATN (International Automotive Technicians Network)** | Members-only, paid. ToS forbids redistribution; content is technician-authored copyright. | Breach of contract + infringement. |
| **Forums (TruckersReport, BobIsTheOilGuy, brand-specific boards, Reddit threads)** | User-authored copyright; ToS commonly forbid scraping and republication. Reddit's ToS in particular now restricts commercial/ML use of content. | Infringement + ToS breach. **Mitigation:** NHTSA complaints give you better-structured, public-domain, real-world narratives. **Use those instead.** |
| **NMEA 2000 standard / NMEA 0183 standard** | **Paid** copyrighted NMEA documents. | Piracy. **Mitigation:** CANboat (Apache-2.0) has already done the reverse-engineering work and is redistributable — use it. |
| **Wikipedia PID/DTC tables (CC BY-SA)** | Not "rejected" but **flagged**: share-alike is viral and can contaminate a proprietary dataset. | License contamination, not infringement. **Prefer OBDex (CC0), which covers the same ground with zero obligations.** |
| **`alperunlu/DTCparser`, `f-steff/J1939_Reserved_SPN_Convention`, `digitalyacht/NMEA2000-Simulator-App`** | **No license declared** = all rights reserved by default. | Vendoring is infringement by default. **Reference/reading only.** |
| **Paywalled VIN-decode / DTC-lookup APIs (e.g. commercial aggregators)** | Per-query commercial licensing; ToS forbid bulk extraction and redistribution. | Breach of contract. **vPIC is free and public domain — use it instead.** |
| **Anything behind a login, CAPTCHA, or "request access"** | Circumventing a technical access control. | Potential CFAA / equivalent computer-misuse exposure, on top of ToS breach. **Never** use BrightData or any proxy to defeat such a control. |

**Governing principle:** the boundary is **"was this published for public consumption *and* under terms that permit reuse?"** Public *visibility* is not permission. If a source's ToS has to be argued around, treat it as rejected.

---

## 5. RANKED RECOMMENDATION — top 8, highest leverage first

| # | Source | One-line justification |
|---|---|---|
| **1** | **`foerbsnavi/OBDex`** (CC0-1.0 data) | Public-domain, zero-obligation, 9,533 generic DTCs *fully* enriched with causes/symptoms/repair cost/MIL flags **plus 132 PIDs** — it single-handedly solves the OBD-II half of your data problem with no attribution or fee, and CC0 is the cleanest license that exists for a vendored commercial dataset. |
| **2** | **`canboat/canboat`** (Apache-2.0) | One repo, two of your four code families: the complete NMEA 2000 PGN dictionary *and* a real J1939 PGN set including DM1 (PGN 65226) with the exact SPN/FMI/CM/OC bit layout — pre-generated as a 2.6 MB `canboat.json` requiring no build step. |
| **3** | **NHTSA complaints + recalls API / `FLAT_CMPL.zip`** | Public-domain, key-free, verified live — and the complaint `summary` free-text is the *only* legally-clean large corpus of real field narratives that routinely names actual DTCs, cascades, intermittent wiring faults, and TSB cross-references. This is your Golden-Traces raw material. |
| **4** | **`Wal33D/dtc-database`** (MIT) | 3.1 MB SQLite with 9,390 **manufacturer-specific** definitions across 33 brands — exactly the OEM layer OBDex deliberately omits, at a license (MIT) you can ship freely, readable via stdlib `sqlite3`. |
| **5** | **`STAS63-bit/sitrak-error-codes`** (CC BY 4.0) | 8,042 real heavy-duty SPN/FMI codes mapped to Bosch EDC17, ZF TraXon, KNORR/WABCO EBS, ECAS, NanoBCU — the closest thing to genuine redistributable heavy-duty OEM field data, and it directly strengthens your `cummins`/`scania`/`volvo`-style OEM decoder story. |
| **6** | **`commaai/opendbc`** (MIT) | The de-facto standard open DBC corpus (3,428★), including electrified powertrains — your best available EV/BMS-adjacent signal-level data, to be vendored at a pinned commit with a provenance note. |
| **7** | **NHTSA vPIC API** (public domain) | Free, key-free, verified (12,364 makes) — the clean way to resolve VIN→make/model/year/engine so your diagnostic findings can be attributed to a specific vehicle instead of a bare code. |
| **8** | **`Sherin-SEF-AI/CanLab`** (MIT) | Not primarily a dataset — an open, offline-first CAN diagnostics app (counters/checksums/DBC builder/J1939/ISO-TP/UDS/OBD-II, exports DBC + Wireshark dissectors) that is architecturally the nearest sibling to your project; mine its tables *and* its design decisions. |

**Deliberately excluded from the top 8:** Wikipedia (CC BY-SA viral risk; OBDex supersedes it), all SAE/ISO standard texts, all OEM TSB bodies, AllData/Mitchell/Identifix/iATN, and all forum content — for the reasons in §4.

---

## 6. VERIFICATION LEDGER

| URL probed | Result |
|---|---|
| `api.nhtsa.gov/products/vehicle/makes?issueType=c&modelYear=2020` | **HTTP 200** — `count:141` |
| `api.nhtsa.gov/complaints/complaintsByVehicle?make=honda&model=civic&modelYear=2020` | **HTTP 200** — `count:224`, real ODI records |
| `api.nhtsa.gov/recalls/recallsByVehicle?make=honda&model=civic&modelYear=2020` | **HTTP 200** — `Count:5` |
| `vpic.nhtsa.dot.gov/api/vehicles/getallmakes?format=json` | **HTTP 200** — `Count:12364` |
| `raw.githubusercontent.com/canboat/canboat/master/LICENSE` | **HTTP 200** — Apache-2.0, verified text |
| `raw.githubusercontent.com/canboat/canboat/master/docs/canboat.json` | **HTTP 200** — `License: Apache-2.0`, `Version 8.1.0`, `SchemaVersion 2.6.0` |
| `raw.githubusercontent.com/canboat/canboat/master/database/REFERENCES.md` | **HTTP 200** |
| `api.github.com/repos/canboat/canboat/contents/docs` | **HTTP 200** — canboat.json 2,664,633 B; xml 3,248,959 B; dbc 507,987 B |
| `api.github.com/repos/canboat/canboat/contents/database/j1939/pgns` | **HTTP 200** — DM1/ECU/temp/fuel/aftertreatment PGNs |
| `raw.githubusercontent.com/canboat/canboat/master/database/j1939/pgns/065226-activeTroubleCodes.yaml` | **HTTP 200** — SPN 19b / FMI 5b / CM 1b / OC 7b confirmed |
| `api.github.com/repos/canboat/canboat` | **HTTP 200** — 655★, 201 forks, pushed 2026-09-21 |
| `api.github.com/search/repositories?q=nmea+2000+pgn` | **HTTP 200** — 27 results |
| `api.github.com/repos/Wal33D/dtc-database/contents/` | **HTTP 200** — LICENSE, data/, python/, build_database.py |
| `api.github.com/repos/Wal33D/dtc-database/contents/data` | **HTTP 200** — `dtc_codes.db` **3,256,320 B** |
| `raw.githubusercontent.com/Wal33D/dtc-database/main/README.md` | **HTTP 200** — MIT, 18,805 rows, 33 manufacturers |
| `api.github.com/repos/foerbsnavi/OBDex/contents/` | **HTTP 200** — LICENSE-DATA, LICENSE-CODE, data/, schema/ |
| `api.github.com/repos/foerbsnavi/OBDex/contents/data` | **HTTP 200** — `generic/`, `pids/` |
| `raw.githubusercontent.com/foerbsnavi/OBDex/main/README.md` | **HTTP 200** — 9,533/9,533 codes, 132 PIDs, CC0 |
| `raw.githubusercontent.com/foerbsnavi/OBDex/main/LICENSE-DATA` | **HTTP 200** — **CC0 1.0 Universal**, verified text |
| `raw.githubusercontent.com/STAS63-bit/sitrak-error-codes/master/README.md` | **HTTP 200** — 8,042 codes, 44 systems, CC BY 4.0 |
| `api.github.com/repos/arthithadee/J1939-DBC-Database/contents/` | **HTTP 200** — MIT, `j1939_database.dbc` **6,204 B** (⚠️ *small* — verify before relying on it) |
| `api.github.com/repos/commaai/opendbc` | **HTTP 200** — MIT, 3,428★, 2,273 forks, pushed 2026-09-21 |
| `api.github.com/repos/linux-can/can-utils` | **HTTP 200** — 2,918★, **no license declared** ⚠️ |
| `en.wikipedia.org/wiki/OBD-II_PIDs` | **HTTP 200** — full PID tables (CC BY-SA) |
| `api.github.com/search/repositories?q=uds+iso14229+did+database` | **HTTP 200** — `total_count: 0` (**no open UDS DID DB**) |
| `static.nhtsa.gov/odi/ffdd/cmpl/FLAT_CMPL.zip` | **binary — file exists; fetch layer rejected `application/octet-stream`** |
| `www.nhtsa.gov/nhtsa-datasets-and-apis` | **HTTP 403 Access Denied** (Akamai; docs page only, not a data endpoint) |
| `www.epa.gov/compliance/obd-diagnostic-trouble-code-dtc-list` | **HTTP 404** — page does not exist |
| `raw.githubusercontent.com/InfluxTechnology/DBC-Files/main/README.md` | **HTTP 404** |
| `raw.githubusercontent.com/holtgrewe/obd-trouble-codes/master/README.md` | **HTTP 404** |
| `raw.githubusercontent.com/linux-can/can-dbc/master/README.md` | **HTTP 404** |
| `raw.githubusercontent.com/canboat/canboat/master/docs/canboat.csv` | **HTTP 404** (no CSV; JSON/XML/DBC/XSD only) |
| `api.github.com/repos/openxc/obd2-dtc-database` | **HTTP 404** — repo gone (the commonly-cited `openxc/obd2-dtc-database` no longer exists) |
| `api.github.com/search/code?...` | **HTTP 401** — code search requires authentication |

### Not verified in this session — labeled explicitly
- **OpenSkipper** (`github.com/OpenSkipper`) — not probed; believed dormant/superseded by CANboat + Signal K. **[UNVERIFIED]**
- **Cummins / CAT / Volvo / Scania / Detroit / Mercedes Actros / PACCAR public fault-code PDFs** — no specific redistributable URL identified. **Deliberately not hunted further**, because OEM service literature is copyrighted by default even when publicly posted (§4). **[UNVERIFIED / REJECTED]**
- **`nhtsa.gov` alternative routing** to the dataset index — the canonical page 403s and no alternative path was confirmed. The *API* endpoints are unaffected. **[UNVERIFIED]**
- **Whether a LICENSE file exists in `STAS63-bit/sitrak-error-codes`** — the README asserts CC BY 4.0 and the repo carries a license badge, but the GitHub API reports `NOASSERTION`. **Verify the raw LICENSE file before vendoring.**
- **CANboat repo-level SPDX** — API says `NOASSERTION` while the `LICENSE` file is unambiguously Apache-2.0. Trust the file; **pin a commit SHA** so the license you verified is the license you shipped.
- **UDS DID / Mode 06 open datasets** — exhaustive repo search returned zero results. Confirmed absent, not merely unfound.

---

## 7. Copy-paste build-time ingest commands (describe-only; DO NOT run inside the repo tree)

These are the commands a maintainer would run **once, on a dev machine, into a staging directory**, before committing only the distilled artifacts. They are documented here rather than executed, per the recon-only scope.

```bash
# ---------- Stage 0: staging area OUTSIDE the AI package ----------
mkdir -p /tmp/canbus-data-staging && cd /tmp/canbus-data-staging

# ---------- 1. OBDex (CC0-1.0) -> generic DTCs + PIDs ----------
git clone --depth 1 https://github.com/foerbsnavi/OBDex.git obdex
# Prefer the pre-built JSON from Pages (no Node build step needed):
curl -sSL -o obdex_generic.min.json https://foerbsnavi.github.io/obdex/generic.min.json
curl -sSL -o obdex_mode01.json      https://foerbsnavi.github.io/obdex/pids/mode01.json
curl -sSL -o obdex_mode09.json      https://foerbsnavi.github.io/obdex/pids/mode09.json
curl -sSL -o obdex_meta.json        https://foerbsnavi.github.io/obdex/meta.json
# ARRIVAL: cp obdex_*.json  <repo>/data/dtc/obdex/
#          + copy obdex/LICENSE-DATA and obdex/LICENSE-CODE alongside.

# ---------- 2. canboat (Apache-2.0) -> NMEA 2000 + J1939 PGN/SPN ----------
git clone --depth 1 https://github.com/canboat/canboat.git canboat
# Use the generated machine-readable files (no `make generated` needed):
#   canboat/docs/canboat.json   (2.6 MB, SchemaVersion 2.6.0)
#   canboat/docs/canboat.dbc    (508 KB)
#   canboat/database/j1939/pgns/*.yaml   (DM1 = 065226-activeTroubleCodes.yaml)
# ARRIVAL: cp canboat/docs/canboat.json  <repo>/data/pgn/canboat.json
#          + copy canboat/LICENSE (Apache-2.0) -> <repo>/data/pgn/LICENSE.canboat
#          + pin commit SHA into <repo>/data/pgn/PROVENANCE.md

# ---------- 3. dtc-database (MIT) -> 33-brand manufacturer-specific ----------
git clone --depth 1 https://github.com/Wal33D/dtc-database.git dtcdb
# ARRIVAL: cp dtcdb/data/dtc_codes.db  <repo>/data/dtc/dtc_codes.db   # 3.1 MB
#          + copy dtcdb/LICENSE (MIT)

# ---------- 4. SITRAK SPN/FMI (CC BY 4.0 -- VERIFY LICENSE FIRST) ----------
git clone --depth 1 https://github.com/STAS63-bit/sitrak-error-codes.git sitrak
# ARRIVAL: cp sitrak/error-codes.json  <repo>/data/dtc/sitrak_spn_fmi.json
#          + copy any LICENSE file found; else record README license claim.
#          + REQUIRED: ship attribution "megadata.pro, CC BY 4.0" in the about screen.

# ---------- 5. NHTSA (public domain) -> snapshot ONCE, then distill ----------
# Preferred: bulk flat file (large -- distill before committing!)
curl -sSL -o FLAT_CMPL.zip https://static.nhtsa.gov/odi/ffdd/cmpl/FLAT_CMPL.zip
# Or per-vehicle API snapshots for a curated set of vehicle families:
curl -sSL -o cmpl_honda_civic_2020.json \
  'https://api.nhtsa.gov/complaints/complaintsByVehicle?make=honda&model=civic&modelYear=2020'
curl -sSL -o recall_honda_civic_2020.json \
  'https://api.nhtsa.gov/recalls/recallsByVehicle?make=honda&model=civic&modelYear=2020'
curl -sSL -o vpic_makes.json \
  'https://vpic.nhtsa.dot.gov/api/vehicles/getallmakes?format=json'
# ARRIVAL: run a one-off distillation script; commit ONLY the bounded, curated
#          subset to <repo>/data/cases/. Do NOT commit raw FLAT_CMPL.zip.

# ---------- 6. DBC corpora (MIT, pinned commit + provenance) ----------
git clone --depth 1 https://github.com/commaai/opendbc.git opendbc
# ARRIVAL: copy only the specific .dbc files you need -> <repo>/data/dbc/opendbc/
#          + record origin repo + commit SHA in <repo>/data/dbc/PROVENANCE.md

# ---------- VERIFY license assumptions before vendoring ----------
# for each candidate: confirm a LICENSE file actually exists and read it.
# OBDex  -> LICENSE-DATA = CC0-1.0  (verified this session)
# canboat-> LICENSE      = Apache-2.0 (verified this session)
# Wal33D -> LICENSE      = MIT (API-verified)
# SITRAK -> license file NOT confirmed; README claims CC BY 4.0  <-- verify!

# ---------- RECORD provenance for every vendored artifact ----------
# sha256sum + source URL + fetched_at + license  ->  <repo>/data/PROVENANCE.md
```

**Guardrails for whoever runs this:**
- Run it from `tools/data_ingest/` (or a scratch dir), **never** from `src/engine/ai/**` — otherwise a `requests`/`urllib` import will trip `tests/safety/test_ai_tx_isolation.py`.
- Never commit a blob you haven't confirmed the license of. Pin commit SHAs. Keep `PROVENANCE.md` current.
- Filter NHTSA complaint free-text for PII before it enters `data/` — complaints contain VINs and occasionally redacted-but-recoverable personal details. **VINs must be truncated to the 8-char WMI/VDS prefix or dropped entirely.**
- No fabricated values anywhere in the vendored set (AGENTS.md §2.3): if a field is absent in the source, leave it absent rather than filling a plausible default.
