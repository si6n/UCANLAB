#!/usr/bin/env python
"""I-15: gate `mypy src` against the accepted legacy error baseline.

The repository carries a large, pre-existing set of strict-mypy errors. A plain
`mypy src scripts` CI step is therefore red on arrival and can never be
enforced, so typing is effectively unchecked. This script turns the legacy
noise into a ratchet instead:

  * every error currently reported by ``mypy src`` that is NOT recorded in
    ``mypy-baseline.txt`` fails the run — a NEW error can never be merged;
  * an error recorded in the baseline that is no longer reported is an
    informational NOTE, not a failure — fixing errors is always allowed, and
    the baseline may only shrink deliberately (``--update``);
  * comparison is on the normalized ``relative/path.py:LINE: [code]`` triple,
    so the baseline is independent of message wording, mypy patch version and
    platform path separators (mypy notes are ignored; they carry no code).

``mypy-baseline.txt`` is a committed data file: one normalized error per line,
sorted, with the duplicates mypy reports for the same line/code kept as
repeated lines.

Usage
-----
    python scripts/check_mypy_baseline.py            # gate (CI)
    python scripts/check_mypy_baseline.py --update   # accept the current state

Exit codes
----------
    0  no new errors (possibly with informational "fixed" notes)
    1  at least one new error was reported
    2  the typecheck could not be evaluated (mypy missing, config/usage error,
       or unparseable output) — fail loudly instead of reporting a clean pass

Stdlib only.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO_ROOT / "mypy-baseline.txt"
MYPY_TARGET = "src"
MYPY_ARGV = [
    sys.executable,
    "-m",
    "mypy",
    MYPY_TARGET,
    "--no-error-summary",
    "--show-error-codes",
    "--no-pretty",
]

# `path:line: error: <message> [code]` — mypy's classic (non-grouped) format.
# The message itself may contain brackets (e.g. `list[<type>]`), so the code is
# the LAST bracketed token on the line. An optional column group is tolerated so
# a future `--show-column-numbers` default cannot silently break the parser.
_ERROR_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+)(?::\d+)?: error: .*\[(?P<code>[A-Za-z0-9_-]+)\]\s*$"
)
_ERROR_WITHOUT_CODE_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+)(?::\d+)?: error: ")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_NO_CODE = "no-code"


def _fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)


def _normalize_path(raw: str) -> str:
    """`src\\ui\\desktop_app.py` / `C:\\repo\\src\\x.py` -> `src/ui/desktop_app.py`."""
    text = raw.strip().replace("\\", "/")
    if text.startswith("/") or _DRIVE_RE.match(text):
        try:
            return Path(text).resolve().relative_to(REPO_ROOT).as_posix()
        except (OSError, ValueError):
            return text
    return text


def parse_errors(output: str) -> Counter[str]:
    """Normalize mypy's error lines into a ``path:line: [code]`` -> count map."""
    found: Counter[str] = Counter()
    for raw_line in output.splitlines():
        line = _ANSI_RE.sub("", raw_line).rstrip()
        match = _ERROR_RE.match(line)
        if match:
            code = match.group("code")
        else:
            # An error carrying no code must never be silently dropped (that
            # would let it pass the gate); keep it under an explicit sentinel.
            match = _ERROR_WITHOUT_CODE_RE.match(line)
            if not match:
                continue
            code = _NO_CODE
        key = f"{_normalize_path(match.group('path'))}:{match.group('line')}: [{code}]"
        found[key] += 1
    return found


def load_baseline(path: Path) -> Counter[str]:
    """Read the committed baseline; blank and ``#`` comment lines are ignored."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return Counter(line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#"))


def write_baseline(path: Path, errors: Counter[str]) -> int:
    """Rewrite the baseline from the current run; returns the line count."""
    lines = sorted(errors.elements())
    # newline="\n" keeps the file byte-identical on Windows and Linux.
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8", newline="\n")
    return len(lines)


def run_mypy() -> tuple[int, str, str]:
    """Run mypy from the repository root; returns (returncode, stdout, stderr)."""
    env = dict(os.environ)
    # Pin the child's output encoding so the parse is platform-independent.
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        MYPY_ARGV,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _dump(stdout: str, stderr: str) -> None:
    for name, text in (("stdout", stdout), ("stderr", stderr)):
        if text.strip():
            print(f"--- mypy {name} ---", file=sys.stderr)
            print(text.rstrip(), file=sys.stderr)


def _format_keys(errors: Counter[str]) -> list[str]:
    return [f"  {key}" if count == 1 else f"  {key} (x{count})" for key, count in sorted(errors.items())]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate `mypy src` against mypy-baseline.txt (I-15).")
    parser.add_argument(
        "--update",
        action="store_true",
        help="rewrite mypy-baseline.txt from the current mypy run (deliberate baseline refresh)",
    )
    args = parser.parse_args(argv)

    try:
        returncode, stdout, stderr = run_mypy()
    except OSError as exc:  # mypy could not even be launched
        _fail(f"could not run mypy ({exc})")
        return 2

    if "No module named" in stderr and "mypy" in stderr:
        _fail("mypy is not installed for this interpreter; install requirements-dev.txt")
        _dump(stdout, stderr)
        return 2
    if returncode not in (0, 1):
        _fail(f"mypy exited with code {returncode} (config/usage problem) — the typecheck was not evaluated")
        _dump(stdout, stderr)
        return 2

    current = parse_errors(stdout)
    if returncode == 1 and not current:
        _fail("mypy reported errors but none could be parsed — the output format changed?")
        _dump(stdout, stderr)
        return 2

    if args.update:
        # A missing baseline is the bootstrap case, not an error: --update is
        # how the very first baseline gets created.
        previous = sum(load_baseline(BASELINE_PATH).values()) if BASELINE_PATH.is_file() else 0
        written = write_baseline(BASELINE_PATH, current)
        print(f"OK: wrote {written} error line(s) to {BASELINE_PATH.name} (was {previous})")
        return 0

    try:
        baseline = load_baseline(BASELINE_PATH)
    except OSError as exc:
        _fail(
            f"cannot read baseline {BASELINE_PATH}: {exc} "
            "(create it with: python scripts/check_mypy_baseline.py --update)"
        )
        return 2

    new_errors = current - baseline
    fixed_errors = baseline - current

    if new_errors:
        _fail(
            f"{sum(new_errors.values())} new mypy error(s) not present in {BASELINE_PATH.name} "
            "(fix them, or refresh the baseline deliberately):"
        )
        for line in _format_keys(new_errors):
            print(line, file=sys.stderr)
        print(
            "  to accept the new baseline deliberately: python scripts/check_mypy_baseline.py --update",
            file=sys.stderr,
        )
        return 1

    if fixed_errors:
        print(
            f"NOTE: {sum(fixed_errors.values())} baseline error(s) are no longer reported "
            "(the baseline may only shrink deliberately; --update records it):"
        )
        for line in _format_keys(fixed_errors):
            print(line)

    print(
        f"OK: no new mypy errors "
        f"({sum(current.values())} reported, {len(current)} unique; baseline {sum(baseline.values())})"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
