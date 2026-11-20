"""Preferenze dell'app (non i dati dei progetti): cartella dove esportare i video, lingua dell'interfaccia.

Salvate in <dati>/settings.json. Le chiavi API stanno a parte, in secrets/.
"""
import json
from pathlib import Path

from app.core import paths

FILE = paths.DATA / "settings.json"
UI_LANGS = ("it", "en", "es", "fr", "de")


def default_export_dir():
    """Video\\ClipTale: la cartella dei video di Windows, dove l'utente se li aspetta."""
    return paths.known_folder(14, "Videos") / "ClipTale"


def load():
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def get():
    data = load()
    return {
        "export_dir": data.get("export_dir") or str(default_export_dir()),
        "export_default": not data.get("export_dir"),
        "auto_export": data.get("auto_export", True),
        "ui_lang": data.get("ui_lang") if data.get("ui_lang") in UI_LANGS else "it",
    }


def update(**changes):
    data = load()
    for key, value in changes.items():
        if value is None:
            continue
        if key == "export_dir" and value:
            value = str(Path(value).expanduser())
            if value == str(default_export_dir()):
                value = ""  # di nuovo quella predefinita
        data[key] = value
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return get()


def export_dir():
    return Path(get()["export_dir"])
