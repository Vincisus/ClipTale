"""Lingue dei video.

Una produzione ha una lingua principale (quella in cui l'AI scrive la storia) e, se vuoi, altre lingue:
la storia viene tradotta e rifatta con voce, sottotitoli, copertine e testi social nella nuova lingua,
riusando gli stessi disegni.

Su disco la lingua principale resta nella cartella del progetto (come i progetti di prima);
ogni altra lingua ha la sua cartella langs/<codice>/ con beats.json, metadata.json, structure.json e render/.
"""
from pathlib import Path

LANGS = {
    "it": {"name": "Italiano", "it": "italiano", "country": "Italia", "num": "it", "part": "PARTE", "full": "STORIA COMPLETA",
           "next": "PARTE {n} NEL PROFILO", "more": "CONTINUA", "word_part": "Parte", "chapters": "Capitoli:", "fiction": "Storia di fantasia.",
           "ai": "Voce e immagini generate con intelligenza artificiale.", "quotes": "«»"},
    "en": {"name": "English", "it": "inglese", "country": "Stati Uniti", "num": "en", "part": "PART", "full": "FULL STORY",
           "next": "PART {n} ON MY PROFILE", "more": "TO BE CONTINUED", "word_part": "Part", "chapters": "Chapters:", "fiction": "Fictional story.",
           "ai": "Voice and images generated with artificial intelligence.", "quotes": "“”"},
    "es": {"name": "Español", "it": "spagnolo", "country": "Spagna", "num": "es", "part": "PARTE", "full": "HISTORIA COMPLETA",
           "next": "PARTE {n} EN MI PERFIL", "more": "CONTINUARÁ", "word_part": "Parte", "chapters": "Capítulos:", "fiction": "Historia de ficción.",
           "ai": "Voz e imágenes generadas con inteligencia artificial.", "quotes": "«»"},
    "fr": {"name": "Français", "it": "francese", "country": "Francia", "num": "fr", "part": "PARTIE", "full": "HISTOIRE COMPLÈTE",
           "next": "PARTIE {n} SUR MON PROFIL", "more": "À SUIVRE", "word_part": "Partie", "chapters": "Chapitres :", "fiction": "Histoire fictive.",
           "ai": "Voix et images générées par intelligence artificielle.", "quotes": "«»"},
    "de": {"name": "Deutsch", "it": "tedesco", "country": "Germania", "num": "de", "part": "TEIL", "full": "GANZE GESCHICHTE",
           "next": "TEIL {n} IN MEINEM PROFIL", "more": "FORTSETZUNG FOLGT", "word_part": "Teil", "chapters": "Kapitel:", "fiction": "Fiktive Geschichte.",
           "ai": "Stimme und Bilder mit künstlicher Intelligenz erstellt.", "quotes": "„“"},
    "pt": {"name": "Português", "it": "portoghese", "country": "Brasile", "num": "pt_BR", "part": "PARTE", "full": "HISTÓRIA COMPLETA",
           "next": "PARTE {n} NO MEU PERFIL", "more": "CONTINUA", "word_part": "Parte", "chapters": "Capítulos:", "fiction": "História fictícia.",
           "ai": "Voz e imagens geradas com inteligência artificial.", "quotes": "“”"},
}
DEFAULT = "it"


def info(lang):
    return LANGS.get(lang or DEFAULT, LANGS[DEFAULT])


def clean_list(codes, primary=None):
    """Lingue valide, senza doppioni, con la principale per prima."""
    out = []
    for c in ([primary] if primary else []) + list(codes or []):
        if c in LANGS and c not in out:
            out.append(c)
    return out or [DEFAULT]


def of_brief(brief):
    """Lingue del progetto (la prima è la principale). I progetti di prima sono solo in italiano."""
    return clean_list((brief or {}).get("languages") or [DEFAULT])


def primary(brief):
    return of_brief(brief)[0]


def lang_dir(folder, lang, brief=None):
    """Cartella dei file di una lingua: quella del progetto per la principale, langs/<codice>/ per le altre."""
    folder = Path(folder)
    if brief is None:
        from app.core.projects import load_json
        brief = load_json(folder / "brief.json", {}) or {}
    return folder if not lang or lang == primary(brief) else folder / "langs" / lang


def localize_prompt(text, lang):
    """Adatta un'istruzione scritta per l'italiano a un'altra lingua di scrittura."""
    if not lang or lang == "it":
        return text
    i = info(lang)
    for old, new in (
        ("ambientata in Italia", f"ambientata in {i['country']}"),
        ("Personaggi con nomi italiani", f"Personaggi con nomi tipici di {i['country']}"),
        ("storie di Reddit' italiane", "storie di Reddit'"),
        ("tra caporali «»", f"tra virgolette {i['quotes']}"),
        ("italiano parlato", f"{i['it']} parlato"),
        ("italiano chiaro", f"{i['it']} chiaro"),
        ("in italiano", f"in {i['it']}"),
        ("IN ITALIANO", f"IN {i['it'].upper()}"),
        ("Sei un editor italiano", f"Sei un editor madrelingua {i['it']}"),
        ("(includi #reddititalia #storievere #storie)", f"(hashtag popolari per le storie in {i['it']})"),
    ):
        text = text.replace(old, new)
    return f"LINGUA: scrivi tutti i testi destinati al pubblico esclusivamente in {i['it']}.\n" + text
