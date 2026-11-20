"""Sintesi vocale per beat.

Motori (si sceglie dalla pagina: "Voce del PC" gratis e locale, oppure "ElevenLabs" online):
- "chatterbox:<voce>" voci REALISTICHE locali (Chatterbox Multilingual, licenza MIT) sulla scheda video, in un ambiente
  Python separato (.venv-voice) e in un processo a parte; ogni voce è clonata da un campione di ~12 s in models/chatterbox_refs/;
- "eleven:<id>" / "eleven:auto" voci ElevenLabs (online, piano dell'utente; se i crediti finiscono si torna al PC);
- "kokoro:<voce>" voci neurali locali veloci (Kokoro-82M, ONNX su processore): riserva quando Chatterbox non c'è;
- "edge:<voce>"  voci neurali Microsoft (pacchetto edge-tts, richiede Internet; il testo viene inviato a Microsoft).
"""
import asyncio
import json
import os
import subprocess
import threading
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KOKORO_DIR = ROOT / "models" / "kokoro"
VOICE_PY = ROOT / ".venv-voice" / "Scripts" / "python.exe"
CB_REFS = ROOT / "models" / "chatterbox_refs"
# voce: (nome mostrato, lingua, genere, voce Edge da cui si registra il campione da clonare)
CB_VOICES = {
    "it_F": ("Giulia (IT, realistica)", "it", "F", "it-IT-IsabellaNeural"),
    "it_M": ("Marco (IT, realistica)", "it", "M", "it-IT-GiuseppeMultilingualNeural"),
    "en_F": ("Emma (EN, realistica)", "en", "F", "en-US-AvaMultilingualNeural"),
    "en_M": ("James (EN, realistica)", "en", "M", "en-US-AndrewNeural"),
    "es_F": ("Lucía (ES, realistica)", "es", "F", "es-ES-ElviraNeural"),
    "es_M": ("Diego (ES, realistica)", "es", "M", "es-ES-AlvaroNeural"),
    "fr_F": ("Claire (FR, realistica)", "fr", "F", "fr-FR-DeniseNeural"),
    "fr_M": ("Louis (FR, realistica)", "fr", "M", "fr-FR-HenriNeural"),
    "de_F": ("Anna (DE, realistica)", "de", "F", "de-DE-KatjaNeural"),
    "de_M": ("Jonas (DE, realistica)", "de", "M", "de-DE-ConradNeural"),
    "pt_F": ("Ana (PT, realistica)", "pt", "F", "pt-BR-FranciscaNeural"),
    "pt_M": ("Pedro (PT, realistica)", "pt", "M", "pt-BR-AntonioNeural"),
}
# voci Edge di riserva per registrare il campione, se quella scelta non risponde
REF_BACKUP = {"F": {"it": "it-IT-ElsaNeural", "en": "en-US-JennyNeural", "es": "es-MX-DaliaNeural", "fr": "fr-FR-VivienneMultilingualNeural",
                    "de": "de-DE-SeraphinaMultilingualNeural", "pt": "pt-PT-RaquelNeural"},
              "M": {"it": "it-IT-DiegoNeural", "en": "en-US-GuyNeural", "es": "es-MX-JorgeNeural", "fr": "fr-FR-RemyMultilingualNeural",
                    "de": "de-DE-FlorianMultilingualNeural", "pt": "pt-PT-DuarteNeural"}}
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_VOICES = {"if_sara": "Sara (IT, naturale locale)", "im_nicola": "Nicola (IT, naturale locale)",
                 "af_heart": "Heart (EN, naturale locale)", "am_michael": "Michael (EN, naturale locale)",
                 "ef_dora": "Dora (ES, naturale locale)", "em_alex": "Alex (ES, naturale locale)",
                 "ff_siwis": "Siwis (FR, naturale locale)",
                 "pf_dora": "Dora (PT, naturale locale)", "pm_alex": "Alex (PT, naturale locale)"}
