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
# Due stili: "anim" (cartone animato con personaggi che parlano) e "cartoon" (mostrato come "Illustrazione":
# una scena disegnata per ogni momento). I progetti fatti con gli stili tolti (reel, schizzo, fumetto) usano "cartoon".
STYLE_REFS = ROOT / "style_refs"  # immagini di riferimento per lo "Stile reel" (non vanno su Git)

STYLES = {
    "anim": {
        "label": "Cartone animato",
        # sfondi vuoti nello stile dei riferimenti; i personaggi sono disegnati e animati da toon.py
        "prompt": "flat 2d cartoon background, thick black outlines, warm beige palette, cozy illustrated interior, detailed props",
        "post": None,
        "refs": True,
        "size": (512, 768),
        "animated": True,
    },
    "cartoon": {
        "label": "Illustrazione",
        "prompt": "flat 2d cartoon illustration, cozy warm beige and orange palette, thick black outlines, simple round face, big expressive eyes, clean cel shading, detailed cozy illustrated background",
        "post": None,
    },
}


def normalize_style(style):
    return style if style in STYLES else "cartoon"
NEGATIVE = ("two people stacked, duplicate person, multiple heads, photo, photograph, photorealistic, realistic, 3d render, anime, smooth skin, text, letters, words, caption, watermark, signature, logo, "
            "blurry, lowres, jpeg artifacts, deformed, disfigured, extra limbs, extra fingers, bad hands, cropped head, nsfw, nude")
BG_NEGATIVE = ("person, people, man, woman, character, face, head, body, crowd, text, letters, watermark, logo, photo, "
               "photorealistic, 3d render, blurry, deformed")
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


def style_refs():
    return sorted(p for p in STYLE_REFS.glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))


def default_style():
    return "anim"


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


def _lang_name(lang):
    from app.core.languages import info
    return info(lang)["it"].upper()


async def scene_prompts(llm, beats, concept, moments=None, lang="it"):
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
            f"stickers: esattamente {len(idx)} etichette, una per BEAT, IN {_lang_name(lang)} e MAIUSCOLO: la frase chiave o la cifra di quel beat "
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
            size = spec.get("size", (WIDTH, HEIGHT))
            refs_sig = [f"{r.name}:{r.stat().st_size}" for r in style_refs()] if spec.get("refs") else []
            key = hashlib.sha1(json.dumps([prompt, seed, list(size), post, refs_sig, "ds8-lcm"]).encode()).hexdigest()[:16]
            path = out_dir / f"{key}.png"
            items.append({"prompt": prompt, "negative": NEGATIVE, "seed": seed, "post": post, "out": str(path.resolve())})
            row.append((path, "photo", secs))
            n += 1
        per_beat.append(row)
    return items, per_beat


# ------------------------------------------------------------------ cartone animato: piano delle inquadrature
def toon_cast_schema():
    from app.services import toon
    char = {"type": "object", "properties": {
        "name": {"type": "string"}, "gender": {"type": "string", "enum": ["F", "M"]},
        "age": {"type": "string", "enum": ["young", "adult", "old"]},
        "hair_style": {"type": "string", "enum": toon.HAIR_STYLES}, "hair_color": {"type": "string", "enum": list(toon.HAIR_COLORS)},
        "outfit_color": {"type": "string", "enum": list(toon.OUTFIT_COLORS)},
        "tie": {"type": "boolean"}, "glasses": {"type": "boolean"}, "beard": {"type": "boolean"}, "necklace": {"type": "boolean"},
    }, "required": ["name", "gender", "age", "hair_style", "hair_color", "outfit_color", "tie", "glasses", "beard", "necklace"]}
    return {"type": "object", "properties": {
        "characters": {"type": "array", "minItems": 1, "maxItems": 6, "items": char},
        "places": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "string"}},
    }, "required": ["characters", "places"]}


def toon_shots_schema(moments, beats, names, places):
    from app.services import toon
    on_screen = {"type": "object", "properties": {
        "name": {"type": "string", "enum": names}, "expression": {"type": "string", "enum": toon.EXPRESSIONS},
        "pose": {"type": "string", "enum": toon.POSES}}, "required": ["name", "expression", "pose"]}
    shot = {"type": "object", "properties": {
        "place": {"type": "integer", "minimum": 0, "maximum": max(0, places - 1)},
        "chars": {"type": "array", "minItems": 1, "maxItems": 2, "items": on_screen},
        "speaker": {"type": "string", "enum": names},
        "framing": {"type": "string", "enum": toon.FRAMINGS}}, "required": ["place", "chars", "speaker", "framing"]}
    return {"type": "object", "properties": {
        "shots": {"type": "array", "minItems": moments, "maxItems": moments, "items": shot},
        "stickers": {"type": "array", "minItems": beats, "maxItems": beats, "items": {"type": "string"}},
    }, "required": ["shots", "stickers"]}


