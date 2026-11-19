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

logger = logging.getLogger("uvicorn.error")
THINK_RE = re.compile(r"<think>.*?(</think>|$)", re.DOTALL)


def env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


class LLMError(RuntimeError):
    pass


class LLMService:
    def __init__(self, model=None, transport=None):
        load_dotenv()
        self.base_url = os.getenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
        if urlparse(self.base_url).hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Sono consentiti solo server LLM locali (127.0.0.1 / localhost).")
        self.api_key = os.getenv("LMSTUDIO_API_KEY", "lm-studio")
        self.model = (model or os.getenv("LMSTUDIO_MODEL", "")).strip().strip("'\"")
        self.thinking = os.getenv("LMSTUDIO_THINKING", "off").strip().lower() not in ("off", "0", "false", "no")
        self.max_tokens = env_int("LMSTUDIO_MAX_TOKENS", 12000)
        self.idle_timeout = env_int("LMSTUDIO_IDLE_TIMEOUT", 300)
        self.total_timeout = env_int("LMSTUDIO_TOTAL_TIMEOUT", 3600)
        self.transport = transport
        self.diagnostic_dir = Path(os.getenv("LLM_DIAGNOSTIC_DIR", "projects/_diagnostics"))
        self.on_activity = None  # callback(str) per mostrare l'avanzamento nella UI

    # ------------------------------------------------------------------ server
    @property
    def server_root(self):
        return self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url

    async def list_models(self):
        """Modelli esposti da LM Studio, con stato di caricamento se disponibile."""
        headers = {"Authorization": f"Bearer {self.api_key}"}
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
        if not self.thinking:
            # LM Studio: unico parametro che spegne davvero il ragionamento dei Qwen3.x
            # (chat_template_kwargs, "/no_think" e reasoning_effort="low" vengono ignorati: verificato).
            payload["reasoning_effort"] = "none"
        if schema is not None:
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "risposta", "strict": True, "schema": schema}}
        return payload

    def _activity(self, text):
        if self.on_activity:
            try:
                self.on_activity(text)
            except Exception:
                pass

    async def chat(self, prompt, system, temperature=0.8, schema=None, max_tokens=None, label="LLM"):
        """Una chiamata in streaming. Restituisce (content, finish_reason)."""
        if not self.model:
            raise LLMError("Nessun modello configurato: imposta LMSTUDIO_MODEL nel file .env o scegli un modello nella pagina.")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        payload = self._payload(messages, temperature, max_tokens or self.max_tokens, schema)
        self.diagnostic_dir.mkdir(parents=True, exist_ok=True)
        diagnostic = self.diagnostic_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.json"
        content, reasoning, finish, error = [], 0, None, None
        started = last_report = time.monotonic()
        try:
            timeout = httpx.Timeout(connect=15, read=self.idle_timeout, write=60, pool=30)
            async with asyncio.timeout(self.total_timeout):
                async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
                    async with client.stream("POST", self.base_url + "/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json=payload) as response:
                        if response.is_error:
                            body = (await response.aread()).decode(errors="replace")
                            raise LLMError(f"LM Studio HTTP {response.status_code}: {body[:600]}")
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
                            for choice in event.get("choices", []):
                                delta = choice.get("delta") or {}
                                if isinstance(delta.get("content"), str):
                                    content.append(delta["content"])
                                reasoning += len(delta.get("reasoning_content") or delta.get("reasoning") or "")
                                finish = choice.get("finish_reason") or finish
                            now = time.monotonic()
                            if now - last_report >= 3:
                                last_report = now
                                chars = sum(map(len, content))
                                phase = f"ragionamento {reasoning} car." if reasoning and not chars else f"{chars} caratteri scritti"
                                self._activity(f"{label}: {phase} · {now - started:.0f}s")
        except (TimeoutError, httpx.TimeoutException) as exc:
            error = "Tempo scaduto: il modello non ha risposto in tempo"
            raise LLMError(f"{error}. Controlla LM Studio. Diagnostica: {diagnostic}") from exc
        except httpx.ConnectError as exc:
            error = "LM Studio non raggiungibile"
            raise LLMError(f"{error} su {self.base_url}: avvia il server locale in LM Studio.") from exc
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
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
