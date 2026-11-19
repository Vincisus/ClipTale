"""Personaggi cartoon animati (testa tonda bianca, contorni neri, occhi stanchi con le occhiaie) disegnati dal codice.

Stable Diffusion disegna solo gli sfondi vuoti (uno per luogo, nello stile dei riferimenti); i personaggi sono
disegnati qui, sempre uguali da una scena all'altra, e animati fotogramma per fotogramma:
- respirano e ondeggiano, sbattono le palpebre ogni 2-5 secondi;
- chi parla muove la bocca a tempo con la voce (inviluppo dell'audio);
- entrano con un piccolo "pop" quando cambia l'inquadratura;
- espressione e posa cambiano a ogni inquadratura secondo il copione.

Ogni stato del personaggio (espressione, posa, bocca, palpebre) viene disegnato una volta in alta risoluzione
(supersampling 2x per bordi lisci) e messo in cache; i fotogrammi sono poi solo incollati sullo sfondo in movimento.
"""
import math
import random
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H, FPS = 1080, 1920, 30
SW, SH = 1000, 1500  # tela del personaggio (unità finali)
SS = 2  # supersampling
INK = (30, 26, 26, 255)
SKIN = (248, 242, 231, 255)
BAGS = (112, 86, 128, 255)
BAGS_FILL = (176, 150, 186, 255)

EXPRESSIONS = ["neutral", "happy", "angry", "shocked", "sad", "smug", "scared", "crying"]
POSES = ["rest", "crossed", "point", "phone", "facepalm", "shrug", "hips", "holding"]
HAIR_STYLES = ["bald", "short", "bob", "long", "curly", "bun", "spiky"]
HAIR_COLORS = {"black": (40, 34, 34), "brown": (110, 70, 40), "blonde": (232, 196, 110), "red": (190, 82, 46),
               "gray": (160, 160, 160), "white": (235, 235, 235)}
OUTFIT_COLORS = {"navy": (44, 62, 108), "grey": (120, 124, 130), "black": (45, 45, 50), "mustard": (226, 168, 40),
                 "red": (192, 56, 50), "green": (70, 128, 80), "beige": (214, 186, 140), "blue": (70, 120, 200),
                 "pink": (230, 140, 160), "brown": (130, 88, 56), "white": (236, 234, 228), "orange": (226, 120, 40)}
FRAMINGS = ["single", "close", "two", "wide"]


def design_defaults(c):
    """Completa la scheda di un personaggio con valori sicuri."""
    d = {
        "name": str(c.get("name") or "?"), "gender": "M" if str(c.get("gender", "")).upper().startswith("M") else "F",
        "age": c.get("age") if c.get("age") in ("young", "adult", "old") else "adult",
        "hair_style": c.get("hair_style") if c.get("hair_style") in HAIR_STYLES else "short",
        "hair_color": c.get("hair_color") if c.get("hair_color") in HAIR_COLORS else "brown",
        "outfit_color": c.get("outfit_color") if c.get("outfit_color") in OUTFIT_COLORS else "navy",
        "tie": bool(c.get("tie")), "glasses": bool(c.get("glasses")), "beard": bool(c.get("beard")), "necklace": bool(c.get("necklace")),
    }
    if d["age"] == "old" and d["hair_color"] not in ("gray", "white"):
        d["hair_color"] = "gray"
    return d


# ------------------------------------------------------------------ disegno
def _s(v):
    return int(round(v * SS))


def _line(d, pts, color, width):
    pts = [(_s(x), _s(y)) for x, y in pts]
    d.line(pts, fill=color, width=_s(width), joint="curve")
    for x, y in (pts[0], pts[-1]):
        r = _s(width) / 2
        d.ellipse([x - r, y - r, x + r, y + r], fill=color)


def _limb(d, pts, color, width):
    """Braccio con contorno: prima il nero più spesso, poi il colore."""
    _line(d, pts, INK, width + 14)
    _line(d, pts, color, width)


