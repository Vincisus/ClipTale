"""Avvio locale: prepara .env, controlla LM Studio, avvia il server e apre il browser."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

URL = "http://127.0.0.1:8000/"


def lmstudio_models(base):
    root = base[:-3] if base.endswith("/v1") else base
    try:
        with urllib.request.urlopen(root + "/api/v0/models", timeout=5) as r:
            return [(m["id"], m.get("state") == "loaded") for m in json.load(r).get("data", []) if m.get("type") in ("llm", "vlm")]
    except Exception:
        with urllib.request.urlopen(base + "/models", timeout=5) as r:
            return [(m["id"], None) for m in json.load(r).get("data", []) if "embed" not in m["id"]]


def main():
    os.chdir(Path(__file__).resolve().parent)
    if not Path(".env").exists():
        shutil.copyfile(".env.example", ".env")
        print("Creato .env da .env.example")
    from dotenv import load_dotenv, set_key
    load_dotenv(override=True)
    base = os.getenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
    model = os.getenv("LMSTUDIO_MODEL", "").strip().strip("'\"")
    try:
        models = lmstudio_models(base)
        ids = [m for m, _ in models]
        print("LM Studio raggiungibile. Modelli:", ", ".join(f"{m}{' (caricato)' if l else ''}" for m, l in models))
        if model not in ids:
            loaded = [m for m, l in models if l]
            chosen = loaded[0] if loaded else (ids[0] if ids else "")
            if chosen:
                print(f"Modello in .env non trovato ({model or 'vuoto'}): uso {chosen}. Puoi cambiarlo dalla pagina.")
                set_key(".env", "LMSTUDIO_MODEL", chosen, quote_mode="never")
    except Exception as exc:
        print(f"ATTENZIONE: LM Studio non risponde su {base} ({exc}).")
        print("Apri LM Studio > Developer > avvia il server locale. L'interfaccia si apre comunque.")

    def open_page():
        for _ in range(40):
            try:
                with urllib.request.urlopen(URL + "api/health", timeout=2):
                    webbrowser.open(URL)
                    return
            except Exception:
                time.sleep(1)

    threading.Thread(target=open_page, daemon=True).start()
    print("TiktokFactory Studio su", URL, "(chiudi questa finestra per fermarlo)")
    return subprocess.call([sys.executable, "-m", "uvicorn", "app.main:app", "--env-file", ".env", "--host", "127.0.0.1", "--port", "8000", "--no-access-log"])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:
        print("Avvio interrotto:", exc)
        sys.exit(1)
