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
    def __init__(self, shots_per_call=None):
        self.prompts = []
        self.shots_per_call = shots_per_call

    async def generate_json(self, prompt, schema=None, **kw):
        self.prompts.append(prompt)
        if "characters" in schema["properties"]:
            return {"characters": [{"name": "Marta", "look": "a 35-year-old woman with a short red bob and a green coat"}]}
        n = self.shots_per_call or schema["properties"]["shots"]["minItems"]
        beats = schema["properties"]["stickers"]["minItems"]
        return {"shots": [f"Marta in scene {i}" for i in range(n)], "stickers": ["pagami a gennaio"] * beats}


def test_beat_moments_follow_sentences():
    from app.services.illustration_service import beat_moments

    text = "Ho trovato un telefono nel garage. Era acceso. Sullo schermo c'era un messaggio: «Ti aspetto stasera». Non era per me."
    m = beat_moments(text, 14.0)
    assert len(m) == 4 and abs(sum(s for _, s in m) - 14.0) < 1e-6
    assert " ".join(t for t, _ in m) == text  # nessuna parola persa o duplicata
    assert beat_moments("Breve.", 1.0) == [("Breve.", 1.0)]


def test_scene_prompts_per_moment_with_stickers():
    from app.services.illustration_service import scene_prompts

    beats = [{"part": 1, "text": "a"}, {"part": 1, "text": "b"}, {"part": 2, "text": "c"}]
    moments = [[("uno", 3), ("due", 3)], [("tre", 3)], [("quattro", 3), ("cinque", 3), ("sei", 3)]]
    llm = FakeSceneLLM()
    looks, shots, stickers = asyncio.run(scene_prompts(llm, beats, {"title": "t"}, moments))
    assert [len(s) for s in shots] == [2, 1, 3]
    assert "Marta" not in shots[0][0] and "short red bob" in shots[0][0]
    assert stickers == ["PAGAMI A GENNAIO"] * 3
    assert len(llm.prompts) == 3  # personaggi + una chiamata per parte
    with pytest.raises(ValueError):
        asyncio.run(scene_prompts(FakeSceneLLM(shots_per_call=1), beats, {}, moments))


def test_build_items_shots_and_cache_paths(tmp_path):
    from app.services.illustration_service import NEGATIVE, STYLES, build_items

    moments = [[("a", 4.0)], [("b", 7.0), ("c", 8.0)]]
    shots = [["a kitchen"], ["a garage", "close-up on a phone"]]
    items, per_beat = build_items([{}, {}], moments, shots, "sketch", tmp_path, 100)
    assert [len(s) for s in per_beat] == [1, 2] and per_beat[1][1][2] == 8.0
    assert len(items) == 3 and all(i["prompt"].startswith(STYLES["sketch"]["prompt"]) for i in items)
    assert items[0]["post"] == "pencil" and items[0]["negative"] == NEGATIVE
    assert "close-up on a phone" in items[2]["prompt"] and "close-up on the face" not in items[2]["prompt"]
    assert len({i["out"] for i in items}) == 3 and len({i["seed"] for i in items}) == 3
    again, _ = build_items([{}, {}], moments, shots, "sketch", tmp_path, 100)
    assert [i["out"] for i in again] == [i["out"] for i in items]  # stessa scena = stesso file in cache


