# -*- coding: utf-8 -*-
"""Research Agent — web search & arxiv.

Capabilities: research.web_search, research.summarize, research.arxiv_search
"""
from __future__ import annotations

from typing import Any

from packages.contracts.enums import AgentResponseStatus, Domain
from packages.contracts.models import AgentDescriptor, AgentResponse, ErrorDetail, TaskRequest
from packages.llm.base import LLMProvider
from packages.llm.mock import MockLLMProvider

try:
    from hermes_tools import web_search as _web_search, web_extract as _web_extract

    _HAS_HERMES = True
except ImportError:
    _HAS_HERMES = False
    _web_search = None  # type: ignore
    _web_extract = None  # type: ignore

SUPPORTED_ACTIONS = {"web_search", "summarize", "arxiv_search"}


class ResearchAgent:
    def __init__(self, descriptor: AgentDescriptor | None = None, llm: LLMProvider | None = None) -> None:
        self.descriptor = descriptor or AgentDescriptor(
            name="research",
            domain=Domain.RESEARCH,
            version="1",
            description="Web research: web search, arxiv search, summarize.",
            capabilities=frozenset({"research.web_search", "research.summarize", "research.arxiv_search"}),
        )
        self._llm = llm or MockLLMProvider()

    async def handle(self, request: TaskRequest) -> AgentResponse:
        if request.action not in SUPPORTED_ACTIONS:
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.REJECTED, error=ErrorDetail(code="VALIDATION_ERROR", message=f"unsupported action {request.action!r}"))
        if request.action == "web_search":
            return await self._web_search(request)
        if request.action == "arxiv_search":
            return await self._arxiv_search(request)
        if request.action == "summarize":
            return await self._summarize(request)
        return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.FAILED, error=ErrorDetail(code="UNKNOWN", message="unhandled"))

    async def _web_search(self, request: TaskRequest) -> AgentResponse:
        query = str(request.payload.get("query") or request.payload.get("q") or "").strip()
        if not query:
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.REJECTED, error=ErrorDetail(code="VALIDATION_ERROR", message="payload.query required"))
        limit = int(request.payload.get("limit", 5) or 5)
        if _HAS_HERMES and _web_search is not None:
            try:
                res = await _web_search(query=query, limit=limit)  # type: ignore
                items = res.get("data", {}).get("web", []) if isinstance(res, dict) else []
                return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.SUCCESS, result={"query": query, "results": items, "count": len(items)})
            except Exception as e:
                return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.FAILED, error=ErrorDetail(code="SEARCH_ERROR", message=str(e)))
        # Fallback mock
        mock_results = [{"title": f"Mock result for {query}", "url": "https://example.com", "snippet": "mock"}]
        return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.SUCCESS, result={"query": query, "results": mock_results, "count": 1, "mock": True})

    async def _arxiv_search(self, request: TaskRequest) -> AgentResponse:
        query = str(request.payload.get("query") or request.payload.get("q") or "").strip()
        if not query:
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.REJECTED, error=ErrorDetail(code="VALIDATION_ERROR", message="payload.query required"))
        # Use web_search with arxiv site filter
        if _HAS_HERMES and _web_search is not None:
            try:
                res = await _web_search(query=f"site:arxiv.org {query}", limit=5)  # type: ignore
                items = res.get("data", {}).get("web", []) if isinstance(res, dict) else []
                return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.SUCCESS, result={"query": query, "results": items, "count": len(items)})
            except Exception as e:
                return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.FAILED, error=ErrorDetail(code="SEARCH_ERROR", message=str(e)))
        return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.SUCCESS, result={"query": query, "results": [{"title": f"arxiv mock {query}", "url": "https://arxiv.org/abs/0000"}], "count": 1, "mock": True})

    async def _summarize(self, request: TaskRequest) -> AgentResponse:
        text = str(request.payload.get("text") or request.payload.get("content") or "").strip()
        urls = request.payload.get("urls") or []
        if not text and not urls:
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.REJECTED, error=ErrorDetail(code="VALIDATION_ERROR", message="payload.text or payload.urls required"))
        # If urls provided and hermes available, extract first
        if urls and _HAS_HERMES and _web_extract is not None:
            try:
                ex = await _web_extract(urls=list(urls)[:3])  # type: ignore
                # ex is dict with results
                if isinstance(ex, dict):
                    extracted = " ".join(r.get("content", "")[:2000] for r in ex.get("results", []))
                    if extracted:
                        text = extracted
            except Exception:
                pass
        if not text:
            text = " ".join(str(u) for u in urls)
        try:
            summary = await self._llm.generate(prompt=f"Summarize concisely:\n{text[:6000]}", system="You are a research summarizer. Be concise and factual.")
            s = summary if isinstance(summary, str) else str(summary)
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.SUCCESS, result={"summary": s})
        except Exception as e:
            return AgentResponse(task_id=request.task_id, agent=self.descriptor.qualified_name, status=AgentResponseStatus.FAILED, error=ErrorDetail(code="LLM_ERROR", message=str(e)))


def create_research_agent(llm: LLMProvider | None = None) -> ResearchAgent:
    return ResearchAgent(llm=llm)
