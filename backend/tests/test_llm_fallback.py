"""LLM client falls back to the next model on overload, but not on real errors."""

from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError, APITimeoutError

from app.llm import client as llm


def _status_error(code: int) -> APIStatusError:
    req = httpx.Request("POST", "https://example.test")
    return APIStatusError(f"HTTP {code}", response=httpx.Response(code, request=req), body=None)


def _ok(text: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class FakeClient:
    def __init__(self, outcomes: dict):
        self.outcomes = outcomes
        self.calls: list[str] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw["model"])
        out = self.outcomes[kw["model"]]
        if isinstance(out, Exception):
            raise out
        return _ok(out)


@pytest.fixture
def fake(monkeypatch):
    def install(outcomes):
        fc = FakeClient(outcomes)
        monkeypatch.setattr(llm, "_client", lambda: fc)
        monkeypatch.setattr(llm, "_model_chain", lambda model: list(outcomes))
        return fc
    return install


def test_primary_model_used_when_healthy(fake):
    fc = fake({"a": "hello", "b": "unused"})
    assert llm.chat([{"role": "user", "content": "hi"}]) == "hello"
    assert fc.calls == ["a"]


@pytest.mark.parametrize("exc", [_status_error(503), _status_error(429), _status_error(500),
                                 APITimeoutError(httpx.Request("POST", "https://x.test"))])
def test_falls_back_on_transient_errors(fake, exc):
    fc = fake({"a": exc, "b": "from fallback"})
    assert llm.chat([{"role": "user", "content": "hi"}]) == "from fallback"
    assert fc.calls == ["a", "b"]


def test_no_fallback_on_bad_key(fake):
    fc = fake({"a": _status_error(401), "b": "should not be reached"})
    with pytest.raises(llm.LLMError):
        llm.chat([{"role": "user", "content": "hi"}])
    assert fc.calls == ["a"]


def test_all_models_down_raises(fake):
    fake({"a": _status_error(503), "b": _status_error(503)})
    with pytest.raises(llm.LLMError, match="All LLM models unavailable"):
        llm.chat([{"role": "user", "content": "hi"}])


def test_model_chain_dedupes(monkeypatch):
    s = llm.get_settings()
    monkeypatch.setattr(s, "llm_model", "m1")
    monkeypatch.setattr(s, "llm_fallback_models", "m2, m1 ,m3,")
    assert llm._model_chain(None) == ["m1", "m2", "m3"]
    assert llm._model_chain("m3") == ["m3", "m2", "m1"]
