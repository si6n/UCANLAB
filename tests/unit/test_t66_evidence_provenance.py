# -*- coding: utf-8 -*-
"""T66 / I-18 regression lock — evidence of KEPT records must survive the merge.

THE DEFECT (review finding I-18)
--------------------------------
`t66_adapter.py` produced an `evidence` field (plus the exact source text), but
`t66_merge.py` only checked its LENGTH (`evidence_min`) and then wrote the
production record WITHOUT the evidence or its hash. A kept record's only proof
of provenance was dropped at the last step, so nothing downstream could tell a
verbatim-quoted record from a bare assertion — and nothing marked unverified
harvest output as non-authoritative.

LOCKED CONTRACT
---------------
  * every KEPT merged record carries: exact evidence text OR its SHA-256, the
    source URL/ID, the retrieval timestamp, the adapter version, and an
    explicit verification status;
  * `verification_status: unverified` (the adapter's default) is marked
    `evidence_authoritative: false`, so harvest output can never be read as
    independently verified evidence;
  * the fail-closed DROP for below-minimum evidence is intentional and stays:
    the fix only stops LOSING the evidence of records that are kept;
  * fill-if-empty: a record that already has an evidence chain is never
    overwritten by a later merge.

All tests use tmp_path fixtures; the real data/diagnostics DBs are untouched.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTER_PATH = REPO_ROOT / "scripts" / "t66_adapter.py"
MERGE_PATH = REPO_ROOT / "scripts" / "t66_merge.py"

EVIDENCE = (
    "Catalyst efficiency below threshold bank 1: the PCM compares the "
    "upstream and downstream oxygen sensor signals and flags the converter "
    "when switching activity falls under the calibrated threshold."
)
EVIDENCE_SHA = hashlib.sha256(EVIDENCE.encode("utf-8")).hexdigest()


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"{path} must be loadable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def adapter_mod():
    return _load(ADAPTER_PATH, "t66_adapter_i18")


@pytest.fixture(scope="module")
def merge_mod():
    return _load(MERGE_PATH, "t66_merge_i18")


@pytest.fixture()
def dbs(tmp_path: Path) -> tuple[Path, Path]:
    dtc_db = tmp_path / "dtc_database.json"
    j1939_db = tmp_path / "j1939_spn_fmi_database.json"
    dtc_db.write_text(
        json.dumps({"P0420": {"title_en": "", "symptoms_en": []}}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    j1939_db.write_text(
        json.dumps({"metadata": {"total_spns": 1}, "spns": {"SPN_100": {"causes": []}}},
                   ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    return dtc_db, j1939_db


def _merge(merge_mod, tmp_path: Path, dbs, records, target_db="dtc"):
    inp = tmp_path / "mr_t66.json"
    inp.write_text(json.dumps({"target_db": target_db, "records": records},
                              ensure_ascii=False), encoding="utf-8")
    rc = merge_mod.main(
        ["--inputs", str(inp), "--report", str(tmp_path / "report.json"), "--apply"],
        dtc_db=dbs[0], j1939_db=dbs[1], lock_path=tmp_path / ".t66_merge.lock",
    )
    return rc, json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))


def _raw_rec(**over) -> dict:
    rec = {
        "code": "P0420",
        "url": "https://obd2.com/dtc/p0420-catalyst",
        "source": "obd2.com",
        "title": "Catalyst efficiency below threshold bank 1",
        "description": EVIDENCE,
        "symptoms": ["Rough idle and a stored P0420 code after warm-up"],
        "steps": ["Inspect the converter for physical damage", "Compare O2 waveforms"],
    }
    rec.update(over)
    return rec


# ---------------------------------------------------------------------------
# 1. Adapter output carries the full evidence chain
# ---------------------------------------------------------------------------

class TestAdapterProvenance:
    def test_adapter_emits_evidence_and_provenance(self, adapter_mod) -> None:
        out = adapter_mod.adapt_dtc(_raw_rec(retrieved_at="2026-09-24T10:00:00+00:00"), 20)
        assert out is not None
        assert out["evidence"] in (EVIDENCE,)
        prov = out["provenance"]
        assert prov["evidence"] == out["evidence"]
        assert prov["evidence_sha256"] == hashlib.sha256(
            out["evidence"].encode("utf-8")
        ).hexdigest()
        assert prov["source_url"] == "https://obd2.com/dtc/p0420-catalyst"
        assert prov["retrieved_at"] == "2026-09-24T10:00:00+00:00"
        assert prov["adapter_version"].startswith("t66_adapter/")
        assert prov["verification_status"] == "unverified"

    def test_adapter_stamps_retrieval_time_when_absent(self, adapter_mod) -> None:
        out = adapter_mod.adapt_dtc(_raw_rec(), 20)
        assert out is not None
        assert out["provenance"]["retrieved_at"], "a retrieval timestamp is mandatory"

    def test_below_minimum_evidence_is_still_dropped(self, adapter_mod) -> None:
        """The fail-closed drop is intentional — the fix must not weaken it."""
        short = {"code": "P0420", "url": "https://obd2.com/dtc/p0420-catalyst",
                 "source": "obd2.com", "title": "P0420", "description": "too short",
                 "symptoms": ["rough idle"], "steps": ["check it"]}
        assert adapter_mod.adapt_dtc(short, evidence_min=20) is None


# ---------------------------------------------------------------------------
# 2. Merge writes the evidence chain into the production record
# ---------------------------------------------------------------------------

class TestMergedRecordProvenance:
    def test_kept_record_carries_full_evidence_chain(self, merge_mod, dbs, tmp_path) -> None:
        rec = {
            "code": "P0420",
            "url": "https://obd2.com/dtc/p0420-catalyst",
            "source": "obd2.com",
            "title_en": "Catalyst efficiency below threshold bank 1",
            "evidence": EVIDENCE,
            "provenance": {
                "evidence_sha256": EVIDENCE_SHA,
                "source_id": "obd2.com/p0420",
                "retrieved_at": "2026-09-24T10:00:00+00:00",
                "adapter_version": "t66_adapter/1.1-provenance",
                "verification_status": "unverified",
            },
        }
        rc, report = _merge(merge_mod, tmp_path, dbs, [rec])
        assert rc == 0 and report["totals"]["dtc_filled"] == 1

        entry = json.loads(dbs[0].read_text(encoding="utf-8"))["P0420"]
        assert entry["evidence"] == EVIDENCE, "the exact evidence text must be kept"
        assert entry["evidence_sha256"] == EVIDENCE_SHA
        assert entry["evidence_source_url"] == "https://obd2.com/dtc/p0420-catalyst"
        assert entry["evidence_source_id"] == "obd2.com/p0420"
        assert entry["evidence_retrieved_at"] == "2026-09-24T10:00:00+00:00"
        assert entry["evidence_adapter_version"] == "t66_adapter/1.1-provenance"
        assert entry["evidence_verification_status"] == "unverified"

    def test_unverified_is_marked_non_authoritative(self, merge_mod, dbs, tmp_path) -> None:
        rec = {
            "code": "P0420", "url": "https://obd2.com/dtc/p0420-catalyst",
            "source": "obd2.com",
            "title_en": "Catalyst efficiency below threshold bank 1",
            "evidence": EVIDENCE,
        }
        rc, _ = _merge(merge_mod, tmp_path, dbs, [rec])
        assert rc == 0
        entry = json.loads(dbs[0].read_text(encoding="utf-8"))["P0420"]
        assert entry["evidence_verification_status"] == "unverified"
        assert entry["evidence_authoritative"] is False, (
            "unverified harvest output must never be treated as authoritative"
        )

    def test_hash_is_computed_when_provenance_lacks_it(self, merge_mod, dbs, tmp_path) -> None:
        rec = {
            "code": "P0420", "url": "https://obd2.com/dtc/p0420-catalyst",
            "source": "obd2.com",
            "title_en": "Catalyst efficiency below threshold bank 1",
            "evidence": EVIDENCE,
            "provenance": {"verification_status": "unverified"},
        }
        rc, _ = _merge(merge_mod, tmp_path, dbs, [rec])
        assert rc == 0
        entry = json.loads(dbs[0].read_text(encoding="utf-8"))["P0420"]
        assert entry["evidence_sha256"] == EVIDENCE_SHA, (
            "the merge must derive the hash when the adapter did not supply one"
        )

    def test_existing_evidence_chain_is_never_overwritten(self, merge_mod, dbs, tmp_path) -> None:
        original = {
            "title_en": "",
            "evidence": "OPERATOR-VERIFIED ORIGINAL EVIDENCE TEXT",
            "evidence_sha256": "f" * 64,
            "evidence_verification_status": "operator_verified",
            "evidence_authoritative": True,
        }
        dbs[0].write_text(json.dumps({"P0420": original}, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
        rec = {
            "code": "P0420", "url": "https://obd2.com/dtc/p0420-catalyst",
            "source": "obd2.com",
            "title_en": "Catalyst efficiency below threshold bank 1",
            "evidence": EVIDENCE,
        }
        rc, _ = _merge(merge_mod, tmp_path, dbs, [rec])
        assert rc == 0
        entry = json.loads(dbs[0].read_text(encoding="utf-8"))["P0420"]
        assert entry["evidence"] == "OPERATOR-VERIFIED ORIGINAL EVIDENCE TEXT"
        assert entry["evidence_sha256"] == "f" * 64
        assert entry["evidence_verification_status"] == "operator_verified"
        assert entry["evidence_authoritative"] is True

    def test_below_minimum_evidence_is_not_merged(self, merge_mod, dbs, tmp_path) -> None:
        before = json.loads(dbs[0].read_text(encoding="utf-8"))
        rec = {
            "code": "P0420", "url": "https://obd2.com/dtc/p0420-catalyst",
            "source": "obd2.com",
            "title_en": "Catalyst efficiency below threshold bank 1",
            "evidence": "short",
        }
        rc, report = _merge(merge_mod, tmp_path, dbs, [rec])
        assert rc == 0
        assert report["skipped"]["no_evidence"] == 1
        after = json.loads(dbs[0].read_text(encoding="utf-8"))
        assert after == before, "below-minimum evidence must stay dropped"
        assert "evidence" not in after["P0420"]

    def test_j1939_records_carry_the_same_chain(self, merge_mod, dbs, tmp_path) -> None:
        rec = {
            "key": "SPN_100", "spn": 100,
            "url": "https://obd2.com/dtc/spn100",
            "source": "obd2.com",
            "causes": ["Failed sensor or wiring fault at the connector"],
            "evidence": EVIDENCE,
            "provenance": {"retrieved_at": "2026-09-24T10:00:00+00:00",
                           "source_id": "obd2.com/spn100"},
        }
        rc, report = _merge(merge_mod, tmp_path, dbs, [rec], target_db="j1939")
        assert rc == 0 and report["totals"]["j1939_filled"] == 1
        entry = json.loads(dbs[1].read_text(encoding="utf-8"))["spns"]["SPN_100"]
        assert entry["evidence"] == EVIDENCE
        assert entry["evidence_sha256"] == EVIDENCE_SHA
        assert entry["evidence_source_id"] == "obd2.com/spn100"
        assert entry["evidence_retrieved_at"] == "2026-09-24T10:00:00+00:00"
        assert entry["evidence_adapter_version"].startswith("t66_adapter/")
        assert entry["evidence_verification_status"] == "unverified"
        assert entry["evidence_authoritative"] is False


# ---------------------------------------------------------------------------
# 3. End-to-end: adapter output -> merge keeps the chain byte-identically
# ---------------------------------------------------------------------------

class TestAdapterToMergeEndToEnd:
    def test_adapted_record_reaches_the_db_with_its_evidence(
        self, adapter_mod, merge_mod, dbs, tmp_path
    ) -> None:
        adapted = adapter_mod.adapt_dtc(
            _raw_rec(retrieved_at="2026-09-24T10:00:00+00:00"), 20
        )
        assert adapted is not None
        rc, _ = _merge(merge_mod, tmp_path, dbs, [adapted])
        assert rc == 0
        entry = json.loads(dbs[0].read_text(encoding="utf-8"))["P0420"]
        assert entry["evidence"] == adapted["evidence"]
        assert entry["evidence_sha256"] == adapted["provenance"]["evidence_sha256"]
        assert entry["evidence_retrieved_at"] == "2026-09-24T10:00:00+00:00"
        assert entry["evidence_adapter_version"] == adapted["provenance"]["adapter_version"]
        assert entry["evidence_authoritative"] is False
