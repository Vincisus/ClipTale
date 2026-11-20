"""ClipTale: server locale FastAPI + interfaccia web in app/web/index.html (aperta nella finestra dell'app o nel browser)."""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router

from app.core.paths import ENV_FILE  # noqa: E402

load_dotenv(ENV_FILE)
logging.getLogger("httpx").setLevel(logging.WARNING)

WEB = Path(__file__).parent / "web"
@asynccontextmanager
async def lifespan(_app):
    from app.core import video_queue
    task = asyncio.create_task(video_queue.run_forever())  # la coda di video lavora finché il server è acceso
    yield
    task.cancel()


app = FastAPI(title="ClipTale", version="3.0.0", lifespan=lifespan)
app.include_router(router)
app.mount("/static", StaticFiles(directory=WEB, html=True), name="static")


@app.get("/", include_in_schema=False)
async def home():
    return FileResponse(WEB / "index.html")


@app.get("/index.html", include_in_schema=False)
async def index_alias():
    return RedirectResponse("/")