async def toon_plan(llm, beats, concept, moments, lang="it"):
    """(cast, luoghi, inquadrature per beat, etichette per beat) per il cartone animato.

    cast: schede dei personaggi (toon.design_defaults); luoghi: descrizioni inglesi degli ambienti vuoti;
    inquadratura: {"place", "chars": [{"name","expression","pose"}], "speaker", "framing"}.
    """
    from app.services import toon
    chars = concept.get("characters") or []
    char_lines = "\n".join(f"- {c.get('name')}: {c.get('role', '')}" for c in chars if isinstance(c, dict) and c.get("name")) or "- (deducili dalla storia)"
    story = (f"Storia: {concept.get('title', '')}. Ambientazione: {concept.get('setting', '')}. "
             f"Chi racconta in prima persona: {'una donna' if concept.get('narrator_gender') == 'F' else 'un uomo' if concept.get('narrator_gender') == 'M' else 'il protagonista'}.")
    sample = " ".join(b.get("text", "") for b in beats)[:2500]
    data = await llm.generate_json(
        f"{story}\nPersonaggi:\n{char_lines}\nInizio del racconto: {sample[:1200]}\n\n"
        "1) characters: la scheda grafica di ogni personaggio di un cartone animato (incluso chi racconta, con il suo nome): "
        "genere, età, acconciatura, colore dei capelli, colore del vestito, cravatta, occhiali, barba, collana. "
        "Rendili molto diversi fra loro e coerenti col ruolo.\n"
        "2) places: da 2 a 6 luoghi della storia, IN INGLESE, come ambienti vuoti da disegnare (es. 'cozy family kitchen at night', "
        "'notary office with wooden desk and folders').",
        toon_cast_schema(), system="Rispondi esclusivamente con un oggetto JSON valido.", temperature=0.4, label="Personaggi e luoghi")
    cast = [toon.design_defaults(c) for c in data.get("characters") or [] if isinstance(c, dict) and c.get("name")]
    places = [re.sub(r"\s+", " ", str(p)).strip() for p in data.get("places") or [] if str(p).strip()][:8] or [concept.get("setting") or "cozy living room"]
    names = [c["name"] for c in cast] or ["Narratore"]
    if not cast:
        cast = [toon.design_defaults({"name": "Narratore", "gender": concept.get("narrator_gender", "F")})]
    cast_lines = "\n".join(f"- {c['name']} ({'donna' if c['gender'] == 'F' else 'uomo'}, {c['age']})" for c in cast)
    place_lines = "\n".join(f"{i}. {p}" for i, p in enumerate(places))
    shots, stickers = [], []
    for part in sorted({b.get("part", 1) for b in beats}):
        idx = [i for i, b in enumerate(beats) if b.get("part", 1) == part]
        lines, n = [], 0
        for i in idx:
            lines.append(f"BEAT {idx.index(i) + 1}:")
            for text, _ in moments[i]:
                n += 1
                lines.append(f"  {n}. «{text[:220]}»")
        prompt = (
            "Sei il regista di un cartone animato verticale. Per ogni frase numerata del racconto decidi l'inquadratura:\n"
            "- place: il numero del luogo;\n- chars: 1 o 2 personaggi in scena (quelli di cui parla la frase), con espressione e posa adatte "
            "(esagerate, da cartone: shocked per le rivelazioni, angry per gli insulti, smug per le rivincite, phone se telefona, "
            "holding se mostra un documento, point se accusa, facepalm se è esasperato, crossed se è sulla difensiva);\n"
            "- speaker: chi sta parlando in quella frase (chi racconta, se è narrazione);\n"
            "- framing: single (un personaggio a mezzo busto), close (primo piano sulle reazioni forti), two (due personaggi), wide (scena larga). "
            "Varia spesso inquadrature ed espressioni.\n"
            f"stickers: esattamente {len(idx)} etichette, una per BEAT, IN {_lang_name(lang)} e MAIUSCOLO: la frase chiave o la cifra del beat in 2-4 parole, "
            "oppure stringa vuota.\n\n"
            f"{story}\nPersonaggi:\n{cast_lines}\nLuoghi:\n{place_lines}\n\nFrasi:\n" + "\n".join(lines)
        )
        data = await llm.generate_json(prompt, toon_shots_schema(n, len(idx), names, len(places)),
                                       system="Rispondi esclusivamente con un oggetto JSON valido.", temperature=0.5, label=f"Regia parte {part}")
        got = [s for s in data.get("shots") or [] if isinstance(s, dict)]
        if len(got) != n:
            raise ValueError(f"parte {part}: il modello ha pianificato {len(got)} inquadrature invece di {n}")
        clean = []
        for s in got:
            cs = [c for c in s.get("chars") or [] if isinstance(c, dict) and c.get("name") in names][:2] or [{"name": names[0]}]
            cs = [{"name": c["name"], "expression": c.get("expression") if c.get("expression") in toon.EXPRESSIONS else "neutral",
                   "pose": c.get("pose") if c.get("pose") in toon.POSES else "rest"} for c in cs]
            framing = s.get("framing") if s.get("framing") in toon.FRAMINGS else "single"
            if len(cs) == 2 and framing in ("single", "close"):
                framing = "two"
            if len(cs) == 1 and framing == "two":
                framing = "single"
            clean.append({"place": min(max(0, int(s.get("place") or 0)), len(places) - 1), "chars": cs,
                          "speaker": s.get("speaker") if s.get("speaker") in names else cs[0]["name"], "framing": framing})
        st = [re.sub(r"\s+", " ", str(x)).strip().strip("\"'«»“”").strip().upper()[:28] for x in data.get("stickers") or []]
        st = (st + [""] * len(idx))[: len(idx)]
        k = 0
        for j, i in enumerate(idx):
            shots.append(clean[k:k + len(moments[i])])
            stickers.append(st[j])
            k += len(moments[i])
    return cast, places, shots, stickers


