"""Estrae i testi italiani dell'interfaccia (app/web/index.html) da tradurre nei pacchetti di lingua.

Uso: python desktop/extract_strings.py  ->  app/web/i18n/_keys.json (elenco ordinato dei testi)
Prende i nodi di testo dell'HTML, gli attributi placeholder/aria-label/title e le stringhe del codice JS che contengono
parole (le parti variabili ${...} diventano {0}, {1}…). Spazi multipli ridotti a uno, come fa la pagina quando traduce.
"""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "app" / "web" / "index.html"
OUT = ROOT / "app" / "web" / "i18n" / "_keys.json"
WORD = re.compile(r"[A-Za-zÀ-ÿ]{3,}")
# non testo: selettori, attributi, nomi di file, URL, codici
JUNK = re.compile(r"^\[|^\.|data-|^#|=\"|\.(json|mp4|srt|md|jpg|wav|mp3)|^[a-z]+:[a-z]|^\{\d\}$|^[A-Z_]+$|^tf_|^Content-Type|^application/")


def norm(text):
    return re.sub(r"\s+", " ", text).strip()


class Collector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.keys, self.skip = set(), 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "textarea", "code"):
            self.skip += 1
        for name, value in attrs:
            if name in ("placeholder", "aria-label", "title") and value and WORD.search(value) and "${" not in value:
                self.keys.add(norm(value))

    def handle_endtag(self, tag):
        if tag in ("script", "style", "textarea", "code"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip and WORD.search(data):
            self.keys.add(norm(data))


def js_strings(source):
    """Stringhe JS ("...", '...', `...`) con parole italiane; ${…} -> {n}; tag HTML tolti (vengono tradotti a pezzi)."""
    out = set()
    for m in re.finditer(r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'|`((?:[^`\\]|\\.)*)`', source):
        text = next(g for g in m.groups() if g is not None)
        if not WORD.search(text) or re.fullmatch(r"[\w\-./#:%]+", text) or text.startswith(("/api", "http", "#")):
            continue
        if re.search(r"[{};=]\s*$|^\s*[.#]?[a-z-]+\s*[{:]|\(\)|=>|document\.|\$\(", text) and "${" not in text:
            continue  # codice, CSS o selettori, non testo
        counter = iter(range(100))
        text = re.sub(r"\$\{(?:[^{}]|\{[^{}]*\})*\}", lambda _m: "{%d}" % next(counter), text)
        for piece in re.split(r"<[^>]+>", text):  # i pezzi fra i tag HTML diventano nodi di testo separati
            piece = norm(piece.replace("\\n", " ").replace('\\"', '"').replace("\\'", "'"))
            if WORD.search(piece) and not re.fullmatch(r"[\w\-]+", piece) and not JUNK.search(piece):
                out.add(piece)
    return out


if __name__ == "__main__":
    html = PAGE.read_text(encoding="utf-8")
    c = Collector()
    c.feed(html)
    scripts = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S))
    keys = sorted(c.keys | js_strings(scripts))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(keys, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(keys), "testi ->", OUT)
