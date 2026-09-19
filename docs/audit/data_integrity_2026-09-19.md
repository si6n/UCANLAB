# data/ Bütünlük Denetimi — 2026-09-19

> `python scripts/data_integrity_audit.py` çıktısı. Offline, ağ yok; her bulgu dosya yolu ve ölçülen sayı ile birlikte verilir.

- Bulgu: **FAIL 0** · WARN 1 · INFO 1

## Bulgular

| Önem | Denetim | Bulgu | Kanıt |
|---|---|---|---|
| INFO | golden_cases | 55/55 vakada trace_ref boş (canlı kayıt kanıtı yok) | `örnek: case-c1a00-verified, case-can-term-60-verified, case-can-volt-fault-verified` |
| WARN | truncation_signature | 4/4 aday tam (400, 403) uzunlukta ama metinsel kırpma izi yok (belirsiz — elle doğrulanmalı) | `örnek: SPN_639.steps[403], SPN_70.steps[400], SPN_560.steps[403], SPN_560.steps[403]` |

## Kapsam (INFO — bilgi tabanı doluluk oranları)

| Küme | Alan | Dolu | Toplam | Oran |
|---|---|---|---|---|
| J1939 | title_tr | 4253 | 4253 | 100.0% |
| J1939 | description | 4000 | 4253 | 94.1% |
| J1939 | causes | 3720 | 4253 | 87.5% |
| J1939 | steps | 3457 | 4253 | 81.3% |
| J1939 | procedures_full | 1235 | 4253 | 29.0% |
| J1939 | fault_matrix | 4218 | 4253 | 99.2% |
| DTC | causes | 14349 | 14352 | 100.0% |
| DTC | steps | 14349 | 14352 | 100.0% |
| DTC | symptoms | 876 | 14352 | 6.1% |
| DTC | measurement | 9300 | 14352 | 64.8% |
| DTC | source | 4866 | 14352 | 33.9% |
| DTC | evidence_url | 4866 | 14352 | 33.9% |

## Ölçümler

```json
{
 "json_asset_count": 674,
 "metadata_totals": {
  "data/diagnostics/j1939_spn_fmi_database.json": {
   "declared": 4253,
   "actual": 4253
  },
  "data/diagnostics/uds_did_database.json": {
   "declared": 68,
   "actual": 68
  },
  "data/diagnostics/extended_pid_database.json": {
   "declared": 112,
   "actual": 112
  },
  "data/diagnostics/obd_mode06_database.json": {
   "declared": 38,
   "actual": 38
  }
 },
 "csv_twins": {
  "dtc_database.csv": {
   "filled": 14352,
   "blank": 0,
   "ragged": 0,
   "header_len": 6,
   "widths": {
    "6": 14352
   },
   "expected": 14352,
   "md5": "21c06ddab7930594023ec69072381eed"
  },
  "uds_did_database.csv": {
   "filled": 68,
   "blank": 0,
   "ragged": 0,
   "header_len": 10,
   "widths": {
    "10": 68
   },
   "expected": 68,
   "md5": "b8476e24827758818fc6179697e98a59"
  },
  "extended_pid_database.csv": {
   "filled": 112,
   "blank": 0,
   "ragged": 0,
   "header_len": 15,
   "widths": {
    "15": 112
   },
   "expected": 112,
   "md5": "44a06bca05da85ef3c129978be0003c3"
  },
  "j1939_spn_fmi_database.csv": {
   "filled": 11128,
   "blank": 0,
   "ragged": 0,
   "header_len": 8,
   "widths": {
    "8": 11128
   },
   "expected": 11128,
   "md5": "04fcd64102d304d0cdd379dcbd867e14"
  },
  "nhtsa_can_recalls_database.csv": {
   "filled": 282,
   "blank": 0,
   "ragged": 0,
   "header_len": 11,
   "widths": {
    "11": 282
   },
   "expected": 282,
   "md5": "d322f9c86f20523c9e2bc511e56afba2"
  }
 },
 "dbc_catalog": {
  "catalog_files": 186,
  "resolved": 186,
  "missing": 0,
  "sha_mismatch": 0,
  "size_mismatch": 0,
  "dbc_on_disk": 196,
  "unlisted": 25,
  "unlisted_singular": []
 },
 "golden_cases": {
  "loader": true,
  "cases": 55,
  "valid": 55,
  "calibration_eligible": 54,
  "drafts": [
   "volvo-penta-d4-300-nonstart"
  ],
  "domains": {
   "PASSENGER": 42,
   "HEAVY_DUTY": 12,
   "MARINE": 1
  },
  "without_trace_ref": 55
 },
 "knowledge_procedures": {
  "files": 601,
  "parse_errors": 0,
  "orphans": 0,
  "name_mismatch": 0,
  "incomplete": 0
 },
 "truncation_signature": {
  "cap_length_candidates": 4,
  "flagged": [],
  "caps": [
   400,
   403
  ]
 },
 "fault_matrix_titles": {
  "fault_matrix_rows": 11128,
  "missing_title": 0,
  "samples": []
 }
}
```
