"""Costruisce l'installer: build/installer/ClipTale-Setup-<versione>.exe

Uso: python desktop/build_installer.py
Serve Inno Setup 6 (https://jrsoftware.org/isinfo.php, oppure: winget install JRSoftware.InnoSetup) e uv.
Passi: ClipTale.exe (PyInstaller) -> copia dei file del programma in build/installer/stage -> ISCC.
Nello stage vanno solo codice e risorse: niente chiavi (secrets/), progetti, modelli o ambienti Python.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "installer"
STAGE = OUT / "stage"
UV_NOTICE = """uv - https://github.com/astral-sh/uv
Copyright (c) Astral Software Inc.
Licensed under either of the Apache License, Version 2.0 or the MIT license, at your option.
ClipTale ships the unmodified uv.exe to set up its Python environment.
"""


def version():
    return re.search(r'VERSION = "([^"]+)"', (ROOT / "app" / "core" / "desktop.py").read_text(encoding="utf-8")).group(1)


def find_iscc():
    for base in (os.getenv("ProgramFiles(x86)"), os.getenv("ProgramFiles"), os.path.join(os.getenv("LOCALAPPDATA", ""), "Programs")):
        if base and (Path(base) / "Inno Setup 6" / "ISCC.exe").is_file():
            return Path(base) / "Inno Setup 6" / "ISCC.exe"
    found = shutil.which("ISCC")
    if found:
        return Path(found)
    sys.exit("Inno Setup 6 non trovato: installalo con  winget install JRSoftware.InnoSetup")


def find_uv():
    for cand in (shutil.which("uv"), Path.home() / ".local" / "bin" / "uv.exe"):
        if cand and Path(cand).is_file():
            return Path(cand)
    sys.exit("uv non trovato: installalo da https://docs.astral.sh/uv/")


def stage(ver):
    if STAGE.exists():
        shutil.rmtree(STAGE)
    skip = shutil.ignore_patterns("__pycache__", "*.pyc", "source", "_keys.json")
    shutil.copytree(ROOT / "app", STAGE / "app", ignore=skip)
    (STAGE / "desktop").mkdir(parents=True)
    for f in ("launcher.py", "cliptale.ico"):
        shutil.copy2(ROOT / "desktop" / f, STAGE / "desktop" / f)
    for f in ("requirements.txt", "requirements-images.txt", "ClipTale.exe"):
        shutil.copy2(ROOT / f, STAGE / f)
    (STAGE / "tools").mkdir()
    shutil.copy2(find_uv(), STAGE / "tools" / "uv.exe")
    (STAGE / "tools" / "uv-LICENSE.txt").write_text(UV_NOTICE, encoding="utf-8")
    (STAGE / "installed.json").write_text(json.dumps({"version": ver}), encoding="utf-8")
    leaked = [p for p in STAGE.rglob("*") if p.name in ("secrets", ".env", "projects") or p.suffix in (".pyc",)]
    if leaked:
        sys.exit(f"File che non devono finire nell'installer: {leaked}")


if __name__ == "__main__":
    ver = version()
    subprocess.run([sys.executable, str(ROOT / "desktop" / "build_exe.py")], check=True)
    stage(ver)
    iscc = find_iscc()
    subprocess.run([str(iscc), f"/DAppVersion={ver}", f"/DStage={STAGE}", str(ROOT / "desktop" / "installer" / "cliptale.iss")], check=True)
    print(OUT / f"ClipTale-Setup-{ver}.exe")
