"""Musica di sottofondo ed effetti sonori sintetizzati (niente file da scaricare, niente diritti d'autore).

- musica: un giro di accordi pizzicati con basso morbido e un leggero ritmo, in loop per tutta la parte;
- effetti: "whoosh" a ogni cambio d'inquadratura, "pop" quando entra un'etichetta, "sting" (colpo basso + nota acuta)
  sul cliffhanger di fine parte;
- livelli: la musica sta a una frazione fissa del volume della voce (RMS sulle parti parlate), gli effetti hanno i picchi
  limitati sotto quelli della voce, così non coprono mai la narrazione.
"""
import math
import wave
from pathlib import Path

import numpy as np

RATE = 44100


def read_wav(path):
    with wave.open(str(path), "rb") as w:
        rate, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
        data = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if rate != RATE:  # ricampionamento lineare
        x = np.linspace(0, len(data) - 1, int(len(data) * RATE / rate))
        data = np.interp(x, np.arange(len(data)), data).astype(np.float32)
    return data


def write_wav(path, data):
    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def gated_rms(x, gate_db=-40):
    """RMS delle sole parti con suono (i silenzi non abbassano la media)."""
    hop = RATE // 20
    blocks = [x[i:i + hop] for i in range(0, len(x) - hop, hop)]
    levels = np.array([np.sqrt(np.mean(b ** 2)) for b in blocks]) if blocks else np.array([0.0])
    gate = 10 ** (gate_db / 20)
    voiced = levels[levels > gate]
    return float(np.sqrt(np.mean(voiced ** 2))) if len(voiced) else 1e-4


def _note(freq, dur, kind="pluck"):
    t = np.arange(int(dur * RATE)) / RATE
    if kind == "pluck":
        env = np.exp(-t * 4.5)
        tone = np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(4 * np.pi * freq * t) + 0.12 * np.sin(6 * np.pi * freq * t)
    elif kind == "bass":
        env = np.exp(-t * 2.2)
        tone = np.sin(2 * np.pi * freq * t) + 0.2 * np.sin(4 * np.pi * freq * t)
    else:  # pad
        env = np.minimum(1, t / 0.3) * np.exp(-t * 0.6)
        tone = np.sin(2 * np.pi * freq * t) + 0.5 * np.sin(2 * np.pi * freq * 1.005 * t)
    return (tone * env).astype(np.float32)


def music_loop(seconds, bpm=96, seed=0):
    """Giro Am - F - C - G (o variante) pizzicato, con basso e piccolo ritmo; tono leggermente teso, da racconto."""
    rng = np.random.default_rng(seed)
    beat = 60 / bpm
    progressions = [[57, 53, 48, 55], [57, 52, 53, 55], [50, 53, 57, 55]]
    prog = progressions[seed % len(progressions)]
    bar = 4 * beat
    total = np.zeros(int((seconds + bar) * RATE), dtype=np.float32)
    n_bars = int(seconds / bar) + 1
    for b in range(n_bars):
        root = prog[b % 4]
        minor = (b % 4) in (0,) or root in (57, 52)
        chord = [root, root + (3 if minor else 4), root + 7]
        start = int(b * bar * RATE)
        pad = sum(_note(440 * 2 ** ((m - 69) / 12), bar, "pad") for m in chord) * 0.12
        total[start:start + len(pad)] += pad[: len(total) - start]
        bass = _note(440 * 2 ** ((root - 12 - 69) / 12), beat * 2, "bass") * 0.45
        for k in (0, 2):
            s = start + int(k * beat * RATE)
            total[s:s + len(bass)] += bass[: max(0, len(total) - s)]
        arp = [chord[0] + 12, chord[1] + 12, chord[2] + 12, chord[1] + 12]
        for k in range(8):
            m = arp[k % 4] + (12 if rng.random() < 0.15 else 0)
            p = _note(440 * 2 ** ((m - 69) / 12), beat * 0.9, "pluck") * 0.22
            s = start + int(k * beat / 2 * RATE)
            total[s:s + len(p)] += p[: max(0, len(total) - s)]
        for k in range(8):  # charleston leggero
            s = start + int(k * beat / 2 * RATE)
            n = int(0.03 * RATE)
            tick = rng.standard_normal(n).astype(np.float32) * np.exp(-np.arange(n) / RATE * 160) * (0.05 if k % 2 else 0.03)
            total[s:s + n] += tick[: max(0, len(total) - s)]
    total = total[: int(seconds * RATE)]
    fade = int(0.8 * RATE)
    if len(total) > 2 * fade:
        total[:fade] *= np.linspace(0, 1, fade)
        total[-fade:] *= np.linspace(1, 0, fade)
    return total


