"""Generazione del copione: concept -> struttura in parti -> testo di ogni parte diviso in beat -> metadati.

Poche chiamate (3 + una per parte), ognuna salvata su disco: se il lavoro si interrompe, rilanciandolo
sullo stesso progetto riparte dal primo passaggio mancante. Nessun revisore automatico bloccante:
i difetti si correggono a mano nell'editor dei beat, che e la fonte per il render.
"""
import json
import re

from app.core.projects import load_json, save_json, save_text

WPM_DEFAULT = 150

FORMATS = {
    # Formula delle "storie di Reddit" italiane (canali come Moglie Reddit): chi racconta subisce un torto,
    # ha una carta nascosta, la situazione peggiora a ondate, arriva la rivincita e un ultimo colpo di scena.
    "reddit": {
        "label": "Storia stile Reddit",
        "concept": (
            "Crea il concept di una storia ORIGINALE in italiano nello stile delle 'storie di Reddit' (come il canale Moglie Reddit): "
            "raccontata in prima persona da chi subisce un torto, realistica e ambientata in Italia. Temi tipici: matrimonio e tradimenti, "
            "suoceri o parenti che umiliano, fratelli e sorelle invidiosi, figli ingrati, soldi ed eredità, capi ingiusti, segreti scoperti per caso "
            "(un telefono, una chat, il gruppo WhatsApp di famiglia o del condominio).\n"
            "Ingredienti obbligatori:\n"
            "1) una frase d'apertura shock, in prima persona, che contiene già il conflitto (esempi di tono: «Quando chiesi a mio marito cosa volesse "
            "per il compleanno, mi rispose che voleva il divorzio.» / «Alla cena di famiglia, papà mi chiese se la paghetta mi bastava.»);\n"
            "2) un'ingiustizia o umiliazione concreta con dialoghi taglienti, che fa arrabbiare chi ascolta;\n"
            "3) una carta nascosta del narratore che gli altri ignorano (soldi segreti, una prova, un'informazione, un ruolo importante);\n"
            "4) un'escalation in 3-4 colpi, ognuno peggiore del precedente;\n"
            "5) una rivincita soddisfacente e legale, con la reazione degli altri personaggi;\n"
            "6) un ultimo colpo di scena che nessuno si aspetta.\n"
            "Personaggi con nomi italiani. Niente violenza esplicita, niente contenuti sessuali."
        ),
        "write": (
            "Prima persona, italiano parlato come chi racconta la propria storia a voce: frasi brevi, molti dialoghi diretti tra caporali «», "
            "dettagli concreti (cifre, oggetti, luoghi, messaggi letti sul telefono). Ogni 3-4 frasi un piccolo gancio che fa restare: "
            "una rivelazione a metà, una domanda, un «ma loro non sapevano che…». Niente titoli, elenchi, emoji o note dell'autore."
        ),
    },
    "story": {
        "label": "Racconto in prima persona",
        "concept": (
            "Crea il concept di una storia ORIGINALE di fiction in italiano, raccontata in prima persona, "
            "realistica (famiglia, coppia, amicizia, lavoro, segreti, vendetta, mistero domestico). "
            "Pochi personaggi, conflitto concreto, colpo di scena motivato da una prova comprensibile, finale che chiude la storia."
        ),
        "write": (
            "Narrazione in prima persona, italiano parlato e scorrevole, frasi brevi adatte alla voce. "
            "Dialoghi naturali riportati nel racconto. Niente titoli, elenchi, didascalie o note dell'autore."
        ),
    },
    "explainer": {
        "label": "Divulgativo / spiegazione",
        "concept": (
            "Crea il concept di un video divulgativo in italiano sull'argomento richiesto: un gancio iniziale forte, "
            "una tesi chiara, 3-5 punti concreti e verificabili, una chiusura memorabile. Non inventare dati precisi "
            "(numeri, date, nomi) se non sei sicuro: preferisci formulazioni prudenti."
        ),
        "write": (
            "Testo parlato da voce narrante, italiano chiaro e incisivo, frasi brevi, ritmo da video social. "
            "Niente titoli, elenchi puntati, emoji o indicazioni di regia."
        ),
    },
}

S = {"type": "string"}
CONCEPT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": S, "hook": S, "logline": S, "setting": S, "tone": S, "ending": S, "narrator_gender": {"type": "string", "enum": ["F", "M"]},
        "characters": {"type": "array", "maxItems": 5, "items": {"type": "object", "properties": {"id": S, "name": S, "role": S}, "required": ["id", "name", "role"]}},
    },
    "required": ["title", "hook", "logline", "setting", "tone", "ending", "narrator_gender", "characters"],
}


