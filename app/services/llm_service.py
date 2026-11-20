"""Client per LM Studio (API compatibile OpenAI), pensato per modelli locali con 'thinking' (Qwen3.x).

- streaming: il timeout scatta solo se il modello resta muto, non se sta ragionando a lungo;
- il ragionamento viene disattivato quando possibile (LMSTUDIO_THINKING=off) perche, sul PC locale,
  moltiplica per 5-20 i tempi senza migliorare la narrazione;
- il JSON e vincolato da json_schema quando il server lo supporta, poi riparato in modo tollerante.
"""
import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

from app.core.paths import ENV_FILE, PROJECTS

logger = logging.getLogger("uvicorn.error")
THINK_RE = re.compile(r"<think>.*?(</think>|$)", re.DOTALL)


def env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


# Velocità dell'ultima chiamata al modello (token al secondo), letta dalla pagina durante i lavori.
LIVE = {"tps": None, "tokens": 0, "model": None, "at": 0.0, "streaming": False}


def live_stats(max_age=30):
    """{'tps', 'tokens', 'model', 'active'} dell'ultima generazione, o None se è troppo vecchia."""
    if LIVE["tps"] is None or time.monotonic() - LIVE["at"] > max_age:
        return None
    return {"tps": LIVE["tps"], "tokens": LIVE["tokens"], "model": LIVE["model"],
            "active": LIVE["streaming"] and time.monotonic() - LIVE["at"] < 5}


class LLMError(RuntimeError):
    pass


def spec_needs_key(provider):
    from app.services import ai_providers
    return bool(ai_providers.PROVIDERS.get(provider, {}).get("key"))


