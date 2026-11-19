"""Sintesi vocale per beat.

Motori (si sceglie dalla pagina: "Voce del PC" gratis e locale, oppure "ElevenLabs" online):
- "eleven:<id>" / "eleven:auto" voci ElevenLabs (online, piano dell'utente; se i crediti finiscono si torna al PC);
- "kokoro:<voce>" voci neurali LOCALI più naturali (Kokoro-82M, ONNX su CPU): if_sara, im_nicola. File in models/kokoro/;
- "piper:<file>" voci neurali LOCALI (Piper, ONNX su CPU): nessun dato esce dal PC. File .onnx in models/piper/;
- "edge:<voce>"  voci neurali Microsoft (pacchetto edge-tts, richiede Internet; il testo viene inviato a Microsoft);
- "local:<nome>" voci SAPI installate in Windows (offline), sintetizzate in un processo separato
  perche pyttsx3/COM non e affidabile se richiamato piu volte dentro il server.
"""
import asyncio
import json
import subprocess
import sys
import wave
from pathlib import Path

PIPER_DIR = Path(__file__).resolve().parents[2] / "models" / "piper"
KOKORO_DIR = Path(__file__).resolve().parents[2] / "models" / "kokoro"
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
    "it": {"F": ["kokoro:if_sara", "edge:it-IT-ElsaNeural", "piper:it_IT-paola-medium"],
           "M": ["kokoro:im_nicola", "edge:it-IT-DiegoNeural", "piper:it_IT-riccardo-x_low"]},
    "en": {"F": ["edge:en-US-AvaMultilingualNeural", "kokoro:af_heart"], "M": ["edge:en-US-AndrewMultilingualNeural", "kokoro:am_michael"]},
    "es": {"F": ["edge:es-ES-ElviraNeural", "kokoro:ef_dora"], "M": ["edge:es-ES-AlvaroNeural", "kokoro:em_alex"]},
    "fr": {"F": ["edge:fr-FR-DeniseNeural", "kokoro:ff_siwis"], "M": ["edge:fr-FR-HenriNeural"]},
    "de": {"F": ["edge:de-DE-KatjaNeural"], "M": ["edge:de-DE-ConradNeural"]},
    "pt": {"F": ["edge:pt-BR-FranciscaNeural", "kokoro:pf_dora"], "M": ["edge:pt-BR-AntonioNeural", "kokoro:pm_alex"]},
}


def resolve_voice(voice, gender="", lang="it"):
    """'auto' (o vuoto) -> la migliore voce installata della lingua e del genere di chi racconta (femminile se incerto)."""
    if voice and voice != "auto":
        return voice
    voices = list_voices()
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


PIPER_LABELS = {"it_IT-paola-medium": "Paola (IT, neurale locale)", "it_IT-riccardo-x_low": "Riccardo (IT, neurale locale)"}
_PIPER_CACHE = {}

try:
    import piper  # noqa: F401
    PIPER_AVAILABLE = True
except ImportError:
    PIPER_AVAILABLE = False


PIPER_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT/paola/medium/"


def download_piper_voice(name="it_IT-paola-medium"):
    """Scarica la voce Piper italiana (una volta sola) in models/piper/."""
    import urllib.request
    PIPER_DIR.mkdir(parents=True, exist_ok=True)
    for f in (f"{name}.onnx.json", f"{name}.onnx"):
        target = PIPER_DIR / f
        if not target.is_file():
            urllib.request.urlretrieve(PIPER_URL + f, target.with_suffix(target.suffix + ".part"))
            target.with_suffix(target.suffix + ".part").replace(target)


def piper_voices():
    if not PIPER_AVAILABLE or not PIPER_DIR.is_dir():
        return []
    return [p.stem for p in sorted(PIPER_DIR.glob("*.onnx")) if p.with_suffix(".onnx.json").is_file()]


def _piper_synth(name, items, wpm):
    from piper import PiperVoice, SynthesisConfig

    voice = _PIPER_CACHE.get(name) or PiperVoice.load(str(PIPER_DIR / f"{name}.onnx"))
    _PIPER_CACHE[name] = voice
    # length_scale > 1 rallenta: Piper parla a circa 160 parole al minuto con 1.0
    cfg = SynthesisConfig(length_scale=max(0.6, min(1.6, 160 / max(80, wpm))))
    created = []
    for text, path in items:
        path = Path(path).with_suffix(".wav")
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            voice.synthesize_wav(speakable(text, "it"), w, syn_config=cfg)
        created.append(path)
    return created

