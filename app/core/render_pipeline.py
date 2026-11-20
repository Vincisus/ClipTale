"""Voce + video: dai beat salvati a render/tiktok/part_XX.mp4, render/full_video.mp4 e render/subtitles.srt.

Con più lingue, la principale si crea per prima (voce, disegni, video); le altre usano la loro traduzione
(langs/<codice>/) con la propria voce, e riusano gli stessi disegni: le inquadrature si allungano o accorciano
sulla durata del nuovo audio.
"""
import asyncio
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from app.core import languages
from app.core.projects import load_json, save_json
from app.services import illustration_service
from app.services.llm_service import LLMService
from app.services.tts_service import TTSService
from app.services.video_service import VideoService, clean_beat_audio, wav_duration


async def make_toon(folder, beats, durations, concept, model, log, lang="it"):
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
        cast, places, plan, stickers = await illustration_service.toon_plan(llm, beats, concept, moments, lang)
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
        await illustration_service.wait_for_manual_gpu(llm, lambda m, lvl: ilog(m, level=lvl))

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


async def make_illustrations(folder, beats, durations, concept, style, model, log, lang="it"):
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
            looks, shots, stickers = await illustration_service.scene_prompts(llm, beats, concept, moments, lang)
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
        await illustration_service.wait_for_manual_gpu(llm, lambda m, lvl: ilog(m, level=lvl))
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


async def render_project(folder, job, voice, wpm, cut_seconds, silence_ms, visuals="ai", model=None, style="anim", music=0.25, sfx=True,
                         voices=None, langs=None):
    """Crea il video in tutte le lingue del progetto (o in quelle di langs), la principale per prima.

    voices: {lingua: voce}; voice è la voce della lingua principale (compatibilità con le chiamate di prima).
    """
    folder = Path(folder)
    style = illustration_service.normalize_style(style)
    brief = load_json(folder / "brief.json", {}) or {}
    all_langs = languages.of_brief(brief)
    main = all_langs[0]
    targets = [code for code in all_langs if code in (langs or all_langs)]
    voices = dict(voices or {})
    if voice or main not in voices:
        voices[main] = voice or voices.get(main)
    if len(all_langs) > 1:
        from app.core.translate import is_current, translate_project
        if any(not is_current(folder, code, brief) for code in targets if code != main):
            job.log("Traduzione del copione nelle altre lingue…", module="translate", progress=1)
            await translate_project(LLMService(model=model or None), folder, brief,
                                    lambda m, progress=None, level="info": job.log(m, module="translate", level=level))
    if len(targets) == 1:
        return await render_language(folder, job, voices.get(targets[0]), wpm, cut_seconds, silence_ms, visuals, model, style, music, sfx,
                                     lang=targets[0], brief=brief)
    start = 3
    span = (100 - start) / len(targets)
    for k, code in enumerate(targets):
        name = languages.info(code)["name"]
        stage = _Stage(job, start + span * k, start + span * (k + 1), f"[{name}] ")
        job.log(f"Lingua {k + 1}/{len(targets)}: {name}", module="compositor", progress=start + span * k)
        await render_language(folder, stage, voices.get(code), wpm, cut_seconds, silence_ms, visuals, model, style, music, sfx, lang=code, brief=brief)


class _Stage:
    """Avanzamento in un intervallo, con il nome della lingua davanti a ogni riga del registro."""

    def __init__(self, job, lo, hi, prefix):
        self.job, self.lo, self.hi, self.prefix = job, lo, hi, prefix
        self.data = job.data

    def log(self, message, module="system", progress=None, level="info"):
        scaled = None if progress is None else self.lo + (self.hi - self.lo) * max(0, min(100, progress)) / 100
        self.job.log(self.prefix + message, module=module, progress=scaled, level=level)


