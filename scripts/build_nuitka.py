"""Nuitka C++ Native Standalone Compilation Pipeline for Universal CAN Platform.

Compiles all core Python modules (src.*) into native C++ machine binaries with Link Time
Optimization (LTO), stripping all Python source code (.py/.pyc) to prevent decompilation.
Bundles the React+Tailwind UI bundle and 133 curated DBCs into the standalone distribution.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path


def ensure_frontend_built(frontend_dir: Path) -> None:
    """Ensure React/Tailwind frontend is built and dist/index.html exists."""
    dist_index = frontend_dir / "dist" / "index.html"
    if not dist_index.is_file():
        print(f"Frontend dist not found at {dist_index}. Building via npm run build...")
        subprocess.check_call(["cmd", "/c", "npm run build"], cwd=frontend_dir)
        if not dist_index.is_file():
            raise FileNotFoundError(f"Frontend build failed: {dist_index} missing after build.")
    print(f"Frontend bundle verified: {dist_index}")


def run_nuitka_build(onefile: bool = False, console: bool = False) -> int:
    """Execute Nuitka native C++ compilation command."""
    root_dir = Path(__file__).parent.parent.resolve()
    entry_point = root_dir / "src" / "main.py"
    output_dir = root_dir / "dist"
    frontend_dir = root_dir / "src" / "ui" / "frontend"
    frontend_dist = frontend_dir / "dist"
    dbc_dir = root_dir / "data" / "dbc"
    # H-9 (P1-11): external diagnostic databases must ship in the bundle.
    diagnostics_dir = root_dir / "data" / "diagnostics"

    if not entry_point.is_file():
        raise FileNotFoundError(f"Entry point not found: {entry_point}")

    ensure_frontend_built(frontend_dir)

    mode_flag = "--onefile" if onefile else "--standalone"
    console_mode = "force" if console else "disable"

    # Y-13 (REVIEW-QWEN): build cache lives in a throwaway temp dir, never
    # inside the repository — a committed/poisoned cache cannot influence
    # the produced binary, and no stale artifacts leak into the repo.
    cache_dir = Path(tempfile.mkdtemp(prefix="nuitka_cache_"))

    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        mode_flag,
        f"--cache-dir={cache_dir}",
        "--lto=yes",
        "--msvc=latest",
        "--jobs=8",
        "--output-filename=ucanlab.exe",
        f"--output-dir={output_dir}",
        f"--windows-console-mode={console_mode}",
        "--include-package=src",
        "--include-package-data=cantools",
        "--nofollow-import-to=pytest,unittest,hypothesis,setuptools",
        f"--include-data-dir={frontend_dist}=src/ui/frontend/dist",
    ]

    if dbc_dir.is_dir():
        cmd.append(f"--include-data-dir={dbc_dir}=data/dbc")

    # H-9 (P1-11): without data/diagnostics the compiled binary silently
    # drops ~98% of the DTC knowledge base to debug-level fallbacks.
    if diagnostics_dir.is_dir():
        cmd.append(f"--include-data-dir={diagnostics_dir}=data/diagnostics")

    icon_file = root_dir / "assets" / "icon.ico"
    if icon_file.is_file():
        cmd.append(f"--windows-icon-from-ico={icon_file}")

    cmd.extend([
        "--assume-yes-for-downloads",
        "--remove-output",
        str(entry_point),
    ])

    print("=" * 70)
    print("UNIVERSAL CAN PLATFORM - NUITKA NATIVE C++ BUILD PIPELINE")
    print(f"Target: {'OneFile' if onefile else 'Standalone Folder'}")
    print(f"Console Mode: {console_mode}")
    print("Source Protection: All src.* modules compiled to C++ machine code")
    print("=" * 70)
    print(f"Command: {' '.join(cmd)}\n")

    exit_code = subprocess.call(cmd)

    # Emit a SHA-256 manifest next to the artifact for tamper-evident
    # distribution (also covers the PyInstaller path via dist/ contents).
    try:
        exe_path = output_dir / "ucanlab.exe"
        if exe_path.is_file():
            digest = hashlib.sha256(exe_path.read_bytes()).hexdigest()
            (output_dir / "SHA256SUMS").write_text(f"{digest}  {exe_path.name}\n", encoding="utf-8")
            print(f"SHA-256: {digest}  ({exe_path.name})")
    except OSError as exc:
        print(f"[WARN] Could not write SHA256SUMS: {exc}")

    try:
        import shutil

        shutil.rmtree(cache_dir, ignore_errors=True)
    except Exception:
        pass

    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Universal CAN Platform with Nuitka Native Compiler")
    parser.add_argument("--onefile", action="store_true", help="Build single standalone .exe file")
    parser.add_argument("--console", action="store_true", help="Keep console window open for debugging")
    args = parser.parse_args()
    return run_nuitka_build(onefile=args.onefile, console=args.console)


if __name__ == "__main__":
    sys.exit(main())
