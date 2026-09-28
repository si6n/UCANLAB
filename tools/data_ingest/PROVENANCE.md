# Vendored Data Provenance — tools/data_ingest/staging/

Generated: `2026-09-21T14:15:42+00:00`  |  by `tools/data_ingest/fetch_sources.py`

> **Build-time vendor data only.** The product is fully offline; runtime reads
> these files from disk. No LLM, no cloud call, no network dependency ships.
> Nothing here is merged into `data/` — that is Tur-2's job.
>
> **2026-09-28 — ingest tooling removed.** `fetch_sources.py`,
> `merge_staging_into_data.py`, `verify_records.py`, `write_provenance.py` and
> `staging/` are gone; this document, `provenance.json`, `verification.json`
> and `licenses/` remain as the frozen evidence record for the shipped
> `data/` knowledge base. Commands below that name a removed script are
> historical.

## Verified licenses (read from the actual LICENSE file, not the README)

| Source | Repo | Pinned commit | License (SPDX) | Evidence |
|---|---|---|---|---|
| `foerbsnavi/OBDex` | obdex | `bc58b0eb7273` | **CC0-1.0** | `LICENSE-DATA` (709 B) — CC0 1.0 Universal dedication, data files |
| `canboat/canboat` | canboat | `f7f088b49d58` | **Apache-2.0** | `LICENSE` (614 B) — Apache License 2.0, Kees Verruijt |
| `Wal33D/dtc-database` | dtcdb | `04c43d72e7db` | **MIT** | `LICENSE` (1,078 B) — MIT, Copyright (c) 2024 Wal33D |
| `STAS63-bit/sitrak-error-codes` | sitrak | `fdb0c0d9daf0` | **CC-BY-4.0** | `LICENSE` (881 B) — CC BY 4.0 (Russian-language deed) |

### SITRAK resolution (the one the recon could not verify)

The recon flagged `STAS63-bit/sitrak-error-codes` as suspicious because the GitHub
API reports `NOASSERTION`. **A real `LICENSE` file does exist** (881 bytes, sha
`ffc973284ba440ae28b5eba7c22352a5582c7adf` at the repo root). Its text declares:

> Creative Commons Attribution 4.0 International (CC BY 4.0)
> База кодов ошибок SITRAK / SITRAK Error Codes Database — © МегаДата (megadata.pro)
> Commercial use, copying, modification and distribution permitted **with**
> attribution: name the source МегаДата / megadata.pro and keep the link.

`NOASSERTION` is a **GitHub classifier artifact**: the license is a Russian-language
deed, which GitHub's SPDX matcher cannot map. **Verdict: REDISTRIBUTABLE** under
CC BY 4.0, *provided the attribution below ships in the product's about/licenses UI.*

> **Required attribution (CC BY 4.0 obligation):**
> `Источник: МегаДата / megadata.pro — CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)`

## Artifacts