async def _shared_visuals(folder, beats, concept, model, style, visuals, log, main_lang, lang, render):
    """Lingua secondaria: gli stessi disegni della principale (dalla cache), etichette tradotte."""
    main_beats = [b for b in load_json(folder / "beats.json", []) if str(b.get("text", "")).strip()]
    audio = folder / "render" / "audio"
    files = [audio / f"beat_{i:03}_clean.wav" for i in range(1, len(main_beats) + 1)]
    empty = ([[] for _ in beats], [""] * len(beats), [], [])
    if visuals == "none" or not illustration_service.available():
        return empty
    if not main_beats or not all(f.is_file() for f in files):
        raise ValueError("Crea prima il video nella lingua principale: le altre lingue riusano i suoi disegni.")
    if len(main_beats) != len(beats):
        log(f"Il copione tradotto ha {len(beats)} blocchi e quello principale {len(main_beats)}: rifai la traduzione per avere i disegni giusti.",
            41, module="illustrator", level="warn")
    durations = [wav_duration(f) for f in files]
    if illustration_service.STYLES.get(style, {}).get("animated"):
        beat_shots, stickers, reload_models, cast = await make_toon(folder, main_beats, durations, concept, model, log, main_lang)
    else:
        beat_shots, stickers, reload_models = await make_illustrations(folder, main_beats, durations, concept, style, model, log, main_lang)
        cast = []
    if reload_models:  # mancavano disegni e sono stati rifatti: il modello di testo serve di nuovo per le etichette
        await illustration_service.restore_gpu(reload_models)
    # etichette a schermo nella lingua del video (in cache finché non cambiano)
    key = hashlib.sha1(json.dumps([stickers, lang], ensure_ascii=False).encode()).hexdigest()
    saved = load_json(render / "stickers.json", {}) or {}
    if saved.get("key") == key:
        stickers = saved["stickers"]
    elif any(stickers):
        try:
            from app.core.translate import translate_list
            stickers = await translate_list(LLMService(model=model or None), stickers, lang, label="Etichette a schermo")
            save_json(render / "stickers.json", {"key": key, "stickers": stickers})
        except Exception as exc:
            log(f"Etichette a schermo non tradotte ({exc}): le tolgo", module="illustrator", level="warn")
            stickers = [""] * len(stickers)

    def fit(rows, fill):
        return (list(rows) + [fill] * len(beats))[:len(beats)]
    return fit(beat_shots, []), fit(stickers, ""), [], cast


