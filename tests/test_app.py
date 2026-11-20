"""Test con LM Studio simulato: python -m pytest tests -q"""
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import numpy as np
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
    from app.services.illustration_service import NEGATIVE, STYLES, build_items, normalize_style

    assert sorted(STYLES) == ["anim", "cartoon"] and normalize_style("sketch") == "cartoon" and normalize_style("anim") == "anim"
    moments = [[("a", 4.0)], [("b", 7.0), ("c", 8.0)]]
    shots = [["a kitchen"], ["a garage", "close-up on a phone"]]
    items, per_beat = build_items([{}, {}], moments, shots, "cartoon", tmp_path, 100)
    assert [len(s) for s in per_beat] == [1, 2] and per_beat[1][1][2] == 8.0
    assert len(items) == 3 and all(i["prompt"].startswith(STYLES["cartoon"]["prompt"]) for i in items)
    assert items[0]["post"] is None and items[0]["negative"] == NEGATIVE
    assert "close-up on a phone" in items[2]["prompt"] and "close-up on the face" not in items[2]["prompt"]
    assert len({i["out"] for i in items}) == 3 and len({i["seed"] for i in items}) == 3
    again, _ = build_items([{}, {}], moments, shots, "cartoon", tmp_path, 100)
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
    MB = ps.MB
    assert ps.tiktok_chunks(3 * MB) == (3 * MB, 1)
    assert ps.tiktok_chunks(int(9.3 * MB)) == (int(9.3 * MB), 1)  # le clip normali: un blocco grande quanto il file
    assert ps.tiktok_chunks(64 * MB) == (64 * MB, 1)
    assert ps.tiktok_chunks(int(91.7 * MB)) == (32 * MB, 2)  # oltre 64 MB: blocchi da 32 MB, l'ultimo assorbe il resto
    for size in (int(65 * MB), int(91.7 * MB), 500 * MB):
        chunk, count = ps.tiktok_chunks(size)
        last = size - chunk * (count - 1)
        assert 5 * MB <= chunk <= 64 * MB and chunk <= last <= 128 * MB
    monkeypatch.setattr(ps, "MB", 1000)  # file piccoli nel test, stesse regole
    ps.save_app("tiktok", "ck", "cs")
    ps._write("tiktok_token.json", {"refresh_token": "r", "access_token": "a", "expires_at": 9e12})
    video = tmp_path / "c.mp4"
    video.write_bytes(b"y" * (100 * ps.MB))
    ranges = []

    def handler(req):
        path = req.url.path
        if path.endswith("/inbox/video/init/"):
            src = json.loads(req.content)["source_info"]
            assert src["total_chunk_count"] == 3 and src["chunk_size"] == 32 * ps.MB
            return httpx.Response(200, json={"data": {"upload_url": "https://up.test/u", "publish_id": "p1"}, "error": {"code": "ok"}})
        if req.method == "PUT":
            ranges.append(req.headers["content-range"])
            return httpx.Response(201)
        if path.endswith("/status/fetch/"):
            return httpx.Response(200, json={"data": {"status": "SEND_TO_USER_INBOX"}})
        return httpx.Response(404)

    res = asyncio.run(ps.upload_tiktok(video, "Parte 1", "draft", log=lambda m: None, transport=httpx.MockTransport(handler)))
    size, c = 100 * ps.MB, 32 * ps.MB
    assert ranges == [f"bytes 0-{c - 1}/{size}", f"bytes {c}-{2 * c - 1}/{size}", f"bytes {2 * c}-{size - 1}/{size}"]
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


