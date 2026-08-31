"""Unit tests: LLM cost tracking + prompt cache (AI-Engineer point 3)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.core.llm_cost import (  # noqa: E402
    estimate_tokens,
    log_llm_usage,
    prompt_cache_get,
    prompt_cache_key,
    prompt_cache_set,
)


def test_estimate_tokens_rough():
    assert estimate_tokens("") == 0
    # ~4 chars/token
    assert estimate_tokens("x" * 40) == 10
    assert estimate_tokens("món ăn việt nam") > 0


def test_log_llm_usage_appends_ledger(tmp_path, monkeypatch):
    import packages.core.llm_cost as lc

    ledger = tmp_path / "usage.jsonl"
    monkeypatch.setattr(lc, "_LEDGER", ledger)
    rec = log_llm_usage("qwen3:1.7b", "prompt text here", "answer text", 1.23, tag="food")
    assert rec["in_tokens"] > 0
    assert rec["cache_hit"] is False
    assert ledger.read_text(encoding="utf-8").strip().endswith("}")


def test_prompt_cache_roundtrip(tmp_path, monkeypatch):
    import packages.core.llm_cost as lc

    cache = tmp_path / "cache"
    monkeypatch.setattr(lc, "_CACHE_DIR", cache)
    key = prompt_cache_key("same prompt", "same system")
    assert prompt_cache_get(key) is None
    prompt_cache_set(key, "cached answer")
    assert prompt_cache_get(key) == "cached answer"


def test_summarize_food_uses_cache_second_call(tmp_path, monkeypatch):
    """Second identical call must hit cache (no 2nd LLM call) and still return text."""
    import asyncio

    import packages.core.llm_cost as lc
    from agents.monitoring.telegram_bot import _summarize_food

    cache = tmp_path / "cache"
    monkeypatch.setattr(lc, "_CACHE_DIR", cache)
    ledger = tmp_path / "ledger" / "u.jsonl"
    monkeypatch.setattr(lc, "_LEDGER", ledger)

    calls = {"n": 0}

    class FakeLLM:
        name = "qwen3:1.7b"

        async def generate(self, prompt, system=None, max_tokens=500, temperature=0.0):
            calls["n"] += 1
            return "1. Nhà hàng A (https://example.com/a)"

    res = [{"title": "T", "url": "https://example.com", "snippet": "Michelin 2026"}]
    out1 = asyncio.run(_summarize_food(FakeLLM(), "Câu hỏi Michelin?", res))
    out2 = asyncio.run(_summarize_food(FakeLLM(), "Câu hỏi Michelin?", res))
    assert out1 == out2
    assert calls["n"] == 1, "second call should be served from cache, not LLM"
    # Ledger should have 1 miss + 1 hit
    lines = ledger.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert any('"cache_hit": true' in line for line in lines)