# la prima lettera della voce Kokoro indica la lingua
KOKORO_LANG = {"i": ("it", "it"), "a": ("en", "en-us"), "b": ("en", "en-gb"), "e": ("es", "es"), "f": ("fr", "fr-fr"), "p": ("pt", "pt-br")}
KOKORO_WPM = 155  # parole al minuto con speed=1.0, misurate sulla lettura frase per frase (pause comprese)
KOKORO_RATE = {"im_nicola": 1.1}  # Nicola è naturalmente più lento di Sara
_KOKORO = {}

try:
    import kokoro_onnx  # noqa: F401
    KOKORO_AVAILABLE = True
except ImportError:
    KOKORO_AVAILABLE = False


def kokoro_ready():
    return KOKORO_AVAILABLE and (KOKORO_DIR / "kokoro-v1.0.onnx").is_file() and (KOKORO_DIR / "voices-v1.0.bin").is_file()


def download_kokoro():
    """Scarica il modello Kokoro (circa 340 MB, una volta sola) in models/kokoro/."""
    import urllib.request
    KOKORO_DIR.mkdir(parents=True, exist_ok=True)
    for f in ("voices-v1.0.bin", "kokoro-v1.0.onnx"):
        target = KOKORO_DIR / f
        if not target.is_file():
            urllib.request.urlretrieve(KOKORO_URL + f, target.with_suffix(target.suffix + ".part"))
            target.with_suffix(target.suffix + ".part").replace(target)


def speakable(text, lang="it"):
    """Testo pronto per la voce: numeri, cifre, date e sigle scritti in parole (vedi text_norm)."""
    from app.services.text_norm import normalize
    return normalize(text, lang)


def _kokoro_model():
    from kokoro_onnx import Kokoro

    k = _KOKORO.get("model")
    if k is None:
        k = _KOKORO["model"] = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))
    return k


def _kokoro_synth(voice, items, wpm):
    """Sintesi frase per frase: ogni frase ha la sua intonazione e fra una frase e l'altra c'è una pausa naturale
    (più lunga dopo domande, esclamazioni e puntini), come in una lettura vera."""
    import numpy as np
    from app.services.text_norm import sentences

    k = _kokoro_model()
    speed = max(0.6, min(1.5, wpm / KOKORO_WPM * KOKORO_RATE.get(voice, 1.0)))
    created = []
    for text, path in items:
        path = Path(path).with_suffix(".wav")
        path.parent.mkdir(parents=True, exist_ok=True)
        chunks, rate = [], 24000
        lang, code = KOKORO_LANG.get(voice[:1], ("it", "it"))
        for sentence, pause in sentences(speakable(text, lang)) or [(speakable(text, lang), 0.3)]:
            if not sentence.strip(" .,;:!?"):
                continue
            samples, rate = k.create(sentence, voice=voice, speed=speed, lang=code)
            chunks.append(np.asarray(samples, dtype=np.float32))
            chunks.append(np.zeros(int(rate * pause), dtype=np.float32))
        audio = np.concatenate(chunks) if chunks else np.zeros(rate // 2, dtype=np.float32)
        pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())
        created.append(path)
    return created


AUTO_VOICES = {
    "it": {"F": ["chatterbox:it_F", "kokoro:if_sara", "edge:it-IT-ElsaNeural"], "M": ["chatterbox:it_M", "kokoro:im_nicola", "edge:it-IT-DiegoNeural"]},
    "en": {"F": ["chatterbox:en_F", "edge:en-US-AvaMultilingualNeural", "kokoro:af_heart"], "M": ["chatterbox:en_M", "edge:en-US-AndrewNeural", "kokoro:am_michael"]},
    "es": {"F": ["chatterbox:es_F", "edge:es-ES-ElviraNeural", "kokoro:ef_dora"], "M": ["chatterbox:es_M", "edge:es-ES-AlvaroNeural", "kokoro:em_alex"]},
    "fr": {"F": ["chatterbox:fr_F", "edge:fr-FR-DeniseNeural", "kokoro:ff_siwis"], "M": ["chatterbox:fr_M", "edge:fr-FR-HenriNeural"]},
    "de": {"F": ["chatterbox:de_F", "edge:de-DE-KatjaNeural"], "M": ["chatterbox:de_M", "edge:de-DE-ConradNeural"]},
    "pt": {"F": ["chatterbox:pt_F", "edge:pt-BR-FranciscaNeural", "kokoro:pf_dora"], "M": ["chatterbox:pt_M", "edge:pt-BR-AntonioNeural", "kokoro:pm_alex"]},
}


