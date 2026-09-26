"""M-05 — DBC identifier sanitization (rendered through the real TS module).

`src/ui/frontend/src/services/reverseEngineeringEngine.ts` emits
``SG_ <name>`` signal lines and ``<name>_Discovered.dbc`` filenames from
discovered signal names, which are derived from live bus data and are
therefore untrusted text. An unsanitized name can produce a DBC that fails
to reparse (spaces, punctuation, leading digits) — or worse, inject extra
DBC statements.

This repo has no vitest, so the real TypeScript module is transpiled and
executed in-process by ``tests/fixtures/render_dbc_sanitizer.cjs`` and the
resulting rows are asserted here. Skips when the frontend toolchain is
absent, so a Python-only environment still runs the rest of the suite.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = REPO_ROOT / "src" / "ui" / "frontend"
HARNESS = REPO_ROOT / "tests" / "fixtures" / "render_dbc_sanitizer.cjs"

# The DBC grammar's identifier rule (letters/digits/underscore, no leading digit).
DBC_IDENTIFIER_RE = r"^[A-Za-z_][A-Za-z0-9_]*$"


def _require_toolchain() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; sanitizer proof unavailable")
    if not (FRONTEND / "node_modules" / "typescript").exists():
        pytest.skip("frontend typescript dep missing; run npm install first")
    return node


@pytest.fixture(scope="module")
def rows() -> list[dict[str, object]]:
    node = _require_toolchain()
    proc = subprocess.run(
        [node, str(HARNESS), str(FRONTEND)],
        cwd=str(FRONTEND),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"sanitizer harness failed:\nstdout: {proc.stdout[-2000:]}\n"
            f"stderr: {proc.stderr[-2000:]}"
        )
    payload = json.loads(proc.stdout)
    return payload["rows"]


class TestDbcSanitizer:
    def test_every_output_is_dbc_safe(self, rows: list[dict[str, object]]) -> None:
        """The core contract: whatever comes in, the identifier is valid."""
        import re

        offenders = [
            row
            for row in rows
            if not row["dbcSafe"]
            or not re.match(DBC_IDENTIFIER_RE, str(row["output"]))
        ]
        assert offenders == [], f"non-DBC-safe sanitizer outputs: {offenders}"

    def test_output_length_is_bounded(self, rows: list[dict[str, object]]) -> None:
        assert all(row["lengthOk"] for row in rows), "an output exceeded the 64-char cap"

    def test_spaces_and_punctuation_are_collapsed(self, rows: list[dict[str, object]]) -> None:
        by_input = {str(row["input"]): str(row["output"]) for row in rows}
        assert by_input["engine speed (rpm)"] == "engine_speed_rpm"
        assert by_input["a-b.c/d"] == "a_b_c_d"
        assert by_input["  spaced  "] == "spaced"

    def test_leading_digit_is_prefixed(self, rows: list[dict[str, object]]) -> None:
        by_input = {str(row["input"]): str(row["output"]) for row in rows}
        assert by_input["3WayCatTemp"].startswith("_")
        assert by_input["9lives"].startswith("_")

    def test_empty_and_non_ascii_fall_back(self, rows: list[dict[str, object]]) -> None:
        by_input = {str(row["input"]): str(row["output"]) for row in rows}
        assert by_input[""] == "Discovered_Signal"
        assert by_input["   "] == "Discovered_Signal"
        assert by_input["üöçşğÜÖÇŞĞ"] == "Discovered_Signal"

    def test_injection_attempts_cannot_escape_the_identifier(
        self, rows: list[dict[str, object]]
    ) -> None:
        """A payload trying to close the SG_ statement must stay one token."""
        import re

        by_input = {str(row["input"]): str(row["output"]) for row in rows}
        for hostile in ('name;DROP TABLE', 'a".b', "a\nb\tc"):
            out = by_input[hostile]
            assert re.match(DBC_IDENTIFIER_RE, out), f"{hostile!r} produced {out!r}"
            assert ";" not in out and '"' not in out and "\n" not in out

    def test_long_names_are_truncated_not_rejected(self, rows: list[dict[str, object]]) -> None:
        by_input = {str(row["input"]): str(row["output"]) for row in rows}
        long_out = by_input["x" * 200]
        assert len(long_out) == 64
        assert long_out == "x" * 64
