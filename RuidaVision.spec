# -*- mode: python ; coding: utf-8 -*-
"""Empaquetado con PyInstaller. Se ejecuta en el PC de Windows, no aqui.

  py -3 -m Pyinstaller --noconfirm RuidaVision.spec

Un solo ejecutable en dist\\RuidaVision\\ (onedir: arranca mucho antes que
onefile, que descomprime 200 MB en cada ejecucion). La version sale de
ruidavision\\__init__.py, que es el unico sitio donde se toca.
"""

import os
import re

ESPEC = os.path.abspath(SPECPATH)
with open(os.path.join(ESPEC, "ruidavision", "__init__.py"), encoding="utf-8") as f:
    VERSION = re.search(r'VERSION\s*=\s*"([^"]+)"', f.read()).group(1)

a = Analysis(
    [os.path.join(ESPEC, "ruidavision", "app.py")],
    pathex=[ESPEC],                       # para que `import ruidavision` resolver
    binaries=[],
    # calib.json va DENTRO del ejecutable para que la app abra con valores por
    # defecto; el instalador pone tambien una copia al lado, que es la que se
    # guarda (escribir en Archivos de programa no se puede sin permisos).
    datas=[(os.path.join(ESPEC, "calib.json"), ".")],
    hiddenimports=["PIL._tkinter_finder"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "scipy", "pandas", "pytest", "setuptools", "pip"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="RuidaVision",
    debug=False,
    strip=False,
    upx=False,
    console=False,                       # sin consola: la app ya tiene registro
    disable_windowed_traceback=False,
    icon=os.path.join(ESPEC, "ruida.ico") if os.path.exists(
        os.path.join(ESPEC, "ruida.ico")) else None,
)
coll = COLLECT(exe, a.binaries, a.datas,
               strip=False, upx=False, name="RuidaVision")
