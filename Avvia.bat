@echo off
setlocal
cd /d "%~dp0"
title ClipTale
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 (
        echo Installa Python 3.12 con il launcher py, poi riprova.
        pause
        exit /b 1
    )
)
if not exist ".venv\factory_v6_ready" (
    ".venv\Scripts\python.exe" -m pip install --upgrade -r requirements.txt
    if errorlevel 1 (
        pause
        exit /b 1
    )
    type nul > ".venv\factory_v6_ready"
)
rem ClipTale come programma: finestra propria e icona nella barra (vedi desktop\launcher.py)
start "" ".venv\Scripts\pythonw.exe" desktop\launcher.py
