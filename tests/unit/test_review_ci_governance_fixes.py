"""Tests for the S6 security-advisory exception governance checker.

REVIEW Aşama 4 (S6): `pip-audit --ignore-vuln <ID>` was inlined in CI with no
expiry, owner or justification, so an accepted advisory could stay suppressed
forever. `scripts/check_security_exceptions.py` now requires all four fields
and fails closed once an entry expires.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import importlib.util
import io
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    path = REPO_ROOT / "scripts" / "check_security_exceptions.py"
    spec = importlib.util.spec_from_file_location("check_security_exceptions", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_security_exceptions"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker():
    pytest.importorskip("yaml")
    return _load_module()


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "sec.yaml"
    path.write_text(body, encoding="utf-8")
    return path


_VALID = """\
exceptions:
  - id: ADV-1
    package: foo
    owner: "@owner"
    justification: not reachable
    expires: "2999-12-31"
"""


def test_repository_exceptions_file_is_valid(checker) -> None:
    """The checked-in exceptions file must be structurally valid."""
    entries = checker.load_exceptions(REPO_ROOT / "security-exceptions.yaml")
    assert entries, "expected at least one documented exception"
    assert checker.expired_ids(entries) == []


def test_valid_file_returns_entries(checker, tmp_path: Path) -> None:
    entries = checker.load_exceptions(_write(tmp_path, _VALID))
    assert [entry["id"] for entry in entries] == ["ADV-1"]


def test_missing_file_is_an_error(checker, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        checker.load_exceptions(tmp_path / "nope.yaml")


_LINES = {
    "id": "  - id: ADV-1",
    "owner": "    owner: \"@owner\"",
    "justification": "    justification: not reachable",
    "expires": "    expires: \"2999-12-31\"",
}


@pytest.mark.parametrize("field", list(_LINES))
def test_missing_required_field_is_rejected(checker, tmp_path: Path, field: str) -> None:
    """A required field that is absent from an entry must be rejected."""
    lines = [
        "exceptions:",
        "  - id: ADV-1",
        "    package: foo",
        '    owner: "@owner"',
        "    justification: not reachable",
        '    expires: "2999-12-31"',
    ]
    lines = [line for line in lines if line != _LINES[field]]
    if field == "id":
        # Dropping `- id:` would make the list item indentation invalid; use a
        # placeholder key so the entry stays a mapping but has no `id`.
        lines[1] = "  - package: foo"
        lines.pop(2)
    with pytest.raises(ValueError, match=field):
        checker.load_exceptions(_write(tmp_path, "\n".join(lines) + "\n"))


def test_duplicate_id_is_rejected(checker, tmp_path: Path) -> None:
    body = _VALID + """\
  - id: ADV-1
    package: bar
    owner: "@owner"
    justification: dup
    expires: "2999-12-31"
