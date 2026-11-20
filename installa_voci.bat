@echo off
setlocal
cd /d "%~dp0"
title TiktokFactory - voci realistiche (Chatterbox)
where uv >nul 2>nul
if errorlevel 1 (
    echo Installo uv, il gestore degli ambienti Python...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
echo Creo l'ambiente separato per la voce (.venv-voice, Python 3.11)...
uv venv .venv-voice --python 3.11
if errorlevel 1 goto fail
echo Installo PyTorch con CUDA (circa 2,5 GB)...
uv pip install --python .venv-voice\Scripts\python.exe torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
if errorlevel 1 goto fail
echo Installo Chatterbox...
uv pip install --python .venv-voice\Scripts\python.exe chatterbox-tts==0.1.7 "setuptools<81" --extra-index-url https://download.pytorch.org/whl/cu124 --index-strategy unsafe-best-match
if errorlevel 1 goto fail
echo Scarico il modello della voce (circa 3 GB)...
.venv-voice\Scripts\python.exe -c "from chatterbox.mtl_tts import ChatterboxMultilingualTTS; ChatterboxMultilingualTTS.from_pretrained(device='cpu')"
echo.
echo Fatto. Riavvia l'app con Avvia.bat: le voci "realistiche" compaiono nell'elenco.
pause
exit /b 0
:fail
echo Installazione non riuscita.
pause
exit /b 1
