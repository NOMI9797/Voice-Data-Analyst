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
    return OpenAI(api_key=s.llm_key, base_url=BASE_URLS[s.llm_provider], timeout=60, max_retries=2)


def available() -> bool:
    return bool(get_settings().llm_key)


def chat(messages: list[dict], *, json_mode: bool = False, temperature: float = 0.0,
         max_tokens: int = 2000, model: str | None = None) -> str:
    s = get_settings()
    t0 = time.perf_counter()
    kwargs = dict(model=model or s.llm_model, messages=messages, temperature=temperature,
                  max_tokens=max_tokens)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = _client().chat.completions.create(**kwargs)
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError(f"LLM call failed: {exc}") from exc
    log.info("llm %s %.2fs", kwargs["model"], time.perf_counter() - t0)
    return resp.choices[0].message.content or ""


def chat_json(messages: list[dict], **kw) -> dict:
    raw = chat(messages, json_mode=True, **kw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM returned invalid JSON: {raw[:200]}") from exc
