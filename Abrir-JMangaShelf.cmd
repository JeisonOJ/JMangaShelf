@echo off
setlocal
cd /d "%~dp0"
title JMangaShelf
call ".\Iniciar-JMangaShelf.cmd" --prepare
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" launcher.py
set "launcher_status=%errorlevel%"
if not "%launcher_status%"=="0" pause
exit /b %launcher_status%