def resolve_voice(voice, gender="", lang="it", exclude=()):
    """'auto' (o vuoto) -> la migliore voce installata della lingua e del genere di chi racconta (femminile se incerto).
    `exclude`: motori da saltare (es. ("chatterbox",) per scegliere la riserva quando Chatterbox non funziona)."""
    if voice and voice != "auto":
        return voice
    voices = [v for v in list_voices() if v.get("engine") not in exclude]
    available = {v["id"] for v in voices}
    auto = AUTO_VOICES.get(lang, AUTO_VOICES["it"])
    for vid in auto["M" if gender == "M" else "F"] + auto["F" if gender == "M" else "M"]:
        if vid in available:
            return vid
    same = sorted(v["id"] for v in voices if v["id"] != "auto" and v.get("lang") == lang)
    if same:
        return same[0]
    return auto["M" if gender == "M" else "F"][0]


PREVIEW_TEXTS = {
    "it": ("Quando chiesi a mio marito cosa volesse per il compleanno, mi rispose che voleva il divorzio. "
           "Ma lui non sapeva una cosa: la casa, con il 51% delle quote, era intestata a me."),
    "en": ("When I asked my husband what he wanted for his birthday, he said he wanted a divorce. "
           "But he didn't know one thing: the house, with 51% of the shares, was in my name."),
    "es": ("Cuando le pregunté a mi marido qué quería para su cumpleaños, me dijo que quería el divorcio. "
           "Pero él no sabía una cosa: la casa, con el 51% de las acciones, estaba a mi nombre."),
    "fr": ("Quand j'ai demandé à mon mari ce qu'il voulait pour son anniversaire, il m'a répondu qu'il voulait divorcer. "
           "Mais il ignorait une chose : la maison, avec 51 % des parts, était à mon nom."),
    "de": ("Als ich meinen Mann fragte, was er sich zum Geburtstag wünscht, sagte er, er wolle die Scheidung. "
           "Aber eines wusste er nicht: Das Haus, mit 51 % der Anteile, lief auf meinen Namen."),
    "pt": ("Quando perguntei ao meu marido o que ele queria de aniversário, ele disse que queria o divórcio. "
           "Mas ele não sabia de uma coisa: a casa, com 51% das cotas, estava no meu nome."),
}
PREVIEW_TEXT = PREVIEW_TEXTS["it"]


EDGE_VOICES = [
    ("it-IT-ElsaNeural", "Elsa (IT, neurale)"),
    ("it-IT-DiegoNeural", "Diego (IT, neurale)"),
    ("it-IT-IsabellaNeural", "Isabella (IT, neurale)"),
    ("it-IT-GiuseppeMultilingualNeural", "Giuseppe (IT, neurale)"),
    ("en-US-AvaMultilingualNeural", "Ava (EN, neurale)"),
    ("en-US-AndrewNeural", "Andrew (EN, neurale)"),
    ("en-GB-SoniaNeural", "Sonia (EN-GB, neurale)"),
    ("en-GB-RyanNeural", "Ryan (EN-GB, neurale)"),
    ("es-ES-ElviraNeural", "Elvira (ES, neurale)"),
    ("es-ES-AlvaroNeural", "Álvaro (ES, neurale)"),
    ("es-MX-DaliaNeural", "Dalia (ES-MX, neurale)"),
    ("es-MX-JorgeNeural", "Jorge (ES-MX, neurale)"),
    ("fr-FR-DeniseNeural", "Denise (FR, neurale)"),
    ("fr-FR-HenriNeural", "Henri (FR, neurale)"),
    ("de-DE-KatjaNeural", "Katja (DE, neurale)"),
    ("de-DE-ConradNeural", "Conrad (DE, neurale)"),
    ("pt-BR-FranciscaNeural", "Francisca (PT-BR, neurale)"),
    ("pt-BR-AntonioNeural", "Antônio (PT-BR, neurale)"),
]
BASE_WPM = 150  # velocita "normale" approssimativa delle voci neurali italiane

