@echo off
setlocal
cd /d "%~dp0"
title JMangaShelf

if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto :setup_error
)

".venv\Scripts\python.exe" -c "import flask, PIL; from importlib.metadata import version; assert all(version(p) == v for p,v in (line.strip().split('==') for line in open('requirements.txt') if line.strip()))" >nul 2>&1
if errorlevel 1 (
    if exist "wheels\flask-*.whl" (
        ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-index --find-links wheels -r requirements.txt
    ) else (
        ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    )
    if errorlevel 1 goto :setup_error
)

if /i "%~1"=="--prepare" exit /b 0

echo Abre http://127.0.0.1:8765/ en tu navegador.
echo Pulsa Ctrl+C en esta ventana para cerrar JMangaShelf.
echo.
".venv\Scripts\python.exe" app.py
if errorlevel 1 pause
exit /b

:setup_error
echo Se requiere Python 3.14 de 64 bits en Windows y las dependencias de requirements.txt.
pause
exit /b 1