# ------------------------------------------------------------------ ElevenLabs (server finto)
def test_elevenlabs_voices_quota_and_fallback(tmp_path, monkeypatch):
    from app.services import eleven_service as el
    from app.services import tts_service as tts

    monkeypatch.setattr(el, "SECRETS", tmp_path)
    el.save_key("abc123")
    calls = []

    def handler(req):
        calls.append(req.url.path)
        assert req.headers["xi-api-key"] == "abc123"
        if req.url.path.endswith("/voices"):
            return httpx.Response(200, json={"voices": [
                {"voice_id": "v1", "name": "Bella", "labels": {"gender": "female"}},
                {"voice_id": "v2", "name": "Marco", "labels": {"gender": "male", "accent": "italian"}}]})
        if req.url.path.endswith("/user/subscription"):
            return httpx.Response(200, json={"character_count": 900, "character_limit": 10000, "tier": "free"})
        body = json.loads(req.content)
        assert body["model_id"] == el.MODEL and body["next_text"] == "Seconda."
        return httpx.Response(200, content=b"ID3" + b"x" * 2000)

    t = httpx.MockTransport(handler)
    vs = asyncio.run(el.voices(transport=t))
    assert el.pick_for_gender(vs, "M") == "v2" and el.pick_for_gender(vs, "F") == "v1"
    assert asyncio.run(el.status(transport=t))["limit"] == 10000
    out = asyncio.run(el.synthesize("v1", [("Prima.", tmp_path / "a"), ("Seconda.", tmp_path / "b")], transport=httpx.MockTransport(
        lambda r: handler(r) if json.loads(r.content).get("text") == "Prima." else httpx.Response(200, content=b"ID3" + b"y" * 2000))))
    assert [p.suffix for p in out] == [".mp3", ".mp3"]

    def broke(req):
        return httpx.Response(401, text='{"detail":{"status":"quota_exceeded"}}')
    with pytest.raises(el.QuotaError):
        asyncio.run(el.synthesize("v1", [("Ciao.", tmp_path / "c")], transport=httpx.MockTransport(broke)))
    el.save_key("")
    assert not el.configured()


# ------------------------------------------------------------------ cartone animato, musica, voce
class FakeDirectorLLM:
    async def generate_json(self, prompt, schema=None, **kw):
        if "characters" in schema["properties"]:
            return {"characters": [
                {"name": "Luca", "gender": "M", "age": "adult", "hair_style": "bald", "hair_color": "black", "outfit_color": "mustard", "tie": True, "glasses": False, "beard": False, "necklace": False},
                {"name": "Sara", "gender": "F", "age": "old", "hair_style": "curly", "hair_color": "red", "outfit_color": "red", "tie": False, "glasses": True, "beard": False, "necklace": True}],
                "places": ["cozy kitchen", "lawyer office"]}
        n = schema["properties"]["shots"]["minItems"]
        beats = schema["properties"]["stickers"]["minItems"]
        return {"shots": [{"place": i % 2, "chars": [{"name": "Luca", "expression": "shocked", "pose": "phone"}, {"name": "Sara", "expression": "angry", "pose": "point"}][: 1 + i % 2],
                           "speaker": "Luca", "framing": "close"} for i in range(n)], "stickers": ["3.000 € subito?"] * beats}


def test_toon_plan_and_render(tmp_path):
    import wave
    from app.services import illustration_service as ils, toon
    from app.services.video_service import ffmpeg_exe

    beats = [{"part": 1, "text": "a"}, {"part": 1, "text": "b"}]
    moments = [[("uno", 1.0), ("due", 1.0)], [("tre", 1.0)]]
    cast, places, shots, stickers = asyncio.run(ils.toon_plan(FakeDirectorLLM(), beats, {"title": "t", "narrator_gender": "M"}, moments))
    assert [c["name"] for c in cast] == ["Luca", "Sara"] and cast[1]["hair_color"] == "gray" and places == ["cozy kitchen", "lawyer office"]
    assert [len(s) for s in shots] == [2, 1] and shots[0][1]["framing"] == "two" and shots[0][0]["framing"] == "close"
    assert stickers == ["3.000 € SUBITO?"] * 2
    wav = tmp_path / "v.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes((np.sin(np.arange(24000 * 2) / 8) * 8000).astype("<i2").tobytes())
    flat = [{**s, "bg": None, "secs": 1.0} for row in shots for s in row]
    secs = toon.render_picture(flat[:2], wav, tmp_path / "p.mp4", cast, ffmpeg_exe())
    assert abs(secs - 2.0) < 0.05 and (tmp_path / "p.mp4").stat().st_size > 1000


