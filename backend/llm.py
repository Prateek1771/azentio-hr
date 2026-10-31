"""One OpenAI-compatible client for any provider (OpenRouter by default; Groq/Mistral/OpenAI via .env)."""
import os

from openai import AsyncOpenAI

BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY", "")
# ponytail: free models by default (user asked for cheap); fallback is also free, never a paid model.
MODEL = os.getenv("LLM_MODEL", "nvidia/nemotron-3.5-super-vl:free")
FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "google/gemma-4-31b-it:free")
EMBED_MODEL = os.getenv("LLM_EMBED_MODEL", "")  # empty = keyword retrieval only

client = AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY or "missing")


async def complete(messages: list, max_tokens: int, **kw):
    """Chat completion with one free-model fallback. Returns (response, model_used)."""
    if "openrouter" in BASE_URL:  # keep reasoning models from burning the token budget on hidden thinking
        kw.setdefault("extra_body", {"reasoning": {"effort": "low", "exclude": True}})
    last = None
    for model in dict.fromkeys([MODEL, FALLBACK_MODEL]):
        try:
            resp = await client.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens, **kw)
            if kw.get("stream") or (resp.choices and resp.choices[0].message.content):
                return resp, model
            last = RuntimeError(f"{model} returned an empty reply")
        except Exception as e:
            last = e
    raise last
