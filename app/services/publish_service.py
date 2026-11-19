"""Pubblicazione: video lungo su YouTube, clip su TikTok, con le API ufficiali e OAuth.

Le credenziali restano sul PC in secrets/ (escluso da Git):
- secrets/youtube_client.json  client_id e client_secret di un client OAuth "App desktop" (Google Cloud, YouTube Data API v3)
- secrets/youtube_token.json   refresh token dopo "Collega YouTube"
- secrets/tiktok_app.json      client_key e client_secret di un'app TikTok for Developers (Login Kit + Content Posting API)
- secrets/tiktok_token.json    token dopo "Collega TikTok"

Limiti delle piattaforme (non aggirabili dall'app):
- YouTube: i progetti API non verificati da Google caricano i video come PRIVATI; si rendono pubblici da YouTube Studio
  oppure dopo l'audit del progetto. Quota gratuita: circa 6 caricamenti al giorno.
- TikTok: le app non revisionate possono pubblicare direttamente solo in privato (SELF_ONLY). La modalità "bozza" manda
  le clip nella casella dell'app TikTok, da cui si pubblicano con un tocco: è la via consigliata finché l'app non è approvata.
"""
import asyncio
import base64
import hashlib
import json
import math
import os
import secrets as pysecrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

ROOT = Path(__file__).resolve().parents[2]
SECRETS = ROOT / "secrets"
BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

YT_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
YT_TOKEN = "https://oauth2.googleapis.com/token"
YT_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
YT_SCOPE = "https://www.googleapis.com/auth/youtube.upload"

TT_AUTH = "https://www.tiktok.com/v2/auth/authorize/"
TT_API = "https://open.tiktokapis.com/v2"
TT_SCOPE = "user.info.basic,video.upload,video.publish"

MB = 1024 * 1024
_pending = {}  # state OAuth -> (piattaforma, code_verifier, scadenza)


