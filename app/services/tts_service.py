"""Sintesi vocale per beat.

Quattro motori:
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
KOKORO_VOICES = {"if_sara": "Sara (IT, naturale locale)", "im_nicola": "Nicola (IT, naturale locale)"}
KOKORO_WPM = 200  # velocità di Kokoro con speed=1.0 sull'italiano, misurata
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


def speakable(text):
    """Testo più adatto alla lettura: virgolette caporali e trattini lunghi diventano pause naturali."""
    import re
    text = text.replace("«", "").replace("»", "").replace("“", "").replace("”", "")
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    text = text.replace("…", "...")
    return re.sub(r"\s+", " ", text).strip()


def _kokoro_synth(voice, items, wpm):
    import numpy as np
    from kokoro_onnx import Kokoro

    k = _KOKORO.get("model")
    if k is None:
        k = _KOKORO["model"] = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))
    speed = max(0.6, min(1.4, wpm / KOKORO_WPM))
    created = []
    for text, path in items:
        path = Path(path).with_suffix(".wav")
        path.parent.mkdir(parents=True, exist_ok=True)
        samples, rate = k.create(speakable(text), voice=voice, speed=speed, lang="it")
        pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())
        created.append(path)
    return created
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
            voice.synthesize_wav(text, w, syn_config=cfg)
        created.append(path)
    return created

EDGE_VOICES = [
    ("it-IT-ElsaNeural", "Elsa (IT, neurale)"),
    ("it-IT-DiegoNeural", "Diego (IT, neurale)"),
    ("it-IT-IsabellaNeural", "Isabella (IT, neurale)"),
    ("it-IT-GiuseppeMultilingualNeural", "Giuseppe (IT, neurale)"),
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


def list_voices():
    voices = [{"id": f"kokoro:{v}", "label": label, "engine": "kokoro", "online": False} for v, label in KOKORO_VOICES.items()] if kokoro_ready() else []
    voices += [{"id": f"piper:{n}", "label": PIPER_LABELS.get(n, n + " (neurale locale)"), "engine": "piper", "online": False} for n in piper_voices()]
    if EDGE_AVAILABLE:
        voices += [{"id": f"edge:{vid}", "label": label, "engine": "edge", "online": True} for vid, label in EDGE_VOICES]
    for v in local_voices():
        voices.append({"id": f"local:{v['id']}", "label": v["name"] + " (Windows, offline)", "engine": "local", "online": False, "italian": v.get("italian", False)})
    order = {"kokoro": 0, "piper": 1, "edge": 2, "local": 3}
    voices.sort(key=lambda v: (order[v["engine"]], not v.get("italian", True)))
    return voices


def default_voice():
    voices = list_voices()
    return voices[0]["id"] if voices else "edge:it-IT-ElsaNeural"


class TTSService:
    def __init__(self, voice=None, wpm=BASE_WPM):
        self.voice = voice or default_voice()
        self.wpm = int(wpm)

    async def synthesize_many(self, items, on_progress=None):
        """items: lista di (testo, percorso_mp3_o_wav). Restituisce l'elenco dei file creati."""
        engine, _, name = self.voice.partition(":")
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
                if piper_voices():
                    if on_progress:
                        on_progress(f"Voce online non disponibile ({type(exc).__name__}): uso la voce neurale locale")
                    return await asyncio.to_thread(_piper_synth, piper_voices()[0], items, self.wpm)
                if not local_voices():
                    raise RuntimeError(f"Voce online non disponibile ({exc}) e nessuna voce Windows installata.") from exc
                if on_progress:
                    on_progress(f"Voce online non disponibile ({type(exc).__name__}): uso la voce di Windows")
                fallback = next((v for v in local_voices() if v.get("italian")), local_voices()[0])
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
                    await edge_tts.Communicate(text, voice, rate=rate).save(str(path))
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
