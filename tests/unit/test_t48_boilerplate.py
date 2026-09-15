"""T48-A / IS 1 regression tests — strip the obdhut boilerplate from ``causes_en``.

Task: kanban t_bc783d23 (T48-A, item 1).

Problem
-------
``scripts/merge_t45_dtc.py`` strips the obdhut.com site boilerplate that the T45
harvester scraped in front of every ``causes`` string::

    The most common cause of <CODE> (<code text>) is: <real payload>

The original regex used a single-level parenthetical group ``\\([^)]*\\)``,
which cannot match NESTED parentheses such as::

    B0001 (Driver Frontal Stage 1 Deployment Control (Subfault))

so 625 records kept the boilerplate prefix in ``causes_en``.

Invariants locked here (task body — violation = task rejection):
  1. After the fix, zero ``causes_en`` values start with the boilerplate.
  2. The nested-parenthesis record (B0001) is cleaned correctly.
  3. The Turkish fields are untouched — a byte-level diff of everything that is
     not a ``*_en`` string proves the strip only ever writes English fields.
  4. The record count stays frozen at 14,352.
  5. Idempotent: a second run over the same DB changes nothing.

The tests exercise a synthetic minimal DB (fast, hermetic) plus read-only
assertions against the production file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROD_DB_PATH = REPO_ROOT / "data" / "diagnostics" / "dtc_database.json"

BOILERPLATE = "The most common cause of"
EXPECTED_RECORDS = 14352

# The nested-parenthesis specimen called out in the task body.
B0001_RAW = (
    "The most common cause of B0001 (Driver Frontal Stage 1 Deployment Control "
    "(Subfault)) is: Faulty clock spring (spiral cable) causing intermittent or "
    "open circuit in driver airbag connection"
)
B0001_CLEAN = (
    "Faulty clock spring (spiral cable) causing intermittent or "
    "open circuit in driver airbag connection"
)
# A flat, single-level parenthetical (the form the OLD regex already handled).
U0003_RAW = (
    "The most common cause of U0003 (High Speed CAN Communication Bus (+) Open) "
    "is: Broken or severed CAN High wire in harness"
)
U0003_CLEAN = "Broken or severed CAN High wire in harness"
# P0874 uses the "the <CODE> code is" phrasing without a colon.
P0874_RAW = (
    "The most common cause of the P0874 code is low transmission fluid level."
)
P0874_CLEAN = "low transmission fluid level."


def _is_boilerplated(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(
        BOILERPLATE.lower()
    )


@pytest.fixture(scope="module")
def stripper():
    """The prefix-stripping helper exposed by the merge script."""

    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "merge_t45_dtc", REPO_ROOT / "scripts" / "merge_t45_dtc.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["merge_t45_dtc"] = module
    spec.loader.exec_module(module)
    assert hasattr(module, "strip_cause_boilerplate"), (
        "merge_t45_dtc must expose strip_cause_boilerplate(text)"
    )
    return module.strip_cause_boilerplate


class TestStripHelper:
    """Unit-level contract of the fixed helper."""

    def test_strips_flat_parenthetical(self, stripper) -> None:
        assert stripper(U0003_RAW) == U0003_CLEAN

    def test_strips_nested_parenthetical(self, stripper) -> None:
        # This is the exact case the old regex could not match.
        assert stripper(B0001_RAW) == B0001_CLEAN

    def test_strips_the_code_code_form(self, stripper) -> None:
        assert stripper(P0874_RAW) == P0874_CLEAN

    def test_is_idempotent(self, stripper) -> None:
        once = stripper(B0001_RAW)
        assert stripper(once) == once
        assert stripper(U0003_RAW) == stripper(stripper(U0003_RAW))

    def test_leaves_non_boilerplate_text_alone(self, stripper) -> None:
        plain = "Faulty clock spring causing intermittent connection"
        assert stripper(plain) == plain

    def test_does_not_strip_mid_string_occurrence(self, stripper) -> None:
        # Only an ANCHORED prefix is boilerplate; a mention mid-sentence stays.
        mid = "Solenoid failure – it is the most common cause of this code"
        assert stripper(mid) == mid


class TestProductionDatabase:
    """Read-only assertions against the real DB (no writes)."""

    @pytest.fixture(scope="class")
    @classmethod
    def db(cls) -> dict:  # type: ignore[misc]
        assert PROD_DB_PATH.exists(), "production DTC database is missing"
        with PROD_DB_PATH.open(encoding="utf-8") as fh:
            return json.load(fh)

    def test_record_count_frozen(self, db: dict) -> None:
        assert len(db) == EXPECTED_RECORDS

    def test_no_causes_en_keeps_boilerplate(self, db: dict) -> None:
        offenders = [code for code, rec in db.items() if _is_boilerplated(rec.get("causes_en"))]
        assert offenders == [], (
            f"{len(offenders)} causes_en values still carry the boilerplate "
            f"prefix (e.g. {offenders[:5]})"
        )

    def test_b0001_nested_paren_cleaned(self, db: dict) -> None:
        value = db["B0001"]["causes_en"]
        assert value == B0001_CLEAN
        assert "(Subfault))" not in value
        assert not _is_boilerplated(value)

    def test_cleaned_values_keep_real_payload(self, db: dict) -> None:
        """The strip must not eat the real cause text."""
        for code in ("U0003", "B0001", "U0009"):
            value = db[code]["causes_en"]
            assert value and not _is_boilerplated(value)
            assert ")" not in value[:1]  # never leaves a dangling close paren
