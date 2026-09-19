# -*- mode: python ; coding: utf-8 -*-
# supply-chain: repo-relative paths (no hardcoded user dirs).
# Release: sign artifact + publish dist/SHA256SUMS.
import os


a = Analysis(
    ['src/launcher/app.py'],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=[],
    hiddenimports=['src.launcher.app', 'src.launcher.auth', 'src.launcher.prereqs', 'src.launcher.updater', 'src.security.hwid.collector', 'src.security.cloud.client', 'src.security.cloud.license_flow', 'src.safety.secret_provider', 'cryptography'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ucanlab_launcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,  # TODO(supply-chain): no cert; keep None, sign release + publish SHA256SUMS
    entitlements_file=None,
)