try:
    import edge_tts  # noqa: F401
    EDGE_AVAILABLE = True
except ImportError:
    EDGE_AVAILABLE = False


def chatterbox_ready():
    """Chatterbox è installato nel suo ambiente (.venv-voice)."""
    return VOICE_PY.is_file()


def chatterbox_custom():
    """Voci clonate dall'utente: models/chatterbox_refs/custom_<nome>.wav (10-20 s di parlato pulito)."""
    return sorted(p.stem for p in CB_REFS.glob("custom_*.wav")) if CB_REFS.is_dir() else []


async def ensure_reference(vid):
    """Il campione da clonare per una voce Chatterbox. Le voci di base si registrano la prima volta da una voce Edge
    (frase neutra di ~12 s); le voci dell'utente sono già file. None = voce predefinita del modello."""
    path = CB_REFS / f"{vid}.wav"
    if path.is_file() or vid not in CB_VOICES:
        return path if path.is_file() else None
    if not EDGE_AVAILABLE:
        return None
    from app.services.video_service import ffmpeg_exe
    _, lang, gender, edge_voice = CB_VOICES[vid]
    CB_REFS.mkdir(parents=True, exist_ok=True)
    mp3 = path.with_suffix(".mp3")
    import edge_tts
    for attempt, voice in enumerate([edge_voice, edge_voice, REF_BACKUP[gender][lang]]):
        try:
            await edge_tts.Communicate(speakable(PREVIEW_TEXTS[lang], lang), voice, rate="-4%").save(str(mp3))
            break
        except Exception:
            mp3.unlink(missing_ok=True)
            await asyncio.sleep(1 + attempt)
    else:
        return None  # nessun campione: Chatterbox usa la sua voce predefinita
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    await asyncio.to_thread(subprocess.run, [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(mp3), "-ac", "1", "-ar", "24000", str(path)],
                            capture_output=True, creationflags=flags)
    mp3.unlink(missing_ok=True)
    return path if path.is_file() else None


def emotion(sentence, role=""):
    """Intensità della voce per frase (Chatterbox): esclamazioni e colpi di scena più accesi, puntini più sommessi.
    Restituisce (exaggeration, cfg_weight): un cfg più basso rallenta e dà più espressione."""
    s = sentence.strip()
    if s.endswith(("!", "!»", "!\"", "?!")):
        return 0.75, 0.35
    if s.endswith(("...", "…")):
        return 0.4, 0.5
    if s.endswith(("?", "?»", "?\"")):
        return 0.6, 0.45
    if role in ("HOOK", "CLIFFHANGER", "FINALE"):
        return 0.62, 0.45
    return 0.5, 0.5


def list_voices():
    """Voci del PC. Ogni voce ha "lang" (it, en, es…); "auto" ("*") vale per tutte le lingue."""
    voices = [{"id": "auto", "label": "Automatica: maschile o femminile in base a chi racconta", "engine": "auto", "online": False, "lang": "*"}]
    voices += [{"id": f"kokoro:{v}", "label": label, "engine": "kokoro", "online": False, "lang": KOKORO_LANG.get(v[:1], ("it",))[0]}
               for v, label in KOKORO_VOICES.items()] if kokoro_ready() else []
    if chatterbox_ready():
        voices += [{"id": f"chatterbox:{vid}", "label": label, "engine": "chatterbox", "online": False, "lang": lang, "gender": g}
                   for vid, (label, lang, g, _) in CB_VOICES.items()]
        voices += [{"id": f"chatterbox:{vid}", "label": vid[7:].replace("_", " ").title() + " (tua voce, realistica)", "engine": "chatterbox",
                    "online": False, "lang": "*"} for vid in chatterbox_custom()]
    if EDGE_AVAILABLE:
        voices += [{"id": f"edge:{vid}", "label": label, "engine": "edge", "online": True, "lang": vid[:2]} for vid, label in EDGE_VOICES]
    order = {"auto": -1, "chatterbox": 0, "kokoro": 1, "edge": 2}
    voices.sort(key=lambda v: order[v["engine"]])
    return voices