def test_karaoke_timeline_and_end_card(tmp_path):
    from PIL import Image
    from app.services.video_service import H, overlay_timeline, render_overlay, word_timeline

    texts = ["Paghi tu, cara. La casa è nostra.", "Ma loro non sapevano niente."]
    durs = [4.0, 3.0]
    chunks, words, starts = word_timeline(texts, durs, 2.2)
    assert starts == [0.0, 4.0] and all(len(c[0]) <= 5 for c in chunks)
    assert len(words) == sum(len(t.split()) for t in texts)
    tl = overlay_timeline(chunks, words, starts, durs, ["PAGAMI A GENNAIO", ""], 7.0, "PARTE 1", "PARTE 2 NEL PROFILO")
    assert abs(sum(d for _, d, _ in tl) - 7.0) < 1e-6
    scales = [st[4] for _, _, st in tl if st[2]]
    assert scales[:3] == [0.7, 1.14, 1.0]  # l'etichetta entra con un piccolo rimbalzo
    assert tl[-1][2][6] == "PARTE 2 NEL PROFILO" and tl[0][2][5] == "PARTE 1"
    highlighted = [st[1] for _, _, st in tl if st[0]]
    assert highlighted[0] == 0 and max(highlighted) >= 2  # la parola evidenziata avanza
    clean = overlay_timeline(chunks, words, starts, durs, ["", ""], 7.0, "", None)
    assert all(st[5] == "" and st[6] is None for _, _, st in clean)
    out = tmp_path / "o.png"
    render_overlay((("Paghi", "tu,", "cara."), 1, "PAGAMI A GENNAIO", (46, 158, 68), 1.0, "", None), out)
    img = Image.open(out)
    yellow = [p for p in img.crop((0, int(H * 0.6), 1080, int(H * 0.79))).getdata() if p[0] > 240 and 190 < p[1] < 230 and p[2] < 80 and p[3] > 240]
    assert yellow  # parola pronunciata in giallo
    green = [p for p in img.crop((0, int(H * 0.12), 1080, int(H * 0.28))).getdata() if p[1] > 140 and p[0] < 80 and p[3] > 240]
    assert green  # etichetta in alto


# ------------------------------------------------------------------ pubblicazione (server finti)
@pytest.fixture
def secrets_dir(tmp_path, monkeypatch):
    from app.services import publish_service
    monkeypatch.setattr(publish_service, "SECRETS", tmp_path / "secrets")
    return publish_service


def test_auth_urls_use_pkce_and_state(secrets_dir):
    ps = secrets_dir
    assert ps.status()["youtube"]["configured"] is False
    ps.save_app("youtube", "cid.apps.googleusercontent.com", "sec-1")
    ps.save_app("tiktok", "ckey", "csec")
    yt = ps.auth_url("youtube")
    assert "code_challenge_method=S256" in yt and "access_type=offline" in yt and "youtube.upload" in yt
    tt = ps.auth_url("tiktok")
    assert "client_key=ckey" in tt and "video.upload" in tt
    challenge = tt.split("code_challenge=")[1].split("&")[0]
    assert len(challenge) == 64 and all(c in "0123456789abcdef" for c in challenge)  # TikTok desktop: SHA256 esadecimale
    with pytest.raises(ValueError):
        asyncio.run(ps.finish_auth("youtube", "code", "state-sbagliato"))


def test_youtube_texts_have_chapters(tmp_path):
    from app.core.projects import save_json
    from app.services.publish_service import youtube_texts

    save_json(tmp_path / "metadata.json", {"youtube_title": "Quando chiesi a mio marito cosa volesse per il compleanno, mi rispose...", "description": "Una cena.", "hashtags": ["#reddititalia", "#storie"]})
    save_json(tmp_path / "structure.json", {"parts": [{"part": i, "title": f"T{i}"} for i in (1, 2, 3)]})
    save_json(tmp_path / "render" / "render.json", {"beats": [{"part": 1, "start": 0}, {"part": 1, "start": 20}, {"part": 2, "start": 65.4}, {"part": 3, "start": 3700}]})
    t = youtube_texts(tmp_path)
    assert t["title"].startswith("Quando chiesi") and t["tags"] == ["reddititalia", "storie"]
    assert "00:00 Parte 1 — T1" in t["description"] and "01:05 Parte 2 — T2" in t["description"] and "1:01:40 Parte 3" in t["description"]
    assert "intelligenza artificiale" in t["description"]


