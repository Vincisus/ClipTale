<<<<<<< HEAD

=======
<<<<<<< HEAD
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
"""FastAPI application con serving file statici per console web."""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as main_router
from app.core import card_coherence, scene_contract
from app.core.lean_pipeline import router as lean_router
from app.core.orchestrator import Orchestrator
from app.core.project_console import project as resolve_project

# Attiva patch di coerenza schede e contratto scene all'avvio del server
card_coherence.install()
scene_contract.install()

app = FastAPI(title="TiktokFactory", version="1.0.0")

web_dir = Path(__file__).parent / "web"
app.mount("/static", StaticFiles(directory=web_dir, html=True))

# project_console (incluso in main_router) espone /api/projects
app.include_router(main_router, prefix="")
app.include_router(lean_router, prefix="")


@app.get("/")
async def root():
    """Serve la console principale."""
    return HTMLResponse(content=(web_dir / "index.html").read_text(encoding="utf-8"))


@app.get("/health")
async def health():
    return Orchestrator().health()


def _project_file(project_id: str, filename: str) -> Path:
    folder = resolve_project(project_id)
    path = folder / filename
    if path.resolve().parent != folder.resolve():
        raise HTTPException(400, "Percorso file non consentito")
    return path


@app.get("/api/projects/{project_id}/script")
async def api_script(project_id: str):
    path = _project_file(project_id, "script.md")
    if not path.exists():
        raise HTTPException(404, "Script non trovato")
    return {"content": path.read_text(encoding="utf-8"), "filename": "script.md"}


@app.get("/api/projects/{project_id}/storyboard")
async def api_storyboard(project_id: str):
    path = _project_file(project_id, "storyboard.json")
    if not path.exists():
        raise HTTPException(404, "Storyboard non trovato")
    return {"content": path.read_text(encoding="utf-8"), "filename": "storyboard.json"}


@app.get("/api/projects/{project_id}/title-options")
async def api_titles(project_id: str):
    path = _project_file(project_id, "title_options.json")
    if not path.exists():
        raise HTTPException(404, "Titoli non trovati")
    return {"content": path.read_text(encoding="utf-8"), "filename": "title_options.json"}


@app.get("/api/projects/{project_id}/metadata")
async def api_metadata(project_id: str):
    path = _project_file(project_id, "metadata.json")
    if not path.exists():
        raise HTTPException(404, "Metadati non trovati")
    return {"content": path.read_text(encoding="utf-8"), "filename": "metadata.json"}


@app.get("/api/projects/{project_id}/download/script")
@app.get("/api/projects/{project_id}/script/download")
async def download_script(project_id: str):
    path = _project_file(project_id, "script.md")
    if not path.exists():
        raise HTTPException(404, "File non trovato")
    return FileResponse(path, filename="script.md", media_type="text/markdown")


@app.get("/api/projects/{project_id}/download/storyboard")
@app.get("/api/projects/{project_id}/storyboard/download")
async def download_storyboard(project_id: str):
    path = _project_file(project_id, "storyboard.json")
    if not path.exists():
        raise HTTPException(404, "File non trovato")
    return FileResponse(path, filename="storyboard.json", media_type="application/json")


@app.get("/api/projects/{project_id}/download/titles")
@app.get("/api/projects/{project_id}/titles/download")
async def download_titles(project_id: str):
    path = _project_file(project_id, "title_options.json")
    if not path.exists():
        raise HTTPException(404, "File non trovato")
    return FileResponse(path, filename="title_options.json", media_type="application/json")


@app.get("/api/projects/{project_id}/download/metadata")
@app.get("/api/projects/{project_id}/metadata/download")
async def download_metadata(project_id: str):
    path = _project_file(project_id, "metadata.json")
    if not path.exists():
        raise HTTPException(404, "File non trovato")
    return FileResponse(path, filename="metadata.json", media_type="application/json")
<<<<<<< HEAD
=======
=======
monta la nuova console come home
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
