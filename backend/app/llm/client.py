"""Thin LLM wrapper. Every module calls `chat()` / `chat_json()`, so switching
provider (OpenAI <-> Anthropic) only touches this file."""

from __future__ import annotations

import json
import logging
import time
from functools import lru_cache

from app.config import get_settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


@lru_cache
def _openai_client():
    from openai import OpenAI

    key = get_settings().openai_api_key
    if not key:
        raise LLMError("OPENAI_API_KEY is not set in .env")
    return OpenAI(api_key=key, timeout=60, max_retries=2)


def available() -> bool:
    s = get_settings()
    return bool(s.openai_api_key if s.llm_provider == "openai" else s.anthropic_api_key)


def chat(messages: list[dict], *, json_mode: bool = False, temperature: float = 0.0,
         max_tokens: int = 2000, model: str | None = None) -> str:
    s = get_settings()
    t0 = time.perf_counter()
    if s.llm_provider != "openai":
        raise LLMError(f"LLM provider '{s.llm_provider}' is not implemented yet")
    kwargs = dict(model=model or s.llm_model, messages=messages, temperature=temperature,
                  max_tokens=max_tokens)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = _openai_client().chat.completions.create(**kwargs)
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
