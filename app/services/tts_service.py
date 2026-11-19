"""Sintesi vocale per beat.

Due motori:
- "edge:<voce>"  voci neurali Microsoft (pacchetto edge-tts, richiede Internet; il testo viene inviato a Microsoft);
- "local:<nome>" voci SAPI installate in Windows (offline), sintetizzate in un processo separato
  perche pyttsx3/COM non e affidabile se richiamato piu volte dentro il server.
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

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
    voices = []
    if EDGE_AVAILABLE:
        voices += [{"id": f"edge:{vid}", "label": label, "engine": "edge", "online": True} for vid, label in EDGE_VOICES]
    for v in local_voices():
        voices.append({"id": f"local:{v['id']}", "label": v["name"] + " (Windows, offline)", "engine": "local", "online": False, "italian": v.get("italian", False)})
    voices.sort(key=lambda v: (v["engine"] != "edge", not v.get("italian", True)))
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
        if engine == "edge":
            try:
                return await self._edge(name, items, on_progress)
            except Exception as exc:
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
