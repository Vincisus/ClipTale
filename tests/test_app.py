"""Test con LM Studio simulato: python -m pytest tests -q"""
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LMSTUDIO_MODEL", "test-model")

from app.core import pipeline  # noqa: E402
from app.core.pipeline import StoryPipeline  # noqa: E402
from app.services.llm_service import LLMService  # noqa: E402
from app.services.video_service import caption_chunks  # noqa: E402


def sse(text, reasoning=""):
    events = []
    if reasoning:
        events.append({"choices": [{"delta": {"reasoning_content": reasoning}}]})
    for i in range(0, len(text), 40):
        events.append({"choices": [{"delta": {"content": text[i:i + 40]}}]})
    events.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"


class FakeLMStudio:
    """Risponde in base al nome dello schema richiesto / al contenuto del prompt."""

    def __init__(self):
        self.payloads = []

    def __call__(self, request):
        payload = json.loads(request.content)
        self.payloads.append(payload)
        prompt = payload["messages"][-1]["content"]
        if prompt.startswith("Crea il concept") or "concept di" in prompt:
            body = {"title": "Il secondo telefono", "hook": "Ho trovato un telefono.", "logline": "x", "setting": "casa", "tone": "teso", "ending": "verita", "characters": [{"id": "Io", "name": "Marta", "role": "protagonista"}, {"id": "io", "name": "Luca", "role": "marito"}]}
        elif prompt.startswith("Dividi"):
            body = {"parts": [{"part": 1, "title": "Scoperta", "summary": "s", "cliffhanger": "c"}]}  # una parte in meno: va completata
        elif prompt.startswith("Scrivi il testo parlato"):
            n = int(prompt.split("divise in ")[1].split(" beat")[0])
            words = int(prompt.split("circa ")[1].split(" parole")[0])
            body = {"beats": [{"text": " ".join(["parola."] * (words // n)), "visual": "cucina"} for _ in range(n)]}
        elif prompt.startswith("Prepara i metadati"):
            body = {"titles": ["a", "b", "c", "d", "e"], "description": "d", "tiktok_caption": "c", "hashtags": ["storia vera", "#pov"]}
        else:
            body = {}
        return httpx.Response(200, text=sse("```json\n" + json.dumps(body, ensure_ascii=False) + "\n```", reasoning="penso..."))


class FakeJob:
    def __init__(self):
        self.logs = []
        self.data = {}

    def log(self, msg, **kw):
        self.logs.append(msg)


def test_llm_disables_thinking_and_parses_fenced_json():
    fake = FakeLMStudio()
    llm = LLMService(model="m", transport=httpx.MockTransport(fake))
    llm.diagnostic_dir = Path(os.environ.get("TMP", ".")) / "tf_diag_test"
    result = asyncio.run(llm.generate_json("Prepara i metadati", {"type": "object"}))
    assert result["titles"][0] == "a"
    sent = fake.payloads[0]
    assert sent["reasoning_effort"] == "none"
    assert sent["stream"] is True and sent["response_format"]["type"] == "json_schema"


def test_repair_truncated_json():
    assert LLMService.extract_json('{"beats": [{"text": "ciao", "visual": "x"}, {"text": "tronc') == {"beats": [{"text": "ciao", "visual": "x"}, {"text": "tronc"}]}
    assert LLMService.extract_json("<think>bla</think>{\"a\": 1,}") == {"a": 1}


def test_full_story_pipeline(tmp_path):
    llm = LLMService(model="m", transport=httpx.MockTransport(FakeLMStudio()))
    llm.diagnostic_dir = tmp_path / "diag"
    brief = {"id": "p", "topic": "tradimento", "format": "story", "duration_minutes": 2, "parts": 2, "wpm": 150}
    job = FakeJob()
    beats = asyncio.run(StoryPipeline(llm, tmp_path, job).run(brief))
    concept = json.loads((tmp_path / "concept.json").read_text(encoding="utf-8"))
    assert [c["id"] for c in concept["characters"]] == ["io", "io_"]  # ID duplicati resi univoci
    structure = json.loads((tmp_path / "structure.json").read_text(encoding="utf-8"))
    assert len(structure["parts"]) == 2  # parte mancante aggiunta, non errore
    assert {b["part"] for b in beats} == {1, 2}
    assert beats[0]["role"] == "HOOK" and beats[-1]["role"] == "FINALE"
    assert (tmp_path / "script.md").is_file() and (tmp_path / "metadata.json").is_file()
    meta = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert meta["hashtags"] == ["#storiavera", "#pov"]
    # ripresa: nessuna nuova chiamata se tutto e gia salvato
    fake = FakeLMStudio()
    llm2 = LLMService(model="m", transport=httpx.MockTransport(fake))
    llm2.diagnostic_dir = tmp_path / "diag"
    asyncio.run(StoryPipeline(llm2, tmp_path, FakeJob()).run(brief))
    assert fake.payloads == []


def test_plan_numbers():
    numbers = pipeline.plan({"duration_minutes": 12, "parts": 4, "wpm": 150})
    assert numbers["total_words"] == 1800 and numbers["words_per_part"] == 450 and numbers["beats_per_part"] == 4


def test_caption_chunks_cover_duration():
    chunks = caption_chunks("Uno due tre quattro cinque sei sette otto nove dieci. Undici dodici.", 6.0, 2.0)
    assert abs(sum(d for _, d in chunks) - 6.0) < 1e-6
    assert " ".join(c for c, _ in chunks).split() == "Uno due tre quattro cinque sei sette otto nove dieci. Undici dodici.".split()


@pytest.mark.parametrize("bad", ["../x", "a/b", "_diagnostics", ""])
def test_project_id_validation(bad):
    from fastapi import HTTPException
    from app.core.projects import folder
    with pytest.raises(HTTPException):
        folder(bad)


def test_render_caption_bottom(tmp_path):
    from PIL import Image
    from app.services.video_service import H, render_caption

    out = tmp_path / "c.png"
    render_caption("Ogni notte trovo un biglietto sul frigo", "", out)
    img = Image.open(out)
    assert img.mode == "RGBA" and img.size == (1080, 1920)
    alpha = img.getchannel("A")
    # testo bianco solo nella parte bassa: nessun pixel bianco opaco al centro dello schermo
    centre = img.crop((0, int(H * 0.40), 1080, int(H * 0.55)))
    assert not any(p[0] > 240 and p[3] > 240 for p in centre.getdata())
    bottom = img.crop((0, int(H * 0.60), 1080, int(H * 0.81)))
    assert any(p[0] > 240 and p[1] > 240 and p[3] > 240 for p in bottom.getdata())
    assert alpha.getpixel((540, 900)) == 0


# ------------------------------------------------------------------ illustrazioni locali
class FakeSceneLLM:
    def __init__(self, scenes):
        self.scenes = scenes
        self.prompts = []

    async def generate_json(self, prompt, schema=None, **kw):
        self.prompts.append(prompt)
        return {"characters": [{"name": "Marta", "look": "a 35-year-old woman with a short red bob and a green coat"}], "scenes": self.scenes}


def test_scene_prompts_keep_character_look():
    from app.services.illustration_service import scene_prompts

    llm = FakeSceneLLM(["Marta finds a phone in the garage", "close view of a phone on a workbench"])
    beats = [{"text": "Ho trovato un telefono.", "cue": "garage"}, {"text": "Era acceso.", "cue": "telefono"}]
    looks, scenes = asyncio.run(scene_prompts(llm, beats, {"title": "t", "characters": [{"name": "Marta", "role": "moglie"}]}))
    assert "Marta" not in scenes[0] and "short red bob" in scenes[0]
    assert looks[0]["name"] == "Marta"
    assert "esattamente 2 descrizioni" in llm.prompts[0]
    with pytest.raises(ValueError):
        asyncio.run(scene_prompts(FakeSceneLLM(["solo una"]), beats, {}))


def test_build_items_shots_and_cache_paths(tmp_path):
    from app.services.illustration_service import NEGATIVE, STYLES, build_items, shots_for

    assert [shots_for(d) for d in (3, 10, 15, 60)] == [1, 1, 2, 3]
    items, per_beat = build_items([{}, {}], [4.0, 15.0], ["a kitchen", "a garage"], "sketch", tmp_path, 100)
    assert [len(s) for s in per_beat] == [1, 2]
    assert len(items) == 3 and all(STYLES["sketch"]["prompt"] in i["prompt"] for i in items)
    assert "close-up" in items[2]["prompt"] and items[2]["prompt"].startswith(STYLES["sketch"]["prompt"]) and items[0]["post"] == "pencil" and items[0]["negative"] == NEGATIVE
    assert len({i["out"] for i in items}) == 3 and len({i["seed"] for i in items}) == 3
    again, _ = build_items([{}, {}], [4.0, 15.0], ["a kitchen", "a garage"], "sketch", tmp_path, 100)
    assert [i["out"] for i in again] == [i["out"] for i in items]  # stessa scena = stesso file in cache
