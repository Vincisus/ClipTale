@echo off
setlocal
cd /d "%~dp0"
title TiktokFactory - installazione AI locale (disegni e voce)
if not exist ".venv\Scripts\python.exe" (
    echo Avvia prima Avvia.bat almeno una volta, poi riprova.
    pause
    exit /b 1
)
echo Installo PyTorch con CUDA e Stable Diffusion (circa 4 GB)...
".venv\Scripts\python.exe" -m pip install --upgrade -r requirements-images.txt
if errorlevel 1 (
    echo Installazione non riuscita.
    pause
    exit /b 1
)
echo Scarico il modello di disegno (circa 2 GB)...
".venv\Scripts\python.exe" -c "from app.services.sd_worker import ensure_models; ensure_models()"
if errorlevel 1 (
    echo Download del modello non riuscito: verra ritentato al primo video.
)
echo Scarico la voce italiana locale Paola (circa 63 MB)...
".venv\Scripts\python.exe" -c "from app.services.tts_service import download_piper_voice; download_piper_voice()"
echo Scarico la voce naturale locale Kokoro (circa 340 MB)...
".venv\Scripts\python.exe" -c "from app.services.tts_service import download_kokoro; download_kokoro()"
echo Scarico IP-Adapter per copiare lo stile dei riferimenti (circa 2,6 GB)...
".venv\Scripts\python.exe" -c "from app.services.sd_worker import ensure_models; ensure_models(ip_adapter=True)"
echo.
echo Fatto. Riavvia l'app con Avvia.bat.
pause
