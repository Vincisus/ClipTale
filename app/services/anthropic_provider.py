"""Claude come motore delle storie, con l'SDK ufficiale di Anthropic (pacchetto `anthropic`).

- streaming (risposte lunghe senza timeout), velocità in token/s mostrata nella barra come per i modelli locali;
- JSON vincolato con output_config.format (lo schema viene adattato: oggetti chiusi, niente limiti sul numero di elementi);
- niente temperature: i modelli attuali la rifiutano; la profondità si regola con output_config.effort;
- se Claude declina una richiesta, il fallback lato server ripete la stessa richiesta su un altro modello.
"""
import copy
import time

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-2", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _client(api_key):
    from anthropic import AsyncAnthropic
    return AsyncAnthropic(api_key=api_key, max_retries=3)


def adapt_schema(schema):
    """Schema JSON accettato da output_config.format: ogni oggetto chiuso (additionalProperties false) con tutti i
    campi obbligatori; via minItems/maxItems (il conteggio lo controlla già la pipeline dopo)."""
    def walk(node):
        if isinstance(node, dict):
            node.pop("minItems", None)
            node.pop("maxItems", None)
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        return node
    return walk(copy.deepcopy(schema))


async def list_models(api_key):
    client = _client(api_key)
    out = []
    async for m in client.models.list():
        out.append({"id": m.id, "loaded": None, "type": "llm", "context": getattr(m, "max_input_tokens", None)})
    return out


async def chat(llm, prompt, system, schema=None, max_tokens=None, label="LLM"):
    """Una chiamata in streaming. Restituisce (testo, finish_reason) come LLMService.chat."""
    from app.services.llm_service import LIVE, LLMError
    from anthropic import APIConnectionError, APIStatusError, AuthenticationError, RateLimitError

    model = llm.model or DEFAULT_MODEL
    output_config = {"effort": "medium"}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": adapt_schema(schema)}
    params = {"model": model, "max_tokens": max(16000, int(max_tokens or llm.max_tokens)), "system": system,
              "messages": [{"role": "user", "content": prompt}], "output_config": output_config}
    if model in FALLBACK_MODELS:
        params.update(betas=[FALLBACK_BETA], fallbacks="default")
    client = _client(llm.api_key)
    started = time.monotonic()
    first, chars, last = None, 0, started
    try:
        async with client.beta.messages.stream(**params) as stream:
            async for text in stream.text_stream:
                now = time.monotonic()
                first = first or now
                chars += len(text)
                if now - last >= 1 and now > first:
                    last = now
                    # stima finché lo streaming è in corso (~4 caratteri per token); il valore vero arriva alla fine
                    LIVE.update(tps=round(chars / 4 / (now - first), 1), tokens=chars // 4, model=model, at=now, streaming=True)
                    llm._activity(f"{label}: {chars} caratteri scritti · {now - started:.0f}s")
            message = await stream.get_final_message()
    except AuthenticationError as exc:
        raise LLMError("Claude: chiave API non valida. Controllala nelle impostazioni del motore AI.") from exc
    except RateLimitError as exc:
        raise LLMError("Claude: troppe richieste o credito esaurito, riprova tra poco.") from exc
    except APIConnectionError as exc:
        raise LLMError("Claude non raggiungibile: controlla la connessione a Internet.") from exc
    except APIStatusError as exc:
        raise LLMError(f"Claude HTTP {exc.status_code}: {exc.message}") from exc
    finally:
        LIVE["streaming"] = False
    end = time.monotonic()
    out_tokens = getattr(message.usage, "output_tokens", 0) or chars // 4
    if first and out_tokens > 1:
        LIVE.update(tps=round(out_tokens / max(0.05, end - first), 1), tokens=out_tokens, model=message.model or model, at=end)
    if message.stop_reason == "refusal":
        raise LLMError("Claude ha rifiutato questa richiesta: prova a riformulare l'idea.")
    text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
    finish = "length" if message.stop_reason == "max_tokens" else "stop"
    return text, finish