def _ellipse(d, cx, cy, rx, ry, fill, outline=INK, width=9):
    d.ellipse([_s(cx - rx), _s(cy - ry), _s(cx + rx), _s(cy + ry)], fill=fill, outline=outline, width=_s(width) if outline else 0)


def _hand(d, x, y, r=34):
    _ellipse(d, x, y, r, r, SKIN, width=8)


def _poly(d, pts, fill, outline=INK, width=9):
    d.polygon([(_s(x), _s(y)) for x, y in pts], fill=fill, outline=outline, width=_s(width) if outline else 0)


def _arc(d, box, start, end, color=INK, width=8):
    d.arc([_s(box[0]), _s(box[1]), _s(box[2]), _s(box[3])], start, end, fill=color, width=_s(width))


def _darker(c, k=0.75):
    return tuple(int(v * k) for v in c[:3]) + (255,)


HEAD_C = (500, 420)
HEAD_R = (330, 312)


def _hair(d, spec, back):
    style, col = spec["hair_style"], HAIR_COLORS[spec["hair_color"]] + (255,)
    cx, cy = HEAD_C
    rx, ry = HEAD_R
    if back:  # parti dietro la testa
        if style == "long":
            _poly(d, [(cx - rx - 25, cy - 40), (cx - rx - 45, cy + 360), (cx - rx + 70, cy + 380), (cx - rx + 60, cy)], col)
            _poly(d, [(cx + rx + 25, cy - 40), (cx + rx + 45, cy + 360), (cx + rx - 70, cy + 380), (cx + rx - 60, cy)], col)
        if style == "bob":
            _ellipse(d, cx, cy + 40, rx + 40, ry + 30, col)
        if style == "curly":
            for a in range(0, 360, 24):
                t = math.radians(a)
                _ellipse(d, cx + math.cos(t) * (rx + 10), cy - 20 + math.sin(t) * (ry - 10) * (0.8 if a < 180 else 1.0), 70, 70, col, width=7)
        if style == "bun":
            _ellipse(d, cx, cy - ry - 40, 90, 80, col)
        return
    if style == "bald":  # una ciocca sola, come nei cartoni del reel
        d.line([(_s(cx - 20), _s(cy - ry + 10)), (_s(cx + 10), _s(cy - ry - 40)), (_s(cx + 60), _s(cy - ry - 30))], fill=INK, width=_s(14), joint="curve")
        return
    if style == "spiky":
        pts = [(cx - rx + 5, cy - 50)]
        for i in range(6):
            a0 = math.radians(195 + i * 25)
            a1 = math.radians(195 + i * 25 + 12)
            pts.append((cx + math.cos(a0) * (rx + 5), cy - 20 + math.sin(a0) * (ry + 5)))
            pts.append((cx + math.cos(a1) * (rx + 70), cy - 20 + math.sin(a1) * (ry + 80)))
        pts += [(cx + rx - 5, cy - 50), (cx + 150, cy - 110), (cx - 150, cy - 110)]
        _poly(d, pts, col)
        return
    if style == "curly":
        for a in range(200, 341, 20):
            t = math.radians(a)
            _ellipse(d, cx + math.cos(t) * (rx - 30), cy - 30 + math.sin(t) * (ry - 20), 75, 70, col, width=7)
        return
    # short, bob, long, bun: calotta con frangia
    top = [(cx - rx - 8, cy - 10)]
    for i in range(0, 181, 15):
        t = math.radians(180 + i)
        top.append((cx + math.cos(t) * (rx + 12), cy - 10 + math.sin(t) * (ry + 18)))
    top.append((cx + rx + 8, cy - 10))
    fringe = [(cx + rx - 20, cy - 70), (cx + 120, cy - 120), (cx + 40, cy - 90), (cx - 60, cy - 135), (cx - 160, cy - 95), (cx - rx + 20, cy - 70)]
    _poly(d, top + fringe, col)
    if style in ("bob", "long"):
        _poly(d, [(cx - rx - 30, cy - 30), (cx - rx + 22, cy - 70), (cx - rx + 18, cy + 170), (cx - rx - 40, cy + 200)], col)
        _poly(d, [(cx + rx + 30, cy - 30), (cx + rx - 22, cy - 70), (cx + rx - 18, cy + 170), (cx + rx + 40, cy + 200)], col)


