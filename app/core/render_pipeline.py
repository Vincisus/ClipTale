"""Voce + video: dai beat salvati a render/tiktok/part_XX.mp4, render/full_video.mp4 e render/subtitles.srt."""
import asyncio
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from app.core.projects import load_json, save_json
from app.services import illustration_service
from app.services.llm_service import LLMService
from app.services.tts_service import TTSService
from app.services.video_service import VideoService, clean_beat_audio, wav_duration


async def make_illustrations(folder, beats, durations, concept, style, model, log):
    """Descrizioni delle scene (LM Studio) -> disegni (Stable Diffusion). Restituisce (inquadrature per beat, modelli da ricaricare)."""
    def ilog(msg, progress=None, level="info"):
        log(msg, progress, module="illustrator", level=level)

    scenes_dir = folder / "render" / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.sha1(json.dumps([[b["text"], b.get("cue", "")] for b in beats], ensure_ascii=False).encode()).hexdigest()
    cache = load_json(scenes_dir / "prompts.json", {}) or {}
    llm = LLMService(model=model or None)
    if cache.get("key") == cache_key and len(cache.get("scenes", [])) == len(beats):
        scenes = cache["scenes"]
        ilog("Descrizioni delle scene dalla cache", 42)
    else:
        ilog(f"LM Studio descrive {len(beats)} scene e l'aspetto dei personaggi…", 42)
        try:
            looks, scenes = await illustration_service.scene_prompts(llm, beats, concept)
        except Exception as exc:
            ilog(f"Descrizioni non riuscite ({exc}): uso i visual cue del copione", level="warn")
            looks, scenes = [], [f"{b.get('cue') or b['text'][:160]}, {concept.get('setting', '')}" for b in beats]
        else:
            save_json(scenes_dir / "prompts.json", {"key": cache_key, "characters": looks, "scenes": scenes})
            for c in looks:
                ilog(f"Personaggio {c['name']}: {c['look']}")
    seed_base = int(hashlib.sha1(folder.name.encode()).hexdigest()[:6], 16)
    items, beat_shots = illustration_service.build_items(beats, durations, scenes, style, folder, seed_base)
    missing = sum(1 for i in items if not Path(i["out"]).is_file())
    label = illustration_service.STYLES.get(style, illustration_service.STYLES["cartoon"])["label"]
    ilog(f"{len(items)} illustrazioni in stile {label}: {missing} da disegnare, {len(items) - missing} dalla cache", 44)
    reload_models = []
    if missing:
        reload_models = await illustration_service.free_gpu(llm)
        if reload_models:
            ilog("LM Studio scaricato dalla GPU per far posto al modello di disegno (verrà ricaricato alla fine)")

        def on_line(line):
            if line.startswith("PROGRESS"):
                parts = line.split()
                done, total = int(parts[1]), int(parts[2])
                secs = f" ({parts[3]}s)" if len(parts) > 3 else ""
                ilog(f"Illustrazione {done}/{total}{secs}", 44 + 14 * done / max(1, total))
            elif line.startswith("STATUS"):
                ilog(line[7:])
        try:
            await illustration_service.draw(items, folder, on_line)
        except asyncio.CancelledError:
            await illustration_service.restore_gpu(reload_models)
            raise
        except Exception as exc:
            ilog(f"{exc}. Le scene mancanti avranno uno sfondo scuro.", level="error")
    beat_shots = [[(p, k) for p, k in shots if Path(p).is_file()] for shots in beat_shots]
    ilog(f"Illustrazioni pronte per {sum(1 for s in beat_shots if s)}/{len(beats)} scene", 58, level="ok")
    return beat_shots, reload_models


async def render_project(folder, job, voice, wpm, cut_seconds, silence_ms, visuals="ai", model=None, style="cartoon"):
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

    # 2. illustrazioni disegnate in locale per ogni scena
    beat_shots = [[] for _ in beats]
    reload_models = []
    if visuals == "none":
        log("Illustrazioni disattivate: sfondo scuro e sottotitoli", 41, module="illustrator")
    elif not illustration_service.available():
        log("Illustrazioni non installate: esegui installa_disegni.bat. Per ora uso uno sfondo scuro.", 41, module="illustrator", level="warn")
    else:
        beat_shots, reload_models = await make_illustrations(folder, beats, durations, concept, style, model, log)
    reload_task = asyncio.create_task(illustration_service.restore_gpu(reload_models)) if reload_models else None

    # 3. video per parte (= clip TikTok) e video completo
    video = VideoService(log)
    parts = sorted({b["part"] for b in beats})
    tiktok = render / "tiktok"
    if tiktok.exists():
        shutil.rmtree(tiktok)
    clips, entries, offset, timings = [], [], 0.0, []
    for n, part in enumerate(parts, 1):
        idx = [i for i, b in enumerate(beats) if b["part"] == part]
        title = titles.get(part) or concept.get("title", "")
        log(f"Render parte {n}/{len(parts)} ({len(idx)} beat)…", 60 + 32 * (n - 1) / len(parts))
        out = tiktok / f"part_{n:02}.mp4"
        total, part_entries = await video.render_part(n, len(parts), title, [clean_paths[i] for i in idx], [beats[i]["text"] for i in idx], work, out, cut_seconds, offset, [beat_shots[i] for i in idx])
        t = offset
        for i in idx:
            timings.append({"beat": i, "part": part, "start": round(t, 2), "end": round(t + durations[i], 2)})
            t += durations[i]
        entries += part_entries
        offset += total
        clips.append(out)
        log(f"Parte {n}: {total:.1f}s → {out.relative_to(folder).as_posix()}", 60 + 32 * n / len(parts), level="ok")
    full = render / "full_video.mp4"
    if len(clips) == 1:
        shutil.copyfile(clips[0], full)
    else:
        log("Unione delle parti nel video completo…", 92)
        await video.concat_videos(clips, full, work)
    video.write_srt(entries, render / "subtitles.srt")
    (render / "crediti_immagini.txt").unlink(missing_ok=True)  # file delle vecchie versioni con foto stock
    shutil.rmtree(work, ignore_errors=True)
    save_json(render / "render.json", {
        "rendered_at": datetime.now().isoformat(timespec="seconds"),
        "voice": tts.voice, "wpm": wpm, "visuals": visuals, "style": style, "illustrated": sum(1 for s in beat_shots if s),
        "scenes": [[Path(f).relative_to(folder).as_posix() for f, _ in shots] for shots in beat_shots], "cut_seconds": cut_seconds, "silence_ms": silence_ms,
        "duration_seconds": round(offset, 2), "beats": timings,
        "clips": [c.relative_to(folder).as_posix() for c in clips],
    })
    if reload_task:
        log("Ricarico il modello di testo in LM Studio…", 98, module="illustrator")
        await reload_task
    log(f"Video completo: {offset / 60:.1f} min, {len(clips)} clip TikTok, sottotitoli SRT", 99, level="ok")
