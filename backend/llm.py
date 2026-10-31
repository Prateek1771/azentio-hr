"""One OpenAI-compatible client for any provider (OpenRouter by default; Groq/Mistral/OpenAI via .env)."""
import logging
import os

from openai import AsyncOpenAI

BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY", "")
FREE_DEFAULT = "nvidia/nemotron-3.5-super-vl:free"
FREE_FALLBACK = "google/gemma-4-31b-it:free"
EMBED_MODEL = os.getenv("LLM_EMBED_MODEL", "")  # empty = keyword retrieval only


def _free_only(model: str, default: str) -> str:
    """Free models only on OpenRouter: a paid model set in .env is refused and the free default is used."""
    if "openrouter" in BASE_URL and not model.endswith(":free"):
        logging.getLogger("azentio.llm").warning("llm.paid_model_refused model=%s using=%s", model, default)
        return default
    return model


MODEL = _free_only(os.getenv("LLM_MODEL", FREE_DEFAULT), FREE_DEFAULT)
FALLBACK_MODEL = _free_only(os.getenv("LLM_FALLBACK_MODEL", FREE_FALLBACK), FREE_FALLBACK)

client = AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY or "missing")


async def complete(messages: list, max_tokens: int, **kw):
    """Chat completion with one free-model fallback. Returns (response, model_used)."""
    if "openrouter" in BASE_URL:  # keep reasoning models from burning the token budget on hidden thinking
        kw.setdefault("extra_body", {"reasoning": {"effort": "low", "exclude": True}})
    last = None
    for model in dict.fromkeys([MODEL, FALLBACK_MODEL]):
        try:
            resp = await client.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens, **kw)
            if kw.get("stream"):
                return resp, model
            msg = resp.choices[0].message if resp.choices else None
            if msg and (msg.content or getattr(msg, "tool_calls", None)):
                return resp, model
            last = RuntimeError(f"{model} returned an empty reply")
        except Exception as e:
            last = e
    raise last
