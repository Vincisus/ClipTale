"""TiktokFactory Studio: server locale FastAPI + interfaccia web in app/web/index.html."""
import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router

load_dotenv()
logging.getLogger("httpx").setLevel(logging.WARNING)

WEB = Path(__file__).parent / "web"
app = FastAPI(title="TiktokFactory Studio", version="2.0.0")
app.include_router(router)
app.mount("/static", StaticFiles(directory=WEB, html=True), name="static")


@app.get("/", include_in_schema=False)
async def home():
    return FileResponse(WEB / "index.html")


@app.get("/index.html", include_in_schema=False)
async def index_alias():
    return RedirectResponse("/")
