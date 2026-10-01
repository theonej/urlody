# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for a standalone `scorer` build.

    uv run pyinstaller scorer.spec --noconfirm

produces dist/scorer/, a folder holding scorer.exe (scorer on macOS/Linux) and
the _internal/ libraries it needs. PyInstaller only builds for the OS it runs
on; .github/workflows/build-windows.yml runs this on a Windows runner.

A single-file build was tried first but unpacks ~300 MB to a temp folder on
every run and deletes it on exit, which took minutes on a machine with
endpoint security scanning; the folder build starts in about a second.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# verovio loads its music fonts and resources from verovio/data at runtime.
datas = collect_data_files("verovio")

# yt-dlp resolves extractors dynamically; music21 has no PyInstaller hook and
# imports parts of itself lazily. (Its 66 MB score corpus is not needed.)
hiddenimports = collect_submodules("yt_dlp") + collect_submodules("music21")

a = Analysis(
    ["src/scorer/__main__.py"],
    pathex=["src"],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["matplotlib", "tkinter", "PyQt5", "PySide6", "IPython", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="scorer",
    console=True,
    upx=False,
    strip=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="scorer", upx=False, strip=False)
