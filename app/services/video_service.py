"""Render 9:16 con FFmpeg + Pillow.

Per ogni parte: audio dei beat (silenzi rifilati) -> sottotitoli a blocchi tempati sulla durata reale
-> sfondo di inquadrature inerenti a ogni beat (video stock o foto con zoom lento)
-> sottotitoli bianchi in basso -> part_XX.mp4 (gia pronta per TikTok). Il video completo e la concatenazione delle parti.
"""
import asyncio
import bisect
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
    size = max(2, min(5, round(max_seconds / per_word)))
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


def font_black(size):
    """Carattere molto spesso per sottotitoli ed etichette (Segoe UI Black, poi Arial Black / Arial Bold)."""
    for name in ("seguibl.ttf", "ariblk.ttf", "arialbd.ttf"):
        if (FONT_DIR / name).is_file():
            return ImageFont.truetype(str(FONT_DIR / name), size)
    return font(size)


def _caption_font(words, draw):
    size = 74
    while True:
        fnt = font_black(size)
        lines = _wrap(words, fnt, W - 150, draw)
        if len(lines) <= 2 or size <= 50:
            return fnt, size, lines
        size -= 4


_SHADE = None
KARAOKE = (255, 210, 31)  # giallo della parola pronunciata
STICKER_COLORS = [(46, 158, 68), (215, 38, 61), (33, 112, 214), (232, 140, 20)]


def _bottom_shade():
    """Sfumatura leggera in basso (e in alto) per leggere il testo su qualsiasi disegno."""
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shade)
    top = int(H * 0.58)
    for y in range(top, H):
        a = int(120 * ((y - top) / (H - top)) ** 1.6)
        draw.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    for y in range(0, int(H * 0.10)):
        a = int(80 * (1 - y / (H * 0.10)))
        draw.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    return shade


