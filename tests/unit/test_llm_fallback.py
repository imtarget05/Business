# -*- coding: utf-8 -*-
"""Tests for LLM fallback chain (Phase F)."""

from __future__ import annotations

from packages.llm.fallback import FallbackLLMProvider
from packages.llm.mock import MockLLMProvider


class _FailThenOk:
    """Provider that fails N times then succeeds (simulates transient 429/timeout)."""

    def __init__(self, fail_times: int = 1, name: str = "flaky"):
        self._fail = fail_times
        self._name = name
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    async def generate(self, prompt: str, **kwargs) -> str:
        self.calls += 1
        if self._fail > 0:
            self._fail -= 1
            raise TimeoutError("simulated timeout")
        return f"ok:{prompt}"

    async def generate_structured(self, prompt, schema, **kwargs):
        raise NotImplementedError

    async def complete_with_tools(self, messages, tools, **kwargs):
        raise NotImplementedError


class _RaiseAlways:
    @property
    def name(self) -> str:
        return "broken"

    async def generate(self, prompt, **kwargs):
        raise RuntimeError("always fails")

    async def generate_structured(self, prompt, schema, **kwargs):
        raise RuntimeError("always fails")

    async def complete_with_tools(self, messages, tools, **kwargs):
        raise RuntimeError("always fails")


async def test_fallback_switches_on_failure():
    flaky = _FailThenOk(fail_times=1, name="flaky")
    mock = MockLLMProvider(scripted=["mock_fallback"])
    chain = FallbackLLMProvider([flaky, mock])
    # First attempt: flaky fails -> switches to mock
    out = await chain.generate("hi")
    assert out == "mock_fallback"
    assert chain.active_provider_name == "mock"


async def test_fallback_sticks_after_success():
    ok = _FailThenOk(fail_times=0, name="ok")
    chain = FallbackLLMProvider([ok])
    out = await chain.generate("a")
    assert out == "ok:a"
    assert chain.active_provider_name == "ok"
    # Second call stays on same provider
    out2 = await chain.generate("b")
    assert out2 == "ok:b"
    assert chain.active_provider_name == "ok"


async def test_fallback_always_has_mock():
    broken = _RaiseAlways()
    chain = FallbackLLMProvider([broken])
    # Mock is auto-appended as last resort -> returns deterministic text
    out = await chain.generate("x")
    assert "mock-llm" in out
    assert "mock" in chain.active_provider_name


async def test_fallback_chain_reports_names():
    a = _FailThenOk(name="a")
    b = _FailThenOk(name="b")
    chain = FallbackLLMProvider([a, b])
    assert chain.provider_chain[0] == "a"
    assert chain.provider_chain[-1] == "mock"
