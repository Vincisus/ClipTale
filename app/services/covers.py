"""Copertine: una 9:16 per ogni clip TikTok ("PARTE N") e una 16:9 per il video lungo su YouTube.

- Sfondo: il luogo della prima inquadratura della parte (cartone animato) o il primo disegno della parte.
- Personaggi: nel cartone animato, quelli in scena nel momento più "forte" della parte (shocked/angry/scared), grandi.
- Testo: titolo e "PARTE N" su TikTok; su YouTube una frase di 2-5 parole (metadata["thumbnail_text"]), enorme.

TikTok non accetta un'immagine di copertina separata: la copertina diventa il primo fotogramma della clip
(il fotogramma che TikTok usa come copertina predefinita). Su YouTube viene caricata con thumbnails.set.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from app.services.video_service import KARAOKE, font_black

DRAMATIC = ("shocked", "angry", "scared", "crying", "smug")


def _wrap(text, fnt, width, draw):
    words, lines, line = text.split(), [], []
    for w in words:
        test = " ".join(line + [w])
        if line and draw.textlength(test, font=fnt) > width:
            lines.append(" ".join(line))
            line = [w]
        else:
            line.append(w)
    if line:
        lines.append(" ".join(line))
    return lines


def _fit_text(draw, text, width, max_size, min_size, max_lines):
    size = max_size
    while True:
        fnt = font_black(size)
        lines = _wrap(text, fnt, width, draw)
        if len(lines) <= max_lines or size <= min_size:
            return fnt, size, lines
        size -= 6


def _background(src, size):
    w, h = size
    try:
        im = Image.open(src).convert("RGB")
    except Exception:
        im = Image.new("RGB", (512, 768), (226, 210, 186))
    k = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * k) + 1, int(im.height * k) + 1), Image.LANCZOS)
    left, top = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((left, top, left + w, top + h))


def _pick_shot(shots):
    """Il momento più espressivo della parte (per le facce in copertina)."""
    for s in shots:
        if isinstance(s, dict) and any(c.get("expression") in DRAMATIC for c in s.get("chars", [])):
            return s
    return next((s for s in shots if isinstance(s, dict)), None)


def _draw_cast(canvas, shot, cast, slots):
    from app.services import toon
    if not shot:
        return
    designs = {c["name"].lower(): c for c in cast}
    for c, (cx, base_y, scale, flip) in zip(shot.get("chars", [])[:2], slots):
        spec = designs.get(c["name"].lower()) or toon.design_defaults({"name": c["name"]})
        expr = c.get("expression") if c.get("expression") in DRAMATIC else "shocked"
        sprite = toon.draw_character(toon.design_defaults(spec), expr, c.get("pose", "rest"), mouth_level=2 if expr in ("shocked", "scared") else 0)
        sprite = sprite.resize((int(sprite.width * scale), int(sprite.height * scale)), Image.LANCZOS)
        if flip:
            sprite = sprite.transpose(Image.FLIP_LEFT_RIGHT)
        x, y = int(cx - sprite.width / 2), int(base_y - sprite.height)
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        layer.paste(sprite, (x, y), sprite)
        canvas.alpha_composite(layer)


def _outlined(draw, xy, text, fnt, fill, stroke, anchor="mm"):
    x, y = xy
    draw.text((x + 6, y + 8), text, font=fnt, fill=(0, 0, 0, 140), anchor=anchor, stroke_width=stroke, stroke_fill=(0, 0, 0, 140))
    draw.text((x, y), text, font=fnt, fill=fill, anchor=anchor, stroke_width=stroke, stroke_fill=(0, 0, 0))


def tiktok_cover(out, part_no, parts_total, title, bg, shots=None, cast=None, lang="it"):
    """Copertina 1080x1920 della clip: sfondo, personaggi, "PARTE N" grande e titolo."""
    W, H = 1080, 1920
    img = _background(bg, (W, H)).convert("RGBA")
    img = Image.alpha_composite(img, Image.new("RGBA", (W, H), (0, 0, 0, 40)))
    shot = _pick_shot(shots or [])
    if shot and cast:
        n = len(shot.get("chars", [])[:2])
        slots = [(W / 2, H + 380, 1.25, False)] if n == 1 else [(300, H + 260, 0.95, False), (790, H + 260, 0.95, True)]
        _draw_cast(img, shot, cast, slots)
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(shade)
    for y in range(0, 760):
        d.line([(0, y), (W, y)], fill=(0, 0, 0, int(170 * (1 - y / 760) ** 1.3)))
    img = Image.alpha_composite(img, shade)
    d = ImageDraw.Draw(img)
    from app.core.languages import info
    words = info(lang)
    label = f"{words['part']} {part_no}" if parts_total > 1 else words["full"]
    _outlined(d, (W / 2, 230), label, font_black(170 if parts_total > 1 else 120), KARAOKE, 12)
    fnt, size, lines = _fit_text(d, title.upper(), W - 140, 84, 52, 4)
    y = 400
    for line in lines:
        _outlined(d, (W / 2, y), line, fnt, (255, 255, 255), 9)
        y += int(size * 1.12)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "JPEG", quality=92)
    return Path(out)


def youtube_thumbnail(out, text, bg, shots=None, cast=None):
    """Copertina 1280x720: personaggi grandi a destra, frase enorme a sinistra, sfondo leggermente sfocato."""
    W, H = 1280, 720
    base = _background(bg, (W, H))
    base = ImageEnhance.Color(base.filter(ImageFilter.GaussianBlur(3))).enhance(1.25)
    img = base.convert("RGBA")
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(grad)
    for x in range(W):
        d.line([(x, 0), (x, H)], fill=(0, 0, 0, int(175 * max(0, 1 - x / (W * 0.62)))))
    img = Image.alpha_composite(img, grad)
    shot = _pick_shot(shots or [])
    if shot and cast:
        n = len(shot.get("chars", [])[:2])
        slots = [(990, H + 430, 0.8, True)] if n == 1 else [(860, H + 380, 0.66, False), (1120, H + 380, 0.66, True)]
        _draw_cast(img, shot, cast, slots)
    d = ImageDraw.Draw(img)
    fnt, size, lines = _fit_text(d, text.upper(), 700, 150, 70, 3)
    total = len(lines) * int(size * 1.08)
    y = (H - total) / 2 + size / 2
    for i, line in enumerate(lines):
        _outlined(d, (60, y), line, fnt, KARAOKE if i == len(lines) - 1 else (255, 255, 255), 10, anchor="lm")
        y += int(size * 1.08)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "JPEG", quality=90)
    return Path(out)


def thumbnail_text(meta, concept, stickers):
    """La frase per la copertina YouTube: dal modello, oppure la prima etichetta, oppure il titolo."""
    t = str((meta or {}).get("thumbnail_text") or "").strip().strip("\"'«»“”")
    if not t:
        t = next((s for s in stickers or [] if s), "") or str((concept or {}).get("title", "Storia"))
    words = t.split()
    return " ".join(words[:6])


def make_all(folder, parts, part_titles, part_shots, part_bgs, cast, meta, concept, stickers, lang="it"):
    """Crea tutte le copertine in render/covers/. Restituisce {"tiktok": [file], "youtube": file}."""
    folder = Path(folder)
    covers = folder / "render" / "covers"
    tiktok = []
    for n, (title, shots, bg) in enumerate(zip(part_titles, part_shots, part_bgs), 1):
        tiktok.append(tiktok_cover(covers / f"tiktok_part_{n:02}.jpg", n, len(parts), title, bg, shots, cast, lang))
    all_shots = [s for shots in part_shots for s in shots]
    yt = youtube_thumbnail(covers / "youtube.jpg", thumbnail_text(meta, concept, stickers), part_bgs[0] if part_bgs else None, all_shots, cast)
    return {"tiktok": tiktok, "youtube": yt}
