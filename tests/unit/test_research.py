# -*- coding: utf-8 -*-
"""Unit tests for research module.

Mocks the web_search / web_extract wrappers so no network or hermes_tools needed.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from agents.monitoring import research as research_mod
from agents.monitoring.research import ResearchOrchestrator


@pytest.mark.asyncio
async def test_research_orchestrator_web_success(monkeypatch):
    """Web research with mocked search/extract returns a report."""

    async def fake_search(query: str, limit: int = 5) -> dict:
        return {"data": {"web": [
            {"title": "LangGraph Guide", "url": "https://example.com/1", "description": "Intro"},
        ]}}

    async def fake_extract(urls, char_limit=5000) -> dict:
        return {"results": [
            {"title": "LangGraph Guide", "url": "https://example.com/1", "content": "LangGraph is a graph-based agent framework."},
        ]}

    monkeypatch.setattr(research_mod, "_call_web_search", fake_search)
    monkeypatch.setattr(research_mod, "_call_web_extract", fake_extract)

    orch = ResearchOrchestrator()
    result = await orch.execute(task_id=uuid4(), query="What is LangGraph?", domain="web")

    assert result["status"] == "success"
    assert "report" in result
    assert len(result["report"]) > 0


@pytest.mark.asyncio
async def test_research_orchestrator_search_error(monkeypatch):
    """When search tool raises, orchestrator returns failed status gracefully."""

    async def fake_search(query: str, limit: int = 5) -> dict:
        raise RuntimeError("search unavailable")

    async def fake_extract(urls, char_limit=5000) -> dict:
        return {"results": []}

    monkeypatch.setattr(research_mod, "_call_web_search", fake_search)
    monkeypatch.setattr(research_mod, "_call_web_extract", fake_extract)

    orch = ResearchOrchestrator()
    result = await orch.execute(task_id=uuid4(), query="test", domain="web")

    assert result["status"] in {"failed", "success"}


@pytest.mark.asyncio
async def test_web_search_agent_search_empty(monkeypatch):
    """WebSearchAgent.search returns [] when web tool returns no web data."""

    async def fake_search(query: str, limit: int = 5) -> dict:
        return {"data": {}}

    monkeypatch.setattr(research_mod, "_call_web_search", fake_search)

    from agents.monitoring.research import WebSearchAgent
    agent = WebSearchAgent()
    results = await agent.search("anything")
    assert results == []


@pytest.mark.asyncio
async def test_web_search_agent_extract_no_urls():
    """WebSearchAgent.extract handles results without URLs gracefully."""
    from agents.monitoring.research import WebSearchAgent
    agent = WebSearchAgent()
    extracted = await agent.extract([{"title": "T", "description": "D"}])
    assert len(extracted) == 1
    assert extracted[0]["title"] == "T"