def toon_background_items(places, folder, seed_base):
    """Uno sfondo vuoto per luogo, nello stile dei riferimenti. Restituisce (items per sd_worker, file per luogo)."""
    spec = STYLES["anim"]
    out_dir = Path(folder) / "render" / "scenes"
    refs_sig = [f"{r.name}:{r.stat().st_size}" for r in style_refs()]
    items, files = [], []
    for i, place in enumerate(places):
        prompt = f"{spec['prompt']}, empty {place}, no people, nobody, interior background"
        seed = seed_base + 1000 + i
        key = hashlib.sha1(json.dumps([prompt, seed, list(spec["size"]), refs_sig, "bg-v1"]).encode()).hexdigest()[:16]
        path = out_dir / f"bg_{key}.png"
        items.append({"prompt": prompt, "negative": BG_NEGATIVE, "seed": seed, "post": None, "out": str(path.resolve())})
        files.append(path)
    return items, files


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


MANUAL_GPU = {"unsloth": "Unsloth Studio"}  # motori locali che l'app non può mettere in pausa da sola


def _free_vram_mb():
    """Memoria video libera in MB (nvidia-smi), o None se non si può leggere."""
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10, creationflags=flags).stdout
        return int(out.strip().splitlines()[0])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


async def wait_for_manual_gpu(llm, log, need_mb=5000, timeout=900, every=5):
    """Con un motore che l'app non sa scaricare (Unsloth), aspetta che l'utente liberi la scheda video.
    Restituisce True se la memoria è libera (o non si può misurare), False se il tempo è scaduto."""
    name = MANUAL_GPU.get(getattr(llm, "provider", ""))
    if not name:
        return True
    free = await asyncio.to_thread(_free_vram_mb)
    if free is None or free >= need_mb:
        return True
    log(f"Scarica il modello in {name} (Unload) per liberare la scheda video: l'app non può farlo da sola. "
        f"Aspetto che si liberi la memoria ({free} MB liberi, ne servono {need_mb})…", "warn")
    waited = 0
    while waited < timeout:
        await asyncio.sleep(every)
        waited += every
        free = await asyncio.to_thread(_free_vram_mb)
        if free is None or free >= need_mb:
            log(f"Memoria video libera ({free} MB): continuo", "ok")
            return True
        if waited % 60 == 0:
            log(f"Sto ancora aspettando: scarica il modello in {name} ({free} MB liberi su {need_mb} necessari)", "warn")
    log(f"Memoria video ancora occupata dopo {timeout // 60} minuti: provo lo stesso, potrebbe non bastare", "warn")
    return False


async def free_gpu(llm):
    """Libera la scheda video dal motore AI locale. Restituisce gli id da ricaricare dopo (solo LM Studio:
    Ollama ricarica da solo il modello alla richiesta successiva; i motori cloud non usano la GPU)."""
    provider = getattr(llm, "provider", "lmstudio")
    if provider == "ollama":
        try:
            import httpx
            async with httpx.AsyncClient(timeout=20) as client:
                for m in (await client.get(llm.server_root + "/api/ps")).json().get("models", []):
                    await client.post(llm.server_root + "/api/generate", json={"model": m["name"], "keep_alive": 0})
        except Exception:
            pass
        return []
    if provider != "lmstudio":
        return []
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


async def draw(items, folder, on_line, attempts=3, style="cartoon"):
    """Lancia sd_worker in un processo separato e inoltra le sue righe di stato. Annullabile.

    Se il processo si blocca (sulle GTX 10xx capitano errori CUDA casuali come "misaligned address", che rendono
    inutilizzabile la GPU per quel processo) lo riavvia e ridisegna solo le immagini mancanti.
    """
    work = Path(folder) / "render" / "scenes"
    work.mkdir(parents=True, exist_ok=True)
    job = work / "job.json"
    spec = STYLES.get(style, STYLES["cartoon"])
    width, height = spec.get("size", (WIDTH, HEIGHT))
    payload = {"width": width, "height": height, "steps": 6, "guidance": 2.0, "items": items}
    if spec.get("refs") and style_refs():
        payload["ip_refs"] = [str(r.resolve()) for r in style_refs()]
    job.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
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
