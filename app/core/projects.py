"""Archivio progetti su disco: una cartella per progetto dentro projects/."""
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

ROOT = Path("projects")
ID_RE = re.compile(r"[A-Za-z0-9_-]+")


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def save_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def load_json(path, default=None):
    path = Path(path)
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default


def new_project_id():
    return datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]


def folder(project_id, must_exist=True):
    if not isinstance(project_id, str) or not ID_RE.fullmatch(project_id) or project_id.startswith("_"):
        raise HTTPException(400, "ID progetto non valido")
    path = ROOT / project_id
    if must_exist and not path.is_dir():
        raise HTTPException(404, "Progetto non trovato")
    return path


def safe_file(project_id, relative):
    """Risolve un file dentro la cartella del progetto, rifiutando percorsi esterni."""
    base = folder(project_id).resolve()
    path = (base / relative).resolve()
    if base not in path.parents or not path.is_file():
        raise HTTPException(404, "File non disponibile")
    return path


def outputs(path):
    """Elenca i file prodotti, come percorsi relativi alla cartella del progetto."""
    found = {}
    for name in ("script.md", "metadata.json", "render/full_video.mp4", "render/subtitles.srt"):
        if (path / name).is_file():
            found[name] = (path / name).stat().st_mtime
    clips = sorted(p.relative_to(path).as_posix() for p in (path / "render" / "tiktok").glob("part_*.mp4"))
    return {"files": sorted(found), "clips": clips}


def summary(path):
    brief = load_json(path / "brief.json", {}) or {}
    concept = load_json(path / "concept.json", {}) or {}
    job = load_json(path / "job.json", {}) or {}
    beats = load_json(path / "beats.json", []) or []
    out = outputs(path)
    return {
        "id": path.name,
        "title": concept.get("title") or brief.get("topic") or (brief.get("request") or {}).get("message") or path.name,
        "topic": brief.get("topic") or (brief.get("request") or {}).get("message", ""),
        "created": brief.get("created_at", ""),
        "has_plan": (path / "concept.json").is_file() and (path / "structure.json").is_file(),
        "has_script": bool(beats),
        "has_video": "render/full_video.mp4" in out["files"],
        "last_job": {k: job.get(k) for k in ("kind", "status", "current_step", "error")} if job else None,
    }


def list_projects():
    ROOT.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(ROOT.iterdir(), reverse=True):
        if path.is_dir() and ID_RE.fullmatch(path.name) and not path.name.startswith("_") and (path / "brief.json").is_file():
            items.append(summary(path))
    return items


def detail(project_id):
    path = folder(project_id)
    data = summary(path)
    data.update(
        brief=load_json(path / "brief.json", {}),
        concept=load_json(path / "concept.json"),
        structure=load_json(path / "structure.json"),
        beats=load_json(path / "beats.json", []),
        metadata=load_json(path / "metadata.json"),
        render=load_json(path / "render" / "render.json"),
        outputs=outputs(path),
    )
    return data
