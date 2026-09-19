# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller：Windows 目录分发（onedir，Qt + PyMuPDF 更稳）。"""
from __future__ import annotations

import os

from PyInstaller.utils.hooks import collect_all

SPECDIR = os.path.abspath(SPECPATH)
ROOT = os.path.abspath(os.path.join(SPECDIR, ".."))
ICON = os.path.join(SPECDIR, "app.ico")

datas, binaries, hiddenimports = [], [], []
for pkg in ("pymupdf", "fitz"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

def _keep_bundle(src):
    p = src.replace("\\", "/").lower()
    return "mupdf-devel" not in p

datas = [(s, d) for s, d in datas if _keep_bundle(s)]
binaries = [(s, d) for s, d in binaries if _keep_bundle(s)]

datas += [
    (src, dst)
    for src, dst in (
        (os.path.join(ROOT, "LICENSE"), "."),
        (os.path.join(ROOT, "README.md"), "."),
        (ICON, "."),
    )
    if os.path.isfile(src)
]

hiddenimports += [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
    "shiboken6",
    "numpy",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.PngImagePlugin",
    "PIL.JpegImagePlugin",
    "PIL.BmpImagePlugin",
    "core",
    "ui",
]

excludes = [
    "tkinter",
    "pytest",
    "ruff",
    "unittest",
    "IPython",
    "jupyter",
    "notebook",
    "matplotlib",
    "pandas",
    "scipy",
    "sklearn",
    "cv2",
    "pygame",
    "torch",
    "torchvision",
    "torchaudio",
    "tensorflow",
    "sympy",
    "lxml",
    "numba",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQml",
    "PySide6.QtRemoteObjects",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PDFSeamlessEditor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=ICON if os.path.isfile(ICON) else None,
    version=os.path.join(SPECDIR, "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PDFSeamlessEditor",
)
