"""Avvio locale: prepara .env, controlla LM Studio, avvia il server e apre il browser."""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
PORTS = range(8000, 8011)


def port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, port))
            return True
        except OSError:
            return False


def is_tiktokfactory(port):
    """True se sulla porta risponde già TiktokFactory (avviato da un'altra finestra)."""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/health", timeout=3) as r:
            return "lmstudio" in json.load(r)
    except Exception:
        return False


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

    # già aperto in un'altra finestra? allora basta aprire la pagina
    for port in PORTS:
        if not port_free(port) and is_tiktokfactory(port):
            url = f"http://{HOST}:{port}/"
            print(f"TiktokFactory Studio è già in esecuzione su {url}: apro la pagina.")
            print("Per riavviarlo chiudi prima l'altra finestra di Avvia.bat.")
            webbrowser.open(url)
            return 0
    port = next((p for p in PORTS if port_free(p)), None)
    if port is None:
        print(f"Le porte {PORTS.start}-{PORTS.stop - 1} sono tutte occupate da altri programmi: chiudine uno e riprova.")
        return 1
    if port != PORTS.start:
        print(f"La porta {PORTS.start} è occupata da un altro programma: uso la {port}.")
        print("Nota: per YouTube/TikTok il Redirect URI registrato deve usare questa stessa porta.")
    URL = f"http://{HOST}:{port}/"
    os.environ["APP_BASE_URL"] = URL.rstrip("/")  # usato per i collegamenti OAuth

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
    return subprocess.call([sys.executable, "-m", "uvicorn", "app.main:app", "--env-file", ".env", "--host", HOST, "--port", str(port), "--no-access-log"])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:
        print("Avvio interrotto:", exc)
        sys.exit(1)
