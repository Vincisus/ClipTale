"""Render 9:16 con FFmpeg + Pillow.

Per ogni parte: audio dei beat (silenzi rifilati) -> sottotitoli a blocchi tempati sulla durata reale
-> sfondo di inquadrature inerenti a ogni beat (video stock o foto con zoom lento)
-> sottotitoli bianchi in basso -> part_XX.mp4 (gia pronta per TikTok). Il video completo e la concatenazione delle parti.
"""
import asyncio
import os
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


def _caption_font(words, draw):
    size = 76
    while True:
        fnt = font(size)
        lines = _wrap(words, fnt, W - 160, draw)
        if len(lines) <= 3 or size <= 50:
            return fnt, size, lines
        size -= 4


_SHADE = None


def _bottom_shade():
    """Sfumatura scura in basso (e leggera in alto) per leggere il testo su qualsiasi immagine."""
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shade)
    top = int(H * 0.52)
    for y in range(top, H):
        a = int(170 * ((y - top) / (H - top)) ** 1.4)
        draw.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    for y in range(0, int(H * 0.12)):
        a = int(110 * (1 - y / (H * 0.12)))
        draw.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    return shade


def render_caption(text, header, path):
    """Livello trasparente 1080x1920: parte in alto, sottotitolo bianco con bordo nero in basso."""
    global _SHADE
    if _SHADE is None:
        _SHADE = _bottom_shade()
    img = _SHADE.copy()
    draw = ImageDraw.Draw(img)
    if header:
        draw.text((W / 2, H * 0.075), header, font=font(32), fill=(255, 255, 255, 215), anchor="mm", stroke_width=3, stroke_fill=(0, 0, 0, 160))
    words = text.split()
    if words:
        fnt, size, lines = _caption_font(words, draw)
        line_h = int(size * 1.2)
        bottom = int(H * 0.80)  # sopra la zona dei pulsanti di TikTok (ultimo 20%)
        top = bottom - line_h * len(lines)
        stroke = max(4, size // 12)
        for row, line in enumerate(lines):
            draw.text((W / 2, top + row * line_h), " ".join(line), font=fnt, fill=WHITE, anchor="ma", stroke_width=stroke, stroke_fill=(0, 0, 0))
    img.save(path, "PNG", compress_level=1)


def background_still(path):
    """Sfondo neutro usato quando non ci sono immagini."""
    _background().save(path, "JPEG", quality=92)
    return path


async def render_shot(source, kind, duration, output, variant=0):
    """Una inquadratura 1080x1920 a 30 fps senza audio: video ritagliato in verticale o foto con zoom lento."""
    frames = max(1, int(round(duration * FPS)))
    if kind == "video":
        vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1,format=yuv420p"
        args = ["-stream_loop", "-1", "-i", source, "-frames:v", str(frames), "-an", "-vf", vf]
    else:
        # Ken Burns: zoom avanti o indietro con un piccolo spostamento, diverso per ogni inquadratura
        zin = variant % 2 == 0
        z = f"1+0.14*on/{frames}" if zin else f"1.14-0.14*on/{frames}"
        dx = ["0", "0.6", "-0.6", "0"][variant % 4]
        x = f"(iw-iw/zoom)/2*(1+({dx})*on/{frames})"
        y = "(ih-ih/zoom)/2"
        vf = (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
              f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={FPS},setsar=1,format=yuv420p")
        args = ["-loop", "1", "-framerate", str(FPS), "-i", source, "-frames:v", str(frames), "-vf", vf]
    await run_ffmpeg([*args, "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", output])


# ---------------------------------------------------------------- render
class VideoService:
    def __init__(self, log):
        self.log = log  # callable(msg, progress=None)

    async def render_part(self, part_no, parts_total, title, beat_audio, beat_texts, work, output, cut_seconds, offset, beat_shots=None):
        """Restituisce (durata, voci SRT) per una parte. beat_shots: per ogni beat una lista di (file, 'video'|'photo')."""
        frames_dir = work / f"frames_{part_no:02}"
        if frames_dir.exists():
            shutil.rmtree(frames_dir)
        frames_dir.mkdir(parents=True)
        durations = [wav_duration(p) for p in beat_audio]
        part_wav = work / f"part_{part_no:02}.wav"
        concat_wavs(beat_audio, part_wav)
        total = sum(durations)
        header = f"Parte {part_no}/{parts_total}" if parts_total > 1 else ""

        # 1. sfondo: una o piu inquadrature per beat, codificate in parallelo
        beat_shots = beat_shots or [[] for _ in durations]
        still = None
        jobs = []
        for b, (dur, shots) in enumerate(zip(durations, beat_shots)):
            if not shots:
                still = still or background_still(frames_dir / "still.jpg")
                shots = [(still, "photo")]
            each = dur / len(shots)
            for s, (src, kind) in enumerate(shots):
                jobs.append((src, kind, each, frames_dir / f"shot_{b:03}_{s:02}.mp4", b + s))
        sem = asyncio.Semaphore(max(1, min(6, (os.cpu_count() or 4) // 4)))
        done = [0]

        async def one(src, kind, dur, out, variant):
            async with sem:
                try:
                    await render_shot(src, kind, dur, out, variant)
                except RuntimeError:  # file danneggiato: sfondo neutro
                    await render_shot(background_still(frames_dir / f"still_{variant}.jpg"), "photo", dur, out, variant)
                done[0] += 1
                if done[0] % 4 == 0 or done[0] == len(jobs):
                    self.log(f"Parte {part_no}: inquadrature {done[0]}/{len(jobs)}")

        await asyncio.gather(*(one(*j) for j in jobs))
        bg_list = work / f"bg_{part_no:02}.txt"
        bg_list.write_text("".join(f"file '{j[3].resolve().as_posix()}'\n" for j in jobs), encoding="utf-8")
        bg = work / f"bg_{part_no:02}.mp4"
        await run_ffmpeg(["-f", "concat", "-safe", "0", "-i", bg_list, "-c", "copy", bg])

        # 2. sottotitoli come livelli trasparenti tempati sull'audio
        entries, listing, t = [], [], 0.0
        for text, dur in zip(beat_texts, durations):
            for chunk, secs in caption_chunks(text, dur, cut_seconds):
                frame = frames_dir / f"{len(listing):05}.png"
                render_caption(chunk, header if t < 3.0 else "", frame)  # "Parte N/M" solo nei primi secondi: il video lungo resta pulito
                listing.append((frame, secs))
                entries.append((offset + t, offset + t + secs - (GAP_SECONDS if secs > GAP_SECONDS + 0.3 else 0), chunk))
                t += secs
        concat = work / f"frames_{part_no:02}.txt"
        lines = []
        for frame, secs in listing:
            lines += [f"file '{frame.resolve().as_posix()}'", f"duration {secs:.3f}"]
        lines.append(f"file '{listing[-1][0].resolve().as_posix()}'")
        concat.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # 3. composizione finale con la voce
        output.parent.mkdir(parents=True, exist_ok=True)
        await run_ffmpeg([
            "-i", bg, "-f", "concat", "-safe", "0", "-i", concat, "-i", part_wav,
            "-filter_complex",
            f"[0:v]tpad=stop_mode=clone:stop_duration=3[bg];[1:v]fps={FPS},format=rgba[cap];"
            f"[bg][cap]overlay=0:0:format=auto:eof_action=repeat,scale=in_range=pc:out_range=tv,format=yuv420p[v]",
            "-map", "[v]", "-map", "2:a",
            "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", str(FPS),
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