def test_upload_youtube_resumable(secrets_dir, tmp_path):
    ps = secrets_dir
    ps.save_app("youtube", "cid", "sec")
    ps._write("youtube_token.json", {"refresh_token": "r1"})
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 1000)
    calls = []

    def handler(req):
        calls.append((req.method, str(req.url)))
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "a1"})
        if req.method == "POST":
            body = json.loads(req.content)
            assert body["status"]["privacyStatus"] == "private" and body["status"]["containsSyntheticMedia"] is True
            return httpx.Response(200, headers={"Location": "https://upload.test/session"})
        assert req.headers["content-range"] == "bytes 0-999/1000"
        return httpx.Response(200, json={"id": "VID123"})

    res = asyncio.run(ps.upload_youtube(video, "Titolo", "Desc", ["a"], log=lambda m: None, transport=httpx.MockTransport(handler)))
    assert res["url"] == "https://youtu.be/VID123" and len(calls) == 3


def test_upload_tiktok_draft_chunks(secrets_dir, tmp_path, monkeypatch):
    ps = secrets_dir
    real_sleep = asyncio.sleep
    monkeypatch.setattr(ps.asyncio, "sleep", lambda *_: real_sleep(0))
    assert ps.tiktok_chunks(3 * ps.MB) == (3 * ps.MB, 1)
    assert ps.tiktok_chunks(25 * ps.MB) == (10 * ps.MB, 2)  # l'ultimo blocco assorbe il resto
    ps.save_app("tiktok", "ck", "cs")
    ps._write("tiktok_token.json", {"refresh_token": "r", "access_token": "a", "expires_at": 9e12})
    video = tmp_path / "c.mp4"
    video.write_bytes(b"y" * (25 * ps.MB))
    ranges = []

    def handler(req):
        path = req.url.path
        if path.endswith("/inbox/video/init/"):
            src = json.loads(req.content)["source_info"]
            assert src["total_chunk_count"] == 2 and src["chunk_size"] == 10 * ps.MB
            return httpx.Response(200, json={"data": {"upload_url": "https://up.test/u", "publish_id": "p1"}, "error": {"code": "ok"}})
        if req.method == "PUT":
            ranges.append(req.headers["content-range"])
            return httpx.Response(201)
        if path.endswith("/status/fetch/"):
            return httpx.Response(200, json={"data": {"status": "SEND_TO_USER_INBOX"}})
        return httpx.Response(404)

    res = asyncio.run(ps.upload_tiktok(video, "Parte 1", "draft", log=lambda m: None, transport=httpx.MockTransport(handler)))
    size = 25 * ps.MB
    assert ranges == [f"bytes 0-{10 * ps.MB - 1}/{size}", f"bytes {10 * ps.MB}-{size - 1}/{size}"]
    assert res["status"] == "SEND_TO_USER_INBOX"


def test_reddit_format_is_default_and_has_cut_rules():
    from app.core.pipeline import CUT_RULES, FORMATS, metadata_schema

    assert "carta nascosta" in FORMATS["reddit"]["concept"] and "massima tensione" in CUT_RULES
    assert metadata_schema(4)["properties"]["part_captions"]["minItems"] == 4


def test_short_title_cuts_on_word():
    from app.services.publish_service import short_title

    t = short_title("Mia suocera mi ha chiesto di pagare il conto del pranzo di Natale perché, disse, 'la tua paga da impiegata è l'unica che entra'")
    assert len(t) <= 100 and t.endswith("...") and not t.endswith("imp...")
    assert short_title("Breve") == "Breve"


def test_draw_restarts_after_crash(tmp_path, monkeypatch):
    from app.services import illustration_service as ils

    items = [{"out": str(tmp_path / f"{i}.png")} for i in range(3)]
    calls, lines = [], []

    async def fake_once(job, on_line):
        calls.append(1)
        Path(items[len(calls) - 1]["out"]).write_bytes(b"x")  # ogni tentativo disegna un'immagine poi "si blocca"
        return "CUDA error: misaligned address" if len(calls) < 3 else None

    monkeypatch.setattr(ils, "_draw_once", fake_once)
    asyncio.run(ils.draw(items, tmp_path, lines.append))
    assert len(calls) == 3 and all(Path(i["out"]).is_file() for i in items)
    assert any("riparto dalle 2 immagini mancanti" in ln for ln in lines)
