"""Aggiornamenti automatici dalle release di GitHub.

Ogni release di ClipTale ha un tag vX.Y.Z e l'installer ClipTale-Setup-X.Y.Z.exe come allegato. L'app controlla la
release più recente (al massimo ogni 6 ore, senza inviare dati dell'utente), e se è più nuova lo dice nell'interfaccia.
"Aggiorna" scarica l'installer, lo avvia in modalità silenziosa e chiude ClipTale: l'installer la riapre a fine lavoro.
Solo nella versione installata; in quella di sviluppo si aggiorna con git.
"""
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx

from app.core import desktop, paths

# l'id numerico del repository (Vincisus/ClipTale) non cambia se il repository viene rinominato
REPO_ID = os.getenv("CLIPTALE_UPDATE_REPO_ID", "1454114294")
API = f"https://api.github.com/repositories/{REPO_ID}/releases/latest"
EVERY = 6 * 3600

_cache = {"at": 0.0, "info": None}
_dl = {"running": False, "done_mb": 0.0, "total_mb": 0.0, "error": "", "ready": False}


def parse(version):
    return tuple(int(n) for n in re.findall(r"\d+", str(version))[:3]) or (0,)


async def check(force=False):
    if not force and _cache["info"] is not None and time.time() - _cache["at"] < EVERY:
        return _cache["info"]
    info = {"current": desktop.VERSION, "latest": None, "available": False, "installed": paths.INSTALLED, "notes": "", "asset": None}
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers={"Accept": "application/vnd.github+json", "User-Agent": "ClipTale-updater"}) as client:
            data = (await client.get(API)).raise_for_status().json()
        asset = next((a for a in data.get("assets", []) if a.get("name", "").lower().endswith(".exe") and "setup" in a["name"].lower()), None)
        info.update(latest=data.get("tag_name", "").lstrip("v"), notes=(data.get("body") or "")[:2000],
                    asset={"url": asset["browser_download_url"], "name": asset["name"], "size": asset.get("size", 0)} if asset else None)
        info["available"] = bool(asset) and parse(info["latest"]) > parse(desktop.VERSION)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        info["error"] = str(exc)[:200]
    _cache.update(at=time.time(), info=info)
    return info


def download_status():
    return dict(_dl)


def _download_and_run(url, name):
    try:
        target = Path(tempfile.gettempdir()) / "ClipTale-update" / Path(name).name
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(".part")
        with httpx.stream("GET", url, follow_redirects=True, timeout=60) as resp:
            resp.raise_for_status()
            _dl["total_mb"] = round(int(resp.headers.get("content-length", 0)) / 1e6, 1)
            with open(part, "wb") as fh:
                for chunk in resp.iter_bytes(1 << 20):
                    fh.write(chunk)
                    _dl["done_mb"] = round(_dl["done_mb"] + len(chunk) / 1e6, 1)
        part.replace(target)
        _dl["ready"] = True
        # /UPDATE: l'installer riapre ClipTale alla fine. Se Windows chiede il permesso e l'utente dice no, ClipTale resta com'è.
        subprocess.Popen([str(target), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/UPDATE"], close_fds=True)
        time.sleep(1.5)
        if desktop.HOOKS.get("quit"):
            desktop.HOOKS["quit"]()
    except (httpx.HTTPError, OSError) as exc:
        _dl["error"] = str(exc)[:300]
    finally:
        _dl["running"] = False


def start_update(info):
    if not paths.INSTALLED:
        raise RuntimeError("Questa è la versione di sviluppo: aggiornala con git pull.")
    if not info or not info.get("available") or not info.get("asset"):
        raise RuntimeError("Nessun aggiornamento disponibile.")
    if _dl["running"]:
        return
    _dl.update(running=True, done_mb=0.0, total_mb=0.0, error="", ready=False)
    threading.Thread(target=_download_and_run, args=(info["asset"]["url"], info["asset"]["name"]), daemon=True).start()
