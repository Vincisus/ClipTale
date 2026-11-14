"""FastAPI application con serving file statici per console web."""
from fastapi import FastAPI, File, Response
from fastapi.staticfiles import StaticFiles
from pathlib import Path

app = FastAPI(title="TiktokFactory", version="1.0.0")

# Mount static files
web_dir = Path(__file__).parent / "web"
app.mount("/static", StaticFiles(directory=web_dir, html=True))

# Include routers
from app.api.routes import router as main_router
app.include_router(main_router, prefix="")

from app.core.lean_pipeline import router as lean_router
app.include_router(lean_router, prefix="")


@app.get("/")
async def root():
    """Serve la console principale."""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=open(web_dir / "index.html", encoding="utf-8").read())


@app.get("/health")
async def health():
    from app.core.orchestrator import Orchestrator
    return Orchestrator().health()


# API Projects list
from app.core.project_console import ROOT, load as load_json
import json

@app.get("/api/projects")
async def api_projects():
    """Lista progetti con stato."""
    from app.core.orchestrator import busy
    projects = []
    for p in ROOT.glob("*"):
        if p.is_dir():
            brief = p / "brief.json"
            concept = p / "concept.json"
            structure = p / "structure.json"
            ready = structure.exists()
            project_info = {
                "id": p.name,
                "title": p.name,
                "ready": ready,
                "path": str(p)
            }
            if brief.exists():
                try:
                    project_info["brief"] = json.loads(brief.read_text(encoding="utf-8"))
                except Exception:
                    pass
            projects.append(project_info)
    return {
        "busy": busy(),
        "projects": sorted(projects, key=lambda x: x["id"], reverse=True)
    }


@app.get("/api/projects/{project_id}/script")
async def api_script(project_id: str):
    """Restituisce il script del progetto."""
    path = ROOT / project_id / "script.md"
    if not path.exists():
        return {"error": "Script non trovato", "project": project_id}
    return {
        "content": path.read_text(encoding="utf-8"),
        "filename": "script.md"
    }


@app.get("/api/projects/{project_id}/storyboard")
async def api_storyboard(project_id: str):
    """Restituisce il storyboard del progetto."""
    path = ROOT / project_id / "storyboard.json"
    if not path.exists():
        return {"error": "Storyboard non trovato", "project": project_id}
    return {
        "content": path.read_text(encoding="utf-8"),
        "filename": "storyboard.json"
    }


@app.get("/api/projects/{project_id}/title-options")
async def api_titles(project_id: str):
    """Restituisce le opzioni titolo."""
    path = ROOT / project_id / "title_options.json"
    if not path.exists():
        return {"error": "Titoli non trovati", "project": project_id}
    return {
        "content": path.read_text(encoding="utf-8"),
        "filename": "title_options.json"
    }


@app.get("/api/projects/{project_id}/metadata")
async def api_metadata(project_id: str):
    """Restituisce i metadati."""
    path = ROOT / project_id / "metadata.json"
    if not path.exists():
        return {"error": "Metadati non trovati", "project": project_id}
    return {
        "content": path.read_text(encoding="utf-8"),
        "filename": "metadata.json"
    }


@app.get("/api/projects/{project_id}/download/script")
async def download_script(project_id: str):
    """Download script."""
    path = ROOT / project_id / "script.md"
    if not path.exists():
        return {"error": "File non trovato"}
    return FileResponse(path, filename="script.md", media_type="text/markdown")


@app.get("/api/projects/{project_id}/download/storyboard")
async def download_storyboard(project_id: str):
    """Download storyboard."""
    path = ROOT / project_id / "storyboard.json"
    if not path.exists():
        return {"error": "File non trovato"}
    return FileResponse(path, filename="storyboard.json", media_type="application/json")


@app.get("/api/projects/{project_id}/download/titles")
async def download_titles(project_id: str):
    """Download title options."""
    path = ROOT / project_id / "title_options.json"
    if not path.exists():
        return {"error": "File non trovato"}
    return FileResponse(path, filename="title_options.json", media_type="application/json")


@app.get("/api/projects/{project_id}/download/metadata")
async def download_metadata(project_id: str):
    """Download metadata."""
    path = ROOT / project_id / "metadata.json"
    if not path.exists():
        return {"error": "File non trovato"}
    return FileResponse(path, filename="metadata.json", media_type="application/json")
