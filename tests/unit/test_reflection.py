"""Unit tests: reflection engine (MockLLM-safe)."""

from __future__ import annotations

from packages.core.reflection import ReflectionEngine
from packages.llm.mock import MockLLMProvider


class TestReflection:
    async def test_critique_never_raises(self) -> None:
        eng = ReflectionEngine(llm=MockLLMProvider())
        result = await eng.critique("t-1", "knowledge.query", "hello", "good answer")
        assert "score" in result
        assert "issues" in result

    async def test_critique_llm_failure_returns_safe(self) -> None:
        class _BrokenLLM(MockLLMProvider):
            async def generate_structured(self, *args, **kwargs):
                raise RuntimeError("llm down")

        eng = ReflectionEngine(llm=_BrokenLLM())
        result = await eng.critique("t-1", "knowledge.query", "hello", "good")
        assert result["score"] == -1.0
        assert "critique_unavailable" in result["issues"][0]