def _read(name):
    try:
        return json.loads((SECRETS / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(name, data):
    SECRETS.mkdir(parents=True, exist_ok=True)
    (SECRETS / name).write_text(json.dumps(data, indent=1), encoding="utf-8")


def redirect_uri(platform):
    return os.getenv(f"{platform.upper()}_REDIRECT_URI") or f"{BASE_URL}/api/publish/{platform}/callback"


def status():
    yc, yt = _read("youtube_client.json"), _read("youtube_token.json")
    tc, tt = _read("tiktok_app.json"), _read("tiktok_token.json")
    return {
        "youtube": {"configured": bool(yc.get("client_id")), "connected": bool(yt.get("refresh_token")), "account": yt.get("account", ""), "redirect_uri": redirect_uri("youtube")},
        "tiktok": {"configured": bool(tc.get("client_key")), "connected": bool(tt.get("refresh_token")), "account": tt.get("account", ""), "redirect_uri": redirect_uri("tiktok")},
    }


def save_app(platform, key, secret):
    if platform == "youtube":
        _write("youtube_client.json", {"client_id": key.strip(), "client_secret": secret.strip()})
        (SECRETS / "youtube_token.json").unlink(missing_ok=True)
    else:
        _write("tiktok_app.json", {"client_key": key.strip(), "client_secret": secret.strip()})
        (SECRETS / "tiktok_token.json").unlink(missing_ok=True)


def disconnect(platform):
    (SECRETS / f"{platform}_token.json").unlink(missing_ok=True)


# ------------------------------------------------------------------ OAuth
def auth_url(platform):
    """URL della pagina di consenso (YouTube o TikTok), con PKCE e state monouso."""
    now = time.time()
    for k in [k for k, v in _pending.items() if v[2] < now]:
        _pending.pop(k, None)
    state = pysecrets.token_urlsafe(24)
    verifier = pysecrets.token_urlsafe(48)
    _pending[state] = (platform, verifier, now + 900)
    if platform == "youtube":
        app = _read("youtube_client.json")
        if not app.get("client_id"):
            raise ValueError("Prima salva Client ID e Client secret di Google.")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        return YT_AUTH + "?" + urlencode({
            "client_id": app["client_id"], "redirect_uri": redirect_uri("youtube"), "response_type": "code", "scope": YT_SCOPE,
            "access_type": "offline", "prompt": "consent", "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        })
    app = _read("tiktok_app.json")
    if not app.get("client_key"):
        raise ValueError("Prima salva Client key e Client secret di TikTok.")
    # TikTok (app desktop) vuole la SHA256 del verifier in esadecimale
    challenge = hashlib.sha256(verifier.encode()).hexdigest()
    return TT_AUTH + "?" + urlencode({
        "client_key": app["client_key"], "scope": TT_SCOPE, "response_type": "code", "redirect_uri": redirect_uri("tiktok"),
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    })


async def finish_auth(platform, code, state, transport=None):
    entry = _pending.pop(state or "", None)
    if not entry or entry[0] != platform or entry[2] < time.time():
        raise ValueError("Collegamento scaduto o non valido: riprova da TiktokFactory.")
    verifier = entry[1]
    async with httpx.AsyncClient(timeout=30, transport=transport) as c:
        if platform == "youtube":
            app = _read("youtube_client.json")
            r = await c.post(YT_TOKEN, data={"code": code, "client_id": app["client_id"], "client_secret": app["client_secret"],
                                              "redirect_uri": redirect_uri("youtube"), "grant_type": "authorization_code", "code_verifier": verifier})
            data = r.json()
            if r.status_code != 200 or not data.get("refresh_token"):
                raise ValueError(f"Google ha rifiutato il collegamento: {data.get('error_description') or data.get('error') or r.status_code}")
            _write("youtube_token.json", {"refresh_token": data["refresh_token"], "account": "canale collegato"})
        else:
            app = _read("tiktok_app.json")
            r = await c.post(f"{TT_API}/oauth/token/", data={"client_key": app["client_key"], "client_secret": app["client_secret"], "code": code,
                                                             "grant_type": "authorization_code", "redirect_uri": redirect_uri("tiktok"), "code_verifier": verifier})
            data = r.json()
            if r.status_code != 200 or not data.get("refresh_token"):
                raise ValueError(f"TikTok ha rifiutato il collegamento: {data.get('error_description') or data.get('error') or r.status_code}")
            token = {"refresh_token": data["refresh_token"], "access_token": data.get("access_token"), "expires_at": time.time() + int(data.get("expires_in", 0)) - 60, "open_id": data.get("open_id")}
            try:
                me = await c.get(f"{TT_API}/user/info/", params={"fields": "display_name"}, headers={"Authorization": f"Bearer {token['access_token']}"})
                token["account"] = me.json().get("data", {}).get("user", {}).get("display_name", "")
            except Exception:
                token["account"] = ""
            _write("tiktok_token.json", token)


async def _youtube_access(c):
    app, tok = _read("youtube_client.json"), _read("youtube_token.json")
    if not tok.get("refresh_token"):
        raise RuntimeError("YouTube non collegato: premi Collega YouTube nella sezione Pubblica.")
    r = await c.post(YT_TOKEN, data={"client_id": app["client_id"], "client_secret": app["client_secret"], "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
    if r.status_code != 200:
        raise RuntimeError(f"Accesso a YouTube scaduto ({r.json().get('error', r.status_code)}): ricollega il canale.")
    return r.json()["access_token"]


async def _tiktok_access(c):
    app, tok = _read("tiktok_app.json"), _read("tiktok_token.json")
    if not tok.get("refresh_token"):
        raise RuntimeError("TikTok non collegato: premi Collega TikTok nella sezione Pubblica.")
    if tok.get("access_token") and tok.get("expires_at", 0) > time.time():
        return tok["access_token"]
    r = await c.post(f"{TT_API}/oauth/token/", data={"client_key": app["client_key"], "client_secret": app["client_secret"],
                                                     "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    data = r.json()
    if r.status_code != 200 or not data.get("access_token"):
        raise RuntimeError(f"Accesso a TikTok scaduto ({data.get('error_description') or data.get('error')}): ricollega l'account.")
    tok.update(access_token=data["access_token"], refresh_token=data.get("refresh_token", tok["refresh_token"]), expires_at=time.time() + int(data.get("expires_in", 0)) - 60)
    _write("tiktok_token.json", tok)
    return tok["access_token"]


# ------------------------------------------------------------------ testi
def short_title(text, limit=100):
    """Titolo entro il limite di YouTube, tagliato su una parola intera e chiuso con «...»."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 3].rsplit(" ", 1)[0].rstrip(",;:.-'\"«")
    return cut + "..."


def fmt_ts(seconds):
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02}:{s % 60:02}" if s >= 3600 else f"{s // 60:02}:{s % 60:02}"


def youtube_texts(folder):
    """Titolo, descrizione con capitoli (00:00 Parte 1 …) e tag del video lungo, dai file del progetto."""
    from app.core.projects import load_json
    folder = Path(folder)
    meta = load_json(folder / "metadata.json", {}) or {}
    concept = load_json(folder / "concept.json", {}) or {}
    structure = load_json(folder / "structure.json", {}) or {}
    render = load_json(folder / "render" / "render.json", {}) or {}
    title = short_title(meta.get("youtube_title") or (meta.get("titles") or [concept.get("title", "Storia")])[0])
    lines = [meta.get("description", "").strip()]
    titles = {p.get("part"): p.get("title", "") for p in structure.get("parts", [])}
    starts = {}
    for b in render.get("beats", []):
        starts.setdefault(b["part"], b["start"])
    if len(starts) >= 3:  # YouTube mostra i capitoli solo con almeno 3 voci, la prima a 00:00
        lines += ["", "Capitoli:"] + [f"{fmt_ts(0 if i == 0 else starts[p])} Parte {p}{' — ' + titles[p] if titles.get(p) else ''}" for i, p in enumerate(sorted(starts))]
    hashtags = meta.get("hashtags") or []
    if hashtags:
        lines += ["", " ".join(hashtags[:8])]
    footer = "Voce e immagini generate con intelligenza artificiale."
    lines += ["", footer if "fantasia" in lines[0].lower() else "Storia di fantasia. " + footer]
    tags = [h.lstrip("#") for h in hashtags][:15]
    return {"title": title, "description": "\n".join(lines).strip()[:4900], "tags": tags}


def tiktok_captions(folder):
    from app.core.projects import load_json
    folder = Path(folder)
    meta = load_json(folder / "metadata.json", {}) or {}
    render = load_json(folder / "render" / "render.json", {}) or {}
    clips = render.get("clips") or sorted(p.relative_to(folder).as_posix() for p in (folder / "render" / "tiktok").glob("part_*.mp4"))
    caps = meta.get("part_captions") or []
    tags = " ".join((meta.get("hashtags") or [])[:6])
    out = []
    for i, clip in enumerate(clips):
        base = caps[i] if i < len(caps) else f"{meta.get('tiktok_caption', '')} Parte {i + 1}".strip()
        out.append({"clip": clip, "caption": f"{base} {tags}".strip()[:2000]})
    return out


# ------------------------------------------------------------------ caricamenti
async def upload_youtube(path, title, description, tags, privacy="private", log=print, transport=None):
    path = Path(path)
    size = path.stat().st_size
    body = {
        "snippet": {"title": short_title(title), "description": description[:5000], "tags": tags, "categoryId": "24", "defaultLanguage": "it", "defaultAudioLanguage": "it"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=20), transport=transport) as c:
        token = await _youtube_access(c)
        r = await c.post(YT_UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"}, json=body,
                         headers={"Authorization": f"Bearer {token}", "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
        if r.status_code != 200 or "location" not in r.headers:
            raise RuntimeError(f"YouTube ha rifiutato il caricamento: {r.text[:300]}")
        url = r.headers["location"]
        chunk = 32 * MB
        sent = 0
        with open(path, "rb") as fh:
            while sent < size:
                data = fh.read(chunk)
                end = sent + len(data) - 1
                r = await c.put(url, content=data, headers={"Authorization": f"Bearer {token}", "Content-Length": str(len(data)), "Content-Range": f"bytes {sent}-{end}/{size}"})
                if r.status_code in (200, 201):
                    vid = r.json().get("id")
                    log(f"YouTube: caricato ({size / MB:.0f} MB) → https://youtu.be/{vid}")
                    return {"id": vid, "url": f"https://youtu.be/{vid}", "privacy": privacy}
                if r.status_code != 308:
                    raise RuntimeError(f"YouTube: caricamento interrotto ({r.status_code}): {r.text[:300]}")
                sent = int(r.headers.get("range", f"bytes=0-{end}").split("-")[-1]) + 1
                log(f"YouTube: {sent * 100 // size}% caricato")
    raise RuntimeError("YouTube: caricamento non completato")


def tiktok_chunks(size):
    """Regole TikTok: blocchi da 5 a 64 MB, l'ultimo può arrivare a 128 MB; sotto i 5 MB un blocco unico.

    Con un solo blocco TikTok vuole chunk_size uguale all'intero file ("The chunk size is invalid" altrimenti):
    fino a 64 MB si invia quindi tutto in un blocco; oltre, blocchi da 32 MB con l'ultimo che assorbe il resto.
    """
    if size <= 64 * MB:
        return size, 1
    chunk = 32 * MB
    return chunk, math.floor(size / chunk)


async def upload_tiktok(path, caption, mode="draft", log=print, transport=None):
    """mode: 'draft' = nella casella dell'app TikTok (si pubblica dall'app); 'direct' = pubblicazione diretta."""
    path = Path(path)
    size = path.stat().st_size
    chunk, count = tiktok_chunks(size)
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=20), transport=transport) as c:
        token = await _tiktok_access(c)
        auth = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
        def failed(resp):
            try:
                err = resp.json().get("error", {})
            except ValueError:
                err = {}
            code = err.get("code", "ok")
            return (resp.status_code != 200 or code != "ok"), code, err.get("message", "")

        r, privacy = None, "bozza"
        if mode == "direct":
            info = (await c.post(f"{TT_API}/post/publish/creator_info/query/", headers=auth)).json().get("data", {})
            options = info.get("privacy_level_options") or ["SELF_ONLY"]
            # prima pubblico; le app non ancora revisionate da TikTok possono solo privato (SELF_ONLY)
            tries = (["PUBLIC_TO_EVERYONE"] if "PUBLIC_TO_EVERYONE" in options else []) + (["SELF_ONLY"] if "SELF_ONLY" in options else options[:1])
            for privacy in dict.fromkeys(tries):
                body = {"post_info": {"title": caption[:2200], "privacy_level": privacy, "disable_comment": False, "disable_duet": False,
                                      "disable_stitch": False, "is_aigc": True}, "source_info": source}
                r = await c.post(f"{TT_API}/post/publish/video/init/", headers=auth, json=body)
                bad, code, _ = failed(r)
                if not bad:
                    break
                if code not in ("unaudited_client_can_only_post_to_private_accounts", "privacy_level_option_mismatch"):
                    break
                log(f"TikTok non accetta la visibilità {privacy} per un'app non ancora revisionata ({code})")
            bad, code, _ = failed(r)
            if bad and code in ("unaudited_client_can_only_post_to_private_accounts", "privacy_level_option_mismatch"):
                # account pubblico + app non revisionata: la pubblicazione diretta è bloccata, si passa alle bozze
                log("Pubblicazione diretta non consentita finché TikTok non approva la tua app: invio la clip come bozza nell'app TikTok")
                mode, r, privacy = "draft", None, "bozza"
        if r is None:
            r = await c.post(f"{TT_API}/post/publish/inbox/video/init/", headers=auth, json={"source_info": source})
        bad, code, message = failed(r)
        if bad:
            hint = {
                "unaudited_client_can_only_post_to_private_accounts": "app non ancora approvata da TikTok: usa Bozze nell'app",
                "scope_not_authorized": "manca un permesso: scollega e ricollega TikTok accettando tutti i permessi",
                "access_token_invalid": "accesso scaduto: ricollega TikTok",
                "spam_risk_too_many_posts": "hai raggiunto il limite giornaliero di post di TikTok",
                "rate_limit_exceeded": "troppe richieste ravvicinate: riprova fra qualche minuto",
            }.get(code, "")
            raise RuntimeError(f"TikTok ha rifiutato la clip [{code or r.status_code}]{': ' + hint if hint else ''} ({message[:160]})")
        data = r.json()
        upload_url, publish_id = data["data"]["upload_url"], data["data"]["publish_id"]
        with open(path, "rb") as fh:
            for i in range(count):
                start = i * chunk
                length = size - start if i == count - 1 else chunk
                fh.seek(start)
                part = fh.read(length)
                r = await c.put(upload_url, content=part, headers={"Content-Type": "video/mp4", "Content-Length": str(length),
                                                                   "Content-Range": f"bytes {start}-{start + length - 1}/{size}"})
                if r.status_code not in (200, 201, 206):
                    raise RuntimeError(f"TikTok: invio interrotto ({r.status_code}) {r.text[:200]}")
        state = "PROCESSING_UPLOAD"
        for _ in range(40):
            await asyncio.sleep(3)
            s = (await c.post(f"{TT_API}/post/publish/status/fetch/", headers=auth, json={"publish_id": publish_id})).json().get("data", {})
            state = s.get("status", state)
            if state in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE", "FAILED"):
                break
        if state == "FAILED":
            raise RuntimeError(f"TikTok: elaborazione fallita ({s.get('fail_reason', 'motivo sconosciuto')})")
        label = {"SEND_TO_USER_INBOX": "nella casella dell'app TikTok", "PUBLISH_COMPLETE": f"pubblicata ({privacy})"}.get(state, f"in elaborazione ({state})")
        log(f"TikTok: {path.name} {label}")
        return {"publish_id": publish_id, "status": state, "privacy": privacy}


async def publish_project(folder, job_log, youtube=None, tiktok=None):
    """youtube={'title','description','tags','privacy'}; tiktok={'mode', 'captions': [..]}. Salva l'esito in publish.json."""
    from app.core.projects import load_json, save_json
    folder = Path(folder)
    record = load_json(folder / "publish.json", {}) or {}
    errors = []
    if youtube is not None:
        video = folder / "render" / "full_video.mp4"
        try:
            job_log("YouTube: caricamento del video lungo…", 5)
            texts = youtube_texts(folder)
            res = await upload_youtube(video, youtube.get("title") or texts["title"], youtube.get("description") or texts["description"],
                                       youtube.get("tags") or texts["tags"], youtube.get("privacy", "private"), log=lambda m: job_log(m, None))
            record["youtube"] = {**res, "at": time.strftime("%Y-%m-%d %H:%M")}
        except Exception as exc:
            errors.append(str(exc))
            job_log(f"YouTube: {exc}", None, "error")
    if tiktok is not None:
        items = tiktok_captions(folder)
        captions = tiktok.get("captions") or []
        results = []
        for i, item in enumerate(items):
            caption = captions[i] if i < len(captions) and captions[i] else item["caption"]
            try:
                job_log(f"TikTok: clip {i + 1}/{len(items)}…", 50 + 45 * i / max(1, len(items)))
                res = await upload_tiktok(folder / item["clip"], caption, tiktok.get("mode", "draft"), log=lambda m: job_log(m, None))
                results.append({"clip": item["clip"], **res})
            except Exception as exc:
                errors.append(str(exc))
                job_log(f"TikTok clip {i + 1}: {exc}", None, "error")
                results.append({"clip": item["clip"], "error": str(exc)})
            if i < len(items) - 1:
                await asyncio.sleep(12)  # TikTok accetta pochi avvii di caricamento al minuto
        record["tiktok"] = {"items": results, "mode": tiktok.get("mode", "draft"), "at": time.strftime("%Y-%m-%d %H:%M")}
    save_json(folder / "publish.json", record)
    if errors and len(errors) == (youtube is not None) + len(tiktok_captions(folder) if tiktok is not None else []):
        raise RuntimeError("Pubblicazione non riuscita: " + errors[0])
    return record
