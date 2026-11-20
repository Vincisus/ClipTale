"""Coda di video: idee in attesa che ClipTale produce una dopo l'altra, anche a finestra chiusa.

La coda è salvata in projects/_queue.json, quindi sopravvive alla chiusura dell'app. Un ciclo in sottofondo
(avviato con il server) prende la prima idea quando non c'è un lavoro in corso, la coda non è in pausa
e il motore AI risponde: così, se LM Studio è spento, le idee restano in attesa invece di fallire una dopo l'altra.
"""
import asyncio
import logging
import uuid
from datetime import datetime

from app.core.projects import ROOT, load_json, save_json

logger = logging.getLogger("uvicorn.error")
FILE = ROOT / "_queue.json"
STATE = {"last_error": None}


def load():
    data = load_json(FILE, {}) or {}
    data.setdefault("paused", False)
    data.setdefault("items", [])
    return data


def items():
    return load()["items"]


def add(payload, title=""):
    data = load()
    item = {"id": uuid.uuid4().hex[:10], "title": (title or payload.get("topic", "")).strip()[:120],
            "languages": payload.get("languages") or ["it"], "duration": payload.get("duration_minutes"),
            "added": datetime.now().isoformat(timespec="seconds"), "payload": payload}
    data["items"].append(item)
    save_json(FILE, data)
    return item


def remove(item_id):
    data = load()
    before = len(data["items"])
    data["items"] = [i for i in data["items"] if i["id"] != item_id]
    save_json(FILE, data)
    return len(data["items"]) < before


def move(item_id, to_index):
    data = load()
    found = next((i for i in data["items"] if i["id"] == item_id), None)
    if found:
        data["items"].remove(found)
        data["items"].insert(max(0, min(len(data["items"]), int(to_index))), found)
        save_json(FILE, data)
    return bool(found)


def set_paused(paused):
    data = load()
    data["paused"] = bool(paused)
    save_json(FILE, data)
    return data


def status():
    data = load()
    return {"paused": data["paused"], "items": [{k: v for k, v in i.items() if k != "payload"} for i in data["items"]],
            "last_error": STATE["last_error"]}


async def _ai_ready():
    from app.services.llm_service import LLMService
    try:
        await LLMService().list_models()
        return True
    except Exception:
        return False


async def run_forever(every=4):
    """Ciclo della coda: parte con il server e non si ferma mai (gli errori di un'idea non bloccano le altre)."""
    from app.core import jobs
    while True:
        await asyncio.sleep(every)
        try:
            data = load()
            if data["paused"] or not data["items"] or jobs.active():
                continue
            if not await _ai_ready():
                STATE["last_error"] = "Il motore AI non risponde: la coda aspetta."
                continue
            item = data["items"].pop(0)
            save_json(FILE, data)
            from app.api.routes import AutoRequest, start_auto
            try:
                start_auto(AutoRequest(**item["payload"]))
                STATE["last_error"] = None
                logger.info("Coda: avviato «%s»", item["title"])
            except Exception as exc:  # idea non valida: la si scarta e si passa alla prossima
                STATE["last_error"] = f"«{item['title'][:40]}» non avviata: {exc}"
                logger.warning("Coda: %s", STATE["last_error"])
        except Exception:
            logger.exception("Coda: errore inatteso")
