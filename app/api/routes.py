import asyncio
import os
import shutil
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.core import jobs, projects
from app.core.pipeline import StoryPipeline, script_markdown
from app.core.render_pipeline import render_project
from app.services import eleven_service, illustration_service, publish_service, tts_service
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
    for v in voices:
        v["group"] = "pc"
    eleven = await eleven_service.voices()
    if eleven:
        voices.append({"id": "eleven:auto", "label": "Automatica: maschile o femminile in base a chi racconta", "engine": "eleven", "online": True, "group": "eleven"})
        voices += [{"id": f"eleven:{v['id']}", "label": v["label"], "engine": "eleven", "online": True, "group": "eleven", "gender": v["gender"]} for v in eleven]
    return {"voices": voices, "default": voices[0]["id"] if voices else None}


@router.get("/eleven/status")
async def eleven_status():
    return await eleven_service.status()


class ElevenKey(BaseModel):
    key: str = Field(default="", max_length=200, pattern=r"^[A-Za-z0-9_\-]*$")


@router.post("/eleven/key")
async def eleven_key(payload: ElevenKey, request: Request):
    same_origin(request)
    eleven_service.save_key(payload.key)
    return await eleven_service.status()


@router.get("/voices/preview")
async def voice_preview(voice: str, gender: str = ""):
    """Una frase di prova con la voce scelta (salvata in cache), per ascoltarla prima di creare il video."""
    import hashlib
    g = gender.upper()[:1]
    if voice.startswith("eleven:"):
        tts = tts_service.TTSService(voice, 165, g)
        vid = await tts.prepare()
        if not vid.startswith("eleven:"):
            raise HTTPException(404, "Nessuna voce ElevenLabs disponibile")
    else:
        vid = tts_service.resolve_voice(voice, g)
        if vid not in {v["id"] for v in await asyncio.to_thread(tts_service.list_voices)}:
            raise HTTPException(404, "Voce non disponibile")
    folder = projects.ROOT / "_voice_previews"
    folder.mkdir(parents=True, exist_ok=True)
    stem = folder / hashlib.sha1(f"{vid}|{tts_service.PREVIEW_TEXT}".encode()).hexdigest()[:16]
    found = [p for p in (stem.with_suffix(".wav"), stem.with_suffix(".mp3")) if p.is_file()]
    if not found:
        found = await tts_service.TTSService(vid, 165).synthesize_many([(tts_service.PREVIEW_TEXT, stem)])
    path = found[0]
    return FileResponse(path, media_type="audio/wav" if path.suffix == ".wav" else "audio/mpeg")


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
    format: Literal["reddit", "story", "explainer"] = "reddit"
    duration_minutes: float = Field(default=3, ge=0.5, le=30)
    parts: int = Field(default=3, ge=1, le=8)
    wpm: int = Field(default=150, ge=100, le=220)
    model: Optional[str] = Field(default=None, max_length=200)
    project_id: Optional[str] = Field(default=None, max_length=80)
    restart: bool = False


def prepare_brief(payload):
    """Crea (o riprende) la cartella del progetto e il brief. Restituisce (folder, brief, llm)."""
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
    return folder, brief, llm


@router.post("/generate")
async def generate(payload: GenerateRequest, request: Request):
    same_origin(request)
    folder, brief, llm = prepare_brief(payload)

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
    music: float = Field(default=0.25, ge=0, le=1)
    sfx: bool = True
    visuals: Literal["ai", "none"] = "ai"
    style: Literal["anim", "reel", "cartoon", "sketch", "comic"] = "anim"
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
        await render_project(folder, job, payload.voice, payload.wpm, payload.cut_seconds, payload.silence_ms, payload.visuals, payload.model, payload.style, payload.music, payload.sfx)

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
        "style_refs": len(illustration_service.style_refs()),
        "default_style": illustration_service.default_style(),
    }


# ------------------------------------------------------------------ tutto in automatico
class AutoRequest(GenerateRequest):
    voice: Optional[str] = Field(default=None, max_length=300)
    visuals: Literal["ai", "none"] = "ai"
    style: Literal["anim", "reel", "cartoon", "sketch", "comic"] = "anim"
    cut_seconds: float = Field(default=2.2, ge=0.8, le=6)
    silence_ms: int = Field(default=120, ge=0, le=1000)
    music: float = Field(default=0.25, ge=0, le=1)
    sfx: bool = True
    publish_youtube: bool = False
    youtube_privacy: Literal["private", "unlisted", "public"] = "private"
    publish_tiktok: bool = False
    tiktok_mode: Literal["draft", "direct"] = "draft"


@router.post("/auto")
async def auto(payload: AutoRequest, request: Request):
    """Dall'idea al video finito in un solo lavoro: copione (LM Studio) -> voce -> disegni -> video -> pubblicazione facoltativa."""
    same_origin(request)
    if not ffmpeg_exe():
        raise HTTPException(400, "FFmpeg non disponibile")
    folder, brief, llm = prepare_brief(payload)
    publish = payload.publish_youtube or payload.publish_tiktok
    render_end = 95 if publish else 100

    async def worker(job):
        def activity(text):
            job.data["activity"] = text
        llm.on_activity = activity
        job.log("Modalità automatica: copione, voce, disegni e video", module="auto", progress=1)
        await StoryPipeline(llm, folder, jobs.Stage(job, 1, 40)).run(brief)
        job.data["activity"] = None
        await render_project(folder, jobs.Stage(job, 40, render_end), payload.voice, payload.wpm, payload.cut_seconds,
                             payload.silence_ms, payload.visuals, llm.model, payload.style, payload.music, payload.sfx)
        if publish:
            stage = jobs.Stage(job, 95, 100)
            await publish_service.publish_project(
                folder, lambda m, p=None, level="info": stage.log(m, module="publish", progress=p, level=level),
                youtube={"privacy": payload.youtube_privacy} if payload.publish_youtube else None,
                tiktok={"mode": payload.tiktok_mode} if payload.publish_tiktok else None)

    job = jobs.start("auto", folder.name, worker)
    return {"job_id": job.id, "project_id": folder.name}