def default_voice():
    return resolve_voice("auto")


class TTSService:
    def __init__(self, voice=None, wpm=BASE_WPM, gender="", lang="it"):
        self.gender = gender
        self.lang = lang or "it"
        self.voice = voice if str(voice or "").startswith("eleven:") else resolve_voice(voice, gender, self.lang)
        self.wpm = int(wpm)

    async def prepare(self):
        """Risolve 'eleven:auto' nella voce ElevenLabs del genere giusto (serve la lista delle voci dell'account)."""
        from app.services import eleven_service
        if self.voice == "eleven:auto":
            vid = eleven_service.pick_for_gender(await eleven_service.voices(), self.gender, self.lang)
            self.voice = f"eleven:{vid}" if vid else resolve_voice("auto", self.gender, self.lang)
        return self.voice

    async def synthesize_many(self, items, on_progress=None, roles=None):
        """items: lista di (testo, percorso_mp3_o_wav). Restituisce l'elenco dei file creati.
        roles: ruolo di ogni beat (HOOK, CLIFFHANGER…), per dare più intensità ai momenti forti con Chatterbox."""
        await self.prepare()
        engine, _, name = self.voice.partition(":")
        if engine == "chatterbox":
            try:
                return await self._chatterbox(name, items, on_progress, roles)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                fallback = resolve_voice("auto", self.gender, self.lang, exclude=("chatterbox",))
                if on_progress:
                    on_progress(f"Voce realistica non disponibile ({exc}). Continuo con {fallback}")
                self.voice = fallback
                engine, _, name = fallback.partition(":")
        if engine == "eleven":
            from app.services import eleven_service
            try:
                return await eleven_service.synthesize(name, items, speed=self.wpm / 160, on_progress=on_progress)
            except eleven_service.QuotaError as exc:
                fallback = resolve_voice("auto", self.gender, self.lang)
                if on_progress:
                    on_progress(f"{exc}. Continuo con la voce del PC ({fallback})")
                self.voice = fallback
                engine, _, name = fallback.partition(":")
        if engine == "kokoro":
            if not kokoro_ready():
                raise RuntimeError("Voce Kokoro non installata: esegui installa_disegni.bat")
            if on_progress:
                on_progress(f"Voce locale {KOKORO_VOICES.get(name, name)}: sintesi di {len(items)} beat…")
            return await asyncio.to_thread(_kokoro_synth, name, items, self.wpm)
        if engine == "edge":
            try:
                return await self._edge(name, items, on_progress)
            except Exception as exc:
                local = next((v for v in AUTO_VOICES.get(self.lang, {}).get(self.gender or "F", []) if v.startswith("kokoro:")), None)
                if not (local and kokoro_ready()):
                    raise RuntimeError(f"Voce online non disponibile ({exc}) e nessuna voce locale per questa lingua.") from exc
                if on_progress:
                    on_progress(f"Voce online non disponibile ({type(exc).__name__}): uso la voce locale {local}")
                self.voice = local
                return await asyncio.to_thread(_kokoro_synth, local.partition(":")[2], items, self.wpm)
        raise ValueError(f"Voce non riconosciuta: {self.voice}")

    async def _chatterbox(self, vid, items, on_progress, roles=None, device=None):
        """Sintesi con Chatterbox nel suo processo. Sulla scheda video LM Studio viene scaricato prima e ricaricato dopo
        (8 GB non bastano per entrambi)."""
        from app.services.text_norm import sentences
        if not chatterbox_ready():
            raise RuntimeError("Chatterbox non installato")
        label, lang, _, _ = CB_VOICES.get(vid, (vid, self.lang, "", ""))
        lang = self.lang if vid.startswith("custom_") else lang
        ref = await ensure_reference(vid)
        out_dir = Path(items[0][1]).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        job_items = []
        for k, (text, path) in enumerate(items):
            role = (roles or [""] * len(items))[k] if roles and k < len(roles) else ""
            parts = sentences(speakable(text, lang)) or [(speakable(text, lang), 0.3)]
            job_items.append({"out": str(Path(path).with_suffix(".wav")), "sentences": [
                {"text": t, "pause": p, "exaggeration": emotion(t, role)[0], "cfg": emotion(t, role)[1]} for t, p in parts]})
        device = device or os.getenv("CHATTERBOX_DEVICE", "cuda")
        job = out_dir / "_chatterbox_job.json"
        job.write_text(json.dumps({"lang": lang, "ref": str(ref) if ref else None, "device": device, "items": job_items}, ensure_ascii=False), encoding="utf-8")
        reload_models = []
        if device == "cuda":
            from app.services import illustration_service
            from app.services.llm_service import LLMService
            engine_llm = LLMService()
            reload_models = await illustration_service.free_gpu(engine_llm)
            await illustration_service.wait_for_manual_gpu(engine_llm, lambda m, lvl: on_progress and on_progress(m), need_mb=4000)
            if reload_models and on_progress:
                on_progress("LM Studio messo in pausa per far posto alla voce realistica (verrà ricaricato)")
        if on_progress:
            on_progress(f"Voce realistica {label}: {len(items)} beat…")
        try:
            error = await run_voice_worker(job, lambda line: on_progress and _forward(line, on_progress, len(items)))
        finally:
            job.unlink(missing_ok=True)
            if reload_models:
                from app.services import illustration_service
                await illustration_service.restore_gpu(reload_models)
        if error:
            raise RuntimeError(error)
        created = [Path(i["out"]) for i in job_items]
        missing = [p.name for p in created if not p.is_file() or p.stat().st_size < 1000]
        if missing:
            raise RuntimeError("audio non creato per " + ", ".join(missing))
        return created

    async def _edge(self, voice, items, on_progress):
        import edge_tts
        pct = round((self.wpm / BASE_WPM - 1) * 100)
        rate = f"{pct:+d}%"
        created = []
        for i, (text, path) in enumerate(items, 1):
            path = Path(path).with_suffix(".mp3")
            path.parent.mkdir(parents=True, exist_ok=True)
            for attempt in range(3):
                try:
                    await edge_tts.Communicate(speakable(text, voice[:2]), voice, rate=rate).save(str(path))
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2 * (attempt + 1))
            if not path.is_file() or path.stat().st_size < 500:
                raise RuntimeError(f"Audio vuoto per il beat {i}")
            created.append(path)
            if on_progress:
                on_progress(f"Voce: beat {i}/{len(items)}")
        return created


