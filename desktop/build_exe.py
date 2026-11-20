"""Costruisce ClipTale.exe (piccolo avviatore con l'icona) nella cartella del programma. Uso: python desktop/build_exe.py"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "exe"

if __name__ == "__main__":
    subprocess.run([sys.executable, str(ROOT / "desktop" / "make_icon.py")], check=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--noconsole",
                    "--name", "ClipTale", "--icon", str(ROOT / "desktop" / "cliptale.ico"),
                    "--distpath", str(BUILD / "dist"), "--workpath", str(BUILD / "work"), "--specpath", str(BUILD),
                    str(ROOT / "desktop" / "stub.py")], check=True)
    shutil.copy2(BUILD / "dist" / "ClipTale.exe", ROOT / "ClipTale.exe")
    print(ROOT / "ClipTale.exe")
