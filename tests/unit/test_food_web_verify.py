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

from agents.monitoring.telegram_bot import _is_food_lookup, _food_query, _real_web_search


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
    assert _food_query("Các món ăn việt nam lọt vào Michelin") == "ăn michelin"
    assert "michelin" in _food_query("nhà hàng hà nội đạt sao")
    assert _food_query("món nước việt nam") == "nước michelin"


def test_real_web_search_returns_verifiable_links(monkeypatch):
    _patch_container(monkeypatch, [
        {"title": "Michelin Guide Vietnam", "url": "https://guide.michelin.com/vn"},
        {"title": "Bún riêu Hà Nội", "url": "https://example.com/bun-rieu"},
    ])
    import asyncio
    out = asyncio.run(_real_web_search("món việt nam michelin"))
    assert len(out) == 2
    assert out[0]["url"].startswith("https://")


def test_real_web_search_empty_on_no_source(monkeypatch):
    _patch_container(monkeypatch, [])
    import asyncio
    out = asyncio.run(_real_web_search("món việt nam michelin"))
    assert out == []
