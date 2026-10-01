"""M-05 — DBC identifier sanitization for exported discovery databases.

Signal names in an exported DBC can be derived from live bus data, so they are
untrusted text. An unsanitized name can produce a DBC that fails to reparse
(spaces, punctuation, leading digits) or inject extra DBC statements.

The workbench exports DBC files from the Python discovery engine
(``src/engine/discovery/dbc_builder.py``); the browser-side
``reverseEngineeringEngine.ts`` these tests used to drive was removed with the
old frontend (B8), so the same contract is now asserted on the code that ships.
"""

from __future__ import annotations

import re

import cantools
import pytest

from src.engine.discovery.dbc_builder import DBC_IDENTIFIER_MAX, DbcBuilder, _sanitize_c_identifier
from src.engine.discovery.hypotheses import Hypothesis, IdReport

# The DBC grammar's identifier rule (letters/digits/underscore, no leading digit).
DBC_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

INPUTS = [
    "EngineSpeed", "engine speed (rpm)", "a-b.c/d", "  spaced  ", "3WayCatTemp", "9lives", "", "   ",
    "üöçşğÜÖÇŞĞ", "name;DROP TABLE", 'a".b', "a\nb\tc", "x" * 200, "__x__", "SG_ X : 0|8@1+ (1,0) [0|1]",
]


@pytest.mark.parametrize("raw", INPUTS)
def test_every_output_is_dbc_safe_and_bounded(raw: str) -> None:
    out = _sanitize_c_identifier(raw)
    assert DBC_IDENTIFIER_RE.match(out), f"{raw!r} produced {out!r}"
    assert 0 < len(out) <= DBC_IDENTIFIER_MAX


def test_spaces_and_punctuation_are_collapsed() -> None:
    assert _sanitize_c_identifier("engine speed (rpm)") == "engine_speed_rpm"
    assert _sanitize_c_identifier("a-b.c/d") == "a_b_c_d"
    assert _sanitize_c_identifier("  spaced  ") == "spaced"


def test_leading_digit_is_prefixed() -> None:
    assert _sanitize_c_identifier("3WayCatTemp") == "sig_3WayCatTemp"
    assert _sanitize_c_identifier("9lives") == "sig_9lives"


def test_empty_and_non_ascii_fall_back() -> None:
    for raw in ("", "   ", "üöçşğÜÖÇŞĞ", None):
        assert _sanitize_c_identifier(raw) == "signal"  # type: ignore[arg-type]


def test_injection_attempts_cannot_escape_the_identifier() -> None:
    for hostile in ("name;DROP TABLE", 'a".b', "a\nb\tc", "SG_ X : 0|8@1+ (1,0) [0|1]"):
        out = _sanitize_c_identifier(hostile)
        assert DBC_IDENTIFIER_RE.match(out)
        assert not set(out) & set(';"\n\t :|@()[]')


def test_long_names_are_truncated_not_rejected() -> None:
    assert _sanitize_c_identifier("x" * 200) == "x" * DBC_IDENTIFIER_MAX


def test_exported_dbc_reparses_with_hostile_and_colliding_names() -> None:
    """End to end: hostile names survive dump → cantools reparse as single identifiers."""
    long_name = "y" * 100
    hyps = [
        Hypothesis(htype="SIGNAL", start_bit=0, length=8, name='rpm";\nBO_ 1 X: 8 Y', confidence=0.9),
        Hypothesis(htype="SIGNAL", start_bit=8, length=8, name=long_name, confidence=0.9),
        Hypothesis(htype="SIGNAL", start_bit=16, length=8, name=long_name, confidence=0.9),
    ]
    report = IdReport(arbitration_id=0x123, frame_count=10, rate_hz=10.0, dlc=8, hypotheses=hyps)
    text = DbcBuilder.build_database({0x123: report}).as_dbc_string()
    reparsed = cantools.database.load_string(text, database_format="dbc")
    assert len(reparsed.messages) == 1
    names = [s.name for s in reparsed.messages[0].signals]
    assert len(names) == 3 and len(set(names)) == 3
    assert all(DBC_IDENTIFIER_RE.match(n) and len(n) <= DBC_IDENTIFIER_MAX for n in names)