EDGE_VOICES = [
    ("it-IT-ElsaNeural", "Elsa (IT, neurale)"),
    ("it-IT-DiegoNeural", "Diego (IT, neurale)"),
    ("it-IT-IsabellaNeural", "Isabella (IT, neurale)"),
    ("it-IT-GiuseppeMultilingualNeural", "Giuseppe (IT, neurale)"),
    ("en-US-AvaMultilingualNeural", "Ava (EN, neurale)"),
    ("en-US-AndrewMultilingualNeural", "Andrew (EN, neurale)"),
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


def _run_sapi_worker(args, timeout):
    return subprocess.run([sys.executable, "-m", "app.services.sapi_worker", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def local_voices():
    try:
        result = _run_sapi_worker(["--list"], 30)
        return json.loads(result.stdout or "[]") if result.returncode == 0 else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


SAPI_LANG = {"ital": "it", "english": "en", "spanish": "es", "español": "es", "french": "fr", "fran": "fr", "german": "de", "deutsch": "de", "portug": "pt"}


def _sapi_lang(name, italian=False):
    low = name.lower()
    return "it" if italian else next((code for key, code in SAPI_LANG.items() if key in low), "")


def list_voices():
    """Voci del PC. Ogni voce ha "lang" (it, en, es…); "auto" ("*") vale per tutte le lingue."""
    voices = [{"id": "auto", "label": "Automatica: maschile o femminile in base a chi racconta", "engine": "auto", "online": False, "lang": "*"}]
    voices += [{"id": f"kokoro:{v}", "label": label, "engine": "kokoro", "online": False, "lang": KOKORO_LANG.get(v[:1], ("it",))[0]}
               for v, label in KOKORO_VOICES.items()] if kokoro_ready() else []
    voices += [{"id": f"piper:{n}", "label": PIPER_LABELS.get(n, n + " (neurale locale)"), "engine": "piper", "online": False, "lang": n[:2]} for n in piper_voices()]
    if EDGE_AVAILABLE:
        voices += [{"id": f"edge:{vid}", "label": label, "engine": "edge", "online": True, "lang": vid[:2]} for vid, label in EDGE_VOICES]
    for v in local_voices():
        voices.append({"id": f"local:{v['id']}", "label": v["name"] + " (Windows, offline)", "engine": "local", "online": False,
                       "italian": v.get("italian", False), "lang": _sapi_lang(v["name"], v.get("italian", False))})
    order = {"auto": -1, "kokoro": 0, "piper": 1, "edge": 2, "local": 3}
    voices.sort(key=lambda v: (order[v["engine"]], not v.get("italian", True)))
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

    async def synthesize_many(self, items, on_progress=None):
        """items: lista di (testo, percorso_mp3_o_wav). Restituisce l'elenco dei file creati."""
        await self.prepare()
        engine, _, name = self.voice.partition(":")
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
        if engine == "piper":
            if name not in piper_voices():
                raise RuntimeError(f"Voce locale {name} non trovata in models/piper/")
            if on_progress:
                on_progress(f"Voce locale {name}: sintesi di {len(items)} beat…")
            return await asyncio.to_thread(_piper_synth, name, items, self.wpm)
        if engine == "edge":
            try:
                return await self._edge(name, items, on_progress)
            except Exception as exc:
                if piper_voices() and self.lang == "it":
                    if on_progress:
                        on_progress(f"Voce online non disponibile ({type(exc).__name__}): uso la voce neurale locale")
                    return await asyncio.to_thread(_piper_synth, piper_voices()[0], items, self.wpm)
                if not local_voices():
                    raise RuntimeError(f"Voce online non disponibile ({exc}) e nessuna voce Windows installata.") from exc
                if on_progress:
                    on_progress(f"Voce online non disponibile ({type(exc).__name__}): uso la voce di Windows")
                fallback = next((v for v in local_voices() if _sapi_lang(v["name"], v.get("italian", False)) == self.lang), local_voices()[0])
                engine, name = "local", fallback["id"]
        if engine == "local":
            return await self._local(name, items, on_progress)
        raise ValueError(f"Voce non riconosciuta: {self.voice}")

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

    async def _local(self, voice_id, items, on_progress):
        jobs = [{"text": t, "path": str(Path(p).with_suffix(".wav"))} for t, p in items]
        for j in jobs:
            Path(j["path"]).parent.mkdir(parents=True, exist_ok=True)
        spec = Path(jobs[0]["path"]).parent / "_sapi_jobs.json"
        spec.write_text(json.dumps({"voice": voice_id, "rate": self.wpm, "items": jobs}, ensure_ascii=False), encoding="utf-8")
        if on_progress:
            on_progress(f"Voce Windows: sintesi di {len(jobs)} beat…")
        result = await asyncio.to_thread(_run_sapi_worker, [str(spec)], 60 + 30 * len(jobs))
        spec.unlink(missing_ok=True)
        if result.returncode != 0:
            raise RuntimeError("Sintesi vocale Windows fallita: " + (result.stderr or result.stdout)[-400:])
        created = [Path(j["path"]) for j in jobs]
        missing = [p.name for p in created if not p.is_file() or p.stat().st_size < 500]
        if missing:
            raise RuntimeError("Audio non creato per: " + ", ".join(missing))
        return created
