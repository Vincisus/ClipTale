"""Dove ClipTale tiene i suoi file.

- Versione di sviluppo (cartella del progetto): tutto accanto al codice, come sempre (projects/, models/, secrets/…).
- Versione installata (C:\\Program Files\\ClipTale, riconosciuta dal file installed.json messo dall'installer):
  il codice resta in sola lettura; ambienti Python, modelli e chiavi vanno in %LOCALAPPDATA%\\ClipTale,
  i video dell'utente in Video\\ClipTale.
CLIPTALE_DATA e CLIPTALE_PROJECTS cambiano queste cartelle (utile per i test).
"""
import ctypes
import os
from pathlib import Path

CODE = Path(__file__).resolve().parents[2]
INSTALLED = (CODE / "installed.json").is_file()


def _known_folder(csidl, fallback):
    """Cartella speciale di Windows (Video, Documenti…), anche se l'utente l'ha spostata o è su OneDrive."""
    try:
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
            return Path(buf.value)
    except (AttributeError, OSError):
        pass
    return Path.home() / fallback


def _data():
    if os.getenv("CLIPTALE_DATA"):
        return Path(os.environ["CLIPTALE_DATA"])
    if INSTALLED:
        return Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "ClipTale"
    return CODE


def _projects():
    if os.getenv("CLIPTALE_PROJECTS"):
        return Path(os.environ["CLIPTALE_PROJECTS"])
    if INSTALLED and not os.getenv("CLIPTALE_DATA"):
        return _known_folder(14, "Videos") / "ClipTale"  # CSIDL_MYVIDEO
    return DATA / "projects"


DATA = _data()
PROJECTS = _projects()
MODELS = DATA / "models"
SECRETS = DATA / "secrets"
STYLE_REFS = DATA / "style_refs"
VOICE_VENV = DATA / ".venv-voice"
ENV_FILE = DATA / ".env"