def test_music_sits_under_voice(tmp_path):
    import math
    import wave
    from app.services import audio_fx as a

    v = (np.sin(np.arange(a.RATE * 4) / 9) * 9000 * (np.arange(a.RATE * 4) % a.RATE < a.RATE * 0.7)).astype("<i2")
    with wave.open(str(tmp_path / "v.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(a.RATE); w.writeframes(v.tobytes())
    out = a.mix_part(tmp_path / "v.wav", tmp_path / "m.wav", [0, 2], [0.2], 3.0, 0.25, True)
    voice, mixed = a.read_wav(tmp_path / "v.wav"), a.read_wav(out)
    ratio = 20 * math.log10(a.gated_rms(mixed - voice) / a.gated_rms(voice))
    assert -16 < ratio < -8  # musica ed effetti ben sotto la voce
    assert np.max(np.abs(mixed)) <= 0.99


def test_italian_normalization_and_gender():
    from app.services.text_norm import narrator_gender, normalize

    assert normalize("Ho 2.500 € e il 51% delle quote") == "Ho duemilacinquecento euro e il cinquantuno per cento delle quote"
    assert "ventotto dicembre" in normalize("entro il 28/12") and "diciotto e trenta" in normalize("alle 18:30.")
    assert narrator_gender("Io ero rimasta zitta. Mio marito urlava.") == "F"
    assert narrator_gender("Mia moglie rideva. Io ero rimasto seduto.") == "M"
    assert narrator_gender("Ciao", "M") == "M"


def test_tiktok_direct_falls_back_to_private_then_draft(secrets_dir, tmp_path, monkeypatch):
    ps = secrets_dir
    real_sleep = asyncio.sleep
    monkeypatch.setattr(ps.asyncio, "sleep", lambda *_: real_sleep(0))
    ps.save_app("tiktok", "ck", "cs")
    ps._write("tiktok_token.json", {"refresh_token": "r", "access_token": "a", "expires_at": 9e12})
    video = tmp_path / "c.mp4"
    video.write_bytes(b"z" * 20000)
    tried, logs = [], []
    unaudited = {"error": {"code": "unaudited_client_can_only_post_to_private_accounts", "message": "Please review our integration guidelines"}}

    def handler(req):
        path = req.url.path
        if path.endswith("/creator_info/query/"):
            return httpx.Response(200, json={"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]}, "error": {"code": "ok"}})
        if path.endswith("/post/publish/video/init/"):
            tried.append(json.loads(req.content)["post_info"]["privacy_level"])
            return httpx.Response(403, json=unaudited)
        if path.endswith("/inbox/video/init/"):
            tried.append("inbox")
            return httpx.Response(200, json={"data": {"upload_url": "https://up.test/u", "publish_id": "p9"}, "error": {"code": "ok"}})
        if req.method == "PUT":
            return httpx.Response(201)
        if path.endswith("/status/fetch/"):
            return httpx.Response(200, json={"data": {"status": "SEND_TO_USER_INBOX"}})
        return httpx.Response(404)

    res = asyncio.run(ps.upload_tiktok(video, "Parte 1", "direct", log=logs.append, transport=httpx.MockTransport(handler)))
    assert tried == ["PUBLIC_TO_EVERYONE", "SELF_ONLY", "inbox"]  # pubblico -> privato -> bozza
    assert res["status"] == "SEND_TO_USER_INBOX" and res["privacy"] == "bozza"
    assert any("bozza" in m for m in logs)

    def always_bad(req):
        return httpx.Response(401, json={"error": {"code": "scope_not_authorized", "message": "no scope"}})
    with pytest.raises(RuntimeError, match=r"\[scope_not_authorized\].*ricollega"):
        asyncio.run(ps.upload_tiktok(video, "x", "draft", log=logs.append, transport=httpx.MockTransport(always_bad)))


def test_covers_and_youtube_thumbnail(tmp_path, secrets_dir):
    from PIL import Image
    from app.services import covers

    bg = tmp_path / "bg.png"
    Image.new("RGB", (512, 768), (200, 170, 130)).save(bg)
    cast = [{"name": "Luca", "gender": "M", "hair_style": "bald", "outfit_color": "navy", "tie": True}]
    shots = [{"bg": str(bg), "chars": [{"name": "Luca", "expression": "shocked", "pose": "phone"}]}]
    made = covers.make_all(tmp_path, [1, 2], ["La casa era mia"] * 2, [shots, shots], [str(bg), str(bg)], cast,
                           {"thumbnail_text": "LA CASA ERA MIA"}, {"title": "t"}, [])
    assert [Image.open(p).size for p in made["tiktok"]] == [(1080, 1920), (1080, 1920)]
    assert Image.open(made["youtube"]).size == (1280, 720)
    assert covers.thumbnail_text({}, {"title": "Il segreto"}, ["", "TRE MILIONI!"]) == "TRE MILIONI!"

    ps = secrets_dir
    ps.save_app("youtube", "cid", "sec")
    ps._write("youtube_token.json", {"refresh_token": "r1"})
    seen = []

    def handler(req):
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "a1"})
        seen.append((req.url.params["videoId"], req.headers["content-type"], len(req.content)))
        return httpx.Response(200, json={"items": []})
    asyncio.run(ps.set_youtube_thumbnail("VID1", made["youtube"], transport=httpx.MockTransport(handler)))
    assert seen and seen[0][0] == "VID1" and seen[0][1] == "image/jpeg" and seen[0][2] > 1000

    def forbidden(req):
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "a1"})
        return httpx.Response(403, json={"error": {"errors": [{"reason": "forbidden"}]}})
    with pytest.raises(RuntimeError, match="verificalo"):
        asyncio.run(ps.set_youtube_thumbnail("VID1", made["youtube"], transport=httpx.MockTransport(forbidden)))


