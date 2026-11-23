"""Thin LLM wrapper. Every module calls `chat()` / `chat_json()`, so switching
provider only touches this file.

Gemini is used through Google's OpenAI-compatible endpoint, so one SDK
(openai) serves both providers: only the base URL and key differ."""

from __future__ import annotations

import json
import logging
import time
from functools import lru_cache

from app.config import get_settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "openai": None,
}


@lru_cache
def _client():
    from openai import OpenAI

    s = get_settings()
    if s.llm_provider not in BASE_URLS:
        raise LLMError(f"Unknown LLM_PROVIDER '{s.llm_provider}' (use gemini or openai)")
    if not s.llm_key:
        raise LLMError(f"{s.llm_provider.upper()}_API_KEY is not set in .env")
    return OpenAI(api_key=s.llm_key, base_url=BASE_URLS[s.llm_provider],
                  timeout=s.llm_timeout_seconds, max_retries=0)  # retries = the model chain


def available() -> bool:
    return bool(get_settings().llm_key)


def _model_chain(model: str | None) -> list[str]:
    """Primary model first, then the configured fallbacks (no duplicates)."""
    s = get_settings()
    chain = [model or s.llm_model]
    chain += [m.strip() for m in s.llm_fallback_models.split(",") if m.strip()]
    return list(dict.fromkeys(chain))


def _is_transient(exc: Exception) -> bool:
    """Overload, rate limit, timeout or server error: worth trying another model.
    Bad key / bad request are not (another model would fail the same way)."""
    from openai import APIConnectionError, APIStatusError, APITimeoutError

    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in (404, 408, 429) or exc.status_code >= 500
    return False


def chat(messages: list[dict], *, json_mode: bool = False, temperature: float = 0.0,
         max_tokens: int = 2000, model: str | None = None, timeout: float | None = None,
         fallback: bool = True) -> str:
    """timeout: per-call deadline in seconds (default from settings).
    fallback=False tries only the first model: for calls where a quick
    non-LLM answer beats waiting for another model."""
    client = _client() if timeout is None else _client().with_options(timeout=timeout)
    last_exc: Exception | None = None
    chain = _model_chain(model)
    for name in (chain if fallback else chain[:1]):
        t0 = time.perf_counter()
        kwargs = dict(model=name, messages=messages, temperature=temperature,
                      max_tokens=max_tokens)
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as exc:
            if not _is_transient(exc):
                raise LLMError(f"LLM call failed: {exc}") from exc
            log.warning("llm %s unavailable after %.2fs (%s), trying next model",
                        name, time.perf_counter() - t0, type(exc).__name__)
            last_exc = exc
            continue
        log.info("llm %s %.2fs", name, time.perf_counter() - t0)
        return resp.choices[0].message.content or ""
    raise LLMError(f"All LLM models unavailable, last error: {last_exc}") from last_exc


def chat_json(messages: list[dict], **kw) -> dict:
    raw = chat(messages, json_mode=True, **kw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM returned invalid JSON: {raw[:200]}") from exc
