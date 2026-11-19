"""Processo separato che disegna le scene con Stable Diffusion sulla GPU locale.

Uso: python -m app.services.sd_worker job.json
job.json = {"width", "height", "steps", "guidance", "items": [{"prompt", "negative", "seed", "out"}]}

Gira in un processo a parte cosi, quando finisce, tutta la memoria video torna libera per LM Studio.
Stampa righe "PROGRESS n totale" e "DONE"/"ERROR messaggio" su stdout.
Modello: DreamShaper 8 (SD 1.5) + LCM-LoRA (6 passi, ~9 s a immagine 576x1024 su GTX 1070).
Sulla GTX 1070 (Pascal) si usa float32: il float16 e lentissimo. Niente attention slicing: con la GPU libera
c'e memoria a sufficienza e senza slicing e 3 volte piu veloce.
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "models"
BASE = ("Lykon/dreamshaper-8", MODELS / "dreamshaper-8")
LCM = ("latent-consistency/lcm-lora-sdv1-5", MODELS / "lcm-lora-sdv1-5")


def say(*parts):
    print(*parts, flush=True)


def ensure_models():
    """Scarica i pesi la prima volta (circa 2,2 GB) dentro models/."""
    from huggingface_hub import snapshot_download

    repo, folder = BASE
    if not (folder / "model_index.json").is_file():
        say("STATUS Download del modello di disegno (circa 2 GB, solo la prima volta)…")
        snapshot_download(repo, local_dir=folder, allow_patterns=["*.json", "*.txt", "*.fp16.safetensors"],
                          ignore_patterns=["*safety_checker*"])
    repo, folder = LCM
    if not (folder / "pytorch_lora_weights.safetensors").is_file():
        say("STATUS Download dell'acceleratore LCM (67 MB)…")
        snapshot_download(repo, local_dir=folder, allow_patterns=["*.json", "pytorch_lora_weights.safetensors"])


def load_pipeline():
    import torch
    from diffusers import LCMScheduler, StableDiffusionPipeline

    cuda = torch.cuda.is_available()
    say("STATUS Caricamento del modello di disegno su " + ("GPU" if cuda else "CPU (lento)") + "…")
    pipe = StableDiffusionPipeline.from_pretrained(
        str(BASE[1]), variant="fp16", torch_dtype=torch.float32, safety_checker=None, requires_safety_checker=False,
    )
    pipe.load_lora_weights(str(LCM[1]))
    pipe.fuse_lora()
    pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
    pipe = pipe.to("cuda" if cuda else "cpu")
    pipe.enable_vae_slicing()
    pipe.set_progress_bar_config(disable=True)
    return pipe, ("cuda" if cuda else "cpu")


def pencil(img):
    """Effetto schizzo a matita: scala di grigi + 'color dodge' col negativo sfocato, grana e carta calda."""
    import numpy as np
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    gray = ImageOps.grayscale(img)
    blur = ImageOps.invert(gray).filter(ImageFilter.GaussianBlur(9))
    a = np.asarray(gray, dtype=np.float32)
    b = np.asarray(blur, dtype=np.float32)
    sketch = Image.fromarray(np.clip(a * 255.0 / np.maximum(1.0, 255.0 - b), 0, 255).astype("uint8"))
    sketch = np.asarray(ImageEnhance.Contrast(sketch).enhance(1.5), dtype=np.float32)
    sketch = np.clip(sketch + np.random.default_rng(0).normal(0, 7, sketch.shape), 0, 255)
    paper = np.stack([sketch * 0.98 + 4, sketch * 0.95 + 6, sketch * 0.88 + 10], -1)
    return Image.fromarray(paper.clip(0, 255).astype("uint8"))


def main(job_path):
    import torch

    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    items = [i for i in job["items"] if not Path(i["out"]).is_file()]
    total = len(job["items"])
    if not items:
        say("DONE")
        return
    ensure_models()
    pipe, device = load_pipeline()
    done = total - len(items)
    say(f"PROGRESS {done} {total}")
    for item in items:
        t = time.time()
        gen = torch.Generator(device=device).manual_seed(int(item["seed"]))
        image = pipe(
            prompt=item["prompt"], negative_prompt=item.get("negative") or None,
            width=int(job.get("width", 576)), height=int(job.get("height", 1024)),
            num_inference_steps=int(job.get("steps", 6)), guidance_scale=float(job.get("guidance", 1.5)), generator=gen,
        ).images[0]
        if item.get("post") == "pencil":
            image = pencil(image)
        out = Path(item["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp.png")
        image.save(tmp)
        os.replace(tmp, out)
        done += 1
        say(f"PROGRESS {done} {total} {time.time() - t:.1f}")
    say("DONE")


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Exception as exc:  # il chiamante legge il messaggio
        say(f"ERROR {type(exc).__name__}: {str(exc)[:400]}")
        sys.exit(1)
