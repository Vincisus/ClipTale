# TiktokFactory Studio

App locale che trasforma **un'idea di una riga** in video pronti da pubblicare, tutto sul tuo PC:
la storia la scrive il modello in **LM Studio**, la voce è neurale **locale** (Piper), ogni scena è **disegnata
in locale** da Stable Diffusion. In uscita: il video lungo per **YouTube** e una clip per parte per **TikTok**,
con titoli, descrizione, capitoli e didascalie. Dalla sezione **Pubblica** li carichi con un clic.

## Avvio

1. Apri **LM Studio**, carica il modello e avvia il server locale (Developer → Start server, porta 1234).
2. Doppio clic su **`Avvia.bat`**. La prima volta crea `.venv` e installa le dipendenze.
3. Una volta sola: **`installa_disegni.bat`** (PyTorch + Stable Diffusion + voce italiana Paola, circa 6 GB).
4. Il browser si apre su <http://127.0.0.1:8000/>.

Servono Python 3.12 e LM Studio. FFmpeg è incluso tramite `imageio-ffmpeg`; se ne hai uno nel PATH viene usato quello.

## Crea tutto (automatico)

Scrivi l'idea (es. *«mia suocera mi ha umiliata al pranzo di Natale, ma la casa era intestata a me»*) e premi
**Crea tutto**. Senza altri passaggi l'app fa:

