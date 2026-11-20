"""Processo separato per la voce Chatterbox Multilingual (voci realistiche locali, licenza MIT).

Uso: <python di .venv-voice> -m app.services.chatterbox_worker job.json
job.json = {"lang": "it", "ref": "percorso del campione di voce" | null, "device": "cuda" | "cpu",
            "items": [{"out": "beat_001_raw.wav", "sentences": [{"text", "pause", "exaggeration", "cfg"}]}]}

Gira nel suo ambiente (.venv-voice, torch 2.6) per non toccare le librerie dei disegni, e in un processo a parte così,
quando finisce, la memoria video torna libera. Il testo arriva già pronto per la voce (numeri in parole) dal processo
principale. Ogni frase viene sintetizzata da sola e controllata: se dura troppo o troppo poco rispetto al testo
(il difetto tipico: parole inventate o una coda di suoni alla fine) viene rifatta con un altro seme.
Stampa righe "PROGRESS n totale", "STATUS testo" e "DONE"/"ERROR messaggio" su stdout.
"""
import json
import sys
import time
import wave
from pathlib import Path


def say(*parts):
    print(*parts, flush=True)


def expected_seconds(text):
    """Durata attesa di una frase letta a ritmo normale (circa 14 caratteri al secondo, pause comprese)."""
    return max(0.6, len(text) / 14.0)


def plausible(seconds, text):
    exp = expected_seconds(text)
    return exp * 0.35 <= seconds <= exp * 2.2 + 0.8


def trim_tail(audio, rate, threshold=0.012, keep=0.12):
    """Taglia il silenzio (e il fruscio debole) in coda, lasciando un respiro di `keep` secondi."""
    import numpy as np
    loud = np.where(np.abs(audio) > threshold)[0]
    if not len(loud):
        return audio
    end = min(len(audio), loud[-1] + int(rate * keep))
    start = max(0, loud[0] - int(rate * 0.04))
    return audio[start:end]


def main(argv):
    import warnings
    warnings.filterwarnings("ignore")
    import numpy as np
    import perth
    import torch
    if perth.PerthImplicitWatermarker is None:  # filigrana non caricabile (manca setuptools<81): si va avanti senza
        perth.PerthImplicitWatermarker = perth.DummyWatermarker
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    spec = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    device = spec.get("device") or ("cuda" if torch.cuda.is_available() else "cpu")
    say(f"STATUS Carico la voce Chatterbox ({'scheda video' if device == 'cuda' else 'processore'})…")
    t0 = time.time()
    model = ChatterboxMultilingualTTS.from_pretrained(device=device)
    rate = model.sr
    ref = spec.get("ref")
    if ref:
        model.prepare_conditionals(ref, exaggeration=0.5)
    say(f"STATUS Voce pronta in {time.time() - t0:.0f}s")
    items = spec["items"]
    lang = spec.get("lang", "it")
    total_audio, started = 0.0, time.time()
    for n, item in enumerate(items, 1):
        chunks = []
        for s in item["sentences"]:
            text = s["text"].strip()
            if not text.strip(" .,;:!?"):
                continue
            best = None
            for attempt in range(3):
                torch.manual_seed(1234 + attempt * 7919 + n)
                wav = model.generate(text, language_id=lang, exaggeration=float(s.get("exaggeration", 0.5)),
                                     cfg_weight=float(s.get("cfg", 0.5)), temperature=0.8 if attempt == 0 else 0.7)
                audio = trim_tail(wav.squeeze(0).detach().cpu().numpy().astype(np.float32), rate)
                secs = len(audio) / rate
                if best is None or abs(secs - expected_seconds(text)) < abs(len(best) / rate - expected_seconds(text)):
                    best = audio
                if plausible(secs, text):
                    break
                say(f"STATUS Frase rifatta ({secs:.1f}s per {len(text)} caratteri): {text[:50]}")
            chunks.append(best)
            chunks.append(np.zeros(int(rate * float(s.get("pause", 0.3))), dtype=np.float32))
        audio = np.concatenate(chunks) if chunks else np.zeros(rate // 2, dtype=np.float32)
        peak = float(np.max(np.abs(audio))) or 1.0
        pcm = (np.clip(audio / max(peak, 0.5) * 0.95, -1, 1) * 32767).astype("<i2")
        out = Path(item["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())
        total_audio += len(audio) / rate
        speed = total_audio / max(0.1, time.time() - started)
        say(f"PROGRESS {n} {len(items)} {speed:.2f}")
    say("DONE")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # il processo principale legge l'errore e passa alla voce di riserva
        say(f"ERROR {type(exc).__name__}: {exc}")
        sys.exit(1)
