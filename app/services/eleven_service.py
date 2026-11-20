"""Voce ElevenLabs (online): la più naturale, con il limite di caratteri del proprio piano.

- La chiave API resta sul PC in secrets/elevenlabs.json (escluso da Git).
- Il testo delle storie viene inviato a ElevenLabs.
- Il piano gratuito (~10.000 caratteri al mese) NON consente l'uso commerciale (video monetizzati) e chiede
  di citare ElevenLabs; per i video monetizzati serve almeno il piano Starter.
- Ogni beat viene letto con il testo precedente e successivo come contesto (previous_text / next_text):
  l'intonazione resta continua fra un beat e l'altro.
"""
import asyncio
import json
import time
from pathlib import Path

import httpx

from app.core.paths import SECRETS
API = "https://api.elevenlabs.io/v1"
MODEL = "eleven_multilingual_v2"
_cache = {"voices": None, "at": 0}


class QuotaError(RuntimeError):
    """Crediti finiti o chiave rifiutata: si torna alla voce del PC."""


def _key():
    try:
        return json.loads((SECRETS / "elevenlabs.json").read_text(encoding="utf-8")).get("api_key", "")
    except (OSError, ValueError):
        return ""


def save_key(key):
    SECRETS.mkdir(parents=True, exist_ok=True)
    if key:
        (SECRETS / "elevenlabs.json").write_text(json.dumps({"api_key": key.strip()}), encoding="utf-8")
    else:
        (SECRETS / "elevenlabs.json").unlink(missing_ok=True)
    _cache.update(voices=None, at=0)


def configured():
    return bool(_key())


def _headers():
    return {"xi-api-key": _key(), "Accept": "application/json"}


async def status(transport=None):
    """{'configured', 'ok', 'used', 'limit', 'tier', 'error'}"""
    if not configured():
        return {"configured": False, "ok": False}
    try:
        async with httpx.AsyncClient(timeout=15, transport=transport) as c:
            r = await c.get(f"{API}/user/subscription", headers=_headers())
        if r.status_code == 401:
            try:
                detail = r.json().get("detail") or {}
            except ValueError:
                detail = {}
            if isinstance(detail, dict) and detail.get("status") == "missing_permissions":
                return {"configured": True, "ok": False, "error": "la chiave non ha i permessi necessari: su elevenlabs.io → API Keys abilita Text to Speech, Voices (lettura), Models (lettura) e User (lettura)"}
            return {"configured": True, "ok": False, "error": "chiave rifiutata"}
        r.raise_for_status()
        d = r.json()
        return {"configured": True, "ok": True, "used": d.get("character_count", 0), "limit": d.get("character_limit", 0), "tier": d.get("tier", "")}
    except Exception as exc:
        return {"configured": True, "ok": False, "error": type(exc).__name__}


async def voices(transport=None):
    """Voci dell'account: [{'id', 'name', 'gender', 'label'}] (in cache 10 minuti)."""
    if not configured():
        return []
    if _cache["voices"] is not None and time.time() - _cache["at"] < 600:
        return _cache["voices"]
    try:
        async with httpx.AsyncClient(timeout=20, transport=transport) as c:
            r = await c.get(f"{API}/voices", headers=_headers())
            r.raise_for_status()
            sub = await c.get(f"{API}/user/subscription", headers=_headers())
        tier = sub.json().get("tier", "") if sub.status_code == 200 else ""
    except Exception:
        return _cache["voices"] or []
    out = []
    for v in r.json().get("voices", []):
        labels = v.get("labels") or {}
        gender = {"female": "F", "male": "M"}.get(str(labels.get("gender", "")).lower(), "")
        lang = str(labels.get("language") or labels.get("locale") or "").lower()[:2]
        if not lang and str(labels.get("accent", "")).lower() in ("american", "british", "australian", "irish", "canadian"):
            lang = "en"
        italian = lang == "it" or "ital" in str(labels.get("accent", "")).lower()
        lang = "it" if italian else lang
        accent = "italiano" if italian else labels.get("accent")
        extra = ", ".join(x for x in (accent, labels.get("age"), labels.get("description") or labels.get("descriptive")) if x)
        # Le voci copiate dalla Voice Library non si possono usare via API con il piano gratuito (errore 402).
        library = v.get("category") == "professional" or (v.get("sharing") or {}).get("status") == "copied"
        paid_only = library and tier == "free"
        out.append({"id": v["voice_id"], "name": v.get("name", "?"), "gender": gender, "italian": italian, "lang": lang, "paid_only": paid_only,
                    "label": f"{v.get('name', '?')} ({'donna' if gender == 'F' else 'uomo' if gender == 'M' else 'voce'}{', ' + extra if extra else ''})"
                             + (" · serve piano a pagamento" if paid_only else "")})
    out.sort(key=lambda v: (v["paid_only"], not v["italian"], v["gender"] != "F", v["name"]))
    _cache.update(voices=out, at=time.time())
    return out


def pick_for_gender(voice_list, gender, lang="it"):
    """La prima voce dell'account del genere giusto; quelle della lingua del video hanno la precedenza.

    Il modello multilingue legge qualsiasi lingua con qualsiasi voce, ma l'accento è più naturale
    se la voce è nata in quella lingua.
    """
    wanted = "M" if gender == "M" else "F"
    voice_list = [v for v in voice_list if not v.get("paid_only")]
    same = [v for v in voice_list if v["gender"] == wanted] or voice_list
    if lang == "it":
        native = [v for v in same if v.get("italian") or "ital" in v["label"].lower()]
    else:
        native = [v for v in same if v.get("lang") == lang]
    return (native or same)[0]["id"] if same else None


async def synthesize(voice_id, items, speed=1.0, transport=None, on_progress=None):
    """items: [(testo, percorso)]. Crea file .mp3; QuotaError se i crediti sono finiti o la chiave non vale."""
    created = []
    texts = [t for t, _ in items]
    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15), transport=transport) as c:
        for i, (text, path) in enumerate(items):
            path = Path(path).with_suffix(".mp3")
            path.parent.mkdir(parents=True, exist_ok=True)
            body = {
                "text": text, "model_id": MODEL,
                "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.25, "use_speaker_boost": True,
                                   "speed": max(0.7, min(1.2, speed))},
                "previous_text": texts[i - 1][-600:] if i else None,
                "next_text": texts[i + 1][:600] if i + 1 < len(texts) else None,
            }
            for attempt in range(3):
                r = await c.post(f"{API}/text-to-speech/{voice_id}", params={"output_format": "mp3_44100_128"},
                                 headers={**_headers(), "Accept": "audio/mpeg"}, json=body)
                if r.status_code == 200:
                    break
                detail = r.text[:300]
                if r.status_code in (401, 402) or "quota" in detail.lower() or "credits" in detail.lower():
                    raise QuotaError(f"ElevenLabs: {detail}")
                if r.status_code == 429 or r.status_code >= 500:
                    await asyncio.sleep(3 * (attempt + 1))
                    continue
                raise RuntimeError(f"ElevenLabs ha rifiutato il testo ({r.status_code}): {detail}")
            else:
                raise RuntimeError(f"ElevenLabs non risponde ({r.status_code})")
            path.write_bytes(r.content)
            created.append(path)
            if on_progress:
                on_progress(f"ElevenLabs: beat {i + 1}/{len(items)}")
    return created