class LLMService:
    """Client per il motore AI scelto (vedi ai_providers): LM Studio, Ollama, OpenAI, Gemini (formato OpenAI) o Claude (SDK)."""

    def __init__(self, model=None, transport=None, provider=None):
        from app.services import ai_providers
        load_dotenv(ENV_FILE)
        config = ai_providers.load()
        self.provider = provider or config["provider"]
        spec = ai_providers.PROVIDERS[self.provider]
        if self.provider == "lmstudio":
            self.base_url = os.getenv("LMSTUDIO_BASE_URL", spec["base"]).rstrip("/")
            self.api_key = os.getenv("LMSTUDIO_API_KEY", "lm-studio")
            default_model = os.getenv("LMSTUDIO_MODEL", "")
        else:
            env_base = {"ollama": "OLLAMA_BASE_URL", "unsloth": "UNSLOTH_BASE_URL"}.get(self.provider)
            self.base_url = (os.getenv(env_base, spec["base"]) if env_base else spec.get("base", "")).rstrip("/")
            self.api_key = config["keys"].get(self.provider, "ollama" if self.provider == "ollama" else "")
            default_model = config["models"].get(self.provider) or spec.get("default_model", "")
        host = urlparse(self.base_url).hostname if self.base_url else "api.anthropic.com"
        if host not in ("127.0.0.1", "localhost", "::1") and host not in ai_providers.CLOUD_HOSTS:
            raise ValueError("Sono consentiti solo server locali (127.0.0.1 / localhost) o i servizi cloud previsti.")
        self.label = spec["label"]
        self.local = bool(spec.get("local"))
        self.model = (model or default_model).strip().strip("'\"")
        self.thinking = os.getenv("LMSTUDIO_THINKING", "off").strip().lower() not in ("off", "0", "false", "no")
        self.max_tokens = env_int("LMSTUDIO_MAX_TOKENS", 12000)
        self.idle_timeout = env_int("LMSTUDIO_IDLE_TIMEOUT", 300)
        self.total_timeout = env_int("LMSTUDIO_TOTAL_TIMEOUT", 3600)
        self.transport = transport
        self.diagnostic_dir = Path(os.getenv("LLM_DIAGNOSTIC_DIR") or PROJECTS / "_diagnostics")
        self.on_activity = None  # callback(str) per mostrare l'avanzamento nella UI

    # ------------------------------------------------------------------ server
    @property
    def server_root(self):
        return self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url

    async def list_models(self):
        """Modelli del motore scelto, con lo stato di caricamento quando il motore lo dice (LM Studio, Ollama)."""
        if self.provider == "anthropic":
            from app.services import anthropic_provider
            if not self.api_key:
                raise LLMError("manca la chiave API di Claude")
            return await anthropic_provider.list_models(self.api_key)
        if spec_needs_key(self.provider) and not self.api_key:
            raise LLMError(f"manca la chiave API di {self.label}")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.provider == "ollama":
            async with httpx.AsyncClient(timeout=8, transport=self.transport) as client:
                tags = (await client.get(self.server_root + "/api/tags")).json().get("models", [])
                try:
                    running = {m["name"] for m in (await client.get(self.server_root + "/api/ps")).json().get("models", [])}
                except (httpx.HTTPError, ValueError, KeyError):
                    running = set()
            return [{"id": m["name"], "loaded": m["name"] in running, "type": "llm", "context": None} for m in tags if "embed" not in m["name"]]
        if not self.local:
            async with httpx.AsyncClient(timeout=15, transport=self.transport) as client:
                r = await client.get(self.base_url + "/models", headers=headers)
                r.raise_for_status()
            skip = ("embed", "tts", "whisper", "dall-e", "image", "audio", "realtime", "transcribe", "moderation", "search", "imagen", "veo", "aqa")
            ids = [m["id"].removeprefix("models/") for m in r.json().get("data", [])]
            return [{"id": i, "loaded": None, "type": "llm", "context": None} for i in sorted(ids) if not any(k in i for k in skip)]
        async with httpx.AsyncClient(timeout=8, transport=self.transport) as client:
            try:
                response = await client.get(self.server_root + "/api/v0/models", headers=headers)
                response.raise_for_status()
                return [
                    {"id": m["id"], "loaded": m.get("state") == "loaded", "type": m.get("type"), "context": m.get("loaded_context_length")}
                    for m in response.json().get("data", [])
                    if m.get("type") in (None, "llm", "vlm")
                ]
            except (httpx.HTTPError, ValueError, KeyError):
                response = await client.get(self.base_url + "/models", headers=headers)
                response.raise_for_status()
                return [{"id": m["id"], "loaded": None, "type": None, "context": None} for m in response.json().get("data", []) if "embed" not in m["id"]]

    # ------------------------------------------------------------------ core
    def _payload(self, messages, temperature, max_tokens, schema):
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "top_p": 0.9,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if self.provider == "openai":  # i modelli recenti di OpenAI vogliono max_completion_tokens
            payload["max_completion_tokens"] = payload.pop("max_tokens")
        if self.provider == "lmstudio" and not self.thinking:
            # LM Studio: unico parametro che spegne davvero il ragionamento dei Qwen3.x
            # (chat_template_kwargs, "/no_think" e reasoning_effort="low" vengono ignorati: verificato).
            payload["reasoning_effort"] = "none"
        if schema is not None:
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "risposta", "strict": self.provider == "lmstudio", "schema": schema}}
        return payload

    def _activity(self, text):
        if self.on_activity:
            try:
                self.on_activity(text)
            except Exception:
                pass

    async def chat(self, prompt, system, temperature=0.8, schema=None, max_tokens=None, label="LLM"):
        """Una chiamata in streaming. Restituisce (content, finish_reason)."""
        if self.provider == "anthropic":
            from app.services import anthropic_provider
            if not self.api_key:
                raise LLMError("Manca la chiave API di Claude: aggiungila nella scelta del motore AI.")
            return await anthropic_provider.chat(self, prompt, system, schema, max_tokens, label)
        if not self.model:
            raise LLMError("Nessun modello scelto: scegline uno nella pagina (Modello che scrive).")
        try:
            return await self._chat_once(prompt, system, temperature, schema, max_tokens, label)
        except LLMError as exc:
            # alcuni modelli cloud accettano solo la temperatura predefinita: si riprova senza
            if not self.local and "HTTP 400" in str(exc) and ("temperature" in str(exc) or "top_p" in str(exc)):
                self._no_sampling = True
                return await self._chat_once(prompt, system, temperature, schema, max_tokens, label)
            raise

    async def _chat_once(self, prompt, system, temperature, schema, max_tokens, label):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        payload = self._payload(messages, temperature, max_tokens or self.max_tokens, schema)
        if getattr(self, "_no_sampling", False):
            payload.pop("temperature", None)
            payload.pop("top_p", None)
        self.diagnostic_dir.mkdir(parents=True, exist_ok=True)
        diagnostic = self.diagnostic_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.json"
        content, reasoning, finish, error = [], 0, None, None
        started = last_report = time.monotonic()
        tokens, first_token, last_speed = 0, None, started  # ogni pezzo dello streaming di LM Studio è circa un token
        usage = None
        try:
            timeout = httpx.Timeout(connect=15, read=self.idle_timeout, write=60, pool=30)
            async with asyncio.timeout(self.total_timeout):
                async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
                    async with client.stream("POST", self.base_url + "/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json=payload) as response:
                        if response.is_error:
                            body = (await response.aread()).decode(errors="replace")
                            raise LLMError(f"{self.label} HTTP {response.status_code}: {body[:600]}")
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if raw == "[DONE]":
                                break
                            if not raw:
                                continue
                            event = json.loads(raw)
                            if event.get("error"):
                                raise LLMError(str(event["error"]))
                            if event.get("usage"):
                                usage = event["usage"]
                            for choice in event.get("choices", []):
                                delta = choice.get("delta") or {}
                                if delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning"):
                                    tokens += 1
                                    first_token = first_token or time.monotonic()
                                if isinstance(delta.get("content"), str):
                                    content.append(delta["content"])
                                reasoning += len(delta.get("reasoning_content") or delta.get("reasoning") or "")
                                finish = choice.get("finish_reason") or finish
                            now = time.monotonic()
                            if first_token and tokens > 1 and now - last_speed >= 1:
                                last_speed = now
                                LIVE.update(tps=round((tokens - 1) / max(0.05, now - first_token), 1), tokens=tokens,
                                            model=self.model, at=now, streaming=True)
                            if now - last_report >= 3:
                                last_report = now
                                chars = sum(map(len, content))
                                phase = f"ragionamento {reasoning} car." if reasoning and not chars else f"{chars} caratteri scritti"
                                self._activity(f"{label}: {phase} · {now - started:.0f}s")
        except (TimeoutError, httpx.TimeoutException) as exc:
            error = "Tempo scaduto: il modello non ha risposto in tempo"
            raise LLMError(f"{error}. Controlla {self.label}. Diagnostica: {diagnostic}") from exc
        except httpx.ConnectError as exc:
            error = f"{self.label} non raggiungibile"
            hint = "avvia il server locale" if self.local else "controlla la connessione a Internet"
            raise LLMError(f"{error} su {self.base_url}: {hint}.") from exc
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            end = time.monotonic()
            done = (usage or {}).get("completion_tokens") or tokens
            if first_token and done > 1:
                LIVE.update(tps=round((done - 1) / max(0.05, end - first_token), 1), tokens=done, model=self.model, at=end)
            LIVE["streaming"] = False
            elapsed = round(time.monotonic() - started, 1)
            text = "".join(content)
            diagnostic.write_text(json.dumps({"label": label, "model": self.model, "thinking": self.thinking, "structured": schema is not None, "finish_reason": finish, "reasoning_characters": reasoning, "elapsed_seconds": elapsed, "error": error, "prompt": prompt[:4000], "response": text}, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("LLM %s: %.1fs finish=%s contenuto=%d reasoning=%d", label, elapsed, finish, len(text), reasoning)
        return self.clean("".join(content)), finish

    # ------------------------------------------------------------------ helpers pubblici
    async def generate_text(self, prompt, system="Sei uno scrittore italiano. Rispondi solo con il testo richiesto.", temperature=0.8, label="Testo"):
        text, finish = await self.chat(prompt, system, temperature, label=label)
        if not text:
            raise LLMError("Il modello ha restituito una risposta vuota (forse ha esaurito i token ragionando). Aumenta LMSTUDIO_MAX_TOKENS o disattiva il thinking.")
        if finish == "length":
            logger.warning("%s: risposta troncata per limite token, uso la parte completa", label)
            text = re.sub(r"[^.!?»\"]*$", "", text).strip() or text
        return text

    async def generate_json(self, prompt, schema=None, system="Rispondi esclusivamente con un oggetto JSON valido, in italiano, senza testo aggiuntivo.", temperature=0.6, label="JSON"):
        """Restituisce un dict. Un tentativo di riparazione se il JSON non e leggibile."""
        use_schema = schema
        last_error = None
        for attempt in range(3):
            try:
                text, finish = await self.chat(prompt, system, temperature, use_schema, label=label if not attempt else f"{label} (nuovo tentativo {attempt})")
            except LLMError as exc:
                if use_schema is not None and "HTTP 4" in str(exc) and ("response_format" in str(exc) or "schema" in str(exc).lower()):
                    logger.warning("Il server rifiuta json_schema: riprovo senza vincolo di formato")
                    use_schema = None
                    continue
                raise
            try:
                return self.extract_json(text)
            except ValueError as exc:
                last_error = exc
                logger.warning("%s: JSON non leggibile (%s), finish=%s", label, exc, finish)
                prompt = prompt + "\n\nIMPORTANTE: la risposta precedente non era JSON valido. Restituisci SOLO l'oggetto JSON completo, piu conciso."
        raise LLMError(f"{label}: il modello non ha prodotto JSON valido ({last_error}). Dettagli in {self.diagnostic_dir}")

    @staticmethod
    def clean(text):
        text = THINK_RE.sub("", text or "")
        return text.replace("</think>", "").strip()

    @staticmethod
    def extract_json(text):
        text = LLMService.clean(text)
        fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1)
        start = text.find("{")
        if start < 0:
            raise ValueError("nessun oggetto JSON nella risposta")
        candidate = text[start:]
        try:
            value, _ = json.JSONDecoder().raw_decode(candidate)
        except json.JSONDecodeError:
            value = json.loads(LLMService._repair(candidate))
        if not isinstance(value, dict):
            raise ValueError("era richiesto un oggetto JSON")
        return value

    @staticmethod
    def _repair(text):
        """Chiude stringhe/parentesi di un JSON troncato e rimuove virgole finali."""
        text = re.sub(r",\s*([}\]])", r"\1", text.strip())
        stack, in_string, escape = [], False, False
        for ch in text:
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]" and stack:
                stack.pop()
        if in_string:
            text += '"'
        text = re.sub(r",\s*$", "", text)
        try:
            json.loads(text + "".join(reversed(stack)))
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON non riparabile: {exc.msg}") from exc
        return text + "".join(reversed(stack))