"""
    with pytest.raises(ValueError, match="duplicate"):
        checker.load_exceptions(_write(tmp_path, body))


@pytest.mark.parametrize("bad_date", ["31-12-2999", "2999/12/31", "soon", "2999-13-99"])
def test_malformed_expiry_is_rejected(checker, tmp_path: Path, bad_date: str) -> None:
    body = _VALID.replace('"2999-12-31"', f'"{bad_date}"')
    with pytest.raises(ValueError, match="expires"):
        checker.load_exceptions(_write(tmp_path, body))


def test_expired_entry_is_flagged(checker, tmp_path: Path) -> None:
    entries = checker.load_exceptions(_write(tmp_path, _VALID))
    assert checker.expired_ids(entries, dt.date(3000, 1, 1)) == ["ADV-1"]


def test_expiry_on_today_is_flagged(checker, tmp_path: Path) -> None:
    body = _VALID.replace('"2999-12-31"', '"2026-06-15"')
    entries = checker.load_exceptions(_write(tmp_path, body))
    assert checker.expired_ids(entries, dt.date(2026, 6, 15)) == ["ADV-1"]


def _capture_raw_stdout(call) -> bytes:
    """Run ``call`` and return the exact bytes it wrote to stdout.

    ``--print-flags`` writes to ``sys.stdout.buffer`` on purpose (so the
    payload is never newline-translated), which a text-mode ``capsys``
    capture cannot represent faithfully. Redirecting to a binary buffer and
    reading it back INSIDE the context keeps the raw payload — including any
    ``\r`` or trailing space — visible to the assertion.
    """
    buf = io.BytesIO()
    wrapper = io.TextIOWrapper(buf, encoding="utf-8", newline="")
    with contextlib.redirect_stdout(wrapper):
        call()
        wrapper.flush()
    return buf.getvalue()

def test_print_flags_emits_ignore_vuln(checker, tmp_path: Path) -> None:
    """The flag payload must survive CI command substitution verbatim.

    CI consumes this as `$(python scripts/check_security_exceptions.py
    --print-flags)` inside an UNQUOTED substitution. Bash strips the trailing
    newline but NOT a carriage return, so a Windows-style ``\\r\\n`` (or a
    trailing space from joining) turns the last ID into ``ADV-1\\r`` and
    pip-audit then aborts with "couldn't find a supported project file".

    Asserted on raw bytes because that is exactly what the shell sees — a
    text-mode `capsys` capture would hide the CRLF/space defect that broke CI.
    """
    path = _write(tmp_path, _VALID)
    out = _capture_raw_stdout(
        lambda: checker.main(["--path", str(path), "--print-flags"])
    )

    assert out == b"--ignore-vuln ADV-1", f"unexpected flag payload: {out!r}"
    assert b"\r" not in out, "flag payload must not contain a carriage return"
    assert b"\n" not in out, "flag payload must not contain a newline"
    assert not out.endswith(b" "), "flag payload must not end with a space"


def test_print_flags_payload_matches_the_committed_exceptions_file(checker) -> None:
    """The real repo file must emit a clean, single-line, CR-free payload."""
    from scripts.check_security_exceptions import DEFAULT_PATH

    out = _capture_raw_stdout(
        lambda: checker.main(["--path", str(DEFAULT_PATH), "--print-flags"])
    )

    assert b"\r" not in out and b"\n" not in out, f"payload must be one CR/LF-free line: {out!r}"
    assert out and not out.endswith(b" "), f"payload must not end with a space: {out!r}"

    # The payload is a space-separated sequence of `--ignore-vuln <ID>` pairs;
    # every `--ignore-vuln` must be followed by a non-empty, flag-free value.
    tokens = out.split(b" ")
    for index, token in enumerate(tokens):
        if token == b"--ignore-vuln":
            assert index + 1 < len(tokens), "dangling --ignore-vuln with no value"
            value = tokens[index + 1]
            assert value and not value.startswith(b"-"), f"bad advisory id: {value!r}"


def test_main_fails_closed_on_expired_entry(checker, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    body = _VALID.replace('"2999-12-31"', '"2000-01-01"')
    path = _write(tmp_path, body)
    assert checker.main(["--path", str(path)]) == 1
    assert "EXPIRED" in capsys.readouterr().err


def test_ci_files_reference_the_governance_checker() -> None:
    """Both pipelines must validate exceptions instead of inlining ignore flags."""
    github = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    gitlab = (REPO_ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    for name, text in (("github", github), ("gitlab", gitlab)):
        assert "check_security_exceptions.py" in text, f"{name} must run the checker"
        assert "--ignore-vuln PYSEC-2026-2447" not in text, f"{name} must not inline the exception"


def test_pip_audit_step_forces_a_word_splitting_shell() -> None:
    """The pip-audit step relies on word-splitting of `$(... --print-flags)`.

    The PyTest & Conformance job runs on windows-latest, where `run:` defaults
    to pwsh. PowerShell does NOT word-split a command substitution, so the
    payload reached pip-audit as a single argv entry and the job failed with
    "couldn't find a supported project file in --ignore-vuln PYSEC-2026-2447".
    The step must therefore pin a POSIX shell. Regression guard: if someone
    drops the `shell:` key (or switches it back to pwsh), this test fails.
    """
    import re

    github = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    # Isolate the pip-audit step block: from its `- name:` up to the next
    # step at the same indentation.
    match = re.search(
        r"-\s*name:\s*Run pip-audit Dependency Scan\n(?P<body>(?:[ \t]+.*\n|\n)*)",
        github,
    )
    assert match, "pip-audit step not found in ci.yml"
    body = match.group("body")

    assert "print-flags" in body, "pip-audit step must use --print-flags"
    shell = re.search(r"^\s*shell:\s*(\S+)\s*$", body, re.MULTILINE)
    assert shell, (
        "the pip-audit step on windows-latest MUST pin `shell:` — without it "
        "GitHub defaults to pwsh, which passes the flag payload as one argv "
        "entry and breaks the scan"
    )
    assert shell.group(1) in {"bash", "sh"}, (
        f"pip-audit needs a word-splitting shell, got {shell.group(1)!r}"
    )


def test_coverage_threshold_consistent_across_pipelines() -> None:
    """C4: the GitLab gate drifted to 79 while GitHub enforced 80.

    The threshold was later raised to match the MEASURED coverage (82%). The
    invariant under test is that BOTH pipelines enforce the SAME floor and that
    it is not the stale 79 — not the specific number, which legitimately rises
    as coverage improves.
    """
    import re

    github = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    gitlab = (REPO_ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")

    assert "cov-fail-under=79" not in gitlab, "C4 regression: GitLab drifted back to 79"

    gh_floors = {int(n) for n in re.findall(r"cov-fail-under=(\d+)", github)}
    gl_floors = {int(n) for n in re.findall(r"cov-fail-under=(\d+)", gitlab)}

    assert gh_floors, "GitHub pipeline lost its coverage gate"
    assert gl_floors, "GitLab pipeline lost its coverage gate"
    assert gh_floors == gl_floors, f"pipelines disagree on the coverage floor: {gh_floors} vs {gl_floors}"
    # Raised from the stale 80 to the measured level during the residual-item
    # pass. MEASURED coverage is 81.95% (displayed as 82%), so the floor is 81 —
    # one point of honest headroom. A floor of exactly 82 would fail on the very
    # tree that justified it, which is why it is not 82.
    assert gh_floors >= {81}, f"coverage floor regressed below the measured level: {gh_floors}"


def test_s4_linux_runs_the_full_suite() -> None:
    """S4: a full-suite Linux job must exist, not just the vcan slice."""
    import yaml

    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert "test-linux-full" in jobs, "full-suite Linux job missing"
    steps = jobs["test-linux-full"]["steps"]
    body = "\n".join(str(step.get("run", "")) for step in steps)
    assert "pytest" in body
    # The full job must NOT restrict to the hardware slice.
    assert "-k " not in body, "the full-suite job must not select a subset"