async def render_language(folder, job, voice, wpm, cut_seconds, silence_ms, visuals="ai", model=None, style="anim", music=0.25, sfx=True,
                          lang=None, brief=None):
    folder = Path(folder)
    brief = brief if brief is not None else (load_json(folder / "brief.json", {}) or {})
    main_lang = languages.primary(brief)
    lang = lang or main_lang
    base = languages.lang_dir(folder, lang, brief)
    is_main = base == folder
    beats = load_json(base / "beats.json", [])
    beats = [b for b in beats if str(b.get("text", "")).strip()]
    if not beats:
        raise ValueError("Nessun beat da renderizzare: genera o scrivi prima il copione.")
    structure = load_json(base / "structure.json", None) or load_json(folder / "structure.json", {}) or {}
    titles = {p.get("part"): p.get("title", "") for p in structure.get("parts", [])}
    concept = load_json(folder / "concept.json", {}) or {}
    render = base / "render"
    audio_dir, work = render / "audio", render / "_work"
    audio_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    def log(msg, progress=None, module="compositor", level="info"):
        job.log(msg, module=module, progress=progress, level=level)

    # 1. voce, con cache per beat (testo + voce + velocita)
    from app.services.text_norm import narrator_gender
    main_text = " ".join(b.get("text", "") for b in (load_json(folder / "beats.json", []) or []))
    gender = narrator_gender(main_text, concept.get("narrator_gender"))
    tts = TTSService(voice, wpm, gender, lang)
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
        raw = await tts.synthesize_many([(text, audio_dir / f"beat_{i:03}_raw") for i, text, _ in todo], on_progress=lambda m: log(m, module="tts_engine"),
                                        roles=[beats[i - 1].get("role", "") for i, _, _ in todo])
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
    if not is_main:
        beat_shots, stickers, reload_models, cast = await _shared_visuals(folder, beats, concept, model, style, visuals, log, main_lang, lang, render)
        if any(beat_shots):
            log("Disegni della lingua principale riutilizzati", 58, module="illustrator", level="ok")
    elif visuals == "none":
        log("Illustrazioni disattivate: sfondo scuro e sottotitoli", 41, module="illustrator")
    elif not illustration_service.available():
        log("Illustrazioni non installate: esegui installa_disegni.bat. Per ora uso uno sfondo scuro.", 41, module="illustrator", level="warn")
    elif illustration_service.STYLES.get(style, {}).get("animated"):
        beat_shots, stickers, reload_models, cast = await make_toon(folder, beats, durations, concept, model, log, lang)
    else:
        beat_shots, stickers, reload_models = await make_illustrations(folder, beats, durations, concept, style, model, log, lang)
    reload_task = asyncio.create_task(illustration_service.restore_gpu(reload_models)) if reload_models else None

    # 3. video per parte (= clip TikTok) e video completo
    video = VideoService(log, music, sfx)
    parts = sorted({b["part"] for b in beats})

    # copertine: una 9:16 per clip ("PARTE N") e la 16:9 per YouTube
    from app.services import covers
    meta = load_json(base / "metadata.json", {}) or {}
    part_shots, part_bgs = [], []
    for part in parts:
        idx = [i for i, b in enumerate(beats) if b["part"] == part]
        shots = [s for i in idx for s in beat_shots[i]]
        part_shots.append(shots)
        first = next((s for s in shots if Path(s["bg"] if isinstance(s, dict) else s[0]).is_file()), None)
        part_bgs.append((first["bg"] if isinstance(first, dict) else first[0]) if first else None)
    try:
        story_title = concept.get("title") if is_main else meta.get("story_title")
        cover_files = await asyncio.to_thread(covers.make_all, base, parts, [story_title or titles.get(p, "") for p in parts],
                                              part_shots, part_bgs, cast, meta, concept, stickers, lang)
        log(f"Copertine pronte: {len(cover_files['tiktok'])} per TikTok e 1 per YouTube (16:9)", 59)
    except Exception as exc:
        cover_files = {"tiktok": [], "youtube": None}
        log(f"Copertine non create ({exc})", level="warn")
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
            [beat_shots[i] for i in idx], [stickers[i] for i in idx], cast,
            cover_files["tiktok"][n - 1] if len(cover_files["tiktok"]) >= n else None, lang=lang)
        cleans.append(clean)
        t = offset
        for i in idx:
            timings.append({"beat": i, "part": part, "start": round(t, 2), "end": round(t + durations[i], 2)})
            t += durations[i]
        entries += part_entries
        offset += total
        clips.append(out)
        log(f"Parte {n}: {total:.1f}s → {out.relative_to(base).as_posix()}", 60 + 32 * n / len(parts), level="ok")
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
        "rendered_at": datetime.now().isoformat(timespec="seconds"), "lang": lang,
        "voice": tts.voice, "wpm": wpm, "visuals": visuals, "style": style, "illustrated": sum(1 for s in beat_shots if s),
        "scenes": [[Path(s["bg"] if isinstance(s, dict) else s[0]).relative_to(folder.resolve() if Path(s["bg"] if isinstance(s, dict) else s[0]).is_absolute() else folder).as_posix()
                    for s in shots if Path(s["bg"] if isinstance(s, dict) else s[0]).is_file()][:1] for shots in beat_shots], "stickers": stickers, "cut_seconds": cut_seconds, "silence_ms": silence_ms,
        "duration_seconds": round(offset, 2), "beats": timings,
        "clips": [c.relative_to(base).as_posix() for c in clips],
    })
    if reload_task:
        log("Ricarico il modello di testo in LM Studio…", 98, module="illustrator")
        await reload_task
    log(f"Video completo: {offset / 60:.1f} min, {len(clips)} clip TikTok, sottotitoli SRT", 99, level="ok")
