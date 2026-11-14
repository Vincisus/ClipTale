"""FastAPI application con serving file statici per console web."""
from fastapi import FastAPI
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
    return {"message": "TiktokFactory API", "docs": "/docs"}

@app.get("/health")
async def health():
    from app.core.orchestrator import Orchestrator
    return Orchestrator().health()