# ------------------------------------------------------------------ più lingue
class FakeTranslator:
    """Traduce mettendo "EN:" davanti; testi social in inglese."""

    def __init__(self):
        self.calls = 0

    async def generate_json(self, prompt, schema=None, **kw):
        self.calls += 1
        props = (schema or {}).get("properties", {})
        if "beats" in props:
            texts = json.loads(prompt.rsplit("Blocchi: ", 1)[1])
            return {"beats": [f"EN: {t}" for t in texts]}
        n = props["part_captions"]["minItems"]
        return {"youtube_title": "When I asked my husband...", "thumbnail_text": "THE HOUSE WAS MINE", "description": "Fictional story.",
                "tiktok_caption": "Wait", "story_title": "The house", "titles": ["t"], "part_captions": [f"Part {i + 1}" for i in range(n)],
                "part_titles": ["One", "Two"], "hashtags": ["storytime", "#reddit stories"]}


def test_translate_project_into_lang_folder(tmp_path):
    from app.core import languages, translate
    from app.core.projects import save_json
    brief = {"languages": ["it", "en", "en", "xx"]}
    assert languages.of_brief(brief) == ["it", "en"]
    beats = [{"part": 1, "role": "HOOK", "text": "Mio marito voleva il divorzio.", "cue": "c"},
             {"part": 2, "role": "FINALE", "text": "La casa era mia.", "cue": "d"}]
    save_json(tmp_path / "beats.json", beats)
    save_json(tmp_path / "metadata.json", {"youtube_title": "Quando...", "part_captions": ["Parte 1", "Parte 2"], "hashtags": ["#storie"]})
    save_json(tmp_path / "structure.json", {"parts": [{"part": 1, "title": "Uno"}, {"part": 2, "title": "Due"}]})
    save_json(tmp_path / "concept.json", {"title": "La casa"})
    llm = FakeTranslator()
    assert asyncio.run(translate.translate_project(llm, tmp_path, brief, lambda *a, **k: None)) == ["en"]
    base = languages.lang_dir(tmp_path, "en", brief)
    assert base == tmp_path / "langs" / "en"
    out = json.loads((base / "beats.json").read_text(encoding="utf-8"))
    assert [b["text"] for b in out] == ["EN: Mio marito voleva il divorzio.", "EN: La casa era mia."]
    assert [b["cue"] for b in out] == ["c", "d"]  # part, role e cue restano quelli della principale
    meta = json.loads((base / "metadata.json").read_text(encoding="utf-8"))
    assert meta["story_title"] == "The house" and meta["hashtags"] == ["#storytime", "#redditstories"]
    assert json.loads((base / "structure.json").read_text(encoding="utf-8"))["parts"][1]["title"] == "Two"
    # già aggiornata: nessuna nuova chiamata; copione principale cambiato: si rifà
    calls = llm.calls
    asyncio.run(translate.translate_project(llm, tmp_path, brief, lambda *a, **k: None))
    assert llm.calls == calls
    save_json(tmp_path / "beats.json", beats[:1] + [{**beats[1], "text": "La casa era intestata a me."}])
    assert not translate.is_current(tmp_path, "en", brief)


