"""One OpenAI-compatible client for any provider (OpenRouter by default; Groq/Mistral/OpenAI via .env)."""
import logging
import asyncio
import os
import re
import time

import logging_setup as ls

# Langfuse tracing is on only when both keys are set; otherwise the plain OpenAI client is used.
LANGFUSE = bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))
_DATA_URL = re.compile(r"data:[\w/.+-]+;base64,[A-Za-z0-9+/=]+")


def _mask(*, data, **_):
    """Never ship document scans (IDs, passports) to Langfuse: base64 files become a placeholder."""
    if isinstance(data, str):
        return _DATA_URL.sub("[document image redacted]", data)
    if isinstance(data, list):
        return [_mask(data=v) for v in data]
    if isinstance(data, dict):
        return {k: _mask(data=v) for k, v in data.items()}
    return data


if LANGFUSE:
    from langfuse import Langfuse
    from langfuse.openai import AsyncOpenAI  # drop-in: traces every chat + embedding call

    Langfuse(mask=_mask)  # reads LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL
else:
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

# Free tiers on other providers, tried in order once OpenRouter's free quota (50 requests/day without
# credits) is used up. Each is used only when its key is in .env; all three have no-card free plans.
_FREE_PROVIDERS = [
    ("Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]),
    ("Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", ["gemini-2.5-flash", "gemini-2.0-flash"]),
    ("Cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", ["llama-3.3-70b", "llama3.1-8b"]),
]
PROVIDERS = [("OpenRouter" if "openrouter" in BASE_URL else "Primary", client, list(dict.fromkeys([MODEL, FALLBACK_MODEL])))] if API_KEY else []
PROVIDERS += [(name, AsyncOpenAI(base_url=url, api_key=os.environ[env]), models)
              for name, url, env, models in _FREE_PROVIDERS if os.getenv(env)]
# Local Ollama: free, unlimited and private (nothing leaves the machine). Used when the model is pulled
# (`ollama pull qwen2.5:3b`). Text only: the small local model can't read document scans.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")


def _ollama_has_model() -> bool:
    import httpx
    try:
        tags = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=1.5).json().get("models", [])
        return any(m.get("name") == OLLAMA_MODEL or m.get("model") == OLLAMA_MODEL for m in tags)
    except Exception:
        return False


if os.getenv("LLM_LOCAL_FALLBACK", "1") != "0" and _ollama_has_model():
    PROVIDERS.append(("Ollama (local)", AsyncOpenAI(base_url=f"{OLLAMA_URL}/v1", api_key="ollama"), [OLLAMA_MODEL]))

# Last resort, no key needed: Pollinations' free OpenAI-compatible endpoint (open gpt-oss model, supports tools).
# ponytail: a public keyless service, so it never gets document images (ID scans) and can be switched off with
# LLM_KEYLESS_FALLBACK=0. Its /chat/completions path is paid, so it's called directly at /openai.
KEYLESS_URL = "https://text.pollinations.ai/openai"
if os.getenv("LLM_KEYLESS_FALLBACK", "1") != "0":
    PROVIDERS.append(("Pollinations (free, keyless)", None, ["openai"]))
_blocked: dict[str, tuple[float, str]] = {}  # provider -> (until epoch, why)
_log = ls.get("llm")


def _note_limit(provider: str, e: Exception) -> None:
    """A 429 benches the provider: until the daily reset for a daily cap, else for a minute."""
    body = getattr(e, "body", None) or {}
    err = body.get("error", body) if isinstance(body, dict) else {}
    meta = (err.get("metadata") or {}) if isinstance(err, dict) else {}
    reset = (meta.get("headers") or {}).get("X-RateLimit-Reset")
    daily = "per-day" in str(err.get("message", "")) or "daily" in str(meta.get("limit_source", ""))
    until = int(reset) / 1000 if reset and str(reset).isdigit() else time.time() + (3600 if daily else 60)
    why = f"{provider}'s free daily limit is used up until {time.strftime('%H:%M', time.localtime(until))}" if daily         else f"{provider} is rate limiting free requests for a minute"
    _blocked[provider] = (until, why)
    _log.warning("llm.provider_benched provider=%s until=%s daily=%s", provider, int(until), daily)


