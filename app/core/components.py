"""Componenti facoltativi da scaricare dopo l'installazione (procedura del primo avvio e Impostazioni).

L'installer di ClipTale è leggero: l'app funziona subito con le voci Edge e un motore AI a scelta. I pezzi pesanti
si scaricano solo se l'utente li vuole, con una barra di avanzamento nell'app:
- drawings: disegni locali con Stable Diffusion (PyTorch + modelli, ~7 GB, serve una scheda NVIDIA)
- voices:   voci realistiche Chatterbox (ambiente Python separato + modello, ~5,5 GB, serve una scheda NVIDIA)
- kokoro:   voce naturale locale Kokoro (~340 MB)
Una installazione alla volta, in un thread: lo stato si legge con status().
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from app.core import paths

TORCH_CU124 = "https://download.pytorch.org/whl/cu124"
COMPONENTS = {
    "drawings": {"label": "Disegni locali (Stable Diffusion)", "size_gb": 7.0, "gpu": True},
    "voices": {"label": "Voci realistiche (Chatterbox)", "size_gb": 5.5, "gpu": True},
    "kokoro": {"label": "Voce naturale locale (Kokoro)", "size_gb": 0.4, "gpu": False},
}
SETUP_FILE = paths.DATA / "setup.json"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: niente finestre nere durante i download

_lock = threading.Lock()
_state = {"running": False, "current": None, "step": "", "done_steps": 0, "total_steps": 0, "detail": "",
          "error": "", "finished": [], "cancel": False}
_proc = None
_gpu_cache = None


def uv_exe():
    """uv (gestore di ambienti Python): accanto all'app nella versione installata, altrimenti quello dell'utente."""
    for cand in (paths.CODE / "tools" / "uv.exe", shutil.which("uv"), Path.home() / ".local" / "bin" / "uv.exe"):
        if cand and Path(cand).is_file():
            return str(cand)
    return None


def gpu():
    """Scheda NVIDIA (nome e memoria in MB), o None. Letta una volta sola."""
    global _gpu_cache
    if _gpu_cache is None:
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW).stdout.strip().splitlines()
            name, mem = out[0].rsplit(",", 1)
            _gpu_cache = {"name": name.strip(), "vram_mb": int(float(mem))}
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            _gpu_cache = {}
    return _gpu_cache or None


def installed():
    from app.services import illustration_service, tts_service
    return {
        "drawings": illustration_service.available() and illustration_service.models_ready(),
        "voices": tts_service.chatterbox_ready(),
        "kokoro": tts_service.kokoro_ready(),
    }


def setup_done():
    try:
        return bool(json.loads(SETUP_FILE.read_text(encoding="utf-8")).get("done"))
    except (OSError, ValueError):
        return False


def mark_setup_done():
    SETUP_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETUP_FILE.write_text(json.dumps({"done": True, "at": time.strftime("%Y-%m-%d %H:%M")}), encoding="utf-8")


def status():
    have = installed()
    paths.DATA.mkdir(parents=True, exist_ok=True)
    free_gb = round(shutil.disk_usage(paths.DATA).free / 1e9, 1)
    with _lock:
        state = {k: v for k, v in _state.items() if k != "cancel"}
    return {
        "setup_done": setup_done() or all(have.values()),
        "gpu": gpu(),
        "free_gb": free_gb,
        "data_dir": str(paths.DATA),
        "components": [{"id": k, **v, "installed": have[k]} for k, v in COMPONENTS.items()],
        "job": state,
    }


def _env():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
           "UV_PYTHON_INSTALL_DIR": str(paths.DATA / "python"), "UV_NO_PROGRESS": "1"}
    if paths.INSTALLED:
        env["UV_CACHE_DIR"] = str(paths.DATA / "cache")
    return env


def _steps(component):
    """Lista di (descrizione, comando) per un componente. None come comando = funzione Python da chiamare."""
    uv, py, code = uv_exe(), sys.executable, str(paths.CODE)
    if component == "drawings":
        install = ([uv, "pip", "install", "--python", py, "--index-strategy", "unsafe-best-match", "-r", f"{code}/requirements-images.txt"]
                   if uv else [py, "-m", "pip", "install", "-r", f"{code}/requirements-images.txt"])
        return [("Installo PyTorch e Stable Diffusion (circa 2,5 GB)", install),
                ("Scarico i modelli di disegno (circa 4,5 GB)",
                 [py, "-c", "from app.services.sd_worker import ensure_models; ensure_models(ip_adapter=True)"])]
    if component == "voices":
        if not uv:
            raise RuntimeError("Manca uv, che serve per l'ambiente delle voci: reinstalla ClipTale.")
        vpy = str(paths.VOICE_VENV / "Scripts" / "python.exe")
        return [("Preparo l'ambiente delle voci (Python 3.11)", [uv, "venv", str(paths.VOICE_VENV), "--python", "3.11", "--managed-python", "--allow-existing"]),
                ("Installo PyTorch per le voci (circa 2,5 GB)", [uv, "pip", "install", "--python", vpy, "torch==2.6.0", "torchaudio==2.6.0", "--index-url", TORCH_CU124]),
                ("Installo Chatterbox", [uv, "pip", "install", "--python", vpy, "chatterbox-tts==0.1.7", "setuptools<81",
                                         "--extra-index-url", TORCH_CU124, "--index-strategy", "unsafe-best-match"]),
                ("Scarico il modello delle voci (circa 3 GB)",
                 [vpy, "-c", "from chatterbox.mtl_tts import ChatterboxMultilingualTTS; ChatterboxMultilingualTTS.from_pretrained(device='cpu')"])]
    if component == "kokoro":
        from app.services.tts_service import download_kokoro
        return [("Scarico la voce Kokoro (circa 340 MB)", download_kokoro)]
    raise ValueError(component)


def _set(**kw):
    with _lock:
        _state.update(kw)


def _run_cmd(cmd):
    global _proc
    _proc = subprocess.Popen(cmd, cwd=str(paths.CODE), env=_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    tail = []
    for line in _proc.stdout:
        line = line.strip()
        if line:
            tail = (tail + [line])[-15:]
            _set(detail=line.removeprefix("STATUS ")[:200])
    code = _proc.wait()
    _proc = None
    if _state["cancel"]:
        raise RuntimeError("Annullato.")
    if code != 0:
        raise RuntimeError("\n".join(tail[-6:]) or f"errore {code}")


def _worker(items):
    try:
        plan = [(c, d, cmd) for c in items for d, cmd in _steps(c)]
        _set(total_steps=len(plan), done_steps=0)
        for i, (comp, desc, cmd) in enumerate(plan):
            if _state["cancel"]:
                raise RuntimeError("Annullato.")
            _set(current=comp, step=desc, done_steps=i, detail="")
            if comp == "voices" and (i == 0 or plan[i - 1][0] != comp):
                paths.VOICE_VENV.mkdir(parents=True, exist_ok=True)
                (paths.VOICE_VENV / "installing").touch()  # finché c'è, le voci non risultano pronte
            cmd() if callable(cmd) else _run_cmd(cmd)
            if i + 1 == len(plan) or plan[i + 1][0] != comp:
                if comp == "voices":
                    (paths.VOICE_VENV / "installing").unlink(missing_ok=True)
                with _lock:
                    _state["finished"] = _state["finished"] + [comp]
        _set(done_steps=len(plan), step="", detail="")
        import importlib
        importlib.invalidate_caches()  # i pacchetti appena installati diventano visibili all'app senza riavviarla
    except Exception as exc:  # noqa: BLE001 - qualsiasi errore va mostrato all'utente, non deve fermare l'app
        _set(error=str(exc)[:1500])
    finally:
        _set(running=False, current=None, cancel=False)


def install(items):
    items = [c for c in COMPONENTS if c in items]  # ordine fisso: prima i disegni, poi le voci
    if not items:
        raise ValueError("Nessun componente scelto.")
    with _lock:
        if _state["running"]:
            raise RuntimeError("C'è già un'installazione in corso.")
        _state.update(running=True, error="", finished=[], cancel=False, step="", detail="", done_steps=0, total_steps=0)
    threading.Thread(target=_worker, args=(items,), daemon=True, name="cliptale-components").start()


def cancel():
    with _lock:
        if not _state["running"]:
            return False
        _state["cancel"] = True
    if _proc and _proc.poll() is None:
        _proc.kill()
    return True