storia (LM Studio) → voce locale → descrizione delle scene → disegni (Stable Diffusion) → video lungo + clip TikTok
→ titolo, descrizione con capitoli, didascalie → pubblicazione (se l'hai attivata e gli account sono collegati).

Una storia di 2 minuti richiede circa 6 minuti sulla GTX 1070. Una di 8 minuti circa 15-20.

### Storie in stile Reddit

Il formato predefinito segue la formula dei canali di "storie di Reddit" italiane: frase d'apertura shock in prima
persona, un torto o un'umiliazione, una carta nascosta del narratore, escalation, rivincita e colpo di scena finale.
Ogni parte è una clip TikTok **tagliata nel punto di massima tensione** (mai su una pausa). Dalla seconda parte in
poi la clip si apre con una riga che rimette dentro chi arriva da lì. Il titolo YouTube è la frase-gancio interrotta
con «...». La didascalia di ogni clip rimanda alla parte successiva, l'ultima al video completo su YouTube.

## Flusso manuale

| Passo | Cosa succede |
|---|---|
| **1. Idea** | concept → struttura in N parti → testo di ogni parte diviso in beat → titoli, didascalie, hashtag. |
| **2. Copione** | Modifica testo e "visual cue" di ogni beat; il salvataggio è automatico. Puoi aggiungere o eliminare beat. |
| **3. Video** | voce per ogni beat (con cache) → taglio silenzi → un disegno AI locale per ogni scena, animato → sottotitoli bianchi in basso, tempati sull'audio reale → `render/tiktok/part_XX.mp4` + `render/full_video.mp4` + `render/subtitles.srt` |
| **4. Pubblica** | video lungo su YouTube, clip su TikTok (vedi sotto). |

Se un lavoro si interrompe, seleziona il progetto e premi **Riprendi Script**: riparte dal primo passo mancante.
Tutto è salvato in `projects/<id>/`.

### Cartone animato (stile predefinito)

Personaggi a testa tonda con contorni neri e occhi stanchi (stile dei reel "cartoon"), **disegnati e animati dal codice**
(`app/services/toon.py`): respirano, sbattono le palpebre, muovono la bocca a tempo con la voce, cambiano espressione
(8) e posa (8) a ogni inquadratura e restano identici per tutto il video. Il flusso:

1. LM Studio fa la **regia**: scheda grafica di ogni personaggio (capelli, colori, occhiali, barba…), i luoghi della storia
   e, per ogni momento di ~3,5 s del parlato, chi è in scena, con che espressione e posa, chi parla e l'inquadratura
   (mezzo busto, primo piano, due personaggi, campo largo);
2. LM Studio va in pausa e **Stable Diffusion disegna solo gli sfondi vuoti** (uno per luogo, ~5 s ciascuno), copiando lo
   stile delle immagini in `style_refs/` con IP-Adapter Plus + InstantStyle (si copia il modo di disegnare, non i personaggi);
3. ogni fotogramma viene composto (sfondo con movimento di camera + personaggi animati) e codificato da FFmpeg;
4. sottotitoli karaoke, etichette con la frase chiave, "PARTE N" e cartello "CONTINUA" sulle clip, video lungo pulito;
5. **musica di sottofondo ed effetti sonori** sintetizzati (whoosh ai cambi di scena, pop sulle etichette, sting sul
   cliffhanger), mixati sotto la voce: volume regolabile in "Impostazioni avanzate".

`style_refs/` contiene le immagini di riferimento dello stile (non vanno su Git). Mettici fotogrammi del tuo stile
preferito, ritagliati senza testi; usa riferimenti tuoi o di cui hai i diritti se pubblichi i video.

Altri stili: **Stile reel** (illustrazioni ferme dai riferimenti), **Cartoon**, **Schizzo a matita**, **Fumetto**.

**Installazione (una volta):** `installa_disegni.bat` (PyTorch CUDA, Stable Diffusion, IP-Adapter, voci locali: ~9 GB).
Sulla GTX 1070 il modello di testo e quello di disegno non stanno insieme in 8 GB: l'app scarica LM Studio
durante il disegno (`lms unload`) e lo ricarica alla fine con la sua configurazione.

### Voce

Nel passo 3 scegli il **motore**:

- **Voce del PC (gratis, locale)**: Kokoro (Sara, Nicola), Piper (Paola), voci di Windows; Edge (online) come alternativa.
  Il testo viene preparato per la lettura: numeri, euro, percentuali, date, orari e sigle in parole, pause naturali fra
  le frasi (più lunghe dopo domande e puntini).
- **ElevenLabs (online)**: la più realistica. Incolla la chiave API nella pagina (resta in `secrets/`). Ogni beat viene letto
  con il testo vicino come contesto, così l'intonazione resta continua. Il piano gratuito (~10 minuti al mese) **non**
  consente l'uso commerciale e chiede di citare ElevenLabs; per video monetizzati serve almeno Starter. Se i crediti
  finiscono il video continua con la voce del PC.

**Voce automatica**: "Automatica" sceglie una voce maschile o femminile in base a chi racconta (lo dichiara il modello nel
concept, altrimenti si capisce dalle concordanze: "sono rimasta" / "sono rimasto"). Il pulsante **Ascolta** fa sentire
ogni voce prima di scegliere.


## Pubblicazione (passo 4)

Le credenziali restano sul PC in `secrets/` (escluso da Git). Si configurano una volta dalla pagina.

**YouTube** - in Google Cloud Console: crea un progetto, attiva *YouTube Data API v3*, nella schermata di consenso
OAuth scegli "Esterno" e aggiungi il tuo account tra gli utenti di test, crea un *ID client OAuth* di tipo
**App desktop**. Incolla Client ID e Client secret nella pagina e premi *Collega YouTube*.
Limiti di Google: finché il progetto non supera la verifica, i video caricati via API restano **privati** (li rendi
pubblici da YouTube Studio); la quota gratuita consente circa 6 caricamenti al giorno. L'app dichiara i contenuti
come generati con AI.

**TikTok** - su developers.tiktok.com: crea un'app **Desktop**, aggiungi *Login Kit* e *Content Posting API*,
registra il Redirect URI `http://127.0.0.1:8000/api/publish/tiktok/callback` e aggiungi il tuo account tra i tester.
Incolla Client key e Client secret e premi *Collega TikTok*.
Due modi: **Bozze nell'app** (consigliato: le clip arrivano nella casella dell'app TikTok e le pubblichi con un tocco)
oppure **Pubblica subito**, che finché TikTok non approva l'app pubblica solo in privato. Le clip sono marcate come
contenuto AI.

