"""Illustrazioni create in locale per ogni scena della storia (niente foto prese da Internet).

1. LM Studio scrive una scheda dei personaggi (aspetto fisso) e descrive in inglese ogni "momento" del parlato
   (una nuova inquadratura ogni ~3,5 secondi, che segue la frase pronunciata), più un'etichetta-frase per beat.
2. LM Studio viene scaricato dalla memoria video (la GTX 1070 ha 8 GB: o il modello di testo o quello di disegno).
3. Stable Diffusion (app/services/sd_worker.py, processo separato) disegna ogni inquadratura nello stile scelto.
4. Il modello di testo viene ricaricato con la sua configurazione salvata in LM Studio.
Le immagini restano in render/scenes/ come cache: rifare il video con lo stesso copione non le ridisegna.
"""
import asyncio
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHOT_SECONDS = 3.5  # una nuova inquadratura ogni ~3,5 secondi, come nei reel
MAX_SHOTS_PER_BEAT = 40
WIDTH, HEIGHT = 576, 1024  # 9:16, poi ingrandite a 1080x1920 con i movimenti di camera

# Lo stile va all'inizio del prompt: CLIP legge solo 77 token e taglia la coda.
# Lo schizzo a matita nasce come disegno a inchiostro e poi passa nel filtro "pencil" di sd_worker:
# il modello da solo, chiedendo "pencil sketch", produce foto in bianco e nero.
STYLES = {
    "cartoon": {
        "label": "Cartoon",
        "prompt": "flat 2d cartoon illustration, cozy warm beige and orange palette, thick black outlines, simple round face, big expressive eyes, clean cel shading, detailed cozy illustrated background",
        "post": None,
    },
    "sketch": {
        "label": "Schizzo a matita",
        "prompt": "comic book art, inked lineart, cel shading, clean line drawing, graphic novel illustration",
        "post": "pencil",
    },
    "comic": {
        "label": "Fumetto",
        "prompt": "comic book art, inked lineart, cel shading, halftone dots, noir colors, graphic novel illustration",
        "post": None,
    },
}
NEGATIVE = ("photo, photograph, photorealistic, realistic, 3d render, anime, smooth skin, text, letters, words, caption, watermark, signature, logo, "
            "blurry, lowres, jpeg artifacts, deformed, disfigured, extra limbs, extra fingers, bad hands, cropped head, nsfw, nude")
FRAMINGS = ["medium shot, ", "close-up on the face, ", "wide shot, ", "over the shoulder shot, "]

CHARACTERS_SCHEMA = {
    "type": "object",
    "properties": {"characters": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "look": {"type": "string"}}, "required": ["name", "look"]}}},
    "required": ["characters"],
}


def shots_schema(moments, beats):
    return {
        "type": "object",
        "properties": {
            "shots": {"type": "array", "minItems": moments, "maxItems": moments, "items": {"type": "string"}},
            "stickers": {"type": "array", "minItems": beats, "maxItems": beats, "items": {"type": "string"}},
        },
        "required": ["shots", "stickers"],
    }


def available():
    """True se torch e diffusers sono installati (requirements-images.txt)."""
    return all(importlib.util.find_spec(m) is not None for m in ("torch", "diffusers", "transformers", "peft"))


def models_ready():
    return (ROOT / "models" / "dreamshaper-8" / "model_index.json").is_file() and (ROOT / "models" / "lcm-lora-sdv1-5" / "pytorch_lora_weights.safetensors").is_file()


def shots_for(duration):
    return max(1, min(MAX_SHOTS_PER_BEAT, int(round(duration / SHOT_SECONDS))))


def beat_moments(text, duration):
    """Divide il testo di un beat in momenti di ~3,5 s (gruppi di frasi o pezzi di frase) con la loro durata.

    Il tempo di ogni momento è proporzionale ai caratteri: l'immagine cambia insieme alla frase che si sente.
    """
    count = shots_for(duration)
    words = text.split()
    if not words:
        return [("", duration)]
    count = min(count, len(words))
    target = sum(len(w) + 1 for w in words) / count
    groups, current, size = [], [], 0
    for i, w in enumerate(words):
        current.append(w)
        size += len(w) + 1
        left_groups = count - len(groups) - 1
        left_words = len(words) - i - 1
        end_sentence = bool(re.search(r"[.!?…:;,»]$", w))
        if left_groups > 0 and left_words >= left_groups and (size >= target * 1.25 or (size >= target * 0.7 and end_sentence)):
            groups.append(" ".join(current))
            current, size = [], 0
    if current:
        groups.append(" ".join(current))
    total = sum(len(g) for g in groups)
    return [(g, duration * len(g) / total) for g in groups]


