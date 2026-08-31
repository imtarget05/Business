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


def test_feedback_callback_keeps_original_message(monkeypatch):
    """Clicking 👍/👎 must NOT overwrite the source message text."""
    import asyncio
    import agents.monitoring.telegram_bot as tb
    bot = tb.MonitoringBot(tb.TelegramConfig(bot_token="STUB"))

    edited_text = {}
    replied = {}

    class FakeQuery:
        data = "fb:up:food"
        async def answer(self):
            return

        async def edit_message_reply_markup(self, reply_markup=None):
            return

        async def edit_message_text(self, text, **k):
            edited_text["text"] = text  # must NEVER happen

        class message:
            @staticmethod
            async def reply_text(text, **k):
                replied["text"] = text
                return {"t": text}

    class FakeUpdate:
        callback_query = FakeQuery()

    called = {}
    class FakeLearning:
        async def record_feedback(self, payload):
            called["fb"] = payload

    class _C:
        learning = FakeLearning()

    import packages.core.bootstrap as boot
    monkeypatch.setattr(boot, "get_container", lambda: _C())

    async def run():
        await bot._button_callback(FakeUpdate(), type("Ctx", (), {})())

    asyncio.run(run())
    assert called.get("fb", {}).get("rating") == "up"
    assert "text" not in edited_text, "feedback must not edit_message_text on source post"
    assert "text" in replied, "thanks should be a NEW reply, not overwrite"


def test_summarize_food_rejects_invented_counts(monkeypatch, tmp_path):
    """LLM must not invent aggregate counts (7 one-star, 58 Bib...) not in snippet."""
    import asyncio
    import packages.core.llm_cost as lc
    monkeypatch.setattr(lc, "_CACHE_DIR", tmp_path / "cache")
    captured = {}

    class FakeLLM:
        async def generate(self, prompt, system, max_tokens=500, temperature=0.0):
            captured["system"] = system
            # A compliant model would refuse to invent; we assert the prompt/sytem
            # forbids it and the helper still returns whatever the model says.
            return "Nguồn ghi khoảng 193 cơ sở — xem chi tiết tại link guide.michelin.com"

    res = [
        {"title": "Vietnam MICHELIN Restaurants", "url": "https://guide.michelin.com/vn",
         "snippet": "Michelin Guide Việt Nam 2026 vinh danh 193 cơ sở ăn uống"},
    ]
    out = asyncio.run(_summarize_food(FakeLLM(), "Các món ăn việt nam lọt vào Michelin", res))
    assert "guide.michelin.com" in out
    # The strict rules must be present in the system prompt.
    assert "TUYỆT ĐỐI KHÔNG" in captured["system"]
    assert "7 one-star" in captured["system"] or "58 Bib" in captured["system"]


def test_food_safety_rejects_invented_distinction():
    """_food_summary_is_safe must reject '7 one-star' when snippet lacks it."""
    from agents.monitoring.telegram_bot import _food_summary_is_safe
    results = [{"title": "Michelin VN", "snippet": "Michelin Guide 2026 có 193 cơ sở"}]
    good = "1. Nhà hàng A (link)\n2. Nhà hàng B (link)"
    bad = "Có 7 nhà hàng 1 sao và 58 Bib Gourmand được vinh danh."
    assert _food_summary_is_safe(good, results) is True
    assert _food_summary_is_safe(bad, results) is False
    # Generic count present in snippet is allowed.
    assert _food_summary_is_safe("193 cơ sở được vinh danh", results) is True


def test_summarize_food_falls_back_on_error():
    import asyncio

    class BoomLLM:
        async def generate(self, *a, **k):
            raise RuntimeError("llm down")

    out = asyncio.run(_summarize_food(BoomLLM(), "q", [{"title": "x", "url": "https://y"}]))
    assert out == ""