EXPR = {
    #          lid  brows(inner,outer)  eye scale  pupil  mouth
    "neutral": (0.45, (0, 0), 1.0, 13, "flat"),
    "happy": (1.0, (-18, -10), 1.0, 13, "smile"),
    "angry": (0.32, (26, -14), 0.9, 10, "frown"),
    "shocked": (0.0, (-34, -26), 1.25, 9, "o"),
    "sad": (0.5, (-24, 10), 1.0, 13, "frown"),
    "smug": (0.55, (8, -2), 1.0, 13, "smirk"),
    "scared": (0.0, (-30, 6), 1.15, 9, "wobble"),
    "crying": (0.6, (-26, 12), 1.0, 12, "frown"),
}


def _face(d, spec, expression, mouth_level, blink):
    cx, cy = HEAD_C
    lid0, (b_in, b_out), escale, pupil, mouth = EXPR.get(expression, EXPR["neutral"])
    lid = 1.0 if blink >= 1 else max(lid0, 0.75 if blink > 0 else lid0)
    for side in (-1, 1):
        ex, ey = cx + side * 112, cy - 5
        erx, ery = 64 * escale, 54 * escale
        # occhiaie
        d.chord([_s(ex - erx + 2), _s(ey + ery - 52), _s(ex + erx - 2), _s(ey + ery + 26)], 15, 165, fill=BAGS_FILL)
        _arc(d, (ex - erx + 2, ey + ery - 52, ex + erx - 2, ey + ery + 26), 15, 165, BAGS, 10)
        if expression == "happy" or lid >= 0.98:  # occhi chiusi ad arco
            if expression == "happy":
                _arc(d, (ex - erx, ey - 10, ex + erx, ey + 60), 200, 340, INK, 11)
            else:
                d.line([(_s(ex - erx), _s(ey + 8)), (_s(ex + erx), _s(ey + 8))], fill=INK, width=_s(10))
        else:
            _ellipse(d, ex, ey, erx, ery, (255, 255, 255, 255), width=8)
            _ellipse(d, ex + side * 4, ey + 8, pupil, pupil, INK, outline=None)
            if lid > 0:  # palpebra superiore: copre l'occhio dall'alto
                cover = ey - ery + 2 * ery * lid
                _poly(d, [(ex - erx - 4, ey - ery - 6), (ex + erx + 4, ey - ery - 6), (ex + erx + 4, cover), (ex - erx - 4, cover)], SKIN, outline=None)
                d.line([(_s(ex - erx + 4), _s(cover)), (_s(ex + erx - 4), _s(cover))], fill=INK, width=_s(10))
                _arc(d, (ex - erx, ey - ery, ex + erx, ey + ery), 180, 360, INK, 8)
        # sopracciglia: inner/outer = spostamento verticale (positivo = giù)
        inner_x, outer_x = ex - side * 30, ex + side * 62
        by = ey - ery * escale - 34
        d.line([(_s(inner_x), _s(by + b_in)), (_s(outer_x), _s(by + b_out))], fill=INK, width=_s(13))
        if expression == "crying":
            _ellipse(d, ex + side * 30, ey + ery + 40, 12, 20, (120, 190, 240, 255), width=4)
    if expression == "scared":
        _ellipse(d, cx + 230, cy - 140, 18, 28, (150, 210, 245, 255), width=5)
    # naso
    _arc(d, (cx - 24, cy + 35, cx + 24, cy + 75), 20, 160, INK, 7)
    if spec["age"] == "old":
        for k in range(2):
            _arc(d, (cx - 110, cy - 205 + k * 26, cx + 110, cy - 150 + k * 26), 200, 340, INK, 5)
    if spec["glasses"]:
        for side in (-1, 1):
            _ellipse(d, cx + side * 112, cy - 2, 88, 76, None, width=10)
        d.line([(_s(cx - 26), _s(cy - 8)), (_s(cx + 26), _s(cy - 8))], fill=INK, width=_s(9))
    # bocca
    my = cy + 150
    if spec["beard"]:
        col = HAIR_COLORS[spec["hair_color"]] + (255,)
        _poly(d, [(cx - 210, cy + 70), (cx - 160, cy + 250), (cx, cy + 300), (cx + 160, cy + 250), (cx + 210, cy + 70), (cx + 90, cy + 120), (cx - 90, cy + 120)], col)
    if mouth_level > 0 or mouth in ("o",):
        level = max(mouth_level, 2 if mouth == "o" else 0)
        h = 14 + level * 20
        wdt = 60 if mouth == "o" else 78
        _ellipse(d, cx, my + 6, wdt, h, (120, 30, 40, 255), width=9)
        if level >= 2:
            _ellipse(d, cx, my + 6 + h * 0.45, wdt * 0.6, h * 0.35, (220, 90, 100, 255), outline=None)
    elif mouth == "smile":
        _arc(d, (cx - 95, my - 60, cx + 95, my + 40), 20, 160, INK, 11)
    elif mouth == "frown":
        _arc(d, (cx - 80, my + 5, cx + 80, my + 75), 200, 340, INK, 11)
    elif mouth == "smirk":
        d.line([(_s(cx - 70), _s(my + 10)), (_s(cx + 30), _s(my + 10)), (_s(cx + 75), _s(my - 18))], fill=INK, width=_s(11), joint="curve")
    elif mouth == "wobble":
        pts = [(cx - 70 + i * 20, my + (8 if i % 2 else -8)) for i in range(8)]
        d.line([(_s(x), _s(y)) for x, y in pts], fill=INK, width=_s(9), joint="curve")
    else:
        d.line([(_s(cx - 60), _s(my + 10)), (_s(cx + 60), _s(my + 10))], fill=INK, width=_s(11))


