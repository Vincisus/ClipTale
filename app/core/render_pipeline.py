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


async def make_toon(folder, beats, durations, concept, model, log):
    """Cartone animato: regia (LM Studio) -> sfondi vuoti (Stable Diffusion) -> inquadrature con personaggi animati."""
    def ilog(msg, progress=None, level="info"):
        log(msg, progress, module="illustrator", level=level)

    scenes_dir = folder / "render" / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    moments = [illustration_service.beat_moments(b["text"], d) for b, d in zip(beats, durations)]
    cache_key = hashlib.sha1(json.dumps([[b["text"], len(m)] for b, m in zip(beats, moments)], ensure_ascii=False).encode()).hexdigest()
    cache = load_json(scenes_dir / "toon_plan.json", {}) or {}
    llm = LLMService(model=model or None)
    total = sum(len(m) for m in moments)
    if cache.get("key") == cache_key:
        cast, places, plan, stickers = cache["cast"], cache["places"], cache["shots"], cache["stickers"]
        ilog("Regia del cartone dalla cache", 42)
    else:
        ilog(f"LM Studio fa la regia: personaggi, luoghi e {total} inquadrature (una ogni ~{illustration_service.SHOT_SECONDS:g} s)…", 42)
        cast, places, plan, stickers = await illustration_service.toon_plan(llm, beats, concept, moments)
        save_json(scenes_dir / "toon_plan.json", {"key": cache_key, "cast": cast, "places": places, "shots": plan, "stickers": stickers})
    for c in cast:
        extra = ", ".join(k for k in ("glasses", "tie", "beard", "necklace") if c.get(k))
        ilog(f"Personaggio {c['name']}: {c['gender']}, {c['age']}, capelli {c['hair_style']} {c['hair_color']}, vestito {c['outfit_color']}{', ' + extra if extra else ''}")
    seed_base = int(hashlib.sha1(folder.name.encode()).hexdigest()[:6], 16)
    items, bg_files = illustration_service.toon_background_items(places, folder, seed_base)
    missing = sum(1 for i in items if not Path(i["out"]).is_file())
    ilog(f"{len(places)} luoghi da disegnare come sfondi: {missing} nuovi, {len(items) - missing} dalla cache", 44)
    reload_models = []
    if missing:
        reload_models = await illustration_service.free_gpu(llm)

        def on_line(line):
            if line.startswith("PROGRESS"):
                parts = line.split()
                ilog(f"Sfondo {parts[1]}/{parts[2]}", 44 + 14 * int(parts[1]) / max(1, int(parts[2])))
            elif line.startswith("STATUS"):
                ilog(line[7:])
        try:
            await illustration_service.draw(items, folder, on_line, style="anim")
        except asyncio.CancelledError:
            await illustration_service.restore_gpu(reload_models)
            raise
        except Exception as exc:
            ilog(f"{exc}. I luoghi mancanti avranno uno sfondo neutro.", level="error")
    beat_shots = []
    for beat_moments_, beat_plan in zip(moments, plan):
        row = []
        for (_, secs), shot in zip(beat_moments_, beat_plan):
            row.append({**shot, "bg": str(bg_files[shot["place"]]), "secs": secs})
        beat_shots.append(row)
    ilog(f"Cartone pronto: {len(cast)} personaggi, {len(places)} luoghi, {total} inquadrature", 58, level="ok")
    return beat_shots, stickers, reload_models, cast


async def make_illustrations(folder, beats, durations, concept, style, model, log):
    """Momenti del parlato -> descrizioni (LM Studio) -> disegni (Stable Diffusion).

    Restituisce (per beat la lista di (file, 'photo', secondi), etichette per beat, modelli da ricaricare).
    """
    def ilog(msg, progress=None, level="info"):
        log(msg, progress, module="illustrator", level=level)

    scenes_dir = folder / "render" / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    moments = [illustration_service.beat_moments(b["text"], d) for b, d in zip(beats, durations)]
    cache_key = hashlib.sha1(json.dumps([[b["text"], len(m)] for b, m in zip(beats, moments)], ensure_ascii=False).encode()).hexdigest()
    cache = load_json(scenes_dir / "prompts.json", {}) or {}
    llm = LLMService(model=model or None)
    total_moments = sum(len(m) for m in moments)
    if cache.get("key") == cache_key and len(cache.get("shots", [])) == len(beats):
        shots, stickers = cache["shots"], cache.get("stickers") or [""] * len(beats)
        ilog("Inquadrature dalla cache", 42)
    else:
        ilog(f"LM Studio descrive {total_moments} inquadrature (una ogni ~{illustration_service.SHOT_SECONDS:g} s) e l'aspetto dei personaggi…", 42)
        try:
            looks, shots, stickers = await illustration_service.scene_prompts(llm, beats, concept, moments)
        except Exception as exc:
            ilog(f"Descrizioni non riuscite ({exc}): uso i visual cue del copione", level="warn")
            shots = [[f"{b.get('cue') or b['text'][:160]}, {concept.get('setting', '')}"] * len(m) for b, m in zip(beats, moments)]
            stickers = [""] * len(beats)
        else:
            save_json(scenes_dir / "prompts.json", {"key": cache_key, "characters": looks, "shots": shots, "stickers": stickers})
            for c in looks:
                ilog(f"Personaggio {c['name']}: {c['look']}")
    seed_base = int(hashlib.sha1(folder.name.encode()).hexdigest()[:6], 16)
    items, beat_shots = illustration_service.build_items(beats, moments, shots, style, folder, seed_base)
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
            await illustration_service.draw(items, folder, on_line, style=style)
        except asyncio.CancelledError:
            await illustration_service.restore_gpu(reload_models)
            raise
        except Exception as exc:
            ilog(f"{exc}. Le inquadrature mancanti avranno uno sfondo scuro.", level="error")
    ready = sum(1 for row in beat_shots for p, _, _ in row if Path(p).is_file())
    ilog(f"Illustrazioni pronte: {ready}/{len(items)}", 58, level="ok")
    return beat_shots, stickers, reload_models


