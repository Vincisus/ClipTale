"""Archivio progetti su disco: una cartella per progetto dentro projects/."""
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from app.core.paths import PROJECTS as ROOT  # cartella dei progetti: vedi app/core/paths.py

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


def outputs(path, base=None):
    """Elenca i file prodotti (di una lingua: base), come percorsi relativi alla cartella del progetto."""
    base = Path(base or path)
    prefix = "" if base == Path(path) else base.relative_to(path).as_posix() + "/"
    found = {}
    for name in ("script.md", "metadata.json", "render/full_video.mp4", "render/subtitles.srt"):
        if (base / name).is_file():
            found[prefix + name] = (base / name).stat().st_mtime
    clips = sorted(prefix + p.relative_to(base).as_posix() for p in (base / "render" / "tiktok").glob("part_*.mp4"))
    return {"files": sorted(found), "clips": clips, "prefix": prefix}


def language_status(path, brief=None):
    """Per ogni lingua del progetto: copione, video e pubblicazioni."""
    from app.core import languages
    brief = brief if brief is not None else (load_json(path / "brief.json", {}) or {})
    out = []
    for code in languages.of_brief(brief):
        base = languages.lang_dir(path, code, brief)
        pub = load_json(base / "publish.json", {}) or {}
        out.append({"code": code, "name": languages.info(code)["name"], "main": base == path,
                    "has_script": bool(load_json(base / "beats.json")), "has_video": (base / "render" / "full_video.mp4").is_file(),
                    "youtube": bool(pub.get("youtube")), "shorts": bool(pub.get("shorts")), "tiktok": bool(pub.get("tiktok"))})
    return out


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
        "languages": language_status(path, brief),
    }


def list_projects():
    ROOT.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(ROOT.iterdir(), reverse=True):
        if path.is_dir() and ID_RE.fullmatch(path.name) and not path.name.startswith("_") and (path / "brief.json").is_file():
            items.append(summary(path))
    return items


def detail(project_id, lang=None):
    """Dati del progetto visti in una lingua (la principale se lang è vuoto): copione, testi, video e file di quella lingua."""
    from app.core import languages
    path = folder(project_id)
    data = summary(path)
    brief = load_json(path / "brief.json", {}) or {}
    codes = languages.of_brief(brief)
    lang = lang if lang in codes else codes[0]
    base = languages.lang_dir(path, lang, brief)
    data.update(
        lang=lang,
        brief=brief,
        concept=load_json(path / "concept.json"),
        structure=load_json(base / "structure.json") or load_json(path / "structure.json"),
        beats=load_json(base / "beats.json", []),
        metadata=load_json(base / "metadata.json"),
        render=load_json(base / "render" / "render.json"),
        outputs=outputs(path, base),
    )
    return data