def _body(d, spec, pose, front):
    col = OUTFIT_COLORS[spec["outfit_color"]] + (255,)
    cx = 500
    top, bottom = 720, 1500
    sh_l, sh_r = (cx - 215, top + 70), (cx + 215, top + 70)
    if not front:
        # busto con spalle arrotondate
        shoulder = []
        for i in range(0, 19):
            t = math.radians(180 + i * 10)
            shoulder.append((cx + math.cos(t) * 235, top + 90 + math.sin(t) * 95))
        _poly(d, [(cx - 270, bottom + 20)] + shoulder + [(cx + 270, bottom + 20)], col)
        # collo + camicia
        _poly(d, [(cx - 70, top - 30), (cx + 70, top - 30), (cx + 60, top + 30), (cx - 60, top + 30)], SKIN, width=8)
        if spec["outfit_color"] != "white":
            _poly(d, [(cx - 85, top), (cx + 85, top), (cx, top + 210)], (250, 250, 248, 255), width=8)
            if spec["tie"]:
                _poly(d, [(cx - 22, top + 20), (cx + 22, top + 20), (cx + 34, top + 190), (cx, top + 230), (cx - 34, top + 190)], (30, 50, 90, 255), width=7)
            _poly(d, [(cx - 85, top), (cx - 20, top + 150), (cx - 120, top + 40)], _darker(col, 0.85), width=8)
            _poly(d, [(cx + 85, top), (cx + 20, top + 150), (cx + 120, top + 40)], _darker(col, 0.85), width=8)
        if spec["necklace"]:
            for i in range(9):
                t = math.radians(200 + i * 17.5)
                _ellipse(d, cx + math.cos(t) * 110, top + 30 - math.sin(t) * 90, 13, 13, (250, 248, 240, 255), width=4)
        if pose == "rest":
            _limb(d, [sh_l, (cx - 300, top + 420), (cx - 290, top + 640)], col, 92)
            _limb(d, [sh_r, (cx + 300, top + 420), (cx + 290, top + 640)], col, 92)
        return
    # braccia davanti al corpo
    if pose == "crossed":
        _limb(d, [sh_l, (cx - 230, top + 330), (cx + 170, top + 300)], col, 95)
        _limb(d, [sh_r, (cx + 230, top + 360), (cx - 170, top + 340)], _darker(col, 0.92), 95)
        _hand(d, cx + 185, top + 290)
        _hand(d, cx - 185, top + 330)
    elif pose == "point":
        _limb(d, [sh_l, (cx - 300, top + 420), (cx - 290, top + 640)], col, 92)
        _limb(d, [sh_r, (cx + 360, top - 20), (cx + 450, top - 160)], col, 88)
        _hand(d, cx + 455, top - 170)
        _line(d, [(cx + 465, top - 190), (cx + 500, top - 265)], INK, 26)
        _line(d, [(cx + 465, top - 190), (cx + 500, top - 265)], SKIN, 12)
    elif pose == "phone":
        _limb(d, [sh_l, (cx - 300, top + 420), (cx - 290, top + 640)], col, 92)
        _limb(d, [sh_r, (cx + 330, top + 120), (cx + 250, top - 160)], col, 88)
        _poly(d, [(cx + 200, top - 360), (cx + 280, top - 380), (cx + 330, top - 150), (cx + 250, top - 130)], (40, 40, 48, 255), width=7)
        _hand(d, cx + 255, top - 170, 40)
    elif pose == "facepalm":
        _limb(d, [sh_l, (cx - 300, top + 420), (cx - 290, top + 640)], col, 92)
        _limb(d, [sh_r, (cx + 280, top + 80), (cx + 120, top - 380)], col, 88)
        _hand(d, cx + 90, top - 420, 60)
    elif pose == "shrug":
        _limb(d, [sh_l, (cx - 360, top + 220), (cx - 430, top + 80)], col, 88)
        _limb(d, [sh_r, (cx + 360, top + 220), (cx + 430, top + 80)], col, 88)
        _hand(d, cx - 440, top + 60, 40)
        _hand(d, cx + 440, top + 60, 40)
    elif pose == "hips":
        _limb(d, [sh_l, (cx - 400, top + 260), (cx - 250, top + 440)], col, 90)
        _limb(d, [sh_r, (cx + 400, top + 260), (cx + 250, top + 440)], col, 90)
        _hand(d, cx - 240, top + 450)
        _hand(d, cx + 240, top + 450)
    elif pose == "holding":
        _limb(d, [sh_l, (cx - 260, top + 360), (cx - 120, top + 420)], col, 90)
        _limb(d, [sh_r, (cx + 260, top + 360), (cx + 120, top + 420)], col, 90)
        _poly(d, [(cx - 150, top + 250), (cx + 150, top + 250), (cx + 150, top + 520), (cx - 150, top + 520)], (252, 250, 244, 255), width=8)
        for k in range(4):
            d.line([(_s(cx - 110), _s(top + 300 + k * 45)), (_s(cx + 110), _s(top + 300 + k * 45))], fill=(150, 150, 150, 255), width=_s(6))
        _hand(d, cx - 140, top + 430)
        _hand(d, cx + 140, top + 430)