def whoosh(dur=0.35):
    n = int(dur * RATE)
    t = np.arange(n) / RATE
    noise = np.random.default_rng(1).standard_normal(n).astype(np.float32)
    # filtro passa-basso che si apre e si chiude: media mobile di lunghezza variabile
    out = np.zeros(n, dtype=np.float32)
    acc = 0.0
    for i in range(n):
        a = 0.02 + 0.25 * math.sin(math.pi * i / n)
        acc += a * (noise[i] - acc)
        out[i] = acc
    return out * np.sin(np.pi * t / dur) ** 2 * 1.6


def pop(dur=0.12):
    t = np.arange(int(dur * RATE)) / RATE
    f = 900 * np.exp(-t * 18) + 300
    return (np.sin(2 * np.pi * np.cumsum(f) / RATE) * np.exp(-t * 30)).astype(np.float32)


def sting(dur=1.6):
    t = np.arange(int(dur * RATE)) / RATE
    boom = np.sin(2 * np.pi * 55 * t) * np.exp(-t * 2.5)
    hit = (np.sin(2 * np.pi * 880 * t) + 0.5 * np.sin(2 * np.pi * 1320 * t)) * np.exp(-t * 4) * 0.4
    return (boom + hit).astype(np.float32)


def mix_part(voice_wav, output, cuts, sticker_times, end_sting_at=None, music_level=0.25, sfx=True, seed=0):
    """Voce + musica + effetti in un solo WAV. music_level = volume della musica rispetto alla voce (0 = niente)."""
    voice = read_wav(voice_wav)
    n = len(voice)
    v_rms = gated_rms(voice)
    v_peak = float(np.max(np.abs(voice))) or 1.0
    bed = np.zeros(n, dtype=np.float32)
    if music_level > 0:
        m = music_loop(n / RATE, seed=seed)
        m = m[:n] if len(m) >= n else np.pad(m, (0, n - len(m)))
        bed += m * (v_rms * music_level / max(1e-6, gated_rms(m)))
        if end_sting_at is not None:  # la musica si ferma sul cliffhanger
            s = int(end_sting_at * RATE)
            bed[s:] *= np.linspace(1, 0, n - s) ** 3 if n > s else 1
    if sfx:
        fx = np.zeros(n, dtype=np.float32)

        def place(sound, at, gain):
            s = int(at * RATE)
            if 0 <= s < n:
                seg = sound[: n - s] * gain
                fx[s:s + len(seg)] += seg

        for t in cuts[1:]:
            place(whoosh(), max(0, t - 0.12), 0.6)
        for t in sticker_times:
            place(pop(), t, 0.8)
        if end_sting_at is not None:
            place(sting(), end_sting_at, 1.0)
        peak = float(np.max(np.abs(fx))) or 1.0
        fx *= min(1.0, 0.5 * v_peak / peak)  # i picchi degli effetti restano a metà di quelli della voce
        bed += fx
    out = voice + bed
    peak = float(np.max(np.abs(out)))
    if peak > 0.98:
        out *= 0.98 / peak
    write_wav(output, out)
    return output
