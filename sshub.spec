# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Smart Shutdown Hub (Windows, onedir folder build).

The result is a complete self-contained folder in dist/SmartShutdownHub/
(imports, DLLs, customtkinter themes and the .ico live next to the exe),
so the installer deploys the whole folder instead of a single file.
"""
import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)

from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "sshub" / "gui" / "assets" / "sshub.ico"), "sshub/gui/assets"),
        *collect_data_files("customtkinter"),  # JSON themes + fonts
    ],
    hiddenimports=[
        "customtkinter",
        "psutil",
        "pystray",
        "PIL._tkinter_finder",
        "wmi",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter.test", "unittest", "pydoc_data"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # binaries go to the COLLECT folder, not the exe
    name="SmartShutdownHub",
    debug=False,
    strip=False,
    upx=True,
    console=False,               # windowed app: no console flash
    icon=str(ROOT / "sshub" / "gui" / "assets" / "sshub.ico"),
    version=str(ROOT / "scripts" / "version_info.txt") if (ROOT / "scripts" / "version_info.txt").exists() else None,
    manifest=str(ROOT / "scripts" / "app.manifest") if (ROOT / "scripts" / "app.manifest").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SmartShutdownHub",  # complete folder: dist/SmartShutdownHub/
)

