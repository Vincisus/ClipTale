"""Traduzione di una produzione nelle altre lingue scelte.

La storia viene scritta una volta sola nella lingua principale; qui l'AI locale traduce i beat (una chiamata per parte,
con la fine della parte precedente come contesto), i titoli delle parti e i testi social. Ogni lingua finisce in
langs/<codice>/. Se il copione principale cambia, la traduzione viene rifatta; se è già aggiornata, viene riusata.
"""
import hashlib
import json

from app.core import languages
from app.core.pipeline import script_markdown, word_count
from app.core.projects import load_json, save_json, save_text

SYSTEM = "Rispondi esclusivamente con un oggetto JSON valido, senza testo aggiuntivo."
S = {"type": "string"}


def _source_key(folder):
    """Impronta del copione principale: se cambia, le traduzioni vanno rifatte."""
    data = [load_json(folder / "beats.json", []), load_json(folder / "metadata.json", {}), load_json(folder / "structure.json", {})]
    return hashlib.sha1(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def is_current(folder, lang, brief=None):
    base = languages.lang_dir(folder, lang, brief)
    return bool(load_json(base / "beats.json")) and (load_json(base / "source.json", {}) or {}).get("key") == _source_key(folder)


def _rules(lang):
    i = languages.info(lang)
    return (f"Traduci in {i['it']} naturale e parlato, come lo racconterebbe un madrelingua a voce: frasi brevi, stesso tono, "
            f"stessa persona narrante, dialoghi tra virgolette {i['quotes']}. Mantieni i nomi propri dei personaggi. "
            "Scrivi le cifre importanti come nell'originale. Non aggiungere né togliere eventi, non commentare.")


async def translate_beats(llm, beats, lang, label_prefix=""):
    """Traduce i beat parte per parte. Restituisce la lista tradotta (stessi part/role/cue)."""
    out = []
    parts = sorted({b["part"] for b in beats})
    previous = ""
    for part in parts:
        idx = [i for i, b in enumerate(beats) if b["part"] == part]
        texts = [beats[i]["text"] for i in idx]
        schema = {"type": "object", "properties": {"beats": {"type": "array", "minItems": len(texts), "maxItems": len(texts), "items": S}}, "required": ["beats"]}
        prompt = (f"{_rules(lang)}\nTraduci questi {len(texts)} blocchi consecutivi di una storia narrata (parte {part}). "
                  f"Restituisci beats: esattamente {len(texts)} stringhe, una per blocco, nello stesso ordine.\n"
                  + (f"Fine della parte precedente già tradotta: «{previous[-500:]}»\n" if previous else "")
                  + f"Blocchi: {json.dumps(texts, ensure_ascii=False)}")
        got = []
        for attempt in range(2):
            data = await llm.generate_json(prompt, schema, system=SYSTEM, temperature=0.3, label=f"{label_prefix}Traduzione parte {part}")
            got = [str(x or "").strip() for x in (data.get("beats") or [])]
            if len(got) == len(texts) and all(g and word_count(g) >= 0.5 * word_count(t) for g, t in zip(got, texts)):
                break
            prompt += f"\nATTENZIONE: servono esattamente {len(texts)} blocchi completi, nessuno vuoto o riassunto."
        else:
            if len(got) != len(texts):
                raise ValueError(f"parte {part}: tradotti {len(got)} blocchi invece di {len(texts)}")
        for i, text in zip(idx, got):
            out.append({**beats[i], "text": text or beats[i]["text"]})
        previous = got[-1] if got else previous
    return out


async def translate_meta(llm, meta, structure, lang, concept=None):
    """Titoli delle parti e testi social (YouTube e TikTok) nella nuova lingua."""
    parts = structure.get("parts") or []
    n_caps = len(meta.get("part_captions") or []) or len(parts)
    i = languages.info(lang)
    schema = {
        "type": "object",
        "properties": {
            "youtube_title": S, "thumbnail_text": S, "description": S, "tiktok_caption": S, "story_title": S,
            "titles": {"type": "array", "maxItems": 5, "items": S},
            "part_captions": {"type": "array", "minItems": n_caps, "maxItems": n_caps, "items": S},
            "part_titles": {"type": "array", "minItems": len(parts), "maxItems": len(parts), "items": S},
            "hashtags": {"type": "array", "maxItems": 12, "items": S},
        },
        "required": ["youtube_title", "thumbnail_text", "description", "tiktok_caption", "story_title", "titles", "part_captions", "part_titles", "hashtags"],
    }
    source = {k: meta.get(k) for k in ("youtube_title", "thumbnail_text", "description", "tiktok_caption", "titles", "part_captions")}
    source["part_titles"] = [p.get("title", "") for p in parts]
    source["story_title"] = (concept or {}).get("title", "")
    data = await llm.generate_json(
        f"{_rules(lang)}\nTraduci e adatta questi testi social per un pubblico che parla {i['it']}. "
        "Mantieni lo stile: youtube_title con la frase-gancio interrotta da «...» (max 100 caratteri), thumbnail_text 2-5 parole MAIUSCOLE, "
        f"part_captions con il numero della parte scritto come «{i['word_part']} N» e il rimando alla parte successiva o al video completo. "
        f"hashtags: 6-10 hashtag senza spazi, popolari tra chi guarda storie in {i['it']} (non tradurre alla lettera quelli italiani).\n"
        f"Testi: {json.dumps(source, ensure_ascii=False)}",
        schema, system=SYSTEM, temperature=0.4, label=f"Testi social ({i['name']})")
    out = dict(meta)
    for k in ("youtube_title", "thumbnail_text", "description", "tiktok_caption", "story_title"):
        if str(data.get(k) or "").strip():
            out[k] = str(data[k]).strip()
    if data.get("titles"):
        out["titles"] = [str(t).strip() for t in data["titles"] if str(t).strip()][:5]
    caps = [str(c).strip() for c in data.get("part_captions") or []]
    if len(caps) == n_caps:
        out["part_captions"] = caps
    out["hashtags"] = ["#" + str(h).lstrip("#").replace(" ", "") for h in data.get("hashtags") or [] if str(h).strip()] or meta.get("hashtags", [])
    from app.services.publish_service import short_title
    out["youtube_title"] = short_title(out.get("youtube_title", ""))
    titles = [str(t).strip() for t in data.get("part_titles") or []]
    new_structure = {"parts": [{**p, "title": titles[k] if k < len(titles) and titles[k] else p.get("title", "")} for k, p in enumerate(parts)]}
    return out, new_structure


async def translate_project(llm, folder, brief, log, force=False):
    """Traduce il progetto in tutte le lingue secondarie mancanti o non aggiornate."""
    langs = languages.of_brief(brief)
    beats = [b for b in load_json(folder / "beats.json", []) if str(b.get("text", "")).strip()]
    if len(langs) < 2 or not beats:
        return []
    meta = load_json(folder / "metadata.json", {}) or {}
    structure = load_json(folder / "structure.json", {}) or {}
    concept = load_json(folder / "concept.json", {}) or {}
    key = _source_key(folder)
    done = []
    for n, lang in enumerate(langs[1:], 1):
        name = languages.info(lang)["name"]
        base = languages.lang_dir(folder, lang, brief)
        if not force and is_current(folder, lang, brief):
            log(f"{name}: traduzione già aggiornata, riutilizzata", level="info")
            continue
        log(f"{name}: traduzione del copione ({len(beats)} blocchi)…", progress=100 * (n - 1) / (len(langs) - 1))
        if (base / "beats.json").is_file() and not force:
            log(f"{name}: il copione principale è cambiato, rifaccio la traduzione (le correzioni a mano su {name} vengono sostituite)", level="warn")
        translated = await translate_beats(llm, beats, lang, label_prefix=f"{name}: ")
        new_meta, new_structure = (meta, structure)
        if meta or structure:
            try:
                new_meta, new_structure = await translate_meta(llm, meta, structure, lang, concept)
            except Exception as exc:  # i testi social non devono fermare la traduzione della storia
                log(f"{name}: testi social non tradotti ({exc})", level="warn")
        save_json(base / "beats.json", translated)
        save_json(base / "metadata.json", new_meta)
        save_json(base / "structure.json", new_structure)
        save_text(base / "script.md", script_markdown(concept, new_structure, translated))
        save_json(base / "source.json", {"key": key, "lang": lang})
        words = sum(word_count(b["text"]) for b in translated)
        log(f"{name}: copione tradotto, {words} parole", progress=100 * n / (len(langs) - 1), level="ok")
        done.append(lang)
    return done


async def translate_list(llm, items, lang, label="Etichette"):
    """Traduce una lista di frasi brevi (etichette a schermo). Stessa lunghezza; '' resta ''."""
    todo = [(k, t) for k, t in enumerate(items) if str(t or "").strip()]
    if not todo:
        return list(items)
    i = languages.info(lang)
    schema = {"type": "object", "properties": {"items": {"type": "array", "minItems": len(todo), "maxItems": len(todo), "items": S}}, "required": ["items"]}
    data = await llm.generate_json(
        f"Traduci in {i['it']} queste etichette brevi mostrate a schermo in un video (MAIUSCOLO, max 28 caratteri ciascuna, "
        f"stesso numero e ordine): {json.dumps([t for _, t in todo], ensure_ascii=False)}",
        schema, system=SYSTEM, temperature=0.2, label=label)
    got = [str(x or "").strip().upper()[:28] for x in data.get("items") or []]
    if len(got) != len(todo):
        raise ValueError("numero di etichette tradotte diverso")
    out = list(items)
    for (k, _), t in zip(todo, got):
        out[k] = t
    return out
