@echo off
rem Empaqueta Ruida Vision: ejecutable con PyInstaller + instalador con Inno Setup.
rem Se ejecuta en el PC de Windows, desde la carpeta del proyecto:
rem     build_windows.bat
rem La version sale de ruidavision\__init__.py. No hay mas VERSION en ningun sitio.
setlocal enabledelayedexpansion

cd /d "%~dp0"
set INNO="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist %INNO% set INNO="C:\Program Files\Inno Setup 6\ISCC.exe"

echo === 1/5 version
for /f %%v in ('py -3 -c "import ruidavision;print(ruidavision.VERSION)"') do set VER=%%v
if "%VER%"=="" (echo no se pudo leer la version & exit /b 1)
echo version %VER%

echo === 2/5 comprobaciones rapidas
py -3 -m py_compile hybrid_vision.py ruida.py ruidavision\app.py ruidavision\actualizar.py
if errorlevel 1 (echo FALLO al compilar el codigo & exit /b 1)
py -3 -m ruidavision.actualizar test
if errorlevel 1 (echo FALLO el autocomprobado del actualizador & exit /b 1)
py -3 -m ruidavision.prueba_app
if errorlevel 1 (echo FALLO la prueba de la app & exit /b 1)
if not exist calib.json (
  echo AVISO: no hay calib.json; se empaquetara sin el y habra que crearlo
)

echo === 3/5 ejecutable
py -3 -m PyInstaller --noconfirm --clean RuidaVision.spec
if errorlevel 1 (echo FALLO PyInstaller & exit /b 1)

echo === 4/5 instalador
%INNO% /DAPPVER=%VER% installer\RuidaVision.iss
if errorlevel 1 (echo FALLO Inno Setup & exit /b 1)

echo === 5/5 listo
dir /b dist\instalar_RuidaVision_*.exe
echo.
echo Para publicarlo como actualizacion OTA:
rem Sin --prerelease: el OTA lee /releases/latest, que ignora los pre-releases.
echo   gh release create v%VER% dist\instalar_RuidaVision_%VER%.exe
