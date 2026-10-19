# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
import sys

ROOT = Path(os.getcwd())
TARGET_ARCH = os.environ.get("TARGET_ARCH") or None
MAC_ICON = ROOT / "assets" / "HV_P2P_NMS.icns"
WIN_ICON = ROOT / "assets" / "HV_P2P_NMS.ico"

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

if sys.platform == "darwin":
    # Two thin native .app builds: one per GitHub macOS runner.
    exe = EXE(
        pyz, a.scripts, [],
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
        exe, a.binaries, a.zipfiles, a.datas,
        strip=False, upx=False, upx_exclude=[],
        name="HV P2P NMS",
    )

    app = BUNDLE(
        coll,
        name="HV P2P NMS.app",
        icon=str(MAC_ICON) if MAC_ICON.exists() else None,
        bundle_identifier="com.hudorvisual.hvp2pnms",
        info_plist={
            "CFBundleDisplayName": "HV P2P NMS",
            "CFBundleName": "HV P2P NMS",
            "CFBundleShortVersionString": "26.10.19.03",
            "CFBundleVersion": "26.10.19.03",
            "LSMinimumSystemVersion": "13.0",
            "NSHighResolutionCapable": True,
            "NSPrincipalClass": "NSApplication",
            "NSLocalNetworkUsageDescription": "HV P2P NMS discovers and monitors devices on the local production network.",
            "NSBonjourServices": [
                "_services._dns-sd._udp",
                "_http._tcp", "_https._tcp", "_ssh._tcp", "_smb._tcp",
                "_workstation._tcp", "_device-info._tcp", "_ipp._tcp", "_printer._tcp",
            ],
        },
    )
elif sys.platform == "win32":
    # Windows distribution: standalone native x64 EXE, including all Qt libs.
    # Build on Windows rather than cross-compiling from a Mac or Linux runner.
    exe = EXE(
        pyz, a.scripts, a.binaries, a.zipfiles, a.datas,
        [],
        name="HV P2P NMS",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        icon=str(WIN_ICON) if WIN_ICON.exists() else None,
    )
else:
    raise SystemExit("Packaging supported only on macOS or Windows")
