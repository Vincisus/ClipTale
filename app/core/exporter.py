"""Esportazione ordinata dei video finiti, pronta da caricare a mano o da archiviare.

<cartella scelta>/<Nome del progetto>/<Lingua>/
    TikTok e Shorts/   Parte 1.mp4, Parte 2.mp4…
    Video YouTube/     <titolo>.mp4, sottotitoli .srt
    Copertine/         YouTube.jpg, TikTok parte 1.jpg…
    Testi social.txt   titolo, descrizione, tag e didascalie da copiare
I nomi delle cartelle seguono la lingua dell'interfaccia. Parte da sola a fine video; si può rifare da Video.
I file si collegano (hard link) invece di copiarli quando la cartella è sullo stesso disco: nessuno spazio in più.
"""
import os
import re
import shutil
from pathlib import Path

from app.core import languages, settings
from app.core.projects import load_json

NAMES = {
    "it": {"clips": "TikTok e Shorts", "youtube": "Video YouTube", "covers": "Copertine", "texts": "Testi social.txt", "part": "Parte",
           "subs": "Sottotitoli", "tags": "Tag", "desc": "Descrizione", "title": "Titolo", "caption": "Didascalia",
           "langs": {"it": "Italiano", "en": "Inglese", "es": "Spagnolo", "fr": "Francese", "de": "Tedesco", "pt": "Portoghese"}},
    "en": {"clips": "TikTok and Shorts", "youtube": "YouTube video", "covers": "Covers", "texts": "Social texts.txt", "part": "Part",
           "subs": "Subtitles", "tags": "Tags", "desc": "Description", "title": "Title", "caption": "Caption",
           "langs": {"it": "Italian", "en": "English", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese"}},
    "es": {"clips": "TikTok y Shorts", "youtube": "Vídeo YouTube", "covers": "Portadas", "texts": "Textos sociales.txt", "part": "Parte",
           "subs": "Subtítulos", "tags": "Etiquetas", "desc": "Descripción", "title": "Título", "caption": "Descripción del clip",
           "langs": {"it": "Italiano", "en": "Inglés", "es": "Español", "fr": "Francés", "de": "Alemán", "pt": "Portugués"}},
    "fr": {"clips": "TikTok et Shorts", "youtube": "Vidéo YouTube", "covers": "Couvertures", "texts": "Textes sociaux.txt", "part": "Partie",
           "subs": "Sous-titres", "tags": "Tags", "desc": "Description", "title": "Titre", "caption": "Légende",
           "langs": {"it": "Italien", "en": "Anglais", "es": "Espagnol", "fr": "Français", "de": "Allemand", "pt": "Portugais"}},
    "de": {"clips": "TikTok und Shorts", "youtube": "YouTube-Video", "covers": "Cover", "texts": "Social-Media-Texte.txt", "part": "Teil",
           "subs": "Untertitel", "tags": "Tags", "desc": "Beschreibung", "title": "Titel", "caption": "Bildunterschrift",
           "langs": {"it": "Italienisch", "en": "Englisch", "es": "Spanisch", "fr": "Französisch", "de": "Deutsch", "pt": "Portugiesisch"}},
}
MARK = ".cliptale"  # dentro la cartella esportata: l'id del progetto, per non mescolare due progetti con lo stesso titolo


def safe_name(text, limit=80):
    """Nome valido per una cartella o un file di Windows."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(text or "")).strip()
    text = re.sub(r"\s+", " ", text).rstrip(". ")
    if len(text) > limit:  # si taglia fra due parole, non a metà
        text = text[:limit].rsplit(" ", 1)[0] if " " in text[:limit] else text[:limit]
    text = text.rstrip(".,;:!?… ")
    if text.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        text = "_" + text
    return text or "Video"


def _put(src, dst):
    """Mette src in dst: hard link se si può (stesso disco), altrimenti copia. Sostituisce la versione vecchia."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def project_dir(folder, title):
    """Cartella del progetto nell'esportazione: il titolo, con l'id solo se c'è già un altro progetto con quel nome."""
    root = settings.export_dir()
    base = root / safe_name(title)
    for candidate in (base, root / f"{safe_name(title, 70)} ({folder.name[-6:]})"):
        mark = candidate / MARK
        if not candidate.exists() or not mark.is_file() or mark.read_text(encoding="utf-8").strip() == folder.name:
            return candidate
    return candidate


def _texts(base, n):
    from app.services import publish_service
    yt = publish_service.youtube_texts(base)
    shorts = publish_service.shorts_texts(base)
    tiktok = publish_service.tiktok_captions(base)
    lines = ["== YouTube ==", f"{n['title']}: {yt['title']}", "", f"{n['desc']}:", yt["description"], "", f"{n['tags']}: {', '.join(yt['tags'])}", ""]
    for i, item in enumerate(shorts):
        lines += [f"== Shorts · {n['part']} {i + 1} ==", item["title"], "", item["description"], ""]
    for i, item in enumerate(tiktok):
        lines += [f"== TikTok · {n['part']} {i + 1} ==", item["caption"], ""]
    return "\n".join(lines).strip() + "\n"


def export_language(folder, code, brief, target, n):
    base = languages.lang_dir(folder, code, brief)
    render = base / "render"
    if not (render / "full_video.mp4").is_file():
        return 0
    out = target / safe_name(n["langs"].get(code, code))
    count = 0
    for d in (n["clips"], n["youtube"], n["covers"]):  # via i file vecchi: un video rifatto può avere meno parti
        shutil.rmtree(out / d, ignore_errors=True)
    for i, clip in enumerate(sorted((render / "tiktok").glob("part_*.mp4")), 1):
        _put(clip, out / n["clips"] / f"{n['part']} {i}.mp4")
        count += 1
    meta = load_json(base / "metadata.json", {}) or {}
    title = safe_name(meta.get("youtube_title") or meta.get("story_title") or target.name)
    _put(render / "full_video.mp4", out / n["youtube"] / f"{title}.mp4")
    count += 1
    if (render / "subtitles.srt").is_file():
        _put(render / "subtitles.srt", out / n["youtube"] / f"{title}.srt")
    covers = render / "covers"
    if (covers / "youtube.jpg").is_file():
        _put(covers / "youtube.jpg", out / n["covers"] / "YouTube.jpg")
    for cover in sorted(covers.glob("tiktok_part_*.jpg")):
        num = int(re.search(r"(\d+)", cover.stem).group(1))
        _put(cover, out / n["covers"] / f"TikTok {n['part'].lower()} {num}.jpg")
    try:
        (out / n["texts"]).write_text(_texts(base, n), encoding="utf-8")
    except (OSError, KeyError, ValueError, TypeError):
        pass  # i testi sono un di più: il video esportato conta di più
    return count


def export_project(folder, log=None):
    """Esporta tutte le lingue già pronte. Torna la cartella del progetto esportato (o None se non c'era niente)."""
    folder = Path(folder)
    brief = load_json(folder / "brief.json", {}) or {}
    concept = load_json(folder / "concept.json", {}) or {}
    title = concept.get("title") or brief.get("topic") or folder.name
    n = NAMES.get(settings.get()["ui_lang"], NAMES["it"])
    target = project_dir(folder, title)
    done = 0
    for code in languages.of_brief(brief):
        done += export_language(folder, code, brief, target, n)
    if not done:
        return None
    (target / MARK).write_text(folder.name, encoding="utf-8")
    if log:
        log(f"Video esportati in {target}")
    return target
