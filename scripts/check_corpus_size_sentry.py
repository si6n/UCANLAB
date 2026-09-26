# -*- coding: utf-8 -*-
"""Repository Corpus Size Sentry & LFS Partitioning Gate (Aksiyon 37).

Enforces:
- Individual non-LFS asset size ceiling (default 100 MB per file, Git limit).
- Cumulative directory budget (data/traces <= 350 MB, data/dbc <= 50 MB, data/diagnostics <= 150 MB).
- Scans git-tracked files in data/ to prevent uncommitted local backups (.bak_*) from triggering false positives.
- Invariant: Do not convert repository object storage to SHA-256 raw blobs.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

DEFAULT_MAX_SINGLE_FILE_MB = 100.0
DIRECTORY_BUDGETS_MB = {
    "data/traces": 350.0,
    "data/dbc": 50.0,
    "data/diagnostics": 150.0,
    "data/knowledge": 20.0,
}


def get_tracked_files(repo_root: Path) -> list[Path]:
    """Get list of git-tracked files under data/, falling back to filesystem traversal."""
    try:
        res = subprocess.run(
            ["git", "ls-files", "data/"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
        )
        tracked = [repo_root / line.strip() for line in res.stdout.splitlines() if line.strip()]
        return [f for f in tracked if f.is_file()]
    except Exception:
        # Fallback to filesystem traversal ignoring common backup patterns
        return [
            p for p in repo_root.glob("data/**/*")
            if p.is_file() and not p.name.endswith((".bak", ".tmp")) and ".bak_t" not in p.name
        ]


def check_corpus_sizes(repo_root: Path) -> tuple[bool, list[str]]:
    """Audit asset file sizes against CI ceilings."""
    violations: list[str] = []
    tracked_files = get_tracked_files(repo_root)

    # Check individual file sizes
    for item in tracked_files:
        size_mb = item.stat().st_size / (1024 * 1024)
        if size_mb > DEFAULT_MAX_SINGLE_FILE_MB:
            violations.append(
                f"File exceeds non-LFS threshold ({size_mb:.2f} MB > {DEFAULT_MAX_SINGLE_FILE_MB} MB): {item.relative_to(repo_root)}"
            )

    # Check cumulative budgets
    dir_sizes: dict[str, float] = {}
    for item in tracked_files:
        rel = item.relative_to(repo_root).as_posix()
        for budget_dir in DIRECTORY_BUDGETS_MB:
            if rel.startswith(budget_dir):
                size_mb = item.stat().st_size / (1024 * 1024)
                dir_sizes[budget_dir] = dir_sizes.get(budget_dir, 0.0) + size_mb

    for budget_dir, budget_mb in DIRECTORY_BUDGETS_MB.items():
        actual_mb = dir_sizes.get(budget_dir, 0.0)
        if actual_mb > budget_mb:
            violations.append(
                f"Directory exceeds budget ({actual_mb:.2f} MB > {budget_mb:.2f} MB): {budget_dir}"
            )

    return len(violations) == 0, violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Corpus Size Sentry & LFS Partitioning Gate")
    parser.add_argument("--root", type=str, default=".", help="Repository root path")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    print(f"[*] Auditing repository assets under: {root}")

    passed, violations = check_corpus_sizes(root)
    if not passed:
        print("[-] FAIL: Size budget violations detected:")
        for v in violations:
            print(f"    - {v}")
        return 1

    print("[+] PASS: All corpus asset sizes within LFS and CI budgets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
