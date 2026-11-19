"""Illustrazioni create in locale per ogni scena della storia (niente foto prese da Internet).

1. LM Studio scrive una scheda dei personaggi (aspetto fisso) e, per ogni beat, la descrizione della scena in inglese.
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
SHOT_SECONDS = 7.0  # una nuova illustrazione ogni ~7 secondi di parlato
MAX_SHOTS_PER_BEAT = 3
WIDTH, HEIGHT = 576, 1024  # 9:16, poi ingrandite a 1080x1920 con lo zoom lento

# Lo stile va all'inizio del prompt: CLIP legge solo 77 token e taglia la coda.
# Lo schizzo a matita nasce come disegno a inchiostro e poi passa nel filtro "pencil" di sd_worker:
# il modello da solo, chiedendo "pencil sketch", produce foto in bianco e nero.
STYLES = {
    "cartoon": {
        "label": "Cartoon",
        "prompt": "2d cartoon, adult animation style, bold black lineart, flat cel shading, bright flat colors, webcomic illustration",
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
NEGATIVE = ("photo, photograph, photorealistic, realistic, 3d render, smooth skin, text, letters, words, caption, watermark, signature, logo, "
            "blurry, lowres, jpeg artifacts, deformed, disfigured, extra limbs, extra fingers, bad hands, cropped head, nsfw, nude")

PROMPTS_SCHEMA = {
    "type": "object",
    "properties": {
        "characters": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "look": {"type": "string"}}, "required": ["name", "look"]}},
        "scenes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["characters", "scenes"],
}


def available():
    """True se torch e diffusers sono installati (requirements-images.txt)."""
    return all(importlib.util.find_spec(m) is not None for m in ("torch", "diffusers", "transformers", "peft"))


def models_ready():
    return (ROOT / "models" / "dreamshaper-8" / "model_index.json").is_file() and (ROOT / "models" / "lcm-lora-sdv1-5" / "pytorch_lora_weights.safetensors").is_file()


def shots_for(duration):
    return max(1, min(MAX_SHOTS_PER_BEAT, int(round(duration / SHOT_SECONDS))))


# ------------------------------------------------------------------ prompt delle scene
async def scene_prompts(llm, beats, concept):
    """(personaggi, scene): una descrizione inglese per beat, con l'aspetto dei personaggi sempre uguale."""
    chars = concept.get("characters") or []
    char_lines = "\n".join(f"- {c.get('name')}: {c.get('role', '')}" for c in chars if isinstance(c, dict) and c.get("name")) or "- (deducili dalla storia)"
    numbered = "\n".join(f"{i}. TESTO: {b.get('text', '')[:400]}\n   VISUAL: {b.get('cue', '')}" for i, b in enumerate(beats, 1))
    prompt = (
        "Devi preparare le illustrazioni di un video verticale. Rispondi in INGLESE dentro il JSON.\n\n"
        f"Storia: {concept.get('title', '')}. Ambientazione: {concept.get('setting', '')}. Tono: {concept.get('tone', '')}.\n"
        f"Personaggi:\n{char_lines}\n\n"
        "1) characters: per ogni personaggio un aspetto fisso e molto riconoscibile (età, corporatura, capelli, vestiti con colori precisi), max 25 parole.\n"
        f"2) scenes: esattamente {len(beats)} descrizioni, una per beat, nello stesso ordine. Ognuna max 35 parole: chi si vede "
        "(ripeti per intero l'aspetto del personaggio invece del nome), cosa fa, espressione del viso, luogo, luce, inquadratura. "
        "Una sola azione chiara, niente testo scritto o insegne nell'immagine, niente nomi propri.\n\n"
        f"Beat:\n{numbered}\n\n"
        'Formato: {"characters": [{"name": "...", "look": "..."}], "scenes": ["...", "..."]}'
    )
    data = await llm.generate_json(prompt, PROMPTS_SCHEMA, system="Rispondi esclusivamente con un oggetto JSON valido.", temperature=0.4, label="Scene da disegnare")
    scenes = [re.sub(r"\s+", " ", str(s)).strip() for s in (data.get("scenes") or [])]
    if len(scenes) != len(beats) or not all(scenes):
        raise ValueError(f"il modello ha descritto {len(scenes)} scene invece di {len(beats)}")
    looks = [c for c in data.get("characters") or [] if isinstance(c, dict) and c.get("name") and c.get("look")]
    # se un nome è rimasto nella scena, lo sostituisce con l'aspetto (lo stesso personaggio resta riconoscibile)
    for c in looks:
        name = re.escape(str(c["name"]).split()[0])
        scenes = [re.sub(rf"\b{name}\b", str(c["look"]), s) for s in scenes]
    return looks, scenes


def build_items(beats, durations, scenes, style, folder, seed_base):
    """Le inquadrature da disegnare: per beat 1-3 immagini con inquadrature diverse."""
    spec = STYLES.get(style, STYLES["cartoon"])
    style_prompt, post = spec["prompt"], spec["post"]
    framings = ["", "close-up on the face, ", "wide shot, "]
    out_dir = Path(folder) / "render" / "scenes"
    per_beat, items = [], []
    for b, (scene, dur) in enumerate(zip(scenes, durations)):
        shots = []
        for s in range(shots_for(dur)):
            prompt = f"{style_prompt}, {framings[s]}{scene}"
            seed = seed_base + b * 7 + s
            key = hashlib.sha1(json.dumps([prompt, seed, WIDTH, HEIGHT, post, "ds8-lcm"]).encode()).hexdigest()[:16]
            path = out_dir / f"{key}.png"
            items.append({"prompt": prompt, "negative": NEGATIVE, "seed": seed, "post": post, "out": str(path.resolve())})
            shots.append((path, "photo"))
        per_beat.append(shots)
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
async def draw(items, folder, on_line):
    """Lancia sd_worker in un processo separato e inoltra le sue righe di stato. Annullabile."""
    todo = [i for i in items if not Path(i["out"]).is_file()]
    if not todo:
        return
    work = Path(folder) / "render" / "scenes"
    work.mkdir(parents=True, exist_ok=True)
    job = work / "job.json"
    job.write_text(json.dumps({"width": WIDTH, "height": HEIGHT, "steps": 6, "guidance": 2.0, "items": items}, ensure_ascii=False), encoding="utf-8")
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "HF_HUB_DISABLE_TELEMETRY": "1", "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "app.services.sd_worker", str(job)], cwd=str(ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", creationflags=flags, env=env)
    lines, error = [], []
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
        tail = error[-1][6:] if error else (lines[-1] if lines else f"codice {proc.returncode}")
        raise RuntimeError(f"Disegno non riuscito: {tail}")