def _forward(line, on_progress, total):
    if line.startswith("PROGRESS"):
        parts = line.split()
        speed = f" · {float(parts[3]):.1f}× tempo reale" if len(parts) > 3 else ""
        on_progress(f"Voce realistica: beat {parts[1]}/{parts[2]}{speed}")
    elif line.startswith("STATUS"):
        on_progress(line[7:])


async def run_voice_worker(job, on_line, timeout=3600):
    """Lancia chatterbox_worker con il Python di .venv-voice e inoltra le righe di stato. Annullabile.
    Restituisce il messaggio d'errore, o None se è andato tutto bene."""
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "HF_HUB_DISABLE_TELEMETRY": "1", "PYTHONUNBUFFERED": "1", "TOKENIZERS_PARALLELISM": "false"}
    proc = subprocess.Popen([str(VOICE_PY), "-m", "app.services.chatterbox_worker", str(job)], cwd=str(ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", creationflags=flags, env=env)
    lines = []
    loop = asyncio.get_running_loop()

    def reader():
        for line in proc.stdout:
            line = line.strip()
            if line:
                lines.append(line)
                loop.call_soon_threadsafe(on_line, line)
    t = threading.Thread(target=reader, daemon=True)
    t.start()
    waited = 0.0
    try:
        while proc.poll() is None:
            await asyncio.sleep(0.3)
            waited += 0.3
            if waited > timeout:
                proc.kill()
                return "la voce non ha finito in tempo"
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    t.join(timeout=5)
    error = [ln for ln in lines if ln.startswith("ERROR")]
    if proc.returncode != 0 or error:
        return error[-1][6:] if error else (lines[-1] if lines else f"codice {proc.returncode}")
    return None