def unavailable_reason() -> str:
    """Why no AI answered, in words HR can act on (empty when nothing is benched)."""
    now = time.time()
    reasons = [why for until, why in _blocked.values() if until > now]
    if not PROVIDERS:
        return "No AI key is configured"
    if reasons and len(reasons) >= len(PROVIDERS):
        tip = " (add a free GROQ_API_KEY or GEMINI_API_KEY to .env to keep going)"
        return "; ".join(reasons) + tip
    return ""


def _has_image(messages: list) -> bool:
    return any(isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"] if isinstance(p, dict))
               for m in messages if isinstance(m, dict))


async def _keyless(model: str, messages: list, max_tokens: int, kw: dict):
    import httpx
    from openai.types.chat import ChatCompletion
    body = {"model": model, "messages": messages, "max_tokens": max_tokens,
            **{k: kw[k] for k in ("tools", "tool_choice", "temperature", "response_format") if k in kw}}
    async with httpx.AsyncClient(timeout=60) as h:
        for attempt in range(3):
            r = await h.post(KEYLESS_URL, json=body)
            # Free tier: about one reply per 45 s per IP. It says how long to wait; wait briefly, then give up.
            wait = re.search(r"[Rr]etry after (\d+)s", r.text) if r.status_code in (402, 429) else None
            if wait and attempt < 2 and int(wait[1]) <= 20:
                await asyncio.sleep(int(wait[1]) + 1)
                continue
            r.raise_for_status()
            break
        data = r.json()
    for ch in data.get("choices", []):  # their reply carries extra keys; keep what the OpenAI types know
        ch.get("message", {}).pop("reasoning", None)
        if ch.get("message", {}).get("tool_calls") == []:
            ch["message"]["tool_calls"] = None
    return ChatCompletion.model_validate({"object": "chat.completion", "created": int(time.time()), **data})


async def complete(messages: list, max_tokens: int, name: str = "llm", **kw):
    """Chat completion across free models and free providers. Returns (response, model_used).
    `name` labels the generation in Langfuse; each fallback attempt shows up as its own generation."""
    if LANGFUSE:
        kw |= {"name": name, "metadata": {"request_id": ls.request_id.get()}}
    last: Exception | None = None
    for provider, cli, models in PROVIDERS:
        until = _blocked.get(provider, (0, ""))[0]
        if until > time.time():
            continue  # benched: don't spend seconds on a call that will be refused
        if cli is None and (kw.get("stream") or _has_image(messages)):
            continue  # keyless public endpoint: no document images, and no streaming
        if provider.startswith("Ollama") and _has_image(messages):
            continue  # the small local model is text-only
        extra = dict(kw)
        if provider == "OpenRouter":  # keep reasoning models from burning the token budget on hidden thinking
            extra.setdefault("extra_body", {"reasoning": {"effort": "low", "exclude": True}})
        else:
            extra.pop("extra_body", None)
        if provider.startswith("Ollama"):
            extra.pop("response_format", None)  # prompts already ask for JSON; not every local model supports the flag
        for model in models:
            try:
                if cli is None:
                    resp = await _keyless(model, messages, max_tokens, extra)
                else:
                    resp = await cli.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens, **extra)
                if extra.get("stream"):
                    return resp, model
                msg = resp.choices[0].message if resp.choices else None
                if msg and (msg.content or getattr(msg, "tool_calls", None)):
                    return resp, model
                last = RuntimeError(f"{model} returned an empty reply")
            except Exception as e:
                last = e
                if getattr(e, "status_code", None) == 429 or getattr(getattr(e, "response", None), "status_code", None) == 429:
                    _note_limit(provider, e)
                    break  # the quota is per account, so the provider's other models are refused too
    raise last or RuntimeError(unavailable_reason() or "No AI provider available")
