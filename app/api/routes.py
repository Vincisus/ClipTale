import asyncio
import os
import shutil
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core import jobs, projects
from app.core.pipeline import StoryPipeline, script_markdown
from app.core.render_pipeline import render_project
from app.services import illustration_service, tts_service
from app.services.llm_service import LLMService
from app.services.video_service import ffmpeg_exe

router = APIRouter(prefix="/api")


def same_origin(request: Request):
    """Blocca richieste di scrittura provenienti da altri siti aperti nel browser."""
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Operazione consentita solo dalla pagina locale")


# ------------------------------------------------------------------ stato
@router.get("/health")
async def health():
    llm = LLMService()
    info = {"model": llm.model, "thinking": llm.thinking, "ffmpeg": bool(ffmpeg_exe()), "edge_tts": tts_service.EDGE_AVAILABLE, "lmstudio": False, "model_loaded": None}
    try:
        models = await llm.list_models()
        info["lmstudio"] = True
        match = next((m for m in models if m["id"] == llm.model), None)
        info["model_loaded"] = match["loaded"] if match else False
    except Exception as exc:
        info["lmstudio_error"] = str(exc)
    info["busy"] = jobs.active().data if jobs.active() else None
    return info


@router.get("/models")
async def models():
    llm = LLMService()
    try:
        return {"default": llm.model, "models": await llm.list_models()}
    except Exception as exc:
        raise HTTPException(502, f"LM Studio non raggiungibile su {llm.base_url}: avvia il server locale ({exc})")


@router.get("/voices")
async def voices():
    voices = await asyncio.to_thread(tts_service.list_voices)
    return {"voices": voices, "default": voices[0]["id"] if voices else None}


# ------------------------------------------------------------------ progetti
@router.get("/projects")
async def list_projects():
    return {"projects": projects.list_projects()}


@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    return projects.detail(project_id)


class Beat(BaseModel):
    part: int = Field(ge=1, le=20)
    role: str = Field(default="", max_length=40)
    text: str = Field(default="", max_length=8000)
    cue: str = Field(default="", max_length=300)


class BeatsUpdate(BaseModel):
    beats: list[Beat] = Field(max_length=400)


@router.put("/projects/{project_id}/beats")
async def save_beats(project_id: str, payload: BeatsUpdate, request: Request):
    same_origin(request)
    folder = projects.folder(project_id)
    active = jobs.active()
    if active and active.data["project_id"] == project_id:
        raise HTTPException(409, "Il progetto e in lavorazione: attendi la fine prima di salvare.")
    beats = [b.model_dump() for b in payload.beats]
    projects.save_json(folder / "beats.json", beats)
    concept = projects.load_json(folder / "concept.json", {}) or {}
    projects.save_text(folder / "script.md", script_markdown(concept, projects.load_json(folder / "structure.json", {}), beats))
    return {"ok": True, "count": len(beats)}


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, request: Request):
    same_origin(request)
    folder = projects.folder(project_id)
    active = jobs.active()
    if active and active.data["project_id"] == project_id:
        raise HTTPException(409, "Progetto in lavorazione")
    shutil.rmtree(folder)
    return {"ok": True}


@router.get("/projects/{project_id}/file/{path:path}")
async def project_file(project_id: str, path: str, download: bool = False):
    file = projects.safe_file(project_id, path)
    if file.suffix.lower() not in (".mp4", ".srt", ".md", ".json", ".wav", ".mp3", ".txt", ".png"):
        raise HTTPException(404, "Tipo di file non esposto")
    return FileResponse(file, filename=f"{project_id}_{file.name}" if download else None)


# ------------------------------------------------------------------ lavori
class GenerateRequest(BaseModel):
    topic: str = Field(default="", max_length=4000)
    format: Literal["story", "explainer"] = "story"
    duration_minutes: float = Field(default=3, ge=0.5, le=30)
    parts: int = Field(default=3, ge=1, le=8)
    wpm: int = Field(default=150, ge=100, le=220)
    model: Optional[str] = Field(default=None, max_length=200)
    project_id: Optional[str] = Field(default=None, max_length=80)
    restart: bool = False


