# -*- mode: python ; coding: utf-8 -*-
# supply-chain: paths are repo-relative (no hardcoded user dirs); see scripts/build_*.py
# which emit dist/SHA256SUMS next to the artifact for tamper-evident distribution.
import os

a = Analysis(
    ['src/main.py'],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=[('src/ui/frontend/dist', 'src/ui/frontend/dist')],
    hiddenimports=['src.safety.gateway', 'src.safety.estop', 'src.safety.watchdog', 'src.safety.state_machine', 'src.safety.secret_provider', 'src.safety.multiplexer', 'src.safety.reset_authority', 'src.safety.e2e.packager', 'src.safety.e2e.validator', 'src.security.license.validator', 'src.security.anti_tamper.guard', 'src.security.cloud.client', 'src.security.cloud.license_flow', 'src.security.cloud.telemetry_uploader', 'src.security.hwid.collector', 'src.protocols.uds.flasher', 'src.protocols.uds.did_database', 'src.protocols.uds.client', 'src.protocols.uds.isotp', 'src.protocols.uds.services', 'src.protocols.uds.nrc', 'src.protocols.obd.poller', 'src.protocols.obd.pids', 'src.protocols.obd.models', 'src.protocols.j1939.transport', 'src.protocols.j1939.diagnostics', 'src.protocols.j1939.address_claim', 'src.protocols.j1939.oem.registry', 'src.protocols.j1939.oem.cummins', 'src.protocols.j1939.oem.caterpillar', 'src.protocols.j1939.oem.scania', 'src.protocols.j1939.oem.volvo', 'src.protocols.j1939.oem.detroit', 'src.protocols.j1939.oem.actros', 'src.protocols.nmea2000.fast_packet', 'src.protocols.volvo.volvo_decoder', 'src.hal.replay.parsers', 'src.hal.replay.player', 'src.hal.replay.safety_filter', 'src.hal.power.win32_power', 'src.hal.virtual', 'src.hal.tx_port', 'src.hal.can_interface', 'src.engine.discovery.engine', 'src.engine.discovery.bitstats', 'src.engine.discovery.segmenter', 'src.engine.discovery.hypotheses', 'src.engine.discovery.dbc_builder', 'src.engine.discovery.evidence', 'src.engine.discovery.detectors.checksum', 'src.engine.discovery.detectors.counter', 'src.engine.exporters.kml_exporter', 'src.engine.exporters.mat_exporter', 'src.engine.exporters.mdf4_exporter', 'src.engine.exporters.pdf_report', 'src.engine.virtual_channels.channel_engine', 'webview', 'clr_loader', 'pythonnet', 'bottle', 'proxy_tools', 'can', 'cantools', 'asammdf', 'can.interfaces.canalystii', 'can.interfaces.cantact', 'can.interfaces.etas', 'can.interfaces.gs_usb', 'can.interfaces.ics_neovi', 'can.interfaces.iscan', 'can.interfaces.ixxat', 'can.interfaces.kvaser', 'can.interfaces.neousys', 'can.interfaces.nican', 'can.interfaces.nixnet', 'can.interfaces.pcan', 'can.interfaces.robotell', 'can.interfaces.seeedstudio', 'can.interfaces.serial', 'can.interfaces.slcan', 'can.interfaces.socketcan', 'can.interfaces.socketcand', 'can.interfaces.systec', 'can.interfaces.udp_multicast', 'can.interfaces.usb2can', 'can.interfaces.vector', 'can.interfaces.virtual'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6', 'pyqtgraph', 'shiboken6', 'tkinter', 'matplotlib'],
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
    name='ucanlab',
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
