# data/ Bütünlük Denetimi — 2026-09-26

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
| J1939 | title_tr | 4291 | 4291 | 100.0% |
| J1939 | description | 4029 | 4291 | 93.9% |
| J1939 | causes | 3720 | 4291 | 86.7% |
| J1939 | steps | 3457 | 4291 | 80.6% |
| J1939 | procedures_full | 1444 | 4291 | 33.7% |
| J1939 | fault_matrix | 4227 | 4291 | 98.5% |
| DTC | causes | 14481 | 14484 | 100.0% |
| DTC | steps | 14349 | 14484 | 99.1% |
| DTC | symptoms | 1275 | 14484 | 8.8% |
| DTC | measurement | 9300 | 14484 | 64.2% |
| DTC | source | 14237 | 14484 | 98.3% |
| DTC | evidence_url | 14237 | 14484 | 98.3% |

## Ölçümler

```json
{
 "json_asset_count": 688,
 "metadata_totals": {
  "data/diagnostics/j1939_spn_fmi_database.json": {
   "declared": 4291,
   "actual": 4291
  },
  "data/diagnostics/uds_did_database.json": {
   "declared": 68,
   "actual": 68
  },
  "data/diagnostics/extended_pid_database.json": {
   "declared": 226,
   "actual": 226
  },
  "data/diagnostics/obd_mode06_database.json": {
   "declared": 38,
   "actual": 38
  }
 },
 "csv_twins": {
  "dtc_database.csv": {
   "filled": 14484,
   "blank": 0,
   "ragged": 0,
   "header_len": 6,
   "widths": {
    "6": 14484
   },
   "expected": 14484,
   "md5": "071bbfa74e03615e6f31dacf73126ced"
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
   "filled": 226,
   "blank": 0,
   "ragged": 0,
   "header_len": 15,
   "widths": {
    "15": 226
   },
   "expected": 226,
   "md5": "f0d59480a658427f02aef2e27924b450"
  },
  "j1939_spn_fmi_database.csv": {
   "filled": 11146,
   "blank": 0,
   "ragged": 0,
   "header_len": 8,
   "widths": {
    "8": 11146
   },
   "expected": 11146,
   "md5": "0199e49fbc63c12c5aeaca47565551cd"
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
  },
  "obd_mode06_database.csv": {
   "filled": 247,
   "blank": 0,
   "ragged": 0,
   "header_len": 15,
   "widths": {
    "15": 247
   },
   "expected": 247,
   "md5": "14f185920ad246488e52ed39106bac3c"
  },
  "canonical_symptoms.csv": {
   "filled": 152,
   "blank": 0,
   "ragged": 0,
   "header_len": 10,
   "widths": {
    "10": 152
   },
   "expected": 152,
   "md5": "58a978f4a4b36a0814f560f7676fbe5a"
  }
 },
 "dbc_catalog": {
  "catalog_files": 211,
  "resolved": 211,
  "missing": 0,
  "sha_mismatch": 0,
  "size_mismatch": 0,
  "dbc_on_disk": 196,
  "unlisted": 0,
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
  "fault_matrix_rows": 11146,
  "missing_title": 0,
  "samples": []
 }
}
```
