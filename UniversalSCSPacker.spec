# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the single-file HashFS v2/v1 packer."""
import os
import sys
from pathlib import Path

project_root = Path(SPECPATH).resolve()
directstorage = project_root / "DirectStorage"
archive_backend = project_root / "ArchiveBackend"

windows_binaries = [
    (str(directstorage / "dstorage.dll"), "DirectStorage"),
    (str(directstorage / "dstoragecore.dll"), "DirectStorage"),
    (str(archive_backend / "7z.exe"), "ArchiveBackend"),
    (str(archive_backend / "7z.dll"), "ArchiveBackend"),
] if sys.platform == "win32" else []

windows_datas = [
    (str(directstorage / "LICENSE.txt"), "DirectStorage"),
    (str(directstorage / "LICENSE-CODE.txt"), "DirectStorage"),
    (str(directstorage / "NOTICES.txt"), "DirectStorage"),
    (str(archive_backend / "License.txt"), "ArchiveBackend"),
    (str(archive_backend / "README.txt"), "ArchiveBackend"),
] if sys.platform == "win32" else []

analysis = Analysis(
    [str(project_root / "packer.py")],
    pathex=[str(project_root)],
    binaries=windows_binaries,
    datas=[
        (str(project_root / "original_game_paths.tar.gz"), "."),
        (str(project_root / "assets" / "app-icon.ico"), "assets"),
        (str(project_root / "assets" / "app-icon-48.png"), "assets"),
        (str(project_root / "assets" / "app-icon-64.png"), "assets"),
    ] + windows_datas,
    hiddenimports=[
        "cityhash_legacy", "gui", "tkinter", "tkinter.filedialog",
        "tkinter.messagebox",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="UniversalSCSPacker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=sys.platform.startswith("linux"),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=([os.environ["UNIVERSAL_SCS_BUILD_ICON"]]
          if os.environ.get("UNIVERSAL_SCS_BUILD_ICON") else
          ([str(project_root / "assets" / "app-icon.ico")]
           if sys.platform == "win32" else [])),
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="Universal SCS Packer.app",
        icon=os.environ.get("UNIVERSAL_SCS_BUILD_ICON"),
        bundle_identifier="org.universalscs.packer",
    )
