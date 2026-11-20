"""Motori AI che scrivono le storie: LM Studio e Ollama (locali, gratis) oppure un servizio cloud con la chiave dell'utente.

La scelta e le chiavi restano sul PC in secrets/ai_provider.json (escluso da Git). LM Studio, Ollama, OpenAI e Gemini
parlano il formato compatibile OpenAI e passano da llm_service; Claude passa dall'SDK ufficiale (anthropic_provider). Unsloth Studio è locale ma chiede la sua chiave.
"""
import json
from pathlib import Path

from app.core.paths import SECRETS

FILE = SECRETS / "ai_provider.json"

PROVIDERS = {
    "lmstudio": {"label": "LM Studio", "base": "http://127.0.0.1:1234/v1", "local": True,
                 "help": "Apri LM Studio, carica un modello e avvia il server locale (scheda Developer, porta 1234)."},
    "ollama": {"label": "Ollama", "base": "http://127.0.0.1:11434/v1", "local": True,
               "help": "Installa Ollama da ollama.com, scarica un modello (es. ollama pull qwen3) e lascialo aperto."},
    "unsloth": {"label": "Unsloth Studio", "base": "http://127.0.0.1:8888/v1", "local": True, "key": True,
                "help": "Apri Unsloth Studio (unsloth studio -p 8888), carica un modello e copia la chiave da Settings → API (sk-unsloth-…)."},
    "openai": {"label": "OpenAI", "base": "https://api.openai.com/v1", "key": True,
               "help": "Crea una chiave su platform.openai.com → API keys. A pagamento; i testi escono dal PC."},
    "anthropic": {"label": "Claude (Anthropic)", "key": True, "default_model": "claude-opus-5-5",
                  "help": "Crea una chiave su console.anthropic.com → API Keys. A pagamento; i testi escono dal PC."},
    "gemini": {"label": "Google Gemini", "base": "https://generativelanguage.googleapis.com/v1beta/openai", "key": True,
               "help": "Crea una chiave su aistudio.google.com → Get API key. Piano gratuito con limiti; i testi escono dal PC."},
}
CLOUD_HOSTS = {"api.openai.com", "generativelanguage.googleapis.com", "api.anthropic.com"}


def load():
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("provider", "lmstudio")
    if data["provider"] not in PROVIDERS:
        data["provider"] = "lmstudio"
    data.setdefault("keys", {})
    data.setdefault("models", {})
    return data


def save(data):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def current():
    return load()["provider"]


def key(provider):
    return load()["keys"].get(provider, "")


def set_provider(provider, api_key=None, model=None):
    """Sceglie il motore; una chiave vuota ("") la cancella, None la lascia com'è."""
    if provider not in PROVIDERS:
        raise ValueError("Motore AI sconosciuto")
    data = load()
    data["provider"] = provider
    if api_key is not None:
        if api_key:
            data["keys"][provider] = api_key.strip()
        else:
            data["keys"].pop(provider, None)
    if model:
        data["models"][provider] = model
    save(data)
    return data


def remember_model(provider, model):
    data = load()
    if model and data["models"].get(provider) != model:
        data["models"][provider] = model
        save(data)


def public_status():
    """Per la pagina: motori, quale è scelto, quali chiavi sono salvate (mai le chiavi stesse)."""
    data = load()
    return {
        "current": data["provider"],
        "providers": [{"id": k, "label": v["label"], "local": bool(v.get("local")), "needs_key": bool(v.get("key")),
                       "has_key": bool(data["keys"].get(k)), "help": v["help"]} for k, v in PROVIDERS.items()],
    }