def draw_character(spec, expression="neutral", pose="rest", mouth_level=0, blink=0):
    """Immagine RGBA SWxSH del personaggio nello stato richiesto (supersampling 2x)."""
    img = Image.new("RGBA", (SW * SS, SH * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    _body(d, spec, pose, front=False)
    _hair(d, spec, back=True)
    # testa: leggermente più larga in alto (a fagiolo)
    cx, cy = HEAD_C
    rx, ry = HEAD_R
    pts = []
    for i in range(72):
        t = 2 * math.pi * i / 72
        k = 1.04 if math.sin(t) < 0 else 0.97
        pts.append((cx + math.cos(t) * rx * k, cy + math.sin(t) * ry))
    _poly(d, pts, SKIN, width=10)
    _face(d, spec, expression, mouth_level, blink)
    _hair(d, spec, back=False)
    _body(d, spec, pose, front=True)
    img = img.resize((SW, SH), Image.LANCZOS)
    # ombra morbida sotto il personaggio, per staccarlo dallo sfondo
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shadow.putalpha(img.getchannel("A").point(lambda a: int(a * 0.28)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.alpha_composite(shadow, (12, 16))
    out.alpha_composite(img)
    return out


class SpriteCache:
    def __init__(self, designs):
        self.designs = {d["name"].lower(): d for d in designs}
        self.cache = {}

    def get(self, name, expression, pose, mouth, blink, scale, flip):
        key = (name.lower(), expression, pose, mouth, blink, round(scale, 3), flip)
        img = self.cache.get(key)
        if img is None:
            base_key = key[:5]
            base = self.cache.get(base_key)
            if base is None:
                spec = self.designs.get(name.lower()) or design_defaults({"name": name})
                base = self.cache[base_key] = draw_character(spec, expression, pose, mouth, blink)
            img = base.resize((max(1, int(SW * scale)), max(1, int(SH * scale))), Image.BILINEAR)
            if flip:
                img = img.transpose(Image.FLIP_LEFT_RIGHT)
            self.cache[key] = img
        return img


# ------------------------------------------------------------------ animazione
def audio_envelope(wav_path, fps=FPS):
    """Volume della voce per fotogramma, 0..1 (per muovere la bocca)."""
    with wave.open(str(wav_path), "rb") as w:
        rate, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
        data = np.frombuffer(w.readframes(n), dtype=np.int16 if sw == 2 else np.int8).astype(np.float32)
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    hop = rate / fps
    frames = int(len(data) / hop) + 1
    env = np.array([np.sqrt(np.mean(data[int(i * hop):int((i + 1) * hop)] ** 2)) if int(i * hop) < len(data) else 0.0 for i in range(frames)])
    peak = np.percentile(env, 95) or 1.0
    return np.clip(env / peak, 0, 1)


def layout(framing, count):
    """[(x centro, y base, scala, specchiato)] per i personaggi in scena."""
    if count >= 2:
        if framing == "wide":
            return [(320, H + 40, 0.55, False), (760, H + 40, 0.55, True)]
        return [(300, H + 120, 0.72, False), (790, H + 120, 0.72, True)]
    if framing == "close":
        return [(W / 2, H + 520, 1.38, False)]
    if framing == "wide":
        return [(W / 2, H + 40, 0.62, False)]
    return [(W / 2, H + 60, 0.98, False)]


def render_picture(shots, wav_path, output, designs, ffmpeg, log=None):
    """Video senza audio 1080x1920: sfondo in movimento + personaggi animati, inquadratura per inquadratura.

    shots: [{"bg": file, "secs": s, "chars": [{"name","expression","pose"}], "speaker": nome, "framing": ...}]
    """
    env = audio_envelope(wav_path)
    sprites = SpriteCache([design_defaults(d) for d in designs])
    total_frames = sum(max(1, int(round(s["secs"] * FPS))) for s in shots)
    proc = subprocess.Popen([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                             "-r", str(FPS), "-i", "pipe:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(output)],
                            stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    rnd = random.Random(7)
    f_global = 0
    bg_cache = {}
    try:
        for si, shot in enumerate(shots):
            n = max(1, int(round(shot["secs"] * FPS)))
            src = shot.get("bg")
            if src not in bg_cache:
                try:
                    im = Image.open(src).convert("RGB")
                except Exception:
                    im = Image.new("RGB", (512, 768), (226, 210, 186))
                # riempie 1242x2208 (15% di margine per i movimenti di camera)
                bw, bh = int(W * 1.15), int(H * 1.15)
                k = max(bw / im.width, bh / im.height)
                im = im.resize((int(im.width * k) + 1, int(im.height * k) + 1), Image.BICUBIC)
                left, top = (im.width - bw) // 2, (im.height - bh) // 2
                bg_cache[src] = im.crop((left, top, left + bw, top + bh))
            bg = bg_cache[src]
            move = si % 4
            chars = (shot.get("chars") or [])[:2]
            slots = layout(shot.get("framing", "single"), len(chars))
            next_blink = {c["name"]: rnd.randint(20, 90) for c in chars}
            for i in range(n):
                p = i / max(1, n - 1)
                # camera: spinta, panoramica, allontanamento
                z = {0: 1.0 + 0.08 * p, 1: 1.06, 2: 1.08 - 0.07 * p, 3: 1.06}[move]
                cw, ch_ = int(bg.width / 1.15 / z * 1.0), int(bg.height / 1.15 / z * 1.0)
                cw, ch_ = min(cw, bg.width), min(ch_, bg.height)
                fx = {1: 0.2 + 0.6 * p, 3: 0.8 - 0.6 * p}.get(move, 0.5)
                x0 = int((bg.width - cw) * fx)
                y0 = int((bg.height - ch_) * 0.5)
                frame = bg.crop((x0, y0, x0 + cw, y0 + ch_)).resize((W, H), Image.BILINEAR).convert("RGBA")
                pop = 0.9 + 0.1 * min(1.0, i / 5) + (0.03 * math.sin(min(1.0, i / 5) * math.pi))
                for c, (cx, base_y, scale, flip) in zip(chars, slots):
                    name = c["name"]
                    blink = 0
                    if i >= next_blink[name]:
                        blink = 1 if i - next_blink[name] < 3 else 0
                        if i - next_blink[name] >= 4:
                            next_blink[name] = i + rnd.randint(60, 150)
                    mouth = 0
                    if name.lower() == str(shot.get("speaker", "")).lower():
                        e = env[min(len(env) - 1, f_global + i)]
                        mouth = 0 if e < 0.12 else 1 if e < 0.4 else 2 if e < 0.75 else 3
                    s = scale * pop
                    sprite = sprites.get(name, c.get("expression", "neutral"), c.get("pose", "rest"), mouth, blink, round(s, 2), flip)
                    bob = math.sin((f_global + i) / FPS * 2 * math.pi * 0.45 + hash(name) % 7) * 7 * scale
                    talk = (mouth > 0) * math.sin((f_global + i) / FPS * 2 * math.pi * 2.2) * 4 * scale
                    x = int(cx - sprite.width / 2)
                    y = int(base_y - sprite.height + bob + talk)
                    frame.alpha_composite(sprite, (x, y)) if 0 <= x and x + sprite.width <= W and y >= 0 and y + sprite.height <= H \
                        else _paste_clipped(frame, sprite, x, y)
                proc.stdin.write(frame.convert("RGB").tobytes())
            f_global += n
            if log and (si % 5 == 4 or si == len(shots) - 1):
                log(f"Animazione: inquadratura {si + 1}/{len(shots)} ({f_global}/{total_frames} fotogrammi)")
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="replace")
        if proc.wait() != 0:
            raise RuntimeError(f"FFmpeg (animazione) fallito: {err[-400:]}")
    except BaseException:
        proc.kill()
        raise
    return f_global / FPS


def _paste_clipped(frame, sprite, x, y):
    """Incolla un'immagine che esce dai bordi del fotogramma (alpha_composite non lo permette)."""
    l, t = max(0, -x), max(0, -y)
    r, b = min(sprite.width, W - x), min(sprite.height, H - y)
    if r > l and b > t:
        frame.alpha_composite(sprite.crop((l, t, r, b)), (x + l, y + t))
