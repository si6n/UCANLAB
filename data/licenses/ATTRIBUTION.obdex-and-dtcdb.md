# ATTRIBUTION — OBDex (CC0-1.0) and Wal33D/dtc-database (MIT)

Attribution records for the two additional upper-streams merged in T2-4.

---

## 1. OBDex — CC0-1.0 (no obligation, recorded for traceability)

OBDex dedicates its **data files** to the public domain under **CC0 1.0
Universal**, so there is **no attribution obligation**. This note exists purely
so the provenance chain stays auditable.

| Field | Value |
|---|---|
| Work | OBDex — generic OBD-II DTC descriptions + Mode 01/09 PID catalogue |
| Author | obdex contributors |
| Source URL | https://github.com/foerbsnavi/OBDex |
| Pinned commit | `bc58b0eb7273226a1aabae98e956b70b8362bda1` |
| License (data) | **CC0-1.0** — `LICENSE-DATA` (709 B, sha256 `2f0d80d2bf20d5637b0077983b503efec8ed943222d8ca14cc1b1295f9eaab3c`) |
| License (code) | MIT — `LICENSE-CODE` (1,238 B). **Not used**; no OBDex code was copied. |
| License URL | https://creativecommons.org/publicdomain/zero/1.0/legalcode |

License text (verbatim excerpt from the upstream `LICENSE-DATA`):

> The person who associated a work with this deed has dedicated the work to the
> public domain by waiving all of his or her rights to the work worldwide under
> copyright law, including all related and neighboring rights, to the extent
> allowed by law.
>
> You can copy, modify, distribute and perform the work, even for commercial
> purposes, all without asking permission.
>
> This dedication applies to the data files contained in this repository
> (YAML, JSON, schema files describing OBD-II codes and PIDs). The accompanying
> code is licensed separately under the MIT License, see LICENSE-CODE.

### Important correction carried from T1-3

OBDex does **not** publish pre-built JSON over GitHub Pages. The recon report's
ingest commands (`https://foerbsnavi.github.io/obdex/generic.min.json` etc.)
return **HTTP 404** — no Pages site exists and the repo has no `dist/`. The real
authored sources are the YAML files, which are what was vendored and merged:

- `data/generic/{P0,P2,P3,B0,C0,U0,U3}xxx_enriched.yaml` → 9,533 generic DTCs
- `data/pids/mode01.yaml` (119) + `data/pids/mode09.yaml` (13) → 132 PIDs

---

## 2. Wal33D/dtc-database — MIT

| Field | Value |
|---|---|
| Work | dtc-database (`data/dtc_codes.db`, SQLite) |
| Author | **Wal33D (Waleed Judah)** |
| Source URL | https://github.com/Wal33D/dtc-database |
| Pinned commit | `04c43d72e7db7197658b6f72fe582c5076d9eee8` |
| License | **MIT** |
| Vendored license file | `tools/data_ingest/licenses/LICENSE.dtc-database` (1,078 B, sha256 `5106ad327a989f9f864f55f9111d055530af80c3109e9f26026bc2cd486087f5`) |
| Upstream artifact sha256 | `099a4ffd60398112a0540b0bbc93a5929e05e7f4e6d4988ca2a50858af01b743` |
| Content | 18,805 rows / 12,128 distinct codes / 33 manufacturers (9,415 generic + 9,390 OEM) |

MIT requires the copyright notice and permission notice to be included in all
copies or substantial portions. Preserved here:

> MIT License
>
> Copyright (c) 2024 Wal33D (Waleed Judah)
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

### Scope note (layering)

T2-4 preserved the main DTC database's key schema (`P/B/C/U`) and did **not**
mix the OEM manufacturer layer into it. The Wal33D layer ships as a **separate
sibling file**, `data/diagnostics/dtc_database_oem_layer.json`, following the
rule already established in `data/diagnostics/PROVENANCE.md` (T25 /
HANDOFF_TUR24 §4.4: the OEM layer stays in its own file).
