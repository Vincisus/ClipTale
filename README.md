# TiktokFactory Studio

App locale per creare video verticali 9:16 (YouTube Shorts / TikTok) partendo da un'idea:
il modello in **LM Studio** scrive concept, parti e copione; tu lo rifinisci nell'editor dei beat;
l'app genera **voce, sottotitoli, video completo e una clip per ogni parte**.

## Avvio

1. Apri **LM Studio**, carica il modello e avvia il server locale (Developer → Start server, porta 1234).
2. Doppio clic su **`Avvia.bat`**. La prima volta crea `.venv` e installa le dipendenze.
3. Il browser si apre su <http://127.0.0.1:8000/>.

Servono Python 3.12 e LM Studio. FFmpeg è incluso tramite `imageio-ffmpeg`; se ne hai uno nel PATH viene usato quello.

## Flusso di lavoro

| Passo | Cosa succede |
|---|---|
| **1. Genera Script** | concept → struttura in N parti → testo di ogni parte diviso in beat → titoli, caption, hashtag. Circa 3 + N chiamate al modello. |
| Editor beat | Modifica testo e "visual cue" di ogni beat; il salvataggio è automatico. Puoi aggiungere o eliminare beat. |
| **2. Render MP4** | voce per ogni beat (con cache) → taglio silenzi → sottotitoli tempati sull'audio reale → `render/tiktok/part_XX.mp4` + `render/full_video.mp4` + `render/subtitles.srt` |

Se un lavoro si interrompe, seleziona il progetto e premi **Riprendi Script**: riparte dal primo passo mancante.
Tutto è salvato in `projects/<id>/`.

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
app/services/video_service.py render FFmpeg + Pillow
app/web/index.html           interfaccia
legacy/                      versione precedente, non usata
```

Test: `.venv\Scripts\python.exe -m pytest tests -q`
