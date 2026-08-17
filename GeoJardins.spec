# -*- mode: python ; coding: utf-8 -*-
"""Especificação reproduzível dos builds portáteis do GeoJardins."""

from pathlib import Path
import os


ROOT = Path(SPECPATH)
MODE = os.environ.get("GEOJARDINS_BUILD_MODE", "onedir").casefold()
if MODE not in {"onedir", "onefile"}:
    raise ValueError("GEOJARDINS_BUILD_MODE deve ser 'onedir' ou 'onefile'.")

ASSETS = ROOT / "src" / "geojardins" / "gui" / "assets"

DATA_FILES = [
    (str(ROOT / "data" / "processed" / "areas_jardins_partition.geojson"), "data/processed"),
    (str(ROOT / "data" / "processed" / "ugr_jardins_boundary_partition.geojson"), "data/processed"),
    (str(ROOT / "data" / "processed" / "areas_jardins.geojson"), "data/processed"),
    (str(ROOT / "data" / "processed" / "ugr_jardins_boundary.geojson"), "data/processed"),
    (str(ROOT / "config" / "settings.json"), "config"),
    (str(ROOT / "config" / "fiscais.json"), "config"),
    *[(str(asset), "assets") for asset in ASSETS.iterdir() if asset.is_file() and asset.name != "__init__.py"],
]

analysis = Analysis(
    [str(ROOT / "scripts" / "geojardins_gui.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=DATA_FILES,
    hiddenimports=[
        "geojardins.gui.app",
        "geojardins.gui.main_window",
        "geojardins.gui.views.batch_lookup",
        "geojardins.gui.views.dashboard",
        "geojardins.gui.views.fiscais",
        "geojardins.gui.views.individual_lookup",
        "geojardins.gui.views.settings",
        "PySide6.QtSvg",
        "shapely.geometry",
        "shapely.ops",
        "pyproj",
        "openpyxl",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "matplotlib", "pikepdf", "fitz", "tests"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

if MODE == "onefile":
    exe = EXE(
        pyz,
        analysis.scripts,
        analysis.binaries,
        analysis.zipfiles,
        analysis.datas,
        [],
        name="GeoJardins",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        icon=str(ASSETS / "geojardins.ico"),
    )
else:
    exe = EXE(
        pyz,
        analysis.scripts,
        [],
        exclude_binaries=True,
        name="GeoJardins",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        icon=str(ASSETS / "geojardins.ico"),
    )
    coll = COLLECT(
        exe,
        analysis.binaries,
        analysis.zipfiles,
        analysis.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="GeoJardins",
    )
