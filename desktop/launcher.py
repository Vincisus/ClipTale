"""ClipTale come programma: avvia il server, apre la finestra dell'app e mette l'icona nella barra delle applicazioni.

- Una sola copia: se ClipTale è già aperto, una seconda apertura porta in primo piano la finestra esistente.
- Chiudendo la finestra l'app resta nella barra (i lavori continuano); si esce da "Esci" nel menu dell'icona.
- Clic (sinistro o destro) sull'icona: menu con il progetto in corso, l'avanzamento e i tok/s del motore AI.
- Notifiche di Windows a fine video, a fine pubblicazione e in caso di errore.
Uso: pythonw desktop/launcher.py  (ClipTale.exe fa esattamente questo).
"""
import json
import logging
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core import paths  # noqa: E402

paths.DATA.mkdir(parents=True, exist_ok=True)
sys.argv[0] = str(Path(__file__).resolve())  # pywebview cerca i file a partire da qui: dopo il chdir un percorso relativo non vale più
os.chdir(paths.DATA)  # installata: il codice è in sola lettura, si lavora nella cartella dei dati
PORT = int(os.getenv("CLIPTALE_PORT", "8000"))
URL = f"http://127.0.0.1:{PORT}"
DATA = Path(os.getenv("LOCALAPPDATA", str(ROOT))) / "ClipTale"
DATA.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=str(DATA / "cliptale.log"), level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cliptale")

ORANGE, BLACK = (255, 122, 0, 255), (10, 10, 10, 255)
ICO = ROOT / "desktop" / "cliptale.ico"
APP_ID = "ClipTale.Desktop"  # Windows raggruppa la finestra sotto questo nome (e non sotto Python), con l'icona di ClipTale
KIND = {"render": "Creazione del video", "auto": "Creazione automatica", "publish": "Pubblicazione", "generate": "Scrittura del copione"}


# ------------------------------------------------------------------ server
def call(path, payload=None, timeout=3):
    """Chiamata al server locale: dict, oppure None se non risponde."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(URL + path, data=data, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except Exception:
        return None


def start_server():
    import uvicorn
    from app.main import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", access_log=False))
    server.install_signal_handlers = lambda: None  # gira in un thread: i segnali li gestisce il programma
    threading.Thread(target=server.run, name="server", daemon=True).start()
    for _ in range(150):
        if call("/api/health", timeout=2) is not None:
            return server
        time.sleep(0.2)
    raise RuntimeError("Il server di ClipTale non è partito")


# ------------------------------------------------------------------ icona
def make_icon(progress=None, error=False, size=64):
    """Logo ClipTale (quadrato arancione con il triangolo nero); durante un lavoro una barra di avanzamento in basso."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, size - 3, size - 3], radius=size // 4, fill=ORANGE)
    s = size / 64
    d.polygon([(24 * s, 16 * s), (24 * s, 44 * s), (47 * s, 30 * s)], fill=BLACK)
    if progress is not None:
        d.rectangle([8 * s, 52 * s, 56 * s, 58 * s], fill=BLACK)
        d.rectangle([8 * s, 52 * s, (8 + 48 * max(0.04, min(1, progress / 100))) * s, 58 * s], fill=(255, 255, 255, 255))
    if error:
        d.ellipse([size - 22, 2, size - 2, 22], fill=(230, 40, 40, 255), outline=BLACK, width=2)
    return img


