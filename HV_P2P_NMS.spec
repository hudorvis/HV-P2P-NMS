# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os

ROOT = Path(os.getcwd())
TARGET_ARCH = os.environ.get("TARGET_ARCH") or None
ICON = ROOT / "assets" / "HV_P2P_NMS.icns"

block_cipher = None

a = Analysis(
    [str(ROOT / "run_hv_nms.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HV P2P NMS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch=TARGET_ARCH,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="HV P2P NMS",
)

app = BUNDLE(
    coll,
    name="HV P2P NMS.app",
    icon=str(ICON) if ICON.exists() else None,
    bundle_identifier="com.hudorvisual.hvp2pnms",
    info_plist={
        "CFBundleDisplayName": "HV P2P NMS",
        "CFBundleName": "HV P2P NMS",
        "CFBundleShortVersionString": "26.09.02.01",
        "CFBundleVersion": "26.09.02.01",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
    },
)