# ------------------------------------------------------------------ prompt delle scene
def _replace_names(text, looks):
    for c in looks:
        name = re.escape(str(c["name"]).split()[0])
        text = re.sub(rf"\b{name}\b", str(c["look"]), text)
    return text


async def scene_prompts(llm, beats, concept, moments=None):
    """(personaggi, inquadrature per beat, etichette per beat).

    moments: per ogni beat la lista di (testo, secondi) da beat_moments; senza, un momento per beat.
    Una chiamata per la scheda dei personaggi e una per parte per le inquadrature (JSON più corti = più affidabili).
    """
    moments = moments or [[(b.get("text", ""), 0)] for b in beats]
    chars = concept.get("characters") or []
    char_lines = "\n".join(f"- {c.get('name')}: {c.get('role', '')}" for c in chars if isinstance(c, dict) and c.get("name")) or "- (deducili dalla storia)"
    story = f"Storia: {concept.get('title', '')}. Ambientazione: {concept.get('setting', '')}. Tono: {concept.get('tone', '')}."
    data = await llm.generate_json(
        f"{story}\nPersonaggi:\n{char_lines}\n\nPer ogni personaggio scrivi IN INGLESE un aspetto fisso e molto riconoscibile per un cartone animato "
        "(età, corporatura, capelli, occhiali o accessori, vestiti con colori precisi), max 22 parole.\n"
        'Formato: {"characters": [{"name": "...", "look": "..."}]}',
        CHARACTERS_SCHEMA, system="Rispondi esclusivamente con un oggetto JSON valido.", temperature=0.4, label="Personaggi")
    looks = [c for c in data.get("characters") or [] if isinstance(c, dict) and c.get("name") and c.get("look")]
    look_lines = "\n".join(f"- {c['name']}: {c['look']}" for c in looks)
    shots, stickers = [], []
    parts = sorted({b.get("part", 1) for b in beats})
    for part in parts:
        idx = [i for i, b in enumerate(beats) if b.get("part", 1) == part]
        lines, n = [], 0
        for i in idx:
            lines.append(f"BEAT {idx.index(i) + 1}:")
            for text, _ in moments[i]:
                n += 1
                lines.append(f"  {n}. «{text[:220]}»")
        prompt = (
            "Devi preparare le inquadrature di un cartone animato verticale che illustra, frase per frase, il racconto qui sotto. "
            "Rispondi in INGLESE dentro il JSON.\n"
            f"{story}\nAspetto dei personaggi (ripetilo per intero ogni volta che il personaggio è in scena, mai il nome):\n{look_lines}\n\n"
            f"shots: esattamente {n} descrizioni, una per frase numerata, nello stesso ordine. Ognuna max 30 parole: chi si vede, "
            "cosa sta facendo in quel preciso momento, espressione del viso esagerata da cartone, luogo. Varia le inquadrature. "
            "Niente testo scritto, insegne o nomi propri nell'immagine.\n"
            f"stickers: esattamente {len(idx)} etichette, una per BEAT, IN ITALIANO e MAIUSCOLO: la frase chiave o la cifra di quel beat "
            "in 2-4 parole (es. «PAGAMI A GENNAIO», «3.000 € SUBITO?»), oppure stringa vuota se non serve.\n\n"
            + "\n".join(lines)
            + '\n\nFormato: {"shots": ["...", "..."], "stickers": ["...", "..."]}'
        )
        data = await llm.generate_json(prompt, shots_schema(n, len(idx)), system="Rispondi esclusivamente con un oggetto JSON valido.",
                                       temperature=0.5, label=f"Inquadrature parte {part}")
        got = [re.sub(r"\s+", " ", str(x)).strip() for x in data.get("shots") or []]
        if len(got) != n or not all(got):
            raise ValueError(f"parte {part}: il modello ha descritto {len(got)} inquadrature invece di {n}")
        got = [_replace_names(g, looks) for g in got]
        st = [re.sub(r"\s+", " ", str(x)).strip().strip("\"'«»“”").strip().upper()[:28] for x in data.get("stickers") or []]
        st = (st + [""] * len(idx))[: len(idx)]
        k = 0
        for j, i in enumerate(idx):
            shots.append(got[k:k + len(moments[i])])
            stickers.append(st[j])
            k += len(moments[i])
    return looks, shots, stickers