# ------------------------------------------------------------------ pubblicazione
@router.get("/publish/status")
async def publish_status():
    return publish_service.status()


class AppKeys(BaseModel):
    key: str = Field(min_length=4, max_length=300, pattern=r"^[A-Za-z0-9._\-]+$")
    secret: str = Field(min_length=4, max_length=300, pattern=r"^[A-Za-z0-9._\-]+$")


@router.post("/publish/{platform}/app")
async def publish_save_app(platform: Literal["youtube", "tiktok"], payload: AppKeys, request: Request):
    same_origin(request)
    publish_service.save_app(platform, payload.key, payload.secret)
    return publish_service.status()


@router.post("/publish/{platform}/disconnect")
async def publish_disconnect(platform: Literal["youtube", "tiktok"], request: Request):
    same_origin(request)
    publish_service.disconnect(platform)
    return publish_service.status()


@router.get("/publish/{platform}/connect")
async def publish_connect(platform: Literal["youtube", "tiktok"]):
    try:
        return RedirectResponse(publish_service.auth_url(platform))
    except ValueError as exc:
        return HTMLResponse(_page(str(exc), ok=False), status_code=400)


@router.get("/publish/{platform}/callback")
async def publish_callback(platform: Literal["youtube", "tiktok"], code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error or not code:
        return HTMLResponse(_page(f"Collegamento annullato: {error_description or error or 'nessun codice ricevuto'}", ok=False), status_code=400)
    try:
        await publish_service.finish_auth(platform, code, state)
    except Exception as exc:
        return HTMLResponse(_page(str(exc), ok=False), status_code=400)
    name = "YouTube" if platform == "youtube" else "TikTok"
    return HTMLResponse(_page(f"{name} collegato. Puoi chiudere questa scheda e tornare a TiktokFactory.", ok=True))


def _page(message, ok):
    from html import escape
    color = "#34d399" if ok else "#f87171"
    return (f"<!doctype html><meta charset='utf-8'><title>TiktokFactory</title><body style='font-family:system-ui;background:#0b0b0d;"
            f"color:#f2f2f3;display:grid;place-items:center;height:100vh;margin:0'><p style='max-width:520px;font-size:18px;"
            f"border:1px solid {color};padding:24px;border-radius:12px'>{escape(message)}</p>"
            + ("<script>try{window.opener&&window.opener.postMessage('tf-connected','*')}catch(e){};setTimeout(()=>window.close(),1500)</script>" if ok else ""))


@router.get("/projects/{project_id}/publish")
async def publish_info(project_id: str):
    folder = projects.folder(project_id)
    return {
        "youtube": publish_service.youtube_texts(folder),
        "tiktok": publish_service.tiktok_captions(folder),
        "history": projects.load_json(folder / "publish.json", {}) or {},
        "has_video": (folder / "render" / "full_video.mp4").is_file(),
    }


class PublishRequest(BaseModel):
    youtube: bool = False
    youtube_title: str = Field(default="", max_length=100)
    youtube_description: str = Field(default="", max_length=5000)
    youtube_privacy: Literal["private", "unlisted", "public"] = "private"
    tiktok: bool = False
    tiktok_mode: Literal["draft", "direct"] = "draft"
    tiktok_captions: list[str] = Field(default_factory=list, max_length=20)


@router.post("/projects/{project_id}/publish")
async def publish(project_id: str, payload: PublishRequest, request: Request):
    same_origin(request)
    folder = projects.folder(project_id)
    if not (folder / "render" / "full_video.mp4").is_file():
        raise HTTPException(400, "Prima crea il video.")
    if not (payload.youtube or payload.tiktok):
        raise HTTPException(400, "Scegli almeno YouTube o TikTok.")

    async def worker(job):
        await publish_service.publish_project(
            folder, lambda m, p=None, level="info": job.log(m, module="publish", progress=p, level=level),
            youtube={"title": payload.youtube_title, "description": payload.youtube_description, "privacy": payload.youtube_privacy} if payload.youtube else None,
            tiktok={"mode": payload.tiktok_mode, "captions": payload.tiktok_captions} if payload.tiktok else None)

    job = jobs.start("publish", project_id, worker)
    return {"job_id": job.id, "project_id": project_id}


@router.post("/projects/{project_id}/open-folder")
async def open_folder(project_id: str, request: Request, sub: str = "render/tiktok"):
    """Apre in Esplora risorse la cartella delle clip (solo dal PC locale), per il caricamento assistito su TikTok."""
    same_origin(request)
    folder = projects.folder(project_id)
    target = (folder / sub).resolve()
    if folder.resolve() not in (target, *target.parents) or not target.is_dir():
        raise HTTPException(404, "Cartella non trovata")
    if hasattr(os, "startfile"):
        os.startfile(str(target))  # noqa: S606 - solo Windows, cartella del progetto
    return {"ok": True, "path": str(target)}
