"""Un solo lavoro alla volta (il modello locale e uno), eseguito come task asyncio e salvato su disco."""
import asyncio
import logging
import uuid
from datetime import datetime

from fastapi import HTTPException

from app.core.projects import ROOT, load_json, save_json

logger = logging.getLogger("uvicorn.error")
JOBS = {}
_current = {"id": None, "task": None}


class Job:
    def __init__(self, kind, project_id):
        self.data = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "project_id": project_id,
            "status": "queued",
            "progress": 0,
            "current_step": "In coda",
            "logs": [],
            "error": None,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

    @property
    def id(self):
        return self.data["id"]

    def persist(self):
        save_json(ROOT / self.data["project_id"] / "job.json", self.data)

    def log(self, message, module="system", progress=None, level="info"):
        if progress is not None:
            self.data["progress"] = max(0, min(100, int(progress)))
        self.data["current_step"] = message
        entry = {"ts": datetime.now().strftime("%H:%M:%S"), "mod": module, "msg": message, "level": level}
        self.data["logs"] = (self.data["logs"] + [entry])[-400:]
        logger.info("[%s] %s", module, message)
        self.persist()


def active():
    task = _current["task"]
    return JOBS.get(_current["id"]) if task is not None and not task.done() else None


def start(kind, project_id, worker):
    """worker: coroutine function(job) che esegue il lavoro e aggiorna job.log()."""
    if active():
        raise HTTPException(409, "Un lavoro e gia in corso: attendi o annullalo.")
    job = Job(kind, project_id)
    JOBS[job.id] = job

    async def runner():
        job.data["status"] = "running"
        job.persist()
        try:
            await worker(job)
            job.data["status"] = "completed"
            job.log("Completato", progress=100, level="ok")
        except asyncio.CancelledError:
            job.data["status"] = "cancelled"
            job.log("Annullato: il lavoro gia salvato resta su disco e puo essere ripreso", level="warn")
        except Exception as exc:
            logger.exception("Lavoro %s fallito", job.id)
            job.data["status"] = "failed"
            job.data["error"] = f"{type(exc).__name__}: {exc}"
            job.log("Errore: " + job.data["error"], level="error")

    _current["id"] = job.id
    _current["task"] = asyncio.create_task(runner())
    job.persist()
    return job


def get(job_id):
    if job_id in JOBS:
        return JOBS[job_id].data
    for path in ROOT.glob("*/job.json"):
        data = load_json(path, {})
        if data.get("id") == job_id:
            if data.get("status") in ("queued", "running"):
                data["status"] = "interrupted"
                data["current_step"] = "Interrotto (server riavviato). Puoi riprendere dal progetto."
            return data
    raise HTTPException(404, "Job non trovato")


def cancel(job_id):
    job = active()
    if not job or job.id != job_id:
        raise HTTPException(404, "Nessun lavoro attivo con questo ID")
    _current["task"].cancel()
    return {"ok": True}