class App:
    def __init__(self):
        self.window = None
        self.icon = None
        self.server = None
        self.quitting = False
        self.job = None
        self.llm = None
        self.queue = {"paused": False, "items": []}
        self.last_status = {}
        self.error = False

    # -------------------------------------------------------------- finestra
    def pick_folder(self, start=None):
        """Finestra di Windows per scegliere una cartella; None se l'utente annulla."""
        import webview
        self.show()
        res = self.window.create_file_dialog(webview.FileDialog.FOLDER, directory=start or "")
        return res[0] if res else None

    def show(self, project_id=None):
        if not self.window:
            return
        self.window.show()
        self.window.restore()
        try:
            self.window.on_top = True
            self.window.on_top = False
        except Exception:
            pass
        if project_id:
            self.window.evaluate_js(f"openProject({json.dumps(project_id)}, {{autoStep: true}})")

    def on_closing(self):
        if self.quitting:
            return True
        self.window.hide()  # resta nella barra: i lavori continuano
        if not getattr(self, "_told", False):
            self._told = True
            self.notify("ClipTale continua a lavorare qui nella barra. Per chiuderlo davvero: Esci nel menu dell'icona.")
        return False

    # -------------------------------------------------------------- menu dell'icona
    def status_text(self, _item=None):
        j = self.job
        if j:
            step = (j.get("current_step") or "").replace("\n", " ")
            return f"{KIND.get(j.get('kind'), 'Lavoro')} · {int(j.get('progress') or 0)}% · {step[:60]}"
        return "Nessun lavoro in corso"

    def speed_text(self, _item=None):
        if self.llm:
            return f"Motore AI: {self.llm['tps']:.1f} tok/s{' (ultimo valore)' if not self.llm.get('active') else ''}"
        q = len(self.queue.get("items") or [])
        return f"In coda: {q} video" + (" · in pausa" if self.queue.get("paused") else "") if q else "Coda vuota"

    def open_project(self, *_):
        self.show(self.job.get("project_id") if self.job else None)

    def open_videos(self, *_):
        folder = paths.PROJECTS
        if self.job and self.job.get("project_id"):
            folder = folder / self.job["project_id"] / "render"
        os.startfile(str(folder if folder.exists() else paths.PROJECTS))  # noqa: S606 - cartella del programma

    def toggle_pause(self, *_):
        call("/api/queue/pause", {"paused": not self.queue.get("paused")})
        self.poll_once()

    def quit(self, *_, force=False):
        if self.job and not force:
            self.show()
            ok = self.window.create_confirmation_dialog(
                "Esci da ClipTale", "C'è un video in lavorazione. Uscendo si interrompe; il lavoro già fatto resta salvato e si può riprendere. Uscire?")
            if not ok:
                return
        self.quitting = True
        if self.icon:
            self.icon.stop()
        if self.window:
            self.window.destroy()

    def build_tray(self):
        import pystray
        from pystray import Menu, MenuItem as Item
        import pystray._win32 as win32tray

        class Tray(pystray.Icon):
            def _on_notify(self, wparam, lparam):  # clic sinistro = stesso menu del destro
                if lparam == win32tray.win32.WM_LBUTTONUP:
                    lparam = win32tray.win32.WM_RBUTTONUP
                return super()._on_notify(wparam, lparam)

        menu = Menu(
            Item(self.status_text, None, enabled=False),
            Item(self.speed_text, None, enabled=False),
            Menu.SEPARATOR,
            Item("Apri ClipTale", lambda *_: self.show(), default=True),
            Item("Apri il progetto in corso", self.open_project, enabled=lambda _i: bool(self.job)),
            Item("Apri la cartella dei video", self.open_videos),
            Item(lambda _i: "Riprendi la coda" if self.queue.get("paused") else "Metti in pausa la coda", self.toggle_pause),
            Menu.SEPARATOR,
            Item("Esci da ClipTale", self.quit),
        )
        self.icon = Tray("ClipTale", make_icon(), "ClipTale", menu)
        self.icon.run_detached()

    def notify(self, message, title="ClipTale"):
        try:
            self.icon.notify(message, title)
        except Exception:
            log.exception("notifica non mostrata")

    # -------------------------------------------------------------- avanzamento
    def poll_once(self):
        cur = call("/api/jobs/current") or {}
        job = cur.get("job")
        self.queue = call("/api/queue") or self.queue
        if self.job and not job:  # il lavoro è appena finito: com'è andato?
            done = call(f"/api/jobs/{self.job['id']}") or {}
            status = done.get("status")
            what = KIND.get(done.get("kind"), "Lavoro")
            if status == "completed":
                self.error = False
                self.notify({"publish": "Pubblicazione completata."}.get(done.get("kind"), "Il video è pronto: aprilo da ClipTale."), what)
            elif status in ("failed", "interrupted"):
                self.error = True
                self.notify(f"Non riuscito: {(done.get('error') or 'apri ClipTale per i dettagli')[:180]}", what)
        if job and not self.job:
            self.error = False
        self.job = job
        self.llm = (job or {}).get("llm")
        if self.icon:
            self.icon.icon = make_icon(job.get("progress") if job else None, self.error)
            self.icon.title = ("ClipTale · " + self.status_text())[:127]
            self.icon.update_menu()

    def watch(self):
        while not self.quitting:
            try:
                self.poll_once()
            except Exception:
                log.exception("aggiornamento dell'icona non riuscito")
            time.sleep(2)

    # -------------------------------------------------------------- avvio
    def run(self):
        import webview
        from app.core import desktop
        if call("/api/health", timeout=2) is not None:  # ClipTale è già aperto: mostra quella finestra
            if call("/api/app/show", {}) is None:
                import webbrowser
                webbrowser.open(URL)
            return
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
            # il disinstallatore vede questo segnale e chiede di chiudere ClipTale prima di togliere i file
            self._mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "ClipTaleRunning")
        except Exception:
            pass
        self.server = start_server()
        desktop.HOOKS["show"] = lambda pid=None: self.show(pid)
        desktop.HOOKS["pick_folder"] = self.pick_folder
        desktop.HOOKS["quit"] = lambda: self.quit(force=True)  # aggiornamento: l'installer prende il posto dell'app
        # ?v=versione: dopo un aggiornamento la finestra non può riusare la pagina della versione prima
        self.window = webview.create_window("ClipTale", f"{URL}/?v={desktop.VERSION}", width=1400, height=880, min_size=(1000, 680), background_color="#0a0a0a")
        self.window.events.closing += self.on_closing
        self.build_tray()
        threading.Thread(target=self.watch, name="watch", daemon=True).start()
        webview.start(private_mode=False, storage_path=str(DATA / "webview"), icon=str(ICO))  # icona arancione anche nella barra delle app  # ritorna quando la finestra viene distrutta
        self.quitting = True
        if self.icon:
            self.icon.stop()
        if self.server:
            self.server.should_exit = True


if __name__ == "__main__":
    try:
        App().run()
    except Exception:
        log.exception("ClipTale si è fermato")
        raise