def test_language_texts_voices_and_prompts(tmp_path):
    from app.core.languages import localize_prompt
    from app.services import eleven_service, tts_service
    from app.services.publish_service import youtube_texts
    from app.services.text_norm import normalize
    assert normalize("I paid 2,500 € and 51%", "en") == "I paid two thousand, five hundred euros and fifty-one percent"
    assert "cincuenta y uno por ciento" in normalize("el 51% de las acciones", "es")
    p = localize_prompt("Storia ambientata in Italia, italiano parlato. Personaggi con nomi italiani.", "es")
    assert p.startswith("LINGUA:") and "Spagna" in p and "spagnolo parlato" in p and "nomi italiani" not in p
    assert localize_prompt("in italiano", "it") == "in italiano"
    # voci automatiche della lingua giusta
    assert tts_service.resolve_voice("auto", "M", "es") in ("chatterbox:es_M", "edge:es-ES-AlvaroNeural", "kokoro:em_alex")
    assert tts_service.resolve_voice("auto", "M", "es", exclude=("chatterbox",)).startswith(("edge:es-", "kokoro:em_"))
    assert tts_service.emotion("Non pago un centesimo!")[0] > tts_service.emotion("Era martedì.")[0] > tts_service.emotion("E poi...")[0]
    voices = [{"id": "a", "gender": "F", "lang": "it", "italian": True, "label": "A"}, {"id": "b", "gender": "F", "lang": "en", "label": "B"}]
    assert eleven_service.pick_for_gender(voices, "F", "en") == "b" and eleven_service.pick_for_gender(voices, "F", "it") == "a"
    # testi YouTube nella lingua della cartella
    folder = tmp_path / "langs" / "en"
    (folder / "render").mkdir(parents=True)
    (folder / "metadata.json").write_text(json.dumps({"description": "A story."}), encoding="utf-8")
    (folder / "render" / "render.json").write_text(json.dumps({"lang": "en", "beats": [{"part": p, "start": s} for p, s in ((1, 0), (2, 60), (3, 120))]}), encoding="utf-8")
    d = youtube_texts(folder)["description"]
    assert "Chapters:" in d and "01:00 Part 2" in d and d.endswith("Fictional story. Voice and images generated with artificial intelligence.")


def test_youtube_shorts_from_tiktok_clips(tmp_path, monkeypatch):
    from app.services import publish_service as ps
    (tmp_path / "render" / "tiktok").mkdir(parents=True)
    for n in (1, 2):
        (tmp_path / "render" / "tiktok" / f"part_{n:02}.mp4").write_bytes(b"x")
    (tmp_path / "metadata.json").write_text(json.dumps({
        "part_captions": ["Mi ha chiesto il divorzio al compleanno. Parte 1 👉 Parte 2 nel profilo", "La casa era mia. Parte 2"],
        "hashtags": ["#storie", "#reddit"], "youtube_title": "Quando..."}), encoding="utf-8")
    (tmp_path / "concept.json").write_text(json.dumps({"title": "La casa era mia"}), encoding="utf-8")
    (tmp_path / "render" / "render.json").write_text(json.dumps({"clips": ["render/tiktok/part_01.mp4", "render/tiktok/part_02.mp4"],
        "beats": [{"part": 1, "start": 0, "end": 50}, {"part": 2, "start": 50, "end": 250}]}), encoding="utf-8")
    items = ps.shorts_texts(tmp_path)
    assert items[0]["title"] == "Mi ha chiesto il divorzio al compleanno. Parte 1 #Shorts"
    assert items[0]["description"].endswith("#storie #reddit #Shorts") and items[1]["seconds"] == 200
    uploads = []

    async def fake_upload(path, title, description, tags, privacy="private", log=print, transport=None, lang="it", short=False):
        uploads.append((Path(path).name, title, privacy, short))
        return {"id": f"S{len(uploads)}", "url": f"https://youtube.com/shorts/S{len(uploads)}", "privacy": privacy}

    monkeypatch.setattr(ps, "upload_youtube", fake_upload)
    logs = []
    record = asyncio.run(ps.publish_project(tmp_path, lambda m, p=None, level="info": logs.append((level, m)),
                                            shorts={"privacy": "unlisted", "titles": ["", "Titolo mio #Shorts"]}))
    assert uploads == [("part_01.mp4", items[0]["title"], "unlisted", True), ("part_02.mp4", "Titolo mio #Shorts", "unlisted", True)]
    assert [i["url"] for i in record["shorts"]["items"]] == ["https://youtube.com/shorts/S1", "https://youtube.com/shorts/S2"]
    assert any(level == "warn" and "3 minuti" in m for level, m in logs)  # la parte 2 dura più di 3 minuti


