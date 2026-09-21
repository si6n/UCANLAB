"""Tests for the S6 security-advisory exception governance checker.

REVIEW Aşama 4 (S6): `pip-audit --ignore-vuln <ID>` was inlined in CI with no
expiry, owner or justification, so an accepted advisory could stay suppressed
forever. `scripts/check_security_exceptions.py` now requires all four fields
and fails closed once an entry expires.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
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


def test_print_flags_emits_ignore_vuln(checker, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(tmp_path, _VALID)
    assert checker.main(["--path", str(path), "--print-flags"]) == 0
    assert "--ignore-vuln ADV-1" in capsys.readouterr().out


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