| Artifact | Source URL | Bytes | sha256 | License |
|---|---|---|---|---|
| `tools/data_ingest/staging/obdex/data/generic/B0xxx_enriched.yaml` | [link](https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/generic/B0xxx_enriched.yaml) | 445,985 | `73d317fb49b01a369ee29126bb0bb4f31c4775a5fe0a7e7fae1456aa980d3bc4` | CC0-1.0 |
| `tools/data_ingest/staging/obdex/data/generic/C0xxx_enriched.yaml` | [link](https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/generic/C0xxx_enriched.yaml) | 1,041,438 | `1d60a394ff9cfde96b6e7a738420ac05dd48b85ebfe798001c38310aba7073e6` | CC0-1.0 |
| `tools/data_ingest/staging/obdex/data/generic/P0xxx_enriched.yaml` | [link](https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/generic/P0xxx_enriched.yaml) | 6,206,241 | `a765cade770ffe756a5d4ea91c61fc128d5508f06f38552cd803dd726fecef63` | CC0-1.0 |
| `tools/data_ingest/staging/obdex/data/generic/P2xxx_enriched.yaml` | [link](https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/generic/P2xxx_enriched.yaml) | 5,750,928 | `eda26317419f7e897c13eb254f74a01d905f180c7006174298d08d665b42dc4a` | CC0-1.0 |
| `tools/data_ingest/staging/obdex/data/generic/P3xxx_enriched.yaml` | [link](https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/generic/P3xxx_enriched.yaml) | 255,238 | `bba19fe7dddb757866632ae939e4ace7dbbbe639816d3a46ce3a6936053708a3` | CC0-1.0 |
| `tools/data_ingest/staging/obdex/data/generic/U0xxx_enriched.yaml` | [link](https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/generic/U0xxx_enriched.yaml) | 2,228,894 | `60aebde267b4bae53902e0abfeb7b489e314cd9e2ecdf0eb62384ea6b5a56afc` | CC0-1.0 |
| `tools/data_ingest/staging/obdex/data/generic/U3xxx_enriched.yaml` | [link](https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/generic/U3xxx_enriched.yaml) | 370,022 | `f8a4b3723140b059216bad1d5fd450ce067c9c6bb8361f4120bbd422ecc769b7` | CC0-1.0 |
| `tools/data_ingest/staging/canboat/canboat.json` | [link](https://raw.githubusercontent.com/canboat/canboat/f7f088b49d58f5b4a0feb9b29c288b0ae18a7880/docs/canboat.json) | 2,664,633 | `b5a2c0c84b59af33caef583a372f9e763deb54ef4caf187825eff33d068735ba` | Apache-2.0 |
| `tools/data_ingest/staging/dtcdb/dtc_codes.db` | [link](https://raw.githubusercontent.com/Wal33D/dtc-database/04c43d72e7db7197658b6f72fe582c5076d9eee8/data/dtc_codes.db) | 3,256,320 | `099a4ffd60398112a0540b0bbc93a5929e05e7f4e6d4988ca2a50858af01b743` | MIT |
| `tools/data_ingest/staging/canboat/j1939_065226-activeTroubleCodes.yaml` | [link](https://raw.githubusercontent.com/canboat/canboat/f7f088b49d58f5b4a0feb9b29c288b0ae18a7880/database/j1939/pgns/065226-activeTroubleCodes.yaml) | 1,421 | `2c821042983bd5c751794dba388f1a8121c57ecacd5d0ab5a9ec95b03ec197c9` | Apache-2.0 |
| `tools/data_ingest/staging/obdex/data/pids/mode01.yaml` | [link](https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/pids/mode01.yaml) | 32,408 | `cc4c435fe5ce8af2ff5b9b084dd9a96c32c8257d2ac8116e819281cb06fa367a` | CC0-1.0 |
| `tools/data_ingest/staging/obdex/data/pids/mode09.yaml` | [link](https://raw.githubusercontent.com/foerbsnavi/OBDex/bc58b0eb7273226a1aabae98e956b70b8362bda1/data/pids/mode09.yaml) | 2,893 | `aaf0aa7041f8f1f81cfc0a7dfd1c3dab62dda68ca43c64d46357a44a04726233` | CC0-1.0 |
| `tools/data_ingest/staging/sitrak/sitrak_error-codes.json` | [link](https://raw.githubusercontent.com/STAS63-bit/sitrak-error-codes/fdb0c0d9daf0643975b0ff62e0ff69ef9c07f742/error-codes.json) | 1,962,001 | `69d726d69d5612eb890de0aa2579beef2220a2f2b371b8cbcd560e75f12840d3` | CC-BY-4.0 |

License texts preserved under `tools/data_ingest/licenses/`:

| File | Bytes | sha256 |
|---|---|---|
| `LICENSE-CODE.obdex` | 1,238 | `183f37a19873eb8965f59d3d956549e32663d5d50e79fcaeb80650149c10e377` |
| `LICENSE-DATA.obdex` | 709 | `2f0d80d2bf20d5637b0077983b503efec8ed943222d8ca14cc1b1295f9eaab3c` |
| `LICENSE.canboat` | 614 | `ec0704d2efcacadb11d846ed2c353c57625756647a2dd735094682a3f7bf7721` |
| `LICENSE.dtc-database` | 1,078 | `5106ad327a989f9f864f55f9111d055530af80c3109e9f26026bc2cd486087f5` |
| `LICENSE.sitrak` | 881 | `49ba32b704a635b18f06457a07bcc8e6c7aa57909b233b1ec66a030cb1575d45` |

**Total data bytes staged: 24,218,422** across 13 data artifacts.

## Independent verification (records actually decode)

`tools/data_ingest/verify_records.py` — **5 pass / 0 fail**.
Raw evidence: `tools/data_ingest/verification.json`.

| Check | Result | Key numbers |
|---|---|---|
| Wal33D `dtc_codes.db` (stdlib `sqlite3`) | PASS | tables ['dtc_definitions', 'statistics']; `dtc_definitions` = **18,805** rows, **12,128** distinct codes; P0420 probe returned 2 rows |
| canboat `canboat.json` (JSON parse) | PASS | **628** PGNs; declared license `Apache License Version 2.0`; Version 8.1.0, SchemaVersion 2.6.0; PGN range 59392–131012 |
| SITRAK `error-codes.json` | PASS | **8,042** records, all with SPN+FMI; 7,635 RU / 2,441 EN; **44** distinct systems |
| OBDex generic YAML (PyYAML) | PASS | **9,533** codes across 7 files; PIDs mode01=119 + mode09=13 = **132** |
| canboat J1939 DM1 YAML | PASS | `pgn: 65226`, `id: activeTroubleCodes`; lamp fields: Malfunction Lamp Status, Red Stop Lamp Status, Amber Warning Lamp Status, Protect Lamp Status |

## ⚠️ CORRECTIONS to the recon report (verified divergences)

These were found by probing the real endpoints, and they change what Tur 2 can
assume. Both recon claims below are **wrong** as of this fetch.

### 1. OBDex does NOT publish pre-built JSON over GitHub Pages

The recon's ingest commands fetch `https://foerbsnavi.github.io/obdex/generic.min.json`
(`generic.min.json`, `pids/mode01.json`, `pids/mode09.json`, `meta.json`). **All of
these 404.** The Pages site does not exist —
`https://foerbsnavi.github.io/obdex/` returns **HTTP 404 "There isn't a GitHub Pages
site here"**, and the repository has **no `dist/` directory** and **no `meta.json`**.
**What actually exists** (and is staged): the authored sources under `data/` —
`data/generic/{P0,P2,P3,B0,C0,U0,U3}xxx_enriched.yaml` plus `data/pids/mode0{1,9}.yaml`.
Tur 2 must convert YAML→JSON during merge (PyYAML is already available in this
environment). **We did not fabricate a converted JSON artifact here.**

### 2. `docs/canboat.json` contains NO J1939 — it is NMEA-2000 only

The recon states canboat "is two of the four required families in one repo" and
implies the generated `canboat.json` carries J1939/DM1. Verified against the
actual file:

- `628` PGNs total, number range **59392–131012** (NMEA-2000 space).
- PGN **65226 (DM1) present? False**
- PGNs with an `SPN`/`FMI` field: **0**
- PGNs typed J1939: **0**

**J1939 exists only in the `database/j1939/pgns/*.yaml` sources**, of which we staged
the DM1 file (`j1939_065226-activeTroubleCodes.yaml`). Tur 2 must not plan a J1939
merge against `canboat.json`; it needs the YAML sources (or additional ones fetched
the same way) for the J1939 side.

### 3. Schema note: DM1 YAML and canboat.json use different key casing

- J1939 YAML sources: **lowercase** (`pgn`, `fields`, `id`, `name`, `bitLength`).
- Generated `canboat.json`: **PascalCase** (`PGN`, `Fields`, `Id`, `Name`, `BitLength`).

A parser written for one will silently read nothing from the other — the exact
class of bug in Tuzaklar §11 (a gap is a real state, not something to fill
with an invented default).

## Rejected sources (not vendored)

| Source | Reason |
|---|---|
| SAE J2012 / J1979 / J1939-71 / J1939-73 standard texts | Paid copyrighted works; redistribution not licensed even if bought. The FACTS (code id, bit widths, formulas) are not copyrightable — OBDex re-authors descriptions independently. Do the same. |
| ISO 14229-1 (UDS) / ISO 15031-5 (Mode 06) / ISO 15765-2 | Paid copyrighted ISO standards; same as SAE. Implement from protocol structure. |
| OEM TSB bodies (Cummins/CAT/Scania/Volvo/Detroit/Mercedes/PACCAR) | Copyrighted OEM service literature; public visibility != redistribution license. Many are dealer-login-gated (CFAA exposure). |
| AllData / Mitchell 1 / Identifix / Snap-on / Autodata | Commercial subscription services; ToS forbid scraping + redistribution. |
| iATN + public forums (TruckersReport, Reddit, brand boards) | Members-only / user-authored copyright; ToS forbid scraping + republication. |
| Wikipedia OBD-II PID + DTC tables | CC BY-SA 4.0 is viral — a derivative DB arguably must also be CC BY-SA, which is unacceptable for this proprietary repo. OBDex (CC0) covers the same ground with zero obligations. |
| alperunlu/DTCparser, f-steff/J1939_Reserved_SPN_Convention, digitalyacht/NMEA2000-Simulator-App, linux-can/can-utils | No license declared = all rights reserved by default. Reference-only. |
| NMEA 2000 / NMEA 0183 standard documents | Paid copyrighted NMEA documents. CANboat already did the RE work and is Apache-2.0. |
| UDS DID dictionary / Mode 06 data tables | No open redistributable source exists (exhaustive GitHub search = 0). This is a genuine capability gap — hand-author in-repo, do not fabricate. |
| NHTSA complaints / recalls / vPIC bulk data | Public domain and clean, but OUT OF SCOPE for T1-3 (PHASE 1 vendor set), and complaint free-text carries PII/VINs that must be masked before any data/ entry. Deferred to a later turn with a PII-masking gate. |

## Reproduce

```bash
python tools/data_ingest/fetch_sources.py      # download into staging/ + record provenance
python tools/data_ingest/verify_records.py     # independent decode checks
python tools/data_ingest/write_provenance.py   # regenerate this file
```

> ⚠️ Never relocate these scripts under `src/engine/ai/` — the AI package forbids
> `urllib`/`http`/`socket`/`requests` and `tests/safety/test_ai_tx_isolation.py`
> must stay green.