def build_items(beats, moments, shots, style, folder, seed_base):
    """Le immagini da disegnare. Restituisce (items per sd_worker, per beat la lista di (file, 'photo', secondi))."""
    spec = STYLES.get(style, STYLES["cartoon"])
    style_prompt, post = spec["prompt"], spec["post"]
    out_dir = Path(folder) / "render" / "scenes"
    per_beat, items, n = [], [], 0
    for b, (beat_moments_, beat_shots_) in enumerate(zip(moments, shots)):
        row = []
        for s, ((_, secs), desc) in enumerate(zip(beat_moments_, beat_shots_)):
            framing = "" if re.search(r"\b(shot|close-up|close up|view)\b", desc, re.I) else FRAMINGS[n % len(FRAMINGS)]
            prompt = f"{style_prompt}, {framing}{desc}"
            seed = seed_base + b * 31 + s
            key = hashlib.sha1(json.dumps([prompt, seed, WIDTH, HEIGHT, post, "ds8-lcm"]).encode()).hexdigest()[:16]
            path = out_dir / f"{key}.png"
            items.append({"prompt": prompt, "negative": NEGATIVE, "seed": seed, "post": post, "out": str(path.resolve())})
            row.append((path, "photo", secs))
            n += 1
        per_beat.append(row)
    return items, per_beat


# ------------------------------------------------------------------ LM Studio: libera e riprende la GPU
def lms_exe():
    found = shutil.which("lms")
    if found:
        return found
    for p in (Path.home() / ".lmstudio" / "bin" / "lms.exe", Path.home() / ".lmstudio" / "bin" / "lms"):
        if p.is_file():
            return str(p)
    return None


def _run_lms(*args, timeout=300):
    exe = lms_exe()
    if not exe:
        return False
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        return subprocess.run([exe, *args], capture_output=True, timeout=timeout, creationflags=flags).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


async def free_gpu(llm):
    """Scarica i modelli di LM Studio dalla GPU. Restituisce gli id da ricaricare dopo."""
    loaded = []
    try:
        loaded = [m["id"] for m in await llm.list_models() if m.get("loaded")]
    except Exception:
        return []
    if loaded:
        await asyncio.to_thread(_run_lms, "unload", "--all")
    return loaded


async def restore_gpu(model_ids):
    """Ricarica i modelli di testo con la configurazione salvata in LM Studio."""
    for mid in model_ids:
        await asyncio.to_thread(_run_lms, "load", mid, "-y", timeout=600)


# ------------------------------------------------------------------ disegno
async def _draw_once(job, on_line):
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "HF_HUB_DISABLE_TELEMETRY": "1", "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "app.services.sd_worker", str(job)], cwd=str(ROOT), stdout=subprocess.PIPE,
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
    try:
        while proc.poll() is None:
            await asyncio.sleep(0.3)
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    t.join(timeout=5)
    error = [ln for ln in lines if ln.startswith("ERROR")]
    if proc.returncode != 0 or error:
        return error[-1][6:] if error else (lines[-1] if lines else f"codice {proc.returncode}")
    return None


async def draw(items, folder, on_line, attempts=3):
    """Lancia sd_worker in un processo separato e inoltra le sue righe di stato. Annullabile.

    Se il processo si blocca (sulle GTX 10xx capitano errori CUDA casuali come "misaligned address", che rendono
    inutilizzabile la GPU per quel processo) lo riavvia e ridisegna solo le immagini mancanti.
    """
    work = Path(folder) / "render" / "scenes"
    work.mkdir(parents=True, exist_ok=True)
    job = work / "job.json"
    job.write_text(json.dumps({"width": WIDTH, "height": HEIGHT, "steps": 6, "guidance": 2.0, "items": items}, ensure_ascii=False), encoding="utf-8")
    last_error = None
    for attempt in range(attempts):
        missing = [i for i in items if not Path(i["out"]).is_file()]
        if not missing:
            return
        if attempt:
            on_line(f"STATUS Il disegnatore si è fermato ({last_error}): riparto dalle {len(missing)} immagini mancanti")
        last_error = await _draw_once(job, on_line)
        if last_error is None:
            return
    if any(not Path(i["out"]).is_file() for i in items):
        raise RuntimeError(f"Disegno non riuscito: {last_error}")