def structure_schema(count):
    part = {"type": "object", "properties": {"part": {"type": "integer"}, "title": S, "summary": S, "cliffhanger": S}, "required": ["part", "title", "summary", "cliffhanger"]}
    return {"type": "object", "properties": {"parts": {"type": "array", "minItems": count, "maxItems": count, "items": part}}, "required": ["parts"]}


def part_schema(beats):
    beat = {"type": "object", "properties": {"text": S, "visual": S}, "required": ["text", "visual"]}
    return {"type": "object", "properties": {"beats": {"type": "array", "minItems": beats, "maxItems": beats, "items": beat}}, "required": ["beats"]}


def metadata_schema(parts):
    return {
        "type": "object",
        "properties": {
            "youtube_title": S,
            "titles": {"type": "array", "minItems": 5, "maxItems": 5, "items": S},
            "description": S, "tiktok_caption": S,
            "part_captions": {"type": "array", "minItems": parts, "maxItems": parts, "items": S},
            "hashtags": {"type": "array", "maxItems": 12, "items": S},
        },
        "required": ["youtube_title", "titles", "description", "tiktok_caption", "part_captions", "hashtags"],
    }


# Il taglio delle clip TikTok: ogni parte finisce sul punto di massima tensione (mai su una pausa naturale)
# e, dalla seconda in poi, riparte con una riga che rimette dentro chi arriva direttamente da quella clip.
CUT_RULES = (
    "Regole dei tagli (ogni parte è una clip TikTok separata e deve far venire voglia di vedere la successiva): "
    "la parte 1 contiene il gancio nella prima frase e il primo torto entro i primi 20 secondi; "
    "ogni parte tranne l'ultima finisce nel momento di massima tensione: una frase scioccante appena detta, una rivelazione a metà, "
    "una porta che si apre, un messaggio letto solo in parte; mai su una conclusione o su una pausa naturale. "
    "L'ultima parte chiude tutto con la rivincita e il colpo di scena finale."
)


def word_count(text):
    return len(re.findall(r"[\wÀ-ÿ’']+", text or ""))


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


def plan(brief):
    """Numeri della produzione: parole totali, per parte e numero di beat per parte."""
    wpm = int(brief.get("wpm") or WPM_DEFAULT)
    total_words = max(60, round(float(brief["duration_minutes"]) * wpm))
    parts = int(brief["parts"])
    per_part = max(40, round(total_words / parts))
    beats = max(2, min(8, round(per_part / 110)))
    return {"total_words": total_words, "words_per_part": per_part, "beats_per_part": beats}


def beat_role(part_no, beat_no, parts, beats):
    if part_no == 1 and beat_no == 1:
        return "HOOK"
    if beat_no == beats:
        return "FINALE" if part_no == parts else "CLIFFHANGER"
    return "SVILUPPO"


def normalize_concept(value, brief):
    concept = {k: str(value.get(k) or "").strip() for k in ("title", "hook", "logline", "setting", "tone", "ending", "narrator_gender")}
    concept["title"] = concept["title"] or brief["topic"][:80]
    chars, seen = [], set()
    for i, c in enumerate(value.get("characters") or [], 1):
        if not isinstance(c, dict) or not str(c.get("name") or "").strip():
            continue
        cid = re.sub(r"[^a-z0-9_]", "", str(c.get("id") or "").lower()) or f"p{i}"
        while cid in seen:
            cid += "_"
        seen.add(cid)
        chars.append({"id": cid, "name": str(c["name"]).strip(), "role": str(c.get("role") or "").strip()})
    concept["characters"] = chars
    # i progetti creati dalla versione precedente hanno campi diversi: li conserviamo
    for key in ("conflict", "turning_point", "resolution", "moral", "visual_style"):
        if value.get(key):
            concept[key] = value[key]
    return concept


def normalize_structure(value, count):
    parts = [p for p in (value.get("parts") or []) if isinstance(p, dict)]
    if not parts:
        raise ValueError("Il modello non ha restituito le parti della struttura.")
    parts = parts[:count]
    while len(parts) < count:  # parte mancante: meglio un segnaposto che fermare tutto
        parts.append({"title": f"Parte {len(parts) + 1}", "summary": "Prosegui la storia in modo coerente verso il finale.", "cliffhanger": ""})
    return {"parts": [{"part": i, "title": str(p.get("title") or f"Parte {i}").strip(), "summary": str(p.get("summary") or "").strip(), "cliffhanger": str(p.get("cliffhanger") or "").strip()} for i, p in enumerate(parts, 1)]}