async def render_project(folder, job, voice, wpm, cut_seconds, silence_ms, visuals="ai", model=None, style="anim", music=0.25, sfx=True):
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
    from app.services.text_norm import narrator_gender
    gender = narrator_gender(" ".join(b["text"] for b in beats), concept.get("narrator_gender"))
    tts = TTSService(voice, wpm, gender)
    await tts.prepare()
    if not voice or voice == "auto":
        who = {"F": "una donna", "M": "un uomo"}.get(gender, "non chiaro (uso una voce femminile)")
        log(f"Chi racconta: {who} → voce {tts.voice}", 2, module="tts_engine")
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
    stickers = [""] * len(beats)
    reload_models = []
    cast = []
    if visuals == "none":
        log("Illustrazioni disattivate: sfondo scuro e sottotitoli", 41, module="illustrator")
    elif not illustration_service.available():
        log("Illustrazioni non installate: esegui installa_disegni.bat. Per ora uso uno sfondo scuro.", 41, module="illustrator", level="warn")
    elif illustration_service.STYLES.get(style, {}).get("animated"):
        beat_shots, stickers, reload_models, cast = await make_toon(folder, beats, durations, concept, model, log)
    else:
        beat_shots, stickers, reload_models = await make_illustrations(folder, beats, durations, concept, style, model, log)
    reload_task = asyncio.create_task(illustration_service.restore_gpu(reload_models)) if reload_models else None

    # 3. video per parte (= clip TikTok) e video completo
    video = VideoService(log, music, sfx)
    parts = sorted({b["part"] for b in beats})
    tiktok = render / "tiktok"
    if tiktok.exists():
        shutil.rmtree(tiktok)
    clips, cleans, entries, offset, timings = [], [], [], 0.0, []
    for n, part in enumerate(parts, 1):
        idx = [i for i, b in enumerate(beats) if b["part"] == part]
        title = titles.get(part) or concept.get("title", "")
        log(f"Render parte {n}/{len(parts)} ({len(idx)} beat)…", 60 + 32 * (n - 1) / len(parts))
        out = tiktok / f"part_{n:02}.mp4"
        total, part_entries, clean = await video.render_part(
            n, len(parts), title, [clean_paths[i] for i in idx], [beats[i]["text"] for i in idx], work, out, cut_seconds, offset,
            [beat_shots[i] for i in idx], [stickers[i] for i in idx], cast)
        cleans.append(clean)
        t = offset
        for i in idx:
            timings.append({"beat": i, "part": part, "start": round(t, 2), "end": round(t + durations[i], 2)})
            t += durations[i]
        entries += part_entries
        offset += total
        clips.append(out)
        log(f"Parte {n}: {total:.1f}s → {out.relative_to(folder).as_posix()}", 60 + 32 * n / len(parts), level="ok")
    # il video lungo usa le versioni "pulite" delle parti: senza "Parte N" e senza il cartello di fine parte
    full = render / "full_video.mp4"
    if len(cleans) == 1:
        shutil.copyfile(cleans[0], full)
    else:
        log("Unione delle parti nel video completo…", 92)
        await video.concat_videos(cleans, full, work)
    video.write_srt(entries, render / "subtitles.srt")
    (render / "crediti_immagini.txt").unlink(missing_ok=True)  # file delle vecchie versioni con foto stock
    shutil.rmtree(work, ignore_errors=True)
    save_json(render / "render.json", {
        "rendered_at": datetime.now().isoformat(timespec="seconds"),
        "voice": tts.voice, "wpm": wpm, "visuals": visuals, "style": style, "illustrated": sum(1 for s in beat_shots if s),
        "scenes": [[Path(s["bg"] if isinstance(s, dict) else s[0]).relative_to(folder.resolve() if Path(s["bg"] if isinstance(s, dict) else s[0]).is_absolute() else folder).as_posix()
                    for s in shots if Path(s["bg"] if isinstance(s, dict) else s[0]).is_file()][:1] for shots in beat_shots], "stickers": stickers, "cut_seconds": cut_seconds, "silence_ms": silence_ms,
        "duration_seconds": round(offset, 2), "beats": timings,
        "clips": [c.relative_to(folder).as_posix() for c in clips],
    })
    if reload_task:
        log("Ricarico il modello di testo in LM Studio…", 98, module="illustrator")
        await reload_task
    log(f"Video completo: {offset / 60:.1f} min, {len(clips)} clip TikTok, sottotitoli SRT", 99, level="ok")