def test_part_length_kept_near_target():
    from app.core.pipeline import StoryPipeline, clean_leak, dedupe_sentences, trim_beats, word_count
    loop = "Lui non si è mosso. Il conto era sul tavolo di marmo nero. " * 40 + "Ma lei sapeva tutto."
    assert dedupe_sentences(loop) == "Lui non si è mosso. Il conto era sul tavolo di marmo nero. Ma lei sapeva tutto."
    assert clean_leak('fine del blocco."\n},\n{"text": "Inizio del nuovo') == "fine del blocco. Inizio del nuovo"
    assert clean_leak('Ha detto «Basta così», poi è uscita.') == 'Ha detto «Basta così», poi è uscita.'
    long_beats = [{"text": " ".join(f"Frase numero {i} del blocco {k} con qualche parola in più." for i in range(60)), "visual": "v"} for k in range(3)]
    cut = trim_beats(long_beats, 300)
    assert 200 <= sum(word_count(b["text"]) for b in cut) <= 330
    assert all(b["text"].endswith(f"Frase numero 59 del blocco {k} con qualche parola in più.") for k, b in enumerate(cut))  # finale tenuto

    class ShortLLM:  # risponde alla richiesta di accorciare con la lunghezza giusta
        model = "m"

        async def generate_json(self, prompt, schema=None, **kw):
            assert "troppo lunga" in prompt
            return {"beats": [{"text": "Parola " * 99 + "fine.", "visual": "v"} for _ in range(3)]}

    p = StoryPipeline(ShortLLM(), Path("."), FakeJob())
    out = asyncio.run(p.fit_length(1, "scrivi", long_beats, 300, 3))
    assert sum(word_count(b["text"]) for b in out) == 300


def test_chatterbox_job_and_fallback(tmp_path, monkeypatch):
    from app.services import chatterbox_worker as cw
    from app.services import tts_service as ts
    assert cw.plausible(3.0, "x" * 42) and not cw.plausible(12.0, "x" * 42) and not cw.plausible(0.3, "x" * 42)
    monkeypatch.setattr(ts, "chatterbox_ready", lambda: True)

    async def no_ref(vid):
        return None
    monkeypatch.setattr(ts, "ensure_reference", no_ref)
    jobs = []

    async def fake_worker(job, on_line, timeout=3600):
        spec = json.loads(Path(job).read_text(encoding="utf-8"))
        jobs.append(spec)
        for item in spec["items"]:
            Path(item["out"]).write_bytes(b"x" * 2000)
        on_line("PROGRESS 1 1 0.80")
        return None
    monkeypatch.setattr(ts, "run_voice_worker", fake_worker)
    tts = ts.TTSService("chatterbox:it_F", 165, "F", "it")
    msgs = []
    out = asyncio.run(tts._chatterbox("it_F", [("Ho 2.500 €. Non pago!", tmp_path / "b1")], msgs.append, ["HOOK"], device="cpu"))
    assert out == [tmp_path / "b1.wav"]
    sents = jobs[0]["items"][0]["sentences"]
    assert jobs[0]["lang"] == "it" and sents[0]["text"].startswith("Ho duemilacinquecento euro")
    assert sents[1]["exaggeration"] > sents[0]["exaggeration"]  # l'esclamazione è più accesa
    assert any("0.8× tempo reale" in m for m in msgs)

    async def broken_worker(job, on_line, timeout=3600):
        return "CUDA out of memory"
    monkeypatch.setattr(ts, "run_voice_worker", broken_worker)
    used = []

    async def fake_edge(self, voice, items, on_progress):
        used.append(voice)
        return [Path(p).with_suffix(".mp3") for _, p in items]
    monkeypatch.setattr(ts.TTSService, "_edge", fake_edge)
    monkeypatch.setattr(ts, "kokoro_ready", lambda: False)
    monkeypatch.setattr(ts, "list_voices", lambda: [{"id": "chatterbox:it_F", "engine": "chatterbox", "lang": "it"},
                                                    {"id": "edge:it-IT-ElsaNeural", "engine": "edge", "lang": "it"}])
    tts = ts.TTSService("chatterbox:it_F", 165, "F", "it")
    msgs = []
    asyncio.run(tts.synthesize_many([("Ciao.", tmp_path / "b2")], msgs.append))
    assert used == ["it-IT-ElsaNeural"] and tts.voice == "edge:it-IT-ElsaNeural"
    assert any("CUDA out of memory" in m for m in msgs)


