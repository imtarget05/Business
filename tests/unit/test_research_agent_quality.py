"""Adversarial quality tests for the Research Agent (post UX-review pass).

Ensures /research no longer dumps raw HTML / 403 errors into the report, and
that summarize() synthesizes via the local LLM instead of naive concatenation.
"""

from __future__ import annotations

import pytest

from agents.monitoring import research as research_mod
from agents.monitoring.research import WebSearchAgent


async def test_extract_drops_blocked_and_html_entries(monkeypatch):
    """403 / extract-error / raw-HTML results must be filtered out, not reported."""

    async def fake_extract(urls, char_limit=5000):
        return {
            "results": [
                {"title": "Blocked", "url": "https://x.com/a", "error": "403 Forbidden"},
                {
                    "title": "Raw HTML",
                    "url": "https://y.com/b",
                    "content": "<!doctype html><html><head><title>spam</title></head></html>",
                },
                {"title": "Good", "url": "https://z.com/c", "content": "LangGraph là thư viện tạo agent stateful."},
            ]
        }

    monkeypatch.setattr(research_mod, "_call_web_extract", fake_extract)
    agent = WebSearchAgent()
    results = [
        {"title": "Blocked", "url": "https://x.com/a"},
        {"title": "Raw HTML", "url": "https://y.com/b"},
        {"title": "Good", "url": "https://z.com/c"},
    ]
    extracted = await agent.extract(results)
    assert len(extracted) == 1
    assert extracted[0]["title"] == "Good"
    assert "LangGraph" in extracted[0]["content"]
    assert extracted[0]["error"] is None


async def test_extract_no_urls_keeps_clean_description():
    """When there are no URLs, snippets with real descriptions are kept (HTML skipped)."""
    agent = WebSearchAgent()
    results = [
        {"title": "Ok", "description": "LangGraph hữu ích cho agent.", "url": ""},
        {"title": "Bad", "description": "<html>spam</html>", "url": ""},
    ]
    extracted = await agent.extract(results)
    assert len(extracted) == 1
    assert "LangGraph" in extracted[0]["content"]


async def test_extract_drops_error_text_content(monkeypatch):
    """web_extract returns '[extract error: 403 ...]' as content (no error key) -> drop it."""
    async def fake_extract(urls, char_limit=5000):
        return {
            "results": [
                {
                    "title": "Blocked page",
                    "url": "https://sider.ai/x",
                    "content": "[extract error: Client error '403 Forbidden' for url 'https://sider.ai/x']",
                },
                {"title": "Good", "url": "https://z.com/c", "content": "LangGraph là thư viện stateful."},
            ]
        }

    monkeypatch.setattr(research_mod, "_call_web_extract", fake_extract)
    agent = WebSearchAgent()
    extracted = await agent.extract([{"title": "Blocked page", "url": "https://sider.ai/x"}, {"title": "Good", "url": "https://z.com/c"}])
    assert len(extracted) == 1
    assert extracted[0]["title"] == "Good"


async def test_extract_falls_back_to_search_snippets_when_all_blocked(monkeypatch):
    """When every web extract is blocked, fall back to search-result descriptions."""

    async def fake_extract(urls, char_limit=5000):
        return {
            "results": [
                {"title": "Blocked", "url": "https://sider.ai/x", "content": "[extract error: 403 Forbidden]"},
                {"title": "Blocked2", "url": "https://viblo.asia/y", "content": "<html>spam</html>"},
            ]
        }

    monkeypatch.setattr(research_mod, "_call_web_extract", fake_extract)
    agent = WebSearchAgent()
    search_results = [
        {"title": "Sider", "url": "https://sider.ai/x", "snippet": "LangGraph là framework xây dựng agent."},
        {"title": "Viblo", "url": "https://viblo.asia/y", "snippet": "Dùng graph để quản lý state."},
    ]
    extracted = await agent.extract(search_results)
    assert len(extracted) == 2
    assert any("framework" in e["content"] for e in extracted)
    assert any("graph" in e["content"] for e in extracted)


async def test_summarize_uses_llm_when_available(monkeypatch):
    """summarize() should call the LLM and return its synthesized answer."""

    class FakeLLM:
        async def generate(self, prompt, system=None, temperature=0.2):
            return "LangGraph là framework xây dựng agent stateful bằng graph."

    import packages.llm as llm_mod

    monkeypatch.setattr(llm_mod, "get_llm_provider", lambda: FakeLLM())
    agent = WebSearchAgent()
    extracted = [{"title": "S", "content": "LangGraph là thư viện stateful agent."}]
    out = await agent.summarize(extracted, "LangGraph là gì?")
    assert "framework" in out
    assert "<html" not in out


async def test_summarize_says_when_all_sources_blocked(monkeypatch):
    """If every source was blocked, summarize must say so, not dump garbage."""

    class FakeLLM:
        async def generate(self, prompt, system=None, temperature=0.2):
            return "should not be used"

    import packages.llm as llm_mod

    monkeypatch.setattr(llm_mod, "get_llm_provider", lambda: FakeLLM())
    agent = WebSearchAgent()
    out = await agent.summarize([], "LangGraph là gì?")
    assert "Không thu thập được" in out
    assert "should not be used" not in out
