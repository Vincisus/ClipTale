"""Testo italiano pronto per la voce, e riconoscimento del genere di chi racconta.

- numeri, cifre in euro, percentuali, orari, date e sigle diventano parole ("2.500 €" -> "duemilacinquecento euro"),
  così la voce non li legge cifra per cifra o in inglese;
- abbreviazioni comuni sciolte (sig. -> signor, dott. -> dottor);
- il testo viene diviso in frasi, con una pausa naturale diversa dopo punto, domanda o puntini.
"""
import re

try:
    from num2words import num2words
except ImportError:  # senza num2words i numeri restano come sono
    num2words = None

ABBREVIATIONS = {
    r"\bsig\.ra\b": "signora", r"\bsig\.na\b": "signorina", r"\bsig\.": "signor", r"\bdott\.ssa\b": "dottoressa",
    r"\bdott\.": "dottor", r"\bavv\.": "avvocato", r"\bing\.": "ingegner", r"\bprof\.ssa\b": "professoressa",
    r"\bprof\.": "professor", r"\becc\.": "eccetera", r"\bpag\.": "pagina", r"\bn\.\s?(?=\d)": "numero ",
    r"\bWhatsApp\b": "uozzap", r"\bok\b": "okèi", r"\bOK\b": "okèi", r"\bTV\b": "tivù", r"\bPC\b": "pici",
}
MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


def _num(value, ordinal=False):
    if num2words is None:
        return value
    try:
        if isinstance(value, str):
            value = float(value) if "." in value else int(value)
        return num2words(value, lang="it", to="ordinal" if ordinal else "cardinal")
    except (ValueError, OverflowError, NotImplementedError):
        return str(value)


def _parse_number(text):
    """'2.500' -> 2500, '2.500,50' -> 2500.5, '3,5' -> 3.5 (formato italiano)."""
    text = text.strip()
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", text):
        text = text.replace(".", "")
    text = text.replace(",", ".")
    return float(text) if "." in text else int(text)


def normalize(text):
    """Testo pronto per la sintesi vocale in italiano."""
    t = text
    for pattern, repl in ABBREVIATIONS.items():
        t = re.sub(pattern, repl, t, flags=re.I if pattern.islower() else 0)
    t = t.replace("«", "").replace("»", "").replace("“", "").replace("”", "").replace("\"", "")
    t = re.sub(r"\s*[—–]\s*", ", ", t)
    t = t.replace("…", "...")
    # date: 28/12, 2/1/2027
    t = re.sub(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b",
               lambda m: f"{'primo' if m.group(1) in ('1', '01') else _num(int(m.group(1)))} {MONTHS[int(m.group(2)) - 1] if 1 <= int(m.group(2)) <= 12 else m.group(2)}"
               + (f" {_num(int(m.group(3)))}" if m.group(3) else ""), t)
    # orari: 18:30 -> diciotto e trenta
    t = re.sub(r"\b(\d{1,2})[:.](\d{2})\b(?=\s*(?:di sera|di mattina|del pomeriggio|in punto|$|[,.;!?\s]))",
               lambda m: f"{_num(int(m.group(1)))} e {_num(int(m.group(2)))}" if int(m.group(1)) < 24 and int(m.group(2)) < 60 else m.group(0), t)
    # soldi: € 2.500 / 2.500€ / 2500 euro
    t = re.sub(r"€\s?(\d[\d.,]*)", lambda m: f"{_num(_parse_number(m.group(1)))} euro", t)
    t = re.sub(r"(\d[\d.,]*)\s?€", lambda m: f"{_num(_parse_number(m.group(1)))} euro", t)
    t = re.sub(r"(\d[\d.,]*)\s?k\b", lambda m: f"{_num(_parse_number(m.group(1)))} mila", t)
    # percentuali e ordinali
    t = re.sub(r"(\d[\d.,]*)\s?%", lambda m: f"{_num(_parse_number(m.group(1)))} per cento", t)
    t = re.sub(r"\b(\d{1,3})\s?[°ª]", lambda m: _num(int(m.group(1)), ordinal=True), t)
    # numeri rimasti
    t = re.sub(r"\b\d{1,3}(?:\.\d{3})+(?:,\d+)?\b|\b\d+(?:,\d+)?\b", lambda m: _num(_parse_number(m.group(0))), t)
    return re.sub(r"\s+", " ", t).strip()


def sentences(text):
    """[(frase, pausa in secondi dopo la frase)]: pause più lunghe dopo domande, esclamazioni e puntini."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", text.strip()) if p.strip()]
    out = []
    for p in parts:
        if p.endswith(("...", "…")):
            pause = 0.55
        elif p.endswith(("?", "!")):
            pause = 0.42
        elif p.endswith(":"):
            pause = 0.3
        else:
            pause = 0.32
        out.append((p, pause))
    return out


# ------------------------------------------------------------------ chi racconta
_FEM = re.compile(r"\b(?:sono|ero|sarei|mi sono|mi ero|mi sentivo|mi sento|resto|rimasi|rimango)\s+(?:\w+\s+)?(stata|andata|rimasta|arrivata|tornata|entrata|uscita|nata|felice\b(?!)|sicura|stanca|contenta|pronta|sola|convinta|furiosa|incinta|sposata|arrabbiata|delusa|sorpresa|seduta|ferma|immobile\b(?!)|zitta|gelata|paralizzata|scioccata|incredula)\b", re.I)
_MAS = re.compile(r"\b(?:sono|ero|sarei|mi sono|mi ero|mi sentivo|mi sento|resto|rimasi|rimango)\s+(?:\w+\s+)?(stato|andato|rimasto|arrivato|tornato|entrato|uscito|nato|sicuro|stanco|contento|pronto|solo|convinto|furioso|sposato|arrabbiato|deluso|sorpreso|seduto|fermo|zitto|gelato|paralizzato|scioccato|incredulo)\b", re.I)
_FEM_ROLE = re.compile(r"\b(mio marito|il mio fidanzato|il mio ragazzo|mio ex marito)\b", re.I)
_MAS_ROLE = re.compile(r"\b(mia moglie|la mia fidanzata|la mia ragazza|mia ex moglie)\b", re.I)


def narrator_gender(text, hint=""):
    """'F', 'M' o '' (incerto): dal campo del concept se c'è, altrimenti dalle concordanze in prima persona."""
    hint = str(hint or "").strip().upper()[:1]
    if hint in ("F", "D", "W"):
        return "F"
    if hint in ("M", "U"):
        return "M"
    f = len(_FEM.findall(text)) * 2 + len(_FEM_ROLE.findall(text))
    m = len(_MAS.findall(text)) * 2 + len(_MAS_ROLE.findall(text))
    if f > m:
        return "F"
    if m > f:
        return "M"
    return ""
