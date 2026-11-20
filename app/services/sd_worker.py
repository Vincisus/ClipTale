"""Processo separato che disegna le scene con Stable Diffusion sulla GPU locale.

Uso: python -m app.services.sd_worker job.json
job.json = {"width", "height", "steps", "guidance", "ip_refs"?, "ip_scale"?, "items": [{"prompt", "negative", "seed", "out"}]}

Stile da immagini di riferimento ("ip_refs"): IP-Adapter Plus con la tecnica InstantStyle, cioè lo stile entra solo
negli strati che controllano l'aspetto (up block 1-2) e non in quelli della composizione: si copia il modo di disegnare
senza copiare i personaggi dei riferimenti. I riferimenti si codificano sulla CPU (l'encoder da 2,5 GB non tocca
la GPU) e se ne fa la media: un solo "stile" da 257 token, ~5 s a immagine 512x768 su GTX 1070.

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
IPA = ("h94/IP-Adapter", MODELS / "ip-adapter")
IPA_FILES = ["models/ip-adapter-plus_sd15.safetensors", "models/image_encoder/config.json", "models/image_encoder/model.safetensors"]
INSTANT_STYLE = {"up": {"block_1": 1.0, "block_2": 0.8}}


def say(*parts):
    print(*parts, flush=True)


def ensure_models(ip_adapter=False):
    """Scarica i pesi la prima volta (circa 2,2 GB, più 2,6 GB per lo stile da riferimenti) dentro models/."""
    from huggingface_hub import hf_hub_download, snapshot_download

    if ip_adapter and not all((IPA[1] / f).is_file() for f in IPA_FILES):
        say("STATUS Download di IP-Adapter per copiare lo stile dei riferimenti (circa 2,6 GB, solo la prima volta)…")
        for f in IPA_FILES:
            hf_hub_download(IPA[0], f, local_dir=IPA[1])

    repo, folder = BASE
    if not (folder / "model_index.json").is_file():
        say("STATUS Download del modello di disegno (circa 2 GB, solo la prima volta)…")
        snapshot_download(repo, local_dir=folder, allow_patterns=["*.json", "*.txt", "*.fp16.safetensors"],
                          ignore_patterns=["*safety_checker*"])
    repo, folder = LCM
    if not (folder / "pytorch_lora_weights.safetensors").is_file():
        say("STATUS Download dell'acceleratore LCM (67 MB)…")
        snapshot_download(repo, local_dir=folder, allow_patterns=["*.json", "pytorch_lora_weights.safetensors"])


def load_pipeline(ip_refs=None, ip_scale=None):
    """Restituisce (pipeline, dispositivo, embedding di stile o None)."""
    import gc

    import torch
    from diffusers import LCMScheduler, StableDiffusionPipeline
    from PIL import Image

    cuda = torch.cuda.is_available()
    say("STATUS Caricamento del modello di disegno su " + ("GPU" if cuda else "CPU (lento)") + "…")
    pipe = StableDiffusionPipeline.from_pretrained(
        str(BASE[1]), variant="fp16", torch_dtype=torch.float32, safety_checker=None, requires_safety_checker=False,
    )
    embeds = None
    refs = [Path(r) for r in ip_refs or [] if Path(r).is_file()]
    if refs:
        say(f"STATUS Studio dello stile da {len(refs)} immagini di riferimento…")
        pipe.load_ip_adapter(str(IPA[1]), subfolder="models", weight_name="ip-adapter-plus_sd15.safetensors", image_encoder_folder="image_encoder")
        images = [Image.open(r).convert("RGB") for r in refs]
        with torch.no_grad():
            embeds = pipe.prepare_ip_adapter_image_embeds(ip_adapter_image=[images], ip_adapter_image_embeds=None, device="cpu",
                                                          num_images_per_prompt=1, do_classifier_free_guidance=True)
        embeds = [e.mean(dim=1, keepdim=True).to("cuda" if cuda else "cpu") for e in embeds]
        pipe.register_modules(image_encoder=None)
        gc.collect()
        pipe.set_ip_adapter_scale(ip_scale or INSTANT_STYLE)
    pipe.load_lora_weights(str(LCM[1]))
    pipe.fuse_lora()
    pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
    pipe = pipe.to("cuda" if cuda else "cpu")
    pipe.enable_vae_slicing()
    pipe.set_progress_bar_config(disable=True)
    return pipe, ("cuda" if cuda else "cpu"), embeds


def main(job_path):
    import torch

    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    items = [i for i in job["items"] if not Path(i["out"]).is_file()]
    total = len(job["items"])
    if not items:
        say("DONE")
        return
    ensure_models(ip_adapter=bool(job.get("ip_refs")))
    pipe, device, embeds = load_pipeline(job.get("ip_refs"), job.get("ip_scale"))
    done = total - len(items)
    say(f"PROGRESS {done} {total}")
    for item in items:
        t = time.time()
        gen = torch.Generator(device=device).manual_seed(int(item["seed"]))
        extra = {"ip_adapter_image_embeds": embeds} if embeds is not None else {}
        image = pipe(
            prompt=item["prompt"], negative_prompt=item.get("negative") or None, **extra,
            width=int(job.get("width", 576)), height=int(job.get("height", 1024)),
            num_inference_steps=int(job.get("steps", 6)), guidance_scale=float(job.get("guidance", 1.5)), generator=gen,
        ).images[0]
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
