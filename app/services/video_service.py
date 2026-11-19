"""Render 9:16 con FFmpeg + Pillow.

Per ogni parte: audio dei beat (silenzi rifilati) -> sottotitoli a blocchi tempati sulla durata reale
-> immagini 1080x1920 -> part_XX.mp4 (gia pronta per TikTok). Il video completo e la concatenazione delle parti.
"""
import asyncio
import re
import shutil
import subprocess
import wave
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1080, 1920, 30
BG_TOP, BG_BOTTOM = (16, 16, 24), (6, 6, 10)
ACCENT = (255, 59, 92)
WHITE = (255, 255, 255)
DIM = (156, 163, 175)
GAP_SECONDS = 0.25  # pausa fra un beat e l'altro
FONT_DIR = Path("C:/Windows/Fonts")


def ffmpeg_exe():
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def font(size, bold=True):
    names = ["arialbd.ttf", "segoeuib.ttf"] if bold else ["arial.ttf", "segoeui.ttf"]
    for name in names:
        if (FONT_DIR / name).is_file():
            return ImageFont.truetype(str(FONT_DIR / name), size)
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


async def run_ffmpeg(args, timeout=3600):
    """Esegue ffmpeg in modo annullabile (funziona con qualunque event loop su Windows)."""
    exe = ffmpeg_exe()
    if not exe:
        raise RuntimeError("FFmpeg non trovato: installalo o reinstalla le dipendenze (imageio-ffmpeg).")
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    proc = subprocess.Popen([exe, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=flags)
    waited = 0.0
    try:
        while proc.poll() is None:
            await asyncio.sleep(0.2)
            waited += 0.2
            if waited > timeout:
                raise TimeoutError("FFmpeg ha superato il tempo massimo")
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    err = proc.stderr.read().decode(errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg fallito ({proc.returncode}): {err[-600:]}")


def wav_duration(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def concat_wavs(paths, output):
    params, frames = None, []
    for p in paths:
        with wave.open(str(p), "rb") as w:
            params = params or w.getparams()
            frames.append(w.readframes(w.getnframes()))
    with wave.open(str(output), "wb") as out:
        out.setparams(params)
        for f in frames:
            out.writeframes(f)


async def clean_beat_audio(source, output, silence_ms):
    """Converte in WAV 44.1 kHz mono, rifila i silenzi iniziali/finali piu lunghi della soglia e aggiunge la pausa fra beat."""
    keep = max(0.0, silence_ms / 1000.0)
    trim = f"silenceremove=start_periods=1:start_silence={keep}:start_threshold=-45dB"
    af = f"{trim},areverse,{trim},areverse,apad=pad_dur={GAP_SECONDS}"
    await run_ffmpeg(["-i", source, "-af", af, "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", output])
    return wav_duration(output)


# ---------------------------------------------------------------- sottotitoli
def caption_chunks(text, duration, max_seconds):
    """Divide il testo di un beat in blocchi brevi e assegna a ciascuno un tempo proporzionale ai caratteri."""
    words = text.split()
    if not words:
        return []
    speech = max(0.5, duration - GAP_SECONDS)
    per_word = speech / len(words)
    size = max(2, min(9, round(max_seconds / per_word)))
    chunks, current = [], []
    for w in words:
        current.append(w)
        ends_sentence = re.search(r"[.!?…:;]$", w)
        if len(current) >= size or (ends_sentence and len(current) >= 2):
            chunks.append(" ".join(current))
            current = []
    if current:
        if chunks and len(current) == 1:
            chunks[-1] += " " + current[0]
        else:
            chunks.append(" ".join(current))
    total_chars = sum(len(c) for c in chunks)
    timed = [[c, speech * len(c) / total_chars] for c in chunks]
    timed[-1][1] += duration - speech  # la pausa resta sull'ultimo blocco
    return timed


def srt_time(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


# ---------------------------------------------------------------- grafica
def _background():
    img = Image.new("RGB", (W, H), BG_TOP)
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        draw.line([(0, y), (W, y)], fill=tuple(int(BG_TOP[i] * (1 - t) + BG_BOTTOM[i] * t) for i in range(3)))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse([W * 0.1, H * 0.25, W * 0.9, H * 0.75], fill=(60, 14, 26))
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    return ImageChops.add(img, glow)


_BG = None


def _wrap(words, fnt, width, draw):
    lines, line = [], []
    for w in words:
        test = " ".join(line + [w])
        if line and draw.textlength(test, font=fnt) > width:
            lines.append(line)
            line = [w]
        else:
            line.append(w)
    if line:
        lines.append(line)
    return lines


def render_frame(text, header, progress, path):
    global _BG
    if _BG is None:
        _BG = _background()
    img = _BG.copy()
    draw = ImageDraw.Draw(img, "RGBA")
    # zona sicura superiore: etichetta della parte
    small = font(34)
    draw.text((W / 2, H * 0.16), header, font=small, fill=DIM, anchor="mm")
    # sottotitolo centrale, maiuscolo, con parola evidenziata
    words = text.upper().split()
    longest = max(range(len(words)), key=lambda i: len(re.sub(r"\W", "", words[i]))) if words else -1
    size = 84
    while True:
        fnt = font(size)
        lines = _wrap(words, fnt, W - 180, draw)
        if len(lines) <= 4 or size <= 52:
            break
        size -= 6
    line_h = int(size * 1.22)
    block_h = line_h * len(lines)
    top = int(H * 0.52 - block_h / 2)
    widest = max((draw.textlength(" ".join(l), font=fnt) for l in lines), default=0)
    pad = 36
    draw.rounded_rectangle([W / 2 - widest / 2 - pad, top - pad, W / 2 + widest / 2 + pad, top + block_h + pad * 0.6], radius=26, fill=(9, 9, 11, 200), outline=(255, 255, 255, 22), width=2)
    index = 0
    for row, line in enumerate(lines):
        x = W / 2 - draw.textlength(" ".join(line), font=fnt) / 2
        y = top + row * line_h
        for w in line:
            color = ACCENT if index == longest else WHITE
            draw.text((x + 3, y + 4), w, font=fnt, fill=(0, 0, 0, 160))
            draw.text((x, y), w, font=fnt, fill=color)
            x += draw.textlength(w + " ", font=fnt)
            index += 1
    # barra di avanzamento nella zona inferiore
    y = int(H * 0.86)
    draw.rounded_rectangle([140, y, W - 140, y + 8], radius=4, fill=(255, 255, 255, 30))
    draw.rounded_rectangle([140, y, 140 + (W - 280) * max(0.0, min(1.0, progress)), y + 8], radius=4, fill=ACCENT + (230,))
    img.save(path, "JPEG", quality=90)


# ---------------------------------------------------------------- render
class VideoService:
    def __init__(self, log):
        self.log = log  # callable(msg, progress=None)

    async def render_part(self, part_no, parts_total, title, beat_audio, beat_texts, work, output, cut_seconds, offset):
        """Restituisce (durata, voci SRT) per una parte."""
        frames_dir = work / f"frames_{part_no:02}"
        if frames_dir.exists():
            shutil.rmtree(frames_dir)
        frames_dir.mkdir(parents=True)
        durations = [wav_duration(p) for p in beat_audio]
        part_wav = work / f"part_{part_no:02}.wav"
        concat_wavs(beat_audio, part_wav)
        total = sum(durations)
        header = f"PARTE {part_no}/{parts_total}" if parts_total > 1 else ""
        if title:
            header = (header + " · " if header else "") + title.upper()
        header = header[:58]
        entries, listing, t = [], [], 0.0
        for text, dur in zip(beat_texts, durations):
            for chunk, secs in caption_chunks(text, dur, cut_seconds):
                frame = frames_dir / f"{len(listing):05}.jpg"
                render_frame(chunk, header, (t + secs / 2) / total, frame)
                listing.append((frame, secs))
                entries.append((offset + t, offset + t + secs - (GAP_SECONDS if secs > GAP_SECONDS + 0.3 else 0), chunk))
                t += secs
        concat = work / f"frames_{part_no:02}.txt"
        lines = []
        for frame, secs in listing:
            lines += [f"file '{frame.resolve().as_posix()}'", f"duration {secs:.3f}"]
        lines.append(f"file '{listing[-1][0].resolve().as_posix()}'")
        concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
        output.parent.mkdir(parents=True, exist_ok=True)
        await run_ffmpeg([
            "-f", "concat", "-safe", "0", "-i", concat, "-i", part_wav,
            "-vf", f"fps={FPS},scale=in_range=pc:out_range=tv,format=yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "23",
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "44100", "-c:a", "aac", "-b:a", "192k",
            "-t", f"{total:.3f}", "-movflags", "+faststart", output,
        ])
        shutil.rmtree(frames_dir, ignore_errors=True)
        return total, entries

    @staticmethod
    async def concat_videos(paths, output, work):
        listing = work / "parts.txt"
        listing.write_text("".join(f"file '{Path(p).resolve().as_posix()}'\n" for p in paths), encoding="utf-8")
        await run_ffmpeg(["-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", "-movflags", "+faststart", output])

    @staticmethod
    def write_srt(entries, path):
        blocks = [f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{text}\n" for i, (a, b, text) in enumerate(entries, 1)]
        path.write_text("\n".join(blocks), encoding="utf-8")
