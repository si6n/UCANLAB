"""One-Click Production Windows Installer Builder for uCAN Lab (UCanLab_Setup_v1.exe).

Orchestrates:
1. React+Tailwind UI build (npm run build)
2. PyInstaller standalone compilation for ucanlab.exe & ucanlab_launcher.exe
3. Inno Setup 6 compilation of scripts/installer.iss (with all 229 DBC files)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_ISCC_PATHS = [
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe"),
]


def find_iscc() -> Path | None:
    """Locate the Inno Setup Compiler executable (ISCC.exe)."""
    # 1. Check PATH
    which_iscc = shutil.which("iscc") or shutil.which("ISCC.exe")
    if which_iscc:
        return Path(which_iscc)

    # 2. Check custom environment variable
    custom_path = os.environ.get("INNO_SETUP_DIR")
    if custom_path:
        cand = Path(custom_path) / "ISCC.exe"
        if cand.is_file():
            return cand

    # 3. Check well-known Program Files locations
    for p in DEFAULT_ISCC_PATHS:
        if p.is_file():
            return p

    return None


def run_build_installer(skip_build: bool = False, build_launcher: bool = True) -> int:
    root_dir = Path(__file__).parent.parent.resolve()
    dist_dir = root_dir / "dist"
    iss_file = root_dir / "scripts" / "installer.iss"
    ucanlab_exe = dist_dir / "ucanlab.exe"
    dbc_dir = root_dir / "data" / "dbc"

    print("==================================================")
    print(">>> uCAN Lab - One-Click Installer Pipeline")
    print("==================================================")

    # 1. Step 1: Build Application Binaries (if needed)
    if not skip_build or not ucanlab_exe.is_file():
        print("[1/3] ucanlab.exe derleme süreci başlatılıyor...")
        build_exe_script = root_dir / "scripts" / "build_exe.py"
        build_args = [sys.executable, str(build_exe_script)]
        if build_launcher:
            build_args.append("--all")

        ret = subprocess.call(build_args, cwd=str(root_dir))
        if ret != 0:
            print("[HATA] Python EXE derlemesi başarısız oldu.")
            return ret
    else:
        print(f"[1/3] Mevcut derlenmiş dosya kullanılıyor: {ucanlab_exe}")

    # 2. Step 2: Verify DBC Knowledge Catalog
    if not dbc_dir.is_dir():
        print(f"[UYARI] DBC veri dizini bulunamadı: {dbc_dir}")
    else:
        dbc_count = len(list(dbc_dir.rglob("*.dbc")))
        print(f"[2/3] DBC Bilgi Kataloğu doğrulandı: {dbc_count} adet DBC dosyası kuruluma dahil edilecek.")

    # 3. Step 3: Discover & Run Inno Setup Compiler
    iscc = find_iscc()
    if iscc is None:
        print("\n" + "!" * 65)
        print("[BILGILENDIRME] Inno Setup Derleyicisi (ISCC.exe) sistemde bulunamadı.")
        print("Kurulum dosyasını (.exe) üretmek için Inno Setup 6 gereklidir.")
        print("  1. Otomatik Kurulum (PowerShell/CMD):")
        print("     winget install JRSoftware.InnoSetup")
        print("  2. Manuel İndirme:")
        print("     https://jrsoftware.org/isdl.php")
        print("  3. Inno Setup yüklendikten sonra doğrudan şu komutla derleyebilirsiniz:")
        print(f'     ISCC.exe "{iss_file}"')
        print("!" * 65 + "\n")
        return 2

    print(f"[3/3] Inno Setup bulundu: {iscc}")
    print(f"Kurulum paketi derleniyor ({iss_file.name})...")
    cmd = [str(iscc), str(iss_file)]
    ret = subprocess.call(cmd, cwd=str(root_dir / "scripts"))

    if ret == 0:
        setup_exe = dist_dir / "UCanLab_Setup_v1.exe"
        print("==================================================")
        print("TEBRİKLER! Kurulum paketi başarıyla üretildi:")
        print(f"Dosya: {setup_exe}")
        if setup_exe.is_file():
            size_mb = setup_exe.stat().st_size / (1024 * 1024)
            print(f"Boyut: {size_mb:.2f} MB")
        print("==================================================")

    return ret


def main() -> int:
    parser = argparse.ArgumentParser(description="uCAN Lab - Windows Installer Builder")
    parser.add_argument("--skip-build", action="store_true", help="Skip rebuilding ucanlab.exe if already exists in dist/")
    parser.add_argument("--no-launcher", action="store_true", help="Do not package launcher executable")
    args = parser.parse_args()

    return run_build_installer(skip_build=args.skip_build, build_launcher=not args.no_launcher)


if __name__ == "__main__":
    sys.exit(main())