@router.post("/generate")
async def generate(payload: GenerateRequest, request: Request):
    same_origin(request)
    if jobs.active():
        raise HTTPException(409, "Un lavoro e gia in corso: attendi o annullalo.")
    llm = LLMService(model=payload.model or None)
    if not llm.model:
        raise HTTPException(400, "Seleziona un modello LM Studio.")
    if payload.project_id:  # ripresa / rigenerazione di un progetto esistente
        folder = projects.folder(payload.project_id)
        brief = projects.load_json(folder / "brief.json", {}) or {}
        brief.setdefault("topic", (brief.get("request") or {}).get("message", "") or payload.topic)
        brief.setdefault("format", payload.format)
        brief.setdefault("duration_minutes", brief.get("duration_minutes") or payload.duration_minutes)
        brief["parts"] = int(brief.get("parts") or brief.get("tiktok_clips") or payload.parts)
        brief.setdefault("wpm", payload.wpm)
        if payload.restart:
            for name in ("concept.json", "structure.json", "beats.json", "metadata.json", "script.md"):
                (folder / name).unlink(missing_ok=True)
            shutil.rmtree(folder / "parts", ignore_errors=True)
    else:
        if not payload.topic.strip():
            raise HTTPException(400, "Scrivi un argomento o un'idea per il video.")
        project_id = projects.new_project_id()
        folder = projects.folder(project_id, must_exist=False)
        folder.mkdir(parents=True)
        brief = {"id": project_id, "topic": payload.topic.strip(), "format": payload.format, "duration_minutes": payload.duration_minutes, "parts": payload.parts, "wpm": payload.wpm, "created_at": datetime.now().isoformat(timespec="seconds")}
    brief["model"] = llm.model
    projects.save_json(folder / "brief.json", brief)

    async def worker(job):
        def activity(text):
            job.data["activity"] = text
        llm.on_activity = activity
        await StoryPipeline(llm, folder, job).run(brief)
        job.data["activity"] = None

    job = jobs.start("generate", folder.name, worker)
    return {"job_id": job.id, "project_id": folder.name}


class RenderRequest(BaseModel):
    voice: Optional[str] = Field(default=None, max_length=300)
    visuals: Literal["ai", "none"] = "ai"
    style: Literal["cartoon", "sketch", "comic"] = "cartoon"
    model: Optional[str] = Field(default=None, max_length=200)
    wpm: int = Field(default=165, ge=100, le=220)
    cut_seconds: float = Field(default=2.2, ge=0.8, le=6)
    silence_ms: int = Field(default=120, ge=0, le=1000)


@router.post("/projects/{project_id}/render")
async def render(project_id: str, payload: RenderRequest, request: Request):
    same_origin(request)
    folder = projects.folder(project_id)
    if not ffmpeg_exe():
        raise HTTPException(400, "FFmpeg non disponibile")

    async def worker(job):
        await render_project(folder, job, payload.voice, payload.wpm, payload.cut_seconds, payload.silence_ms, payload.visuals, payload.model, payload.style)

    job = jobs.start("render", project_id, worker)
    return {"job_id": job.id, "project_id": project_id}


@router.get("/jobs/current")
async def current_job():
    job = jobs.active()
    return {"job": job.data if job else None}


@router.get("/jobs/{job_id}")
async def job_status(job_id: str):
    return jobs.get(job_id)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request):
    same_origin(request)
    return jobs.cancel(job_id)


@router.get("/config")
async def config():
    return {"lmstudio_url": os.getenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1")}


# ------------------------------------------------------------------ illustrazioni
@router.get("/illustrations/status")
async def illustrations_status():
    return {
        "available": illustration_service.available(),
        "models_ready": illustration_service.models_ready(),
        "styles": [{"id": k, "label": v["label"]} for k, v in illustration_service.STYLES.items()],
    }