def split_text(text, pieces):
    """Divide un testo in N blocchi di frasi di lunghezza simile."""
    sentences = [s for s in re.split(r"(?<=[.!?…])\s+", text.strip()) if s]
    if len(sentences) <= pieces:
        return sentences + [""] * (pieces - len(sentences))
    target = sum(map(len, sentences)) / pieces
    blocks, current = [], []
    for s in sentences:
        current.append(s)
        if len(blocks) < pieces - 1 and sum(map(len, current)) >= target:
            blocks.append(" ".join(current))
            current = []
    blocks.append(" ".join(current))
    return blocks


class StoryPipeline:
    def __init__(self, llm, folder, job):
        self.llm, self.folder, self.job = llm, folder, job

    def log(self, msg, progress=None, level="info", module="story_brain"):
        self.job.log(msg, module=module, progress=progress, level=level)

    async def run(self, brief):
        fmt = FORMATS.get(brief.get("format"), FORMATS["reddit"])
        numbers = plan(brief)
        parts_count = int(brief["parts"])
        self.log(f"Obiettivo: ~{numbers['total_words']} parole, {parts_count} parti da {numbers['beats_per_part']} beat (modello {self.llm.model})", 3)

        # 1. concept
        concept = load_json(self.folder / "concept.json")
        if concept:
            concept = normalize_concept(concept, brief)
            self.log("Concept gia presente: riutilizzato", 10)
        else:
            self.log("Generazione concept…", 5, module="llm_service")
            raw = await self.llm.generate_json(
                f"{fmt['concept']}\nArgomento / richiesta dell'utente: {brief['topic']}\n"
                f"Durata del video: circa {brief['duration_minutes']} minuti, divisa in {parts_count} parti per TikTok.\n"
                "Campi: title (titolo accattivante), hook (frase d'apertura che cattura in 3 secondi), logline, setting, "
                "tone, ending (come si chiude), narrator_gender (F se chi racconta in prima persona è una donna, M se è un uomo), characters (lista di personaggi con id breve, name, role; vuota se non servono).",
                CONCEPT_SCHEMA, label="Concept")
            concept = normalize_concept(raw, brief)
            save_json(self.folder / "concept.json", concept)
            self.log(f"Concept: «{concept['title']}»", 12, level="ok")

        # 2. struttura
        structure = load_json(self.folder / "structure.json")
        if structure and len(structure.get("parts") or []) == parts_count:
            structure = normalize_structure(structure, parts_count)
            self.log("Struttura gia presente: riutilizzata", 18)
        else:
            self.log(f"Pianificazione di {parts_count} parti…", 14, module="llm_service")
            raw = await self.llm.generate_json(
                f"Dividi questo concept in esattamente {parts_count} parti consecutive. Ogni parte diventa un video TikTok autonomo "
                "ma la sequenza forma un'unica storia (che su YouTube esce intera). Per ogni parte: part (numero da 1), title, "
                "summary (eventi concreti in ordine, 3-5 frasi), cliffhanger (l'ultima frase della parte: il punto di massima tensione "
                "dove si taglia la clip; nell'ultima parte è la frase conclusiva).\n"
                + (CUT_RULES + "\n" if brief.get("format", "reddit") != "explainer" else "")
                + f"Non cambiare il finale previsto.\nConcept: {dumps(concept)}",
                structure_schema(parts_count), label="Struttura")
            structure = normalize_structure(raw, parts_count)
            save_json(self.folder / "structure.json", structure)
            self.log("Struttura approvata: " + " | ".join(p["title"] for p in structure["parts"]), 20, level="ok")

        # 3. testo di ogni parte
        written = []
        span = 70 / parts_count
        for part in structure["parts"]:
            n = part["part"]
            path = self.folder / "parts" / f"part_{n:02}.json"
            saved = load_json(path)
            if saved and saved.get("beats"):
                written.append(saved)
                self.log(f"Parte {n}: gia scritta, riutilizzata", 20 + span * n)
                continue
            self.log(f"Parte {n}/{parts_count}: scrittura «{part['title']}»…", 20 + span * (n - 1), module="llm_service")
            saved = await self.write_part(fmt, concept, structure, part, written, numbers, parts_count)
            save_json(path, saved)
            written.append(saved)
            words = sum(word_count(b["text"]) for b in saved["beats"])
            self.log(f"Parte {n}: {words} parole in {len(saved['beats'])} beat", 20 + span * n, level="ok")

        # 4. beat editabili + copione
        beats = []
        for saved in written:
            count = len(saved["beats"])
            for i, b in enumerate(saved["beats"], 1):
                beats.append({"part": saved["part"], "role": beat_role(saved["part"], i, parts_count, count), "text": b["text"], "cue": b["visual"]})
        save_json(self.folder / "beats.json", beats)
        save_text(self.folder / "script.md", script_markdown(concept, structure, beats))
        self.log(f"Copione pronto: {sum(word_count(b['text']) for b in beats)} parole, {len(beats)} beat", 92, level="ok")

        # 5. metadati (non bloccanti)
        if not load_json(self.folder / "metadata.json"):
            try:
                self.log("Titoli, descrizione e hashtag…", 94, module="metadata")
                meta = await self.llm.generate_json(
                    "Prepara i metadati social in italiano.\n"
                    "youtube_title: il titolo del video lungo su YouTube, nello stile dei canali di storie Reddit: la frase-gancio in prima "
                    "persona che racconta l'inizio del conflitto, interrotta sul più bello con «...» (max 100 caratteri, niente emoji, "
                    "es. «Quando chiesi a mio marito cosa volesse per il compleanno, mi rispose che voleva il...»).\n"
                    "titles: 5 alternative diverse nello stesso stile (max 100 caratteri).\n"
                    "description: descrizione YouTube di 2-3 frasi che incuriosisce senza svelare il finale; termina con «Storia di fantasia.»\n"
                    "tiktok_caption: didascalia breve generica.\n"
                    f"part_captions: esattamente {len(structure['parts'])} didascalie TikTok, una per parte: una frase che stuzzica "
                    "(max 90 caratteri) seguita da «Parte N» (col numero giusto); per tutte le parti tranne l'ultima aggiungi "
                    "«👉 Parte N+1 nel profilo», per l'ultima «Storia completa su YouTube, link nel profilo».\n"
                    "hashtags: 6-10 hashtag senza spazi (includi #reddititalia #storievere #storie).\n"
                    f"Concept: {dumps(concept)}\nParti: {dumps([{'part': p['part'], 'title': p['title'], 'cliffhanger': p['cliffhanger']} for p in structure['parts']])}",
                    metadata_schema(len(structure["parts"])), label="Metadati")
                meta["hashtags"] = ["#" + h.lstrip("#").replace(" ", "") for h in meta.get("hashtags", []) if h.strip()]
                from app.services.publish_service import short_title
                meta["youtube_title"] = short_title(meta.get("youtube_title") or (meta.get("titles") or [concept["title"]])[0])
                caps = [str(c).strip() for c in meta.get("part_captions") or []]
                n_parts = len(structure["parts"])
                meta["part_captions"] = (caps + [f"{concept['title']} — Parte {i}" for i in range(len(caps) + 1, n_parts + 1)])[:n_parts]
                save_json(self.folder / "metadata.json", meta)
            except Exception as exc:  # i metadati non devono far fallire il copione
                self.log(f"Metadati non generati ({exc}); puoi rigenerarli dopo", level="warn", module="metadata")
        return beats

    async def write_part(self, fmt, concept, structure, part, written, numbers, parts_count):
        n, beats_n, words = part["part"], numbers["beats_per_part"], numbers["words_per_part"]
        previous = written[-1]["beats"][-1]["text"][-700:] if written else ""
        narrative = fmt is not FORMATS["explainer"]
        if n == 1:
            position = "Questa è la PRIMA parte: la prima frase è esattamente il gancio del concept, poi arriva subito il primo torto."
        elif narrative:
            position = (f"Riprendi da dove finisce la parte {n - 1}. Apri con UNA sola frase-richiamo di massimo 15 parole che rimette "
                        "dentro chi arriva direttamente da questa clip (es. «Mia suocera aveva appena letto il messaggio ad alta voce…»), poi prosegui.")
        else:
            position = f"Riprendi esattamente da dove finisce la parte {n - 1}, senza riassumerla."
        if n == parts_count:
            position += " Questa è l'ULTIMA parte: rivincita, reazioni e colpo di scena finale, poi chiudi la storia."
        else:
            position += (" Termina ESATTAMENTE sul cliffhanger previsto: l'ultima frase è il punto di massima tensione, "
                         "senza spiegarlo e senza anticipare le parti successive.")
        prompt = (
            f"Scrivi il testo parlato della parte {n} di {parts_count}.\n{fmt['write']}\n{position}\n"
            f"Lunghezza: circa {words} parole in totale, divise in {beats_n} beat consecutivi di lunghezza simile "
            f"(ognuno circa {round(words / beats_n)} parole). Il testo dei beat letto di seguito deve formare un racconto continuo.\n"
            "Per ogni beat: text (il testo da leggere) e visual (descrizione breve dell'immagine di sfondo, in italiano, max 12 parole).\n"
            f"Concept: {dumps(concept)}\n"
            f"Piano completo: {dumps(structure['parts'])}\n"
            f"Parte da scrivere ora: {dumps(part)}\n"
            + (f"Ultime righe della parte precedente: «{previous}»\n" if previous else "")
        )
        data = await self.llm.generate_json(prompt, part_schema(beats_n), temperature=0.85, label=f"Parte {n}")
        beats = [{"text": str(b.get("text") or "").strip(), "visual": str(b.get("visual") or "").strip()} for b in data.get("beats", []) if isinstance(b, dict)]
        beats = [b for b in beats if b["text"]]
        got = sum(word_count(b["text"]) for b in beats)
        if got < words * 0.6:
            self.log(f"Parte {n}: testo corto ({got}/{words} parole), chiedo di ampliarlo", level="warn")
            data = await self.llm.generate_json(
                prompt + f"\nLa versione precedente era troppo corta ({got} parole). Riscrivila ampliandola fino a circa {words} parole, "
                f"stessi eventi, piu dettagli e dialoghi.\nVersione precedente: {dumps(beats)}",
                part_schema(beats_n), temperature=0.85, label=f"Parte {n} (ampliamento)")
            longer = [{"text": str(b.get("text") or "").strip(), "visual": str(b.get("visual") or "").strip()} for b in data.get("beats", []) if isinstance(b, dict) and b.get("text")]
            if sum(word_count(b["text"]) for b in longer) > got:
                beats = longer
        if not beats:
            raise ValueError(f"Parte {n}: il modello non ha prodotto testo.")
        if len(beats) == 1 and beats_n > 1:  # tutto in un solo beat: lo dividiamo noi
            beats = [{"text": t, "visual": beats[0]["visual"]} for t in split_text(beats[0]["text"], beats_n) if t]
        beats = await self.polish(n, part, beats, previous)
        return {"part": n, "title": part["title"], "beats": beats}

    async def polish(self, n, part, beats, previous):
        """Revisione rapida: refusi, grammatica e contraddizioni evidenti. Mai bloccante."""
        self.log(f"Parte {n}: revisione di ortografia e coerenza…", module="llm_service")
        try:
            data = await self.llm.generate_json(
                "Sei un editor italiano. Correggi il testo di questi beat: errori di ortografia e grammatica, parole inesistenti, "
                "frasi poco chiare e contraddizioni evidenti con la parte precedente o con il riassunto della parte. "
                "Mantieni stile, persona narrante, eventi, lunghezza e numero dei beat. Non aggiungere commenti. "
                "Restituisci beats con text e visual (visual invariato).\n"
                f"Riassunto della parte: {part.get('summary', '')}\n"
                + (f"Fine della parte precedente: «{previous}»\n" if previous else "")
                + f"Beat: {dumps(beats)}",
                part_schema(len(beats)), temperature=0.3, label=f"Parte {n} (revisione)")
            fixed = [str(b.get("text") or "").strip() for b in data.get("beats", []) if isinstance(b, dict)]
            if len(fixed) == len(beats) and all(word_count(f) >= 0.8 * word_count(b["text"]) for f, b in zip(fixed, beats)):
                return [{"text": f, "visual": b["visual"]} for f, b in zip(fixed, beats)]
            self.log(f"Parte {n}: revisione scartata (testo accorciato o incompleto)", level="warn")
        except Exception as exc:
            self.log(f"Parte {n}: revisione non riuscita ({exc}), tengo il testo originale", level="warn")
        return beats


def script_markdown(concept, structure, beats):
    lines = [f"# {concept.get('title', 'Copione')}", ""]
    titles = {p["part"]: p["title"] for p in (structure or {}).get("parts", [])}
    current = None
    for b in beats:
        if b["part"] != current:
            current = b["part"]
            lines += [f"## Parte {current} — {titles.get(current, '')}".rstrip(" —"), ""]
        lines += [b["text"], ""]
    return "\n".join(lines)