def test_ai_providers_choice_payload_and_retry(tmp_path, monkeypatch):
    from app.services import ai_providers, anthropic_provider
    monkeypatch.setattr(ai_providers, "FILE", tmp_path / "ai.json")
    assert ai_providers.current() == "lmstudio"
    ai_providers.set_provider("ollama")
    llm = LLMService(model="qwen3")
    assert llm.provider == "ollama" and llm.base_url.endswith(":11434/v1") and llm.local
    assert "reasoning_effort" not in llm._payload([], 0.5, 100, None)  # solo LM Studio usa reasoning_effort
    ai_providers.set_provider("openai", "sk-test")
    status = ai_providers.public_status()
    assert status["current"] == "openai" and next(p for p in status["providers"] if p["id"] == "openai")["has_key"]
    assert "sk-test" not in json.dumps(status)  # la chiave non va mai alla pagina
    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append(body)
        assert req.headers["authorization"] == "Bearer sk-test" and req.url.host == "api.openai.com"
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported value: 'temperature' does not support 0.8"}})
        sse = 'data: {"choices":[{"delta":{"content":"{\\"ok\\": 1}"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
    llm = LLMService(model="gpt-test", transport=httpx.MockTransport(handler))
    llm.diagnostic_dir = tmp_path / "diag"
    assert asyncio.run(llm.generate_json("dammi json", {"type": "object"})) == {"ok": 1}
    assert "max_completion_tokens" in calls[0] and "max_tokens" not in calls[0]
    assert "temperature" in calls[0] and "temperature" not in calls[-1]  # secondo tentativo senza temperatura
    ai_providers.set_provider("anthropic", "sk-ant-test")
    llm = LLMService()
    assert llm.provider == "anthropic" and llm.model == "claude-opus-5-5" and not llm.local
    schema = {"type": "object", "properties": {"beats": {"type": "array", "minItems": 3, "maxItems": 3,
              "items": {"type": "object", "properties": {"text": {"type": "string"}}}}}}
    adapted = anthropic_provider.adapt_schema(schema)
    assert adapted["additionalProperties"] is False and adapted["required"] == ["beats"]
    assert "minItems" not in adapted["properties"]["beats"] and adapted["properties"]["beats"]["items"]["additionalProperties"] is False
    assert schema["properties"]["beats"]["minItems"] == 3  # l'originale resta intatto


def test_unsloth_waits_for_free_gpu(monkeypatch):
    from types import SimpleNamespace
    from app.services import illustration_service as ils
    readings = iter([1200, 1500, 6200])
    monkeypatch.setattr(ils, "_free_vram_mb", lambda: next(readings))
    logs = []
    ok = asyncio.run(ils.wait_for_manual_gpu(SimpleNamespace(provider="unsloth"), lambda m, lvl: logs.append((lvl, m)), every=0))
    assert ok and logs[0][0] == "warn" and "Unsloth Studio" in logs[0][1] and logs[-1][0] == "ok"
    monkeypatch.setattr(ils, "_free_vram_mb", lambda: 100)
    logs.clear()
    assert asyncio.run(ils.wait_for_manual_gpu(SimpleNamespace(provider="lmstudio"), lambda m, lvl: logs.append(m))) and not logs
