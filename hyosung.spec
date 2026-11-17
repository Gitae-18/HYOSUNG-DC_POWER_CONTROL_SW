# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 빌드 설정 — 런처와 DC Power 프로그램을 한 폴더에 함께 만든다.
#   pyinstaller --noconfirm hyosung.spec
# 결과: dist\HyosungChamber\HyosungLauncher.exe, HyosungDCPower.exe (Qt 라이브러리는 _internal 폴더에서 공유)
# 2026-11-04: DuckDB(일별 DB) 포함 — duckdb 패키지의 바이너리를 collect_all 로 함께 넣는다
# 2026-11-03: 실제 장비 통신(pyserial) 포함
# 2026-11-02: assets 폴더(로고·체크 표시)를 함께 넣고, exe 아이콘은 app.ico(회사 로고)

import os
from PyInstaller.utils.hooks import collect_all

DUCK_DATAS, DUCK_BINS, DUCK_HIDDEN = collect_all("duckdb")

ICON = "app.ico" if os.path.exists("app.ico") else None
EXCLUDES = ["PyQt5", "PyQt6", "PySide2", "tkinter", "matplotlib", "IPython", "scipy", "pandas"]

common = dict(pathex=["."], binaries=DUCK_BINS, datas=[("assets", "assets")] + DUCK_DATAS,
              hiddenimports=["PySide6.QtSvg", "PySide6.QtNetwork", "serial", "serial.tools.list_ports", "duckdb"] + DUCK_HIDDEN, hookspath=[], runtime_hooks=[],
              excludes=EXCLUDES, noarchive=False)

a_launcher = Analysis(["app/launcher.py"], **common)
a_dcpower = Analysis(["app/app_dcpower.py"], **common)

pyz_launcher = PYZ(a_launcher.pure)
pyz_dcpower = PYZ(a_dcpower.pure)

exe_launcher = EXE(pyz_launcher, a_launcher.scripts, [], exclude_binaries=True, name="HyosungLauncher",
                   console=False, icon=ICON, upx=False)
exe_dcpower = EXE(pyz_dcpower, a_dcpower.scripts, [], exclude_binaries=True, name="HyosungDCPower",
                  console=False, icon=ICON, upx=False)

coll = COLLECT(exe_launcher, a_launcher.binaries, a_launcher.datas,
               exe_dcpower, a_dcpower.binaries, a_dcpower.datas,
               upx=False, name="HyosungChamber")
