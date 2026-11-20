"""Costruisce i pacchetti di lingua dell'interfaccia: app/web/i18n/{en,es,fr,de}.json da app/web/i18n/source/*.txt.

Ogni riga dei sorgenti: italiano ¦ inglese ¦ spagnolo ¦ francese ¦ tedesco. {0}, {1}… sono le parti variabili
(numeri, nomi): ogni traduzione deve avere esattamente le stesse, in qualsiasi ordine. Le righe che iniziano con # sono note.
Uso: python desktop/build_i18n.py   (fallisce se una riga è sbagliata, così un errore non arriva nell'app)
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
I18N = ROOT / "app" / "web" / "i18n"
LANGS = ["en", "es", "fr", "de"]
PH = re.compile(r"\{\d\}")


def build():
    packs = {lang: {} for lang in LANGS}
    errors = []
    for src in sorted((I18N / "source").glob("*.txt")):
        for n, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            cols = [c.strip() for c in line.split("¦")]
            if len(cols) != 5 or not all(cols):
                errors.append(f"{src.name}:{n}: servono 5 colonne piene, trovate {len(cols)}")
                continue
            it = re.sub(r"\s+", " ", cols[0])
            want = sorted(PH.findall(it))
            for lang, text in zip(LANGS, cols[1:]):
                if sorted(PH.findall(text)) != want:
                    errors.append(f"{src.name}:{n} [{lang}]: parti variabili diverse da «{it[:50]}»")
                if it in packs[lang] and packs[lang][it] != text:
                    errors.append(f"{src.name}:{n}: «{it[:50]}» tradotto due volte in modo diverso")
                packs[lang][it] = text
    return packs, errors


if __name__ == "__main__":
    packs, errors = build()
    if errors:
        print("\n".join(errors))
        sys.exit(1)
    for lang, pack in packs.items():
        (I18N / f"{lang}.json").write_text(json.dumps(dict(sorted(pack.items())), ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{len(packs['en'])} testi tradotti in {', '.join(LANGS)}")
