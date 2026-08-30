# -*- coding: utf-8 -*-
"""Unit tests: Michelin / food questions must be web-verified, never hallucinated.

Mirrors the JobSearch V5 principle: verify with a real tool before answering;
if nothing verifiable is found, refuse instead of inventing fake restaurants.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from agents.monitoring.telegram_bot import _is_food_lookup, _food_query, _real_web_search, _summarize_food


class _FakeResult:
    def __init__(self, results):
        self.result = {"results": results}


class _FakeHandler:
    def __init__(self, results):
        self._results = results
    async def handle(self, req):
        return _FakeResult(self._results)


class _FakeContainer:
    def __init__(self, results):
        self._results = results
    def registry(self):  # attribute access used in code
        return self
    @property
    def registry(self):
        return self
    def get_by_capability(self, cap):
        return ("desc", _FakeHandler(self._results))


def _patch_container(monkeypatch, results):
    """Replace get_container() so _real_web_search hits our fake handler."""
    import packages.core.bootstrap as boot
    class _C:
        @property
        def registry(self):
            return self
        def get_by_capability(self, cap):
            return ("desc", _FakeHandler(results))
    monkeypatch.setattr(boot, "get_container", lambda: _C())


def test_is_food_lookup_detects_michelin_queries():
    assert _is_food_lookup("Các món ăn việt nam lọt vào Michelin")
    assert _is_food_lookup("nhà hàng Hà Nội đạt sao michelin")
    assert _is_food_lookup("ẩm thực việt nam vinh danh")
    # Non-food question must NOT match.
    assert not _is_food_lookup("tìm việc làm AI intern tại Hà Nội")
    assert not _is_food_lookup("thời tiết hôm nay")


def test_food_query_strips_fillers():
    assert _food_query("Các món ăn việt nam lọt vào Michelin") == "món ăn việt nam michelin guide vietnam"
    assert "michelin guide vietnam" in _food_query("nhà hàng hà nội đạt sao")
    assert _food_query("món nước việt nam") == "món việt nam michelin guide vietnam"


def test_real_web_search_returns_verifiable_links(monkeypatch):
    _patch_container(monkeypatch, [
        {"title": "Michelin Guide Vietnam", "url": "https://guide.michelin.com/vn"},
        {"title": "Bún riêu Hà Nội", "url": "https://example.com/bun-rieu"},
    ])
    import asyncio
    out = asyncio.run(_real_web_search("món việt nam michelin"))
    assert len(out) == 2
    assert out[0]["url"].startswith("https://")


def test_summarize_food_uses_only_snippets(monkeypatch):
    """LLM must summarize from real snippets; never invent. Falls back to '' on failure."""
    import asyncio
    calls = {}

    class FakeLLM:
        async def generate(self, prompt, system, max_tokens=500, temperature=0.0):
            calls["prompt"] = prompt
            calls["system"] = system
            # Echo only names present in the snippet (proves it read the source).
            return "1. Michelin Guide Vietnam (https://guide.michelin.com/vn)"

    res = [
        {"title": "Vietnam MICHELIN Restaurants", "url": "https://guide.michelin.com/vn",
         "snippet": "Michelin Guide Việt Nam 2026 vinh danh 193 cơ sở"},
    ]
    out = asyncio.run(_summarize_food(FakeLLM(), "Các món ăn việt nam lọt vào Michelin", res))
    assert "guide.michelin.com" in out
    # The snippet text must have been passed to the LLM (no memory-only answer).
    assert "193 cơ sở" in calls["prompt"]
    assert "KHÔNG" in calls["system"] and "bịa" in calls["system"]


def test_summarize_food_falls_back_on_error():
    import asyncio

    class BoomLLM:
        async def generate(self, *a, **k):
            raise RuntimeError("llm down")

    out = asyncio.run(_summarize_food(BoomLLM(), "q", [{"title": "x", "url": "https://y"}]))
    assert out == ""
