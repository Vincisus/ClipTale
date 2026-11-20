"""ClipTale.exe: avvia l'app senza finestre di console.

Cerca il Python di ClipTale accanto all'eseguibile (python/ nella versione installata, .venv/ in quella di sviluppo)
e lancia desktop/launcher.py con pythonw. Se manca qualcosa, mostra un messaggio invece di chiudersi in silenzio.
Costruito da desktop/build_exe.py con PyInstaller (un solo file, con l'icona arancione).
"""
import ctypes
import os
import subprocess
import sys
from pathlib import Path


def message(text):
    ctypes.windll.user32.MessageBoxW(None, text, "ClipTale", 0x10)


def main():
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    launcher = root / "desktop" / "launcher.py"
    candidates = [root / "python" / "pythonw.exe", root / ".venv" / "Scripts" / "pythonw.exe"]
    pythonw = next((p for p in candidates if p.is_file()), None)
    if not launcher.is_file() or not pythonw:
        message("ClipTale non è installato correttamente: mancano dei file.\nReinstallalo con ClipTale-Setup.exe.")
        return 1
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    subprocess.Popen([str(pythonw), str(launcher)], cwd=str(root), creationflags=flags, env=env, close_fds=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
