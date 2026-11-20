"""ClipTale.exe: avvia l'app senza finestre di console.

- Versione di sviluppo: usa .venv/ accanto all'eseguibile, come Avvia.bat.
- Versione installata (installed.json accanto all'eseguibile): la prima volta, e dopo ogni aggiornamento che cambia
  le librerie, prepara l'ambiente Python in %LOCALAPPDATA%\\ClipTale\\.venv con uv (incluso nell'installer), mostrando
  una piccola finestra di avanzamento. Poi lancia desktop/launcher.py con pythonw.
Se manca qualcosa, mostra un messaggio invece di chiudersi in silenzio.
Costruito da desktop/build_exe.py con PyInstaller (un solo file, con l'icona arancione).
"""
import ctypes
import hashlib
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

NO_WINDOW = 0x08000000
PYTHON = "3.12"


def message(text):
    ctypes.windll.user32.MessageBoxW(None, text, "ClipTale", 0x10)


def app_root():
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]


def data_dir():
    return Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "ClipTale"


def wanted_hash(root):
    return hashlib.sha256((root / "requirements.txt").read_bytes() + PYTHON.encode()).hexdigest()[:16]


def setup_env(root, data, progress):
    """Crea (o aggiorna) l'ambiente Python dell'app. progress(testo) riceve le righe di stato."""
    uv = root / "tools" / "uv.exe"
    venv = data / ".venv"
    env = {**os.environ, "UV_PYTHON_INSTALL_DIR": str(data / "python"), "UV_CACHE_DIR": str(data / "cache"),
           "UV_NO_PROGRESS": "1", "VIRTUAL_ENV": ""}

    def run(cmd, what):
        progress(what)
        proc = subprocess.Popen([str(c) for c in cmd], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        tail = []
        for line in proc.stdout:
            if line.strip():
                tail = (tail + [line.strip()])[-8:]
                progress(what, line.strip())
        if proc.wait() != 0:
            raise RuntimeError("\n".join(tail))

    data.mkdir(parents=True, exist_ok=True)
    if not (venv / "Scripts" / "python.exe").is_file():
        run([uv, "venv", venv, "--python", PYTHON, "--managed-python", "--seed"], "Preparo Python (solo la prima volta)…")
    run([uv, "pip", "install", "--python", venv / "Scripts" / "python.exe", "-r", root / "requirements.txt"],
        "Installo le librerie di ClipTale (circa 300 MB, solo la prima volta)…")
    (venv / "cliptale_ready").write_text(wanted_hash(root), encoding="utf-8")


def setup_with_window(root, data):
    """setup_env con una finestra arancione e nera; True se è andato tutto bene."""
    import tkinter as tk
    from tkinter import ttk

    msgs, result = queue.Queue(), {}
    win = tk.Tk()
    win.title("ClipTale")
    win.configure(bg="#0a0a0a")
    win.resizable(False, False)
    ico = root / "desktop" / "cliptale.ico"
    if ico.is_file():
        try:
            win.iconbitmap(str(ico))
        except tk.TclError:
            pass
    w, h = 520, 190
    win.geometry(f"{w}x{h}+{(win.winfo_screenwidth() - w) // 2}+{(win.winfo_screenheight() - h) // 3}")
    tk.Label(win, text="ClipTale", fg="#ff7a00", bg="#0a0a0a", font=("Segoe UI Semibold", 18)).pack(anchor="w", padx=24, pady=(20, 4))
    title = tk.Label(win, text="Preparazione…", fg="#f5f5f5", bg="#0a0a0a", font=("Segoe UI", 11), anchor="w")
    title.pack(fill="x", padx=24)
    style = ttk.Style(win)
    style.theme_use("clam")
    style.configure("O.Horizontal.TProgressbar", troughcolor="#222228", background="#ff7a00", bordercolor="#0a0a0a", lightcolor="#ff7a00", darkcolor="#ff7a00")
    bar = ttk.Progressbar(win, mode="indeterminate", style="O.Horizontal.TProgressbar", length=w - 48)
    bar.pack(padx=24, pady=12)
    bar.start(12)
    detail = tk.Label(win, text="", fg="#8a8a93", bg="#0a0a0a", font=("Consolas", 9), anchor="w")
    detail.pack(fill="x", padx=24)

    def work():
        try:
            setup_env(root, data, lambda what, line="": msgs.put((what, line)))
            result["ok"] = True
        except Exception as exc:  # noqa: BLE001 - l'errore va mostrato all'utente
            result["error"] = str(exc)
        msgs.put(None)

    def pump():
        try:
            while True:
                item = msgs.get_nowait()
                if item is None:
                    win.destroy()
                    return
                title.config(text=item[0])
                detail.config(text=item[1][:80])
        except queue.Empty:
            pass
        win.after(100, pump)

    win.protocol("WM_DELETE_WINDOW", lambda: None)  # non si chiude a metà
    threading.Thread(target=work, daemon=True).start()
    win.after(100, pump)
    win.mainloop()
    if not result.get("ok"):
        message("Non è stato possibile preparare ClipTale. Controlla la connessione a Internet e riprova.\n\n"
                + (result.get("error") or "")[-800:])
        return False
    return True


def main():
    root = app_root()
    launcher = root / "desktop" / "launcher.py"
    # le variabili che PyInstaller mette per sé (Tcl/Tk nella cartella temporanea) non devono arrivare all'app
    env = {k: v for k, v in os.environ.items() if k not in ("TCL_LIBRARY", "TK_LIBRARY") and not k.startswith("_PYI")}
    env["PYTHONIOENCODING"] = "utf-8"
    if (root / "installed.json").is_file():
        data = data_dir()
        venv = data / ".venv"
        marker = venv / "cliptale_ready"
        if not (root / "tools" / "uv.exe").is_file() or not launcher.is_file():
            message("ClipTale non è installato correttamente: mancano dei file.\nReinstallalo con ClipTale-Setup.exe.")
            return 1
        ready = marker.is_file() and marker.read_text(encoding="utf-8").strip() == wanted_hash(root)
        if not ready and not setup_with_window(root, data):
            return 1
        pythonw = venv / "Scripts" / "pythonw.exe"
        env["PYTHONPYCACHEPREFIX"] = str(data / "pycache")  # Program Files è in sola lettura
    else:
        pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
        if not launcher.is_file() or not pythonw.is_file():
            message("Manca l'ambiente Python di sviluppo (.venv): avvia prima Avvia.bat.")
            return 1
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(pythonw), str(launcher)], cwd=str(root), creationflags=flags, env=env, close_fds=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
