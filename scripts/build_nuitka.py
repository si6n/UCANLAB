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


def _resolve_npm() -> str:
    """Resolve npm to an absolute path (supply-chain: PATH hijack guard).

    Honors NPM_PATH env override; otherwise shutil.which + resolve with logging.
    Absolute path is mandatory — never exec a bare relative name.
    """
    import os
    import shutil

    env_npm = os.environ.get("NPM_PATH")
    if env_npm:
        resolved = str(Path(env_npm).resolve())
        print(f"[supply-chain] npm via NPM_PATH: {env_npm} -> {resolved}")
        return resolved
    found = shutil.which("npm")
    if found is None:
        raise FileNotFoundError("npm bulunamadi (veya NPM_PATH env ayarlayin)")
    resolved = str(Path(found).resolve())
    print(f"[supply-chain] npm via PATH: {found} -> {resolved}")
    return resolved


def ensure_frontend_built(frontend_dir: Path) -> None:
    """Ensure React/Tailwind frontend is built and dist/index.html exists."""
    dist_index = frontend_dir / "dist" / "index.html"
    if not dist_index.is_file():
        print(f"Frontend dist not found at {dist_index}. Building via npm run build...")
        # supply-chain: invoke npm binary directly (list form, absolute path);
        # never use `cmd /c "<string>"` shell-string form (hijack/injection prone).
        subprocess.check_call([_resolve_npm(), "run", "build"], cwd=frontend_dir)
        if not dist_index.is_file():
            raise FileNotFoundError(f"Frontend build failed: {dist_index} missing after build.")
    print(f"Frontend bundle verified: {dist_index}")


def write_target_manifest(root_dir: Path, artifact: Path) -> Path:
    """Emit ``data/target.hash`` for the D-4 frozen-target gate (H-4).

    The launcher resolves its manifest from ``<bundle>/data/target.hash``
    (``src/launcher/app.py::_manifest_path``) and its loader accepts either a
    bare 64-hex line or sha256sum's ``<digest>  <name>`` form. Nothing in the
    repository wrote that file — this script only produced
    ``dist/SHA256SUMS``, the wrong name in the wrong location — so EVERY
    frozen artifact failed the gate and the launcher fell back to a
    ``src/main.py`` that a frozen build does not bundle.

    A missing artifact raises: a manifest-less release must fail the build.
    """
    if not artifact.is_file():
        raise FileNotFoundError(
            f"Build artifact not found, cannot emit the D-4 hash manifest: {artifact}"
        )
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    payload = f"{digest}  {artifact.name}\n"
    manifest_path = root_dir / "data" / "target.hash"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(payload, encoding="utf-8")

    # The copy that must be PACKAGED inside the bundle: the gate is only
    # meaningful when the manifest lives outside the writable dist/ tree.
    packaging_copy = root_dir / "dist" / "data" / "target.hash"
    try:
        packaging_copy.parent.mkdir(parents=True, exist_ok=True)
        packaging_copy.write_text(payload, encoding="utf-8")
    except OSError as exc:  # pragma: no cover - packaging convenience only
        print(f"[WARN] Could not stage the packaged manifest copy: {exc}")

    try:
        import os as _os

        if _os.name == "posix":
            _os.chmod(manifest_path, 0o644)
    except OSError as _exc:  # pragma: no cover - POSIX-only best effort
        print(f"[WARN] Could not chmod target.hash: {_exc}")
    print(f"[D-4] target.hash: {digest}  ({artifact.name}) -> {manifest_path}")
    return manifest_path


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
    knowledge_dir = root_dir / "data" / "knowledge"
    golden_dir = root_dir / "data" / "golden_traces"

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

    if knowledge_dir.is_dir():
        cmd.append(f"--include-data-dir={knowledge_dir}=data/knowledge")

    if golden_dir.is_dir():
        cmd.append(f"--include-data-dir={golden_dir}=data/golden_traces")

    icon_file = root_dir / "assets" / "icon.ico"
    if icon_file.is_file():
        cmd.append(f"--windows-icon-from-ico={icon_file}")

    cmd.extend([
        # supply-chain: --assume-yes-for-downloads removed (auto-downloads are a
        # supply-chain risk); use cached compilers instead.
        "--disable-ccache",
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

    # H-4: emit BOTH the human-facing dist/ checksums AND the manifest the
    # launcher's D-4 gate actually reads (data/target.hash). A build that
    # cannot produce the manifest is a FAILED build.
    try:
        exe_path = output_dir / "ucanlab.exe"
        if exe_path.is_file():
            digest = hashlib.sha256(exe_path.read_bytes()).hexdigest()
            sums_path = output_dir / "SHA256SUMS"
            sums_path.write_text(f"{digest}  {exe_path.name}\n", encoding="utf-8")
            # supply-chain: world-writable manifest must not be executable; POSIX-only.
            try:
                import os as _os

                if _os.name == "posix":
                    _os.chmod(sums_path, 0o644)
            except OSError as _exc:
                print(f"[WARN] Could not chmod SHA256SUMS: {_exc}")
            print(f"SHA-256: {digest}  ({exe_path.name})")
        if exit_code == 0:
            write_target_manifest(root_dir, exe_path)
        else:
            print("[D-4] Build failed — no target.hash emitted.")
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        print("[D-4] Refusing to finish a release build without data/target.hash.")
        return 1
    except OSError as exc:
        print(f"[ERROR] Could not write the D-4 hash manifest: {exc}")
        return 1

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