def _sticker(text, color, scale):
    """Etichetta colorata e inclinata con la frase chiave, come un timbro."""
    fnt = font_black(int(64 * scale))
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tw = probe.textlength(text, font=fnt)
    pad_x, pad_y = int(34 * scale), int(20 * scale)
    box_w, box_h = int(tw + pad_x * 2), int(64 * scale * 1.25 + pad_y * 2)
    img = Image.new("RGBA", (box_w + 24, box_h + 24), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([14, 16, box_w + 14, box_h + 16], radius=int(14 * scale), fill=(0, 0, 0, 110))  # ombra
    d.rounded_rectangle([6, 6, box_w + 6, box_h + 6], radius=int(14 * scale), fill=color + (255,), outline=(255, 255, 255, 255), width=max(3, int(5 * scale)))
    d.text((6 + box_w / 2, 6 + box_h / 2), text, font=fnt, fill=(255, 255, 255), anchor="mm", stroke_width=max(2, int(3 * scale)), stroke_fill=(0, 0, 0, 120))
    return img.rotate(4, resample=Image.BICUBIC, expand=True)


def render_overlay(state, path):
    """Livello trasparente 1080x1920 per un istante del video.

    state = (parole del blocco, indice della parola pronunciata, etichetta, colore, scala, intestazione, cartello finale)
    """
    global _SHADE
    words, hi, sticker, color, scale, header, endcard = state
    if _SHADE is None:
        _SHADE = _bottom_shade()
    img = _SHADE.copy()
    draw = ImageDraw.Draw(img)
    if endcard:
        draw.rectangle([0, 0, W, H], fill=(8, 8, 12, 175))
        draw.text((W / 2, H * 0.36), "CONTINUA", font=font_black(150), fill=KARAOKE, anchor="mm", stroke_width=8, stroke_fill=(0, 0, 0))
        draw.text((W / 2, H * 0.45), endcard, font=font_black(70), fill=(255, 255, 255), anchor="mm", stroke_width=6, stroke_fill=(0, 0, 0))
    if header and not endcard:
        draw.text((W / 2, H * 0.03), header, font=font_black(40), fill=(255, 255, 255), anchor="mm", stroke_width=5, stroke_fill=(0, 0, 0))
    if sticker and not endcard:
        st = _sticker(sticker, color, scale)
        img.alpha_composite(st, (int(W / 2 - st.width / 2), int(H * 0.085 - st.height / 2)))  # in alto, sopra le teste
    if words:
        fnt, size, lines = _caption_font(list(words), draw)
        line_h = int(size * 1.18)
        bottom = int(H * 0.78)  # sopra la zona dei pulsanti di TikTok
        top = bottom - line_h * len(lines)
        stroke = max(6, size // 9)
        space = draw.textlength(" ", font=fnt)
        index = 0
        for row, line in enumerate(lines):
            width = draw.textlength(" ".join(line), font=fnt)
            x = W / 2 - width / 2
            y = top + row * line_h
            for w in line:
                draw.text((x + 4, y + 6), w, font=fnt, fill=(0, 0, 0, 150), stroke_width=stroke, stroke_fill=(0, 0, 0, 150))
                draw.text((x, y), w, font=fnt, fill=KARAOKE if index == hi else WHITE, stroke_width=stroke, stroke_fill=(0, 0, 0))
                x += draw.textlength(w, font=fnt) + space
                index += 1
    img.save(path, "PNG", compress_level=1)


def render_caption(text, header, path):
    """Compatibilità: un blocco di sottotitolo senza parola evidenziata."""
    render_overlay((tuple(text.split()), -1, "", None, 1.0, header, None), path)


def background_still(path):
    """Sfondo neutro usato quando non ci sono immagini."""
    _background().save(path, "JPEG", quality=92)
    return path


def camera_filter(frames, variant):
    """Movimento di camera per un'inquadratura: scatto iniziale (punch-in) e poi spinta, allontanamento o panoramica."""
    punch = "if(lt(on,7),0.09*(1-on/7),0)"
    move = variant % 4
    if move == 0:      # spinta lenta in avanti
        z, x = f"1.02+0.07*on/{frames}+{punch}", "(iw-iw/zoom)/2"
    elif move == 1:    # panoramica verso destra
        z, x = f"1.10+{punch}", f"(iw-iw/zoom)*(0.15+0.7*on/{frames})"
    elif move == 2:    # allontanamento lento
        z, x = f"1.10-0.07*on/{frames}+{punch}", "(iw-iw/zoom)/2"
    else:              # panoramica verso sinistra
        z, x = f"1.10+{punch}", f"(iw-iw/zoom)*(0.85-0.7*on/{frames})"
    y = "(ih-ih/zoom)*0.45"
    return (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
            f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={FPS},setsar=1,format=yuv420p")


async def render_shot(source, kind, duration, output, variant=0):
    """Una inquadratura 1080x1920 a 30 fps senza audio: video ritagliato in verticale o disegno con movimento di camera."""
    frames = max(1, int(round(duration * FPS)))
    if kind == "video":
        vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1,format=yuv420p"
        args = ["-stream_loop", "-1", "-i", source, "-frames:v", str(frames), "-an", "-vf", vf]
    else:
        args = ["-loop", "1", "-framerate", str(FPS), "-i", source, "-frames:v", str(frames), "-vf", camera_filter(frames, variant)]
    await run_ffmpeg([*args, "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", output])


def word_timeline(beat_texts, durations, max_seconds):
    """Blocchi di sottotitolo e tempi di ogni parola (relativi all'inizio della parte).

    Restituisce (blocchi [(parole, inizio, fine)], parole [(inizio, indice_blocco, indice_parola)], inizi dei beat).
    """
    chunks, words, starts, t = [], [], [], 0.0
    for text, dur in zip(beat_texts, durations):
        starts.append(t)
        for chunk, secs in caption_chunks(text, dur, max_seconds):
            ws = chunk.split()
            speech = secs - (GAP_SECONDS if secs > GAP_SECONDS + 0.3 and chunk == text.split()[-1] else 0)
            weights = [len(w) + 2 for w in ws]
            total = sum(weights)
            ct = t
            for k, wt in enumerate(weights):
                words.append((ct, len(chunks), k))
                ct += speech * wt / total
            chunks.append((tuple(ws), t, t + secs))
            t += secs
    return chunks, words, starts


def overlay_timeline(chunks, words, beat_starts, durations, stickers, total, header, endcard):
    """Istanti in cui il livello di testo cambia e lo stato in ciascun intervallo: [(inizio, durata, stato)]."""
    marks = {0.0}
    for ws, a, b in chunks:
        marks.update((a, b))
    marks.update(w[0] for w in words)
    sticker_spans = []
    for i, (bs, dur) in enumerate(zip(beat_starts, durations)):
        text = ((stickers[i] if i < len(stickers) else "") or "").strip().strip("\"'«»“”").strip()
        if text and dur > 1.2:
            s0 = bs + 0.15
            s_end = min(bs + dur - 0.05, s0 + 2.6)
            sticker_spans.append((s0, s_end, text, STICKER_COLORS[i % len(STICKER_COLORS)]))
            marks.update((s0, s0 + 0.07, s0 + 0.14, s_end))
    if header:
        marks.add(min(3.0, total))
    end_start = total - 2.2 if endcard and total > 6 else None
    if end_start is not None:
        marks.add(end_start)
    marks = sorted(m for m in marks if 0 <= m < total - 0.01)
    starts = [w[0] for w in words]
    out = []
    for j, m in enumerate(marks):
        nxt = marks[j + 1] if j + 1 < len(marks) else total
        if nxt - m < 0.005:
            continue
        k = bisect.bisect_right(starts, m + 1e-6) - 1
        cw, hi = (), -1
        if k >= 0:
            _, ci, wi = words[k]
            ws, ca, cb = chunks[ci]
            if ca - 1e-6 <= m < cb:
                cw, hi = ws, wi
        st, color, scale = "", None, 1.0
        for s0, s_end, text, col in sticker_spans:
            if s0 - 1e-6 <= m < s_end:
                st, color = text, col
                scale = 0.7 if m < s0 + 0.07 - 1e-6 else 1.14 if m < s0 + 0.14 - 1e-6 else 1.0
        state = (cw, hi, st, color, scale, header if header and m < 3.0 else "", endcard if end_start is not None and m >= end_start - 1e-6 else None)
        out.append((m, nxt - m, state))
    return out


# ---------------------------------------------------------------- render
class VideoService:
    def __init__(self, log, music=0.25, sfx=True):
        self.log = log  # callable(msg, progress=None)
        self.music = max(0.0, min(1.0, float(music or 0)))  # volume della musica rispetto alla voce
        self.sfx = bool(sfx)

    async def render_part(self, part_no, parts_total, title, beat_audio, beat_texts, work, output, cut_seconds, offset,
                          beat_shots=None, stickers=None, cast=None):
        """Crea la clip TikTok della parte (con "Parte N" e cartello finale) e una versione pulita per il video lungo.

        beat_shots: per ogni beat una lista di (file, 'video'|'photo'[, secondi]); stickers: etichetta per beat.
        Restituisce (durata, voci SRT, percorso della versione pulita).
        """
        frames_dir = work / f"frames_{part_no:02}"
        if frames_dir.exists():
            shutil.rmtree(frames_dir)
        frames_dir.mkdir(parents=True)
        durations = [wav_duration(p) for p in beat_audio]
        part_wav = work / f"part_{part_no:02}.wav"
        concat_wavs(beat_audio, part_wav)
        total = sum(durations)
        stickers = stickers or [""] * len(durations)

        beat_shots = beat_shots or [[] for _ in durations]
        bg = work / f"bg_{part_no:02}.mp4"
        animated = any(isinstance(s, dict) for row in beat_shots for s in row)
        if animated:
            # cartone animato: sfondi in movimento + personaggi disegnati e animati (toon.py), con la bocca a tempo con la voce
            from app.services import toon
            shots = []
            for dur, row in zip(durations, beat_shots):
                given = [s["secs"] for s in row]
                scale = dur / sum(given) if row and sum(given) > 0 else 1.0
                shots += [{**s, "secs": s["secs"] * scale} for s in row] or [{"bg": None, "secs": dur, "chars": []}]
            self.log(f"Parte {part_no}: animazione di {len(shots)} inquadrature…")
            await asyncio.to_thread(toon.render_picture, shots, part_wav, bg, cast or [], ffmpeg_exe(), self.log)
        # 1. sfondo: inquadrature con movimento di camera, codificate in parallelo
        still = None
        jobs = []
        for b, (dur, shots) in enumerate(zip(durations, [] if animated else beat_shots)):
            shots = [s if len(s) == 3 else (s[0], s[1], None) for s in shots]
            usable, last = [], None
            for src, kind, secs in shots:  # un disegno mancante prende il posto del precedente
                if Path(src).is_file():
                    last = (src, kind)
                usable.append((last or (None, None), secs))
            if not shots or all(u[0][0] is None for u in usable):
                still = still or background_still(frames_dir / "still.jpg")
                usable = [((still, "photo"), dur)]
            first = next(u[0] for u in usable if u[0][0] is not None)
            given = [secs for _, secs in usable]
            if any(s is None for s in given) or abs(sum(given) - dur) > 0.05:
                given = [dur / len(usable)] * len(usable)
            for s, ((src, kind), secs) in enumerate(zip([u[0] if u[0][0] else first for u in usable], given)):
                jobs.append((src, kind, secs, frames_dir / f"shot_{b:03}_{s:02}.mp4", len(jobs)))
        sem = asyncio.Semaphore(max(1, min(6, (os.cpu_count() or 4) // 4)))
        done = [0]

        async def one(src, kind, dur, out, variant):
            async with sem:
                try:
                    await render_shot(src, kind, dur, out, variant)
                except RuntimeError:  # file danneggiato: sfondo neutro
                    await render_shot(background_still(frames_dir / f"still_{variant}.jpg"), "photo", dur, out, variant)
                done[0] += 1
                if done[0] % 8 == 0 or done[0] == len(jobs):
                    self.log(f"Parte {part_no}: inquadrature {done[0]}/{len(jobs)}")

        if not animated:
            await asyncio.gather(*(one(*j) for j in jobs))
            bg_list = work / f"bg_{part_no:02}.txt"
            bg_list.write_text("".join(f"file '{j[3].resolve().as_posix()}'\n" for j in jobs), encoding="utf-8")
            await run_ffmpeg(["-f", "concat", "-safe", "0", "-i", bg_list, "-c", "copy", bg])

        # 2. testo: sottotitoli karaoke, etichette, "Parte N" e cartello finale
        chunks, words, beat_starts = word_timeline(beat_texts, durations, cut_seconds)
        entries = [(offset + a, offset + b - (GAP_SECONDS if b - a > GAP_SECONDS + 0.3 else 0), " ".join(ws)) for ws, a, b in chunks]
        header = f"PARTE {part_no}" if parts_total > 1 else ""
        endcard = f"PARTE {part_no + 1} NEL PROFILO" if part_no < parts_total else None
        rendered = {}

        def overlay_list(name, timeline):
            lines = []
            for _, secs, state in timeline:
                if state not in rendered:
                    path = frames_dir / f"ov_{len(rendered):05}.png"
                    render_overlay(state, path)
                    rendered[state] = path
                lines += [f"file '{rendered[state].resolve().as_posix()}'", f"duration {secs:.3f}"]
            lines.append(lines[-2])
            listing = work / f"{name}_{part_no:02}.txt"
            listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return listing

        clip_list = overlay_list("ovclip", overlay_timeline(chunks, words, beat_starts, durations, stickers, total, header, endcard))
        clean_list = overlay_list("ovclean", overlay_timeline(chunks, words, beat_starts, durations, stickers, total, "", None))

        # 3. audio: voce + musica + effetti (cambi d'inquadratura, etichette, sting sul cliffhanger della clip)
        from app.services import audio_fx
        if animated:
            secs_list = [s["secs"] for s in shots]
        else:
            secs_list = [j[2] for j in jobs]
        cuts = [0.0]
        for sec in secs_list[:-1]:
            cuts.append(cuts[-1] + sec)
        sticker_times = [bs + 0.15 for bs, dur, st in zip(beat_starts, durations, stickers) if (st or "").strip() and dur > 1.2]
        sting_at = total - 2.2 if endcard and total > 6 else None
        clip_audio, clean_audio = work / f"mix_clip_{part_no:02}.wav", work / f"mix_clean_{part_no:02}.wav"
        if self.music > 0 or self.sfx:
            await asyncio.to_thread(audio_fx.mix_part, part_wav, clip_audio, cuts, sticker_times, sting_at, self.music, self.sfx, part_no)
            await asyncio.to_thread(audio_fx.mix_part, part_wav, clean_audio, cuts, sticker_times, None, self.music, self.sfx, part_no)
        else:
            clip_audio = clean_audio = part_wav

        # 4. composizione: clip TikTok e versione pulita per il video lungo
        output.parent.mkdir(parents=True, exist_ok=True)
        clean = work / f"clean_{part_no:02}.mp4"
        for listing, target, audio in ((clip_list, output, clip_audio), (clean_list, clean, clean_audio)):
            await run_ffmpeg([
                "-i", bg, "-f", "concat", "-safe", "0", "-i", listing, "-i", audio,
                "-filter_complex",
                f"[0:v]tpad=stop_mode=clone:stop_duration=3[bg];[1:v]fps={FPS},format=rgba[cap];"
                f"[bg][cap]overlay=0:0:format=auto:eof_action=repeat,scale=in_range=pc:out_range=tv,format=yuv420p[v]",
                "-map", "[v]", "-map", "2:a",
                "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", str(FPS),
                "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "44100", "-c:a", "aac", "-b:a", "192k",
                "-t", f"{total:.3f}", "-movflags", "+faststart", target,
            ])
        shutil.rmtree(frames_dir, ignore_errors=True)
        return total, entries, clean

    @staticmethod
    async def concat_videos(paths, output, work):
        listing = work / "parts.txt"
        listing.write_text("".join(f"file '{Path(p).resolve().as_posix()}'\n" for p in paths), encoding="utf-8")
        await run_ffmpeg(["-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", "-movflags", "+faststart", output])

    @staticmethod
    def write_srt(entries, path):
        blocks = [f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{text}\n" for i, (a, b, text) in enumerate(entries, 1)]
        path.write_text("\n".join(blocks), encoding="utf-8")
