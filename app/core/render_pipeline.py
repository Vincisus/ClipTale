"""Voce + video: dai beat salvati a render/tiktok/part_XX.mp4, render/full_video.mp4 e render/subtitles.srt."""
import hashlib
import json
import shutil
from datetime import datetime

from app.core.projects import load_json, save_json
from app.services.tts_service import TTSService
from app.services.video_service import VideoService, clean_beat_audio, wav_duration


async def render_project(folder, job, voice, wpm, cut_seconds, silence_ms):
    beats = load_json(folder / "beats.json", [])
    beats = [b for b in beats if str(b.get("text", "")).strip()]
    if not beats:
        raise ValueError("Nessun beat da renderizzare: genera o scrivi prima il copione.")
    structure = load_json(folder / "structure.json", {}) or {}
    titles = {p.get("part"): p.get("title", "") for p in structure.get("parts", [])}
    concept = load_json(folder / "concept.json", {}) or {}
    render = folder / "render"
    audio_dir, work = render / "audio", render / "_work"
    audio_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    def log(msg, progress=None, module="compositor", level="info"):
        job.log(msg, module=module, progress=progress, level=level)

    # 1. voce, con cache per beat (testo + voce + velocita)
    tts = TTSService(voice, wpm)
    index = load_json(audio_dir / "index.json", {}) or {}
    clean_paths, todo = [], []
    for i, beat in enumerate(beats, 1):
        key = hashlib.sha1(json.dumps([beat["text"], tts.voice, int(wpm), int(silence_ms)], ensure_ascii=False).encode()).hexdigest()
        clean = audio_dir / f"beat_{i:03}_clean.wav"
        clean_paths.append(clean)
        if index.get(str(i)) != key or not clean.is_file():
            todo.append((i, beat["text"], key))
    log(f"Voce {tts.voice} a {wpm} WPM: {len(todo)} beat da sintetizzare, {len(beats) - len(todo)} dalla cache", 3, module="tts_engine")
    if todo:
        raw = await tts.synthesize_many([(text, audio_dir / f"beat_{i:03}_raw") for i, text, _ in todo], on_progress=lambda m: log(m, module="tts_engine"))
        for (i, _, key), source in zip(todo, raw):
            duration = await clean_beat_audio(source, clean_paths[i - 1], silence_ms)
            source.unlink(missing_ok=True)
            index[str(i)] = key
            save_json(audio_dir / "index.json", index)
            log(f"Beat {i}: {duration:.1f}s di audio", 3 + 37 * len([k for k in index]) / len(beats), module="tts_engine")
    durations = [wav_duration(p) for p in clean_paths]
    log(f"Audio pronto: {sum(durations) / 60:.1f} minuti totali", 40, module="tts_engine", level="ok")

    # 2. video per parte (= clip TikTok) e video completo
    video = VideoService(log)
    parts = sorted({b["part"] for b in beats})
    tiktok = render / "tiktok"
    if tiktok.exists():
        shutil.rmtree(tiktok)
    clips, entries, offset, timings = [], [], 0.0, []
    for n, part in enumerate(parts, 1):
        idx = [i for i, b in enumerate(beats) if b["part"] == part]
        title = titles.get(part) or concept.get("title", "")
        log(f"Render parte {n}/{len(parts)} ({len(idx)} beat)…", 40 + 50 * (n - 1) / len(parts))
        out = tiktok / f"part_{n:02}.mp4"
        total, part_entries = await video.render_part(n, len(parts), title, [clean_paths[i] for i in idx], [beats[i]["text"] for i in idx], work, out, cut_seconds, offset)
        t = offset
        for i in idx:
            timings.append({"beat": i, "part": part, "start": round(t, 2), "end": round(t + durations[i], 2)})
            t += durations[i]
        entries += part_entries
        offset += total
        clips.append(out)
        log(f"Parte {n}: {total:.1f}s → {out.relative_to(folder).as_posix()}", 40 + 50 * n / len(parts), level="ok")
    full = render / "full_video.mp4"
    if len(clips) == 1:
        shutil.copyfile(clips[0], full)
    else:
        log("Unione delle parti nel video completo…", 92)
        await video.concat_videos(clips, full, work)
    video.write_srt(entries, render / "subtitles.srt")
    shutil.rmtree(work, ignore_errors=True)
    save_json(render / "render.json", {
        "rendered_at": datetime.now().isoformat(timespec="seconds"),
        "voice": tts.voice, "wpm": wpm, "cut_seconds": cut_seconds, "silence_ms": silence_ms,
        "duration_seconds": round(offset, 2), "beats": timings,
        "clips": [c.relative_to(folder).as_posix() for c in clips],
    })
    log(f"Video completo: {offset / 60:.1f} min, {len(clips)} clip TikTok, sottotitoli SRT", 99, level="ok")