### Controlli

- **Durata / Parti TikTok**: lunghezza totale e numero di clip (ognuna diventa un video separato).
- **Formato**: racconto in prima persona oppure divulgativo.
- **Velocità parlato (WPM)**: velocità della voce e stima delle durate.
- **Durata max sottotitolo**: quanto resta a schermo ogni blocco di sottotitolo.
- **Soglia taglio silenzi**: i silenzi iniziali/finali di ogni beat più lunghi di questo valore vengono rifilati.
- **Voce**: le voci *neurali* (Elsa, Diego, Isabella, Giuseppe) usano il servizio online di Microsoft Edge e inviano il testo a Microsoft;
  le voci *Windows* sono offline. Senza Internet si passa automaticamente alla voce di Windows.

## Configurazione (`.env`)

```env
LMSTUDIO_BASE_URL=http://127.0.0.1:1234/v1
LMSTUDIO_MODEL=huihui-qwen3.6-35b-a3b-abliterated-mtp
LMSTUDIO_THINKING=off        # off = niente ragionamento nascosto (molto più veloce)
LMSTUDIO_MAX_TOKENS=12000
LMSTUDIO_IDLE_TIMEOUT=300
LMSTUDIO_TOTAL_TIMEOUT=3600
```

Il modello si può cambiare anche dal menu nella pagina. Con `LMSTUDIO_THINKING=off` l'app invia
`reasoning_effort: "none"`: sui modelli Qwen3.x questo elimina il ragionamento nascosto che rendeva ogni chiamata
lenta (minuti) e spesso vuota.

## Ottimizzazione LM Studio per GTX 1070 8 GB + Ryzen 9 5950X + 32 GB

Impostazioni di caricamento del modello `huihui-qwen3.6-35b-a3b-abliterated-mtp` (MoE 35B, 3B attivi), misurate sul tuo PC:

| Impostazione | Valore |
|---|---|
| GPU Offload | massimo (tutti i layer) |
| Pesi degli esperti sulla CPU | 80% (`numCpuExpertLayersRatio` 0.8) |
| Context length | 32768 |
| Flash Attention | attivo |
| K/V cache | q8_0 |
| CPU threads | 12 |
| Speculative decoding MTP | attivo, 2 token di bozza |

Risultato: circa **30 token/s** contro i 17,6 della configurazione precedente (32% dei layer su GPU, contesto 90k),
con 7,5 GB di VRAM occupata. Non salire oltre: al 75% la VRAM arriva a 8 GB e qualsiasi altra app la fa traboccare.

## Struttura

```
app/main.py                  server FastAPI
app/api/routes.py            API REST usate dalla pagina
app/core/pipeline.py         generazione del copione (LLM)
app/core/render_pipeline.py  voce + video
app/core/jobs.py             un lavoro alla volta, annullabile
app/core/projects.py         cartelle dei progetti
app/services/llm_service.py  client LM Studio (streaming, thinking off, JSON tollerante)
app/services/tts_service.py  voci Edge neurali / Windows
app/services/video_service.py render FFmpeg + Pillow (sfondi, sottotitoli in basso)
app/services/illustration_service.py scene descritte da LM Studio e disegnate in locale
app/services/sd_worker.py   Stable Diffusion in un processo separato (GPU)
app/services/publish_service.py YouTube Data API e TikTok Content Posting API (OAuth)
app/services/toon.py        personaggi cartoon disegnati e animati (bocca a tempo con la voce)
app/services/audio_fx.py    musica ed effetti sonori sintetizzati, mix sotto la voce
app/services/eleven_service.py voce ElevenLabs (online, facoltativa)
app/services/text_norm.py   testo italiano pronto per la voce, genere di chi racconta
app/web/index.html           interfaccia
legacy/                      versione precedente, non usata
```

Test: `.venv\Scripts\python.exe -m pytest tests -q`
